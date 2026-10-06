import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import App from "./App";

/* OAuth 콜백은 `?surface=settings&tab=…&connect=ok|denied` 로 돌아온다(SPEC-008 §4.2 N-2 · AC-01b).
   그 탭이 열리고 결과가 알려지며, 새로고침이 다시 알리지 않게 쿼리는 지워진다. */

const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

it("콜백 쿼리로 설정의 그 탭이 열리고 거부 결과를 알린 뒤 쿼리를 지운다", async () => {
  window.history.replaceState(null, "", "/?surface=settings&tab=slack&connect=denied");
  vi.stubGlobal("WebSocket", undefined);
  vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
    if (String(input) === "/api/auth/me")
      return json({ member_id: "haram", display_name: "유하람", organizations: [], capabilities: [], profile_image_url: "/api/profile/image?v=1" });
    return json([]);
  });
  const { container } = render(<App />);
  await screen.findByRole("navigation", { name: "제품 탐색" });
  await waitFor(() => expect(container.querySelector(".scax-page-header__title")?.textContent).toBe("슬랙 연동"));
  expect(await screen.findByText("연결되지 않았습니다.")).toBeTruthy();
  expect(window.location.search).toBe("");
  // 프로필 이미지가 있으면 내비 아바타가 그것으로 선다(W-15)
  await waitFor(() => expect(container.querySelector("img.scax-side-nav__avatar")?.getAttribute("src")).toBe("/api/profile/image?v=1"));
});

it("콜백이 connect=error(토큰 교환 실패)로 오면 설정 화면에 오류 배너가 선다(검수 W-1)", async () => {
  window.history.replaceState(null, "", "/?surface=settings&tab=mail&connect=error");
  vi.stubGlobal("WebSocket", undefined);
  vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
    if (String(input) === "/api/auth/me") return json({ member_id: "haram", display_name: "유하람", organizations: [], capabilities: [] });
    return json([]);
  });
  render(<App />);
  const banner = await screen.findByRole("alert");
  expect(banner.textContent).toContain("연결하지 못했습니다");
  expect(window.location.search).toBe("");
});
