#!/usr/bin/env node
/**
 * **Release 자산 동일성 관문**(WORK-006 Phase 9).
 *
 * 묻는 것은 하나다: **「Release 에 올리려는 이 파일이, Phase 8 이 본 바로 그 파일인가.」**
 *
 * 이 스크립트는 **굽지 않는다. 태그를 달지 않는다. Release 를 만들지 않는다. 서명·공증하지 않는다.**
 * 네트워크를 건드리지 않고 **읽기만 한다** — 그래서 몇 번을 돌려도 안전하다.
 *
 * ## 왜 필요한가 — Phase 8 이 막지 못하는 틈
 *
 * Phase 8 관문은 **「무엇으로 구울 것인가」**를 본다. 그 뒤에 굽고 → 올린다. 그 사이에
 * **아무도 보지 않는 구간**이 있다: 다시 구운 판, 손으로 고친 파일, 다른 기기에서 가져온 것,
 * 이름만 같은 옛 판. **이름이 같아도 같은 파일이 아니다.** 그 구간을 해시로 좁힌다.
 *
 * ## 입력 둘
 *
 *   --manifest <path>    Phase 8 관문이 **통과(exit 0)** 하며 찍은 manifest JSON
 *   --candidate <dir>    Release 에 올릴 파일들이 들어 있는 디렉터리
 *
 * ## manifest 스키마 — 읽는 것만 적는다
 *
 * | 열쇠 | 형 | 쓰임 |
 * |---|---|---|
 * | `appVersion` | string(semver) | 조합 대조 |
 * | `shellApi` | number | 조합 대조 |
 * | `operationalOrigin` | string(https origin) | 조합 대조 |
 * | `capabilityRemoteUrl` | string | `<origin>/*` 인지 |
 * | `originsAgree` | **true 여야 한다** | 아니면 «검증되지 않은 manifest» |
 * | `rehearsal` | **false 여야 한다** | true 면 연습 기록이다 |
 * | `artifacts[]` | `{path, sha256, bytes, bundleDir}` | **동일성의 근거** |
 * | `hostPlatform` | string | 어느 플랫폼이 **미검증**인지 |
 *
 * ## 종료 코드 — 원인마다 다르다
 *
 * | 코드 | 뜻 |
 * |---|---|
 * | **0** | 동일하다 — Release 자산으로 쓸 수 있다 |
 * | **2** | 돌릴 수 없다(입력 없음·파일 없음·JSON 아님) |
 * | **3** | **검증되지 않은 manifest**(연습이거나 origin 불일치) |
 * | **4** | manifest 에 **아티팩트가 없다**(굽지 않았다) |
 * | **5** | **집합 불일치** — 기록된 것이 **누락**되었거나, 기록에 없는 **파일/디렉터리가 추가**되었거나, 같은 이름인데 **종류가 다르다**(기록은 파일 · candidate 는 디렉터리) |
 * | **6** | **해시 불일치** — 이름은 같은데 다른 파일이다 |
 * | **7** | **조합 불일치** — 판 번호·shell_api·origin 이 지금 트리와 다르다 |
 * | **8** | **대조 불가 자산** — candidate 에 있는데 기록에 해시가 없다(디렉터리 번들) |
 *
 * ⚠ **이 관문은 `1` 을 판정으로 쓰지 않는다** — 내는 코드는 `0 · 2 · 3 · 4 · 5 · 6 · 7 · 8` 뿐이다.
 * Phase 8 관문(`verify-final-build.mjs`)이 쓰는 `1`(「관문이 막았다」)과 **겹치지 않게** 비워 둔 자리다 —
 * 두 관문을 한 파이프라인에서 집계할 때 `1` 이 어느 쪽 판정인지 헷갈리지 않는다.
 * (다만 **예상 못 한 예외로 죽으면 node 자신이 1 로 끝낸다** — 그때의 `1` 은 판정이 아니라 **사고**다.
 *  이 파일이 낸 `1` 은 「막혔다」가 아니라 「스크립트가 깨졌다」로 읽어야 한다.)
 *
 * ⚠ **`make` 로 부르면 이 코드들이 전부 2 로 뭉개진다**(GNU make 가 레시피의 nonzero 를 감싼다).
 * 코드를 읽어야 하는 자동화는 **이 파일을 직접 부른다**.
 *
 * 사용:
 *   node scripts/verify-release-artifact.mjs --manifest <path> --candidate <dir>
 */
