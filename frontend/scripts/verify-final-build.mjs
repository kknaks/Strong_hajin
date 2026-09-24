#!/usr/bin/env node
/**
 * 운영 origin **최종 빌드 관문**(WORK-006 Phase 8).
 *
 * 이 스크립트는 **굽지 않는다. 설치하지 않는다. 설정 파일을 쓰지 않는다.** 읽고 대조하고 막는다.
 * 하는 일은 하나다 — **「지금 최종판을 구워도 되는가」에 아니오라고 말할 수 있는 근거를 모은다.**
 *
 * ## 왜 관문이 따로 필요한가
 *
 * `verify-shell-build.mjs`(Phase 7)는 **구성이 일관된가**를 본다. 운영 origin 이 자리표시여도
 * 일관되기만 하면 초록이다 — 그것이 그 도구의 올바른 범위다. 하지만 **최종판을 굽는 순간**에는
 * 질문이 달라진다: **「이 자리표시가 그대로 구워지고 있지 않은가」.**
 * 그 질문에 답하는 것이 이 파일이다.
 *
 * ## 관문 여섯
 *
 * | # | 재는 것 | 막는 것 |
 * |---|---|---|
 * | G1 | 입력 origin 의 **형식** | `.invalid` · 와일드카드 · 경로 포함 · http · 빈 값 |
 * | G2 | `shell.config.json` 의 **operationalOrigin 실재** | `null`(미정) 인 채로 굽는 것 |
 * | G3 | capability `remote.urls` 가 **`<origin>/*` 하나** | 자리표시 · 와일드카드 서브도메인 · 목록 팽창 |
 * | G4 | 셋(입력·config·capability)이 **정확히 같다** | 「설정은 A 인데 권한은 B」인 판 |
 * | G5 | **미결 넷**(D-4 · 운영 서버 실재 · M-1 · M-5) | 이것들을 **침묵으로 통과시키는 것** |
 * | G6 | **manifest** 를 찍는다 | 없는 아티팩트의 해시를 **지어내는 것** |
 *
 * ## G5 에 대해 — 이 스크립트가 **잴 수 없는 것**
 *
 * D-4(PRODUCTION 로그인 수단)·운영 서버가 실제로 떠 있는가·M-1(https 신뢰)·
 * M-5(앱 재시작 후 로그인 쿠키 유지)는
 * **정적 검사로 잴 수 없다.** 그래서 여기서는 두 가지 중 하나만 한다:
 *
 * - 증거가 **없으면 막는다**(기본값). **없음을 통과로 바꾸지 않는다.**
 * - 증거 파일이 **있으면 «확인됨»이 아니라 «기록된 주장(attested)»으로** manifest 에 싣고,
 *   **누가 무엇을 근거로 그렇게 말했는지**를 함께 남긴다.
 *
 * **이 스크립트가 초록이어도 「운영 서버가 실재한다」는 뜻이 아니다** — 「그 주장이 기록되었다」는 뜻이다.
 * 그 구분을 출력에서 지우지 않는다.
 *
 * 사용:
 *   node scripts/verify-final-build.mjs                                  # 현재 상태 점검(대개 막힌다)
 *   SHELL_OPERATING_ORIGIN=https://app.example.com node scripts/verify-final-build.mjs
 *   node scripts/verify-final-build.mjs --origin https://app.example.com --evidence final-evidence.json
 *
 * 종료 코드: 0 = 통과 · 1 = 관문이 막았다 · 2 = 사용법 오류
 */
import { readFileSync, existsSync, readdirSync, statSync } from "node:fs";
import { createHash } from "node:crypto";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

function argument(flag) {
  const at = process.argv.indexOf(flag);
  return at === -1 ? null : process.argv[at + 1];
}

const here = dirname(fileURLToPath(import.meta.url));
const defaultShell = resolve(here, "..", "src-tauri");

