//! 셸이 여는 주소와 **네비게이션** 허용 목록.
//!
//! **허용 목록이 둘이고 서로 다르다**(SPEC-006 §5).
//! - **커맨드** 허용 origin → `capabilities/*.json` 의 `remote.urls`. 운영 origin **정확히 하나**
//! - **네비게이션** 허용 origin → 여기. 운영 origin + 인증 흐름이 실제로 거치는 주소들
//!
//! 두 목록을 같은 것으로 다루지 않는다. 로그인 때문에 창이 거쳐야 하는 주소가 생기더라도
//! **그 주소에 네이티브 권한을 주지 않는다** — 넓어지는 것은 이쪽 목록뿐이다.
//!
//! 운영 origin 은 **미정이다**(OQ-T02). 값이 비어 있으면 셸은 가짜 주소로 도는 대신
//! 「설정되지 않았습니다」 화면을 연다.

use serde::Deserialize;
use tauri::Url;

/// 설정 파일의 자리 이름 — 화면과 로그에 그대로 보인다.
pub const CONFIG_SLOT: &str = "shell.config.json · operationalOrigin";

#[derive(Debug, Deserialize)]
// JSON 키는 camelCase 다. 이 줄이 없으면 값을 채워도 **조용히 무시되고**
// 셸이 「미설정」 화면을 계속 연다 — 값이 안 먹는 것보다 나쁜 건 이유를 모르는 것이다.
#[serde(rename_all = "camelCase")]
pub struct ShellConfig {
    /// 운영 웹 주소. **미정이면 `null`.** 여기에 fixture 주소를 올리지 않는다.
    #[serde(default)]
    pub operational_origin: Option<String>,
    /// 네비게이션만 허용할 추가 origin. 커맨드 권한과 무관하다.
    #[serde(default)]
    pub navigation_allowlist: Vec<String>,
}

/// 파싱된 설정. 주소가 서 있으면 `Configured`, 아니면 `Missing`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Target {
    Configured {
        url: Url,
        /// 네비게이션이 허용되는 origin 전부(운영 origin 포함).
        navigation_allowlist: Vec<String>,
    },
    Missing,
}

/// 컴파일 시점에 설정을 싣는다 — 실행 파일만 보고도 무엇을 여는지 확인할 수 있다.
const RAW: &str = include_str!("../shell.config.json");

pub fn load() -> Result<Target, String> {
    parse(RAW)
}

pub fn parse(raw: &str) -> Result<Target, String> {
    let parsed: ShellConfig =
        serde_json::from_str(raw).map_err(|error| format!("shell.config.json 을 읽지 못했습니다: {error}"))?;

    let Some(origin) = parsed.operational_origin.as_deref().map(str::trim).filter(|value| !value.is_empty())
    else {
        return Ok(Target::Missing);
    };

    let url = Url::parse(origin)
        .map_err(|error| format!("operationalOrigin 이 URL 이 아닙니다: {error}"))?;
    if !matches!(url.scheme(), "http" | "https") {
        return Err(format!("operationalOrigin 의 스킴이 http(s) 가 아닙니다: {}", url.scheme()));
    }

    // 운영 origin 은 **언제나** 네비게이션 허용 목록에 들어간다. 그 밖은 설정이 더한 것뿐이다.
    let mut allowlist = vec![crate::guard::origin_of(&url)];
    for extra in &parsed.navigation_allowlist {
        let parsed_extra = Url::parse(extra)
            .map_err(|error| format!("navigationAllowlist 항목이 URL 이 아닙니다({extra}): {error}"))?;
        let origin = crate::guard::origin_of(&parsed_extra);
        if !allowlist.contains(&origin) {
            allowlist.push(origin);
        }
    }

    Ok(Target::Configured {
        url,
        navigation_allowlist: allowlist,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 저장소에_실린_설정은_아직_미정이다() {
        // 운영 origin 을 **발명하지 않았다**는 것을 코드로 고정한다(OQ-T02).
        // Phase 8 이 값을 채우면 이 시험은 그때 함께 바뀐다.
        assert_eq!(load().unwrap(), Target::Missing);
    }

    #[test]
    fn 주소가_서면_운영_origin_이_네비게이션_목록에_들어간다() {
        let target = parse(r#"{"operationalOrigin":"https://example.test/app"}"#).unwrap();
        match target {
            Target::Configured { url, navigation_allowlist } => {
                assert_eq!(url.as_str(), "https://example.test/app");
                assert_eq!(navigation_allowlist, vec!["https://example.test".to_string()]);
            }
            Target::Missing => panic!("설정됐어야 한다"),
        }
    }

    #[test]
    fn 네비게이션_목록은_커맨드_권한과_별개로_넓어진다() {
        // 인증이 거치는 주소를 더해도 **커맨드 허용 origin 은 capability 가 따로 정한다.**
        let target = parse(
            r#"{"operationalOrigin":"https://app.test/","navigationAllowlist":["https://idp.test/login"]}"#,
        )
        .unwrap();
        match target {
            Target::Configured { navigation_allowlist, .. } => {
                assert_eq!(
                    navigation_allowlist,
                    vec!["https://app.test".to_string(), "https://idp.test".to_string()]
                );
            }
            Target::Missing => panic!("설정됐어야 한다"),
        }
    }

    #[test]
    fn 같은_origin_을_두_번_적어도_한_번만_남는다() {
        let target = parse(
            r#"{"operationalOrigin":"https://app.test/a","navigationAllowlist":["https://app.test/b"]}"#,
        )
        .unwrap();
        match target {
            Target::Configured { navigation_allowlist, .. } => {
                assert_eq!(navigation_allowlist.len(), 1);
            }
            Target::Missing => panic!("설정됐어야 한다"),
        }
    }

    #[test]
    fn 빈_문자열은_미정과_같게_본다() {
        assert_eq!(parse(r#"{"operationalOrigin":"   "}"#).unwrap(), Target::Missing);
        assert_eq!(parse(r#"{}"#).unwrap(), Target::Missing);
    }

    #[test]
    fn http_s_가_아닌_스킴은_거절한다() {
        assert!(parse(r#"{"operationalOrigin":"file:///etc/passwd"}"#).is_err());
        assert!(parse(r#"{"operationalOrigin":"not a url"}"#).is_err());
    }
}
