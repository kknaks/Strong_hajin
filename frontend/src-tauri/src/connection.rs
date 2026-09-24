//! **연결 실패 감지 — SPEC-006 U-2 를 세우는 자리.**
//!
//! ## 왜 이 파일이 있나 (Phase 2 의 M-7)
//!
//! Phase 2 실측이 낸 답은 분명했다 — **TLS 가 거절되면 `on_page_load` 가 «아예» 발화하지
//! 않는다.** 로그에 남은 것은 `[probe][nav] allowed=true` 하나뿐이었고 `Started` 도 `Finished` 도
//! 오지 않았으며, 사용자에게는 **빈 흰 창**만 남았다. 그래서 페이지 로드 훅만으로는
//! 「아직 안 왔다」와 「영영 안 온다」를 가를 수 없고, U-2 와 AC-T31 을 만족시킬 수 없다.
//!
//! ## 그래서 신호를 둘로 세운다
//!
//! 1. **로드 감시(watchdog)** — 네비게이션이 «허용된» 시점에 무장하고, `Finished` 가 오면 푼다.
//!    기한 안에 풀리지 않으면 「로드가 끝나지 않았다」는 **관측 가능한 사건**이 된다
//! 2. **연결 전수(preflight)** — 그 시점에 **같은 주소로 직접 붙어 본다.** 실패 사유를
//!    OS 신뢰 저장소가 내는 그대로 받아 U-2 에 싣는다. SPEC U-2 는 「실패 사유를 지어내지
//!    않는다」고 했고, 이것은 **지어내는 것이 아니라 받아 적는 것**이다
//!
//! ## ⚠ 여기의 타이머는 «점유»의 것이 아니다
//!
//! WORK 불변식 I-3 이 금지한 것은 **절전 방지 점유의 TTL·주기 갱신·참조계수**다.
//! 이 파일의 기한은 **문서 로드**에만 걸리고, **점유를 만들지도 풀지도 않는다.**
//! `guard.rs` · `power.rs` 를 한 줄도 건드리지 않는다 — 두 수명을 섞지 않는 것이 규칙이다.
//!
//! ## 신뢰 판정의 충실도
//!
//! 전수는 `native-tls` 를 쓴다 — macOS 에서는 Security.framework, Windows 에서는 SChannel 로
//! 내려간다. **웹뷰(WKWebView·WebView2)가 보는 것과 같은 OS 신뢰 저장소**다.
//! 다만 **웹뷰 «자신»이 낸 오류를 받아 온 것은 아니다** — 같은 저장소를 쓰는 별도 연결의
//! 결과이고, 보고서와 U-2 표기에서 그 사실을 흐리지 않는다.

use std::io::ErrorKind;
use std::net::{TcpStream, ToSocketAddrs};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use tauri::Url;

/// 문서 로드가 이 시간 안에 끝나지 않으면 「끝나지 않았다」로 본다.
///
/// **점유 TTL 이 아니다**(위 모듈 주석). 값은 넉넉히 잡는다 — 느린 회선에서 정상 로드를
/// 실패로 읽으면 U-2 가 거짓말을 하게 된다.
pub const LOAD_DEADLINE: Duration = Duration::from_secs(15);

/// 전수 하나에 허용하는 시간. 감시 기한보다 짧아야 U-2 가 늦지 않는다.
const PROBE_TIMEOUT: Duration = Duration::from_secs(5);

/// 전수 결과. `Reachable` 이면 연결·TLS 까지는 문제가 없었다는 뜻이다.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Reach {
    Reachable,
    /// 사람이 읽을 수 있는 실패 사유. **OS 가 준 문장을 그대로 담는다.**
    Unreachable(String),
}

/// 주소 하나에 **실제로 붙어 본다.** http 면 TCP 까지, https 면 TLS 악수까지 간다.
///
/// HTTP 요청을 보내지 않는다 — U-2 가 말하는 것은 「서버에 닿지 못했습니다」이고,
/// 그것은 연결 계층의 사실이다. 여기서 본문을 받아오면 원격 문서를 셸이 읽는 셈이 되어
/// 「셸에 업무 로직을 두지 않는다」는 규약과도 어긋난다.
pub fn probe(url: &Url) -> Reach {
    let Some(host) = url.host_str() else {
        return Reach::Unreachable("주소에 호스트가 없습니다.".into());
    };
    let Some(port) = url.port_or_known_default() else {
        return Reach::Unreachable("주소에 포트를 정할 수 없습니다.".into());
    };

    let addresses = match (host, port).to_socket_addrs() {
        Ok(found) => found.collect::<Vec<_>>(),
        Err(error) => {
            return Reach::Unreachable(format!("주소를 찾지 못했습니다: {error}"));
        }
    };
    if addresses.is_empty() {
        return Reach::Unreachable("주소를 찾지 못했습니다.".into());
    }

    // 스택이 둘일 수 있다(IPv6·IPv4). **하나라도 되면 닿은 것**이고,
    // 전부 실패하면 마지막 사유를 남긴다 — Phase 2 의 W-1 이 가르쳐 준 자리다.
    let mut last = String::new();
    for address in &addresses {
        match TcpStream::connect_timeout(address, PROBE_TIMEOUT) {
            Ok(stream) => {
                if url.scheme() != "https" {
                    return Reach::Reachable;
                }
                let _ = stream.set_read_timeout(Some(PROBE_TIMEOUT));
                let _ = stream.set_write_timeout(Some(PROBE_TIMEOUT));
                match tls_handshake(host, stream) {
                    Ok(()) => return Reach::Reachable,
                    Err(reason) => last = reason,
                }
            }
            Err(error) => {
                last = describe_io(&error);
            }
        }
    }
    Reach::Unreachable(last)
}

