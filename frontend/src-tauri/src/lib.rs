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
//! ## 첨부 응답 저장 — 커맨드가 아니다 (SPEC-006 U-5 · `E-15`)
//! 같은 origin 의 `/api/` 이동·새 창 링크는 셸이 **스스로** 가로채 받아 보고, 첨부면 OS 다운로드
//! 폴더에 저장한다(`download.rs`). 결과는 **셸 → 웹 사건**(`strong-hajin:download`)으로 알리고
//! 문구는 웹이 만든다. 웹 문서에 파일 권한·경로를 주지 않으므로 커맨드는 그대로 넷이다.
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
mod download;
mod guard;
#[cfg(feature = "kakao-collector")]
mod kakao;
mod notify;
mod power;
mod shell_ui;

use std::sync::{Arc, Mutex};

use config::Target;
use connection::{LoadWatch, Reach};
use guard::{OsIntent, Registry};
use power::{PowerOutcome, PowerThread};
use serde::Serialize;
use tauri::webview::{DownloadEvent, NewWindowResponse};
use tauri::{AppHandle, Manager, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent};

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
    /// 웹뷰가 직접 내려받는 중인 것(`on_download`) — `download::PendingDownloads` 설명 참고.
    downloads: Mutex<download::PendingDownloads>,
    /// OS 알림 클릭 중계 — 웹이 들을 준비가 될 때까지 하나만 보관한다(`notify::ClickRelay` · 검수 W-1).
    clicks: Mutex<notify::ClickRelay>,
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
            downloads: Mutex::new(download::PendingDownloads::default()),
            clicks: Mutex::new(notify::ClickRelay::default()),
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

/// 셸의 관측 기록. 원격 문서로 가는 사건은 **첨부 저장 결과 하나뿐**이다(`notify_download`) —
/// 그것도 웹이 부르는 커맨드가 아니라 I-2(커맨드 넷)는 그대로다.
fn log_event(tag: &str, message: &str) {
    let at = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    println!("[shell][{tag}] t={at} {message}");
}

/// 메인 창이 닫혀 있을 때 창을 **새로** 만드는 길(medi-ax 트레이 「열기」 와 같은 것). strong-hajin 판에는 없다 —
/// 그 판은 마지막 창이 닫히면 프로세스가 끝난다. 알림 클릭(검수 W-1 ①)이 트레이와 같은 길로 창을 연다.
type Reopen = Arc<dyn Fn(&AppHandle) + Send + Sync>;
static REOPEN_MAIN: std::sync::OnceLock<Reopen> = std::sync::OnceLock::new();