/**
 * **읽을 설정 트리.** 기본은 진짜 `src-tauri` 다.
 *
 * `--shell-root` 는 **연습용**이다 — 운영 origin 이 정해지기 전에 「값이 들어오면 관문이 실제로
 * 초록이 되는가」를 **진짜 설정을 건드리지 않고** 확인하려고 둔다. 이 스크립트는 어느 경로에도
 * **쓰지 않는다**(읽기 전용).
 *
 * ⚠ **연습은 0 으로 끝나지 않는다 — 절대.** 사본을 가리켜 초록을 만들 수 있으므로,
 * 모든 관문을 지나도 **exit 3**(`EXIT_REHEARSAL`) 이다. 사람이 읽는 배너·manifest 만으로는
 * 부족하다: **CI 는 글자를 읽지 않고 종료 코드만 본다.** 「연습이지만 0」이면 자동화가
 * 그것을 관문 통과로 집계한다 — 그 구멍을 종료 코드에서 막는다.
 * **exit 0 은 오직 진짜 `src-tauri` 에서만** 나온다.
 */
/** 종료 코드. **0 은 진짜 관문 통과 하나뿐이다.** */
const EXIT_PASS = 0;
const EXIT_BLOCKED = 1;
const EXIT_USAGE = 2;
/** 연습 실행 — 모든 관문을 지나도 이 코드다. 자동화가 통과로 집계하지 못하게 한다. */
const EXIT_REHEARSAL = 3;

const shellRootArgument = argument("--shell-root") ?? process.env.SHELL_ROOT ?? null;
const shell = shellRootArgument ? resolve(shellRootArgument) : defaultShell;
const rehearsal = shell !== defaultShell;

/**
 * 읽을 트리가 **실제로 셸 설정 트리인가**.
 *
 * 없는 경로를 주면 `readFileSync` 가 ENOENT 스택을 토한다 — 그것은 **관문의 판정이 아니라
 * 도구의 사고**로 읽히고, 무엇을 고쳐야 하는지도 말해 주지 않는다. 읽기 전에 여기서 막는다.
 */
const REQUIRED = [
  "shell.config.json",
  "tauri.conf.json",
  "Cargo.toml",
  join("src", "lib.rs"),
  "capabilities",
];

if (!existsSync(shell) || !statSync(shell).isDirectory()) {
  console.error(`셸 설정 트리가 없다: ${shell}`);
  console.error(
    shellRootArgument
      ? "  --shell-root / SHELL_ROOT 가 가리키는 경로를 확인하세요. **이 스크립트는 경로를 만들지 않는다.**"
      : "  기본 src-tauri 가 보이지 않는다 — 워크트리 안에서 실행 중인지 확인하세요.",
  );
  process.exit(EXIT_USAGE);
}
{
  const missing = REQUIRED.filter((name) => !existsSync(join(shell, name)));
  if (missing.length) {
    console.error(`셸 설정 트리에 필요한 파일이 없다: ${shell}`);
    for (const name of missing) console.error(`  · ${name}`);
    console.error("  **관문을 돌릴 수 없다** — 읽을 것이 없는 것은 «통과»도 «차단»도 아니다.");
    process.exit(EXIT_USAGE);
  }
}

/** 관문을 막는 것. 하나라도 있으면 exit 1. */
const blockers = [];
/** 막지는 않지만 기록으로 남기는 것. */
const notes = [];

const block = (what) => blockers.push(what);
const note = (what) => notes.push(what);

function readJson(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}

// ── origin 형식 규칙 (G1 · G2 가 같은 자를 쓴다) ────────────────────────────
/**
 * **정확한 단일 https origin** 인가. 반환은 `{ ok, origin, why }`.
 *
 * 통과 조건을 느슨하게 두면 관문이 관문이 아니게 된다 — 여기서는 **스킴·호스트만** 남은
 * 형태만 받는다. `https://a.example.com/app` 처럼 경로가 붙으면 거부한다.
 */
