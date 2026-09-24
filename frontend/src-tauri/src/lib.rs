//! **Strong Hajin 데스크톱 셸 (제품).**
//!
//! 배포된 HTTPS 웹을 여는 창 하나와, 그 창이 웹에게 주는 **네이티브 커맨드 넷**이 전부다.
//! 보장하는 것은 하나 — **회의를 녹음하는 동안 이 기기가 자동으로 잠들지 않는다.**
//! 화면·업무 로직·데이터는 웹의 것이고 셸은 갖지 않는다.
//!
//! ## 커맨드 넷 — 여기 없는 것은 열지 않는다 (SPEC-006 §4 · AC-T23)
//! `shell_info` · `wake_guard_acquire` · `wake_guard_release` · `open_external`
//! 파일 읽기·쓰기, 프로세스 실행, 범용 셸, 임의 경로 열기는 **하나도 없다.**
//!
//! ## 이 파일이 지는 계약 불변식 (WORK §계약 불변식)
//! - **I-1** 점유의 수명을 네이티브가 소유한다 — 웹이 조용해도 **셸은 풀지 않는다**
//! - **I-2** 커맨드는 **넷**이다
//! - **I-3** 점유에 TTL·주기 갱신·참조계수가 **없다**. (`connection.rs` 의 로드 기한은
//!   **문서 로드**의 것이고 점유를 만들지도 풀지도 않는다 — 두 수명을 섞지 않는다)
//! - **I-4·I-5** 해제는 **«완료» 사건에만** 붙는다 — `CloseRequested`(요청)와 막힌 네비게이션에서
//!   아무것도 풀지 않고, `Destroyed`·«실제» 문서 교체에서만 푼다
//! - **I-6** 재요청은 «거는» 방향이다 (macOS 재무장은 `power.rs` 가 실제로 다시 건다)
//! - **I-7** 늦게 도착한 획득의 최종 잔존은 0 — 장부가 이름 집합이라 중복이 쌓이지 않는다
//!
//! ## Phase 1 탐침에서 **가져오지 않은 것**
//! 탐침 전용 환경 손잡이(`SCAX` 접두 변수) · 계측 페이지 · fixture 주소 · loopback 거울.
//! 제품 셸에는 **주소를 바꾸는 뒷문이 없다** — 여는 곳은 `shell.config.json` 하나가 정하고,
//! 실행 중에 그것을 덮어쓰는 환경변수를 읽지 않는다. (이 사실은 아래 시험이 지킨다)

mod config;
mod connection;
mod guard;
mod power;
mod shell_ui;

use std::sync::{Arc, Mutex};

use config::Target;
use connection::{LoadWatch, Reach};
use guard::{OsIntent, Registry};
use power::{PowerOutcome, PowerThread};
use serde::Serialize;
#[cfg(windows)]
use tauri::Manager;
use tauri::{Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent};

/// 이 인터페이스의 판 번호(SPEC-006 §4). **정수 하나만 늘린다.**
const SHELL_API: u32 = 1;

/// 셸이 자기 화면을 내보내는 스킴. **원격 문서와 섞이지 않는 자기 자리**다.
/// 이 스킴의 문서는 커맨드를 부르지 않고, capability 도 허락하지 않는다(`local: false`).
const SHELL_SCHEME: &str = "stronghajin";
const CONNECTION_ERROR_URL: &str = "stronghajin://shell/connection-error";
const NOT_CONFIGURED_URL: &str = "stronghajin://shell/not-configured";

const MAIN_WINDOW: &str = "main";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
enum WakeState {
    Off,
    On,
    Degraded,
}

#[derive(Debug, Serialize)]
struct WakeReply {
    state: WakeState,
}

#[derive(Debug, Serialize)]
struct ShellInfo {
    shell_api: u32,
    app_version: String,
    platform: &'static str,
    features: Vec<&'static str>,
}

/// 셸의 상태 전부. 점유 장부 · OS 스레드 손잡이 · 로드 감시 · 마지막 연결 실패 사유.
/// **업무 데이터는 없다.**
struct Shell {
    registry: Mutex<Registry>,
    power: PowerThread,
    watch: Arc<LoadWatch>,
    target: Mutex<Option<Url>>,
    last_failure: Mutex<Option<String>>,
}