import { readFileSync, existsSync, readdirSync, statSync } from "node:fs";
import { createHash } from "node:crypto";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const EXIT_PASS = 0;
const EXIT_USAGE = 2;
const EXIT_MANIFEST_UNVERIFIED = 3;
const EXIT_NO_ARTIFACTS = 4;
const EXIT_CANDIDATE_SET = 5;
const EXIT_HASH_MISMATCH = 6;
const EXIT_COMBINATION = 7;
/** candidate 에 **기록돼 있으나 해시가 없는** 자산이 있다 — 동일성을 말할 수 없다. */
const EXIT_UNVERIFIABLE_ASSET = 8;

const here = dirname(fileURLToPath(import.meta.url));
const shell = resolve(here, "..", "src-tauri");

function argument(flag) {
  const at = process.argv.indexOf(flag);
  return at === -1 ? null : process.argv[at + 1];
}

function die(code, headline, ...lines) {
  console.error(headline);
  for (const line of lines) console.error(`  ${line}`);
  process.exit(code);
}

// ── 입력 ────────────────────────────────────────────────────────────────────
const manifestPath = argument("--manifest") ?? process.env.SHELL_RELEASE_MANIFEST ?? null;
const candidateDir = argument("--candidate") ?? process.env.SHELL_RELEASE_CANDIDATE ?? null;

if (!manifestPath || !candidateDir) {
  die(
    EXIT_USAGE,
    "입력이 모자라다 — Release 자산 동일성은 **두 쪽이 있어야** 잴 수 있다.",
    "사용: node scripts/verify-release-artifact.mjs --manifest <phase8-manifest.json> --candidate <dir>",
    "  --manifest  : Phase 8 관문이 **통과하며** 찍은 manifest JSON",
    "  --candidate : Release 에 올릴 파일들이 든 디렉터리",
    "**이 스크립트는 없는 입력을 지어내지 않는다.**",
  );
}
if (!existsSync(manifestPath) || !statSync(manifestPath).isFile()) {
  die(EXIT_USAGE, `manifest 파일이 없다: ${manifestPath}`, "**경로를 만들지 않는다.**");
}
if (!existsSync(candidateDir) || !statSync(candidateDir).isDirectory()) {
  die(EXIT_USAGE, `candidate 디렉터리가 없다: ${candidateDir}`, "**빌드하지 않는다** — 이미 구운 것을 대조할 뿐이다.");
}

let manifest;
try {
  manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
} catch (error) {
  die(EXIT_USAGE, `manifest 를 JSON 으로 읽지 못했다: ${manifestPath}`, error.message);
}

// ── 1. 검증된 manifest 인가 (코드 3) ────────────────────────────────────────
// **연습 기록이나 origin 이 갈린 기록 위에 Release 를 세우지 않는다.**
{
  const why = [];
  if (manifest.rehearsal === true) {
    why.push("`rehearsal: true` — 연습 실행의 기록이다(Phase 8 은 이 경우 exit 3 이다)");
  }
  if (manifest.originsAgree !== true) {
    why.push("`originsAgree` 가 true 가 아니다 — 입력·shell.config·capability 가 같았다는 기록이 없다");
  }
  if (!manifest.operationalOrigin) {
    why.push("`operationalOrigin` 이 비어 있다 — 운영 origin 이 정해지기 전의 기록이다");
  }
  if (why.length) {
    die(
      EXIT_MANIFEST_UNVERIFIED,
      "**검증되지 않은 manifest 다** — 이 기록 위에 Release 를 세울 수 없다.",
      ...why,
      "Phase 8 관문을 **exit 0 으로** 통과시킨 뒤 그때 찍힌 manifest 를 쓰세요.",
    );
  }
}

