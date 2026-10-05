//! **첨부 응답을 파일로 저장한다** (SPEC-006 U-5 · `E-15` · AC-T44 ~ AC-T49).
//!
//! ## 왜 셸이 직접 받나
//! wry 0.55.1(macOS)은 응답의 `Content-Disposition` 을 보지 않는다 — `canShowMIMEType()` 가 참이면
//! (HTML·PDF·이미지) **Allow** 해서 창 안에 그린다(`wkwebview/navigation.rs` `navigation_policy_response`).
//! `on_download` 는 표시할 수 없는 형식에만 불린다. 그래서 같은 origin 의 `/api/` 이동은 셸이 **요청
//! 단계에서 취소**하고, 웹뷰의 쿠키를 실어 여기서 직접 받은 뒤 **실제 응답 헤더로** 첨부인지 가른다
//! (코디 결정 B · `fe-p3-decision.md` §4).
//!
//! ## 여기서 지키는 것
//! - 저장 위치는 OS 다운로드 폴더, **대화상자 없음**. 이름은 서버의 `filename*`(UTF-8)
//! - 같은 이름이 있으면 **덮지 않고** `이름 (1).html` 처럼 번호를 붙인다
//! - 웹 문서에는 파일 권한·경로를 주지 않는다 — 웹이 받는 것은 **저장 여부와 파일 이름**뿐이다
//! - 리다이렉트를 따라가지 않는다 — 쿠키가 다른 주소로 실려 나가지 않게 한다
//! - 저장한 파일에는 macOS 격리 표식(`com.apple.quarantine`)을 단다 — 브라우저로 받은 파일과 같은
//!   Gatekeeper 기준(fix1 W1). Windows 의 `Zone.Identifier` 는 달지 않는다(알려진 차이)

use std::collections::{HashMap, VecDeque};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::time::Duration;

use tauri::Url;

/// 셸 → 웹 사건의 이름. **`frontend/src/lib/shell.ts` 의 `SHELL_DOWNLOAD_EVENT` 와 같아야 한다**(시험이 대조한다).
pub const EVENT: &str = "strong-hajin:download";

/// 이름이 없거나 쓸 수 없을 때의 파일 이름.
const FALLBACK_NAME: &str = "download";

/// 번호를 붙여 볼 최대 횟수. 이만큼 다 차 있으면 실패로 본다(무한히 돌지 않는다).
const MAX_NUMBERED: u32 = 9_999;

/// 파일 이름의 바이트 상한. 대부분의 파일 시스템 한도(255바이트)보다 넉넉히 작게 둔다 — 번호가 붙을 자리.
const MAX_NAME_BYTES: usize = 200;

/// 서버 응답을 기다리는 시간. 서버는 파일을 메모리에서 한 번에 싣는다(`Response(content=bytes)` ·
/// 스트리밍 0 — be-survey §2-4) — 내보내기 렌더까지 포함해 1분이면 넉넉하다.
const RECV_RESPONSE_TIMEOUT: Duration = Duration::from_secs(60);

/// 본문을 다 받는 시간(**전체** 예산 — 읽을 때마다 다시 세지 않는다). 서버 업로드 상한이 25MB
/// (`material_values.py` `MAX_MATERIAL_BYTES`)라 10분이면 약 42KB/s 까지 견딘다. 그보다 느리거나
/// 서버가 멈추면 실패로 끝내고 반쯤 쓴 파일을 지운다 — 토스트가 영영 안 뜨는 일을 막는다(fix1 W7).
const RECV_BODY_TIMEOUT: Duration = Duration::from_secs(600);

/// 연결 + TLS 악수(fix1 W7 · 코디 30초).
const CONNECT_TIMEOUT: Duration = Duration::from_secs(30);

/// 셸이 받아 볼 이동인가 — **운영(앱) origin 과 같고 경로가 `/api/` 로 시작한다.**
/// 인증 흐름용 네비게이션 허용 목록 전체가 아니라 **앱 origin 하나**만 본다(fix1 W3) — 나중에 IdP 주소가
/// 허용 목록에 들어와도 그쪽 이동은 가로채지 않는다. 주소가 없는 판(`None`)은 아무것도 받지 않는다.
/// 서버의 개별 파일 경로를 셸이 알지 않는다(새 파일 엔드포인트도 자동으로 덮인다).
pub fn is_api_request(url: &Url, app_origin: Option<&str>) -> bool {
    let Some(app_origin) = app_origin else {
        return false;
    };
    matches!(url.scheme(), "http" | "https")
        && url.origin().ascii_serialization() == app_origin
        && url.path().starts_with("/api/")
}

