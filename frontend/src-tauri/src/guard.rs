//! 절전 방지 점유의 **세션 장부**. OS 에는 한 줄도 말을 걸지 않는다 — 그것은 `power.rs` 의 일이다.
//!
//! 여기 있는 것은 SPEC-006 §4 의 수명 규칙 중 「누가 들고 있나」뿐이고, 전부 순수 자료구조라
//! 단위검증이 붙는다. **이 파일에 타이머·TTL·참조계수가 없는 것이 계약이다**(WORK I-3):
//! 세션은 **이름의 집합**이지 숫자가 아니다. 같은 이름을 두 번 넣어도 하나이고,
//! 지난 녹음의 늦은 해제가 **다른 이름**이라 지금 도는 녹음을 풀 수 없다(`E-08`·`E-09`).

use std::collections::{BTreeSet, HashMap};

/// SPEC-006 §4 Validation — `session` 은 1~128 자.
pub const MAX_SESSION_LEN: usize = 128;
/// SPEC-006 §4 Validation — `reason` 은 1~64 자, 줄바꿈 없음.
pub const MAX_REASON_LEN: usize = 64;

/// 장부가 바뀐 결과 OS 에 무엇을 해야 하는가.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OsIntent {
    /// 걸어라. **이미 걸려 있어도 다시 확인하고 필요하면 되건다**(재무장 — `E-09`).
    Engage,
    /// 풀어라. **남은 세션이 0 일 때만** 나온다.
    Disengage,
    /// OS 에 말을 걸 필요가 없다.
    Nothing,
}

#[derive(Debug, Default)]
struct WindowGuard {
    sessions: BTreeSet<String>,
}

/// 창 label → 그 창이 들고 있는 세션들.
#[derive(Debug, Default)]
pub struct Registry {
    windows: HashMap<String, WindowGuard>,
}

impl Registry {
    pub fn new() -> Self {
        Self::default()
    }

    /// 점유를 건다. **같은 세션을 다시 넣어도 늘지 않는다**(`E-09`).
    ///
    /// 반환이 언제나 `Engage` 인 것이 재무장이다 — 「이미 있으니 아무 일도 안 함」이 아니라
    /// **그 시점에 OS 가 풀려 있으면 다시 거는** 호출이다(SPEC §4 `wake_guard_acquire`).
    pub fn acquire(&mut self, window: &str, session: &str) -> OsIntent {
        self.windows
            .entry(window.to_string())
            .or_default()
            .sessions
            .insert(session.to_string());
        OsIntent::Engage
    }

    /// 점유를 푼다. **멱등이다** — 모르는 세션·이미 푼 세션도 성공이고,
    /// **다른 세션을 건드리지 않는다**(`E-08`).
    pub fn release(&mut self, window: &str, session: &str) -> OsIntent {
        if let Some(guard) = self.windows.get_mut(window) {
            guard.sessions.remove(session);
        }
        self.settle()
    }

    /// 창의 세션을 통째로 비운다 — **L-06(창이 «실제로» 닫힘) · L-09(문서가 «실제로» 교체)** 전용.
    ///
    /// 「닫기를 요청했다」·「이동이 막혔다」에서는 **부르지 않는다**(I-4·I-5 · `E-12`·`E-05`).
    /// 그 구분은 부르는 쪽(`lib.rs` 의 창 이벤트·페이지 로드 훅)이 진다.
    pub fn clear_window(&mut self, window: &str) -> OsIntent {
        if let Some(guard) = self.windows.get_mut(window) {
            guard.sessions.clear();
        }
        self.settle()
    }

    /// 이 프로세스가 세션을 하나라도 들고 있는가.
    ///
    /// **창 단위가 아니라 프로세스 단위로 본다** — macOS assertion 도 Windows 실행 상태도
    /// 프로세스(스레드)에 매인 자원 하나라서, 창 하나가 비었다고 OS 를 풀면
    /// 다른 창의 녹음이 함께 잠든다.
    pub fn any_held(&self) -> bool {
        self.windows.values().any(|g| !g.sessions.is_empty())
    }

