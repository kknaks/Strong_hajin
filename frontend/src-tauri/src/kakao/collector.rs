//! 수집 루프 — 로컬 카톡 DB 를 **2초 폴링**(OQ-902)으로 읽어 서버(§4.6)에 올린다. 상태는 30초마다
//! 보고한다(서버가 90초 무보고를 `app:off` 로 판정 · W-3). 창과 무관하게 기기 토큰으로 돈다(R3-F2).
//!
//! 올리는 순서(wire): handshake → 방마다 `last_logId` 이후 메시지 묶음(≤500) → 응답의 pending 첨부
//! 바이트 업로드 → 30초마다 status. `selected_rooms_version` 이 바뀌면 re-handshake(W3-3).

use std::io::Read;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use super::client::{self, Client, ClientError, MessageInput};
use super::crypto::{self, OpenFailure};
use super::db;

/// 한 번 폴링 간격(OQ-902).
const POLL: Duration = Duration::from_secs(2);
/// 상태 보고 주기(W-3).
const STATUS_EVERY: Duration = Duration::from_secs(30);
/// 한 묶음 상한(서버 KAKAO_BATCH_LIMIT 과 같다).
const BATCH: usize = 500;
/// 첨부 다운로드 상한(서버 50MB 와 같다 · 넘으면 받지 않는다).
const ATTACH_LIMIT: u64 = 50 * 1024 * 1024;

/// 웹(`kakao_collector_status`)·트레이가 읽는 상태. 서버 status 로도 올라간다.
#[derive(Debug, Clone)]
pub struct Status {
    /// running | off | unreadable
    pub kakao_state: &'static str,
    pub reason: Option<&'static str>,
    pub account_name: Option<String>,
    pub account_changed: bool,
    pub logged_in: bool,
    pub selected_rooms_version: i64,
    pub uploaded_messages: u64,
    pub uploaded_attachments: u64,
    pub last_error: Option<String>,
}

impl Default for Status {
    fn default() -> Self {
        Self {
            kakao_state: "off",
            reason: None,
            account_name: None,
            account_changed: false,
            logged_in: false,
            selected_rooms_version: -1,
            uploaded_messages: 0,
            uploaded_attachments: 0,
            last_error: None,
        }
    }
}

/// 한 번 수집한 결과(E2E·진단 보고 — `collect_once` 가 돌려주고 live 테스트가 숫자로 찍는다).
#[allow(dead_code)]
#[derive(Debug, Default, Clone)]
pub struct Summary {
    pub rooms: usize,
    pub uploaded_messages: u64,
    pub uploaded_attachments: u64,
    pub integration_id: String,
    pub selected_rooms_version: i64,
}

#[derive(Clone)]
pub struct Shared {
    status: Arc<Mutex<Status>>,
    running: Arc<AtomicBool>,
}

impl Default for Shared {
    fn default() -> Self {
        Self {
            status: Arc::new(Mutex::new(Status::default())),
            running: Arc::new(AtomicBool::new(true)),
        }
    }
}

impl Shared {
    pub fn snapshot(&self) -> Status {
        self.status.lock().map(|s| s.clone()).unwrap_or_default()
    }
    fn update(&self, f: impl FnOnce(&mut Status)) {
        if let Ok(mut s) = self.status.lock() {
            f(&mut s);
        }
    }
    #[allow(dead_code)]
    pub fn stop(&self) {
        self.running.store(false, Ordering::SeqCst);
    }
}

fn device_name() -> Option<String> {
    std::process::Command::new("/usr/sbin/scutil")
        .arg("--get")
        .arg("ComputerName")
        .output()
        .ok()
        .and_then(|o| {
            let s = String::from_utf8_lossy(&o.stdout).trim().to_string();
            if s.is_empty() {
                None
            } else {
                Some(s)
            }
        })
        .or_else(|| std::env::var("HOST").ok())
}

fn app_version() -> Option<String> {
    Some(env!("CARGO_PKG_VERSION").to_string())
}

/// CDN 에서 바이트를 받는다(서명 URL · 그대로 GET · 50MB 상한). 인증 헤더 없음.
fn download_cdn(agent: &ureq::Agent, url: &str) -> Option<Vec<u8>> {
    let mut resp = agent.get(url).call().ok()?;
    if !(200..300).contains(&resp.status().as_u16()) {
        return None;
    }
    let mut reader = resp.body_mut().as_reader().take(ATTACH_LIMIT + 1);
    let mut buf = Vec::new();
    reader.read_to_end(&mut buf).ok()?;
    if buf.len() as u64 > ATTACH_LIMIT {
        return None; // 상한 초과 — 받지 않는다(서버도 slot 을 안 연다).
    }
    Some(buf)
}

