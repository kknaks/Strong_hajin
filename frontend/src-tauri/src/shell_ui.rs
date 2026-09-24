//! **셸이 그리는 화면 둘.** SPEC-006 이 셸에 허락한 UI 는 U-2 뿐이고, 여기에 하나를 더한다 —
//! 「운영 주소가 아직 설정되지 않았다」는 개발 상태 화면이다(운영 주소는 OQ-T02 로 미정).
//!
//! **이 화면들은 커맨드를 부르지 않는다.** capability 가 `local: false` 라 부를 수도 없다.
//! `다시 시도` 는 IPC 가 아니라 **평범한 링크**로 운영 주소로 되돌아간다 — 그래서 이 화면에
//! 네이티브 권한을 한 톨도 주지 않고도 U-2 의 CTA 계약이 선다.
//!
//! 제품 웹의 코드·스타일을 가져오지 않는다. `frontend/src/**` 와 무관하다.

/// SPEC-006 U-2 — 연결 실패 화면.
///
/// 문구는 SPEC 이 정한 것을 그대로 쓴다. **실패 사유를 지어내지 않는다** — 사유를 모르면
/// `detail` 을 비워 부르고, 그러면 기술 정보 줄이 아예 나오지 않는다.
pub fn connection_error(target: &str, detail: Option<&str>) -> String {
    let technical = match detail {
        Some(reason) if !reason.trim().is_empty() => format!(
            r#"<p class="detail"><span class="label">기술 정보</span>{}</p>"#,
            escape(reason)
        ),
        _ => String::new(),
    };
    page(
        "연결할 수 없습니다",
        &format!(
            r#"
    <h1>연결할 수 없습니다</h1>
    <p class="body">서버에 닿지 못했습니다. 네트워크를 확인한 뒤 다시 시도해 주세요.</p>
    <p class="target">{target}</p>
    {technical}
    <p><a class="cta" href="{href}">다시 시도</a></p>
"#,
            target = escape(target),
            technical = technical,
            // CTA 는 커맨드가 아니라 링크다 — 네비게이션 허용 목록이 이 이동을 받는다.
            href = escape_attr(target),
        ),
    )
}

/// 운영 주소가 아직 정해지지 않았을 때(OQ-T02) 보이는 화면.
///
/// **가짜 주소를 넣어 도는 것처럼 보이게 하지 않는다.** 설정 자리가 비어 있다는 사실을
/// 그대로 보인다.
pub fn not_configured(slot: &str) -> String {
    page(
        "서버 주소가 설정되지 않았습니다",
        &format!(
            r#"
    <h1>서버 주소가 설정되지 않았습니다</h1>
    <p class="body">이 앱이 열 운영 주소가 아직 정해지지 않았습니다.</p>
    <p class="target">설정 자리: {slot}</p>
    <p class="detail"><span class="label">참고</span>운영 주소 확정은 배포 준비 단계의 일입니다. 이 화면은 값이 비어 있을 때만 보입니다.</p>
"#,
            slot = escape(slot)
        ),
    )
}

fn page(title: &str, body: &str) -> String {
    format!(
        r#"<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{title}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
    font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
    background: Canvas; color: CanvasText;
  }}
  main {{ max-width: 30rem; padding: 2rem; text-align: center; }}
  h1 {{ font-size: 1.25rem; margin: 0 0 .75rem; }}
  .body {{ margin: 0 0 1rem; line-height: 1.6; }}
  .target {{
    margin: 0 0 1rem; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: .8125rem; opacity: .75; word-break: break-all;
  }}
  .detail {{ margin: 0 0 1.25rem; font-size: .8125rem; opacity: .75; line-height: 1.6; word-break: break-word; }}
  .label {{ display: block; font-weight: 600; opacity: .8; margin-bottom: .2rem; }}
  .cta {{
    display: inline-block; padding: .55rem 1.1rem; border-radius: .4rem;
    background: Highlight; color: HighlightText; text-decoration: none; font-weight: 600;
  }}
</style>
</head>
<body><main>{body}</main></body>
</html>"#,
        title = escape(title),
        body = body
    )
}

fn escape(raw: &str) -> String {
    raw.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}

fn escape_attr(raw: &str) -> String {
    escape(raw).replace('"', "&quot;")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn u2_는_spec_문구와_주소와_재시도를_담는다() {
        let html = connection_error("https://example.test/", Some("보안 연결에 실패했습니다: x"));
        assert!(html.contains("연결할 수 없습니다"));
        assert!(html.contains("서버에 닿지 못했습니다. 네트워크를 확인한 뒤 다시 시도해 주세요."));
        assert!(html.contains("https://example.test/"));
        assert!(html.contains("다시 시도"));
        // CTA 는 **같은 주소로 되돌아가는 링크**다 — IPC 를 쓰지 않는다.
        assert!(html.contains(r#"href="https://example.test/""#));
    }

    #[test]
    fn 사유를_모르면_기술_정보_줄이_아예_없다() {
        // SPEC U-2: 「실패 사유를 지어내지 않는다. 원인을 모르면 본문 그대로 둔다」
        let html = connection_error("https://example.test/", None);
        assert!(!html.contains("기술 정보"));
        assert!(html.contains("서버에 닿지 못했습니다"));

        let blank = connection_error("https://example.test/", Some("   "));
        assert!(!blank.contains("기술 정보"));
    }

    #[test]
    fn 셸_화면은_커맨드를_부르지_않는다() {
        // 이 화면에 IPC 가 섞여 들어가면 `local:false` 경계의 의미가 사라진다.
        for html in [
            connection_error("https://example.test/", Some("사유")),
            not_configured("shell.config.json · operationalOrigin"),
        ] {
            assert!(!html.contains("__TAURI"));
            assert!(!html.contains("invoke"));
            assert!(!html.contains("<script"));
        }
    }

    #[test]
    fn 주소에_섞인_따옴표와_꺾쇠를_흘리지_않는다() {
        let html = connection_error("https://x.test/\"><script>alert(1)</script>", None);
        assert!(!html.contains("<script>alert(1)"));
        assert!(html.contains("&lt;script&gt;"));
    }

    #[test]
    fn 미설정_화면은_가짜_주소를_지어내지_않는다() {
        let html = not_configured("shell.config.json · operationalOrigin");
        assert!(html.contains("설정되지 않았습니다"));
        assert!(html.contains("shell.config.json"));
        assert!(!html.contains("http://"));
        assert!(!html.contains("https://"));
    }
}
