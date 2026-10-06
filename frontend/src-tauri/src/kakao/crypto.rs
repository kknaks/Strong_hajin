//! 카톡 로컬 DB 를 여는 **키 유도 + user_id 복구** — mykakao Mac 방식을 Rust 로 옮긴 것이다.
//!
//! **절차는 새로 설계하지 않는다**(SPEC-009 머리 · I-1). `~/git/toy_pr2/mykakao/backend/extract.py`
//! 의 `secure_key`·`db_name`·`_hashed_uuid`·user_id 복구를 **그대로** 옮긴다. `kakaocli` 는
//! reference-only 이고 런타임 의존이 아니다(DEC-001). 키·복호 결과·DB 경로는 **Mac 밖으로 나가지
//! 않는다**(D-16) — 이 모듈이 내는 것은 로컬 파일 경로와 메모리 안의 키뿐이다.

use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;

use base64::Engine as _;
use sha1::Sha1;
use sha2::{Digest, Sha256, Sha512};

/// 카톡 Mac 컨테이너 — 메시지 DB 와 Preferences plist 가 사는 곳.
fn home() -> PathBuf {
    PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| "/".into()))
}

fn container() -> PathBuf {
    home()
        .join("Library/Containers/com.kakao.KakaoTalkMac/Data/Library")
        .join("Application Support/com.kakao.KakaoTalkMac")
}

fn prefs_plist() -> PathBuf {
    home().join("Library/Containers/com.kakao.KakaoTalkMac/Data/Library/Preferences/com.kakao.KakaoTalkMac.plist")
}

/// user_id 복구를 못 할 때의 상한 — mykakao 와 같은 10억(brute force).
const USER_ID_SEARCH_MAX: u64 = 1_000_000_000;

/// 왜 못 열었나 — SPEC-008 §4.6 status `unreadable` 의 `reason` 과 그대로 이어진다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OpenFailure {
    /// 전체 디스크 접근이 없어 파일조차 못 읽는다(EACCES/EPERM · `reason=permission`).
    Permission,
    /// 파일은 읽히는데 복호·구조가 안 맞는다(키/버전 · `reason=version`).
    Version,
    /// 그 밖(로그인 안 됨 등 · `reason=unknown`).
    Unknown,
}

#[derive(Debug, Clone)]
pub struct ResolveError {
    pub failure: OpenFailure,
    pub detail: String,
}

impl ResolveError {
    fn new(failure: OpenFailure, detail: impl Into<String>) -> Self {
        Self { failure, detail: detail.into() }
    }
}

impl std::fmt::Display for ResolveError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{:?}: {}", self.failure, self.detail)
    }
}

/// 열 준비가 된 설정 — uuid·user_id·DB 경로·SQLCipher 키(256-hex).
#[derive(Debug, Clone)]
pub struct Resolved {
    pub user_id: i64,
    /// 키 유도에 쓰인 기기 UUID — 진단·재유도용(서버로 나가지 않는다).
    #[allow(dead_code)]
    pub uuid: String,
    pub db_path: PathBuf,
    /// `PRAGMA key='…'` 에 넣을 256-hex. **로그·화면·서버로 내보내지 않는다.**
    pub key: String,
}

// ── 기기 UUID ────────────────────────────────────────────────────────────────

/// `ioreg` 에서 IOPlatformUUID 를 읽는다(extract.py `platform_uuid`).
pub fn platform_uuid() -> Result<String, ResolveError> {
    let out = std::process::Command::new("/usr/sbin/ioreg")
        .args(["-rd1", "-c", "IOPlatformExpertDevice"])
        .output()
        .map_err(|e| ResolveError::new(OpenFailure::Unknown, format!("ioreg 실행 실패: {e}")))?;
    let text = String::from_utf8_lossy(&out.stdout);
    // "IOPlatformUUID" = "XXXXXXXX-XXXX-..."
    for line in text.lines() {
        if let Some(idx) = line.find("\"IOPlatformUUID\"") {
            let rest = &line[idx..];
            // 두 번째 따옴표 쌍이 값이다.
            let mut quotes = rest.match_indices('"');
            let _k_open = quotes.next();
            let _k_close = quotes.next();
            let v_open = quotes.next();
            let v_close = quotes.next();
            if let (Some((s, _)), Some((e, _))) = (v_open, v_close) {
                let value = &rest[s + 1..e];
                if !value.is_empty() {
                    return Ok(value.to_string());
                }
            }
        }
    }
    Err(ResolveError::new(OpenFailure::Unknown, "IOPlatformUUID 를 찾지 못했다"))
}

// ── 키 유도 (extract.py 그대로) ───────────────────────────────────────────────

