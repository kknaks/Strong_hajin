//! 복호화된 카톡 DB 를 **읽기 전용**으로 열어 방 목록·메시지·첨부 메타를 읽는다.
//!
//! 여는 방식은 mykakao `db.py`(mode=ro · NullPool — 요청마다 새 연결로 최신 WAL 을 본다)를 따른다.
//! 카톡 DB 를 **쓰거나 바꾸지 않는다**(SPEC-009 §3.1·§5 · AC-08). 스키마(테이블·컬럼)는 카톡이
//! 만든 것이고 우리가 만들지 않는다 — 필요한 컬럼만 읽는다.

use rusqlite::{Connection, OpenFlags};

use super::crypto::{OpenFailure, Resolved};

/// 서버 §4.6 첨부 `kind`. image|album|file 만 바이트를 올리고(용량·만료 통과 시), 나머지는 메타만.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AttachKind {
    Image,
    Album,
    File,
    Video,
    Audio,
    Sticker,
}

impl AttachKind {
    pub fn as_str(self) -> &'static str {
        match self {
            AttachKind::Image => "image",
            AttachKind::Album => "album",
            AttachKind::File => "file",
            AttachKind::Video => "video",
            AttachKind::Audio => "audio",
            AttachKind::Sticker => "sticker",
        }
    }
}

#[derive(Debug, Clone)]
pub struct RawAttachment {
    pub kind: AttachKind,
    pub seq: i64,
    pub name: String,
    pub size: Option<i64>,
    pub mime: Option<String>,
    pub expired: bool,
    /// CDN 주소 — **서버로 보내지 않는다.** 수집기가 받아서 바이트만 올린다. 만료/메타만이면 None.
    pub url: Option<String>,
}

#[derive(Debug, Clone)]
pub struct RawMessage {
    pub log_id: i64,
    pub author: Option<String>,
    /// 보낸 시각 — epoch **초**(DB 값 그대로). 서버는 1e10 미만을 초로 본다.
    pub at_secs: i64,
    pub ktype: i64,
    pub text: Option<String>,
    pub attachments: Vec<RawAttachment>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RoomKind {
    Direct,
    Group,
}

impl RoomKind {
    pub fn as_str(self) -> &'static str {
        match self {
            RoomKind::Direct => "direct",
            RoomKind::Group => "group",
        }
    }
}

#[derive(Debug, Clone)]
pub struct Room {
    pub chat_id: i64,
    pub kind: RoomKind,
    pub name: String,
    pub member_count: Option<i64>,
}

/// 복호화된 read-only 연결. 요청(폴링)마다 새로 열어 최신 커밋(WAL)을 본다 — mykakao `raw_ro_connection`.
/// compat 3 으로 먼저, 안 열리면 4 로 다시(= 카톡 버전 차이, `reason=version`).
pub fn open(resolved: &Resolved) -> Result<Connection, OpenFailure> {
    for compat in [3, 4] {
        match try_open(resolved, compat) {
            Ok(conn) => return Ok(conn),
            Err(_) if compat == 3 => continue,
            Err(f) => return Err(f),
        }
    }
    Err(OpenFailure::Version)
}

fn try_open(resolved: &Resolved, compat: u8) -> Result<Connection, OpenFailure> {
    let uri = format!("file:{}?mode=ro", resolved.db_path.display());
    let flags = OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI | OpenFlags::SQLITE_OPEN_NO_MUTEX;
    let conn = Connection::open_with_flags(&uri, flags).map_err(map_open_err)?;
    // cipher_default_compatibility 는 key 보다 **먼저** 와야 한다(mykakao db.py).
    conn.pragma_update(None, "cipher_default_compatibility", compat as i64)
        .map_err(|_| OpenFailure::Version)?;
    // PRAGMA key — 작은따옴표 안의 256-hex. 키는 로그에 남기지 않는다.
    conn.execute_batch(&format!("PRAGMA key='{}';", resolved.key))
        .map_err(|_| OpenFailure::Version)?;
    conn.busy_timeout(std::time::Duration::from_millis(3000))
        .map_err(|_| OpenFailure::Version)?;
    // 키 검증 — 틀리면 여기서 "file is not a database" 로 실패한다.
    conn.query_row("SELECT count(*) FROM sqlite_master", [], |_| Ok(()))
        .map_err(|_| OpenFailure::Version)?;
    Ok(conn)
}