    pub fn sessions(&self, window: &str) -> Vec<String> {
        self.windows
            .get(window)
            .map(|g| g.sessions.iter().cloned().collect())
            .unwrap_or_default()
    }

    pub fn held_count(&self) -> usize {
        self.windows.values().map(|g| g.sessions.len()).sum()
    }

    fn settle(&self) -> OsIntent {
        if self.any_held() {
            // 아직 도는 녹음이 있다. **푸는 방향으로 가지 않는다.**
            OsIntent::Nothing
        } else {
            OsIntent::Disengage
        }
    }
}

/// SPEC-006 §4 Validation 의 `session`.
pub fn validate_session(session: &str) -> Result<(), String> {
    if session.is_empty() || session.chars().count() > MAX_SESSION_LEN {
        return Err(format!("session 은 1~{MAX_SESSION_LEN} 자여야 합니다."));
    }
    Ok(())
}

/// SPEC-006 §4 Validation 의 `reason` — 줄바꿈을 받지 않는다.
/// 값 자체는 **OS 전원 관리 도구에 그대로 보이는 이름**이 되므로 길이를 넘기면 자르지 않고 거절한다.
pub fn validate_reason(reason: &str) -> Result<(), String> {
    if reason.is_empty() || reason.chars().count() > MAX_REASON_LEN {
        return Err(format!("reason 은 1~{MAX_REASON_LEN} 자여야 합니다."));
    }
    if reason.contains('\n') || reason.contains('\r') {
        return Err("reason 에 줄바꿈을 넣을 수 없습니다.".into());
    }
    Ok(())
}

/// SPEC-006 §4 `open_external` — `http`·`https` 만 받는다(`E-04`).
///
/// 파싱은 **Tauri 가 이미 쓰는 표준 `Url`**(`url` crate)에 맡긴다. 직접 조각내면
/// 기본 포트 생략 · `userinfo` · 역슬래시 정규화가 표준과 달라져 **권한 경계를 잘못 읽는다.**
/// 네비게이션 훅(`on_navigation`)이 받는 것도 같은 타입이라 비교 기준이 한 벌로 유지된다.
pub fn validate_external_url(raw: &str) -> Result<tauri::Url, String> {
    let parsed = tauri::Url::parse(raw).map_err(|reason| format!("절대 URL 이 아닙니다: {reason}"))?;
    match parsed.scheme() {
        "http" | "https" => Ok(parsed),
        other => Err(format!("허용하지 않는 스킴입니다: {other}")),
    }
}

/// 허용 목록 비교에 쓰는 origin 문자열. `url` crate 의 직렬화를 그대로 쓴다 —
/// 기본 포트(`https:443`)는 빠지고, 자격증명·경로는 애초에 origin 에 들어가지 않으며,
/// 스킴에 origin 개념이 없으면 `"null"` 이다(그 값은 어떤 허용 항목과도 같지 않다).
pub fn origin_of(url: &tauri::Url) -> String {
    url.origin().ascii_serialization()
}

#[cfg(test)]
mod tests {
    use super::*;

    const W: &str = "main";

    #[test]
    fn 같은_세션을_두_번_걸어도_한_번_풀면_풀린다() {
        // AC-T12 — 참조계수를 두지 않는다는 것의 관측 가능한 형태다.
        let mut registry = Registry::new();
        assert_eq!(registry.acquire(W, "s1"), OsIntent::Engage);
        assert_eq!(registry.acquire(W, "s1"), OsIntent::Engage);
        assert_eq!(registry.held_count(), 1);
        assert_eq!(registry.release(W, "s1"), OsIntent::Disengage);
        assert!(!registry.any_held());
    }

    #[test]
    fn 지난_녹음의_늦은_해제가_지금_도는_녹음을_풀지_못한다() {
        // AC-T13 · `E-08` — 세는 카운터 대신 이름을 쓰는 이유 그대로다.
        let mut registry = Registry::new();
        registry.acquire(W, "past");
        registry.release(W, "past");
        registry.acquire(W, "now");
        assert_eq!(registry.release(W, "past"), OsIntent::Nothing);
        assert!(registry.any_held());
        assert_eq!(registry.sessions(W), vec!["now".to_string()]);
    }