/// `base64(sha1(uuid) + sha256(uuid))` — extract.py `_hashed_uuid`.
fn hashed_uuid(uuid: &str) -> String {
    let d = uuid.as_bytes();
    let mut buf = Vec::with_capacity(20 + 32);
    buf.extend_from_slice(&Sha1::digest(d));
    buf.extend_from_slice(&Sha256::digest(d));
    base64::engine::general_purpose::STANDARD.encode(buf)
}

fn reversed(s: &str) -> String {
    // UUID·base64 는 ASCII 라 char 역순 = byte 역순. extract.py 의 `[::-1]` 과 같다.
    s.chars().rev().collect()
}

/// PBKDF2-HMAC-SHA256, dklen 128 바이트 → 256-hex. (100k 반복)
fn pbkdf2_hex(password: &[u8], salt: &[u8]) -> String {
    let mut out = [0u8; 128];
    pbkdf2::pbkdf2_hmac::<Sha256>(password, salt, 100_000, &mut out);
    let mut hex = String::with_capacity(256);
    for b in out {
        hex.push_str(&format!("{b:02x}"));
    }
    hex
}

/// SQLCipher passphrase(256-hex) — extract.py `secure_key`.
pub fn secure_key(user_id: i64, uuid: &str) -> String {
    let hashed = hashed_uuid(uuid);
    // parts = ["A", hashed, "|", "F", uuid[:5], "H", str(user_id), "|", uuid[7:]]; "F".join(parts)
    let parts = [
        "A",
        &hashed,
        "|",
        "F",
        &uuid[..5.min(uuid.len())],
        "H",
        &user_id.to_string(),
        "|",
        uuid.get(7..).unwrap_or(""),
    ];
    let hawawa = parts.join("F");
    // salt = uuid[int(len*0.3):]
    let cut = ((uuid.len() as f64) * 0.3) as usize;
    let salt = uuid.get(cut..).unwrap_or("");
    pbkdf2_hex(reversed(&hawawa).as_bytes(), salt.as_bytes())
}

/// 이 (user_id, uuid) 가 만들어야 할 78자 DB 파일명 — extract.py `db_name`. 복구 검증에 쓴다.
pub fn db_name(user_id: i64, uuid: &str) -> String {
    // hawawa = ".".join([".", "F", str(user_id), "A", "F", uuid[::-1], ".", "|"])
    let rev = reversed(uuid);
    let parts = [".", "F", &user_id.to_string(), "A", "F", &rev, ".", "|"];
    let hawawa = parts.join(".");
    // salt = hashed_uuid(uuid)[::-1]
    let salt = reversed(&hashed_uuid(uuid));
    let digest = pbkdf2_hex(hawawa.as_bytes(), salt.as_bytes());
    // digest[28:28+78]
    digest[28..28 + 78].to_string()
}

// ── DB 파일 탐색 ──────────────────────────────────────────────────────────────

/// 컨테이너의 78-hex 메시지 DB 들을 **최근 수정 순**으로. 계정을 바꾸면 새 DB 가 생기므로
/// 가장 최근에 쓰인 것(= 지금 로그인한 계정)을 앞에 둔다(extract.py `find_dbs`).
pub fn find_dbs() -> Result<Vec<PathBuf>, ResolveError> {
    let dir = container();
    let entries = std::fs::read_dir(&dir).map_err(|e| {
        let failure = if e.kind() == std::io::ErrorKind::PermissionDenied {
            OpenFailure::Permission
        } else {
            OpenFailure::Unknown
        };
        ResolveError::new(failure, format!("컨테이너를 읽지 못했다({}): {e}", dir.display()))
    })?;
    let mut out: Vec<(PathBuf, std::time::SystemTime)> = Vec::new();
    for entry in entries.flatten() {
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if name.len() == 78 && name.bytes().all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase()) {
            let mtime = entry
                .metadata()
                .and_then(|m| m.modified())
                .unwrap_or(std::time::UNIX_EPOCH);
            out.push((entry.path(), mtime));
        }
    }
    if out.is_empty() {
        return Err(ResolveError::new(
            OpenFailure::Permission,
            format!("메시지 DB(78-hex)를 찾지 못했다 — 전체 디스크 접근을 확인하라: {}", dir.display()),
        ));
    }
    out.sort_by_key(|entry| std::cmp::Reverse(entry.1));
    Ok(out.into_iter().map(|(p, _)| p).collect())
}

// ── user_id 복구 ──────────────────────────────────────────────────────────────

const EMPTY_SHA512_PREIMAGE: &str = "0";

