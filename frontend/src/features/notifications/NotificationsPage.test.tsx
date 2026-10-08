import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useCallback, useState, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EventStreamProvider } from "../../lib/eventStreamContext";
import { FakeEventSource } from "../../lib/fakeEventSource.test-utils";
import type { Notification } from "../../lib/viewModels";
import { NotificationsPage } from "./NotificationsPage";

/*
 * WORK-013 WP3-FE — 알림 목록 화면(SPEC-011 §2.1 · 시안 `handoff/alerts/js/alerts.v1.jsx`).
 * 서버 응답은 SPEC §4.5 모양의 가짜다. 사람 이름은 가상이다. 「지금」 = 2026-10-07(수) 15:00 서울.
 */

const NOW = new Date("2026-10-07T06:00:00Z");
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function row(id: string, overrides: Partial<Notification> = {}): Notification {
  return {
    notification_id: id,
    seq: Number(id.replace(/\D/g, "")) || 1,
    kind: "work.request_received",
    theme: "work",
    item: "request",
    relation: "assignee",
    failure: false,
    actor: { member_id: "m1", display_name: "오지훈" },
    subject: { type: "task", id: `t-${id}`, title: `업무 ${id}` },
    data: {},
    target: { surface: "work", task_id: `t-${id}` },
    created_at: "2026-10-07T05:57:00Z",
    updated_at: "2026-10-07T05:57:00Z",
    read_at: null,
    ...overrides,
  };
}

const ITEMS: Notification[] = [
  row("n9"),
  row("n8", { kind: "message.slack", theme: "message", item: "slack", relation: "mention", actor: { external_name: "한서윤님" }, data: { room_name: "#pilot-launch" }, target: { surface: "inbox", source: "slack", room_id: "r1", message_id: "m7" }, created_at: "2026-10-06T09:20:00Z", updated_at: "2026-10-06T09:20:00Z" }),
  row("n7", { kind: "message.integration_lost", theme: "message", item: "mail", relation: "integration", failure: true, actor: null, data: { channel: "mail", reason: "disconnected", account: "lab@company.example" }, target: { surface: "settings", tab: "mail" }, created_at: "2026-10-05T00:00:00Z", updated_at: "2026-10-05T00:00:00Z", read_at: "2026-10-05T01:00:00Z" }),
  row("n6", { kind: "meeting.shared", theme: "meeting", item: "share", relation: "shared", subject: { type: "meeting", id: "g1", title: "협력사 미팅" }, target: { surface: "meetings", meeting_id: "g1" }, created_at: "2026-09-28T00:00:00Z", updated_at: "2026-09-28T00:00:00Z", read_at: "2026-09-28T01:00:00Z" }),
];

type Call = { path: string; method: string };
let calls: Call[] = [];
let pages: Record<string, () => Response> = {};
let summary = { unread: { all: 2, work: 1, message: 1, meeting: 0 } };

