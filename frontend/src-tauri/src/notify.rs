//! **OS 알림 — 권한 · 표시 · 클릭** (WORK-013 WP4 · SPEC-006 v0.7.0 「시스템 알림 수용」 · SPEC-011 §2.5 · §4.6).
//!
//! 셸은 **웹이 넘긴 것만 띄운다**(D-09) — 알림 종류를 모르고, 거르지 않고, 글자를 만들지 않는다(U-1).
//! 웹이 부르는 것은 커맨드 둘(`notify_permission` · `notify_show`)뿐이고, 클릭은 **셸 → 웹 사건**
//! (`strong-hajin:notification-click` — 다운로드 사건과 같은 `window.eval` 길 · 같은 「앱 origin 일 때만」 규칙)으로 간다.
//!
//! ## 판마다 수단 (사용자 처분 2026-10-08 — OQ-1108 ④)
//! - **macOS** — `UNUserNotificationCenter` 를 objc2 로 **직접** 부른다(`mac` 모듈). 공식 플러그인은 데스크톱 클릭
//!   콜백이 없다(P0-2). 진짜 권한 프롬프트 · 권한 상태 · 보내기(`userInfo` 에 `notification_id` · `target`) ·
//!   delegate `didReceive`(기본 동작 = 클릭) · `willPresent`(앱이 앞에 있어도 배너 — 생략 판단은 웹이 한다).
//!   **UN 은 번들(.app)이어야 한다** — `tauri dev` 처럼 번들이 아니면 **panic 없이** 「거부」 · `{shown:false}` 로 떨어진다.
//! - **Windows** — 공식 `tauri-plugin-notification`(클릭 콜백 없음 — OS 기본 = 앱만 앞으로 · 실기 pending).
//!   권한은 플러그인 데스크톱 구현대로 늘 허용이다.
//! - 그 밖 — 지원하지 않는다(`default` · `{shown:false}`).

use serde::{Deserialize, Serialize};

/// 셸 → 웹 사건 이름. **`frontend/src/lib/shell.ts` 의 `SHELL_NOTIFICATION_CLICK_EVENT` 와 같아야 한다**(시험이 대조한다).
pub const CLICK_EVENT: &str = "strong-hajin:notification-click";

/// 받는 값의 상한 — 웹이 만든 글자라도 셸이 무한히 받지 않는다(OS 배너는 어차피 앞부분만 보인다).
pub const MAX_TITLE_CHARS: usize = 200;
pub const MAX_BODY_CHARS: usize = 1000;
pub const MAX_ID_CHARS: usize = 128;
pub const MAX_TARGET_BYTES: usize = 4096;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum PermissionState {
    Granted,
    Denied,
    /// 아직 묻지 않았다(macOS `NotDetermined`) — 또는 이 판·이 실행 형태가 알림을 지원하지 않는다.
    Default,
}

#[derive(Debug, Serialize)]
pub struct PermissionReply {
    pub state: PermissionState,
}

#[derive(Debug, Serialize)]
pub struct ShowReply {
    pub shown: bool,
}

/// `notify_show` 의 인자(SPEC-011 §4.6) — 묶음 「새 알림 N건」 은 `notification_id: null` · `target: {surface: "notifications"}`.
#[derive(Debug, Clone, Deserialize)]
pub struct ShowRequest {
    pub notification_id: Option<String>,
    pub title: String,
    pub body: String,
    pub target: serde_json::Value,
}

/// 인자 검증 — 어긋나면 띄우지 않고 오류로 돌려준다(웹 버그를 OS 에 흘리지 않는다).
pub fn validate(request: &ShowRequest) -> Result<(), String> {
    if let Some(id) = &request.notification_id {
        if id.is_empty() || id.chars().count() > MAX_ID_CHARS || id.chars().any(|c| c.is_control()) {
            return Err("notification_id 가 올바르지 않다".into());
        }
    }
    if request.title.trim().is_empty() && request.body.trim().is_empty() {
        return Err("제목과 본문이 모두 비었다".into());
    }
    if request.title.chars().count() > MAX_TITLE_CHARS {
        return Err(format!("제목이 {MAX_TITLE_CHARS}자를 넘는다"));
    }
    if request.body.chars().count() > MAX_BODY_CHARS {
        return Err(format!("본문이 {MAX_BODY_CHARS}자를 넘는다"));
    }
    // `null` = 지금 열 수 없는 대상(SPEC-011 §4.5-4) — 누르면 목록 줄과 같은 일(읽음 + 「열 수 없는 항목입니다」 · 검수 W-4)
    if !(request.target.is_object() || request.target.is_null()) {
        return Err("target 은 객체이거나 null 이어야 한다".into());
    }
    if request.target.to_string().len() > MAX_TARGET_BYTES {
        return Err(format!("target 이 {MAX_TARGET_BYTES}바이트를 넘는다"));
    }
    Ok(())
}