function checkOrigin(raw, where) {
  if (raw === null || raw === undefined || raw === "") {
    return { ok: false, why: `${where}: 값이 비어 있다(미정)` };
  }
  if (typeof raw !== "string") {
    return { ok: false, why: `${where}: 문자열이 아니다(${typeof raw})` };
  }
  const value = raw.trim();
  if (value !== raw) return { ok: false, why: `${where}: 앞뒤 공백이 있다 — ${JSON.stringify(raw)}` };
  if (value.includes("*")) return { ok: false, why: `${where}: 와일드카드를 쓰고 있다 — ${value}` };

  let url;
  try {
    url = new URL(value);
  } catch {
    return { ok: false, why: `${where}: URL 이 아니다 — ${value}` };
  }
  if (url.protocol !== "https:") {
    return { ok: false, why: `${where}: https 가 아니다(마이크·쿠키 조건) — ${value}` };
  }
  if (!url.hostname) return { ok: false, why: `${where}: 호스트가 없다 — ${value}` };
  if (url.username || url.password) {
    return { ok: false, why: `${where}: 자격증명이 섞여 있다 — ${url.hostname}` };
  }
  if (url.hostname.endsWith(".invalid")) {
    return { ok: false, why: `${where}: 자리표시 주소다(.invalid) — ${value}` };
  }
  if (url.hostname === "localhost" || /^\d+\.\d+\.\d+\.\d+$/.test(url.hostname)) {
    return { ok: false, why: `${where}: 운영 origin 이 loopback/IP 다 — ${value}` };
  }
  if (url.pathname !== "/" && url.pathname !== "") {
    return { ok: false, why: `${where}: 경로가 붙어 있다(단일 origin 이 아니다) — ${value}` };
  }
  if (url.search || url.hash) {
    return { ok: false, why: `${where}: 질의/조각이 붙어 있다 — ${value}` };
  }
  // 「정확한 origin」 — 끝의 슬래시 하나까지만 허용한다.
  if (value !== url.origin && value !== `${url.origin}/`) {
    return { ok: false, why: `${where}: 정확한 origin 형태가 아니다 — ${value} (기대: ${url.origin})` };
  }
  return { ok: true, origin: url.origin };
}

// ── G1. 입력 origin ─────────────────────────────────────────────────────────
const rawInput = argument("--origin") ?? process.env.SHELL_OPERATING_ORIGIN ?? null;
let inputOrigin = null;

if (rawInput === null) {
  block(
    "G1 입력 없음 — 최종 origin 을 주지 않았다. " +
      "`SHELL_OPERATING_ORIGIN=https://<host>` 또는 `--origin https://<host>`. " +
      "**이 스크립트는 값을 지어내지 않는다**",
  );
} else {
  const verdict = checkOrigin(rawInput, "G1 입력 origin");
  if (!verdict.ok) block(verdict.why);
  else inputOrigin = verdict.origin;
}

// ── G2. shell.config.json 의 operationalOrigin ──────────────────────────────
const configPath = join(shell, "shell.config.json");
const config = readJson(configPath);
let configOrigin = null;
{
  const verdict = checkOrigin(config.operationalOrigin, "G2 shell.config.json · operationalOrigin");
  if (!verdict.ok) {
    block(
      `${verdict.why}. 운영 origin 이 확정되기 전에는 **여기를 채우지 않는다** — ` +
        `이 관문은 «아직 굽지 말라»고 말하는 중이다(OQ-T02)`,
    );
  } else {
    configOrigin = verdict.origin;
  }
}

// ── G3. capability remote.urls ──────────────────────────────────────────────
const capabilityDir = join(shell, "capabilities");
const capabilityFiles = readdirSync(capabilityDir).filter((name) => name.endsWith(".json"));
let capabilityOrigin = null;
let capabilityUrl = null;

