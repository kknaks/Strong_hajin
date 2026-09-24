// Windows 릴리즈에서 콘솔 창이 하나 더 뜨는 것을 막는다.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    strong_hajin_shell_lib::run();
}