#[derive(Debug, PartialEq, Eq)]
pub enum Disposition {
    /// 첨부다. 서버가 준 이름(정리 전)이 있으면 함께.
    Attachment(Option<String>),
    /// 첨부가 아니다(`inline` · 헤더 없음 · 모르는 형식).
    NotAttachment,
}

/// `Content-Disposition` 을 읽는다(RFC 6266). `filename*`(RFC 5987/8187)이 `filename` 보다 앞선다.
pub fn parse_disposition(header: Option<&str>) -> Disposition {
    let Some(header) = header else {
        return Disposition::NotAttachment;
    };
    let parts = split_params(header);
    let Some(kind) = parts.first() else {
        return Disposition::NotAttachment;
    };
    if !kind.trim().eq_ignore_ascii_case("attachment") {
        return Disposition::NotAttachment;
    }
    let mut plain = None;
    let mut extended = None;
    for part in &parts[1..] {
        let Some((key, value)) = part.split_once('=') else {
            continue;
        };
        let key = key.trim();
        let value = value.trim();
        if key.eq_ignore_ascii_case("filename*") {
            extended = decode_ext_value(value);
        } else if key.eq_ignore_ascii_case("filename") {
            plain = Some(unquote(value));
        }
    }
    Disposition::Attachment(extended.or(plain))
}

/// `;` 로 나눈다 — 따옴표 안의 `;` 는 나누지 않는다.
fn split_params(header: &str) -> Vec<String> {
    let mut parts = Vec::new();
    let mut current = String::new();
    let mut quoted = false;
    let mut escaped = false;
    for ch in header.chars() {
        if quoted {
            current.push(ch);
            if escaped {
                escaped = false;
            } else if ch == '\\' {
                escaped = true;
            } else if ch == '"' {
                quoted = false;
            }
            continue;
        }
        match ch {
            '"' => {
                quoted = true;
                current.push(ch);
            }
            ';' => parts.push(std::mem::take(&mut current)),
            _ => current.push(ch),
        }
    }
    parts.push(current);
    parts
}

fn unquote(value: &str) -> String {
    let Some(inner) = value.strip_prefix('"').and_then(|rest| rest.strip_suffix('"')) else {
        return value.to_string();
    };
    let mut out = String::with_capacity(inner.len());
    let mut chars = inner.chars();
    while let Some(ch) = chars.next() {
        if ch == '\\' {
            if let Some(next) = chars.next() {
                out.push(next);
            }
        } else {
            out.push(ch);
        }
    }
    out
}

/// `UTF-8''%ED%9A%8C…` → 문자열. 문자셋은 UTF-8 · ISO-8859-1 만 받는다(그 밖은 무시하고 `filename` 으로 간다).
fn decode_ext_value(value: &str) -> Option<String> {
    let mut pieces = value.splitn(3, '\'');
    let charset = pieces.next()?;
    let _language = pieces.next()?;
    let encoded = pieces.next()?;
    let bytes = percent_decode(encoded)?;
    if charset.eq_ignore_ascii_case("utf-8") {
        String::from_utf8(bytes).ok()
    } else if charset.eq_ignore_ascii_case("iso-8859-1") {
        Some(bytes.into_iter().map(char::from).collect())
    } else {
        None
    }
}

fn percent_decode(encoded: &str) -> Option<Vec<u8>> {
    let raw = encoded.as_bytes();
    let mut out = Vec::with_capacity(raw.len());
    let mut index = 0;
    while index < raw.len() {
        if raw[index] == b'%' {
            let hex = encoded.get(index + 1..index + 3)?;
            out.push(u8::from_str_radix(hex, 16).ok()?);
            index += 3;
        } else {
            out.push(raw[index]);
            index += 1;
        }
    }
    Some(out)
}

/// 표시 방향을 뒤집는 문자 — 「계약서\u{202E}fdp.exe」처럼 **확장자가 뒤집혀 보이는** 이름을 막는다(fix1 W5).
/// LRM·RLM · 내장(LRE·RLE·PDF·LRO·RLO) · 격리(LRI·RLI·FSI·PDI).
fn is_bidi_control(ch: char) -> bool {
    matches!(ch, '\u{200E}' | '\u{200F}' | '\u{202A}'..='\u{202E}' | '\u{2066}'..='\u{2069}')
}

