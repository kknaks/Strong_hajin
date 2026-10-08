//! SPEC-008 §4.6 서버 계약을 거는 **기기 토큰 Bearer** 클라이언트. 이 앱은 스키마를 새로 만들지
//! 않고 §4.6 을 **소비**한다(SPEC-009 §4·§5). TLS 는 native-tls + 플랫폼 신뢰 저장소(`download.rs` 와
//! 같은 결)이고, 리다이렉트를 따라가지 않으며 4xx/5xx 도 응답으로 받아 직접 가른다.

use std::io::Read;
use std::time::Duration;

use super::db::{AttachKind, RawMessage};

const CONNECT_TIMEOUT: Duration = Duration::from_secs(20);
const RECV_TIMEOUT: Duration = Duration::from_secs(120);

/// 서버로 올리는 한 메시지(§4.6 `KakaoMessageInput`). 메시지 필드는 **`at`** 이다(초/ms/ISO 모두 허용 ·
/// epoch 초를 그대로 보낸다 — 1e10 미만이라 초로 해석된다).
#[derive(Debug, Clone)]
pub struct MessageInput {
    pub log_id: i64,
    pub author: Option<String>,
    pub at_secs: i64,
    pub ktype: i64,
    pub text: Option<String>,
    pub attachments: Vec<AttachmentInput>,
    /// 「내가 보냄」(SPEC-008 v0.7.0 §4.6 `from_me?` · SPEC-009 v0.6.1) — `None` 이면 싣지 않는다(서버 `null`).
    pub from_me: Option<bool>,
}

#[derive(Debug, Clone)]
pub struct AttachmentInput {
    pub kind: AttachKind,
    pub seq: i64,
    pub name: String,
    pub size: Option<i64>,
    pub mime: Option<String>,
    pub expired: bool,
}

/// handshake 응답(§4.6). `room_id` = 서버 UUID · `external_id` = 카톡 chatId · `last_log_id` = 반영 지점.
#[derive(Debug, Clone)]
pub struct Handshake {
    pub integration_id: String,
    pub selected_rooms: Vec<SelectedRoom>,
    #[allow(dead_code)]
    pub reset_at: Option<String>,
    pub selected_rooms_version: i64,
}

#[derive(Debug, Clone)]
pub struct SelectedRoom {
    pub room_id: String,
    pub external_id: String,
    pub last_log_id: i64,
}

/// messages 응답 — 받은 수와 **바이트를 아직 안 받은 첨부 슬롯**(여기 aid 로 바이트를 올린다).
#[derive(Debug, Clone)]
pub struct MessagesResult {
    pub accepted: i64,
    pub pending: Vec<PendingUpload>,
}

#[derive(Debug, Clone)]
pub struct PendingUpload {
    pub log_id: i64,
    pub seq: i64,
    pub aid: String,
}

/// 상태 보고(§4.6 status).
#[derive(Debug, Clone)]
pub struct StatusInput {
    pub device_name: Option<String>,
    pub version: Option<String>,
    /// running | off | unreadable
    pub kakao_state: &'static str,
    /// permission | version | unknown (unreadable 일 때만)
    pub kakao_reason: Option<&'static str>,
    pub account_name: Option<String>,
    pub account_changed: bool,
}

#[derive(Debug)]
pub enum ClientError {
    /// 401 — 토큰 없음/무효. 수집기는 `login_required` 로 간다.
    Unauthorized,
    /// 409 handshake_required — handshake 를 먼저.
    HandshakeRequired,
    /// 403 room_not_selected — 서버의 고른 방이 아니다.
    RoomNotSelected,
    /// 그 밖(네트워크·5xx·형식).
    Other(String),
}

impl std::fmt::Display for ClientError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ClientError::Unauthorized => write!(f, "기기 토큰이 유효하지 않습니다(401)"),
            ClientError::HandshakeRequired => write!(f, "handshake 필요(409)"),
            ClientError::RoomNotSelected => write!(f, "서버의 고른 방이 아닙니다(403)"),
            ClientError::Other(s) => write!(f, "{s}"),
        }
    }
}

pub struct Client {
    agent: ureq::Agent,
    base: String,
    token: String,
}

