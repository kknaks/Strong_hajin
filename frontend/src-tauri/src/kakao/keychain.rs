//! 기기 토큰을 **macOS 키체인**에 둔다(R3-F1 · OQ-906). 창 쿠키가 아니라 키체인이라
//! **창을 파괴해도(닫아도) 수집이 이어진다**. 토큰 원문은 화면·브라우저 저장소·로그에 남기지 않는다.
//!
//! 발급은 세션 있는 웹(`POST /api/device-tokens`)이 하고, 받은 토큰을 `kakao_store_device_token`
//! 커맨드가 여기로 넣는다. 수집기는 여기서 읽어 `Authorization: Bearer` 로만 쓴다.

use keyring::Entry;

const SERVICE: &str = "app.ax.desktop.kakao-collector";
const ACCOUNT: &str = "device-token";

fn entry() -> Result<Entry, String> {
    Entry::new(SERVICE, ACCOUNT).map_err(|e| format!("키체인 항목을 열지 못했습니다: {e}"))
}

/// 토큰을 키체인에 넣는다(기존 값 덮어쓰기). 새 발급이 옛 토큰을 철회하는 것은 서버 몫(D-45) —
/// 여기선 이 Mac 이 쓰는 마지막 토큰 하나만 둔다.
pub fn store(token: &str) -> Result<(), String> {
    let token = token.trim();
    if token.is_empty() {
        return Err("빈 토큰입니다.".into());
    }
    // axdt_ 접두는 서버 계약(application.py DEVICE_TOKEN_PREFIX). 어긋나면 서버가 401 이라 미리 막는다.
    if !token.starts_with("axdt_") {
        return Err("기기 토큰 형식이 아닙니다.".into());
    }
    entry()?
        .set_password(token)
        .map_err(|e| format!("키체인에 넣지 못했습니다: {e}"))
}

/// 저장된 토큰. 없으면 None(= `login_required` 표식).
pub fn load() -> Option<String> {
    entry().ok()?.get_password().ok().filter(|t| !t.is_empty())
}

/// 철회·로그아웃 때 지운다(선택). 실패해도 치명적이지 않다.
#[allow(dead_code)]
pub fn clear() {
    if let Ok(e) = entry() {
        let _ = e.delete_credential();
    }
}