/// Windows 예약 장치 이름(대소문자 무시) — `CON.html`·`nul.tar.gz` 처럼 **첫 `.` 앞**이 이것이면 열 수 없다.
fn is_windows_reserved(name: &str) -> bool {
    let stem = name.split('.').next().unwrap_or_default().trim_end().to_ascii_uppercase();
    match stem.as_str() {
        "CON" | "PRN" | "AUX" | "NUL" => true,
        _ => {
            let (head, digit) = stem.split_at(stem.len().min(3));
            matches!(head, "COM" | "LPT") && digit.len() == 1 && matches!(digit.as_bytes()[0], b'1'..=b'9')
        }
    }
}

/// 서버가 준 이름을 **이 기기에 쓸 수 있는 한 조각**으로 만든다.
/// 경로 구분자·제어 문자·양방향 제어 문자·Windows 예약 문자를 지우고, 앞의 `.`(숨김 파일)·끝의 공백과 `.` 을
/// 떼어 낸다. Windows 예약 장치 이름이면 앞에 `_` 를 붙인다. 남는 것이 없으면 `None`.
pub fn safe_file_name(raw: &str) -> Option<String> {
    // 경로가 섞여 와도 마지막 조각만 쓴다 — 다운로드 폴더 밖으로 나가지 않는다.
    let last = raw.rsplit(['/', '\\']).next().unwrap_or_default();
    let cleaned: String = last
        .chars()
        .map(|ch| match ch {
            '<' | '>' | ':' | '"' | '|' | '?' | '*' => '_',
            ch if ch.is_control() || is_bidi_control(ch) => '_',
            ch => ch,
        })
        .collect();
    let trimmed = cleaned.trim().trim_start_matches('.').trim_end_matches(['.', ' ']).trim();
    if trimmed.is_empty() {
        return None;
    }
    let clamped = clamp_bytes(trimmed);
    if is_windows_reserved(&clamped) {
        return Some(format!("_{clamped}"));
    }
    Some(clamped)
}

/// 이름을 바이트 상한 안으로 줄인다 — 확장자는 남긴다.
fn clamp_bytes(name: &str) -> String {
    if name.len() <= MAX_NAME_BYTES {
        return name.to_string();
    }
    let (mut stem, mut ext) = split_extension(name);
    // 확장자 자체가 터무니없이 길면 확장자로 보지 않는다 — `.` 으로 시작하는 덩어리(숨김 파일)가 남지 않게(fix1 W5 ③).
    if ext.len() > MAX_NAME_BYTES / 2 {
        stem = name;
        ext = "";
    }
    let budget = MAX_NAME_BYTES.saturating_sub(ext.len());
    let mut cut = budget.min(stem.len());
    while !stem.is_char_boundary(cut) {
        cut -= 1;
    }
    format!("{}{ext}", &stem[..cut])
}

/// `회의록.html` → (`회의록`, `.html`). 확장자가 없으면 (`이름`, ``).
fn split_extension(name: &str) -> (&str, &str) {
    match name.rfind('.') {
        Some(dot) if dot > 0 => (&name[..dot], &name[dot..]),
        _ => (name, ""),
    }
}

/// `n` 번째 다른 이름 — `이름 (n).확장자`. `n == 0` 이면 원래 이름.
pub fn numbered(name: &str, n: u32) -> String {
    if n == 0 {
        return name.to_string();
    }
    let (stem, ext) = split_extension(name);
    format!("{stem} ({n}){ext}")
}

/// 아직 없는 이름으로 파일을 **만들어 잡는다**(`create_new` — 고르는 순간과 쓰는 순간 사이에 남이 끼어들 수 없다).
fn reserve(dir: &Path, name: &str) -> io::Result<(PathBuf, File)> {
    for n in 0..=MAX_NUMBERED {
        let path = dir.join(numbered(name, n));
        match OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(file) => return Ok((path, file)),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error),
        }
    }
    Err(io::Error::new(io::ErrorKind::AlreadyExists, "같은 이름의 파일이 너무 많다"))
}

/// 본문을 다운로드 폴더에 쓴다. 반환은 **실제로 쓴 파일 이름**(번호 포함).
/// 도중에 실패하면 반쯤 쓴 파일을 지운다 — 깨진 파일이 그 이름으로 남지 않게 한다.
pub fn save(dir: &Path, name: &str, body: &mut dyn Read) -> io::Result<String> {
    fs::create_dir_all(dir)?;
    let (path, mut file) = reserve(dir, name)?;
    let written = io::copy(body, &mut file).and_then(|_| file.flush()).and_then(|_| file.sync_all());
    if let Err(error) = written {
        drop(file);
        let _ = fs::remove_file(&path);
        return Err(error);
    }
    drop(file);
    mark_quarantined(&path);
    Ok(path
        .file_name()
        .map(|name| name.to_string_lossy().to_string())
        .unwrap_or_default())
}