impl Client {
    pub fn new(base: impl Into<String>, token: impl Into<String>) -> Self {
        use ureq::tls::{RootCerts, TlsConfig, TlsProvider};
        let agent: ureq::Agent = ureq::Agent::config_builder()
            .tls_config(
                TlsConfig::builder()
                    .provider(TlsProvider::NativeTls)
                    .root_certs(RootCerts::PlatformVerifier)
                    .build(),
            )
            .max_redirects(0)
            .max_redirects_will_error(false)
            .http_status_as_error(false)
            .timeout_connect(Some(CONNECT_TIMEOUT))
            .timeout_recv_response(Some(RECV_TIMEOUT))
            .timeout_recv_body(Some(RECV_TIMEOUT))
            .build()
            .into();
        let mut base = base.into();
        while base.ends_with('/') {
            base.pop();
        }
        Self { agent, base, token: token.into() }
    }

    fn auth(&self) -> String {
        format!("Bearer {}", self.token)
    }

    /// GET …/kakao/handshake — 서버의 고른 방·reset_at·버전.
    pub fn handshake(&self) -> Result<Handshake, ClientError> {
        let url = format!("{}/api/integrations/kakao/handshake", self.base);
        let mut resp = self
            .agent
            .get(&url)
            .header("Authorization", &self.auth())
            .call()
            .map_err(|e| ClientError::Other(e.to_string()))?;
        let status = resp.status().as_u16();
        let body = read_body(&mut resp);
        check_status(status, &body)?;
        let v: serde_json::Value =
            serde_json::from_str(&body).map_err(|e| ClientError::Other(format!("handshake 응답 파싱: {e}")))?;
        let integration_id = v
            .get("integration_id")
            .and_then(|x| x.as_str())
            .ok_or_else(|| ClientError::Other("integration_id 없음".into()))?
            .to_string();
        let selected_rooms = v
            .get("selected_rooms")
            .and_then(|x| x.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|r| {
                        Some(SelectedRoom {
                            room_id: r.get("room_id")?.as_str()?.to_string(),
                            external_id: coerce_str(r.get("external_id")?)?,
                            last_log_id: r.get("last_logId").map(coerce_i64).unwrap_or(0),
                        })
                    })
                    .collect()
            })
            .unwrap_or_default();
        Ok(Handshake {
            integration_id,
            selected_rooms,
            reset_at: v.get("reset_at").and_then(|x| x.as_str()).map(String::from),
            selected_rooms_version: v.get("selected_rooms_version").map(coerce_i64).unwrap_or(0),
        })
    }

    /// POST …/kakao/messages — 한 방의 새 메시지 묶음. 응답의 `attachment_upload` 로 바이트 올릴 것을 안다.
    pub fn post_messages(
        &self,
        room_id: &str,
        messages: &[MessageInput],
        backfill_done: bool,
    ) -> Result<MessagesResult, ClientError> {
        let url = format!("{}/api/integrations/kakao/messages", self.base);
        let body = serde_json::json!({
            "room_id": room_id,
            "backfill_done": backfill_done,
            "messages": messages.iter().map(message_json).collect::<Vec<_>>(),
        })
        .to_string();
        let mut resp = self
            .agent
            .post(&url)
            .header("Authorization", &self.auth())
            .header("Content-Type", "application/json")
            .send(body.as_bytes())
            .map_err(|e| ClientError::Other(e.to_string()))?;
        let status = resp.status().as_u16();
        let text = read_body(&mut resp);
        check_status(status, &text)?;
        let v: serde_json::Value =
            serde_json::from_str(&text).map_err(|e| ClientError::Other(format!("messages 응답 파싱: {e}")))?;
        let accepted = v.get("accepted").map(coerce_i64).unwrap_or(0);
        let pending = v
            .get("attachment_upload")
            .and_then(|x| x.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|p| {
                        Some(PendingUpload {
                            log_id: coerce_i64(p.get("logId")?),
                            seq: coerce_i64(p.get("seq")?),
                            aid: p.get("aid")?.as_str()?.to_string(),
                        })
                    })
                    .collect()
            })
            .unwrap_or_default();
        Ok(MessagesResult { accepted, pending })
    }

    /// POST …/kakao/attachments/{aid} — multipart `file`(+`name`·`mime`). 50MB 상한은 호출자가 지킨다.
    pub fn post_attachment(
        &self,
        aid: &str,
        bytes: &[u8],
        name: &str,
        mime: Option<&str>,
    ) -> Result<(), ClientError> {
        let url = format!("{}/api/integrations/kakao/attachments/{}", self.base, aid);
        let boundary = format!("----axkakao{}", now_nanos());
        let body = multipart_body(&boundary, bytes, name, mime);
        let content_type = format!("multipart/form-data; boundary={boundary}");
        let mut resp = self
            .agent
            .post(&url)
            .header("Authorization", &self.auth())
            .header("Content-Type", &content_type)
            .send(&body)
            .map_err(|e| ClientError::Other(e.to_string()))?;
        let status = resp.status().as_u16();
        let text = read_body(&mut resp);
        check_status(status, &text)?;
        Ok(())
    }

    /// POST …/kakao/status — 30초 주기 보고. 반환은 `selected_rooms_version`(바뀌면 re-handshake).
    pub fn post_status(&self, status: &StatusInput) -> Result<i64, ClientError> {
        let url = format!("{}/api/integrations/kakao/status", self.base);
        let mut kakao = serde_json::json!({ "state": status.kakao_state });
        if let Some(reason) = status.kakao_reason {
            kakao["reason"] = serde_json::Value::String(reason.to_string());
        }
        let body = serde_json::json!({
            "app": { "device_name": status.device_name, "version": status.version },
            "kakao": kakao,
            "account": { "name": status.account_name },
            "account_changed": status.account_changed,
        })
        .to_string();
        let mut resp = self
            .agent
            .post(&url)
            .header("Authorization", &self.auth())
            .header("Content-Type", "application/json")
            .send(body.as_bytes())
            .map_err(|e| ClientError::Other(e.to_string()))?;
        let st = resp.status().as_u16();
        let text = read_body(&mut resp);
        check_status(st, &text)?;
        let v: serde_json::Value =
            serde_json::from_str(&text).map_err(|e| ClientError::Other(format!("status 응답 파싱: {e}")))?;
        Ok(v.get("selected_rooms_version").map(coerce_i64).unwrap_or(0))
    }
}