/// 클릭 사건을 웹에 쏘는 스크립트 — 값은 `serde_json` 으로만 싣는다(문자열 이어 붙이기 없음).
pub fn click_script(notification_id: Option<&str>, target: &serde_json::Value) -> String {
    let detail = serde_json::json!({ "notification_id": notification_id, "target": target });
    let name = serde_json::Value::String(CLICK_EVENT.to_string());
    format!("window.dispatchEvent(new CustomEvent({name}, {{ detail: {detail} }}));")
}

/// 웹이 아직 들을 수 없을 때 클릭을 보관하는 시간 — 넘으면 버린다(검수 W-1 · 오래된 클릭이 뒤늦게 화면을 옮기지 않게).
pub const PENDING_CLICK_TTL: std::time::Duration = std::time::Duration::from_secs(60);

/// 웹에 보낼 클릭 하나.
#[derive(Debug, Clone, PartialEq)]
pub struct Click {
    pub notification_id: Option<String>,
    pub target: serde_json::Value,
}

/// **클릭 중계** (검수 W-1) — 웹 문서가 들을 준비가 됐는지 보고, 아니면 클릭을 **하나만** 보관했다가 준비되면 한 번 보낸다.
///
/// - 「준비됨」 = 지금 문서(앱 origin)가 `shell_info` 를 불렀다 — 웹은 앱이 선 뒤 알림 다리를 세우며 그것을 묻는다
///   (`lib/shell.ts` `hasShellNotifications`). 그 호출은 클릭 구독(`onShellNotificationClick`)과 같은 렌더에서 걸린 뒤라
///   그 뒤에 쏜 사건은 들린다
/// - 문서가 바뀌거나(새로 고침 · 이동) 창이 파괴되면 다시 「준비 안 됨」
/// - 보관은 한 칸 — 새 클릭이 옛 것을 갈아 끼운다 · `PENDING_CLICK_TTL` 이 지나면 버린다
#[derive(Debug, Default)]
pub struct ClickRelay {
    ready: bool,
    pending: Option<(Click, std::time::Instant)>,
}

impl ClickRelay {
    /// 클릭이 왔다 — 준비됐으면 지금 보낼 것을 돌려주고, 아니면 보관한다.
    pub fn on_click(&mut self, click: Click, now: std::time::Instant) -> Option<Click> {
        if self.ready {
            self.pending = None;
            return Some(click);
        }
        self.pending = Some((click, now));
        None
    }

    /// 웹이 준비됐다 — 보관한 클릭이 상한 안이면 돌려준다(한 번만).
    pub fn on_ready(&mut self, now: std::time::Instant) -> Option<Click> {
        self.ready = true;
        let (click, at) = self.pending.take()?;
        (now.duration_since(at) <= PENDING_CLICK_TTL).then_some(click)
    }

    /// 문서가 바뀌었거나 창이 파괴됐다 — 새 문서가 다시 「준비됨」 을 알릴 때까지 보관한다.
    pub fn on_document_gone(&mut self) {
        self.ready = false;
    }

    #[cfg(test)]
    pub fn is_ready(&self) -> bool {
        self.ready
    }
}

/// 클릭을 받았을 때 부를 것 — `(notification_id, target)`. 셸이 창을 앞으로 가져온 뒤 웹에 사건을 보낸다(lib.rs).
pub type ClickHandler = Box<dyn Fn(Option<String>, serde_json::Value) + Send + Sync + 'static>;

/// 앱이 설 때 한 번 — macOS 는 UN delegate 를 건다(번들이 아니면 아무것도 하지 않는다).
pub fn install(on_click: ClickHandler) {
    #[cfg(target_os = "macos")]
    mac::install(on_click);
    #[cfg(not(target_os = "macos"))]
    drop(on_click);
}

