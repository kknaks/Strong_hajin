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
  /* 아바타 URL 이 없으니 **`<img>` 는 안 그린다** — 없는 주소를 넣으면 깨진 이미지가 뜬다.
     대신 시안 31 의 그 자리를 DS `Avatar` 의 첫 글자로 채운다(자리를 비워 두지 않는다).
     세션에 사진 URL 이 생기면 그때 `<img>` 가 선다. */
  expect(container.querySelector("img.scax-side-nav__avatar")).toBeNull();
  expect(container.querySelector("span.scax-side-nav__avatar")?.textContent).toBe("민");
  // 버전 값이 없으니 안 그린다
  expect(container.querySelector(".scax-side-nav__version")).toBeNull();

  const nav = within(screen.getByRole("navigation", { name: "제품 탐색" }));
  const surfaces = ["홈", "업무", "캘린더", "프로젝트", "회의", "조직", "보고", "관계 탐색"];
  for (const label of surfaces) expect(nav.getByRole("button", { name: label }), label).toBeTruthy();
  expect(Array.from(container.querySelectorAll(".scax-nav-item")).map((item) => item.textContent)).toEqual(
    ["알림", "설정", "홈", "업무", "캘린더", "프로젝트", "자료함", "회의", "조직", "보고", "관계 탐색"],
  );
  expect(nav.getByRole("button", { name: "자료함" }).hasAttribute("disabled")).toBe(true);
  // 시안에만 있는 메뉴는 만들지 않았다
  for (const absent of ["수신함", "진행 현황", "자료"]) expect(nav.queryByRole("button", { name: absent }), absent).toBeNull();

  /* 시안 31 의 기둥 머리 — 알림·설정 두 줄이 구분선 «위» 에 선다.
     알림은 갈 화면이 없으므로 **진짜 disabled** 다: 눌리지도 키보드로 실행되지도 않는다.
     (예전에는 「만들지 않았다」로 아예 없었다 — 자리는 시안대로 서되 기능은 만들지 않는 쪽으로 바뀌었다) */
  const notifications = nav.getByRole("button", { name: "알림" });
  expect(notifications.hasAttribute("disabled")).toBe(true);
  const settingsRow = nav.getByRole("button", { name: "설정" });
  expect(settingsRow.hasAttribute("disabled")).toBe(false);
  // 설정은 화면 전환이 아니다 — 누르면 지금 쓰던 설정(내 AX 캐릭터)이 열린다
  fireEvent.click(settingsRow);
  const picker = await screen.findByRole("dialog", { name: "내 AX 캐릭터" });
  fireEvent.click(within(picker).getByRole("button", { name: "캐릭터 선택 닫기" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "내 AX 캐릭터" })).toBeNull());

  /* 접기 — 시안 31 의 머리 오른쪽 단추다. 접어도 «갈 수 있어야» 한다: 라벨이 사라져도
     글리프 단추의 접근성 이름은 남고, 눌러서 화면이 바뀐다. 알림은 접혀서도 여전히 못 누른다. */
  fireEvent.click(screen.getByRole("button", { name: "메뉴 접기" }));
  const collapsed = within(screen.getByRole("navigation", { name: "제품 탐색" }));
  expect(screen.getByRole("button", { name: "메뉴 펴기" })).toBeTruthy();
  expect(collapsed.getByRole("button", { name: "알림" }).hasAttribute("disabled")).toBe(true);
  fireEvent.click(collapsed.getByRole("button", { name: "회의" }));
  await waitFor(() => expect(container.querySelector(".scax-page-scroll--fixed")).not.toBeNull());
  fireEvent.click(screen.getByRole("button", { name: "메뉴 펴기" }));
  expect(screen.getByRole("button", { name: "메뉴 접기" })).toBeTruthy();

  // 화면마다: 페이지가 스크롤 기둥 «안에» 들어가 있나
  for (const label of surfaces) {
    fireEvent.click(nav.getByRole("button", { name: label }));
    await waitFor(() => expect(container.querySelector(".scax-page-scroll")).not.toBeNull());
    const scroll = container.querySelector(".scax-page-scroll")!;
    /* WORK-004 FE-1: 캘린더가 회의와 같은 편에 섰다 — 격자가 칸을 꽉 채우고 주 뷰의 시간 격자가
       «자기 안에서» 스크롤한다(`styles/calendar.css` 의 `.scax-cal-main{flex:1 1 auto;min-height:0}`).
       바깥이 함께 스크롤하면 스크롤 주인이 둘이 되어 격자가 잘린다.
       WORK-005 FE-1: 프로젝트도 같은 편에 섰다 — 좌·우 레일과 본문(요약 스트립 + 진행 라인)이
       칸을 꽉 채우고 `.scax-pj-view` 가 «자기 안에서» 스크롤한다. 나머지 화면은 그대로 문서형이다. */
    const fixed = label === "회의" || label === "캘린더" || label === "프로젝트";
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