/// TLS 악수만 한다. 검증을 **끄지 않는다** — 끄면 U-2 가 「닿았다」고 거짓말하게 된다.
fn tls_handshake(host: &str, stream: TcpStream) -> Result<(), String> {
    let connector = native_tls::TlsConnector::new()
        .map_err(|error| format!("TLS 준비에 실패했습니다: {error}"))?;
    match connector.connect(host, stream) {
        Ok(_) => Ok(()),
        Err(error) => Err(format!("보안 연결에 실패했습니다: {error}")),
    }
}

fn describe_io(error: &std::io::Error) -> String {
    match error.kind() {
        ErrorKind::TimedOut => "연결이 시간 안에 되지 않았습니다.".into(),
        ErrorKind::ConnectionRefused => "서버가 연결을 거절했습니다.".into(),
        _ => format!("연결하지 못했습니다: {error}"),
    }
}

/// 문서 로드 감시. **창 하나에 하나**다.
///
/// 「무장 → (성공) 해제」가 한 회차이고, 회차는 세대 번호로 구분한다. 늦게 깨어난 감시가
/// 이미 지난 회차를 두고 U-2 를 띄우지 않도록, 깨어나서 **자기 세대가 아직 최신인지** 본다.
#[derive(Default)]
pub struct LoadWatch {
    generation: AtomicU64,
    finished: AtomicBool,
}

impl LoadWatch {
    pub fn new() -> Arc<Self> {
        Arc::new(Self::default())
    }

    /// 새 회차를 연다. 반환값은 이 회차의 세대 번호다.
    pub fn arm(&self) -> u64 {
        self.finished.store(false, Ordering::SeqCst);
        self.generation.fetch_add(1, Ordering::SeqCst) + 1
    }

    /// 로드가 끝났다. 이 회차의 감시는 더 이상 U-2 를 띄우지 않는다.
    pub fn finish(&self) {
        self.finished.store(true, Ordering::SeqCst);
    }

    /// 이 세대가 **아직 최신이고 아직 안 끝났는가** — 감시가 깨어나 묻는 질문.
    pub fn still_pending(&self, generation: u64) -> bool {
        self.generation.load(Ordering::SeqCst) == generation
            && !self.finished.load(Ordering::SeqCst)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 로드가_끝나면_그_회차는_더_이상_대기가_아니다() {
        let watch = LoadWatch::new();
        let generation = watch.arm();
        assert!(watch.still_pending(generation));
        watch.finish();
        assert!(!watch.still_pending(generation));
    }

    #[test]
    fn 새_회차가_열리면_지난_세대의_감시는_잠든다() {
        // 늦게 깨어난 감시가 **지금 잘 도는 문서** 위에 U-2 를 덮지 않아야 한다.
        let watch = LoadWatch::new();
        let stale = watch.arm();
        let fresh = watch.arm();
        assert!(!watch.still_pending(stale));
        assert!(watch.still_pending(fresh));
    }

    #[test]
    fn 무장은_지난_회차의_완료_표시를_지운다() {
        let watch = LoadWatch::new();
        let first = watch.arm();
        watch.finish();
        let second = watch.arm();
        assert!(!watch.still_pending(first));
        assert!(watch.still_pending(second));
    }

    #[test]
    fn 호스트가_없는_주소는_곧바로_실패로_답한다() {
        // `data:` 처럼 host 가 없는 주소가 들어와도 감시가 매달리지 않는다.
        let url = Url::parse("data:text/plain,hi").unwrap();
        assert!(matches!(probe(&url), Reach::Unreachable(_)));
    }

    #[test]
    fn 닫혀_있는_로컬_포트는_닿지_않음으로_답한다() {
        // 네트워크를 타지 않는 확정적 실패 — 사유 문장이 비지 않는지까지 본다.
        let url = Url::parse("http://127.0.0.1:1/").unwrap();
        match probe(&url) {
            Reach::Unreachable(reason) => assert!(!reason.is_empty()),
            Reach::Reachable => panic!("1번 포트에 서버가 있을 리 없다"),
        }
    }
}
