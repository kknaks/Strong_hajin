//! 웹뷰에 여는 **카톡 커맨드 셋** — SPEC-006 v0.6.0 은 정확히 셋이다(W3-1):
//! `kakao_list_rooms` · `kakao_collector_status` · `kakao_store_device_token`.
//! 고른 방의 «저장»은 커맨드가 아니라 서버 API(`POST …/rooms`)다 — 선택 정본은 서버(R-F1).
//!
//! 응답 모양은 `frontend/src/lib/shell.ts`(FE 수신부)와 맞춘다 — `kakao_list_rooms` 는
//! `{chat_id, type, name, member_count}` 의 배열, 카톡이 꺼져 못 읽으면 `kakao_off` 를 담은 오류.

use serde::Serialize;

use super::collector::Shared;
use super::{crypto, db};

/// FE `KakaoLocalRoom` — `chat_id`(문자열 · 18자리 id 는 JS Number 를 넘는다) · `type` · `name` · `member_count`.
#[derive(Debug, Serialize)]
pub struct RoomView {
    pub chat_id: String,
    #[serde(rename = "type")]
    pub kind: &'static str,
    pub name: String,
    pub member_count: Option<i64>,
}

/// `kakao_collector_status` — 앱·카톡·계정 상태를 웹에 준다(설정 상태 카드 보조 · 웹뷰 판정에도 쓰인다).
#[derive(Debug, Serialize)]
pub struct CollectorStatusView {
    pub app: &'static str,
    pub kakao_state: &'static str,
    pub reason: Option<&'static str>,
    pub account_name: Option<String>,
    pub account_changed: bool,
    pub logged_in: bool,
    pub selected_rooms_version: i64,
    pub uploaded_messages: u64,
    pub uploaded_attachments: u64,
}

/// 로컬 카톡 DB 의 방 목록(1:1·단체 · 오픈채팅 제외) — Rust 가 읽어 결과만 준다(웹엔 파일 권한 0).
#[tauri::command]
pub async fn kakao_list_rooms() -> Result<Vec<RoomView>, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let resolved = crypto::resolve().map_err(|e| match e.failure {
            crypto::OpenFailure::Permission => "permission".to_string(),
            _ => e.detail,
        })?;
        let conn = db::open(&resolved).map_err(|f| match f {
            crypto::OpenFailure::Permission => "permission".to_string(),
            _ => "unreadable".to_string(),
        })?;
        let rooms = db::list_rooms(&conn).map_err(|_| "unreadable".to_string())?;
        Ok(rooms
            .into_iter()
            .map(|r| RoomView {
                chat_id: r.chat_id.to_string(),
                kind: r.kind.as_str(),
                name: r.name,
                member_count: r.member_count,
            })
            .collect())
    })
    .await
    .map_err(|e| e.to_string())?
}

/// 수집기 상태(설정 카드 보조 · 커맨드가 있다는 것 자체가 「앱 웹뷰」 판정 신호다 · shell.ts `hasKakaoCollector`).
#[tauri::command]
pub async fn kakao_collector_status(
    shared: tauri::State<'_, Shared>,
) -> Result<CollectorStatusView, String> {
    let s = shared.snapshot();
    Ok(CollectorStatusView {
        app: "on",
        kakao_state: s.kakao_state,
        reason: s.reason,
        account_name: s.account_name,
        account_changed: s.account_changed,
        logged_in: s.logged_in,
        selected_rooms_version: s.selected_rooms_version,
        uploaded_messages: s.uploaded_messages,
        uploaded_attachments: s.uploaded_attachments,
    })
}

/// 웹이 발급받은 기기 토큰을 **Rust 키체인에** 넣는다(R3-F1 ④ · 응답에 토큰을 돌려주지 않는다).
#[tauri::command]
pub async fn kakao_store_device_token(token: String) -> Result<(), String> {
    super::keychain::store(&token)
}
