//! **카톡 수집기 (회사판 medi-ax 전용 · cargo feature `kakao-collector`).**
//!
//! SPEC-009(조회 전용 백그라운드 수집)·SPEC-006 v0.6.0 「외부 채널 수집기 수용」. 개인판
//! strong-hajin 빌드에는 이 모듈이 **컴파일되지 않는다**(판 가르기 N-10 · W3-8).
//!
//! 경계(SPEC-009 §3): 로컬 카톡 DB 를 **읽기 전용**으로 열어(`crypto`+`db`) 방 목록·메시지·첨부
//! 메타를 내고, **기기 토큰 Bearer**(`keychain`)로 SPEC-008 §4.6 서버에 올린다(`client`·`collector`).
//! 키·DB 경로·복호 결과는 Mac 밖으로 나가지 않는다(D-16). 웹뷰에 여는 커맨드는 셋뿐이다(`commands`).

pub mod client;
pub mod collector;
pub mod commands;
pub mod crypto;
pub mod db;
pub mod keychain;
pub mod tray;

#[cfg(test)]
mod live_tests {
    use super::*;

    /// **SHELL-0 완료 조건(실물 · 코디 macOS)** — mykakao 방식 키 유도·user_id 복구가 이 기기에서
    /// 성립해 **방 목록이 이름·종류로 나온다**. 네트워크·GUI 없이 로컬 DB 만 읽는다.
    ///
    /// 전체 디스크 접근과 카톡 로그인이 있어야 통과하므로 기본 스위트에서 빼고(`#[ignore]`),
    /// 코디가 수동으로 돌린다:
    /// `cargo test --features kakao-collector -- --ignored --nocapture kakao_live_room_list`
    #[test]
    #[ignore = "실기기 전용 — 전체 디스크 접근·카톡 로그인 필요"]
    fn kakao_live_room_list() {
        let resolved = match crypto::resolve() {
            Ok(r) => r,
            Err(e) => panic!("resolve 실패({e}) — FDA·로그인 확인"),
        };
        println!(
            "[kakao] user_id={} db={} key_len={}",
            resolved.user_id,
            resolved.db_path.display(),
            resolved.key.len()
        );
        let conn = db::open(&resolved).expect("DB 열기");
        let rooms = db::list_rooms(&conn).expect("방 목록");
        println!("[kakao] 방 {}개", rooms.len());
        // 방마다 메시지 수를 붙여 **적은 방**을 앞에 둔다 — E2E 로 고를 작은 방을 찾기 쉽게.
        let mut with_count: Vec<_> = rooms
            .iter()
            .map(|r| {
                let n: i64 = conn
                    .query_row(
                        "SELECT COUNT(*) FROM NTChatMessage WHERE chatId = ?1 AND type <> 0",
                        rusqlite::params![r.chat_id],
                        |row| row.get(0),
                    )
                    .unwrap_or(0);
                (r, n)
            })
            .collect();
        with_count.sort_by_key(|(_, n)| *n);
        for (room, n) in with_count.iter().filter(|(_, n)| *n > 0).take(40) {
            println!(
                "  - [{}] {} chatId={} msgs={} members={:?}",
                room.kind.as_str(),
                room.name,
                room.chat_id,
                n,
                room.member_count
            );
        }
        assert!(!rooms.is_empty(), "방 목록이 비었다 — 수집 대상이 없다");
    }

    /// **SHELL 완료 조건(실물)** — 로컬 API 로 한 번 수집한다. 고른 방 하나를 (개발 페르소나 세션으로)
    /// 서버에 저장한 뒤, 수집기가 기기 토큰으로 handshake→업로드한다(선택 정본은 서버 · R-F1).
    ///
    /// `KAKAO_API_ORIGIN=http://127.0.0.1:8001 KAKAO_DEVICE_TOKEN=axdt_… \`
    /// `KAKAO_E2E_CHATID=<chatId> KAKAO_E2E_NAME=<이름> KAKAO_PERSONA=jiho \`
    /// `cargo test --features kakao-collector -- --ignored --nocapture kakao_live_collect_once`
    #[test]
    #[ignore = "실기기+로컬 API 전용 — KAKAO_API_ORIGIN·KAKAO_DEVICE_TOKEN·KAKAO_E2E_CHATID 필요"]
    fn kakao_live_collect_once() {
        let base = std::env::var("KAKAO_API_ORIGIN").expect("KAKAO_API_ORIGIN");
        let token = std::env::var("KAKAO_DEVICE_TOKEN").expect("KAKAO_DEVICE_TOKEN");
        let chat_id = std::env::var("KAKAO_E2E_CHATID").expect("KAKAO_E2E_CHATID");
        let name = std::env::var("KAKAO_E2E_NAME").unwrap_or_else(|_| "E2E 방".into());
        let persona = std::env::var("KAKAO_PERSONA").unwrap_or_else(|_| "jiho".into());

        // 1) handshake 로 integration_id 를 얻는다(없으면 여기서 생성).
        let client = client::Client::new(base.clone(), token.clone());
        let hs = client.handshake().expect("handshake");
        println!("[kakao] integration={} version={}", hs.integration_id, hs.selected_rooms_version);

        // 2) 방 하나를 **서버에** 고른다 — 웹 세션 몫을 개발 페르소나 헤더로 대신한다(POST …/rooms).
        let body = serde_json::json!({
            "room_ids": [chat_id],
            "rooms": [{ "room_id": chat_id, "type": "direct", "name": name, "member_count": 2 }],
        })
        .to_string();
        let agent: ureq::Agent = ureq::Agent::config_builder().build().into();
        let select = agent
            .post(format!("{base}/api/integrations/{}/rooms", hs.integration_id))
            .header("X-Demo-Persona", persona.as_str())
            .header("Content-Type", "application/json")
            .send(body.as_bytes());
        match select {
            Ok(r) => println!("[kakao] 방 고르기 status={}", r.status().as_u16()),
            Err(e) => println!("[kakao] 방 고르기 실패(이미 골랐을 수 있음): {e}"),
        }

        // 3) 수집기가 한 번 돈다 — handshake 로 고른 방을 받아 메시지를 올린다.
        let shared = collector::Shared::default();
        let summary = collector::collect_once(&base, &token, &shared).expect("collect_once");
        println!(
            "[kakao] === 수집 결과 === rooms={} uploaded_messages={} uploaded_attachments={} version={}",
            summary.rooms,
            summary.uploaded_messages,
            summary.uploaded_attachments,
            summary.selected_rooms_version,
        );
        assert!(summary.rooms >= 1, "서버가 고른 방이 없다 — 방 고르기가 안 됐다");
    }
}