if (capabilityFiles.length !== 1) {
  block(`G3 capability 파일이 하나가 아니다: ${capabilityFiles.join(", ") || "(없음)"}`);
} else {
  const capabilityPath = join(capabilityDir, capabilityFiles[0]);
  const capability = readJson(capabilityPath);
  const urls = capability.remote?.urls ?? [];

  if (capability.local !== false) block(`G3 ${capabilityFiles[0]}: local 이 false 가 아니다`);
  if ((capability.permissions ?? []).length !== 4) {
    block(`G3 ${capabilityFiles[0]}: 권한이 넷이 아니다 (${(capability.permissions ?? []).length})`);
  }
  if (urls.length !== 1) {
    block(`G3 ${capabilityFiles[0]}: remote.urls 가 하나가 아니다 (${urls.length})`);
  } else {
    capabilityUrl = urls[0];
    if (capabilityUrl.includes("://*.")) {
      block(`G3 ${capabilityFiles[0]}: 와일드카드 서브도메인을 쓰고 있다 — ${capabilityUrl}`);
    } else if (!capabilityUrl.endsWith("/*")) {
      block(
        `G3 ${capabilityFiles[0]}: remote.urls 가 \`<origin>/*\` 형태가 아니다 — ${capabilityUrl}`,
      );
    } else {
      const base = capabilityUrl.slice(0, -2);
      const verdict = checkOrigin(base, `G3 ${capabilityFiles[0]} · remote.urls[0]`);
      if (!verdict.ok) block(verdict.why);
      else capabilityOrigin = verdict.origin;
    }
  }
}

// ── G4. 셋이 정확히 같은가 ──────────────────────────────────────────────────
// **이것이 이 관문의 심장이다.** 「설정은 A 인데 권한은 B」인 판이 구워지면,
// 셸은 A 를 열고 A 에서 온 웹은 커맨드를 못 부른다 — 설치 후에야 드러나는 고장이다.
if (inputOrigin && configOrigin && capabilityOrigin) {
  const all = new Set([inputOrigin, configOrigin, capabilityOrigin]);
  if (all.size !== 1) {
    block(
      "G4 세 곳의 origin 이 갈렸다 — " +
        `입력=${inputOrigin} · shell.config=${configOrigin} · capability=${capabilityOrigin}. ` +
        "**설치 후에야 드러나는 고장**이므로 여기서 멈춘다",
    );
  } else {
    note(`G4 입력·shell.config·capability 가 모두 ${inputOrigin} 로 일치한다`);
  }
} else {
  note("G4 대조 생략 — 앞 관문에서 이미 막혔다(세 값이 모두 갖춰져야 대조한다)");
}

// ── G5. 이 스크립트가 잴 수 없는 것 ─────────────────────────────────────────
// **없음을 통과로 바꾸지 않는다.** 증거가 있으면 «주장»으로 싣고, 없으면 막는다.
const OPEN_ITEMS = [
  {
    key: "d4LoginMethod",
    what: "D-4 — PRODUCTION 로그인 수단",
    why: "운영 프로파일에 세션을 발급하는 경로가 정해지지 않았다. 셸이 열어도 로그인할 수 없다",
  },
  {
    key: "operationalServerLive",
    what: "운영 서버 실재",
    why: "그 origin 에 서버가 실제로 떠 있는지 이 스크립트는 네트워크를 건드리지 않아 알 수 없다",
  },
  {
    key: "m1HttpsTrust",
    what: "M-1 — https 신뢰(인증서 체인)",
    why: "미측정. 신뢰되지 않으면 셸이 흰 화면으로 멈춘다",
  },
  {
    key: "m5",
    what: "M-5 — 앱 재시작 후 로그인(쿠키) 유지",
    why: "미측정. 실기 확인이 필요하다 — 유지되지 않으면 사용자가 열 때마다 다시 로그인한다",
  },
];

const evidencePath = argument("--evidence") ?? process.env.SHELL_FINAL_EVIDENCE ?? null;
let evidence = null;
const attestations = [];

if (evidencePath && !existsSync(evidencePath)) {
  console.error(`--evidence 로 준 파일이 없다: ${evidencePath}`);
  process.exit(EXIT_USAGE);
}
if (evidencePath) {
  try {
    evidence = readJson(evidencePath);
  } catch (error) {
    console.error(`--evidence 파일을 JSON 으로 읽지 못했다: ${evidencePath}\n  ${error.message}`);
    process.exit(EXIT_USAGE);
  }
}