/// 웹에 셸 기능을 알려 준다. **메인 창의 앱 문서가 이것을 부르면 「웹이 준비됐다」** 로 본다 — 보관한 OS 알림 클릭이 있으면
/// 그때 한 번 보낸다(검수 W-1 · `notify::ClickRelay`). 커맨드 수는 그대로다(새 커맨드를 열지 않는다).
#[tauri::command]
async fn shell_info(window: tauri::Window, shell: tauri::State<'_, SharedShell>) -> Result<ShellInfo, String> {
    if window.label() == MAIN_WINDOW {
        let ready_click = shell
            .clicks
            .lock()
            .ok()
            .and_then(|mut relay| relay.on_ready(std::time::Instant::now()));
        if let Some(click) = ready_click {
            let origin = shell.target.lock().ok().and_then(|slot| slot.as_ref().map(guard::origin_of));
            send_click(window.app_handle(), origin.as_deref(), click);
        }
    }
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
        // WORK-013 WP4: `notification` — 웹은 이 값이 있을 때만 알림 커맨드를 부른다(옛 dmg = 없음 → 목록만).
        features: vec!["wake_guard", "open_external", "notification"],
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

/// OS 알림 권한을 읽고/묻는다(SPEC-011 §4.6). 묻는 때는 웹이 정한다(OQ-1102 — 로그인 뒤 첫 화면 한 번).
/// 프롬프트는 사람을 기다리므로 별도 스레드에서 기다린다.
#[tauri::command(rename_all = "snake_case")]
async fn notify_permission(app: AppHandle, request: bool) -> Result<notify::PermissionReply, String> {
    let state = tauri::async_runtime::spawn_blocking(move || notify::permission(&app, request))
        .await
        .map_err(|error| error.to_string())?;
    log_event("notify", &format!("permission request={request} state={state:?}"));
    Ok(notify::PermissionReply { state })
}

/// 웹이 넘긴 알림 하나를 띄운다 — 셸은 거르지도 글자를 만들지도 않는다(D-09). 권한이 없으면 `{shown:false}`(오류 아님).
#[tauri::command(rename_all = "snake_case")]
async fn notify_show(
    app: AppHandle,
    notification_id: Option<String>,
    title: String,
    body: String,
    target: serde_json::Value,
) -> Result<notify::ShowReply, String> {
    let request = notify::ShowRequest { notification_id, title, body, target };
    notify::validate(&request)?;
    let shown = tauri::async_runtime::spawn_blocking(move || notify::show(&app, &request))
        .await
        .map_err(|error| error.to_string())?;
    Ok(notify::ShowReply { shown })
}

/// OS 알림을 눌렀다 — **창을 앞으로**(보이기 · 최소화 풀기 · 포커스) 가져온 뒤 웹에 사건을 보낸다(SPEC-011 §4.6 · AC-T51).
///
/// - 창이 있으면 앞으로 · 웹이 준비됐으면 바로 보내고, 아니면(막 뜬 앱 · 새로 고침 중) **보관했다가** 웹이 `shell_info` 를
///   부를 때 한 번 보낸다(검수 W-1 — strong-hajin 이 알림 클릭으로 새로 뜬 경우도 이 길)
/// - **창이 없으면**(medi-ax 트레이 상주) 트레이 「열기」 와 **같은 길**로 창을 새로 만들고 클릭을 보관한다(검수 W-1 ①).
///   창이 없을 때만 만드므로 둘이 동시에 서지 않는다(AC-T33). 그 길이 없는 판(strong-hajin)은 버린다
fn notify_click(app: &AppHandle, app_origin: Option<&str>, notification_id: Option<String>, target: serde_json::Value) {
    let click = notify::Click { notification_id, target };
    let shell = app.state::<SharedShell>();
    let Some(window) = app.get_webview_window(MAIN_WINDOW) else {
        let Some(reopen) = REOPEN_MAIN.get() else {
            log_event("notify", "창이 없고 다시 여는 길이 없는 판이라 알림 클릭을 버린다");
            return;
        };
        if let Ok(mut relay) = shell.clicks.lock() {
            relay.on_document_gone();
            relay.on_click(click, std::time::Instant::now());
        }
        log_event("notify", "창이 없어 트레이 「열기」 와 같은 길로 창을 만든다 — 웹이 준비되면 클릭을 보낸다");
        reopen(app);
        return;
    };
    let _ = window.show();
    let _ = window.unminimize();
    let _ = window.set_focus();
    let now_click = shell
        .clicks
        .lock()
        .ok()
        .and_then(|mut relay| relay.on_click(click, std::time::Instant::now()));
    match now_click {
        Some(click) => send_click(app, app_origin, click),
        None => log_event("notify", "웹이 아직 준비되지 않아 알림 클릭을 보관한다"),
    }
}

/// 클릭 사건을 웹에 쏜다 — 지금 창에 **앱 origin** 문서가 떠 있을 때만(다운로드 사건과 같은 규칙).
fn send_click(app: &AppHandle, app_origin: Option<&str>, click: notify::Click) {
    let Some(window) = app.get_webview_window(MAIN_WINDOW) else {
        return;
    };
    let on_app = match (window.url(), app_origin) {
        (Ok(current), Some(origin)) => guard::origin_of(&current) == origin,
        _ => false,
    };
    if !on_app {
        log_event("notify", "웹 앱 문서가 아니라 알림 클릭을 알리지 않는다");
        return;
    }
    if let Err(error) = window.eval(notify::click_script(click.notification_id.as_deref(), &click.target)) {
        log_event("notify", &format!("알림 클릭을 웹에 알리지 못했다: {error}"));
    }
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

/// 앱 origin 의 `/api/` 이동·새 창 링크를 **셸이 받아 본다**(U-5 · `E-15`).
///
/// 부르는 쪽은 이미 이동을 취소(또는 새 창을 거절)했다 — 그래서 **창의 문서는 바뀌지 않고**
/// `on_page_load Started`(L-09 점유 정리)도 돌지 않는다. 여기서는:
/// - 첨부면 다운로드 폴더에 저장하고 결과를 웹에 알린다
/// - 2xx 가 아니면(401 세션 만료 · 3xx · 404 · 5xx) **실패로 알린다**(fix1 W2)
/// - 2xx 인데 첨부가 아니면(`inline`) **아무것도 하지 않는다** — 다시 navigate 하지 않는다.
///   macOS 는 `_blank` 도 `on_navigation` 을 먼저 지나 같은 탭과 가를 수 없고, 다시 열면 inline `_blank`
///   (채팅 근거·AX 링크)가 앱 창을 덮는다(OQ-T13 불변 · 코디 결정 X — `fe-p3-decision.md` §5)
///
/// `cookies_for_url` 은 메인 스레드 콜백을 기다리므로 **별도 스레드**에서 부른다(Windows 교착 경고도 같은 이유).
fn take_over_api_link(app: AppHandle, app_origin: Option<String>, url: Url, via: &'static str) {
    let spawned = std::thread::Builder::new()
        .name("shell-download".into())
        .spawn(move || {
            let Some(window) = app.get_webview_window(MAIN_WINDOW) else {
                return;
            };
            let path = url.path().to_string();
            let cookies = match window.cookies_for_url(url.clone()) {
                Ok(cookies) => cookies,
                Err(error) => {
                    log_event("download", &format!("쿠키를 읽지 못했다 via={via} path={path}: {error}"));
                    notify_download(&window, app_origin.as_deref(), false, None);
                    return;
                }
            };
            let header = download::cookie_header(cookies.iter().map(|cookie| (cookie.name(), cookie.value())));
            let dir = match app.path().download_dir() {
                Ok(dir) => dir,
                Err(error) => {
                    log_event("download", &format!("다운로드 폴더를 찾지 못했다: {error}"));
                    notify_download(&window, app_origin.as_deref(), false, None);
                    return;
                }
            };
            match download::fetch(&url, &header, &dir) {
                Ok(download::Fetched::Saved(name)) => {
                    log_event("download", &format!("saved via={via} path={path} name={name}"));
                    notify_download(&window, app_origin.as_deref(), true, Some(&name));
                }
                Ok(download::Fetched::NotAttachment(status)) => {
                    // 2xx inline — 창을 바꾸지 않는다(위 설명). 알림도 없다: 실패가 아니다.
                    log_event("download", &format!("not-attachment via={via} path={path} status={status} — 그대로 둔다"));
                }
                Ok(download::Fetched::Refused(status)) => {
                    // fix1 W2 — 세션 만료·리다이렉트·없음·서버 오류. 「아무 일 없음」으로 삼키지 않는다.
                    log_event("download", &format!("refused via={via} path={path} status={status}"));
                    notify_download(&window, app_origin.as_deref(), false, None);
                }
                Err(error) => {
                    log_event("download", &format!("failed via={via} path={path}: {error}"));
                    notify_download(&window, app_origin.as_deref(), false, None);
                }
            }
        });
    if let Err(error) = spawned {
        log_event("download", &format!("받기 스레드를 띄우지 못했다: {error}"));
    }
}

/// 저장 결과를 **웹에 사건으로** 알린다(U-5 5 · OQ-T12). 셸은 문구를 그리지 않는다.
/// 권한이 필요 없는 길(`eval` → DOM `CustomEvent`)이라 capabilities 를 바꾸지 않는다.
/// 지금 창에 **앱 origin** 의 문서가 떠 있을 때만 보낸다 — 셸 자기 화면·인증 주소에는 보내지 않는다.
fn notify_download(window: &WebviewWindow, app_origin: Option<&str>, ok: bool, filename: Option<&str>) {
    let on_app = match (window.url(), app_origin) {
        (Ok(current), Some(origin)) => guard::origin_of(&current) == origin,
        _ => false,
    };
    if !on_app {
        log_event("download", "웹 앱 문서가 아니라 결과를 알리지 않는다");
        return;
    }
    if let Err(error) = window.eval(download::event_script(ok, filename)) {
        log_event("download", &format!("결과를 웹에 알리지 못했다: {error}"));
    }
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

/// 메인 창(웹뷰)을 **새로** 만든다 — 부팅 때 한 번, 그리고 medi-ax 트레이 「열기」가 닫힌 창을 다시
/// 만들 때(R3-F2). 창의 모든 훅(네비게이션·새 창·내려받기·페이지 로드·창 사건)은 **여기 한 곳**에
/// 있어 두 경로가 갈라지지 않는다. L-06·S-6·AC-T32 는 그대로다(창을 실제로 만들고/파괴한다).
fn spawn_main_window(
    app: &AppHandle,
    shell: &SharedShell,
    start_url: Url,
    nav_allow: Vec<String>,
    app_origin: Option<String>,
    do_watch: bool,
) -> tauri::Result<WebviewWindow> {
    let load_shell = shell.clone();
    let nav_app = app.clone();
    let nav_origin = app_origin.clone();
    let popup_origin = app_origin.clone();
    let popup_app = app.clone();
    let saved_origin = app_origin.clone();
    let saved_shell = shell.clone();

    let window =
        WebviewWindowBuilder::new(app, MAIN_WINDOW, WebviewUrl::External(start_url.clone()))
            // 창 제목 = 판의 productName(flavors/<판>/tauri.conf.json). 이름을 두 곳에 두지 않는다.
            .title(app.package_info().name.clone())
            .inner_size(1280.0, 860.0)
            // 원격 웹의 HTML5 drag/drop 을 WKWebView 가 직접 처리해야 한다.
            // Tauri 의 네이티브 파일 드롭 핸들러를 켜 두면 웹의 drag/drop 이벤트를 소비한다.
            .disable_drag_drop_handler()
            // **쿠키가 남아야 재시작 후에도 로그인이 유지된다**(AC-T26·AC-T35).
            // incognito 를 켜면 실행할 때마다 로그아웃된다 — 켜지 않는다.
            .incognito(false)
            .on_navigation(move |url| {
                // 판정은 `guard::navigation_verdict` 한 곳 — 순서(셸 화면 → 빈 프레임 문서 → 허용 목록 → `/api/` → 허용)가
                // 계약이고 단위 시험이 지킨다. 여기서는 판정대로 «한다»(외부 열기 · 가로채기 · 기록)만.
                match guard::navigation_verdict(url, &nav_allow, nav_origin.as_deref(), SHELL_SCHEME) {
                    guard::NavVerdict::Allow => {
                        // 011 — 하위 프레임의 `about:blank` · `about:srcdoc` 도 여기로 온다(프레임 구분 없음 · I-1).
                        log_event("nav", &format!("allowed url-scheme={} origin={}", url.scheme(), guard::origin_of(url)));
                        true
                    }
                    guard::NavVerdict::Block { origin, open_external } => {
                        // `E-05` — 취소하고 (http(s) 면) 기본 브라우저로 넘긴다.
                        // **점유는 그대로다**(L-11 · I-4): 막힌 이동은 문서 교체가 아니다.
                        if open_external {
                            if let Err(error) = tauri_plugin_opener::open_url(url.as_str(), None::<&str>) {
                                log_event("nav", &format!("외부 열기 실패: {error}"));
                            }
                        }
                        log_event("nav", &format!("blocked origin={origin} — 점유는 유지한다(L-11)"));
                        false
                    }
                    guard::NavVerdict::TakeOverApi => {
                        // U-5 — 같은 origin 의 `/api/` 이동은 **셸이 받아 본다.** 창은 그대로 둔다.
                        // macOS 는 `_blank` 도 여기를 먼저 지난다 — 그래서 두 번째 창도 뜨지 않는다.
                        take_over_api_link(nav_app.clone(), nav_origin.clone(), url.clone(), "navigation");
                        false
                    }
                }
            })
            // U-5 · AC-T45 — 새 창 링크(Windows 는 `_blank` 가 이쪽으로만 온다).
            // `/api/` 가 아니면 **지금 동작을 그대로** 둔다: 핸들러가 없을 때 macOS(wry)는 새 창을
            // 만들지 않았고(`nil`), Windows 는 WebView2 기본 동작이었다(OQ-T13 불변).
            .on_new_window(move |url, _features| {
                if download::is_api_request(&url, popup_origin.as_deref()) {
                    take_over_api_link(popup_app.clone(), popup_origin.clone(), url, "new-window");
                    return NewWindowResponse::Deny;
                }
                if cfg!(target_os = "macos") {
                    NewWindowResponse::Deny
                } else {
                    NewWindowResponse::Allow
                }
            })
            // 웹뷰가 스스로 내려받기로 넘기는 것(표시할 수 없는 형식 · `<a download>`)도 같은 규칙 —
            // 다운로드 폴더 · 대화상자 없음 · 같은 이름이면 번호 · 결과는 웹 사건.
            .on_download(move |webview, event| {
                let notify = |ok: bool, name: Option<&str>| {
                    if let Some(window) = webview.app_handle().get_webview_window(MAIN_WINDOW) {
                        notify_download(&window, saved_origin.as_deref(), ok, name);
                    }
                };
                match event {
                    DownloadEvent::Requested { url, destination } => {
                        let suggested = destination
                            .file_name()
                            .map(|name| name.to_string_lossy().to_string())
                            .and_then(|name| download::safe_file_name(&name))
                            .unwrap_or_else(|| "download".to_string());
                        let dir = match webview.app_handle().path().download_dir() {
                            Ok(dir) => dir,
                            Err(error) => {
                                // fix1 W6 — 취소하는 길도 실패로 알린다.
                                log_event("download", &format!("다운로드 폴더를 찾지 못했다: {error}"));
                                notify(false, None);
                                return false;
                            }
                        };
                        let Ok(mut pending) = saved_shell.downloads.lock() else {
                            log_event("download", "진행 장부 잠금 실패");
                            notify(false, None);
                            return false;
                        };
                        let Some(path) = download::free_path_avoiding(&dir, &suggested, &pending) else {
                            log_event("download", "빈 이름을 찾지 못했다");
                            drop(pending);
                            notify(false, None);
                            return false;
                        };
                        pending.start(url.as_str(), path.clone());
                        *destination = path;
                        true
                    }
                    DownloadEvent::Finished { url, success, .. } => {
                        let path = saved_shell
                            .downloads
                            .lock()
                            .ok()
                            .and_then(|mut pending| pending.finish(url.as_str()));
                        if success {
                            if let Some(path) = &path {
                                download::mark_quarantined(path);
                            }
                        }
                        let name = path
                            .as_ref()
                            .and_then(|path| path.file_name())
                            .map(|name| name.to_string_lossy().to_string());
                        log_event("download", &format!("webview-download success={success} name={name:?}"));
                        notify(success, name.as_deref().filter(|_| success));
                        true
                    }
                    _ => true,
                }
            })
            .on_page_load(move |window, payload| {
                let label = window.label().to_string();
                match payload.event() {
                    tauri::webview::PageLoadEvent::Started => {
                        // 새 문서는 알림 클릭을 들을 준비가 다시 될 때까지(그 문서의 `shell_info`) 기다린다(검수 W-1).
                        if let Ok(mut relay) = load_shell.clicks.lock() {
                            relay.on_document_gone();
                        }
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
    if do_watch {
        watch_load(shell.clone(), window.clone(), start_url.clone());
    }

    let event_shell = shell.clone();
    window.on_window_event(move |event| match event {
        WindowEvent::CloseRequested { .. } => {
            // **요청은 «완료»가 아니다**(I-4·I-5 · `E-12`). 여기서 점유를 풀면,
            // 사용자가 「계속 녹음」을 고른 바로 그 순간 기기가 잠들 수 있게 된다.
            log_event("win", "close-requested — 아무것도 풀지 않는다");
        }
        WindowEvent::Destroyed => {
            if let Ok(mut relay) = event_shell.clicks.lock() {
                relay.on_document_gone();
            }
            // **L-06 — 창이 «실제로» 닫혔다.** 웹이 해제를 못 불렀어도 여기서 풀린다.
            // medi-ax 는 창이 파괴돼도 프로세스가 메뉴 막대에 남는다(RunEvent::ExitRequested · R3-F2).
            event_shell.clear_window(MAIN_WINDOW, "window-destroyed(L-06)");
        }
        _ => {}
    });

    Ok(window)
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
    // 첨부 가로채기·저장 알림의 기준 — **앱(운영) origin 하나**(fix1 W3). 인증 흐름용 허용 목록과 섞지 않는다.
    let app_origin: Option<String> = match &target {
        Target::Configured { url, .. } => Some(guard::origin_of(url)),
        Target::Missing => None,
    };

    let protocol_shell = shell.clone();
    let watch_target = target.clone();
    let builder = tauri::Builder::default()
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
            let handle = app.handle().clone();
            // OS 알림 클릭 → 창 앞으로 + 웹 사건(WORK-013 WP4). macOS 는 여기서 UN delegate 를 건다(번들일 때만).
            let click_app = handle.clone();
            let click_origin = app_origin.clone();
            notify::install(Box::new(move |notification_id, target| {
                notify_click(&click_app, click_origin.as_deref(), notification_id, target);
            }));
            let do_watch = matches!(watch_target, Target::Configured { .. });
            // 창(웹뷰)과 그 훅은 `spawn_main_window` 한 곳에 있다 — 부팅과 트레이 「열기」가 같은 창을 만든다.
            spawn_main_window(
                &handle,
                &shell,
                start_url.clone(),
                navigation_allowlist.clone(),
                app_origin.clone(),
                do_watch,
            )?;

            // ── medi-ax: 수집기 + 메뉴 막대 트레이 (판 가르기 — strong-hajin 에는 컴파일되지 않는다) ──
            #[cfg(feature = "kakao-collector")]
            {
                // 수집기는 창과 무관하게 기기 토큰 Bearer 로 돈다(R3-F2). base = 운영 origin(=/api 와 같은 origin).
                let shared = match app_origin.clone() {
                    Some(origin) => kakao::collector::spawn(origin),
                    None => kakao::collector::Shared::default(),
                };
                app.manage(shared);

                // 트레이 「열기」가 **닫힌 창을 새로** 만든다(창은 닫을 때 실제로 파괴된다 · R3-F2).
                let tray_shell = shell.clone();
                let tray_start = start_url.clone();
                let tray_nav = navigation_allowlist.clone();
                let tray_origin = app_origin.clone();
                // 닫힌 창을 다시 만드는 길 하나 — 트레이 「열기」 와 알림 클릭(검수 W-1 ①)이 같이 쓴다.
                let reopen: Reopen = Arc::new(move |app: &AppHandle| {
                    if let Err(error) = spawn_main_window(
                        app,
                        &tray_shell,
                        tray_start.clone(),
                        tray_nav.clone(),
                        tray_origin.clone(),
                        false,
                    ) {
                        log_event("tray", &format!("창을 다시 만들지 못했다: {error}"));
                    }
                });
                let _ = REOPEN_MAIN.set(reopen.clone());
                kakao::tray::build(&handle, move |app| reopen(app))?;
            }
            Ok(())
        });

    // Windows 는 공식 알림 플러그인으로 띄운다(클릭 콜백 없음). capability 에 플러그인 권한을 넣지 않으므로
    // 원격 문서는 플러그인 커맨드를 부를 수 없다 — 셸 Rust 만 쓴다.
    #[cfg(windows)]
    let builder = builder.plugin(tauri_plugin_notification::init());

    // 커맨드 목록 — 두 판 공통 여섯(기본 넷 + 알림 둘 · SPEC-006 v0.7.0) · medi-ax 는 카톡 셋이 더해져 **아홉**.
    #[cfg(feature = "kakao-collector")]
    let builder = builder.invoke_handler(tauri::generate_handler![
        shell_info,
        wake_guard_acquire,
        wake_guard_release,
        open_external,
        notify_permission,
        notify_show,
        kakao::commands::kakao_list_rooms,
        kakao::commands::kakao_collector_status,
        kakao::commands::kakao_store_device_token
    ]);
    #[cfg(not(feature = "kakao-collector"))]
    let builder = builder.invoke_handler(tauri::generate_handler![
        shell_info,
        wake_guard_acquire,
        wake_guard_release,
        open_external,
        notify_permission,
        notify_show
    ]);

    let app = builder
        .build(tauri::generate_context!())
        .expect("셸을 띄우지 못했습니다");

    // medi-ax: 마지막 창이 닫혀도(웹뷰 파괴 · Destroyed 는 그대로 돈다) 프로세스는 메뉴 막대에 남는다.
    // 종료는 트레이 「종료」뿐이다(R3-F2 · OQ-T10 닫힘). strong-hajin 은 이 콜백이 아무것도 하지 않아 종전 그대로다.
    app.run(move |_app_handle, _event| {
        #[cfg(feature = "kakao-collector")]
        if let tauri::RunEvent::ExitRequested { api, .. } = &_event {
            if kakao::tray::should_prevent_exit() {
                api.prevent_exit();
            }
        }
    });
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
            downloads: Mutex::new(download::PendingDownloads::default()),
            clicks: Mutex::new(notify::ClickRelay::default()),
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

    const CAPABILITY: &str =
        include_str!(concat!("../flavors/", env!("SHELL_FLAVOR"), "/capabilities/product-shell.json"));
    const TAURI_CONF: &str = include_str!("../tauri.conf.json");
    const PERSONAL_CONF: &str = include_str!("../flavors/strong-hajin/tauri.conf.json");
    const COMPANY_CONF: &str = include_str!("../flavors/medi-ax/tauri.conf.json");
    const PERSONAL_CAPABILITY: &str = include_str!("../flavors/strong-hajin/capabilities/product-shell.json");
    const COMPANY_CAPABILITY: &str = include_str!("../flavors/medi-ax/capabilities/product-shell.json");

    fn json(raw: &str) -> serde_json::Value {
        serde_json::from_str(raw).expect("JSON")
    }
    const CARGO_TOML: &str = include_str!("../Cargo.toml");

    fn capability() -> serde_json::Value {
        serde_json::from_str(CAPABILITY).expect("capability 가 JSON 이어야 한다")
    }

    #[test]
    fn 웹에_여는_커맨드가_판에_맞다() {
        // AC-T23 / AC-T47 (SPEC-006 v0.7.0) — strong-hajin = **여섯**(넷 + 알림 둘), medi-ax = **아홉**(여섯 + 카톡 셋).
        // 어느 판이든 파일·프로세스·범용 셸·open_path 표면은 **하나도** 없다 — 카톡 커맨드도 Rust 가
        // 읽어 결과만 주지 웹에 파일 권한을 열지 않는다(「외부 채널 수집기 수용」 절).
        let flavor = env!("SHELL_FLAVOR");
        let permissions = capability()["permissions"]
            .as_array()
            .expect("permissions 배열")
            .iter()
            .map(|value| value.as_str().unwrap().to_string())
            .collect::<Vec<_>>();
        let mut expected = vec![
            "allow-shell-info",
            "allow-wake-guard-acquire",
            "allow-wake-guard-release",
            "allow-open-external",
            "allow-notify-permission",
            "allow-notify-show",
        ];
        if flavor == "medi-ax" {
            expected.extend([
                "allow-kakao-list-rooms",
                "allow-kakao-collector-status",
                "allow-kakao-store-device-token",
            ]);
        }
        assert_eq!(
            permissions.len(),
            expected.len(),
            "판 {flavor} 의 커맨드 수가 다르다: {permissions:?}"
        );
        for want in &expected {
            assert!(permissions.contains(&want.to_string()), "{want} 가 없다");
        }
        for forbidden in ["fs:", "shell:", "process:", "opener:", "open-path", "reveal-item"] {
            assert!(
                !permissions.iter().any(|item| item.contains(forbidden)),
                "{forbidden} 표면이 열렸다: {permissions:?}"
            );
        }
        // strong-hajin 판은 카톡 커맨드가 **없어야** 한다(판 가르기 — 개인판엔 수집기 자체가 없다).
        if flavor != "medi-ax" {
            assert!(
                !permissions.iter().any(|p| p.contains("kakao")),
                "개인판에 카톡 커맨드가 샜다: {permissions:?}"
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
    fn 판마다_운영_origin_은_설정과_커맨드_허용이_같다() {
        // 개인판 — OQ-T02: fixture 주소를 운영값으로 승격하지 않았다. `.invalid` 는 예약 TLD(RFC 2606)라
        // 실재할 수 없다. 개인판 주소가 정해지면 그때 이 단언이 바뀐다.
        let personal = json(PERSONAL_CAPABILITY);
        let url = personal["remote"]["urls"][0].as_str().unwrap();
        assert!(url.contains(".invalid"), "개인판 운영 origin 을 발명했다: {url}");
        assert!(!url.contains("medisolveai"), "회사판 주소가 개인판으로 새었다: {url}");

        // 회사판 — Phase 8(2026-10-01) 확정. 설정은 A 인데 권한은 B 인 판을 막는다.
        let company = json(COMPANY_CAPABILITY);
        assert_eq!(company["remote"]["urls"][0].as_str().unwrap(), "https://ax.medisolveai.xyz/*");

        // 이번 빌드가 실은 판: capability 와 shell.config 가 같은 origin 이다(주소가 서 있다면).
        let url = capability()["remote"]["urls"][0].as_str().unwrap().to_string();
        assert!(!url.contains("localhost"), "fixture 주소가 승격됐다: {url}");
        match config::load().unwrap() {
            config::Target::Configured { url: target, .. } => {
                assert_eq!(format!("{}/*", target.origin().ascii_serialization()), url);
            }
            config::Target::Missing => assert!(url.contains(".invalid"), "주소 없는 판이 커맨드를 연다: {url}"),
        }
    }

    #[test]
    fn 셸_설정이_제품_정체성을_지킨다() {
        // AC-T34 — 참조 제품과 겹치지 않는다. 식별자는 **판마다 고정**한다 — 한 판 안에서 바뀌면
        // 저장소(쿠키)가 새로 잡혀 그 판 사용자가 로그아웃된다. 판끼리는 달라야 서로의 저장소를 밟지 않는다.
        let personal = json(PERSONAL_CONF);
        let company = json(COMPANY_CONF);
        assert_eq!(personal["identifier"], "app.stronghajin.desktop");
        assert_eq!(personal["productName"], "Strong Hajin");
        assert_eq!(company["identifier"], "app.ax.desktop");
        assert_eq!(company["productName"], "medi-ax");
        assert_eq!(company["mainBinaryName"], "medi-ax");
        // 기본 설정(오버레이 없이 도는 cargo·tauri dev)은 개인판이다.
        assert!(TAURI_CONF.contains(r#""identifier": "app.stronghajin.desktop""#));
        assert!(TAURI_CONF.contains(r#""productName": "Strong Hajin""#));
        // 판 오버레이는 이름·실행 파일 이름·식별자만 바꾼다 — 권한·창·번들 구성은 공통이다.
        for overlay in [&personal, &company] {
            for key in overlay.as_object().unwrap().keys() {
                assert!(
                    ["$schema", "productName", "mainBinaryName", "identifier"].contains(&key.as_str()),
                    "판 오버레이가 공통 설정을 바꾼다: {key}"
                );
            }
        }
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

    #[test]
    fn 제품_창은_첨부_응답을_셸이_받고_웹에_권한을_주지_않는다() {
        // U-5 · AC-T44 ~ AC-T47 — 이동·새 창·웹뷰 내려받기 세 길이 모두 창 빌더에 걸려 있어야 한다.
        let source = include_str!("lib.rs");
        let builder_start = source
            .find("WebviewWindowBuilder::new(app, MAIN_WINDOW")
            .expect("제품 창 빌더가 있어야 한다");
        let builder = &source[builder_start..];
        let builder = &builder[..builder.find(".build()?").expect("build")];
        for hook in [".on_navigation(", ".on_new_window(", ".on_download("] {
            assert!(builder.contains(hook), "{hook} 가 창 빌더에 없다");
        }
        // fix1 W3 — 가로채기 기준은 인증 흐름용 허용 목록(`nav_allow`)이 아니라 앱 origin 하나다.
        // 이동 판정은 `guard::navigation_verdict` 로 옮겼다(WORK-012 SHELL 011) — 창 빌더는 앱 origin 을 그 판정에 넘기고,
        // 판정 안에서 `/api/` 가로채기가 앱 origin 으로 갈린다(`guard.rs` 단위 시험이 순서·결과를 지킨다).
        assert!(builder.contains("guard::navigation_verdict(url, &nav_allow, nav_origin.as_deref(), SHELL_SCHEME)"));
        assert!(builder.contains("download::is_api_request(&url, popup_origin.as_deref())"));
        assert!(!builder.contains("is_api_request(url, &nav_allow)"));
        let guard_source = include_str!("guard.rs");
        assert!(guard_source.contains("crate::download::is_api_request(url, app_origin)"));
        // 저장 결과는 eval 사건으로 간다 — 이벤트 수신 권한(core:event)도 웹에 열지 않는다.
        for capability in [PERSONAL_CAPABILITY, COMPANY_CAPABILITY] {
            assert!(!capability.contains("core:"), "기본 권한이 열렸다");
            assert!(!capability.contains("fs:"), "파일 권한이 열렸다");
        }
    }

    fn s_contains_second_window() -> bool {
        // 창은 Rust 가 하나만 만든다(`MAIN_WINDOW`). 설정에 창 목록이 비어 있어야 한다.
        serde_json::from_str::<serde_json::Value>(TAURI_CONF)
            .ok()
            .and_then(|value| value["app"]["windows"].as_array().map(|list| !list.is_empty()))
            .unwrap_or(true)
    }

    #[test]
    fn 셸_프레임워크_하한이_2_12_이상이다() {
        // AC-T25 — GHSA-7gmj-67g7-phm9(원격 문서가 로컬 전용 커맨드를 부르는 origin 혼동)는 2.11.1 하한.
        // WORK-013 WP4 — Windows 알림 플러그인이 2.12 를 요구해 하한이 2.12 로 올랐다(사용자 처분 2026-10-08).
        assert!(CARGO_TOML.contains(r#"tauri = { version = "2.12""#));
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
            (parts[0], parts[1], parts[2]) >= (2, 12, 0),
            "tauri 가 2.12 미만이다: {version}"
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
            include_str!("download.rs"),
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
