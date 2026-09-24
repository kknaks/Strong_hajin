// 앱 커맨드 넷의 ACL 권한(`allow-shell-info` 등)을 자동 생성한다.
//
// **왜 필요한가** — tauri 2.11 `src/webview/mod.rs` 의 invoke 경로는
// `plugin_command.is_some() || has_app_acl_manifest || !is_local` 일 때 ACL 을 강제한다.
// 원격 https 문서는 `is_local == false` 라 **앱 커맨드도 capability 에 명시되어야 부를 수 있다.**
// 커맨드 이름의 `_` 는 `-` 로 바뀌어 `allow-wake-guard-acquire` 같은 식별자가 된다.
fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(tauri_build::AppManifest::new().commands(&[
            "shell_info",
            "wake_guard_acquire",
            "wake_guard_release",
            "open_external",
        ])),
    )
    .expect("tauri-build 실패 — tauri.conf.json 과 capabilities/ 를 확인한다");
}