fn map_open_err(e: rusqlite::Error) -> OpenFailure {
    let s = e.to_string();
    if s.contains(" permission") || s.contains(" permiss") || s.contains("authoriz") {
        OpenFailure::Permission
    } else {
        OpenFailure::Version
    }
}

fn non_empty(s: Option<String>) -> Option<String> {
    s.filter(|v| !v.trim().is_empty())
}

/// 방 목록 — **1:1·단체만**(오픈채팅 제외). 오픈채팅은 `linkId > 0`(DB 조사). 1:1 은
/// `directChatMemberUserId > 0`. 이름은 chatName → 1:1 상대 이름 → "(이름 없음)".
pub fn list_rooms(conn: &Connection) -> Result<Vec<Room>, OpenFailure> {
    let sql = "SELECT r.chatId, r.chatName, r.activeMembersCount, r.directChatMemberUserId, \
               r.linkId, u.displayName, u.friendNickName, u.nickName \
               FROM NTChatRoom r \
               LEFT JOIN NTUser u ON u.userId = r.directChatMemberUserId AND u.linkId = 0 \
               WHERE r.lastUpdatedAt > 0 \
               ORDER BY r.lastUpdatedAt DESC";
    let mut stmt = conn.prepare(sql).map_err(|_| OpenFailure::Version)?;
    let rows = stmt
        .query_map([], |row| {
            let chat_id: i64 = row.get(0)?;
            let chat_name: Option<String> = row.get(1).ok().flatten();
            let members: Option<i64> = row.get(2).ok().flatten();
            let direct_uid: Option<i64> = row.get(3).ok().flatten();
            let link_id: Option<i64> = row.get(4).ok().flatten();
            let display: Option<String> = row.get(5).ok().flatten();
            let friend: Option<String> = row.get(6).ok().flatten();
            let nick: Option<String> = row.get(7).ok().flatten();
            Ok((chat_id, chat_name, members, direct_uid, link_id, display, friend, nick))
        })
        .map_err(|_| OpenFailure::Version)?;

    let mut out = Vec::new();
    for row in rows.flatten() {
        let (chat_id, chat_name, members, direct_uid, link_id, display, friend, nick) = row;
        // 오픈채팅 제외(D-18).
        if link_id.unwrap_or(0) > 0 {
            continue;
        }
        let is_direct = direct_uid.unwrap_or(0) > 0;
        let kind = if is_direct { RoomKind::Direct } else { RoomKind::Group };
        let name = non_empty(chat_name)
            .or_else(|| if is_direct { non_empty(display) } else { None })
            .or_else(|| if is_direct { non_empty(friend) } else { None })
            .or_else(|| if is_direct { non_empty(nick) } else { None })
            .unwrap_or_else(|| "(이름 없음)".to_string());
        out.push(Room { chat_id, kind, name, member_count: members });
    }
    Ok(out)
}

/// 한 방의 메시지 — `logId > after` 인 것만 오래된→최신 순으로, 최대 `limit` 건.
pub fn fetch_messages(
    conn: &Connection,
    chat_id: i64,
    after_log_id: i64,
    limit: usize,
) -> Result<Vec<RawMessage>, OpenFailure> {
    let sql = "SELECT m.logId, m.type, m.message, m.attachment, m.sentAt, \
               u.displayName, u.friendNickName, u.nickName \
               FROM NTChatMessage m \
               LEFT JOIN NTUser u ON u.userId = m.authorId AND u.linkId = 0 \
               WHERE m.chatId = ?1 AND m.logId > ?2 \
               ORDER BY m.logId ASC LIMIT ?3";
    let mut stmt = conn.prepare(sql).map_err(|_| OpenFailure::Version)?;
    let rows = stmt
        .query_map(
            rusqlite::params![chat_id, after_log_id, limit as i64],
            |row| {
                let log_id: i64 = row.get(0)?;
                let ktype: Option<i64> = row.get(1).ok().flatten();
                let message: Option<String> = row.get(2).ok().flatten();
                let attachment: Option<String> = row.get(3).ok().flatten();
                let sent_at: Option<i64> = row.get(4).ok().flatten();
                let display: Option<String> = row.get(5).ok().flatten();
                let friend: Option<String> = row.get(6).ok().flatten();
                let nick: Option<String> = row.get(7).ok().flatten();
                Ok((log_id, ktype, message, attachment, sent_at, display, friend, nick))
            },
        )
        .map_err(|_| OpenFailure::Version)?;

    let mut out = Vec::new();
    for row in rows.flatten() {
        let (log_id, ktype, message, attachment, sent_at, display, friend, nick) = row;
        let ktype = ktype.unwrap_or(1);
        // type 0 = 시스템 메시지(authorId 0) — 업무 내용이 아니라 건너뛴다(DB 조사 §5).
        if ktype == 0 {
            continue;
        }
        let author = non_empty(display).or(non_empty(friend)).or(non_empty(nick));
        let attachments = parse_attachments(ktype, attachment.as_deref());
        out.push(RawMessage {
            log_id,
            author,
            at_secs: sent_at.unwrap_or(0),
            ktype,
            text: non_empty(message),
            attachments,
        });
    }
    Ok(out)
}