    #[test]
    fn 모르는_세션으로_풀어도_에러가_아니다() {
        // AC-T14 — 멱등. 들고 있는 것이 없으면 「풀어라」가 나오고, 그것도 성공이다.
        let mut registry = Registry::new();
        assert_eq!(registry.release(W, "never-existed"), OsIntent::Disengage);
        assert_eq!(registry.held_count(), 0);
    }

    #[test]
    fn 재요청은_언제나_거는_방향이다() {
        // I-6 · `E-09` — 재요청이 «푸는» 의도를 내면 안 된다.
        let mut registry = Registry::new();
        registry.acquire(W, "s1");
        assert_eq!(registry.acquire(W, "s1"), OsIntent::Engage);
        assert_eq!(registry.acquire(W, "s2"), OsIntent::Engage);
        assert!(registry.any_held());
    }

    #[test]
    fn 창_정리는_그_창의_세션만_비운다() {
        // L-06 · L-09 — 그리고 **다른 창이 들고 있으면 OS 를 풀지 않는다**.
        let mut registry = Registry::new();
        registry.acquire("main", "a");
        registry.acquire("other", "b");
        assert_eq!(registry.clear_window("main"), OsIntent::Nothing);
        assert!(registry.any_held());
        assert_eq!(registry.clear_window("other"), OsIntent::Disengage);
        assert!(!registry.any_held());
    }

    #[test]
    fn 여러_세션_중_하나만_풀리면_os_는_그대로다() {
        let mut registry = Registry::new();
        registry.acquire(W, "a");
        registry.acquire(W, "b");
        assert_eq!(registry.release(W, "a"), OsIntent::Nothing);
        assert_eq!(registry.release(W, "b"), OsIntent::Disengage);
    }

    #[test]
    fn 세션_길이_검증() {
        assert!(validate_session("").is_err());
        assert!(validate_session(&"x".repeat(MAX_SESSION_LEN)).is_ok());
        assert!(validate_session(&"x".repeat(MAX_SESSION_LEN + 1)).is_err());
    }

    #[test]
    fn 사유_검증은_줄바꿈을_막는다() {
        assert!(validate_reason("회의 녹음 중").is_ok());
        assert!(validate_reason("").is_err());
        assert!(validate_reason("회의\n녹음").is_err());
        assert!(validate_reason(&"가".repeat(MAX_REASON_LEN + 1)).is_err());
    }

    #[test]
    fn 외부_링크는_http_와_https_만_받는다() {
        // `E-04` — 그 밖의 스킴은 열지 않고 조용히 끝난다.
        assert!(validate_external_url("https://example.com/a?b=1").is_ok());
        assert!(validate_external_url("http://example.com").is_ok());
        assert!(validate_external_url("file:///etc/passwd").is_err());
        assert!(validate_external_url("javascript:alert(1)").is_err());
        assert!(validate_external_url("mailto:a@b.c").is_err());
        assert!(validate_external_url("/relative/path").is_err());
    }

    #[test]
    fn origin_은_표준_직렬화를_그대로_쓴다() {
        // 커맨드 허용 origin 과 네비게이션 허용 목록이 **같은 기준**으로 비교되는지 본다.
        let origin = |raw: &str| origin_of(&tauri::Url::parse(raw).unwrap());
        assert_eq!(origin("https://example.test:8443/page.html?x=1#y"), "https://example.test:8443");
        // 자격증명이 붙어도 origin 은 그대로다 — 직접 자르던 자리에서 가장 틀리기 쉬웠다.
        assert_eq!(origin("https://evil@example.test:8443/"), "https://example.test:8443");
        assert_eq!(origin("HTTPS://Example.TEST:8443/"), "https://example.test:8443");
        // 기본 포트는 직렬화에서 빠진다 — `https://example.com:443` 과 `https://example.com` 은 같다.
        assert_eq!(origin("https://example.com:443/a"), origin("https://example.com/b"));
        // origin 이 없는 스킴은 `"null"` 이고, 어떤 허용 항목과도 같지 않다.
        assert_eq!(origin("about:blank"), "null");
    }
}
