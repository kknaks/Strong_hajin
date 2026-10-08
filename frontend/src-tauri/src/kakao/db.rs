//! 복호화된 카톡 DB 를 **읽기 전용**으로 열어 방 목록·메시지·첨부 메타를 읽는다.
//!
//! 여는 방식은 mykakao `db.py`(mode=ro · NullPool — 요청마다 새 연결로 최신 WAL 을 본다)를 따른다.
//! 카톡 DB 를 **쓰거나 바꾸지 않는다**(SPEC-009 §3.1·§5 · AC-08). 스키마(테이블·컬럼)는 카톡이
//! 만든 것이고 우리가 만들지 않는다 — 필요한 컬럼만 읽는다.

use std::collections::HashMap;

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
    /// 지금 로그인한 카톡 계정이 보낸 줄인가 = `NTChatMessage.authorId == NTChatContext.userId`(SPEC-009 v0.6.1 · OQ-K01 닫힘 ·
    /// P0-1 대조). 내 userId 를 못 읽으면 `None` — 지어내지 않고 싣지 않는다(서버는 `null` · AC-12).
    pub from_me: Option<bool>,
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

/// 로그인한 내 userId — `NTChatContext` 에 있다(단체방 표시 멤버에서 나를 빼는 데 쓴다).
fn self_user_id(conn: &Connection) -> i64 {
    conn.query_row("SELECT userId FROM NTChatContext LIMIT 1", [], |row| row.get(0))
        .unwrap_or(0)
}

/// `userId → 표시 이름`. 한 번에 다 읽어 방·그룹멤버 이름 풀이에 쓴다(N+1 회피). 이름 우선순위는
/// **내가 준 이름(friendNickName) → 프로필 이름(displayName) → 닉네임** — 카톡에 보이는 이름과 맞춘다.
fn user_names(conn: &Connection) -> Result<HashMap<i64, String>, OpenFailure> {
    let mut stmt = conn
        .prepare("SELECT userId, friendNickName, displayName, nickName FROM NTUser WHERE linkId = 0")
        .map_err(|_| OpenFailure::Version)?;
    let rows = stmt
        .query_map([], |row| {
            let uid: i64 = row.get(0)?;
            let friend: Option<String> = row.get(1).ok().flatten();
            let display: Option<String> = row.get(2).ok().flatten();
            let nick: Option<String> = row.get(3).ok().flatten();
            Ok((uid, friend, display, nick))
        })
        .map_err(|_| OpenFailure::Version)?;
    let mut map = HashMap::new();
    for row in rows.flatten() {
        let (uid, friend, display, nick) = row;
        if let Some(name) = non_empty(friend).or(non_empty(display)).or(non_empty(nick)) {
            map.insert(uid, name);
        }
    }
    Ok(map)
}

/// `displayMemberIds` BLOB(= 이름 없는 단체방의 **표시 멤버** userId 들)를 푼다. 카톡 Mac 은 이를
/// **바이너리 plist 배열**(bplist00 · 정수들 · 보통 나 자신은 빠져 있다)로 둔다(DB 조사).
pub fn parse_member_ids(blob: &[u8]) -> Vec<i64> {
    let Ok(value) = plist::Value::from_reader(std::io::Cursor::new(blob)) else {
        return Vec::new();
    };
    let Some(arr) = value.as_array() else {
        return Vec::new();
    };
    arr.iter()
        .filter_map(|v| {
            v.as_signed_integer()
                .or_else(|| v.as_unsigned_integer().map(|u| u as i64))
        })
        .collect()
}

/// 이름 없는 단체방의 이름 = **표시 멤버 이름을 「, 」로 이은 것**(카톡 방 제목 방식). 멤버가 하나도
/// 안 풀리면 None. `exclude` 는 나 자신(혹시 섞여 있으면 뺀다).
fn group_name(blob: &[u8], names: &HashMap<i64, String>, exclude: i64) -> Option<String> {
    let joined: Vec<String> = parse_member_ids(blob)
        .into_iter()
        .filter(|&id| id != exclude)
        .filter_map(|id| names.get(&id).cloned())
        .collect();
    if joined.is_empty() {
        None
    } else {
        Some(joined.join(", "))
    }
}

