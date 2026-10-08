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
  vi.stubGlobal("EventSource", undefined);
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
  vi.stubGlobal("EventSource", undefined);
  vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
    if (String(input) === "/api/auth/me") return json({ member_id: "haram", display_name: "유하람", organizations: [], capabilities: [] });
    return json([]);
  });
  render(<App />);
  const banner = await screen.findByRole("alert");
  expect(banner.textContent).toContain("연결하지 못했습니다");
  expect(window.location.search).toBe("");
});

it("콜백 쿼리 tab=notify 로 알림 설정 탭이 열린다(SPEC-011 §2.3 · WORK-013 WP3-FE)", async () => {
  window.history.replaceState(null, "", "/?surface=settings&tab=notify");
  vi.stubGlobal("EventSource", undefined);
  vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path === "/api/auth/me") return json({ member_id: "haram", display_name: "유하람", organizations: [], capabilities: [] });
    if (path === "/api/me/notification-settings")
      return json({
        enabled: true,
        themes: {
          work: { on: true, items: { request: true, assign: true, answer: true, report: true, rework: true, change: true, comment: false, unblock: true } },
          message: { on: true, items: { mail: true, slack: true, kakao: true } },
          meeting: { on: true, items: { invite: true, change: true, minutes: true, "minutes-fail": true, share: true } },
        },
        version: 0,
      });
    return json([]);
  });
  const { container } = render(<App />);
  await waitFor(() => expect(container.querySelector(".scax-page-header__title")?.textContent).toBe("알림 설정"));
  expect(await screen.findByRole("switch", { name: "알림 받기" })).toBeTruthy();
  expect(window.location.search).toBe("");
});