type SharedShell = Arc<Shell>;

impl Shell {
    fn new() -> Self {
        Self {
            registry: Mutex::new(Registry::new()),
            power: PowerThread::spawn(),
            watch: LoadWatch::new(),
            target: Mutex::new(None),
            last_failure: Mutex::new(None),
        }
    }

    /// 장부를 고치고 그 결과대로 OS 를 만진다.
    ///
    /// **장부 잠금을 OS 호출 동안 들고 있는다** — 「한쪽이 걸고 다른 쪽이 푸는」 교차를 막는다.
    /// OS 스레드는 이쪽을 되부르지 않으므로 교착이 없다(`power.rs`).
    fn apply(&self, intent: OsIntent, reason: &str, registry: &mut Registry) -> WakeState {
        match intent {
            OsIntent::Engage => match self.power.engage(reason) {
                PowerOutcome::Engaged => WakeState::On,
                // `E-02`(구현 없음) · `E-03`(호출 실패) 둘 다 웹에게는 `degraded` 다 —
                // **녹음은 계속되고 U-3 한 줄이 뜬다.** 점유는 선다(장부에 이름이 남는다).
                _ => WakeState::Degraded,
            },
            OsIntent::Disengage => {
                self.power.disengage();
                WakeState::Off
            }
            OsIntent::Nothing => {
                // 아직 도는 녹음이 있다 — 그때의 실제 상태를 돌려준다(SPEC §4 `wake_guard_release`).
                if registry.any_held() {
                    match self.power.engage(reason) {
                        PowerOutcome::Engaged => WakeState::On,
                        _ => WakeState::Degraded,
                    }
                } else {
                    WakeState::Off
                }
            }
        }
    }

    fn acquire(&self, window: &str, session: &str, reason: &str) -> Result<WakeReply, String> {
        guard::validate_session(session)?;
        guard::validate_reason(reason)?;
        let mut registry = self
            .registry
            .lock()
            .map_err(|_| "점유 장부가 깨졌습니다.".to_string())?;
        let intent = registry.acquire(window, session);
        let state = self.apply(intent, reason, &mut registry);
        log_event(
            "wake",
            &format!("acquire window={window} state={state:?} held={}", registry.held_count()),
        );
        Ok(WakeReply { state })
    }

    fn release(&self, window: &str, session: &str) -> Result<WakeReply, String> {
        guard::validate_session(session)?;
        let mut registry = self
            .registry
            .lock()
            .map_err(|_| "점유 장부가 깨졌습니다.".to_string())?;
        let intent = registry.release(window, session);
        let state = self.apply(intent, "회의 녹음 중", &mut registry);
        log_event(
            "wake",
            &format!("release window={window} state={state:?} held={}", registry.held_count()),
        );
        Ok(WakeReply { state })
    }

    /// **L-06 · L-09 전용** — 창이 «실제로» 닫혔거나 문서가 «실제로» 바뀐 뒤에만 부른다.
    /// 닫기 «요청»·막힌 네비게이션에서는 부르지 않는다(I-4 · I-5).
    fn clear_window(&self, window: &str, cause: &str) {
        let Ok(mut registry) = self.registry.lock() else {
            log_event("wake", &format!("cleanup window={window} cause={cause} 장부 잠금 실패"));
            return;
        };
        let dropped = registry.sessions(window);
        let intent = registry.clear_window(window);
        let state = self.apply(intent, "회의 녹음 중", &mut registry);
        log_event(
            "wake",
            &format!("cleanup window={window} cause={cause} dropped={dropped:?} state={state:?}"),
        );
    }
}

/// 셸의 관측 기록. **원격 문서에 이벤트를 쏘지 않는다** — 그것이 다섯째 표면이 되면
/// I-2(커맨드 넷)가 흐려진다.
fn log_event(tag: &str, message: &str) {
    let at = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    println!("[shell][{tag}] t={at} {message}");
}