/// 방 목록 — **1:1·단체만**(오픈채팅 제외). 오픈채팅은 `linkId > 0`(DB 조사). 1:1 은
/// `directChatMemberUserId > 0`. 이름: **사용자가 정한 방 이름(chatName)** → 1:1 상대 이름 /
/// 단체방은 표시 멤버 이름을 「, 」로 이은 것(카톡과 같게) → "(이름 없음)".
pub fn list_rooms(conn: &Connection) -> Result<Vec<Room>, OpenFailure> {
    let names = user_names(conn)?;
    let me = self_user_id(conn);
    let sql = "SELECT chatId, chatName, activeMembersCount, directChatMemberUserId, linkId, displayMemberIds \
               FROM NTChatRoom WHERE lastUpdatedAt > 0 ORDER BY lastUpdatedAt DESC";
    let mut stmt = conn.prepare(sql).map_err(|_| OpenFailure::Version)?;
    let rows = stmt
        .query_map([], |row| {
            let chat_id: i64 = row.get(0)?;
            let chat_name: Option<String> = row.get(1).ok().flatten();
            let members: Option<i64> = row.get(2).ok().flatten();
            let direct_uid: Option<i64> = row.get(3).ok().flatten();
            let link_id: Option<i64> = row.get(4).ok().flatten();
            let member_blob: Option<Vec<u8>> = row.get(5).ok().flatten();
            Ok((chat_id, chat_name, members, direct_uid, link_id, member_blob))
        })
        .map_err(|_| OpenFailure::Version)?;

    let mut out = Vec::new();
    for row in rows.flatten() {
        let (chat_id, chat_name, members, direct_uid, link_id, member_blob) = row;
        // 오픈채팅 제외(D-18).
        if link_id.unwrap_or(0) > 0 {
            continue;
        }
        let direct_uid = direct_uid.unwrap_or(0);
        let is_direct = direct_uid > 0;
        let kind = if is_direct { RoomKind::Direct } else { RoomKind::Group };
        let name = non_empty(chat_name)
            .or_else(|| {
                if is_direct {
                    names.get(&direct_uid).cloned()
                } else {
                    member_blob.as_deref().and_then(|b| group_name(b, &names, me))
                }
            })
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
    let me = self_user_id(conn);
    let sql = "SELECT m.logId, m.type, m.message, m.attachment, m.sentAt, \
               u.displayName, u.friendNickName, u.nickName, m.authorId \
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
                let author_id: Option<i64> = row.get(8).ok().flatten();
                Ok((log_id, ktype, message, attachment, sent_at, display, friend, nick, author_id))
            },
        )
        .map_err(|_| OpenFailure::Version)?;

    let mut out = Vec::new();
    for row in rows.flatten() {
        let (log_id, ktype, message, attachment, sent_at, display, friend, nick, author_id) = row;
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
            // 작성자 없는 줄(authorId 0 · type 1999 등)은 내가 아니다 → false
            from_me: (me != 0).then(|| author_id == Some(me)),
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

    /// 복호화된 DB 와 같은 모양의 작은 평문 DB(필요한 칸만) — 이름·id 는 가상이다.
    fn tiny_db(me: Option<i64>) -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE NTChatContext (userId INTEGER);
             CREATE TABLE NTUser (userId INTEGER, linkId INTEGER, displayName TEXT, friendNickName TEXT, nickName TEXT);
             CREATE TABLE NTChatMessage (chatId INTEGER, logId INTEGER, authorId INTEGER, type INTEGER, message TEXT, attachment TEXT, sentAt INTEGER);
             INSERT INTO NTUser VALUES (100, 0, '나', NULL, NULL), (200, 0, '상대', NULL, NULL);
             INSERT INTO NTChatMessage VALUES
               (7, 1, 100, 1, '내가 보낸 줄', NULL, 1700000000),
               (7, 2, 200, 1, '받은 줄', NULL, 1700000001),
               (7, 3, 0, 1999, NULL, NULL, 1700000002),
               (7, 4, 0, 0, '시스템', NULL, 1700000003);",
        )
        .unwrap();
        if let Some(id) = me {
            conn.execute("INSERT INTO NTChatContext VALUES (?1)", rusqlite::params![id]).unwrap();
        }
        conn
    }

    #[test]
    fn 내가_보낸_줄은_from_me_참_받은_줄과_작성자_없는_줄은_거짓() {
        let rows = fetch_messages(&tiny_db(Some(100)), 7, 0, 10).unwrap();
        // type 0(시스템)은 지금처럼 건너뛴다
        assert_eq!(rows.iter().map(|m| m.log_id).collect::<Vec<_>>(), vec![1, 2, 3]);
        assert_eq!(rows.iter().map(|m| m.from_me).collect::<Vec<_>>(), vec![Some(true), Some(false), Some(false)]);
    }

    #[test]
    fn 내_userid_를_못_읽으면_from_me_를_싣지_않는다() {
        let rows = fetch_messages(&tiny_db(None), 7, 0, 10).unwrap();
        assert!(rows.iter().all(|m| m.from_me.is_none()));
    }

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

    /// 카톡 Mac 의 displayMemberIds 와 같은 **바이너리 plist 정수 배열**을 만든다.
    fn bplist_ids(ids: &[i64]) -> Vec<u8> {
        let arr = plist::Value::Array(ids.iter().map(|&i| plist::Value::Integer(i.into())).collect());
        let mut buf = Vec::new();
        arr.to_writer_binary(&mut buf).unwrap();
        buf
    }

    #[test]
    fn 표시멤버_bplist_를_정수로_푼다() {
        let blob = bplist_ids(&[61342504, 8596879, 29083527]);
        assert_eq!(parse_member_ids(&blob), vec![61342504, 8596879, 29083527]);
        assert!(parse_member_ids(&bplist_ids(&[])).is_empty());
        assert!(parse_member_ids(b"not a plist").is_empty());
    }

    #[test]
    fn 단체방_이름은_멤버_이름을_쉼표로_잇는다() {
        let mut names = HashMap::new();
        names.insert(1i64, "엄마".to_string());
        names.insert(2i64, "하지니♥️".to_string());
        names.insert(3i64, "형".to_string());
        let me = 99i64;
        // 표시 멤버 셋 — 나(99)는 섞여 있어도 빠진다.
        let blob = bplist_ids(&[1, 2, me, 3]);
        assert_eq!(group_name(&blob, &names, me).as_deref(), Some("엄마, 하지니♥️, 형"));
        // 아무도 못 풀면 None(→ 호출부가 "(이름 없음)").
        assert_eq!(group_name(&bplist_ids(&[404]), &names, me), None);
        assert_eq!(group_name(&bplist_ids(&[]), &names, me), None);
    }
}