// ── 2. 아티팩트가 있는가 (코드 4) ───────────────────────────────────────────
// **없으면 여기서 끝이다.** 자리표시 해시를 만들지 않는다.
const recorded = Array.isArray(manifest.artifacts) ? manifest.artifacts : [];
if (!recorded.length) {
  die(
    EXIT_NO_ARTIFACTS,
    "manifest 에 아티팩트가 없다 — **굽지 않은 기록이다.**",
    manifest.artifactNote ? `manifest 의 말: ${manifest.artifactNote}` : "",
    "먼저 호스트 플랫폼 번들을 굽고, Phase 8 관문을 **다시 돌려** manifest 의 해시를 채우세요.",
    "**이 스크립트는 해시를 지어내지 않는다.**",
  );
}

// 해시가 있는 것만 «대조할 수 있는 것»이다. `.app` 같은 디렉터리 항목은 해시가 없다.
const hashable = recorded.filter((entry) => entry && entry.sha256 && !entry.bundleDir);
const unhashable = recorded.filter((entry) => entry && (!entry.sha256 || entry.bundleDir));

if (!hashable.length) {
  die(
    EXIT_NO_ARTIFACTS,
    "manifest 의 아티팩트에 **해시가 하나도 없다** — 동일성을 잴 근거가 없다.",
    `기록된 항목 ${recorded.length}건은 모두 디렉터리이거나 해시가 비어 있다(예: .app 번들).`,
    "**«해시가 없다»를 «같다»로 바꾸지 않는다.**",
  );
}

// ── 3. 조합 대조 — 지금 트리와 같은 판인가 (코드 7) ─────────────────────────
// manifest 는 **과거의 기록**이다. 그 사이 트리가 움직였으면 그 기록으로 올리면 안 된다.
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
function configOrigin() {
  const raw = JSON.parse(readFileSync(join(shell, "shell.config.json"), "utf8"));
  return raw.operationalOrigin ?? null;
}
function capabilityUrl() {
  const dir = join(shell, "capabilities");
  const files = readdirSync(dir).filter((n) => n.endsWith(".json"));
  if (files.length !== 1) return null;
  return (JSON.parse(readFileSync(join(dir, files[0]), "utf8")).remote?.urls ?? [])[0] ?? null;
}

const combination = [];
const treeVersion = cargoVersion();
const treeApi = shellApi();
const treeOrigin = configOrigin();
const treeCapability = capabilityUrl();

if (manifest.appVersion !== treeVersion) {
  combination.push(`판 번호 — manifest=${manifest.appVersion} · 트리(Cargo.toml)=${treeVersion}`);
}
if (manifest.shellApi !== treeApi) {
  combination.push(`shell_api — manifest=${manifest.shellApi} · 트리(lib.rs)=${treeApi}`);
}
if (manifest.operationalOrigin !== treeOrigin) {
  combination.push(`origin — manifest=${manifest.operationalOrigin} · 트리(shell.config.json)=${treeOrigin}`);
}
if (manifest.capabilityRemoteUrl !== treeCapability) {
  combination.push(`capability — manifest=${manifest.capabilityRemoteUrl} · 트리=${treeCapability}`);
}
if (manifest.capabilityRemoteUrl !== `${manifest.operationalOrigin}/*`) {
  combination.push(
    `manifest 안에서 어긋난다 — capability=${manifest.capabilityRemoteUrl} · 기대=${manifest.operationalOrigin}/*`,
  );
}

if (combination.length) {
  die(
    EXIT_COMBINATION,
    "**조합 불일치** — 이 manifest 는 **지금 트리가 아닌 판**을 기록한 것이다.",
    ...combination,
    "이 기록으로 Release 를 올리면 **설치파일이 말하는 판과 저장소의 판이 갈린다.**",
  );
}