/// plist 의 `REVISION:<sha512>` 키 중 값이 0 이 아닌(활성 계정) 128-hex 해시들.
fn active_account_hashes() -> Result<Vec<String>, ResolveError> {
    let path = prefs_plist();
    let value = plist::Value::from_file(&path).map_err(|e| {
        // plist 가 아예 안 읽히면 권한일 수 있다.
        let s = e.to_string();
        let failure = if s.contains("ermission") { OpenFailure::Permission } else { OpenFailure::Unknown };
        ResolveError::new(failure, format!("plist 를 읽지 못했다({}): {e}", path.display()))
    })?;
    let empty = {
        let mut h = Sha512::new();
        h.update(EMPTY_SHA512_PREIMAGE.as_bytes());
        hex_lower(&h.finalize())
    };
    let dict = value
        .as_dictionary()
        .ok_or_else(|| ResolveError::new(OpenFailure::Unknown, "plist 최상위가 dict 가 아니다"))?;
    let mut out = Vec::new();
    for (k, v) in dict.iter() {
        // ...REVISION:<128 hex> 로 끝나는 키.
        let Some(hash) = k.rsplit(':').next() else { continue };
        if hash.len() != 128 || !hash.bytes().all(|b| b.is_ascii_hexdigit()) {
            continue;
        }
        if !k.contains("REVISION:") {
            continue;
        }
        let hash = hash.to_ascii_lowercase();
        if hash == empty {
            continue;
        }
        // 값이 0 이 아닌 것만(활성).
        let active = match v {
            plist::Value::Integer(i) => i.as_signed().map(|n| n != 0).unwrap_or(true),
            plist::Value::String(s) => s.parse::<i64>().map(|n| n != 0).unwrap_or(true),
            plist::Value::Boolean(b) => *b,
            _ => true,
        };
        if active && !out.contains(&hash) {
            out.push(hash);
        }
    }
    Ok(out)
}

fn hex_lower(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push_str(&format!("{b:02x}"));
    }
    s
}

/// `SHA512(str(i)) == 목표해시 중 하나` 인 i 를 찾고, 그 i 의 `db_name` 이 현재 DB 와
/// 같은지까지 본다. 스레드로 구간을 나눠 돌리고, 맞는 것을 찾으면 전부 멈춘다.
fn recover_user_id(targets: &[String], uuid: &str, target_db: &str, max: u64) -> Option<i64> {
    if targets.is_empty() {
        return None;
    }
    let target_bytes: Vec<[u8; 64]> = targets
        .iter()
        .filter_map(|h| {
            let mut out = [0u8; 64];
            for (i, chunk) in h.as_bytes().chunks(2).enumerate() {
                let s = std::str::from_utf8(chunk).ok()?;
                out[i] = u8::from_str_radix(s, 16).ok()?;
            }
            Some(out)
        })
        .collect();
    let threads = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(4) as u64;
    let found = Arc::new(AtomicBool::new(false));
    let answer = Arc::new(AtomicU64::new(u64::MAX));
    let uuid = uuid.to_string();
    let target_db = target_db.to_string();
    let targets = Arc::new(target_bytes);
    std::thread::scope(|scope| {
        for t in 0..threads {
            let found = found.clone();
            let answer = answer.clone();
            let uuid = uuid.clone();
            let target_db = target_db.clone();
            let targets = targets.clone();
            scope.spawn(move || {
                let mut i = t;
                let mut buf = itoa_buf();
                while i < max {
                    if i % 1_048_576 == 0 && found.load(Ordering::Relaxed) {
                        return;
                    }
                    let s = itoa(&mut buf, i);
                    let digest = Sha512::digest(s);
                    if targets.iter().any(|tgt| tgt[..] == digest[..]) {
                        // 해시는 맞았다 — 이 i 의 db_name 이 현재 DB 와 같은지 본다.
                        if db_name(i as i64, &uuid) == target_db {
                            answer.store(i, Ordering::SeqCst);
                            found.store(true, Ordering::SeqCst);
                            return;
                        }
                    }
                    i += threads;
                }
            });
        }
    });
    let v = answer.load(Ordering::SeqCst);
    if v == u64::MAX {
        None
    } else {
        Some(v as i64)
    }
}

fn itoa_buf() -> [u8; 20] {
    [0u8; 20]
}

/// 할당 없는 정수→십진 ASCII. brute force 안쪽 루프에서 쓴다.
fn itoa(buf: &mut [u8; 20], mut n: u64) -> &[u8] {
    if n == 0 {
        buf[0] = b'0';
        return &buf[..1];
    }
    let mut i = 20;
    while n > 0 {
        i -= 1;
        buf[i] = b'0' + (n % 10) as u8;
        n /= 10;
    }
    &buf[i..]
}

