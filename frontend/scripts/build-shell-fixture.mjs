#!/usr/bin/env node
/**
 * fixture origin 판을 굽기 위한 **점검 + 명령 안내** (WORK-006 Phase 7).
 *
 * **기본은 dry-run 이다.** `--run` 을 주지 않으면 아무것도 굽지 않고, 어떤 경우에도
 * **기기에 설치하지 않는다.** Release·서명·공증·자동업데이트는 이 스크립트의 일이 아니다.
 *
 * ## 왜 «굽기» 전에 점검하나
 * 셸이 여는 주소는 **두 곳**이 함께 정한다 —
 *   ① `src-tauri/shell.config.json` 의 `operationalOrigin` (창이 여는 곳)
 *   ② `src-tauri/capabilities/product-shell.json` 의 `remote.urls` (커맨드를 쓸 수 있는 origin)
 * 둘이 어긋나면 **창은 뜨는데 커맨드가 ACL 에서 거절된다**(`E-06`). 구운 뒤에야 알게 되는
 * 종류의 실수라, 굽기 «전에» 같은지 본다.
 *
 * ## 이 스크립트는 그 두 파일을 **고치지 않는다**
 * 주소를 정하는 것은 사람의 일이다(OQ-T02). 어긋나 있으면 **무엇을 어디에 써야 하는지**
 * 알려 주고 멈춘다 — 운영 주소를 지어내지 않는다.
 *
 * ## M-10(재설치·판 올림 쿠키 유지)을 위한 «두 판»
 * 판 번호의 단일 출처는 `src-tauri/Cargo.toml` 의 `[package] version` 이다
 * (`tauri.conf.json` 에 version 이 없어 Tauri 가 그 값을 쓰고, `shell_info.app_version` 도 같다).
 * 그래서 둘째 판은 **그 한 줄을 올리고 다시 굽는다.** 이 스크립트가 그 줄을 대신 고치지 않는다 —
 * 판 번호를 올리는 것은 기록에 남아야 하는 결정이기 때문이다.
 *
 * 사용:
 *   node scripts/build-shell-fixture.mjs --origin https://<fixture-host>      # 점검 + 명령 출력
 *   node scripts/build-shell-fixture.mjs --origin https://<fixture-host> --run # 실제 빌드(호스트 플랫폼만)
 */
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const frontend = resolve(here, "..");
const shell = join(frontend, "src-tauri");

function argument(name) {
  const at = process.argv.indexOf(name);
  if (at >= 0 && process.argv[at + 1] && !process.argv[at + 1].startsWith("--")) return process.argv[at + 1];
  const inline = process.argv.find((value) => value.startsWith(`${name}=`));
  return inline ? inline.split("=").slice(1).join("=") : null;
}

const origin = argument("--origin");
const run = process.argv.includes("--run");

if (!origin) {
  console.error("사용: node scripts/build-shell-fixture.mjs --origin https://<fixture-host> [--run]");
  console.error("  실제 fixture 주소는 이 스크립트가 정하지 않는다 — 부르는 쪽이 준다.");
  process.exit(2);
}

let parsed;
try {
  parsed = new URL(origin);
} catch {
  console.error(`--origin 이 URL 이 아니다: ${origin}`);
  process.exit(2);
}
if (parsed.protocol !== "https:") {
  // 마이크는 secure context 에서만 열린다. fixture 라도 https 여야 같은 조건이 된다.
  console.error(`--origin 은 https 여야 한다(마이크·쿠키 조건): ${origin}`);
  process.exit(2);
}
const wanted = parsed.origin;

// ── 0. 구성 정적 검증을 먼저 통과해야 한다 ─────────────────────────────────
try {
  execFileSync(process.execPath, [join(here, "verify-shell-build.mjs")], { stdio: "inherit" });
} catch {
  console.error("\n구성 검증이 실패했다 — 굽기 전에 위 문제를 먼저 닫는다.");
  process.exit(1);
}