beforeEach(() => {
  calls = [];
  summary = { unread: { all: 2, work: 1, message: 1, meeting: 0 } };
  pages = { "/api/notifications": () => json({ items: ITEMS, next_cursor: null }) };
  FakeEventSource.all = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      calls.push({ path, method });
      if (path === "/api/notifications/summary") return json(summary);
      if (path === "/api/notifications/read-all") return json({ read: 2 });
      if (pages[path]) return pages[path]();
      if (path.startsWith("/api/notifications")) return json({ items: [], next_cursor: null });
      return json({ detail: "없음" }, 404);
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const onOpen = vi.fn();
const onError = vi.fn();
const onReadChanged = vi.fn();

function Harness() {
  const [actions, setActions] = useState<ReactNode>(null);
  const [titleEnd, setTitleEnd] = useState<ReactNode>(null);
  return (
    <EventStreamProvider onSessionLost={() => undefined}>
      <div data-testid="title-end">{titleEnd}</div>
      <div data-testid="actions">{actions}</div>
      <NotificationsPage
        now={NOW}
        onError={onError}
        onOpen={onOpen}
        onReadChanged={onReadChanged}
        onRegisterHeaderActions={useCallback((node: ReactNode) => setActions(node), [])}
        onRegisterTitleEnd={useCallback((node: ReactNode) => setTitleEnd(node), [])}
      />
    </EventStreamProvider>
  );
}

const readAllButton = () => within(screen.getByTestId("actions")).queryByRole("button", { name: "모두 읽음" }) as HTMLButtonElement | null;
const lineOf = (id: string) => document.querySelector(`[data-notification-id="${id}"]`) as HTMLElement;

describe("알림 화면 — 기본", () => {
  it("머리 「안 읽음 N」 · [모두 읽음] · 필터 넷(테마 탭에 안 읽은 수) · 날짜 구분 · 한 줄(표식 · 문장 · 꼬리표 · 시각 · 가는 곳 · 점)", async () => {
    render(<Harness />);
    expect(await screen.findByText("업무 n9", { exact: false })).toBeTruthy();
    await waitFor(() => expect(screen.getByTestId("title-end").textContent).toBe("안 읽음 2"));
    expect(screen.getByTestId("title-end").querySelector(".scax-badge--count")?.textContent).toBe("2");
    expect(readAllButton()?.disabled).toBe(false);
    const tabs = screen.getByRole("tablist", { name: "알림 분류" });
    expect(within(tabs).getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["전체", "업무 1", "메시지 1", "회의"]);

    // 날짜 구분 — 오늘 · 어제 · 이번 주 · 이전 (서울)
    expect(screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent)).toEqual(["오늘", "어제", "이번 주", "이전"]);
    expect(within(screen.getByRole("region", { name: "오늘" })).getByText("3분 전")).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "어제" })).getByText("어제 18:20")).toBeTruthy();

    const first = lineOf("n9");
    expect(first.classList.contains("scax-alert--unread")).toBe(true);
    expect(first.textContent).toContain("오지훈님이 ‘업무 n9’ 업무를 요청했습니다");
    expect(first.querySelector(".scax-alert__rel")?.textContent).toBe("담당");
    expect(first.querySelector(".scax-alert__mark--work")).not.toBeNull();
    expect(first.querySelector(".scax-alert__to")?.textContent).toBe("업무");
    expect(within(first).getByRole("img", { name: "안 읽음" })).toBeTruthy();
    expect(lineOf("n8").querySelector(".scax-alert__rel")?.textContent).toBe("슬랙 · 멘션");
    expect(lineOf("n8").querySelector(".scax-alert__to")?.textContent).toBe("메시지함 · 슬랙");
    // 실패 표식(붉은) — 연동 끊김
    expect(lineOf("n7").querySelector(".scax-alert__mark--fail")).not.toBeNull();
    expect(lineOf("n7").querySelector(".scax-alert__to")?.textContent).toBe("설정 · 메일 연동");
    expect(lineOf("n7").classList.contains("scax-alert--unread")).toBe(false);
    expect(lineOf("n6").querySelector(".scax-alert__rel")?.textContent).toBe("공유받음");
  });

  it("필터를 고르면 서버에 그 테마만 묻는다 · 테마 빈 문구(시안 EMPTY_BY_FILTER)", async () => {
    render(<Harness />);
    await screen.findByText("업무 n9", { exact: false });
    fireEvent.click(screen.getByRole("tab", { name: "회의" }));
    await waitFor(() => expect(calls.some((call) => call.path === "/api/notifications?theme=meeting")).toBe(true));
    expect(await screen.findByText("회의 알림이 없습니다")).toBeTruthy();
    expect(screen.getByText("회의 초대·변경·회의록 소식이 여기에 쌓입니다.")).toBeTruthy();
    // 테마가 비어도 머리는 선다 — 전체가 빈 것이 아니다
    await waitFor(() => expect(readAllButton()).not.toBeNull());
  });

  it("누르면 그 줄이 읽음으로 바뀌고 App 에 넘긴다(읽음 API · 대상 열기는 App)", async () => {
    onOpen.mockClear();
    render(<Harness />);
    await screen.findByText("업무 n9", { exact: false });
    fireEvent.click(lineOf("n8"));
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ notification_id: "n8" }));
    expect(lineOf("n8").classList.contains("scax-alert--unread")).toBe(false);
    expect(screen.getByTestId("title-end").textContent).toBe("안 읽음 1");
  });

  it("[모두 읽음] = 전부(필터와 무관) — 본문 없는 POST · 모든 줄 읽음 · 머리 숨김 · 점 다시 읽기 · 전체가 0 이면 비활성", async () => {
    onReadChanged.mockClear();
    render(<Harness />);
    await screen.findByText("업무 n9", { exact: false });
    fireEvent.click(screen.getByRole("tab", { name: "회의" }));
    await screen.findByText("회의 알림이 없습니다");
    // 회의 탭(안 읽음 0)이어도 전체에 안 읽은 것이 있으니 눌린다
    await waitFor(() => expect(readAllButton()?.disabled).toBe(false));
    fireEvent.click(readAllButton()!);
    await waitFor(() => expect(calls.some((call) => call.path === "/api/notifications/read-all" && call.method === "POST")).toBe(true));
    await waitFor(() => expect(readAllButton()?.disabled).toBe(true));
    expect(screen.getByTestId("title-end").textContent).toBe("");
    expect(onReadChanged).toHaveBeenCalled();
  });
});