/// 권한을 읽는다 — `request` 면 아직 묻지 않았을 때 OS 에 묻는다(프롬프트). 블로킹이라 커맨드는 별도 스레드에서 부른다.
pub fn permission<R: tauri::Runtime>(_app: &tauri::AppHandle<R>, request: bool) -> PermissionState {
    #[cfg(target_os = "macos")]
    {
        mac::permission(request)
    }
    #[cfg(windows)]
    {
        let _ = request;
        PermissionState::Granted
    }
    #[cfg(not(any(target_os = "macos", windows)))]
    {
        let _ = request;
        PermissionState::Default
    }
}

/// 알림 하나를 띄운다 — 권한이 없거나 이 실행 형태가 못 띄우면 `false`(오류 아님 · SPEC-011 §4.6).
pub fn show<R: tauri::Runtime>(_app: &tauri::AppHandle<R>, request: &ShowRequest) -> bool {
    #[cfg(target_os = "macos")]
    {
        mac::show(request)
    }
    #[cfg(windows)]
    {
        use tauri_plugin_notification::NotificationExt;
        _app.notification()
            .builder()
            .title(request.title.clone())
            .body(request.body.clone())
            .show()
            .is_ok()
    }
    #[cfg(not(any(target_os = "macos", windows)))]
    {
        let _ = request;
        false
    }
}

#[cfg(target_os = "macos")]
mod mac {
    //! `UNUserNotificationCenter` (objc2 0.6 · objc2-user-notifications 0.3).
    //!
    //! - **스레드** — UN 의 메서드는 어느 스레드에서 불러도 된다. 완료 블록은 UN 이 자기 큐에서 부른다 → 커맨드 스레드
    //!   (`spawn_blocking`)는 `mpsc` 로 기다린다(상한 시간 — 프롬프트는 사람을 기다리므로 길게). 메인 run loop 를 막지 않는다
    //! - **delegate** — 앱 설치 때(메인 스레드 `setup`) 한 번 걸고 **영영 놓지 않는다**: UN 의 `delegate` 는 약한 참조라
    //!   우리가 들고 있지 않으면 사라진다. 클릭 처리기는 전역 한 칸(`OnceLock`)에 둔다
    //! - **번들 없음** — `currentNotificationCenter` 는 번들이 아니면 Objective-C 예외를 던진다. 그래서 ① 번들(.app ·
    //!   식별자)인지 먼저 보고 ② 그래도 `objc2::exception::catch` 로 감싼다 — 어느 쪽이든 panic 없이 「못 함」 으로 떨어진다
    use std::sync::{mpsc, OnceLock};
    use std::time::Duration;

    use block2::RcBlock;
    use objc2::rc::Retained;
    use objc2::runtime::{AnyObject, Bool, NSObjectProtocol, ProtocolObject};
    use objc2::{define_class, msg_send, AllocAnyThread};
    use objc2_foundation::{NSBundle, NSDictionary, NSError, NSObject, NSString};
    use objc2_user_notifications::{
        UNAuthorizationOptions, UNAuthorizationStatus, UNMutableNotificationContent, UNNotification,
        UNNotificationDefaultActionIdentifier, UNNotificationPresentationOptions, UNNotificationRequest,
        UNNotificationResponse, UNNotificationSettings, UNNotificationSound, UNUserNotificationCenter,
        UNUserNotificationCenterDelegate,
    };

    use super::{ClickHandler, PermissionState, ShowRequest};

    const KEY_ID: &str = "notification_id";
    const KEY_TARGET: &str = "target";
    /// 사람이 프롬프트에 답할 시간 — 넘기면 「아직 모름」.
    const PROMPT_WAIT: Duration = Duration::from_secs(120);
    const CALL_WAIT: Duration = Duration::from_secs(10);

    static ON_CLICK: OnceLock<ClickHandler> = OnceLock::new();

    /// 번들(.app)로 떠 있는가 — `tauri dev` 의 맨 실행 파일은 아니다.
    pub(super) fn bundled() -> bool {
        let bundle = NSBundle::mainBundle();
        bundle.bundleIdentifier().is_some() && bundle.bundlePath().to_string().ends_with(".app")
    }

