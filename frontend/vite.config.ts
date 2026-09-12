import { defineConfig } from "vitest/config";
import { loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const environment = loadEnv(mode, process.cwd(), "");
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        // 문자열형은 HTTP 만 넘기고 **업그레이드를 넘기지 않는다** — 회의 스트림(`WS /api/meetings/{id}/stream`)이
        // 그대로 걸려 핸드셰이크 응답이 오지 않는다. `ws: true` 가 그 한 줄이다.
        "/api": { target: environment.VITE_API_TARGET ?? "http://127.0.0.1:8000", ws: true },
      },
    },
    test: {
      environment: "jsdom",
    },
  };
});