describe("알림 화면 — 상태 넷", () => {
  it("빈(전체) — 「받은 알림이 없습니다」 · 머리 「안 읽음」·「모두 읽음」·필터 수를 숨긴다", async () => {
    pages["/api/notifications"] = () => json({ items: [], next_cursor: null });
    summary = { unread: { all: 0, work: 0, message: 0, meeting: 0 } };
    render(<Harness />);
    expect(await screen.findByText("받은 알림이 없습니다")).toBeTruthy();
    expect(screen.getByText("나에게 온 업무·메시지·회의 소식이 생기면 여기에 쌓입니다.")).toBeTruthy();
    expect(readAllButton()).toBeNull();
    expect(screen.getByTestId("title-end").textContent).toBe("");
  });

  it("로딩 — 날짜 제목 막대 1 + 줄 6 · 머리를 숨긴다", async () => {
    pages["/api/notifications"] = () => new Promise<Response>(() => undefined) as unknown as Response;
    render(<Harness />);
    const loading = screen.getByRole("status");
    expect(loading.querySelectorAll(".scax-alerts__skel-row")).toHaveLength(6);
    expect(loading.querySelectorAll(".scax-skeleton--title")).toHaveLength(1);
    expect(readAllButton()).toBeNull();
  });

  it("오류 — 「알림을 불러오지 못했습니다」 + [다시 시도] → 다시 읽는다", async () => {
    let fail = true;
    pages["/api/notifications"] = () => (fail ? json({ detail: "x" }, 500) : json({ items: ITEMS, next_cursor: null }));
    render(<Harness />);
    expect(await screen.findByText("알림을 불러오지 못했습니다")).toBeTruthy();
    expect(readAllButton()).toBeNull();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    expect(await screen.findByText("업무 n9", { exact: false })).toBeTruthy();
  });
});