// ── 1. 주소를 정하는 «두 곳»이 서로, 그리고 요청과 같은가 ──────────────────
const config = JSON.parse(readFileSync(join(shell, "shell.config.json"), "utf8"));
const capability = JSON.parse(readFileSync(join(shell, "capabilities", "product-shell.json"), "utf8"));

const configured = config.operationalOrigin ? new URL(config.operationalOrigin).origin : null;
const allowed = (capability.remote?.urls ?? [])[0] ?? "";
const allowedOrigin = allowed.replace(/\/\*$/, "").replace(/\/$/, "");

const mismatched = [];
if (configured !== wanted) mismatched.push(`shell.config.json · operationalOrigin = ${config.operationalOrigin ?? "null"}`);
if (allowedOrigin !== wanted) mismatched.push(`capabilities/product-shell.json · remote.urls[0] = ${allowed}`);

if (mismatched.length) {
  console.error("\n== 주소가 아직 이 fixture 판의 것이 아니다 ==");
  for (const line of mismatched) console.error(`  ✗ ${line}`);
  console.error(`\n요청한 origin: ${wanted}`);
  console.error("이 스크립트는 두 파일을 «고치지 않는다». 사람이 아래 둘을 같은 값으로 맞춘 뒤 다시 부른다:");
  console.error(`  1) src-tauri/shell.config.json          "operationalOrigin": "${wanted}"`);
  console.error(`  2) src-tauri/capabilities/product-shell.json  "remote.urls": ["${wanted}/*"]`);
  console.error("\n⚠ 둘이 어긋난 채로 구우면 창은 뜨는데 커맨드가 ACL 에서 거절된다(E-06).");
  process.exit(1);
}

// ── 2. 이 호스트가 구울 수 있는 것만 말한다 ────────────────────────────────
const host = process.platform;
const producible =
  host === "darwin" ? "macOS: .app · .dmg" : host === "win32" ? "Windows: .msi · .exe(NSIS)" : "(지원 대상 밖)";
const other = host === "darwin" ? "Windows(.msi/.exe)" : host === "win32" ? "macOS(.app/.dmg)" : "macOS·Windows";

const version = (readFileSync(join(shell, "Cargo.toml"), "utf8").match(/^version\s*=\s*"([^"]+)"/m) ?? [])[1];

console.log("\n== fixture 판 ==");
console.log(`  판 번호   : ${version}   (출처: src-tauri/Cargo.toml → tauri.conf · shell_info 공통)`);
console.log(`  여는 주소 : ${wanted}`);
console.log(`  구울 것   : ${producible}`);
console.log(`  ⚠ ${other} 는 이 호스트에서 굽지 못한다 — «검증 불가»로 남기고 다른 쪽 결과로 대체하지 않는다`);
console.log("\n  M-10(재설치·판 올림 쿠키 유지)은 판이 «둘» 필요하다:");
console.log(`    1판 = ${version} 를 굽고 설치 → 로그인 → 2판을 위해 Cargo.toml 의 version 한 줄을 올린다`);
console.log("    2판 = 다시 굽고 «덮어 설치» → 쿠키가 남아 로그인이 유지되는지 본다(AC-T35)");
console.log("    ⚠ 식별자(app.stronghajin.desktop)는 판을 넘어 **고정**한다 — 바꾸면 저장소가 새로 잡혀 로그아웃된다");

const command = ["cargo", "tauri", "build"];
if (!run) {
  console.log("\n== dry-run (아무것도 굽지 않았다) ==");
  console.log(`  실제로 구우려면: cd frontend/src-tauri && ${command.join(" ")}`);
  console.log("  또는 이 스크립트에 --run 을 준다.");
  console.log("  설치는 사람이 한다 — 이 스크립트는 기기에 아무것도 설치하지 않는다.");
  process.exit(0);
}

console.log(`\n== 빌드 실행: ${command.join(" ")} ==`);
try {
  execFileSync(command[0], command.slice(1), { cwd: shell, stdio: "inherit" });
} catch (error) {
  console.error(`\n빌드 실패: ${error.message}`);
  process.exit(1);
}
console.log("\n빌드 끝. **설치는 하지 않았다** — 산출물 경로는 위 tauri 출력이 적는다.");