// ── 4. candidate 집합 대조 (코드 5 · 8) ────────────────────────────────────
// 「이름이 있는가」와 「다른 것이 섞였는가」를 **둘 다** 본다.
//
// ⚠ **디렉터리를 «파일이 아니다»라고 조용히 걸러내지 않는다.** macOS `.app` 은 디렉터리이고,
// 그대로 Release 에 올라갈 수 있다. 목록에서 빼 버리면 **아무도 보지 않은 자산이 통과한다** —
// 관문이 「같다」고 말한 집합 밖에서 자산 하나가 따라 올라가는 꼴이다.
// 그래서 **파일이든 디렉터리든 다 센다**: 기록에 없으면 «섞였다»(코드 5),
// 기록에는 있는데 해시가 없으면 «대조할 수 없다»(코드 8).
const wanted = new Map(hashable.map((entry) => [basename(entry.path), entry]));
/** 기록된 **모든** 이름 — 해시가 없는 디렉터리 번들까지 포함한다. */
const recordedNames = new Set(recorded.filter((e) => e && e.path).map((e) => basename(e.path)));
/** 기록에는 있으나 **해시가 없는** 이름(예: `.app`). */
const unhashableNames = new Set(unhashable.filter((e) => e && e.path).map((e) => basename(e.path)));

const present = readdirSync(candidateDir).map((name) => ({
  name,
  isDirectory: statSync(join(candidateDir, name)).isDirectory(),
}));

const missing = [...wanted.keys()].filter(
  (name) => !present.some((entry) => entry.name === name && !entry.isDirectory),
);
// 기록은 «파일»인데 candidate 에 **같은 이름의 디렉터리**가 있는 경우 — 해시를 낼 수 없다.
const wrongKind = present.filter((entry) => entry.isDirectory && wanted.has(entry.name));
const extra = present.filter((entry) => !recordedNames.has(entry.name));

if (missing.length || extra.length || wrongKind.length) {
  die(
    EXIT_CANDIDATE_SET,
    "**candidate 가 기록과 같은 집합이 아니다.**",
    ...missing.map((n) => `없다 — 기록에는 있는데 candidate 에 없다: ${n}`),
    ...wrongKind.map(
      (e) => `종류가 다르다 — 기록은 파일인데 candidate 에는 디렉터리다: ${e.name}/`,
    ),
    ...extra.map(
      (e) =>
        `기록에 없다 — candidate 에 있는데 Phase 8 이 본 적 없다: ${e.name}${e.isDirectory ? "/ (디렉터리)" : ""}`,
    ),
    "**Release 에는 기록된 것만 올라간다** — 하나라도 다르면 「검증된 자산」이 아니다.",
  );
}

// ── 4-1. 기록돼 있으나 **대조할 수 없는** 항목이 candidate 에 있다 (코드 8) ─
// 「해시가 없다」를 「같다」로 바꾸지 않는다 — 그것이 §5-1 의 규칙이고, candidate 안에서도 같다.
const unverifiablePresent = present.filter((entry) => unhashableNames.has(entry.name));
if (unverifiablePresent.length) {
  die(
    EXIT_UNVERIFIABLE_ASSET,
    "**candidate 에 대조할 수 없는 자산이 있다** — 기록에는 있지만 **해시가 없다.**",
    ...unverifiablePresent.map(
      (e) => `${e.name}${e.isDirectory ? "/" : ""} — Phase 8 이 디렉터리 번들로 기록해 sha256 이 없다`,
    ),
    "디렉터리는 바이트 하나로 묶이지 않아 이 관문이 동일성을 말할 수 없다.",
    "**올리려면 해시를 낼 수 있는 형태로 싸세요**(예: `.app` 대신 그것을 담은 `.dmg`/`.zip`).",
    "**«해시가 없다»를 «같다»로 바꾸지 않는다.**",
  );
}

// ── 5. 해시 대조 (코드 6) ───────────────────────────────────────────────────
const verified = [];
const mismatched = [];

for (const [name, entry] of wanted) {
  const path = join(candidateDir, name);
  const bytes = readFileSync(path);
  const sha256 = createHash("sha256").update(bytes).digest("hex");
  if (sha256 !== entry.sha256) {
    mismatched.push({ name, expected: entry.sha256, actual: sha256, expectedBytes: entry.bytes, actualBytes: bytes.length });
  } else if (typeof entry.bytes === "number" && entry.bytes !== bytes.length) {
    // 해시가 같은데 크기가 다르면 manifest 쪽 기록이 상한 것이다 — 조용히 넘기지 않는다.
    mismatched.push({ name, expected: entry.sha256, actual: sha256, expectedBytes: entry.bytes, actualBytes: bytes.length });
  } else {
    verified.push({ name, sha256, bytes: bytes.length });
  }
}

