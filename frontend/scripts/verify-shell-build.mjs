#!/usr/bin/env node
/**
 * 데스크톱 셸의 **빌드 구성 정적 검증** (WORK-006 Phase 7).
 *
 * 설치파일을 굽지 않고, 기기에 아무것도 설치하지 않는다. 읽고 대조만 한다 —
 * 그래서 **언제 몇 번을 돌려도 안전**하다.
 *
 * 재는 것 다섯
 *  1. **판 번호가 한 곳에서 나온다** — `Cargo.toml` → `tauri.conf.json`(version 미기재) →
 *     `shell_info.app_version`(`CARGO_PKG_VERSION`). 셋이 갈라지면 설치파일이 말하는 판과
 *     셸이 웹에게 말하는 판이 달라진다(SPEC-006 §5 「판 번호·여는 주소·shell_api 를 기록으로 남긴다」)
 *  2. **양 플랫폼 번들 타깃**이 살아 있다 — macOS(app·dmg) · Windows(msi·nsis)
 *  3. **아이콘이 실재하고 형식이 맞다** — 목록에 있으나 없는 파일이면 번들이 거기서 멈춘다
 *  4. **원격 문서 계약이 보존된다** — `frontendDist: shell-noop` · 창은 Rust 가 만든다
 *  5. **권한 경계가 그대로다** — capability `local:false` · `remote.urls` 하나 · 운영 origin 미발명
 *
 * ⚠ **이 스크립트는 「이 호스트에서 무엇을 «구울 수» 있는가」를 구분해 적는다.**
 * macOS 에서 돌렸다고 Windows 설치파일이 검증된 것이 아니다 — 그 칸은 «검증 불가»로 남는다.
 *
 * ## 「문제 0건」은 「빌드 가능」이 아니다 — 그리고 «못 재는 것»에도 두 종류가 있다
 *
 * | 종류 | 무엇 | 고칠 수 있나 | strict 에서 |
 * |---|---|---|---|
 * | **구성 미비**(`unverifiable`) | 설정·파일이 모자라 **실제로 빌드를 막을 수 있는 것**(예: `.icns` 부재) | **고칠 수 있다** — 채우면 사라진다 | **실패로 승격** |
 * | **호스트 한계**(`hostNotes`) | 이 기기가 **다른 플랫폼을 못 굽는다**는 사실 | 고칠 수 없다 — 기기의 성질이다 | **정보로 남긴다** |
 *
 * 둘을 섞으면 strict 가 **영원히 붉은** 채로 남아 경보로서 죽는다. macOS 에서 `.icns` 를 채우면
 * **strict 가 실제로 초록이 되어야** 그 모드가 의미를 갖는다 — 그래서 호스트 한계는 승격하지 않는다.
 * **플랫폼 대체 금지는 그대로다**: 이 기기에서 못 구운 쪽을 «검증됐다»고 쓰지 않는다.
 *
 * 사용:
 *   node scripts/verify-shell-build.mjs                # 검증(검증 불가는 경고로 센다)
 *   node scripts/verify-shell-build.mjs --strict       # 검증 불가를 **실패로 승격**
 *   SHELL_STRICT=1 node scripts/verify-shell-build.mjs # 위와 같다
 *   node scripts/verify-shell-build.mjs --tag v0.0.1   # 코드 태그까지 같은 판인지 대조
 */