/// 다운로드한 파일에 macOS 격리 표식을 단다(fix1 W1) — Gatekeeper 가 첫 실행·열기를 검사하게 한다.
/// 브라우저가 다는 것과 같은 속성(`com.apple.quarantine`)·형식(`플래그;시각(16진);에이전트;`).
/// **실패해도 저장은 성공이다** — 로그만 남긴다(코디 2026-10-04). macOS 밖에서는 아무것도 하지 않는다.
#[cfg(target_os = "macos")]
pub fn mark_quarantined(path: &Path) {
    if let Err(error) = quarantine::set(path) {
        println!("[shell][download] 격리 표식을 달지 못했다(저장은 그대로): {error}");
    }
}

/// Windows 의 `Zone.Identifier`(MOTW)는 이번에 달지 않는다 — 알려진 차이(fix1 W1).
#[cfg(not(target_os = "macos"))]
pub fn mark_quarantined(_path: &Path) {}

#[cfg(target_os = "macos")]
mod quarantine {
    use std::ffi::CString;
    use std::io;
    use std::os::raw::{c_char, c_int, c_void};
    use std::os::unix::ffi::OsStrExt;
    use std::path::Path;

    pub const NAME: &str = "com.apple.quarantine";

    extern "C" {
        // <sys/xattr.h> — libSystem. 새 의존성 없이 부른다.
        fn setxattr(
            path: *const c_char,
            name: *const c_char,
            value: *const c_void,
            size: usize,
            position: u32,
            options: c_int,
        ) -> c_int;
    }

    /// `0081` = 다운로드한 파일(kLSQuarantineTypeWebDownload 계열 · 사용자 승인 전) — Chrome 이 다는 값과 같다.
    pub fn value(now_secs: u64, agent: &str) -> String {
        format!("0081;{now_secs:08x};{agent};")
    }

    pub fn set(path: &Path) -> io::Result<()> {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let agent = std::env::current_exe()
            .ok()
            .and_then(|exe| exe.file_stem().map(|stem| stem.to_string_lossy().to_string()))
            .unwrap_or_else(|| "desktop-shell".to_string());
        let value = value(now, &agent);
        let c_path = CString::new(path.as_os_str().as_bytes())?;
        let c_name = CString::new(NAME)?;
        // SAFETY: 세 포인터 모두 이 호출 동안 살아 있는 NUL 종료 문자열/바이트열이다.
        let result = unsafe {
            setxattr(c_path.as_ptr(), c_name.as_ptr(), value.as_ptr().cast(), value.len(), 0, 0)
        };
        if result == 0 {
            Ok(())
        } else {
            Err(io::Error::last_os_error())
        }
    }
}

/// 받아 본 결과.
#[derive(Debug, PartialEq, Eq)]
pub enum Fetched {
    /// 첨부였고 저장했다 — 쓴 파일 이름.
    Saved(String),
    /// 2xx 인데 첨부가 아니었다(`inline` · 헤더 없음). 셸은 **아무것도 하지 않는다**(결정 X).
    NotAttachment(u16),
    /// 2xx 가 아니었다(401 세션 만료 · 3xx · 404 · 5xx). **실패로 알린다**(fix1 W2).
    Refused(u16),
}

/// 상태와 헤더만으로 정한다 — 본문을 받기 전의 판정. 저장할 이름이면 `Ok(이름)`.
pub fn judge(status: u16, disposition: Option<&str>) -> Result<String, Fetched> {
    if !(200..300).contains(&status) {
        return Err(Fetched::Refused(status));
    }
    let Disposition::Attachment(raw_name) = parse_disposition(disposition) else {
        return Err(Fetched::NotAttachment(status));
    };
    Ok(raw_name
        .as_deref()
        .and_then(safe_file_name)
        .unwrap_or_else(|| FALLBACK_NAME.to_string()))
}