fn message_json(m: &MessageInput) -> serde_json::Value {
    let mut value = serde_json::json!({
        "logId": m.log_id,
        "author": m.author,
        "at": m.at_secs,
        "type": m.ktype,
        "text": m.text,
        "attachments": m.attachments.iter().map(|a| serde_json::json!({
            "kind": a.kind.as_str(),
            "seq": a.seq,
            "name": a.name,
            "size": a.size,
            "mime": a.mime,
            "expired": a.expired,
        })).collect::<Vec<_>>(),
    });
    if let Some(from_me) = m.from_me {
        value["from_me"] = serde_json::Value::Bool(from_me);
    }
    value
}

/// 서버로 올리는 메시지 입력으로 바꾼다 — 서버로 보내는 첨부는 **url 을 뺀 메타만**(D-16).
pub fn to_message_input(raw: &RawMessage) -> MessageInput {
    MessageInput {
        log_id: raw.log_id,
        author: raw.author.clone(),
        at_secs: raw.at_secs,
        ktype: raw.ktype,
        text: raw.text.clone(),
        from_me: raw.from_me,
        attachments: raw
            .attachments
            .iter()
            .map(|a| AttachmentInput {
                kind: a.kind,
                seq: a.seq,
                name: a.name.clone(),
                size: a.size,
                mime: a.mime.clone(),
                expired: a.expired,
            })
            .collect(),
    }
}

fn read_body(resp: &mut ureq::http::Response<ureq::Body>) -> String {
    let mut s = String::new();
    let _ = resp.body_mut().as_reader().read_to_string(&mut s);
    s
}

fn check_status(status: u16, body: &str) -> Result<(), ClientError> {
    if (200..300).contains(&status) {
        return Ok(());
    }
    match status {
        401 => Err(ClientError::Unauthorized),
        403 if body.contains("room_not_selected") => Err(ClientError::RoomNotSelected),
        409 if body.contains("handshake_required") => Err(ClientError::HandshakeRequired),
        _ => Err(ClientError::Other(format!("HTTP {status}: {}", body.chars().take(200).collect::<String>()))),
    }
}

/// int | numeric-string | null → i64(없으면 0).
fn coerce_i64(v: &serde_json::Value) -> i64 {
    v.as_i64()
        .or_else(|| v.as_u64().map(|u| u as i64))
        .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
        .unwrap_or(0)
}

fn coerce_str(v: &serde_json::Value) -> Option<String> {
    match v {
        serde_json::Value::String(s) => Some(s.clone()),
        serde_json::Value::Number(n) => Some(n.to_string()),
        _ => None,
    }
}

fn now_nanos() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0)
}