if (mismatched.length) {
  console.error("**해시 불일치** — 이름은 같은데 **다른 파일이다.**");
  for (const m of mismatched) {
    console.error(`  ✗ ${m.name}`);
    console.error(`      기록: ${m.expected}  (${m.expectedBytes ?? "?"} bytes)`);
    console.error(`      실물: ${m.actual}  (${m.actualBytes} bytes)`);
  }
  console.error("  다시 구운 판·손으로 고친 파일·다른 기기에서 온 것일 수 있다.");
  console.error("  **Phase 8 이 본 파일을 그대로 올리거나, 다시 구운 뒤 Phase 8 을 다시 돌리세요.**");
  process.exit(EXIT_HASH_MISMATCH);
}

// ── 6. 플랫폼 상태 — 미검증을 검증됨으로 바꾸지 않는다 ──────────────────────
const host = manifest.hostPlatform ?? "(미기재)";
const macAssets = verified.filter((v) => /\.(dmg|app\.zip)$/.test(v.name));
const winAssets = verified.filter((v) => /\.(msi|exe)$/.test(v.name));

const platformStatus = [
  {
    platform: "macOS(dmg)",
    assets: macAssets.length,
    status: host === "darwin" ? (macAssets.length ? "검증됨(해시 일치)" : "자산 없음") : "**미검증**",
  },
  {
    platform: "Windows(msi·nsis)",
    assets: winAssets.length,
    status:
      host === "win32"
        ? winAssets.length
          ? "검증됨(해시 일치)"
          : "자산 없음"
        : "**미검증** — 이 manifest 는 " + host + " 에서 만들어졌다",
  },
];

// ── 결과 ────────────────────────────────────────────────────────────────────
console.log("== Release 자산 동일성 관문 (Phase 9) ==");
console.log("");
console.log(`manifest  : ${resolve(manifestPath)}`);
console.log(`candidate : ${resolve(candidateDir)}`);
console.log(`판        : ${manifest.appVersion} · shell_api ${manifest.shellApi} · ${manifest.operationalOrigin}`);
console.log("");
console.log(`-- 해시가 일치한 자산: ${verified.length}건 --`);
for (const v of verified) console.log(`  ✓ ${v.name}  ${v.sha256}  (${v.bytes} bytes)`);

if (unhashable.length) {
  console.log("");
  console.log(`-- 해시가 없어 **대조하지 못한** 기록: ${unhashable.length}건 --`);
  for (const u of unhashable) {
    console.log(`  ℹ ${basename(u.path)} — 디렉터리 번들이라 해시가 없다. **«같다»고 쓰지 않는다**`);
  }
}

console.log("");
console.log("-- 플랫폼 상태 --");
for (const row of platformStatus) {
  console.log(`  · ${row.platform}: ${row.status} (자산 ${row.assets}건)`);
}
console.log(`  manifest 를 만든 호스트: ${host}`);
console.log("  **이 호스트에서 굽지 못한 플랫폼은 이 관문으로 검증되지 않는다 — 상태를 그대로 적는다.**");

console.log("");
console.log(`동일성 확인 — ${verified.length}건이 Phase 8 기록과 **바이트까지 같다.**`);
console.log("");
console.log("⚠ **통과가 뜻하지 않는 것**:");
console.log("  · 서명·공증이 되어 있다 — 이 관문은 서명을 보지 않는다");
console.log("  · 설치가 된다 — 실기 설치는 사람이 확인한다(M-10 미측정)");
console.log("  · Release 가 만들어졌다 — **이 관문은 태그도 Release 도 만들지 않는다**");
console.log("");
console.log("(네트워크를 건드리지 않았고, 아무것도 굽지 않았고, 저장소를 한 글자도 쓰지 않았다)");
process.exit(EXIT_PASS);