    fn center() -> Option<Retained<UNUserNotificationCenter>> {
        if !bundled() {
            return None;
        }
        // 클로저는 Objective-C 메서드 하나를 부를 뿐이다 — 예외(번들 없음 등)가 나면 Err 로 받는다.
        objc2::exception::catch(UNUserNotificationCenter::currentNotificationCenter).ok()
    }

    define_class!(
        // SAFETY: NSObject 를 상속하는 평범한 delegate 다 — Drop 을 두지 않고 ivar 도 없다.
        #[unsafe(super(NSObject))]
        #[name = "StrongHajinNotificationDelegate"]
        struct Delegate;

        unsafe impl NSObjectProtocol for Delegate {}

        unsafe impl UNUserNotificationCenterDelegate for Delegate {
            /// 앱이 앞에 있어도 배너를 띄운다 — 「보고 있으면 생략」 은 웹이 이미 판단했다(SPEC-011 §2.5 ①).
            #[unsafe(method(userNotificationCenter:willPresentNotification:withCompletionHandler:))]
            fn will_present(
                &self,
                _center: &UNUserNotificationCenter,
                _notification: &UNNotification,
                completion: &block2::DynBlock<dyn Fn(UNNotificationPresentationOptions)>,
            ) {
                completion.call((UNNotificationPresentationOptions::Banner
                    | UNNotificationPresentationOptions::List
                    | UNNotificationPresentationOptions::Sound,));
            }

            /// 사용자가 알림을 눌렀다(기본 동작) — userInfo 의 `notification_id` · `target` 을 처리기에 넘긴다.
            #[unsafe(method(userNotificationCenter:didReceiveNotificationResponse:withCompletionHandler:))]
            fn did_receive(
                &self,
                _center: &UNUserNotificationCenter,
                response: &UNNotificationResponse,
                completion: &block2::DynBlock<dyn Fn()>,
            ) {
                // SAFETY: 프레임워크가 내보내는 상수 NSString 이다.
                let default_action = unsafe { UNNotificationDefaultActionIdentifier };
                if response.actionIdentifier().isEqualToString(default_action) {
                    let info = response.notification().request().content().userInfo();
                    let id = string_of(&info, KEY_ID);
                    let target = string_of(&info, KEY_TARGET)
                        .and_then(|raw| serde_json::from_str::<serde_json::Value>(&raw).ok())
                        .unwrap_or(serde_json::Value::Null);
                    if let Some(handler) = ON_CLICK.get() {
                        handler(id, target);
                    }
                }
                completion.call(());
            }
        }
    );

    impl Delegate {
        fn new() -> Retained<Self> {
            let this = Self::alloc().set_ivars(());
            // SAFETY: NSObject 의 지정 초기화자.
            unsafe { msg_send![super(this), init] }
        }
    }

    fn string_of(info: &NSDictionary, key: &str) -> Option<String> {
        let key = NSString::from_str(key);
        let key_ref: &AnyObject = &key;
        let value = info.objectForKey(key_ref)?;
        let text = value.downcast::<NSString>().ok()?;
        Some(text.to_string())
    }

    pub(super) fn install(on_click: ClickHandler) {
        let _ = ON_CLICK.set(on_click);
        let Some(center) = center() else {
            crate::log_event("notify", "번들이 아니라 UN delegate 를 걸지 않는다 — OS 알림은 서명 번들에서만 뜬다");
            return;
        };
        let delegate = Delegate::new();
        center.setDelegate(Some(ProtocolObject::from_ref(&*delegate)));
        // UN 의 delegate 는 약한 참조다 — 앱이 사는 동안 놓지 않는다.
        std::mem::forget(delegate);
        crate::log_event("notify", "UN delegate 를 걸었다");
    }

    fn status(center: &UNUserNotificationCenter) -> Option<UNAuthorizationStatus> {
        let (tx, rx) = mpsc::channel();
        let block = RcBlock::new(move |settings: std::ptr::NonNull<UNNotificationSettings>| {
            // SAFETY: UN 이 살아 있는 설정 객체를 넘긴다.
            let settings = unsafe { settings.as_ref() };
            let _ = tx.send(settings.authorizationStatus());
        });
        center.getNotificationSettingsWithCompletionHandler(&block);
        rx.recv_timeout(CALL_WAIT).ok()
    }