/// `multipart/form-data` 바디 — `file`(바이트)·`name`·`mime`. 바이너리 안전하게 바이트로 조립한다.
fn multipart_body(boundary: &str, bytes: &[u8], name: &str, mime: Option<&str>) -> Vec<u8> {
    let mut body = Vec::with_capacity(bytes.len() + 512);
    let text = |body: &mut Vec<u8>, s: &str| body.extend_from_slice(s.as_bytes());

    // name 필드
    text(&mut body, &format!("--{boundary}\r\n"));
    text(&mut body, "Content-Disposition: form-data; name=\"name\"\r\n\r\n");
    text(&mut body, name);
    text(&mut body, "\r\n");

    // mime 필드(있을 때만)
    if let Some(m) = mime {
        text(&mut body, &format!("--{boundary}\r\n"));
        text(&mut body, "Content-Disposition: form-data; name=\"mime\"\r\n\r\n");
        text(&mut body, m);
        text(&mut body, "\r\n");
    }

    // file 파트
    let filename = if name.is_empty() { "attachment" } else { name };
    text(&mut body, &format!("--{boundary}\r\n"));
    text(
        &mut body,
        &format!("Content-Disposition: form-data; name=\"file\"; filename=\"{}\"\r\n", escape_filename(filename)),
    );
    text(&mut body, &format!("Content-Type: {}\r\n\r\n", mime.unwrap_or("application/octet-stream")));
    body.extend_from_slice(bytes);
    text(&mut body, "\r\n");

    text(&mut body, &format!("--{boundary}--\r\n"));
    body
}

fn escape_filename(name: &str) -> String {
    name.replace('"', "'").replace(['\r', '\n'], "_")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn coerce_i64_는_정수_문자열_널을_받는다() {
        assert_eq!(coerce_i64(&serde_json::json!(42)), 42);
        assert_eq!(coerce_i64(&serde_json::json!("123456789012345678")), 123456789012345678);
        assert_eq!(coerce_i64(&serde_json::json!(null)), 0);
        assert_eq!(coerce_i64(&serde_json::json!("abc")), 0);
    }

    #[test]
    fn 메시지_json_은_at_과_type_필드를_쓴다() {
        let m = MessageInput {
            log_id: 10,
            author: Some("박지윤".into()),
            at_secs: 1_700_000_000,
            ktype: 2,
            text: None,
            from_me: Some(true),
            attachments: vec![AttachmentInput {
                kind: AttachKind::Image,
                seq: 0,
                name: String::new(),
                size: Some(99),
                mime: Some("image/jpeg".into()),
                expired: false,
            }],
        };
        let v = message_json(&m);
        assert_eq!(v["logId"], 10);
        assert_eq!(v["at"], 1_700_000_000);
        assert_eq!(v["type"], 2);
        assert_eq!(v["attachments"][0]["kind"], "image");
        assert_eq!(v["attachments"][0]["seq"], 0);
        assert_eq!(v["from_me"], true);
        // 모르면(내 userId 복구 실패) 키 자체를 싣지 않는다 — 서버가 null 로 둔다(SPEC-009 AC-12)
        let unknown = message_json(&MessageInput { from_me: None, ..m.clone() });
        assert!(unknown.get("from_me").is_none());
        let theirs = message_json(&MessageInput { from_me: Some(false), ..m });
        assert_eq!(theirs["from_me"], false);
    }

    #[test]
    fn multipart_바디는_file_파트를_담는다() {
        let body = multipart_body("B", b"\x00\x01hi", "사진.jpg", Some("image/jpeg"));
        let s = String::from_utf8_lossy(&body);
        assert!(s.contains("--B\r\n"));
        assert!(s.contains("name=\"file\"; filename=\"사진.jpg\""));
        assert!(s.contains("Content-Type: image/jpeg"));
        assert!(s.contains("name=\"name\""));
        assert!(s.contains("name=\"mime\""));
        assert!(s.ends_with("--B--\r\n"));
        // 바이너리 바이트가 그대로 들어간다.
        assert!(body.windows(4).any(|w| w == b"\x00\x01hi"));
    }

    #[test]
    fn 상태코드_매핑() {
        assert!(matches!(check_status(401, ""), Err(ClientError::Unauthorized)));
        assert!(matches!(
            check_status(403, r#"{"detail":{"code":"room_not_selected"}}"#),
            Err(ClientError::RoomNotSelected)
        ));
        assert!(matches!(
            check_status(409, r#"{"detail":{"code":"handshake_required"}}"#),
            Err(ClientError::HandshakeRequired)
        ));
        assert!(check_status(202, "").is_ok());
        assert!(matches!(check_status(500, "boom"), Err(ClientError::Other(_))));
    }
}