for (const item of OPEN_ITEMS) {
  const entry = evidence?.[item.key];
  if (!entry || entry.resolved !== true || !entry.reference) {
    block(
      `G5 ${item.what} — **미결**. ${item.why}. ` +
        "증거 파일에 `{ resolved: true, reference: \"<근거>\" }` 로 기록되기 전에는 **통과로 쓰지 않는다**",
    );
    continue;
  }
  attestations.push({
    item: item.what,
    status: "attested",
    reference: String(entry.reference),
    attestedBy: entry.attestedBy ? String(entry.attestedBy) : "(미기재)",
    // **말을 정확히 한다** — 이 스크립트가 잰 것이 아니다.
    measuredByThisGate: false,
  });
}

// ── G6. manifest — 없는 것의 해시를 만들지 않는다 ───────────────────────────
function cargoVersion() {
  const raw = readFileSync(join(shell, "Cargo.toml"), "utf8");
  const pkg = raw.split(/^\[/m).find((b) => b.startsWith("package]"));
  return pkg?.match(/^version\s*=\s*"([^"]+)"/m)?.[1] ?? null;
}

function shellApi() {
  const raw = readFileSync(join(shell, "src", "lib.rs"), "utf8");
  const found = raw.match(/const\s+SHELL_API\s*:\s*u32\s*=\s*(\d+)/);
  return found ? Number(found[1]) : null;
}

/** 실재하는 번들 파일만 해시한다. **없으면 빈 배열이다 — 자리표시 해시를 만들지 않는다.** */
function collectArtifacts() {
  const bundleRoot = join(shell, "target", "release", "bundle");
  if (!existsSync(bundleRoot)) return { artifacts: [], reason: "빌드 아티팩트가 없다(target/release/bundle 부재)" };

  const found = [];
  const walk = (dir) => {
    for (const name of readdirSync(dir)) {
      const path = join(dir, name);
      const info = statSync(path);
      if (info.isDirectory()) {
        // .app 은 디렉터리다 — 통째로 해시하지 않고 «있다»는 사실만 적는다.
        if (name.endsWith(".app")) found.push({ path, bundleDir: true, bytes: null, sha256: null });
        else walk(path);
      } else if (/\.(dmg|msi|exe|zip)$/.test(name)) {
        found.push({
          path,
          bundleDir: false,
          bytes: info.size,
          sha256: createHash("sha256").update(readFileSync(path)).digest("hex"),
        });
      }
    }
  };
  walk(bundleRoot);
  return {
    artifacts: found,
    reason: found.length ? null : "번들 디렉터리는 있으나 설치파일이 없다",
  };
}

const conf = readJson(join(shell, "tauri.conf.json"));
const { artifacts, reason: artifactReason } = collectArtifacts();

const manifest = {
  $comment:
    "WORK-006 Phase 8 최종판 manifest. **이 파일은 «구웠다»는 증거가 아니라 «무엇으로 구울 것인가»의 기록이다.** " +
    "attestations 는 이 관문이 «잰» 것이 아니라 «기록된 주장»이다.",
  generatedAt: new Date().toISOString(),
  appVersion: cargoVersion(),
  shellApi: shellApi(),
  identifier: conf.identifier ?? null,
  productName: conf.productName ?? null,
  bundleTargets: conf.bundle?.targets ?? null,
  operationalOrigin: inputOrigin ?? null,
  capabilityRemoteUrl: capabilityUrl ?? null,
  originsAgree: Boolean(inputOrigin && inputOrigin === configOrigin && inputOrigin === capabilityOrigin),
  attestations,
  artifacts,
  artifactNote:
    artifactReason ??
    "아래 해시는 **이 호스트에 실재하는 파일**을 읽어 만든 것이다. 다른 기기에서 구운 것은 포함되지 않는다",
  shellRoot: shell,
  rehearsal,
  rehearsalNote: rehearsal
    ? "**연습 실행이다** — 기본 src-tauri 가 아닌 트리를 읽었다. 관문 통과로 쓸 수 없다"
    : null,
  hostPlatform: process.platform,
  hostCaveat:
    "이 기기에서 굽지 못한 플랫폼은 이 manifest 로 검증되지 않는다 — macOS 결과를 Windows 자리에 쓰지 않는다",
};