#[tauri::command]
async fn shell_info() -> Result<ShellInfo, String> {
    let platform = if cfg!(target_os = "macos") {
        "macos"
    } else if cfg!(windows) {
        "windows"
    } else {
        "unsupported"
    };
    Ok(ShellInfo {
        shell_api: SHELL_API,
        app_version: env!("CARGO_PKG_VERSION").to_string(),
        platform,
        // 웹은 숫자 비교가 아니라 이 목록으로 기능을 판단한다(SPEC-006 §4).
        features: vec!["wake_guard", "open_external"],
    })
}

#[tauri::command]
async fn wake_guard_acquire(
    window: tauri::Window,
    shell: tauri::State<'_, SharedShell>,
    session: String,
    reason: String,
) -> Result<WakeReply, String> {
    let shell = shell.inner().clone();
    let label = window.label().to_string();
    tauri::async_runtime::spawn_blocking(move || shell.acquire(&label, &session, &reason))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn wake_guard_release(
    window: tauri::Window,
    shell: tauri::State<'_, SharedShell>,
    session: String,
) -> Result<WakeReply, String> {
    let shell = shell.inner().clone();
    let label = window.label().to_string();
    tauri::async_runtime::spawn_blocking(move || shell.release(&label, &session))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn open_external(url: String) -> Result<(), String> {
    // `E-04` — `http(s)` 가 아니면 열지 않는다. 앱 창도 두 번째 웹뷰도 만들지 않는다.
    let parsed = guard::validate_external_url(&url)?;
    tauri_plugin_opener::open_url(parsed.as_str(), None::<&str>).map_err(|error| error.to_string())
}

/// Windows(WebView2) 마이크 권한. 없으면 `getUserMedia` 가 **프롬프트 없이** 실패한다.
/// macOS 는 `Info.plist` 의 `NSMicrophoneUsageDescription` 이 프롬프트를 띄운다.
#[cfg(windows)]
fn allow_microphone_on_webview2(webview: &tauri::Webview) -> tauri::Result<()> {
    use webview2_com::Microsoft::Web::WebView2::Win32::{
        COREWEBVIEW2_PERMISSION_KIND, COREWEBVIEW2_PERMISSION_KIND_MICROPHONE,
        COREWEBVIEW2_PERMISSION_STATE_ALLOW,
    };
    use webview2_com::PermissionRequestedEventHandler;

    webview.with_webview(|platform| {
        let result = unsafe {
            let core = platform.controller().CoreWebView2();
            core.and_then(|core| {
                let mut token: i64 = 0;
                core.add_PermissionRequested(
                    &PermissionRequestedEventHandler::create(Box::new(|_, args| {
                        let Some(args) = args else { return Ok(()) };
                        let mut kind = COREWEBVIEW2_PERMISSION_KIND::default();
                        args.PermissionKind(&mut kind)?;
                        if kind == COREWEBVIEW2_PERMISSION_KIND_MICROPHONE {
                            args.SetState(COREWEBVIEW2_PERMISSION_STATE_ALLOW)?;
                        }
                        Ok(())
                    })),
                    &mut token,
                )
            })
        };
        if let Err(error) = result {
            log_event(
                "mic",
                &format!("WebView2 PermissionRequested 등록 실패 — 마이크가 열리지 않는다: {error}"),
            );
        }
    })
}

/// 로드 감시를 무장하고, 기한이 지나도 안 끝났으면 **U-2 로 넘긴다**.
///
/// Phase 2 의 M-7 이 이 함수의 존재 이유다 — TLS 거절에서는 `on_page_load` 가 **하나도**
/// 발화하지 않아, 페이지 훅만으로는 실패를 영영 알 수 없다.
///
/// ⚠ 여기 기한은 **문서 로드**의 것이다. 점유(`guard.rs`·`power.rs`)를 만들지도 풀지도 않는다.
fn watch_load(shell: SharedShell, window: WebviewWindow, target: Url) {
    let generation = shell.watch.arm();
    let spawned = std::thread::Builder::new()
        .name("shell-load-watch".into())
        .spawn(move || {
            std::thread::sleep(connection::LOAD_DEADLINE);
            if !shell.watch.still_pending(generation) {
                return; // 로드가 끝났거나 다른 회차가 열렸다.
            }
            // 기한 안에 끝나지 않았다. **왜인지 직접 붙어 본다** — 사유를 지어내지 않기 위해서다.
            let detail = match connection::probe(&target) {
                Reach::Reachable => {
                    // 연결·TLS 는 되는데 문서가 안 떴다. 원인을 모르므로 **비워 둔다**(SPEC U-2).
                    log_event("load", "기한 초과 — 연결은 되지만 문서가 오지 않았다");
                    None
                }
                Reach::Unreachable(reason) => {
                    log_event("load", &format!("기한 초과 — 닿지 못했다: {reason}"));
                    Some(reason)
                }
            };
            if let Ok(mut slot) = shell.last_failure.lock() {
                *slot = detail;
            }
            if let Ok(url) = Url::parse(CONNECTION_ERROR_URL) {
                if let Err(error) = window.navigate(url) {
                    log_event("load", &format!("U-2 로 넘기지 못했다: {error}"));
                }
            }
        });
    if let Err(error) = spawned {
        log_event("load", &format!("로드 감시를 띄우지 못했다: {error}"));
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let shell: SharedShell = Arc::new(Shell::new());
    let target = config::load().unwrap_or_else(|error| {
        // 설정이 깨졌으면 **가짜 주소로 도는 대신** 설정되지 않은 것으로 본다.
        log_event("boot", &format!("설정을 읽지 못했다: {error}"));
        Target::Missing
    });

    let (start_url, navigation_allowlist) = match &target {
        Target::Configured { url, navigation_allowlist } => {
            if let Ok(mut slot) = shell.target.lock() {
                *slot = Some(url.clone());
            }
            (url.clone(), navigation_allowlist.clone())
        }
        Target::Missing => (Url::parse(NOT_CONFIGURED_URL).expect("내장 주소"), Vec::new()),
    };
    log_event("boot", &format!("url={start_url} nav_allow={navigation_allowlist:?}"));

    let protocol_shell = shell.clone();
    let watch_target = target.clone();
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(shell.clone())
        // 셸 자기 화면(U-2 · 미설정)을 내보내는 자리. **원격 문서와 섞이지 않는다.**
        .register_uri_scheme_protocol(SHELL_SCHEME, move |_ctx, request| {
            let path = request.uri().path().to_string();
            let body = if path.ends_with("not-configured") {
                shell_ui::not_configured(config::CONFIG_SLOT)
            } else {
                let target = protocol_shell
                    .target
                    .lock()
                    .ok()
                    .and_then(|slot| slot.clone())
                    .map(|url| url.to_string())
                    .unwrap_or_default();
                let detail = protocol_shell
                    .last_failure
                    .lock()
                    .ok()
                    .and_then(|slot| slot.clone());
                shell_ui::connection_error(&target, detail.as_deref())
            };
            tauri::http::Response::builder()
                .status(200)
                .header("Content-Type", "text/html; charset=utf-8")
                .body(body.into_bytes())
                .expect("셸 화면 응답")
        })
        .setup(move |app| {
            let nav_allow = navigation_allowlist.clone();
            let nav_shell = shell.clone();
            let load_shell = shell.clone();

            let window =
                WebviewWindowBuilder::new(app, MAIN_WINDOW, WebviewUrl::External(start_url.clone()))
                    .title("Strong Hajin")
                    .inner_size(1280.0, 860.0)
                    // 원격 웹의 HTML5 drag/drop 을 WKWebView 가 직접 처리해야 한다.
                    // Tauri 의 네이티브 파일 드롭 핸들러를 켜 두면 웹의 drag/drop 이벤트를 소비한다.
                    .disable_drag_drop_handler()
                    // **쿠키가 남아야 재시작 후에도 로그인이 유지된다**(AC-T26·AC-T35).
                    // incognito 를 켜면 실행할 때마다 로그아웃된다 — 켜지 않는다.
                    .incognito(false)
                    .on_navigation(move |url| {
                        // 셸 자기 화면은 언제나 허용한다 — 커맨드 권한과는 무관하다.
                        if url.scheme() == SHELL_SCHEME {
                            return true;
                        }
                        let origin = guard::origin_of(url);
                        let allowed = nav_allow.iter().any(|entry| entry == &origin);
                        if !allowed {
                            // `E-05` — 취소하고 기본 브라우저로 넘긴다.
                            // **점유는 그대로다**(L-11 · I-4): 막힌 이동은 문서 교체가 아니다.
                            if matches!(url.scheme(), "http" | "https") {
                                if let Err(error) =
                                    tauri_plugin_opener::open_url(url.as_str(), None::<&str>)
                                {
                                    log_event("nav", &format!("외부 열기 실패: {error}"));
                                }
                            }
                            log_event("nav", &format!("blocked origin={origin} — 점유는 유지한다(L-11)"));
                            return false;
                        }
                        log_event("nav", &format!("allowed origin={origin}"));
                        true
                    })
                    .on_page_load(move |window, payload| {
                        let label = window.label().to_string();
                        match payload.event() {
                            tauri::webview::PageLoadEvent::Started => {
                                // **L-09 — 문서가 «실제로» 바뀌었다.** 그 창의 세션을 전부 푼다.
                                // 막힌 이동(`E-05`)·같은 문서 안 주소 변경(L-11)은 여기까지 오지 않는다.
                                load_shell.clear_window(&label, "document-replaced(L-09)");
                            }
                            tauri::webview::PageLoadEvent::Finished => {
                                // 문서가 떴다 — 로드 감시를 푼다(U-2 로 넘어가지 않는다).
                                load_shell.watch.finish();
                                log_event("load", &format!("finished url={}", payload.url()));
                            }
                        }
                    })
                    .build()?;

            #[cfg(windows)]
            {
                for webview in app.webviews().values() {
                    allow_microphone_on_webview2(webview)?;
                }
            }

            // 원격 주소를 열 때만 감시한다. 셸 자기 화면은 감시할 이유가 없다.
            if matches!(watch_target, Target::Configured { .. }) {
                watch_load(nav_shell.clone(), window.clone(), start_url.clone());
            }

            let event_shell = shell.clone();
            window.on_window_event(move |event| match event {
                WindowEvent::CloseRequested { .. } => {
                    // **요청은 «완료»가 아니다**(I-4·I-5 · `E-12`). 여기서 점유를 풀면,
                    // 사용자가 「계속 녹음」을 고른 바로 그 순간 기기가 잠들 수 있게 된다.
                    log_event("win", "close-requested — 아무것도 풀지 않는다");
                }
                WindowEvent::Destroyed => {
                    // **L-06 — 창이 «실제로» 닫혔다.** 웹이 해제를 못 불렀어도 여기서 풀린다.
                    event_shell.clear_window(MAIN_WINDOW, "window-destroyed(L-06)");
                }
                _ => {}
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            shell_info,
            wake_guard_acquire,
            wake_guard_release,
            open_external
        ])
        .run(tauri::generate_context!())
        .expect("셸을 띄우지 못했습니다");
}

#[cfg(test)]
mod tests {
    use super::*;
    use power::PowerBackend;

    #[derive(Default)]
    struct FlakyBackend {
        fail: bool,
    }

    impl PowerBackend for FlakyBackend {
        fn engage(&mut self, _reason: &str) -> PowerOutcome {
            if self.fail {
                PowerOutcome::Failed("테스트용 실패".into())
            } else {
                PowerOutcome::Engaged
            }
        }
        fn disengage(&mut self) -> PowerOutcome {
            PowerOutcome::Released
        }
    }

    fn shell_with(fail: bool) -> Shell {
        Shell {
            registry: Mutex::new(Registry::new()),
            power: PowerThread::spawn_with(FlakyBackend { fail }),
            watch: LoadWatch::new(),
            target: Mutex::new(None),
            last_failure: Mutex::new(None),
        }
    }

    #[test]
    fn 획득과_해제가_상태를_그대로_돌려준다() {
        let shell = shell_with(false);
        assert_eq!(shell.acquire(MAIN_WINDOW, "s1", "회의 녹음 중").unwrap().state, WakeState::On);
        assert_eq!(shell.release(MAIN_WINDOW, "s1").unwrap().state, WakeState::Off);
    }

    #[test]
    fn os_가_실패해도_점유는_서고_degraded_로_답한다() {
        // `E-02`·`E-03` — 녹음을 막지 않는다. 점유가 장부에 남아야 창 닫기 정리가 의미를 갖는다.
        let shell = shell_with(true);
        assert_eq!(
            shell.acquire(MAIN_WINDOW, "s1", "회의 녹음 중").unwrap().state,
            WakeState::Degraded
        );
        assert_eq!(shell.registry.lock().unwrap().held_count(), 1);
    }

    #[test]
    fn 지난_녹음의_늦은_해제가_지금_도는_점유를_끄지_않는다() {
        // AC-T13 — 반환 `state` 까지 `on` 이어야 한다.
        let shell = shell_with(false);
        shell.acquire(MAIN_WINDOW, "past", "회의 녹음 중").unwrap();
        shell.release(MAIN_WINDOW, "past").unwrap();
        shell.acquire(MAIN_WINDOW, "now", "회의 녹음 중").unwrap();
        assert_eq!(shell.release(MAIN_WINDOW, "past").unwrap().state, WakeState::On);
        assert_eq!(shell.registry.lock().unwrap().held_count(), 1);
    }

    #[test]
    fn 창이_실제로_닫히면_웹이_못_풀어도_풀린다() {
        // L-06 · AC-T15
        let shell = shell_with(false);
        shell.acquire(MAIN_WINDOW, "s1", "회의 녹음 중").unwrap();
        shell.acquire(MAIN_WINDOW, "s2", "회의 녹음 중").unwrap();
        shell.clear_window(MAIN_WINDOW, "window-destroyed(L-06)");
        assert_eq!(shell.registry.lock().unwrap().held_count(), 0);
    }

    #[test]
    fn 잘못된_인자는_점유를_만들지_않는다() {
        let shell = shell_with(false);
        assert!(shell.acquire(MAIN_WINDOW, "", "회의 녹음 중").is_err());
        assert!(shell.acquire(MAIN_WINDOW, "s1", "").is_err());
        assert!(shell.acquire(MAIN_WINDOW, "s1", "회의\n녹음").is_err());
        assert_eq!(shell.registry.lock().unwrap().held_count(), 0);
    }

    #[test]
    fn 늦은_획득과_해제가_섞여도_최종_잔존이_0_이다() {
        // I-7 · AC-T40 의 네이티브 쪽 — 장부가 이름 집합이라 중복이 쌓이지 않는다.
        let shell = Arc::new(shell_with(false));
        let workers: Vec<_> = (0..16)
            .map(|index| {
                let shell = shell.clone();
                std::thread::spawn(move || {
                    let session = format!("s{index}");
                    shell.acquire(MAIN_WINDOW, &session, "회의 녹음 중").unwrap();
                    shell.release(MAIN_WINDOW, &session).unwrap();
                })
            })
            .collect();
        for worker in workers {
            worker.join().unwrap();
        }
        assert_eq!(shell.registry.lock().unwrap().held_count(), 0);
    }

    // ── 설정·권한 경계의 «정적» 검증 ─────────────────────────────────────────────
    // 운영 origin 이 없어도(OQ-T02) 여기까지는 지금 확인할 수 있다. AC-T23·AC-T24·AC-T25 의
    // 증거 자리이고, 「설정 파일을 사람이 읽어 확인할 수 있어야 한다」를 기계로도 고정한다.

    const CAPABILITY: &str = include_str!("../capabilities/product-shell.json");
    const TAURI_CONF: &str = include_str!("../tauri.conf.json");
    const CARGO_TOML: &str = include_str!("../Cargo.toml");

    fn capability() -> serde_json::Value {
        serde_json::from_str(CAPABILITY).expect("capability 가 JSON 이어야 한다")
    }

    #[test]
    fn 웹에_여는_커맨드는_정확히_넷이다() {
        // AC-T23 — 파일·프로세스·범용 셸·open_path 권한이 **하나도** 없다.
        let permissions = capability()["permissions"]
            .as_array()
            .expect("permissions 배열")
            .iter()
            .map(|value| value.as_str().unwrap().to_string())
            .collect::<Vec<_>>();
        assert_eq!(permissions.len(), 4, "커맨드 넷을 넘었다: {permissions:?}");
        for expected in [
            "allow-shell-info",
            "allow-wake-guard-acquire",
            "allow-wake-guard-release",
            "allow-open-external",
        ] {
            assert!(permissions.contains(&expected.to_string()), "{expected} 가 없다");
        }
        for forbidden in ["fs:", "shell:", "process:", "opener:", "open-path", "reveal-item"] {
            assert!(
                !permissions.iter().any(|item| item.contains(forbidden)),
                "{forbidden} 표면이 열렸다: {permissions:?}"
            );
        }
        // 커맨드 목록과 `invoke_handler` 가 어긋나면 ACL 이 통과해도 호출이 깨진다.
        assert!(CARGO_TOML.contains("tauri-plugin-opener"));
    }

    #[test]
    fn 커맨드_허용_origin_은_하나이고_와일드카드_서브도메인이_아니다() {
        // AC-T24 · SPEC §5 — 와일드카드 서브도메인은 그 전부를 신뢰한다는 뜻이라 쓰지 않는다.
        let capability = capability();
        let urls = capability["remote"]["urls"].as_array().expect("remote.urls");
        assert_eq!(urls.len(), 1, "운영 origin 은 정확히 하나다: {urls:?}");
        let url = urls[0].as_str().unwrap();
        assert!(!url.contains("://*."), "와일드카드 서브도메인을 쓰지 않는다: {url}");

        // **local:false** — 셸 자기 화면(`stronghajin://`)도 커맨드를 부르지 못한다(리뷰 W-6).
        assert_eq!(capability["local"], serde_json::Value::Bool(false));
    }

    #[test]
    fn 운영_origin_은_아직_자리로만_있다() {
        // OQ-T02 — fixture 주소를 제품 운영값으로 승격하지 않았다는 것을 코드로 고정한다.
        // `.invalid` 는 예약 TLD(RFC 2606)라 실재할 수 없다. Phase 8 이 값을 채운다.
        let capability = capability();
        let url = capability["remote"]["urls"][0].as_str().unwrap();
        assert!(url.contains(".invalid"), "운영 origin 을 발명했다: {url}");
        assert!(!url.contains("localhost"), "fixture 주소가 승격됐다: {url}");
        assert_eq!(config::load().unwrap(), config::Target::Missing);
    }

    #[test]
    fn 셸_설정이_제품_정체성을_지킨다() {
        // AC-T34 — 참조 제품과 겹치지 않는다. 식별자는 판을 넘어 고정한다.
        assert!(TAURI_CONF.contains(r#""identifier": "app.stronghajin.desktop""#));
        assert!(!TAURI_CONF.contains("com.kknaks.task-management"));
        // 원격 문서의 CSP 는 **서버 응답 헤더가 정한다** — 셸이 박지 않는다(SPEC §5).
        assert!(!TAURI_CONF.contains("\"csp\""));
        // AC-T33 — 트레이·추가 창을 만들지 않는다.
        assert!(!TAURI_CONF.contains("trayIcon"));
        assert!(!s_contains_second_window());
    }

    #[test]
    fn 제품_창은_웹_html5_drag_drop_을_네이티브_핸들러에_빼앗기지_않는다() {
        // 캘린더는 웹 표준 drag/drop 을 쓴다. Tauri 의 네이티브 파일 드롭 핸들러가 켜지면
        // macOS WKWebView 에서 dragover/drop 이 웹까지 오지 않으므로 창 빌더에서 반드시 끈다.
        let source = include_str!("lib.rs");
        let builder_start = source
            .find("WebviewWindowBuilder::new(app, MAIN_WINDOW")
            .expect("제품 창 빌더가 있어야 한다");
        let builder = &source[builder_start..];
        let builder_end = builder
            .find(".build()?")
            .expect("제품 창을 build 해야 한다");
        let builder = &builder[..builder_end];
        let disable_native_drop = concat!(".disable_drag_drop", "_handler()");

        assert!(
            builder.contains(disable_native_drop),
            "제품 창에서 Tauri 네이티브 파일 드롭 핸들러를 끄지 않으면 웹 HTML5 drag/drop 이 막힌다"
        );
    }

    fn s_contains_second_window() -> bool {
        // 창은 Rust 가 하나만 만든다(`MAIN_WINDOW`). 설정에 창 목록이 비어 있어야 한다.
        serde_json::from_str::<serde_json::Value>(TAURI_CONF)
            .ok()
            .and_then(|value| value["app"]["windows"].as_array().map(|list| !list.is_empty()))
            .unwrap_or(true)
    }

    #[test]
    fn 셸_프레임워크_하한이_2_11_1_이상이다() {
        // AC-T25 — GHSA-7gmj-67g7-phm9(원격 문서가 로컬 전용 커맨드를 부르는 origin 혼동).
        assert!(CARGO_TOML.contains(r#"tauri = { version = "2.11.1""#));
        let lock = include_str!("../Cargo.lock");
        let resolved = lock
            .split("[[package]]")
            .find(|block| block.contains("name = \"tauri\"\n"))
            .expect("Cargo.lock 에 tauri 가 있어야 한다");
        let version = resolved
            .lines()
            .find_map(|line| line.trim().strip_prefix("version = "))
            .expect("tauri version")
            .trim_matches('"');
        let parts = version
            .split('.')
            .filter_map(|part| part.parse::<u32>().ok())
            .collect::<Vec<_>>();
        assert!(parts.len() >= 3, "판 번호를 읽지 못했다: {version}");
        assert!(
            (parts[0], parts[1], parts[2]) >= (2, 11, 1),
            "tauri 가 2.11.1 미만이다: {version}"
        );
    }

    #[test]
    fn 제품_셸에_탐침_손잡이가_없다() {
        // 발주 요구 — 탐침 전용 환경 손잡이 · 계측 페이지 · fixture 주소 · 거울을 옮기지 않았다.
        let sources = [
            include_str!("lib.rs"),
            include_str!("config.rs"),
            include_str!("connection.rs"),
            include_str!("power.rs"),
            include_str!("guard.rs"),
            include_str!("shell_ui.rs"),
        ];
        // 바늘을 «이어 붙여» 만든다 — 이 파일 자신도 검사 대상이라, 바늘을 그대로 적으면
        // 시험이 자기 소스에 걸려 언제나 실패한다.
        let env_handle = concat!("SCAX", "_PROBE");
        let probe_page = concat!("probe", ".html");
        let fixture_mirror = concat!("loopback", "Mirror");
        let fixture_origin = concat!("localhost:", "5180");
        for source in sources {
            assert!(!source.contains(env_handle), "탐침 환경 손잡이가 남았다");
            assert!(!source.contains(probe_page), "계측 페이지 참조가 남았다");
            assert!(!source.contains(fixture_mirror), "fixture 거울이 남았다");
            assert!(!source.contains(fixture_origin), "fixture origin 이 남았다");
        }
        assert!(!CAPABILITY.contains(fixture_origin), "fixture origin 이 남았다");
        assert!(!TAURI_CONF.contains(fixture_origin), "fixture origin 이 남았다");
    }

    #[test]
    fn 셸_전용_스킴은_원격_origin_과_다르다() {
        // 셸 화면이 원격 문서와 같은 origin 에 앉으면 `local:false` 경계가 무의미해진다.
        let error_url = Url::parse(CONNECTION_ERROR_URL).unwrap();
        assert_eq!(error_url.scheme(), SHELL_SCHEME);
        assert_ne!(guard::origin_of(&error_url), "https://example.test");
    }
}