    fn state_of(status: UNAuthorizationStatus) -> PermissionState {
        if status == UNAuthorizationStatus::NotDetermined {
            PermissionState::Default
        } else if status == UNAuthorizationStatus::Denied {
            PermissionState::Denied
        } else {
            // Authorized · Provisional · Ephemeral — 띄울 수 있다
            PermissionState::Granted
        }
    }

    pub(super) fn permission(request: bool) -> PermissionState {
        let Some(center) = center() else {
            return PermissionState::Default;
        };
        let Some(current) = status(&center) else {
            return PermissionState::Default;
        };
        if current != UNAuthorizationStatus::NotDetermined || !request {
            return state_of(current);
        }
        let (tx, rx) = mpsc::channel();
        let block = RcBlock::new(move |granted: Bool, _error: *mut NSError| {
            let _ = tx.send(granted.as_bool());
        });
        center.requestAuthorizationWithOptions_completionHandler(
            UNAuthorizationOptions::Alert | UNAuthorizationOptions::Sound,
            &block,
        );
        match rx.recv_timeout(PROMPT_WAIT) {
            Ok(true) => PermissionState::Granted,
            Ok(false) => PermissionState::Denied,
            Err(_) => PermissionState::Default,
        }
    }

    pub(super) fn show(request: &ShowRequest) -> bool {
        let Some(center) = center() else {
            return false;
        };
        if status(&center).map(state_of) != Some(PermissionState::Granted) {
            return false;
        }
        let content = UNMutableNotificationContent::new();
        content.setTitle(&NSString::from_str(&request.title));
        content.setBody(&NSString::from_str(&request.body));
        content.setSound(Some(&UNNotificationSound::defaultSound()));
        let mut keys = vec![NSString::from_str(KEY_TARGET)];
        let mut values = vec![NSString::from_str(&request.target.to_string())];
        if let Some(id) = &request.notification_id {
            keys.push(NSString::from_str(KEY_ID));
            values.push(NSString::from_str(id));
        }
        let key_refs: Vec<&NSString> = keys.iter().map(|key| &**key).collect();
        let value_refs: Vec<&AnyObject> = values.iter().map(|value| -> &AnyObject { value }).collect();
        let info = NSDictionary::<NSString, AnyObject>::from_slices(&key_refs, &value_refs);
        // SAFETY: 키·값 모두 NSString(속성 목록 형식)이라 userInfo 로 직렬화된다. 형식 인자만 지운다(같은 객체).
        let info: Retained<NSDictionary> = unsafe { Retained::cast_unchecked(info) };
        unsafe { content.setUserInfo(&info) };
        // 식별자 — 알림 id(같은 알림을 다시 보내면 OS 가 갈아 끼운다) · 묶음은 시각으로
        let identifier = request.notification_id.clone().unwrap_or_else(|| {
            let at = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_millis())
                .unwrap_or(0);
            format!("bundle-{at}")
        });
        let request = UNNotificationRequest::requestWithIdentifier_content_trigger(
            &NSString::from_str(&identifier),
            &content,
            None,
        );
        let (tx, rx) = mpsc::channel();
        let block = RcBlock::new(move |error: *mut NSError| {
            let _ = tx.send(error.is_null());
        });
        center.addNotificationRequest_withCompletionHandler(&request, Some(&block));
        rx.recv_timeout(CALL_WAIT).unwrap_or(false)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request(title: &str, body: &str, target: serde_json::Value) -> ShowRequest {
        ShowRequest { notification_id: Some("n1".into()), title: title.into(), body: body.into(), target }
    }

    #[test]
    fn 사건_이름이_웹_수신부와_같다() {
        let web = include_str!("../../src/lib/shell.ts");
        assert!(
            web.contains(&format!("SHELL_NOTIFICATION_CLICK_EVENT = \"{CLICK_EVENT}\"")),
            "shell.ts 의 SHELL_NOTIFICATION_CLICK_EVENT 가 셸의 사건 이름과 다르다"
        );
    }