// ── 출력 ────────────────────────────────────────────────────────────────────
console.log("== 운영 origin 최종 빌드 관문 (Phase 8) ==");
if (rehearsal) {
  console.log("");
  console.log(`⚠⚠ **연습 실행** — 설정을 ${shell} 에서 읽었다(기본 src-tauri 가 아니다).`);
  console.log("   손댄 사본을 가리켜 초록을 만들 수 있으므로 **이 실행은 관문 통과로 쓸 수 없다.**");
}
console.log("");
for (const line of notes) console.log(`  · ${line}`);
if (notes.length) console.log("");

if (blockers.length) {
  console.log(`-- 막은 것: ${blockers.length}건 --`);
  for (const line of blockers) console.log(`  ✗ ${line}`);
  console.log("");
  console.log("== manifest (참고 — 관문이 막혔으므로 최종판 기록이 아니다) ==");
  console.log(JSON.stringify(manifest, null, 2));
  console.log("");
  console.log(`관문이 막혔다 — ${blockers.length}건. **최종 빌드로 넘어가지 않는다.**`);
  console.log("(아무것도 굽지 않았고, 설정 파일을 한 글자도 쓰지 않았다)");
  process.exit(EXIT_BLOCKED);
}

console.log("== manifest ==");
console.log(JSON.stringify(manifest, null, 2));
console.log("");
console.log(
  rehearsal
    ? `연습 통과(관문 통과가 아니다) — 사본 트리 ${shell} 에서 origin ${inputOrigin} 로 세 설정이 일치했다`
    : `관문 통과 — origin ${inputOrigin} 로 세 설정이 일치하고, 미결 ${OPEN_ITEMS.length}건이 기록되었다.`,
);
console.log("");
console.log("⚠ **통과가 뜻하지 않는 것**:");
console.log("  · 운영 서버가 실제로 떠 있다 — 이 관문은 네트워크를 건드리지 않았다");
console.log("  · 로그인이 된다 — D-4 는 «기록된 주장»이지 이 관문의 측정이 아니다");
console.log("  · 설치파일이 만들어졌다 — 이 관문은 굽지 않는다");
console.log("");
console.log("다음 단계(사람이 판단해 실행한다):");
console.log(
  rehearsal
    ? `  1) **진짜** shell.config.json · capability 를 ${inputOrigin} 로 확정 — 이번 실행은 사본만 보았다`
    : `  1) shell.config.json · capability 를 ${inputOrigin} 로 확정(이미 일치함을 확인했다)`,
);
console.log("  2) make shell-verify SHELL_STRICT=1   — 구성 미비 0건 확인");
console.log("  3) 호스트 플랫폼 번들 빌드 → 이 스크립트를 다시 돌려 manifest 의 해시를 채운다");
console.log("(이 실행은 아무것도 굽지 않았고, 설정 파일을 한 글자도 쓰지 않았다)");

// ── 연습은 0 으로 끝나지 않는다 ─────────────────────────────────────────────
// 배너와 manifest 는 **사람**에게 말한다. 자동화는 종료 코드만 본다 — 그래서 여기서 한 번 더 막는다.
if (rehearsal) {
  console.log("");
  console.log(
    `⚠⚠ **exit ${EXIT_REHEARSAL} (연습)** — 모든 관문을 지났지만 **기본 src-tauri 가 아닌 트리**를 읽었다. ` +
      "**exit 0 은 진짜 설정에서만 나온다** — 이 실행을 관문 통과로 집계하지 말 것.",
  );
  process.exit(EXIT_REHEARSAL);
}

process.exit(EXIT_PASS);