/// 웹뷰의 쿠키를 실어 GET 하고, 첨부면 저장한다.
///
/// TLS 는 **native-tls + 플랫폼 신뢰 저장소**다 — 웹뷰와 같은 기준으로 인증서를 본다(`connection.rs` 와 같은 결).
/// 프록시는 환경변수만 본다(웹뷰의 시스템 프록시 설정과 다를 수 있다 — 알려진 차이).
pub fn fetch(url: &Url, cookie_header: &str, dir: &Path) -> Result<Fetched, String> {
    use ureq::tls::{RootCerts, TlsConfig, TlsProvider};

    let agent: ureq::Agent = ureq::Agent::config_builder()
        .tls_config(
            TlsConfig::builder()
                .provider(TlsProvider::NativeTls)
                .root_certs(RootCerts::PlatformVerifier)
                .build(),
        )
        // 리다이렉트를 따라가지 않는다 — 쿠키가 다른 주소로 실려 나가지 않게.
        .max_redirects(0)
        .max_redirects_will_error(false)
        // 4xx·5xx 도 응답으로 받아 직접 가른다.
        .http_status_as_error(false)
        .timeout_connect(Some(CONNECT_TIMEOUT))
        .timeout_recv_response(Some(RECV_RESPONSE_TIMEOUT))
        .timeout_recv_body(Some(RECV_BODY_TIMEOUT))
        .build()
        .into();

    let mut request = agent.get(url.as_str());
    if !cookie_header.is_empty() {
        request = request.header("Cookie", cookie_header);
    }
    let mut response = request.call().map_err(|error| error.to_string())?;
    let status = response.status().as_u16();
    let header = response
        .headers()
        .get("content-disposition")
        .and_then(|value| value.to_str().ok());
    let name = match judge(status, header) {
        Ok(name) => name,
        Err(verdict) => return Ok(verdict),
    };
    let mut body = response.body_mut().as_reader();
    save(dir, &name, &mut body)
        .map(Fetched::Saved)
        .map_err(|error| error.to_string())
}

/// 웹뷰가 직접 내려받는 중인 것(`on_download`) — 주소 → 고른 경로들(**먼저 시작한 것부터**).
/// macOS 는 끝났을 때 경로를 주지 않아(tauri `DownloadEvent::Finished`) 시작 때 적어 둔다.
/// 같은 주소를 연달아 받아도 앞의 것이 덮이지 않는다(fix1 W6) — 같은 주소끼리는 시작 순서로 짝짓는다.
#[derive(Default)]
pub struct PendingDownloads {
    by_url: HashMap<String, VecDeque<PathBuf>>,
}

impl PendingDownloads {
    pub fn start(&mut self, url: &str, path: PathBuf) {
        self.by_url.entry(url.to_string()).or_default().push_back(path);
    }

    pub fn finish(&mut self, url: &str) -> Option<PathBuf> {
        let queue = self.by_url.get_mut(url)?;
        let path = queue.pop_front();
        if queue.is_empty() {
            self.by_url.remove(url);
        }
        path
    }

    /// 아직 고르지 않은 이름까지 피한다 — 같은 이름을 동시에 받는 둘이 같은 자리를 고르지 않게.
    pub fn is_reserved(&self, path: &Path) -> bool {
        self.by_url.values().any(|queue| queue.iter().any(|item| item == path))
    }
}

/// 아직 없고 **진행 중인 다른 내려받기도 고르지 않은** 이름 — 웹뷰가 직접 쓸 자리(`on_download`)용.
/// 파일을 만들지 않는다(WKDownload 는 이미 있는 자리에 쓰지 못한다).
pub fn free_path_avoiding(dir: &Path, name: &str, pending: &PendingDownloads) -> Option<PathBuf> {
    (0..=MAX_NUMBERED)
        .map(|n| dir.join(numbered(name, n)))
        .find(|path| !path.exists() && !pending.is_reserved(path))
}

/// `cookies_for_url` 의 결과를 `Cookie:` 헤더 값으로. 값은 저장·기록하지 않는다.
pub fn cookie_header<'a>(pairs: impl IntoIterator<Item = (&'a str, &'a str)>) -> String {
    pairs
        .into_iter()
        .map(|(name, value)| format!("{name}={value}"))
        .collect::<Vec<_>>()
        .join("; ")
}