    #[test]
    fn 클릭_스크립트는_값을_json_으로만_싣는다() {
        let target = serde_json::json!({ "surface": "work", "task_id": "t\"1</script>" });
        let script = click_script(Some("n'1"), &target);
        assert!(script.starts_with("window.dispatchEvent(new CustomEvent(\"strong-hajin:notification-click\""));
        // 이름·값이 JSON 문자열로 이스케이프된다 — 따옴표가 스크립트를 끊지 않는다
        assert!(script.contains(r#""task_id":"t\"1</script>""#));
        assert!(script.contains(r#""notification_id":"n'1""#));
        let bundle = click_script(None, &serde_json::json!({ "surface": "notifications" }));
        assert!(bundle.contains(r#""notification_id":null"#));
    }

    #[test]
    fn 인자_검증() {
        let ok = request("업무 · 담당", "오지훈님이 업무를 요청했습니다", serde_json::json!({ "surface": "work" }));
        assert!(validate(&ok).is_ok());
        assert!(validate(&ShowRequest { notification_id: None, ..ok.clone() }).is_ok());
        assert!(validate(&ShowRequest { notification_id: Some(String::new()), ..ok.clone() }).is_err());
        assert!(validate(&ShowRequest { notification_id: Some("a\nb".into()), ..ok.clone() }).is_err());
        assert!(validate(&request("", " ", serde_json::json!({}))).is_err());
        assert!(validate(&request(&"가".repeat(MAX_TITLE_CHARS + 1), "", serde_json::json!({}))).is_err());
        assert!(validate(&request("t", &"가".repeat(MAX_BODY_CHARS + 1), serde_json::json!({}))).is_err());
        assert!(validate(&request("t", "b", serde_json::json!("work"))).is_err());
        // 열 수 없는 대상(target: null)도 띄운다 — 검수 W-4
        assert!(validate(&request("t", "b", serde_json::Value::Null)).is_ok());
        assert!(validate(&request("t", "b", serde_json::json!({ "x": "a".repeat(MAX_TARGET_BYTES) }))).is_err());
    }

    fn click(id: &str) -> Click {
        Click { notification_id: Some(id.into()), target: serde_json::json!({ "surface": "work", "task_id": id }) }
    }

    #[test]
    fn 준비된_문서에는_클릭을_바로_보낸다() {
        let mut relay = ClickRelay::default();
        let now = std::time::Instant::now();
        assert_eq!(relay.on_ready(now), None);
        assert_eq!(relay.on_click(click("n1"), now), Some(click("n1")));
    }

    #[test]
    fn 준비_전_클릭은_하나만_보관했다가_준비되면_한_번_보낸다() {
        let mut relay = ClickRelay::default();
        let now = std::time::Instant::now();
        assert_eq!(relay.on_click(click("n1"), now), None);
        assert_eq!(relay.on_click(click("n2"), now), None); // 새 것이 갈아 끼운다
        assert_eq!(relay.on_ready(now), Some(click("n2")));
        assert_eq!(relay.on_ready(now), None); // 한 번만
    }

    #[test]
    fn 보관_상한이_지나면_버린다() {
        let mut relay = ClickRelay::default();
        let then = std::time::Instant::now();
        relay.on_click(click("n1"), then);
        assert_eq!(relay.on_ready(then + PENDING_CLICK_TTL + std::time::Duration::from_secs(1)), None);
    }

    #[test]
    fn 문서가_바뀌면_다시_준비될_때까지_보관한다() {
        let mut relay = ClickRelay::default();
        let now = std::time::Instant::now();
        relay.on_ready(now);
        relay.on_document_gone();
        assert!(!relay.is_ready());
        assert_eq!(relay.on_click(click("n1"), now), None);
        assert_eq!(relay.on_ready(now), Some(click("n1")));
    }

    #[test]
    fn 권한_상태는_소문자_셋이다() {
        for (state, text) in [(PermissionState::Granted, "granted"), (PermissionState::Denied, "denied"), (PermissionState::Default, "default")] {
            assert_eq!(serde_json::to_value(PermissionReply { state }).unwrap(), serde_json::json!({ "state": text }));
        }
        assert_eq!(serde_json::to_value(ShowReply { shown: false }).unwrap(), serde_json::json!({ "shown": false }));
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn 번들이_아니면_panic_없이_못_함으로_떨어진다() {
        // `cargo test` 의 실행 파일은 번들이 아니다 — `tauri dev` 와 같은 처지다(사용자 처분 ④).
        assert!(!mac::bundled());
        assert_eq!(mac::permission(true), PermissionState::Default);
        let ok = request("t", "b", serde_json::json!({ "surface": "work" }));
        assert!(!mac::show(&ok));
        install(Box::new(|_, _| {}));
    }
}