describe("이어 불러오기 — 스크롤 끝에 닿으면 자동(D-36)", () => {
  let observed: Array<(entries: Array<{ isIntersecting: boolean }>) => void> = [];
  beforeEach(() => {
    observed = [];
    vi.stubGlobal(
      "IntersectionObserver",
      class {
        constructor(callback: (entries: Array<{ isIntersecting: boolean }>) => void) {
          observed.push(callback);
        }
        observe() {}
        disconnect() {}
      },
    );
  });
  const reachEnd = () => act(() => observed.at(-1)?.([{ isIntersecting: true }]));

  it("끝 표지가 보이면 다음 쪽(cursor)을 붙인다 · 마지막 쪽이면 표지가 없다", async () => {
    pages["/api/notifications"] = () => json({ items: ITEMS.slice(0, 2), next_cursor: "c2" });
    pages["/api/notifications?cursor=c2"] = () => json({ items: ITEMS.slice(2), next_cursor: null });
    render(<Harness />);
    await screen.findByText("업무 n9", { exact: false });
    expect(screen.getByTestId("alerts-sentinel")).toBeTruthy();
    reachEnd();
    await waitFor(() => expect(lineOf("n6")).not.toBeNull());
    expect(screen.queryByTestId("alerts-sentinel")).toBeNull();
    expect(screen.queryByRole("button", { name: /더 불러오기/ })).toBeNull(); // 단추 없음
  });

  it("실패하면 끝에 「더 불러오지 못했습니다 · 다시 시도」", async () => {
    let fail = true;
    pages["/api/notifications"] = () => json({ items: ITEMS.slice(0, 2), next_cursor: "c2" });
    pages["/api/notifications?cursor=c2"] = () => (fail ? json({ detail: "x" }, 500) : json({ items: ITEMS.slice(2), next_cursor: null }));
    render(<Harness />);
    await screen.findByText("업무 n9", { exact: false });
    reachEnd();
    expect(await screen.findByText("더 불러오지 못했습니다")).toBeTruthy();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    await waitFor(() => expect(lineOf("n6")).not.toBeNull());
  });
});

describe("실시간 — 사건 채널", () => {
  const push = (name: string, data: unknown, id = "") => act(() => FakeEventSource.latest().emit(name, data, id));

  it("새 줄은 맨 위에 끼우고 · 합친 줄은 그 자리에서 고친다 · 요약을 다시 읽는다 · 목록 전체는 다시 읽지 않는다", async () => {
    render(<Harness />);
    await screen.findByText("업무 n9", { exact: false });
    act(() => FakeEventSource.latest().ready());
    const lists = () => calls.filter((call) => call.path === "/api/notifications").length;
    const summaries = () => calls.filter((call) => call.path === "/api/notifications/summary").length;
    const [listsBefore, summariesBefore] = [lists(), summaries()];
    summary = { unread: { all: 3, work: 2, message: 1, meeting: 0 } };
    push("notification.upserted", { v: 1, notification: row("n10", { subject: { type: "task", id: "t10", title: "새 업무" } }), created: true, replayed: false }, "10");
    await waitFor(() => expect(document.querySelector(".scax-alerts__list li:first-child [data-notification-id]")?.getAttribute("data-notification-id")).toBe("n10"));
    await waitFor(() => expect(screen.getByTestId("title-end").textContent).toBe("안 읽음 3"));
    expect(summaries()).toBeGreaterThan(summariesBefore);

    const merged = row("n8", { kind: "message.slack", theme: "message", item: "slack", relation: "channel", actor: null, data: { room_name: "#pilot-launch", count: 3 }, seq: 11, created_at: "2026-10-06T09:20:00Z", updated_at: "2026-10-06T09:20:00Z" });
    push("notification.upserted", { v: 1, notification: merged, created: false, replayed: false }, "11");
    await waitFor(() => expect(lineOf("n8").textContent).toContain("새 메시지가 3건 왔습니다"));
    expect(document.querySelectorAll('[data-notification-id="n8"]')).toHaveLength(1);
    expect(lists()).toBe(listsBefore);
  });

  it("`notification.read` 반영 — 낱건 · 전부", async () => {
    render(<Harness />);
    await screen.findByText("업무 n9", { exact: false });
    push("notification.read", { v: 1, notification_ids: ["n9"] });
    await waitFor(() => expect(lineOf("n9").classList.contains("scax-alert--unread")).toBe(false));
    expect(lineOf("n8").classList.contains("scax-alert--unread")).toBe(true);
    push("notification.read", { v: 1, all: true, theme: null });
    await waitFor(() => expect(lineOf("n8").classList.contains("scax-alert--unread")).toBe(false));
  });

  it("`resync` 면 목록과 요약을 다시 읽는다", async () => {
    render(<Harness />);
    await screen.findByText("업무 n9", { exact: false });
    const lists = () => calls.filter((call) => call.path === "/api/notifications").length;
    const before = lists();
    push("resync", { v: 1, reason: "dropped" });
    await waitFor(() => expect(lists()).toBe(before + 1));
  });
});