/// 웹에 보낼 사건 스크립트. **문구는 없다** — 저장 여부와 파일 이름만 싣는다(U-1 · §2.5).
pub fn event_script(ok: bool, filename: Option<&str>) -> String {
    let detail = serde_json::json!({ "ok": ok, "filename": filename });
    let name = serde_json::Value::String(EVENT.to_string());
    format!("window.dispatchEvent(new CustomEvent({name}, {{ detail: {detail} }}));")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "strong-hajin-shell-{tag}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn 서버의_한글_filename_별표를_읽는다() {
        // 서버 `http.py` 내보내기 헤더 모양 그대로 — `filename=` 대체값이 없다.
        let header = "attachment; filename*=UTF-8''%ED%9A%8C%EC%9D%98%EB%A1%9D%20%EC%A3%BC%EA%B0%84.html";
        assert_eq!(
            parse_disposition(Some(header)),
            Disposition::Attachment(Some("회의록 주간.html".into()))
        );
    }

    #[test]
    fn filename_별표가_filename_보다_앞선다() {
        let header = r#"Attachment; filename="fallback.txt"; filename*=utf-8''%EC%9E%90%EB%A3%8C.txt"#;
        assert_eq!(parse_disposition(Some(header)), Disposition::Attachment(Some("자료.txt".into())));
        let header = r#"attachment; filename="a; b \"c\".pdf""#;
        assert_eq!(parse_disposition(Some(header)), Disposition::Attachment(Some(r#"a; b "c".pdf"#.into())));
    }

    #[test]
    fn 첨부가_아니면_저장하지_않는다() {
        assert_eq!(parse_disposition(None), Disposition::NotAttachment);
        assert_eq!(
            parse_disposition(Some("inline; filename*=UTF-8''a.pdf")),
            Disposition::NotAttachment
        );
        assert_eq!(parse_disposition(Some("")), Disposition::NotAttachment);
        assert_eq!(parse_disposition(Some("attachment")), Disposition::Attachment(None));
    }

    #[test]
    fn 깨진_filename_별표는_filename_으로_물러난다() {
        let header = r#"attachment; filename*=UTF-8''%E0%A4; filename="plain.txt""#;
        assert_eq!(parse_disposition(Some(header)), Disposition::Attachment(Some("plain.txt".into())));
        let header = "attachment; filename*=UTF-8''%zz";
        assert_eq!(parse_disposition(Some(header)), Disposition::Attachment(None));
    }

    #[test]
    fn 파일_이름은_다운로드_폴더_밖으로_나가지_않는다() {
        assert_eq!(safe_file_name("../../etc/passwd").as_deref(), Some("passwd"));
        assert_eq!(safe_file_name(r"..\..\a.txt").as_deref(), Some("a.txt"));
        assert_eq!(safe_file_name("..").as_deref(), None);
        assert_eq!(safe_file_name("  ").as_deref(), None);
        assert_eq!(safe_file_name(".hidden").as_deref(), Some("hidden"));
        assert_eq!(safe_file_name("a:b?c*.txt. ").as_deref(), Some("a_b_c_.txt"));
        assert_eq!(safe_file_name("줄\n바꿈.html").as_deref(), Some("줄_바꿈.html"));
        assert_eq!(safe_file_name("회의록 주간.html").as_deref(), Some("회의록 주간.html"));
    }

    #[test]
    fn 긴_이름은_확장자를_남기고_글자_경계에서_자른다() {
        let long = format!("{}.html", "가".repeat(200));
        let clamped = safe_file_name(&long).unwrap();
        assert!(clamped.len() <= MAX_NAME_BYTES);
        assert!(clamped.ends_with(".html"));
        assert!(clamped.starts_with("가가"));
    }

    #[test]
    fn 번호는_확장자_앞에_붙는다() {
        assert_eq!(numbered("회의록.html", 0), "회의록.html");
        assert_eq!(numbered("회의록.html", 1), "회의록 (1).html");
        assert_eq!(numbered("archive.tar.gz", 2), "archive.tar (2).gz");
        assert_eq!(numbered("README", 3), "README (3)");
    }

    #[test]
    fn 같은_이름이_있으면_덮지_않고_번호를_붙인다() {
        let dir = temp_dir("numbering");
        let first = save(&dir, "회의록.html", &mut "one".as_bytes()).unwrap();
        let second = save(&dir, "회의록.html", &mut "two".as_bytes()).unwrap();
        let third = save(&dir, "회의록.html", &mut "three".as_bytes()).unwrap();
        assert_eq!(first, "회의록.html");
        assert_eq!(second, "회의록 (1).html");
        assert_eq!(third, "회의록 (2).html");
        assert_eq!(fs::read_to_string(dir.join("회의록.html")).unwrap(), "one");
        assert_eq!(fs::read_to_string(dir.join("회의록 (1).html")).unwrap(), "two");
        assert_eq!(
            free_path_avoiding(&dir, "회의록.html", &PendingDownloads::default()).unwrap(),
            dir.join("회의록 (3).html")
        );
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn 쓰다_실패하면_반쯤_쓴_파일을_남기지_않는다() {
        struct Broken;
        impl Read for Broken {
            fn read(&mut self, _: &mut [u8]) -> io::Result<usize> {
                Err(io::Error::other("끊김"))
            }
        }
        let dir = temp_dir("broken");
        assert!(save(&dir, "a.html", &mut Broken).is_err());
        assert!(!dir.join("a.html").exists());
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn 셸이_받아_보는_것은_같은_origin_의_api_뿐이다() {
        let yes = |raw: &str| is_api_request(&Url::parse(raw).unwrap(), Some("https://ax.medisolveai.xyz"));
        assert!(yes("https://ax.medisolveai.xyz/api/meetings/m1/export?format=html"));
        assert!(yes("https://ax.medisolveai.xyz/api/tasks/t1/materials/x/content"));
        assert!(yes("https://ax.medisolveai.xyz/api/work-requests/r/attachments/a/content"));
        // 화면 이동 · 다른 origin · `/api` 로 시작만 비슷한 경로는 건드리지 않는다.
        assert!(!yes("https://ax.medisolveai.xyz/meetings/m1"));
        assert!(!yes("https://ax.medisolveai.xyz/apis/x"));
        assert!(!yes("https://ax.medisolveai.xyz/api"));
        assert!(!yes("https://evil.example/api/meetings/m1/export"));
        // fix1 W3 — 인증 흐름용으로 허용 목록에 들어온 IdP 주소라도 앱 origin 이 아니면 가로채지 않는다.
        assert!(!yes("https://idp.example/api/auth/callback"));
        assert!(!yes("http://ax.medisolveai.xyz/api/meetings/m1/export"));
        assert!(!yes("stronghajin://shell/api/x"));
        // 주소가 없는 판(개인판 · origin null)은 아무것도 받지 않는다.
        assert!(!is_api_request(&Url::parse("https://ax.medisolveai.xyz/api/x").unwrap(), None));
    }

    #[test]
    fn 오류_응답은_실패이고_2xx_inline_만_아무것도_안_한다() {
        // fix1 W2 — 세션 만료·리다이렉트·없음·서버 오류는 「아무 일 없음」이 아니라 실패로 알린다.
        for status in [301, 302, 401, 403, 404, 500, 503] {
            assert_eq!(judge(status, Some("attachment; filename=a.html")), Err(Fetched::Refused(status)));
        }
        assert_eq!(judge(200, Some("inline; filename=a.pdf")), Err(Fetched::NotAttachment(200)));
        assert_eq!(judge(200, None), Err(Fetched::NotAttachment(200)));
        assert_eq!(judge(200, Some("attachment; filename*=UTF-8''%EA%B0%80.html")), Ok("가.html".into()));
        assert_eq!(judge(204, Some("attachment")), Ok("download".into()));
    }

    #[test]
    fn 양방향_제어_문자로_확장자를_뒤집지_못한다() {
        // fix1 W5 ② — 「계약서{U+202E}fdp.exe」 는 Finder·토스트에서 「계약서exe.pdf」 처럼 보인다.
        assert_eq!(safe_file_name("계약서\u{202E}fdp.exe").as_deref(), Some("계약서_fdp.exe"));
        for ch in ['\u{200E}', '\u{200F}', '\u{202A}', '\u{202B}', '\u{202C}', '\u{202D}', '\u{2066}', '\u{2067}', '\u{2068}', '\u{2069}'] {
            let cleaned = safe_file_name(&format!("a{ch}b.txt")).unwrap();
            assert_eq!(cleaned, "a_b.txt", "U+{:04X} 가 남았다", ch as u32);
        }
    }

    #[test]
    fn windows_예약_장치_이름은_피한다() {
        // fix1 W5 ① — 첫 `.` 앞이 예약 이름이면(대소문자 무시) 앞에 `_` 를 붙인다.
        assert_eq!(safe_file_name("CON.html").as_deref(), Some("_CON.html"));
        assert_eq!(safe_file_name("nul.tar.gz").as_deref(), Some("_nul.tar.gz"));
        assert_eq!(safe_file_name("Com1.txt").as_deref(), Some("_Com1.txt"));
        assert_eq!(safe_file_name("LPT9").as_deref(), Some("_LPT9"));
        assert_eq!(safe_file_name("aux").as_deref(), Some("_aux"));
        // 비슷하지만 예약이 아닌 것은 그대로.
        for name in ["CONSOLE.html", "COM0.txt", "COM10.txt", "LPT.txt", "회의 CON.html", "prn1.txt"] {
            assert_eq!(safe_file_name(name).as_deref(), Some(name), "{name}");
        }
    }

    #[test]
    fn 터무니없이_긴_확장자는_숨김_파일이_되지_않는다() {
        // fix1 W5 ③
        let name = format!("a.{}", "x".repeat(300));
        let clamped = safe_file_name(&name).unwrap();
        assert!(clamped.len() <= MAX_NAME_BYTES);
        assert!(clamped.starts_with("a."), "{clamped}");
    }

    #[test]
    fn 같은_주소를_연달아_내려받아도_앞의_것이_덮이지_않는다() {
        // fix1 W6 — 진행 중 맵이 주소 하나에 이름 하나였을 때는 둘째가 첫째를 덮었다.
        let dir = temp_dir("pending");
        let mut pending = PendingDownloads::default();
        let first = free_path_avoiding(&dir, "보고서.docx", &pending).unwrap();
        pending.start("https://a/x", first.clone());
        let second = free_path_avoiding(&dir, "보고서.docx", &pending).unwrap();
        pending.start("https://a/x", second.clone());
        assert_eq!(first, dir.join("보고서.docx"));
        assert_eq!(second, dir.join("보고서 (1).docx"), "진행 중인 이름을 다시 골랐다");
        assert_eq!(pending.finish("https://a/x"), Some(first));
        assert_eq!(pending.finish("https://a/x"), Some(second));
        assert_eq!(pending.finish("https://a/x"), None);
        assert!(!pending.is_reserved(&dir.join("보고서.docx")));
        fs::remove_dir_all(&dir).unwrap();
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn 저장한_파일에는_macos_격리_표식이_붙는다() {
        // fix1 W1 — 브라우저로 받은 파일과 같은 Gatekeeper 기준. `xattr -p com.apple.quarantine` 와 같은 값을 읽는다.
        extern "C" {
            fn getxattr(
                path: *const std::os::raw::c_char,
                name: *const std::os::raw::c_char,
                value: *mut std::os::raw::c_void,
                size: usize,
                position: u32,
                options: std::os::raw::c_int,
            ) -> isize;
        }
        use std::os::unix::ffi::OsStrExt;
        let dir = temp_dir("quarantine");
        let name = save(&dir, "실행.command", &mut "echo hi".as_bytes()).unwrap();
        let path = std::ffi::CString::new(dir.join(&name).as_os_str().as_bytes()).unwrap();
        let attr = std::ffi::CString::new(quarantine::NAME).unwrap();
        let mut buffer = vec![0u8; 256];
        // SAFETY: 버퍼 길이를 그대로 넘긴다.
        let read = unsafe { getxattr(path.as_ptr(), attr.as_ptr(), buffer.as_mut_ptr().cast(), buffer.len(), 0, 0) };
        assert!(read > 0, "격리 표식이 없다");
        let value = String::from_utf8_lossy(&buffer[..read as usize]).to_string();
        assert!(value.starts_with("0081;"), "{value}");
        assert_eq!(quarantine::value(0x1234, "medi-ax"), "0081;00001234;medi-ax;");
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn 쿠키_헤더를_만든다() {
        assert_eq!(cookie_header([("scax_session", "abc"), ("b", "2")]), "scax_session=abc; b=2");
        assert_eq!(cookie_header(std::iter::empty()), "");
    }

    #[test]
    fn 웹에_보내는_사건에는_문구도_경로도_없다() {
        let script = event_script(true, Some("회의 \"록\"</script>.html"));
        assert!(script.starts_with("window.dispatchEvent(new CustomEvent(\"strong-hajin:download\""));
        assert!(script.contains(r#""ok":true"#));
        assert!(script.contains(r#"회의 \"록\"</script>.html"#), "{script}");
        assert_eq!(
            event_script(false, None),
            r#"window.dispatchEvent(new CustomEvent("strong-hajin:download", { detail: {"filename":null,"ok":false} }));"#
        );
    }

    #[test]
    fn 사건_이름이_웹_수신부와_같다() {
        let web = include_str!("../../src/lib/shell.ts");
        assert!(
            web.contains(&format!("SHELL_DOWNLOAD_EVENT = \"{EVENT}\"")),
            "shell.ts 의 SHELL_DOWNLOAD_EVENT 가 셸의 사건 이름과 다르다"
        );
    }
}
