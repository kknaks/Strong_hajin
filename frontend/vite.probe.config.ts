// WORK-006 Phase 1 — **계측 전용** 개발 서버 설정.
//
// 제품 설정(`vite.config.ts`)을 건드리지 않고 따로 둔다. 이유는 둘이다.
// ① 계측 화면과 https fixture 가 **제품 빌드에 섞이지 않는다** — `npm run build` 는
//    여전히 `vite.config.ts` 와 `index.html` 만 본다. 이 파일은 `--config` 로만 쓰인다.
// ② fixture 의 origin·포트가 셸의 capability(`src-tauri/capabilities/probe-fixture.json`)와
//    **한 쌍**이라, 제품 설정이 바뀌어도 측정 기준이 흔들리지 않는다.
//
// 인증서는 `npm run probe:cert` 가 만든다. **커밋 대상이 아니다**(`.gitignore`).
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";
import { createConnection, createServer, type AddressInfo } from "node:net";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

const here = dirname(fileURLToPath(import.meta.url));
const certDir = resolve(here, ".dev-certs");
const key = join(certDir, "localhost.key");
const cert = join(certDir, "localhost.pem");

if (!existsSync(key) || !existsSync(cert)) {
  throw new Error(
    `로컬 https 인증서가 없습니다: ${certDir}\n` +
      "먼저 `npm run probe:cert` 를 실행하고, 출력된 신뢰 설치 명령을 사람이 직접 실행하세요.\n" +
      "TLS 검증을 끄는 우회는 두지 않습니다 — 그렇게 얻은 결과는 M-1 의 증거가 되지 못합니다.",
  );
}

/**
 * 이미 열린 loopback 주소의 **반대쪽 loopback** 을 같은 포트로 마저 연다.
 *
 * 왜 필요한가 — vite/node 는 `host` 하나를 주소 하나로 잡는다. `localhost` 가 어느 스택으로
 * 풀리는지는 기기·설정마다 다르고(`/etc/hosts` · `getaddrinfo` 순서), 한쪽만 열려 있으면
 * **「fixture 에 닿지 못한 것」이 「커맨드 왕복 실패」로 기록될 수 있다.** M-1 은 Phase 2 의
 * 중단 판정 근거라 그 혼동이 판정을 뒤집는다.
 *
 * **여는 것은 loopback 둘뿐이다** — `127.0.0.1` 과 `::1`. `0.0.0.0`·`::` 로 넓히지 않으므로
 * LAN·Tailscale 어디에도 열리지 않는다. 인증서 SAN 에 이미 두 주소가 모두 들어 있어
 * (`scripts/dev-https-cert.mjs`) 재발급이 필요 없다. TLS 는 그대로 vite 가 끝내고,
 * 이 다리는 **바이트만 그대로 넘긴다** — 검증을 건드리지 않는다.
 */
function loopbackMirror(): Plugin {
  return {
    name: "scax-probe-loopback-mirror",
    configureServer(server) {
      server.httpServer?.once("listening", () => {
        const address = server.httpServer?.address() as AddressInfo | string | null | undefined;
        if (!address || typeof address === "string") return;
        // 이미 잡은 쪽의 반대편만 연다. 어느 쪽이 잡혔는지는 기기가 정한다.
        const boundV6 = address.family === "IPv6" || (address.family as unknown as number) === 6;
        const mirrorHost = boundV6 ? "127.0.0.1" : "::1";
        const targetHost = boundV6 ? "::1" : "127.0.0.1";

        const mirror = createServer((incoming) => {
          const upstream = createConnection({ host: targetHost, port: address.port });
          incoming.on("error", () => upstream.destroy());
          upstream.on("error", () => incoming.destroy());
          incoming.pipe(upstream);
          upstream.pipe(incoming);
        });
        mirror.on("error", (reason) => {
          // 못 열어도 fixture 는 그대로 돈다 — 대신 **어느 스택이 죽어 있는지 알린다.**
          server.config.logger.warn(
            `[probe] ${mirrorHost}:${address.port} 을 열지 못했다 — 그 스택으로는 닿지 않는다: ${String(reason)}`,
          );
        });
        mirror.listen(address.port, mirrorHost, () => {
          server.config.logger.info(`  ➜  Loopback mirror: https://${boundV6 ? mirrorHost : `[${mirrorHost}]`}:${address.port}/`);
        });
        server.httpServer?.on("close", () => mirror.close());
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), loopbackMirror()],
  server: {
    // 셸 capability 의 `remote.urls` 와 **같은 origin** 이어야 커맨드가 ACL 을 통과한다.
    // 포트를 바꾸면 `capabilities/probe-fixture.json` 도 같이 바꾼다.
    //
    // **`localhost` 로 듣는다 — `127.0.0.1` 이 아니다**(리뷰 W-1).
    // 셸이 여는 주소는 `https://localhost:5180` 인데, 이 기기의 `localhost` 는
    // `::1` 을 **먼저** 돌려준다(`getaddrinfo`). IPv4 에만 묶어 두면 웹뷰의 첫 연결이
    // 거절되고, 살아나는지 여부가 클라이언트 fallback 에 달린다 — 그러면
    // 「커맨드 왕복이 안 된다」와 「fixture 에 닿지도 못했다」가 섞여
    // **M-1 의 중단 판정이 틀린다.**
    //
    // ⚠ 다만 `host: "localhost"` **하나로는 양쪽 스택이 열리지 않는다.** 실측하니
    // node 가 주소 하나만 잡아 `[::1]:5180` 만 듣고 `127.0.0.1:5180` 은 죽었다
    // (`lsof -nP -iTCP:5180`: IPv6 한 줄 · `curl https://127.0.0.1:5180` → 000).
    // 그래서 아래 `loopbackMirror` 가 **남은 loopback 주소 한쪽을 마저 연다**.
    host: "localhost",
    port: 5180,
    strictPort: true,
    https: { key: readFileSync(key), cert: readFileSync(cert) },
  },
  // 이 설정으로 제품을 빌드하지 않는다. 계측 페이지는 개발 서버에서만 뜬다.
  build: { rollupOptions: { input: resolve(here, "probe.html") } },
});
