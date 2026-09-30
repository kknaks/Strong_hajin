/**
 * 셸 **판(flavor)** — 코드 한 벌, 판마다 설정 한 폴더(`src-tauri/flavors/<판>/`).
 *
 * | 판 | 보이는 이름 | identifier | 여는 주소 |
 * |---|---|---|---|
 * | `strong-hajin`(기본) | Strong Hajin | app.stronghajin.desktop | 미정(null) |
 * | `medi-ax` | medi-ax | app.ax.desktop | https://ax.medisolveai.xyz |
 *
 * 판 폴더에는 셋이 있다 — `tauri.conf.json`(이름·identifier 오버레이, `tauri build --config` 로
 * 기본 `tauri.conf.json` 위에 얹는다) · `shell.config.json`(여는 주소, Rust 가 `include_str!`) ·
 * `capabilities/`(커맨드 허용 origin, tauri-build 가 읽는다). 어느 판인지는 build.rs 가
 * 실린 identifier 로 대조한다 — 이 모듈은 스크립트가 **같은 판의 파일**을 읽게 할 뿐이다.
 *
 * 판 이름은 `--flavor <판>` · `SHELL_FLAVOR` · 기본값 순으로 정한다.
 */
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

export const DEFAULT_FLAVOR = "strong-hajin";

export function flavorNames(shell) {
  const root = join(shell, "flavors");
  if (!existsSync(root)) return [];
  return readdirSync(root).filter((name) => statSync(join(root, name)).isDirectory()).sort();
}

/** `--flavor` · `SHELL_FLAVOR` · 기본값. 없는 판이면 `null` 과 이유를 준다. */
export function requestedFlavor(argv = process.argv, env = process.env) {
  const at = argv.indexOf("--flavor");
  const inline = argv.find((value) => value.startsWith("--flavor="));
  return (at >= 0 ? argv[at + 1] : inline?.split("=")[1]) || env.SHELL_FLAVOR || DEFAULT_FLAVOR;
}

/** RFC 7396 JSON merge patch — Tauri 가 `--config` 를 얹는 방식과 같다. */
function mergePatch(target, patch) {
  if (patch === null || typeof patch !== "object" || Array.isArray(patch)) return patch;
  const out = target && typeof target === "object" && !Array.isArray(target) ? { ...target } : {};
  for (const [key, value] of Object.entries(patch)) {
    if (value === null) delete out[key];
    else out[key] = mergePatch(out[key], value);
  }
  return out;
}

/**
 * 판 하나의 경로·설정. 판 폴더가 없으면 throw 한다 — **없는 판을 기본판으로 바꿔 읽지 않는다.**
 */
export function loadFlavor(shell, name) {
  const dir = join(shell, "flavors", name);
  if (!existsSync(dir)) {
    throw new Error(`판이 없다: ${name} (있는 판: ${flavorNames(shell).join(", ") || "없음"})`);
  }
  const overlayPath = join(dir, "tauri.conf.json");
  const base = JSON.parse(readFileSync(join(shell, "tauri.conf.json"), "utf8"));
  const overlay = JSON.parse(readFileSync(overlayPath, "utf8"));
  return {
    name,
    dir,
    /** `tauri build --config` 에 줄 경로(frontend 기준 상대 경로도 함께). */
    overlayPath,
    overlayRelative: `src-tauri/flavors/${name}/tauri.conf.json`,
    overlay,
    /** 기본 설정 위에 판 오버레이를 얹은 것 — 실제로 구워지는 설정. */
    conf: mergePatch(base, overlay),
    shellConfigPath: join(dir, "shell.config.json"),
    shellConfigRelative: `src-tauri/flavors/${name}/shell.config.json`,
    capabilityDir: join(dir, "capabilities"),
    capabilityRelative: `src-tauri/flavors/${name}/capabilities/product-shell.json`,
  };
}
