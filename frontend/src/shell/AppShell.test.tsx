/* 셸 — 바퀴 2 에서 구 .thesc-shell/.rail/.canvas 를 새 DS 의 AppShell·SideNav·AppHeader·AppBody 로
   갈아탔다. 여기서 잠그는 것은 셋이고, 다음 바퀴들이 이 틀 위에 얹히므로 먼저 깨져야 한다.

   ① 스크롤 경계 (D-B) — 셸이 height:100vh · overflow:hidden 이라, 화면마다 본문이 «자기»
      스크롤 기둥을 가져야 한다. 기둥이 본문 칸의 직계 자식이 아니면 높이를 못 받아 내용이 잘린다.
   ② 없는 메뉴를 만들지 않는다 (D-F) — 시안 nav.js 의 수신함·진행 현황·자료·알림은 갈 화면이 없다.
   ③ 브레드크럼은 살아 있고 «눌린다» (D-A) — 회의 상세에서 목록으로 돌아가는 유일한 길이다.
      지우는 것은 바퀴 6 이다.

   높이를 «재는» 것은 jsdom 이 못 한다 — 기둥이 이어져 있는지를 검사한다. */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import App from "../App";

const jsonResponse = (body: unknown) => new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
afterEach(cleanup);

it("셸 뼈대가 서고, 화면마다 스크롤 기둥이 본문 칸에 직접 붙는다", async () => {
  vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path === "/api/auth/providers") return jsonResponse({ local: true, oidc: false });
    if (path === "/api/auth/me" || path === "/api/organization/me")
      return jsonResponse({
        member_id: "mina", display_name: "민아", organizations: [{ id: "o1", name: "테스트조직" }],
        capabilities: ["task.read", "task.self_manage", "work_request.read", "daily_report.generate"],
      });
    if (path.startsWith("/api/action-items")) return jsonResponse([]);
    if (path.startsWith("/api/graph")) return jsonResponse({ nodes: [], edges: [] });
    if (path.startsWith("/api/meetings")) return jsonResponse({ title: "회의", upcoming: [], past: { items: [], total: 0 } });
    return jsonResponse([]);
  });
  const { container } = render(<App />);
  await screen.findByRole("navigation", { name: "제품 탐색" });

  // 셸 뼈대
  for (const c of ["scax-app-shell", "scax-app-main", "scax-side-nav", "scax-page-header", "scax-page-body", "scax-page-body__content"]) {
    expect(container.querySelector(`.${c}`), c).not.toBeNull();
  }
  // 신원·접기·로고
  expect(screen.getByRole("button", { name: "내 AX 캐릭터" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "메뉴 접기" })).toBeTruthy();
  expect(container.querySelector(".scax-side-nav__logo")?.textContent).toBe("SCAX");
  // 아바타 URL 이 없으니 img 를 안 그린다
  expect(container.querySelector(".scax-side-nav__avatar")).toBeNull();
  // 버전 값이 없으니 안 그린다
  expect(container.querySelector(".scax-side-nav__version")).toBeNull();

  const nav = within(screen.getByRole("navigation", { name: "제품 탐색" }));
  const surfaces = ["오늘", "캘린더", "회의", "내 업무", "보고", "프로젝트", "조직", "관계 탐색"];
  for (const label of surfaces) expect(nav.getByRole("button", { name: label }), label).toBeTruthy();
  // 시안에만 있는 메뉴는 만들지 않았다
  for (const absent of ["수신함", "진행 현황", "자료", "알림"]) expect(nav.queryByRole("button", { name: absent }), absent).toBeNull();

  // 화면마다: 페이지가 스크롤 기둥 «안에» 들어가 있나
  for (const label of surfaces) {
    fireEvent.click(nav.getByRole("button", { name: label }));
    await waitFor(() => expect(container.querySelector(".scax-page-scroll")).not.toBeNull());
    const scroll = container.querySelector(".scax-page-scroll")!;
    const fixed = label === "회의";
    expect(scroll.classList.contains("scax-page-scroll--fixed"), `${label} fixed`).toBe(fixed);
    expect(scroll.children.length, `${label} 내용이 기둥 안에 있다`).toBeGreaterThan(0);
    // 스크롤 기둥은 본문 칸의 «직계 자식» 이어야 높이를 나눠 받는다
    expect(scroll.parentElement?.className, `${label} 부모`).toBe("scax-page-body__content");
  }

  /* 바퀴 2 는 브레드크럼을 «회의 상세에서 목록으로 돌아가는 유일한 길» 이라 살려 뒀고(D-A),
     바퀴 6a 가 회의를 한 화면 4칸으로 합치면서 지웠다(M-3). 지운 것이 맞으려면 «돌아갈 길» 이
     UI 에 들어와 있어야 한다 — 그것을 여기서 지킨다: 회의 화면에서 목록 칸이 상시 서 있다. */
  await waitFor(() => expect(container.querySelector(".scax-breadcrumb")).toBeNull());
  fireEvent.click(nav.getByRole("button", { name: "회의" }));
  await waitFor(() => expect(container.querySelector(".scax-page-body__rail--left")).not.toBeNull());
});