/// 로그인한 계정의 표시 이름(status `account.name`). 내 userId 로 NTUser 를 찾는다.
pub fn account_name(conn: &Connection, user_id: i64) -> Option<String> {
    conn.query_row(
        "SELECT displayName, nickName FROM NTUser WHERE userId = ?1 AND linkId = 0 LIMIT 1",
        rusqlite::params![user_id],
        |row| {
            let display: Option<String> = row.get(0).ok().flatten();
            let nick: Option<String> = row.get(1).ok().flatten();
            Ok(non_empty(display).or(non_empty(nick)))
        },
    )
    .ok()
    .flatten()
}

/// `attachment` TEXT(JSON)에서 첨부 메타를 뽑는다(DB 조사 §3). 종류별 키가 다르다.
/// url 은 **로컬 보관용**(서버로 보내지 않는다). 만료는 url 의 `expires`(초)·`expire`(ms) 중 짧은 쪽.
fn parse_attachments(ktype: i64, raw: Option<&str>) -> Vec<RawAttachment> {
    let Some(raw) = raw.filter(|s| !s.trim().is_empty()) else {
        return Vec::new();
    };
    let Ok(v) = serde_json::from_str::<serde_json::Value>(raw) else {
        return Vec::new();
    };
    let now = now_secs();
    match ktype {
        2 => {
            // 사진
            let url = v.get("url").and_then(|x| x.as_str()).map(String::from);
            let size = v.get("s").and_then(as_i64);
            let mime = v.get("mt").or_else(|| v.get("cmt")).and_then(|x| x.as_str()).map(String::from);
            let expired = is_expired(&v, url.as_deref(), now);
            vec![RawAttachment {
                kind: AttachKind::Image,
                seq: 0,
                name: String::new(),
                size,
                mime,
                expired,
                url: if expired { None } else { url },
            }]
        }
        27 => {
            // 앨범(여러 장) — imageUrls[] · sl[](size) · mtl[](mime) 평행 배열.
            let urls = v.get("imageUrls").and_then(|x| x.as_array()).cloned().unwrap_or_default();
            let sizes = v.get("sl").and_then(|x| x.as_array()).cloned().unwrap_or_default();
            let mimes = v.get("mtl").and_then(|x| x.as_array()).cloned().unwrap_or_default();
            let expired_all = is_expired(&v, None, now);
            urls.iter()
                .enumerate()
                .map(|(i, u)| {
                    let url = u.as_str().map(String::from);
                    RawAttachment {
                        kind: AttachKind::Album,
                        seq: i as i64,
                        name: String::new(),
                        size: sizes.get(i).and_then(as_i64),
                        mime: mimes.get(i).and_then(|x| x.as_str()).map(String::from),
                        expired: expired_all,
                        url: if expired_all { None } else { url },
                    }
                })
                .collect()
        }
        18 => {
            // 파일
            let url = v.get("url").and_then(|x| x.as_str()).map(String::from);
            let size = v.get("size").or_else(|| v.get("s")).and_then(as_i64);
            let name = v.get("name").and_then(|x| x.as_str()).unwrap_or("").to_string();
            let expired = is_expired(&v, url.as_deref(), now);
            vec![RawAttachment {
                kind: AttachKind::File,
                seq: 0,
                name,
                size,
                mime: None,
                expired,
                url: if expired { None } else { url },
            }]
        }
        3 => vec![meta_only(AttachKind::Video, &v)],
        4 | 5 => vec![meta_only(AttachKind::Audio, &v)],
        6 | 12 | 20 => vec![RawAttachment {
            kind: AttachKind::Sticker,
            seq: 0,
            name: v.get("name").and_then(|x| x.as_str()).unwrap_or("").to_string(),
            size: None,
            mime: None,
            expired: false,
            url: None,
        }],
        // 1 텍스트 · 26 답장(인용) · 16385+ 피드 · 71 등 — 첨부 없음.
        _ => Vec::new(),
    }
}