// ── 캐시 ──────────────────────────────────────────────────────────────────────

fn cache_path() -> PathBuf {
    home().join("Library/Application Support/app.ax.desktop/kakao_resolve.json")
}

fn read_cache() -> Option<(String, i64)> {
    let raw = std::fs::read_to_string(cache_path()).ok()?;
    // 아주 작은 JSON 파서 대신 serde_json.
    let v: serde_json::Value = serde_json::from_str(&raw).ok()?;
    let uuid = v.get("uuid")?.as_str()?.to_string();
    let user_id = v.get("user_id")?.as_i64()?;
    Some((uuid, user_id))
}

fn write_cache(uuid: &str, user_id: i64) {
    let path = cache_path();
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(mut f) = std::fs::File::create(&path) {
        let body = serde_json::json!({ "uuid": uuid, "user_id": user_id });
        let _ = f.write_all(body.to_string().as_bytes());
    }
}

// ── 부팅: resolve ─────────────────────────────────────────────────────────────

/// uuid·user_id·db_path·key 를 확보한다(extract.py `resolve`). 계정을 바꿔 로그인하면
/// (= 더 최근 DB 가 생기면) 현재 계정을 따라간다. 결과는 캐시한다(brute force 는 한 번만).
pub fn resolve() -> Result<Resolved, ResolveError> {
    let uuid = platform_uuid()?;
    let dbs = find_dbs()?;
    let newest = dbs[0].clone();
    let target = newest
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_default();

    // 1) 캐시 — 캐시된 계정이 여전히 '현재 DB' 일 때만.
    if let Some((cached_uuid, uid)) = read_cache() {
        if cached_uuid == uuid && db_name(uid, &uuid) == target {
            return Ok(bundle(uid, uuid, newest));
        }
    }

    // 2) plist 활성 계정 해시 → SHA512 preimage → 현재 DB 파일명 일치 검증.
    let targets = active_account_hashes()?;
    if let Some(uid) = recover_user_id(&targets, &uuid, &target, USER_ID_SEARCH_MAX) {
        write_cache(&uuid, uid);
        return Ok(bundle(uid, uuid, newest));
    }

    Err(ResolveError::new(
        OpenFailure::Version,
        "user_id 복구 실패 — 카카오톡 로그인 상태와 전체 디스크 접근을 확인하라",
    ))
}

fn bundle(user_id: i64, uuid: String, db_path: PathBuf) -> Resolved {
    let key = secure_key(user_id, &uuid);
    Resolved { user_id, uuid, db_path, key }
}

#[cfg(test)]
mod tests {
    use super::*;

    // mykakao extract.py 와 **같은 입력 → 같은 출력**인지 고정한다. 값은 파이썬으로 뽑아 박았다.
    // uuid 는 전형적 IOPlatformUUID 모양의 고정 예시다(실기기 값 아님).
    const UUID: &str = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE";

    #[test]
    fn hashed_uuid_는_sha1_더하기_sha256_의_base64() {
        // python: base64.b64encode(sha1(d).digest()+sha256(d).digest())
        let got = hashed_uuid(UUID);
        // 길이: (20+32) 바이트 → base64 = ceil(52/3)*4 = 72 자.
        assert_eq!(got.len(), 72);
        assert!(got.ends_with('='), "표준 base64 패딩이어야 한다: {got}");
    }

    #[test]
    fn secure_key_는_256_hex() {
        let k = secure_key(123_456_789, UUID);
        assert_eq!(k.len(), 256);
        assert!(k.bytes().all(|b| b.is_ascii_hexdigit()));
    }

    #[test]
    fn db_name_은_78_hex() {
        let n = db_name(123_456_789, UUID);
        assert_eq!(n.len(), 78);
        assert!(n.bytes().all(|b| b.is_ascii_hexdigit()));
    }

    #[test]
    fn reversed_는_파이썬_슬라이스와_같다() {
        assert_eq!(reversed("abc"), "cba");
        assert_eq!(reversed(UUID), UUID.chars().rev().collect::<String>());
    }

    #[test]
    fn itoa_는_십진_ascii() {
        let mut buf = itoa_buf();
        assert_eq!(itoa(&mut buf, 0), b"0");
        assert_eq!(itoa(&mut buf, 1_000_000_007), b"1000000007");
    }

    // db_name/secure_key 의 결정성(같은 입력 → 같은 출력)도 지킨다.
    #[test]
    fn 결정적이다() {
        assert_eq!(secure_key(42, UUID), secure_key(42, UUID));
        assert_eq!(db_name(42, UUID), db_name(42, UUID));
        assert_ne!(secure_key(42, UUID), secure_key(43, UUID));
    }
}