fn plain_agent() -> ureq::Agent {
    use ureq::tls::{RootCerts, TlsConfig, TlsProvider};
    ureq::Agent::config_builder()
        .tls_config(
            TlsConfig::builder()
                .provider(TlsProvider::NativeTls)
                .root_certs(RootCerts::PlatformVerifier)
                .build(),
        )
        .timeout_connect(Some(Duration::from_secs(20)))
        .timeout_recv_response(Some(Duration::from_secs(60)))
        .timeout_recv_body(Some(Duration::from_secs(120)))
        .build()
        .into()
}

/// **한 번 수집** — handshake 부터 방별 업로드까지. 서버 base 와 기기 토큰을 받는다.
/// 수집기 스레드와 E2E 테스트가 함께 쓴다. 상태를 `shared` 에 반영한다.
pub fn collect_once(base: &str, token: &str, shared: &Shared) -> Result<Summary, String> {
    let client = Client::new(base, token);
    let cdn = plain_agent();

    // 1) DB 열기 — 못 열면 unreadable 상태를 올리고 끝낸다(예외를 삼키지 않는다 · AC-09).
    let resolved = match crypto::resolve() {
        Ok(r) => r,
        Err(e) => {
            let reason = reason_str(e.failure);
            report_unreadable(&client, &shared_device(), reason, shared);
            shared.update(|s| {
                s.kakao_state = "unreadable";
                s.reason = Some(reason);
                s.last_error = Some(e.detail.clone());
            });
            return Err(format!("DB 열기 실패: {e}"));
        }
    };
    let conn = match db::open(&resolved) {
        Ok(c) => c,
        Err(f) => {
            let reason = reason_str(f);
            report_unreadable(&client, &shared_device(), reason, shared);
            shared.update(|s| {
                s.kakao_state = "unreadable";
                s.reason = Some(reason);
            });
            return Err("DB 복호 실패".into());
        }
    };

    let account = db::account_name(&conn, resolved.user_id);
    shared.update(|s| {
        s.kakao_state = "running";
        s.reason = None;
        s.account_name = account.clone();
        s.logged_in = true;
    });

    // 2) handshake — 서버의 고른 방·버전.
    let hs = client.handshake().map_err(|e| e.to_string())?;
    shared.update(|s| s.selected_rooms_version = hs.selected_rooms_version);

    let mut summary = Summary {
        rooms: hs.selected_rooms.len(),
        integration_id: hs.integration_id.clone(),
        selected_rooms_version: hs.selected_rooms_version,
        ..Default::default()
    };

    // 3) 방마다 last_logId 이후를 올린다. 묶음이 꽉 차면 다음 묶음으로(백필).
    for room in &hs.selected_rooms {
        let Ok(chat_id) = room.external_id.parse::<i64>() else {
            continue;
        };
        let mut cursor = room.last_log_id;
        // DB 읽기가 실패하면(열렸다가 깨짐) 그 방은 이번 틱에서 멈춘다 — while let 이 루프를 끝낸다.
        while let Ok(raws) = db::fetch_messages(&conn, chat_id, cursor, BATCH) {
            let caught_up = raws.len() < BATCH;
            let inputs: Vec<MessageInput> = raws.iter().map(client::to_message_input).collect();
            let result = match client.post_messages(&room.room_id, &inputs, caught_up) {
                Ok(r) => r,
                Err(ClientError::RoomNotSelected) => break, // 서버가 더는 안 고른 방 — 건너뛴다.
                Err(e) => return Err(e.to_string()),
            };
            summary.uploaded_messages += result.accepted.max(0) as u64;
            shared.update(|s| s.uploaded_messages += result.accepted.max(0) as u64);

            // 첨부 바이트 업로드 — 응답이 가리키는 (logId, seq) 의 url 로 받아 올린다.
            for p in &result.pending {
                if let Some((url, name, mime)) = find_attachment(&raws, p.log_id, p.seq) {
                    if let Some(bytes) = download_cdn(&cdn, &url) {
                        if client.post_attachment(&p.aid, &bytes, &name, mime.as_deref()).is_ok() {
                            summary.uploaded_attachments += 1;
                            shared.update(|s| s.uploaded_attachments += 1);
                        }
                    }
                }
            }

            if let Some(last) = raws.last() {
                cursor = last.log_id;
            }
            if caught_up {
                break;
            }
        }
    }

    // 4) 상태 보고.
    let _ = client.post_status(&client::StatusInput {
        device_name: device_name(),
        version: app_version(),
        kakao_state: "running",
        kakao_reason: None,
        account_name: account,
        account_changed: false,
    });
    shared.update(|s| s.last_error = None);
    Ok(summary)
}

