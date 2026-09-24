#!/usr/bin/env node
/**
 * WORK-006 Phase 1 — **로컬 https fixture 용 인증서**를 만든다.
 *
 * 왜 https 여야 하나: SPEC-006 §6 의 **M-1 이 「로컬 https origin 을 허용 목록에 넣어 본다」로
 * 프로토콜을 지정**했고, M-1 은 Phase 2 의 **중단 판정 근거**다. 평문 `localhost` 결과로
 * 대신하면 판정이 흔들린다.
 *
 * **이 스크립트가 하지 않는 것 — 중요하다**
 * - 시스템 신뢰 저장소를 건드리지 않는다. 만든 CA 를 **자동으로 신뢰시키지 않는다**
 * - 전역 보안 설정을 바꾸지 않는다
 * - TLS 검증을 끄지 않는다. 셸에도 그런 우회를 넣지 않았다 —
 *   검증을 무력화하고 얻은 성공은 M-1 의 증거가 되지 못한다
 *
 * 만들어진 키·인증서는 `frontend/.dev-certs/` 에 놓이고 **`.gitignore` 로 커밋에서 빠진다.**
 * 신뢰 설치는 **사람이 직접** 해야 하고, 그 명령을 마지막에 출력만 한다.
 *
 * 실행: `npm run probe:cert` (frontend 에서)
 */
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync, chmodSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const certDir = join(frontendDir, ".dev-certs");
const caKey = join(certDir, "dev-ca.key");
const caCert = join(certDir, "dev-ca.pem");
const leafKey = join(certDir, "localhost.key");
const leafCert = join(certDir, "localhost.pem");

const force = process.argv.includes("--force");

function openssl(args, options = {}) {
  return execFileSync("openssl", args, { stdio: ["ignore", "pipe", "pipe"], ...options });
}

if (existsSync(leafCert) && existsSync(leafKey) && !force) {
  console.log(`이미 있습니다: ${leafCert}`);
  console.log("다시 만들려면 `npm run probe:cert -- --force`");
} else {
  mkdirSync(certDir, { recursive: true });

  // ① 로컬 전용 CA. 이 CA 를 신뢰시키면 아래 leaf 하나만 신뢰된다 —
  //    「모든 인증서를 믿게」 만드는 설정이 아니다.
  openssl(["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256", "-days", "90",
    "-keyout", caKey, "-out", caCert,
    "-subj", "/CN=SCAX WORK-006 probe local CA",
    "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
    "-addext", "keyUsage=critical,keyCertSign,cRLSign"]);

  // ② localhost leaf. SAN 에 fixture 가 실제로 쓰는 이름만 넣는다.
  const extFile = join(certDir, "localhost.ext");
  writeFileSync(
    extFile,
    [
      "basicConstraints=CA:FALSE",
      "keyUsage=critical,digitalSignature,keyEncipherment",
      "extendedKeyUsage=serverAuth",
      "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:::1",
      "",
    ].join("\n"),
  );
  const csr = join(certDir, "localhost.csr");
  openssl(["req", "-newkey", "rsa:2048", "-nodes", "-keyout", leafKey, "-out", csr, "-subj", "/CN=localhost"]);
  openssl(["x509", "-req", "-in", csr, "-CA", caCert, "-CAkey", caKey, "-CAcreateserial",
    "-out", leafCert, "-days", "90", "-sha256", "-extfile", extFile]);

  for (const secret of [caKey, leafKey]) chmodSync(secret, 0o600);
  console.log(`만들었습니다: ${leafCert}`);
}

console.log("");
console.log("── 신뢰 설치는 사람이 한다 (이 스크립트가 대신 하지 않는다) ──");
console.log("macOS — 로그인 키체인에 이 CA 하나만 신뢰시킨다:");
console.log(`  security add-trusted-cert -d -r trustRoot -k ~/Library/Keychains/login.keychain-db "${caCert}"`);
console.log("되돌리기:");
console.log(`  security remove-trusted-cert -d "${caCert}"`);
console.log("");
console.log("Windows — 현재 사용자 신뢰 루트에 넣는다(PowerShell):");
console.log(`  Import-Certificate -FilePath "${caCert}" -CertStoreLocation Cert:\\CurrentUser\\Root`);
console.log("");
console.log("⚠ 신뢰시키지 않으면 웹뷰가 TLS 에서 막혀 **M-1 을 잴 수 없다.**");
console.log("⚠ 유효기간 90일. 측정이 끝나면 위 되돌리기 명령으로 지우는 것을 권한다.");