fn meta_only(kind: AttachKind, v: &serde_json::Value) -> RawAttachment {
    RawAttachment {
        kind,
        seq: 0,
        name: v.get("name").and_then(|x| x.as_str()).unwrap_or("").to_string(),
        size: v.get("s").and_then(as_i64),
        mime: None,
        expired: false,
        url: None,
    }
}

fn as_i64(v: &serde_json::Value) -> Option<i64> {
    v.as_i64().or_else(|| v.as_str().and_then(|s| s.parse().ok()))
}

/// url 의 `expires`(초) · JSON `expire`(ms) 중 **짧은 쪽**으로 만료를 본다(사진 ~3일 · 파일 ~14일).
fn is_expired(v: &serde_json::Value, url: Option<&str>, now: i64) -> bool {
    let mut deadline: Option<i64> = None;
    if let Some(u) = url {
        if let Some(exp) = parse_query_expires(u) {
            deadline = Some(exp);
        }
    }
    if let Some(ms) = v.get("expire").and_then(as_i64) {
        let secs = ms / 1000;
        deadline = Some(deadline.map_or(secs, |d| d.min(secs)));
    }
    deadline.map(|d| d <= now).unwrap_or(false)
}

fn parse_query_expires(url: &str) -> Option<i64> {
    let q = url.split('?').nth(1)?;
    for pair in q.split('&') {
        if let Some(rest) = pair.strip_prefix("expires=") {
            return rest.parse().ok();
        }
    }
    None
}

fn now_secs() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 사진_첨부를_뽑는다() {
        let json = r#"{"url":"https://talk.kakaocdn.net/x?expires=9999999999&signature=z","s":1234,"mt":"image/jpeg","expire":9999999999000}"#;
        let atts = parse_attachments(2, Some(json));
        assert_eq!(atts.len(), 1);
        assert_eq!(atts[0].kind, AttachKind::Image);
        assert_eq!(atts[0].size, Some(1234));
        assert_eq!(atts[0].mime.as_deref(), Some("image/jpeg"));
        assert!(!atts[0].expired);
        assert!(atts[0].url.is_some());
    }

    #[test]
    fn 만료된_사진은_url_을_버린다() {
        let json = r#"{"url":"https://talk.kakaocdn.net/x?expires=1&signature=z","s":1,"expire":1000}"#;
        let atts = parse_attachments(2, Some(json));
        assert!(atts[0].expired);
        assert!(atts[0].url.is_none());
    }

    #[test]
    fn 앨범은_여러_장() {
        let json = r#"{"imageUrls":["https://a/1?expires=9999999999","https://a/2?expires=9999999999"],"sl":[10,20],"mtl":["image/png","image/jpeg"]}"#;
        let atts = parse_attachments(27, Some(json));
        assert_eq!(atts.len(), 2);
        assert_eq!(atts[0].seq, 0);
        assert_eq!(atts[1].seq, 1);
        assert_eq!(atts[1].size, Some(20));
        assert!(atts.iter().all(|a| a.kind == AttachKind::Album));
    }

    #[test]
    fn 파일_동영상_스티커_분류() {
        assert_eq!(parse_attachments(18, Some(r#"{"url":"https://x?expires=9999999999","name":"a.pdf","size":5}"#))[0].kind, AttachKind::File);
        assert_eq!(parse_attachments(3, Some(r#"{"url":"https://x","d":10}"#))[0].kind, AttachKind::Video);
        assert_eq!(parse_attachments(6, Some(r#"{"name":"하트"}"#))[0].kind, AttachKind::Sticker);
        assert!(parse_attachments(1, Some("")).is_empty());
        assert!(parse_attachments(26, Some(r#"{"src_message":"안녕"}"#)).is_empty());
    }
}