fn shared_device() -> Option<String> {
    device_name()
}

fn report_unreadable(client: &Client, device: &Option<String>, reason: &'static str, _shared: &Shared) {
    let _ = client.post_status(&client::StatusInput {
        device_name: device.clone(),
        version: app_version(),
        kakao_state: "unreadable",
        kakao_reason: Some(reason),
        account_name: None,
        account_changed: false,
    });
}

fn reason_str(f: OpenFailure) -> &'static str {
    match f {
        OpenFailure::Permission => "permission",
        OpenFailure::Version => "version",
        OpenFailure::Unknown => "unknown",
    }
}

/// `(logId, seq)` 의 첨부 url·이름·mime 를 그 묶음의 원본에서 찾는다(url 은 서버로 안 가고 여기서만).
fn find_attachment(
    raws: &[db::RawMessage],
    log_id: i64,
    seq: i64,
) -> Option<(String, String, Option<String>)> {
    let msg = raws.iter().find(|m| m.log_id == log_id)?;
    let att = msg.attachments.iter().find(|a| a.seq == seq)?;
    let url = att.url.clone()?;
    Some((url, att.name.clone(), att.mime.clone()))
}

/// 수집기 백그라운드 스레드를 띄운다. 토큰을 키체인에서 매 틱 읽어(갓 저장된 것도 집어낸다) 돌린다.
/// 토큰이 없으면 `login_required`(logged_in=false) 로 쉰다 — 창과 무관하게 계속 산다(R3-F2).
pub fn spawn(base: String) -> Shared {
    let shared = Shared::default();
    let worker = shared.clone();
    std::thread::Builder::new()
        .name("kakao-collector".into())
        .spawn(move || {
            let mut last_status = Instant::now()
                .checked_sub(STATUS_EVERY)
                .unwrap_or_else(Instant::now);
            while worker.running.load(Ordering::SeqCst) {
                let token = super::keychain::load();
                match token {
                    None => {
                        worker.update(|s| {
                            s.logged_in = false;
                            s.last_error = None;
                        });
                    }
                    Some(token) => {
                        worker.update(|s| s.logged_in = true);
                        // 메시지 틱.
                        if let Err(e) = collect_once(&base, &token, &worker) {
                            worker.update(|s| s.last_error = Some(e));
                        }
                        last_status = Instant::now();
                    }
                }
                // 상태 보고 주기 — collect_once 가 이미 한 번 보냈으면 last_status 가 방금이다.
                if last_status.elapsed() >= STATUS_EVERY {
                    last_status = Instant::now();
                }
                std::thread::sleep(POLL);
            }
        })
        .expect("수집기 스레드를 띄우지 못했습니다");
    shared
}

#[cfg(test)]
mod tests {
    use super::*;
    use super::super::db::{AttachKind, RawAttachment, RawMessage};

    fn msg(log_id: i64, atts: Vec<RawAttachment>) -> RawMessage {
        RawMessage {
            log_id,
            author: None,
            at_secs: 1_700_000_000,
            ktype: 2,
            text: None,
            attachments: atts,
        }
    }

    #[test]
    fn find_attachment_은_logid_seq_로_찾는다() {
        let raws = vec![msg(
            10,
            vec![
                RawAttachment { kind: AttachKind::Album, seq: 0, name: "a".into(), size: None, mime: Some("image/png".into()), expired: false, url: Some("https://a/0".into()) },
                RawAttachment { kind: AttachKind::Album, seq: 1, name: "b".into(), size: None, mime: None, expired: false, url: Some("https://a/1".into()) },
            ],
        )];
        assert_eq!(find_attachment(&raws, 10, 1).unwrap().0, "https://a/1");
        assert_eq!(find_attachment(&raws, 10, 0).unwrap().2.as_deref(), Some("image/png"));
        assert!(find_attachment(&raws, 10, 2).is_none());
        assert!(find_attachment(&raws, 11, 0).is_none());
    }

    #[test]
    fn 만료_첨부는_url_이_없어_못_찾는다() {
        let raws = vec![msg(
            10,
            vec![RawAttachment { kind: AttachKind::Image, seq: 0, name: String::new(), size: None, mime: None, expired: true, url: None }],
        )];
        assert!(find_attachment(&raws, 10, 0).is_none());
    }

    #[test]
    fn 기본_상태는_off_로그인안됨() {
        let s = Status::default();
        assert_eq!(s.kakao_state, "off");
        assert!(!s.logged_in);
    }
}
