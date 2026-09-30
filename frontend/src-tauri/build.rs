// 앱 커맨드 넷의 ACL 권한(`allow-shell-info` 등)을 자동 생성한다.
//
// **왜 필요한가** — tauri 2.11 `src/webview/mod.rs` 의 invoke 경로는
// `plugin_command.is_some() || has_app_acl_manifest || !is_local` 일 때 ACL 을 강제한다.
// 원격 https 문서는 `is_local == false` 라 **앱 커맨드도 capability 에 명시되어야 부를 수 있다.**
// 커맨드 이름의 `_` 는 `-` 로 바뀌어 `allow-wake-guard-acquire` 같은 식별자가 된다.
//
// ## 판(flavor) — 코드 한 벌, 판마다 설정 한 폴더
//
// `flavors/<판>/` 에 그 판의 `tauri.conf.json`(이름·identifier 오버레이) · `shell.config.json`
// (여는 주소) · `capabilities/`(커맨드 허용 origin) 이 함께 있다. 어느 판으로 굽는지는
// **실제로 쓰이는 identifier** 가 정한다 — 이 스크립트가 그 셋이 한 판의 것인지 대조한다.
//
// - `tauri build --config flavors/<판>/tauri.conf.json` — CLI 가 `TAURI_CONFIG` 에 오버레이를 싣는다.
//   그 identifier 로 판을 찾는다. `SHELL_FLAVOR` 가 함께 있으면 둘이 같아야 한다.
// - `cargo check` / `cargo test` — `TAURI_CONFIG` 가 없다. `SHELL_FLAVOR`(없으면 기본판)의
//   오버레이를 이 스크립트가 `TAURI_CONFIG` 로 싣는다(tauri-build · `generate_context!` 둘 다 읽는다).
//
// 「이름·identifier 는 A 판인데 주소·권한은 B 판」인 실행 파일은 **여기서 빌드를 멈춘다.**
use std::{collections::BTreeMap, env, fs, path::Path};

/// `SHELL_FLAVOR` 도 `--config` 도 없을 때의 판. 주소가 미정이라 «설정되지 않았습니다» 화면을 연다.
const DEFAULT_FLAVOR: &str = "strong-hajin";

fn read_json(path: &Path) -> serde_json::Value {
    let raw = fs::read_to_string(path)
        .unwrap_or_else(|error| panic!("{} 를 읽지 못했다: {error}", path.display()));
    serde_json::from_str(&raw)
        .unwrap_or_else(|error| panic!("{} 가 JSON 이 아니다: {error}", path.display()))
}

/// 판 이름 → 그 판의 오버레이(`flavors/<판>/tauri.conf.json`).
fn flavors() -> BTreeMap<String, serde_json::Value> {
    let mut found = BTreeMap::new();
    for entry in fs::read_dir("flavors").expect("flavors/ 가 없다") {
        let path = entry.expect("flavors/ 항목").path();
        if path.is_dir() {
            let name = path.file_name().unwrap().to_string_lossy().to_string();
            found.insert(name, read_json(&path.join("tauri.conf.json")));
        }
    }
    found
}

fn resolve_flavor() -> String {
    let flavors = flavors();
    let requested = env::var("SHELL_FLAVOR")
        .ok()
        .filter(|value| !value.is_empty());
    if let Some(name) = &requested {
        assert!(
            flavors.contains_key(name),
            "SHELL_FLAVOR={name} — 그런 판이 없다: {:?}",
            flavors.keys()
        );
    }

    match env::var("TAURI_CONFIG") {
        // CLI 가 `--config` 로 오버레이를 실었다 — identifier 로 판을 찾는다.
        Ok(raw) => {
            let patch: serde_json::Value =
                serde_json::from_str(&raw).expect("TAURI_CONFIG 가 JSON 이 아니다");
            let base = read_json(Path::new("tauri.conf.json"));
            let identifier = patch["identifier"]
                .as_str()
                .or(base["identifier"].as_str())
                .expect("identifier");
            let (name, overlay) = flavors
                .iter()
                .find(|(_, overlay)| overlay["identifier"] == identifier)
                .unwrap_or_else(|| panic!("identifier {identifier} 에 맞는 판이 flavors/ 에 없다"));
            if let Some(requested) = &requested {
                assert_eq!(requested, name, "SHELL_FLAVOR 와 --config 의 판이 갈렸다");
            }
            // 오버레이의 값(이름·실행 파일 이름)이 전부 그대로 실렸는가.
            for (key, value) in overlay.as_object().unwrap() {
                if key != "$schema" {
                    let effective = patch.get(key).or(base.get(key));
                    assert_eq!(
                        effective,
                        Some(value),
                        "판 {name} 의 {key} 가 실린 설정과 다르다"
                    );
                }
            }
            name.clone()
        }
        Err(_) => {
            let name = requested.unwrap_or_else(|| DEFAULT_FLAVOR.to_string());
            // tauri CLI 가 부른 빌드(CLI 는 cargo 에 `TAURI_CLI_VERBOSITY` 를 준다)인데 `--config` 가
            // 없으면 번들(Info.plist·dmg)은 기본판 이름으로 굽히고 실행 파일만 다른 판이 된다 — 막는다.
            if name != DEFAULT_FLAVOR && env::var_os("TAURI_CLI_VERBOSITY").is_some() {
                panic!(
                    "SHELL_FLAVOR={name} 를 tauri CLI 로 굽는데 --config 가 없다 — \
                     `make shell-build SHELL_FLAVOR={name}` 또는 \
                     `tauri build --config src-tauri/flavors/{name}/tauri.conf.json` 로 부른다"
                );
            }
            let overlay = serde_json::to_string(&flavors[&name]).unwrap();
            // tauri-build 는 이 프로세스의 환경을, `generate_context!` 는 rustc 의 환경을 읽는다.
            env::set_var("TAURI_CONFIG", &overlay);
            println!("cargo:rustc-env=TAURI_CONFIG={overlay}");
            name
        }
    }
}

fn main() {
    println!("cargo:rerun-if-env-changed=SHELL_FLAVOR");
    println!("cargo:rerun-if-env-changed=TAURI_CLI_VERBOSITY");
    println!("cargo:rerun-if-changed=flavors");
    let flavor = resolve_flavor();
    for file in [
        "tauri.conf.json",
        "shell.config.json",
        "capabilities/product-shell.json",
    ] {
        println!("cargo:rerun-if-changed=flavors/{flavor}/{file}");
    }
    // 소스가 `include_str!(concat!("../flavors/", env!("SHELL_FLAVOR"), ...))` 로 그 판의 파일을 싣는다.
    println!("cargo:rustc-env=SHELL_FLAVOR={flavor}");

    let capabilities: &'static str =
        Box::leak(format!("./flavors/{flavor}/capabilities/**/*").into_boxed_str());
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .capabilities_path_pattern(capabilities)
            .app_manifest(tauri_build::AppManifest::new().commands(&[
                "shell_info",
                "wake_guard_acquire",
                "wake_guard_release",
                "open_external",
            ])),
    )
    .expect("tauri-build 실패 — tauri.conf.json 과 flavors/<판>/ 을 확인한다");
}