import { readFileSync, existsSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const shell = resolve(here, "..", "src-tauri");

const problems = [];
const notes = [];
/** **구성 미비** — 채우면 사라지는 것. strict 가 실패로 올린다. */
const unverifiable = [];
/** **호스트 한계** — 이 기기의 성질이라 고칠 수 없는 것. 어느 모드에서도 정보다. */
const hostNotes = [];

/** strict: **구성 미비만** 실패로 올린다. 관측을 바꾸지 않고 **판정만** 바꾼다. */
const strict = process.argv.includes("--strict") || process.env.SHELL_STRICT === "1";

const fail = (what) => problems.push(what);
const note = (what) => notes.push(what);

function readJson(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}

/** `Cargo.toml` 의 `[package] version`. 간단한 TOML 이라 한 줄만 본다. */
function cargoVersion() {
  const raw = readFileSync(join(shell, "Cargo.toml"), "utf8");
  const pkg = raw.split(/^\[/m).find((block) => block.startsWith("package]"));
  const found = pkg?.match(/^version\s*=\s*"([^"]+)"/m);
  return found?.[1] ?? null;
}

// ── 1. 판 번호가 한 곳에서 나오는가 ─────────────────────────────────────────
const conf = readJson(join(shell, "tauri.conf.json"));
const cargo = cargoVersion();

if (!cargo) fail("Cargo.toml 의 [package] version 을 읽지 못했다");
if (!/^\d+\.\d+\.\d+/.test(cargo ?? "")) fail(`판 번호가 semver 가 아니다: ${cargo}`);

if (Object.hasOwn(conf, "version")) {
  // 값이 있어도 «같으면» 통과시키되, 두 곳을 손으로 맞추는 구조라는 사실은 적는다.
  if (conf.version !== cargo) {
    fail(
      `판 번호가 갈렸다 — tauri.conf.json=${conf.version} · Cargo.toml=${cargo}. ` +
        `설치파일이 말하는 판과 shell_info.app_version 이 달라진다`,
    );
  } else {
    note(`tauri.conf.json 에 version 이 «있고» Cargo.toml 과 같다(${cargo}) — 두 곳을 손으로 맞추는 구조다`);
  }
} else {
  note(`tauri.conf.json 에 version 이 없다 → Tauri 가 Cargo.toml 을 쓴다. 단일 출처 = ${cargo}`);
}

// shell_info.app_version 은 CARGO_PKG_VERSION 이다 — 소스에서 그 사실을 확인한다.
const libSource = readFileSync(join(shell, "src", "lib.rs"), "utf8");
if (!libSource.includes('env!("CARGO_PKG_VERSION")')) {
  fail("shell_info 가 CARGO_PKG_VERSION 을 쓰지 않는다 — 판 번호 출처가 갈라졌다");
} else {
  note(`shell_info.app_version = CARGO_PKG_VERSION = ${cargo}`);
}

// 코드 태그까지 대조할 때만(선택). 태그를 «만들지» 않는다 — 주어진 값과 비교만 한다.
const tagArgument = process.argv.find((argument) => argument.startsWith("--tag"));
if (tagArgument) {
  const tag = (tagArgument.includes("=") ? tagArgument.split("=")[1] : process.argv[process.argv.indexOf(tagArgument) + 1]) ?? "";
  const stripped = tag.replace(/^v/, "");
  if (!stripped) fail("--tag 에 값이 없다");
  else if (stripped !== cargo) fail(`코드 태그와 판 번호가 다르다 — tag=${tag} · version=${cargo}`);
  else note(`코드 태그 ${tag} 가 판 번호 ${cargo} 와 같다`);
} else {
  note("코드 태그 대조는 생략됐다(--tag 를 주면 함께 잰다)");
}

// ── 2. 양 플랫폼 번들 타깃 ──────────────────────────────────────────────────
const targets = conf.bundle?.targets;
const MAC = ["app", "dmg"];
const WIN = ["msi", "nsis"];
if (targets === "all") {
  note('bundle.targets = "all" — macOS(app·dmg) · Windows(msi·nsis) 를 모두 포함한다');
} else if (Array.isArray(targets)) {
  const missingMac = MAC.filter((t) => !targets.includes(t));
  const missingWin = WIN.filter((t) => !targets.includes(t));
  if (missingMac.length) fail(`macOS 번들 타깃이 빠졌다: ${missingMac.join(", ")}`);
  if (missingWin.length) fail(`Windows 번들 타깃이 빠졌다: ${missingWin.join(", ")}`);
} else {
  fail(`bundle.targets 를 읽지 못했다: ${JSON.stringify(targets)}`);
}
if (conf.bundle?.active !== true) fail("bundle.active 가 true 가 아니다 — 설치파일이 만들어지지 않는다");

// macOS 전용 설정이 붙어 있는가 (마이크 권한·entitlements)
for (const [key, file] of [["infoPlist", "Info.plist"], ["entitlements", "Entitlements.plist"]]) {
  const value = conf.bundle?.macOS?.[key];
  if (value !== file) fail(`bundle.macOS.${key} 가 ${file} 이 아니다: ${value}`);
  else if (!existsSync(join(shell, file))) fail(`${file} 이 없다`);
}

// ── 3. 아이콘 ───────────────────────────────────────────────────────────────
// 확장자별 매직바이트. `.icns` 는 "icns" 로 시작한다 — 이 표에 없으면
// «있는 파일을 형식 오류»로 잡아 strict 가 영영 초록이 되지 못한다.
const MAGIC = {
  ".png": Buffer.from([0x89, 0x50, 0x4e, 0x47]),
  ".ico": Buffer.from([0x00, 0x00, 0x01, 0x00]),
  ".icns": Buffer.from("icns", "ascii"),
};
for (const relative of conf.bundle?.icon ?? []) {
  const path = join(shell, relative);
  if (!existsSync(path)) {
    fail(`아이콘이 목록에 있으나 파일이 없다: ${relative}`);
    continue;
  }
  const extension = Object.keys(MAGIC).find((suffix) => relative.toLowerCase().endsWith(suffix));
  if (!extension) {
    fail(`아이콘 확장자를 모른다(png·ico·icns 만 안다): ${relative}`);
    continue;
  }
  const head = readFileSync(path).subarray(0, MAGIC[extension].length);
  if (!head.equals(MAGIC[extension])) fail(`아이콘 형식이 맞지 않는다: ${relative}`);
}
if (!(conf.bundle?.icon ?? []).some((i) => i.endsWith(".ico"))) {
  fail("Windows 용 .ico 아이콘이 목록에 없다 — Windows 번들이 막힌다");
}
// macOS 배포본은 .icns 를 쓴다. 지금 없다는 사실을 «숨기지 않고» 적는다.
if (!(conf.bundle?.icon ?? []).some((i) => i.endsWith(".icns"))) {
  unverifiable.push(
    "macOS `.icns` 아이콘이 목록에 없다 — `tauri build` 의 macOS 번들 단계에서 필요할 수 있다. " +
      "제품 아이콘 자체가 아직 자리표시라(Phase 7 범위 밖) 여기서 만들지 않는다",
  );
}

// ── 4. 원격 문서 계약 ───────────────────────────────────────────────────────
if (conf.build?.frontendDist !== "shell-noop") {
  fail(`build.frontendDist 가 shell-noop 이 아니다: ${conf.build?.frontendDist} — 원격 문서 계약이 깨진다`);
} else if (!existsSync(join(shell, "shell-noop"))) {
  fail("shell-noop 디렉터리가 없다 — tauri-build 가 멈춘다");
}
if ((conf.app?.windows ?? []).length !== 0) {
  fail("app.windows 가 비어 있지 않다 — 창은 Rust 가 하나만 만든다(AC-T33)");
}
if (conf.app?.withGlobalTauri !== false) fail("app.withGlobalTauri 가 false 가 아니다");
if (conf.app?.security?.csp) fail("셸이 CSP 를 박고 있다 — 원격 문서의 CSP 는 서버 응답 헤더가 정한다");
if (conf.identifier === "com.kknaks.task-management") fail("참조 제품과 식별자가 겹친다(AC-T34)");

// ── 5. 권한 경계 ────────────────────────────────────────────────────────────
const capabilityDir = join(shell, "capabilities");
const capabilities = readdirSync(capabilityDir).filter((name) => name.endsWith(".json"));
if (capabilities.length !== 1) fail(`capability 파일이 하나가 아니다: ${capabilities.join(", ")}`);
for (const name of capabilities) {
  const capability = readJson(join(capabilityDir, name));
  if (capability.local !== false) fail(`${name}: local 이 false 가 아니다`);
  const urls = capability.remote?.urls ?? [];
  if (urls.length !== 1) fail(`${name}: remote.urls 가 하나가 아니다 (${urls.length})`);
  if (urls.some((url) => url.includes("://*."))) fail(`${name}: 와일드카드 서브도메인을 쓰고 있다`);
  if ((capability.permissions ?? []).length !== 4) {
    fail(`${name}: 권한이 넷이 아니다 (${(capability.permissions ?? []).length})`);
  }
  if (urls.some((url) => url.includes(".invalid"))) {
    notes.push(`${name}: 운영 origin 이 아직 자리표시다(${urls[0]}) — fixture/운영 판을 구우려면 먼저 채워야 한다`);
  }
}

// ── 이 호스트에서 «구울 수» 있는 것 ─────────────────────────────────────────
const host = process.platform;
const buildable = host === "darwin" ? "macOS(app·dmg)" : host === "win32" ? "Windows(msi·nsis)" : "없음";
const blocked = host === "darwin" ? "Windows(msi·nsis)" : host === "win32" ? "macOS(app·dmg)" : "macOS·Windows 둘 다";
hostNotes.push(
  `이 호스트(${host})에서 구울 수 있는 것: ${buildable}. ` +
    `${blocked} 는 **이 기기에서 검증 불가** — 다른 쪽 결과로 대체하지 않는다`,
);

// ── 결과 ────────────────────────────────────────────────────────────────────
console.log(`== 셸 빌드 구성 검증 ==${strict ? "  [strict]" : ""}`);
for (const line of notes) console.log(`  · ${line}`);

console.log("");
console.log(`-- 구성 미비(채우면 사라진다): ${unverifiable.length}건 --`);
if (!unverifiable.length) console.log("  (없음)");
for (const line of unverifiable) console.log(`  ${strict ? "✗" : "⚠"} ${line}`);

console.log("");
console.log(`-- 호스트 한계(이 기기의 성질 · 어느 모드에서도 정보): ${hostNotes.length}건 --`);
for (const line of hostNotes) console.log(`  ℹ ${line}`);

if (problems.length) {
  console.log("");
  console.log(`-- 문제: ${problems.length}건 --`);
  for (const line of problems) console.log(`  ✗ ${line}`);
}

const failed = problems.length + (strict ? unverifiable.length : 0);
console.log("");
console.log(`문제 ${problems.length}건 · 구성 미비 ${unverifiable.length}건 · 호스트 한계 ${hostNotes.length}건`);

if (failed) {
  if (strict && !problems.length) {
    console.log("strict 모드 — **구성 미비**를 실패로 올렸다(호스트 한계는 올리지 않는다). 기본 모드에서는 경고다.");
  }
  console.log(`실패 ${failed}건`);
  process.exit(1);
}

if (strict) {
  console.log("strict 통과 — 구성 미비 0건이다.");
  if (hostNotes.length) {
    console.log(
      "ℹ 호스트 한계는 남아 있다(위 ℹ 줄). **이 기기에서 못 구운 플랫폼을 «검증됐다»고 쓰지 않는다** — " +
        "strict 통과는 «구성이 갖춰졌다»는 뜻이지 «모든 플랫폼이 구워진다»는 뜻이 아니다.",
    );
  }
} else {
  console.log(
    `⚠ **「문제 0건」은 「빌드 가능」이 아니다.** 구성 미비 ${unverifiable.length}건은 채우면 사라지는 것이고, ` +
      `그중 하나라도 실제 번들을 막을 수 있다. 굽기 전에 확인하려면 --strict 로 돌린다.`,
  );
}
console.log("(설치파일을 굽지 않았고, 아무것도 설치하지 않았다)");
