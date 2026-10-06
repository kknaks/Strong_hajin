//! **메뉴 막대 트레이 상주** — medi-ax 판에서만(SPEC-006 v0.6.0 「외부 채널 수집기 수용」).
//!
//! 창(웹뷰)을 닫으면 창은 **실제로 파괴되고**(L-06·AC-T32 무변경) 앱 프로세스만 메뉴 막대에 남는다
//! (종료는 트레이 「종료」 · 「열기」가 창을 **새로 만든다**). 수집기는 창과 무관하게 네이티브+기기
//! 토큰으로 돈다(R3-F2). strong-hajin 개인판에는 이 파일이 컴파일되지 않는다.
//!
//! FDA(전체 디스크 접근) 딥링크는 `x-apple.systempreferences:` 라 `open_external`(http(s) 전용)로
//! 못 연다 — **네이티브가 직접** 연다(N-11). 트레이 메뉴의 「전체 디스크 접근 설정 열기」가 그 자리다.

use std::sync::atomic::{AtomicBool, Ordering};

use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager};

/// 트레이 「종료」로 내려갈 때만 참. `RunEvent::ExitRequested` 가 이걸 보고 종료를 막을지 정한다 —
/// 마지막 창이 닫혀 ExitRequested 가 와도(사용자가 창만 닫음) 프로세스는 남고, 「종료」일 때만 끝낸다.
static QUITTING: AtomicBool = AtomicBool::new(false);

const MAIN_WINDOW: &str = "main";

/// ExitRequested 에서 종료를 막아야 하는가(= 트레이 「종료」가 아니다).
pub fn should_prevent_exit() -> bool {
    !QUITTING.load(Ordering::SeqCst)
}

/// 트레이를 세운다. `reopen` 은 닫힌 창을 **새로** 만드는 자리(lib.rs 의 창 빌더를 그대로 부른다).
pub fn build<R>(app: &AppHandle, reopen: R) -> tauri::Result<()>
where
    R: Fn(&AppHandle) + Send + Sync + 'static,
{
    let open = MenuItem::with_id(app, "open", "열기", true, None::<&str>)?;
    let fda = MenuItem::with_id(app, "fda", "전체 디스크 접근 설정 열기", true, None::<&str>)?;
    let sep = PredefinedMenuItem::separator(app)?;
    let quit = MenuItem::with_id(app, "quit", "종료", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &fda, &sep, &quit])?;

    let icon = app.default_window_icon().cloned();
    let mut builder = TrayIconBuilder::with_id("kakao-collector")
        .tooltip("medi-ax")
        .menu(&menu)
        .show_menu_on_left_click(true);
    if let Some(icon) = icon {
        builder = builder.icon(icon);
    }
    builder
        .on_menu_event(move |app, event| match event.id().as_ref() {
            "open" => {
                // 창이 살아 있으면 앞으로, 없으면 **새로 만든다**(파괴됐으므로).
                if let Some(window) = app.get_webview_window(MAIN_WINDOW) {
                    let _ = window.show();
                    let _ = window.set_focus();
                } else {
                    reopen(app);
                }
            }
            "fda" => open_full_disk_access_settings(),
            "quit" => {
                QUITTING.store(true, Ordering::SeqCst);
                app.exit(0);
            }
            _ => {}
        })
        .build(app)?;
    Ok(())
}

/// 시스템 설정의 **전체 디스크 접근** 창을 연다(N-11). `x-apple.systempreferences:` 는 http(s) 가
/// 아니라 `open_external`(`guard.rs`)로 못 열어, 여기서 `/usr/bin/open` 으로 직접 연다.
pub fn open_full_disk_access_settings() {
    #[cfg(target_os = "macos")]
    {
        let _ = std::process::Command::new("/usr/bin/open")
            .arg("x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles")
            .spawn();
    }
}
