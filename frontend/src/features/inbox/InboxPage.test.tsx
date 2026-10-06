import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useCallback, useState, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { InboxPage } from "./InboxPage";

/* 메시지함 (WORK-011 FE-a · SPEC-008 §2.1·2.2·2.8 · AC-07~13) — 서버 응답은 SPEC §4.4 모양의 가짜다. */

const json = (body: unknown, status = 200) => new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const T = (minutes: number) => new Date(Date.UTC(2026, 9, 6, 0, minutes)).toISOString();

const LIST = {
  items: [
    { kind: "mail", message_id: "m1", integration_id: "i-mail", account: "haram@company.example", subject: "2차 파일럿 일정표", sender: "서지안 <jian@noeul.example>", at: T(10), unread: true, attach_count: 2, snippet: "일정표를 공유드립니다" },
    { kind: "slack", room_id: "r1", integration_id: "i-slack", room_type: "channel", title: "#pilot-launch", member_count: 14, unread_count: 5, last_at: T(9), at: T(9), preview: [{ author: "한서윤", text: "확인 부탁드립니다", at: T(9) }] },
    { kind: "kakao", room_id: "k1", integration_id: "i-kakao", room_type: "direct", title: "박지윤", member_count: 2, unread_count: 0, last_at: T(5), at: T(5), preview: [{ author: "박지윤", text: "사진", at: T(5) }] },
  ],
  next_cursor: null,
  unread_counts: { all: 2, mail: 1, slack: 1, kakao: 0 },
};

const MAIL = {
  message_id: "m1",
  integration_id: "i-mail",
  account: "haram@company.example",
  thread_id: "t1",
  subject: "2차 파일럿 일정표",
  sender: "서지안 <jian@noeul.example>",
  to: ["유하람 <haram@company.example>", "문다은 <daeun@company.example>"],
  cc: ["한서윤 <seoyoon@company.example>"],
  reply_to: [],
  date: null,
  at: T(10),
  unread: true,
  safe_html: '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'"><p>일정표 본문</p>',
  attachments: [
    { aid: "a1", name: "범위.pdf", size: 1200000, mime: "application/pdf", kind: "file", state: "reference" },
    { aid: "a2", name: "현장.jpg", size: 2400000, mime: "image/jpeg", kind: "image", state: "reference" },
  ],
  sent_replies: [{ local_id: "s0", status: "sent", payload: { to: ["서지안 <jian@noeul.example>"], cc: [], body: "지난 답장" }, error: null, created_at: T(1), sent_at: T(1) }],
};

const ROOM = {
  room: { room_id: "r1", integration_id: "i-slack", kind: "slack", room_type: "channel", name: "#pilot-launch", member_count: 14, external_id: "C01", read_up_to_key: null, permalink: "https://noeul.slack.com/archives/C01" },
  users: { U1: { name: "한서윤" }, U2: { name: "오지훈" }, B1: { name: "배포 알리미", is_bot: true } },
  next_cursor: null,
  messages: [
    { id: "1", key: "1.0", at: T(1), author: "U1", thread_key: null, raw: { user: "U1", text: "첫 줄 <javascript:alert(document.cookie)|미끼 링크>" }, attachments: [] },
    {
      id: "2",
      key: "2.0",
      at: T(3),
      author: "U1",
      thread_key: null,
      raw: { user: "U1", text: "이어서 <@U2> 확인" },
      attachments: [
        { aid: "img1", name: "현장.png", size: 276000, mime: "image/png", kind: "image", state: "reference" },
        { aid: "doc1", name: "범위.pdf", size: 1200, mime: "application/pdf", kind: "file", state: "reference" },
      ],
    },
    { id: "3", key: "3.0", at: T(4), author: "B1", thread_key: "3.0", raw: { bot_id: "B1", text: "배포 *완료*", reply_count: 1, reply_users: ["U2"], latest_reply: "1791245400.0" }, attachments: [] },
    { id: "4", key: "4.0", at: T(5), author: "U2", thread_key: "3.0", raw: { user: "U2", text: "스레드 답글" }, attachments: [] },
  ],
};

const THREAD = { ...ROOM, messages: [ROOM.messages[2], { ...ROOM.messages[3] }] };

const KAKAO = {
  room: { room_id: "k1", integration_id: "i-kakao", kind: "kakao", room_type: "direct", name: "박지윤", member_count: 2, external_id: "9001", read_up_to_key: null },
  next_cursor: null,
  messages: [
    {
      id: "k-1",
      key: "77",
      at: T(5),
      author: "박지윤",
      thread_key: null,
      raw: { text: "옛 사진이에요" },
      attachments: [
        { aid: "x1", name: "IMG_1.jpg", size: null, mime: "image/jpeg", kind: "image", state: "expired" },
        { aid: "x2", name: "", size: null, mime: null, kind: "video", state: "not_stored" },
      ],
    },
  ],
};

class FakeSocket {
  static all: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((message: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(public url: string) {
    FakeSocket.all.push(this);
  }
  close() {}
  emit(event: unknown) {
    act(() => this.onmessage?.({ data: JSON.stringify(event) }));
  }
}

type Call = { path: string; method: string; body: unknown; headers: Record<string, string> };
let calls: Call[] = [];
let overrides: Record<string, () => Response> = {};

function routeFetch() {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    calls.push({ path, method, body: init?.body, headers: (init?.headers ?? {}) as Record<string, string> });
    const key = `${method} ${path}`;
    if (overrides[key]) return overrides[key]();
    if (path.startsWith("/api/inbox/messages")) return json(LIST);
    if (path === "/api/integrations") return json([]);
    if (path === "/api/inbox/mail/m1") return json(MAIL);
    if (path === "/api/inbox/rooms/r1/messages") return json(ROOM);
    if (path === "/api/inbox/rooms/r1/messages?thread_ts=3.0") return json(THREAD);
    if (path === "/api/inbox/rooms/k1/messages") return json(KAKAO);
    if (path.endsWith("/reply")) return json({ local_id: "L1" }, 202);
    if (method === "POST") return json(null, 204);
    return json({ detail: "없음" }, 404);
  });
}

const noop = () => undefined;

function Harness({ onError = noop }: { onError?: (message: string | null) => void }) {
  const [rails, setRails] = useState<{ left?: ReactNode }>({});
  const [actions, setActions] = useState<ReactNode>(null);
  const registerRails = useCallback((next: { left?: ReactNode }) => setRails(next), []);
  const registerActions = useCallback((node: ReactNode) => setActions(node), []);
  return (
    <>
      <div data-testid="actions">{actions}</div>
      <div data-testid="rail">{rails.left}</div>
      <div data-testid="body">
        <InboxPage meName="유하람" onError={onError} onRegisterHeaderActions={registerActions} onRegisterRails={registerRails} />
      </div>
    </>
  );
}

const rail = () => within(screen.getByTestId("rail"));
const body = () => within(screen.getByTestId("body"));
const card = (key: string) => document.querySelector(`[data-card="${key}"]`) as HTMLElement;

beforeEach(() => {
  calls = [];
  overrides = {};
  FakeSocket.all = [];
  vi.stubGlobal("fetch", routeFetch());
  vi.stubGlobal("WebSocket", FakeSocket);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("메시지함 — 레일 · 네 상태", () => {
  it("메일은 단건, 슬랙·카톡은 방 카드 · 레일 숫자는 미읽음 카드 수 · 카드에 업무 행동이 없다", async () => {
    render(<Harness />);
    expect(await rail().findByText("2차 파일럿 일정표")).toBeTruthy();
    expect(rail().getByText("#pilot-launch")).toBeTruthy();
    expect(rail().getByText("박지윤", { selector: "h3" })).toBeTruthy();
    expect(rail().getByRole("img", { name: "안 읽음" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "메시지함" }).querySelector(".scax-gutter-list__title .scax-badge--count")?.textContent).toBe("2");
    // 그 밖에 N건 더 — 미읽음 5 중 미리보기 1줄
    expect(rail().getByText("그 밖에 4건 더")).toBeTruthy();
    for (const absent of ["내 업무로", "확인완료", "업무", "참고"]) expect(rail().queryByRole("button", { name: absent })).toBeNull();
    // 본문은 고르기 전이다
    expect(body().getByText("고른 메시지가 없습니다")).toBeTruthy();
    expect(within(screen.getByTestId("actions")).getByRole("button", { name: "모두 읽음으로" })).toBeTruthy();
  });

  it("출처 탭은 서버에 그 출처만 묻는다", async () => {
    render(<Harness />);
    await rail().findByText("#pilot-launch");
    fireEvent.click(rail().getByRole("tab", { name: "슬랙" }));
    await waitFor(() => expect(calls.some((call) => call.path === "/api/inbox/messages?source=slack")).toBe(true));
  });

  it("빈 · 출처 빈 · 오류(다시 시도)", async () => {
    overrides["GET /api/inbox/messages"] = () => json({ items: [], next_cursor: null });
    render(<Harness />);
    expect(await rail().findByText("쌓인 메시지가 없습니다")).toBeTruthy();
    overrides["GET /api/inbox/messages?source=kakao"] = () => json({ items: [], next_cursor: null });
    fireEvent.click(rail().getByRole("tab", { name: "카톡" }));
    expect(await rail().findByText("이 출처에는 메시지가 없습니다")).toBeTruthy();
    cleanup();
    overrides["GET /api/inbox/messages"] = () => json({ detail: "x" }, 500);
    render(<Harness />);
    expect(await rail().findByText("메시지함을 불러오지 못했습니다")).toBeTruthy();
    delete overrides["GET /api/inbox/messages"];
    fireEvent.click(rail().getByRole("button", { name: "다시 시도" }));
    expect(await rail().findByText("2차 파일럿 일정표")).toBeTruthy();
  });

  it("모두 읽음으로 — 지금 출처를 서버에 알리고 카드 숫자를 지운다", async () => {
    render(<Harness />);
    await rail().findByText("#pilot-launch");
    fireEvent.click(within(screen.getByTestId("actions")).getByRole("button", { name: "모두 읽음으로" }));
    await waitFor(() => expect(calls.some((call) => call.method === "POST" && call.path === "/api/inbox/read-all")).toBe(true));
    expect(rail().queryByRole("img", { name: "안 읽음" })).toBeNull();
  });

  it("수집이 끊긴 연동은 머리 [!] 배지(개수) → 팝오버에 사유와 「다시 연결」 · 본문 배너는 없다(D-50 · 피드백 1)", async () => {
    overrides["GET /api/integrations"] = () =>
      json([
        { id: "i-slack", kind: "slack", status: "disconnected", display_name: "노을웍스", synced_count: 0, last_synced_at: null, backfill_count: 0, collector: null },
        { id: "i-mail", kind: "mail", status: "disconnected", display_name: "old@company.example", synced_count: 0, last_synced_at: null, backfill_count: 0, collector: null },
        { id: "i-mail2", kind: "mail", status: "connected", display_name: "ok@company.example", synced_count: 0, last_synced_at: null, backfill_count: 0, collector: null },
      ]);
    render(<Harness />);
    const actions = within(screen.getByTestId("actions"));
    const badge = await actions.findByRole("button", { name: "연결 경고 2건" });
    expect(badge.textContent).toContain("2");
    expect(actions.getByRole("button", { name: "모두 읽음으로" })).toBeTruthy();
    expect(body().queryByRole("alert")).toBeNull();
    expect(screen.queryByText(/연결이 끊겼습니다/)).toBeNull();

    fireEvent.click(badge);
    const panel = screen.getByRole("group", { name: "연결 경고" });
    expect(within(panel).getByText("슬랙 연결이 끊겼습니다")).toBeTruthy();
    expect(within(panel).getByText("메일 old@company.example 연결이 끊겼습니다")).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("group", { name: "연결 경고" })).toBeNull());

    fireEvent.click(badge);
    fireEvent.click(within(screen.getByRole("group", { name: "연결 경고" })).getAllByRole("button", { name: "다시 연결" })[0]);
    await waitFor(() => expect(calls.some((call) => call.method === "POST" && call.path === "/api/integrations/i-slack/reconnect")).toBe(true));
    expect(screen.queryByRole("group", { name: "연결 경고" })).toBeNull();
  });

  it("경고가 없으면 배지가 없다", async () => {
    render(<Harness />);
    await rail().findByText("#pilot-launch");
    expect(within(screen.getByTestId("actions")).queryByRole("button", { name: /연결 경고/ })).toBeNull();
  });

  it("새 메시지 사건이 오면 목록을 다시 읽는다(AC-10b · 회의 WS 아님)", async () => {
    render(<Harness />);
    await rail().findByText("#pilot-launch");
    const socket = FakeSocket.all[0];
    expect(socket.url).toMatch(/\/api\/inbox\/stream$/);
    const before = calls.filter((call) => call.path.startsWith("/api/inbox/messages")).length;
    socket.emit({ v: 1, type: "inbox.message_arrived", member_id: "me", room_id: "r1" });
    await waitFor(() => expect(calls.filter((call) => call.path.startsWith("/api/inbox/messages")).length).toBeGreaterThan(before));
  });
});

describe("메일 본문 — 샌드박스 iframe · 답장", () => {
  it("HTML 은 샌드박스 iframe 에만 담긴다(allow-scripts 없음) · 머리 표 · 첨부 받기는 API 중계 · 열면 읽음", async () => {
    render(<Harness />);
    fireEvent.click(await rail().findByText("2차 파일럿 일정표"));
    const frame = (await body().findByTitle("메일 본문")) as HTMLIFrameElement;
    expect(frame.getAttribute("sandbox")).toBe("allow-same-origin allow-popups allow-popups-to-escape-sandbox");
    expect(frame.getAttribute("sandbox")).not.toContain("allow-scripts");
    // srcdoc 이동이 아니라 첫 문서에 써 넣는다(데스크톱 셸이 about:srcdoc 이동을 취소했다 — 운영 결함)
    expect(frame.hasAttribute("srcdoc")).toBe(false);
    expect(frame.contentDocument?.body.textContent).toContain("일정표 본문");
    // 앱 문서에는 원문 HTML 이 요소로 들어오지 않는다(iframe 문서 안에만 있다)
    expect(screen.queryByText("일정표 본문")).toBeNull();
    expect(body().getByText("받은 계정")).toBeTruthy();
    expect(body().getByText("haram@company.example", { selector: "dd" })).toBeTruthy();
    expect(body().getByRole("link", { name: "범위.pdf 받기" }).getAttribute("href")).toBe("/api/inbox/mail/m1/attachments/a1");
    expect(body().getByRole("region", { name: "보낸 답장" }).textContent).toContain("지난 답장");
    await waitFor(() => expect(calls.some((call) => call.method === "POST" && call.path === "/api/inbox/mail/m1/read")).toBe(true));
    await waitFor(() => expect(card("mail:m1").className).toContain("scax-inbox-card--read"));
  });

  it("전체 답장 — 받는 사람·참조 칩(나는 빠짐) · 25MB 넘는 첨부는 막는다 · 보내면 멱등 키와 함께 multipart", async () => {
    render(<Harness />);
    fireEvent.click(await rail().findByText("2차 파일럿 일정표"));
    fireEvent.click(await body().findByRole("button", { name: "전체 답장" }));
    const box = body().getByRole("region", { name: "전체 답장 쓰기" });
    expect(within(box).getByRole("button", { name: "서지안 빼기" })).toBeTruthy();
    expect(within(box).getByRole("button", { name: "문다은 빼기" })).toBeTruthy();
    expect(within(box).getByRole("button", { name: "한서윤 빼기" })).toBeTruthy();
    expect(within(box).queryByRole("button", { name: "유하람 빼기" })).toBeNull();
    expect(within(box).getByText("Re: 2차 파일럿 일정표")).toBeTruthy();

    fireEvent.change(within(box).getByRole("textbox", { name: "답장 내용" }), { target: { value: "잘 받았습니다" } });
    const big = new File(["x"], "도면.pdf", { type: "application/pdf" });
    Object.defineProperty(big, "size", { value: 26 * 1024 * 1024 });
    fireEvent.change(within(box).getByLabelText("파일 추가"), { target: { files: [big] } });
    expect(within(box).getByText("25MB 를 넘어 붙지 않습니다")).toBeTruthy();
    expect((within(box).getByRole("button", { name: "보내기" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(within(box).getByRole("button", { name: "도면.pdf 빼기" }));

    fireEvent.click(within(box).getByRole("button", { name: "보내기" }));
    await waitFor(() => expect(calls.some((call) => call.path === "/api/inbox/mail/m1/reply")).toBe(true));
    const sent = calls.find((call) => call.path === "/api/inbox/mail/m1/reply")!;
    expect(sent.headers["Idempotency-Key"]).toBeTruthy();
    const form = sent.body as FormData;
    expect(form.get("reply_all")).toBe("true");
    expect(form.get("body")).toBe("잘 받았습니다");
    expect(form.getAll("to")).toEqual(["서지안 <jian@noeul.example>", "문다은 <daeun@company.example>"]);
    expect(form.getAll("cc")).toEqual(["한서윤 <seoyoon@company.example>"]);
    expect(within(box).getByRole("button", { name: "보내는 중…" })).toBeTruthy();

    // 결과는 사용자 사건으로 온다 — 실패면 칸 안에 「다시 보내기」, 같은 키로 다시 보낸다
    FakeSocket.all[0].emit({ v: 1, type: "inbox.reply_result", member_id: "me", data: { local_id: "L1", status: "failed" } });
    expect(await within(box).findByText("답장을 보내지 못했습니다")).toBeTruthy();
    fireEvent.click(within(box).getByRole("button", { name: "다시 보내기" }));
    await waitFor(() => expect(calls.filter((call) => call.path === "/api/inbox/mail/m1/reply")).toHaveLength(2));
    const [first, second] = calls.filter((call) => call.path === "/api/inbox/mail/m1/reply");
    expect(second.headers["Idempotency-Key"]).toBe(first.headers["Idempotency-Key"]);

    FakeSocket.all[0].emit({ v: 1, type: "inbox.reply_result", member_id: "me", data: { local_id: "L1", status: "sent" } });
    await waitFor(() => expect(body().queryByRole("region", { name: "전체 답장 쓰기" })).toBeNull());
  });
});

describe("슬랙·카톡 본문 — 대화방 · 스레드 3열 · 조회 전용", () => {
  it("5분 묶음 · 멘션 이름 · 봇 「앱」 · 스레드 답글은 본문에서 빠지고 오른쪽 패널에 선다 · 슬랙에서 열기", async () => {
    render(<Harness />);
    fireEvent.click(await rail().findByText("#pilot-launch"));
    expect(await body().findByText(/첫 줄/)).toBeTruthy();
    expect(document.querySelectorAll(".scax-imsg--grouped")).toHaveLength(1);
    expect(body().getByText("@오지훈")).toBeTruthy();
    expect(body().getByText("앱")).toBeTruthy();
    expect(body().queryByText("스레드 답글")).toBeNull();
    expect(body().getByRole("link", { name: "슬랙에서 열기" })).toBeTruthy();
    await waitFor(() => expect(calls.some((call) => call.path === "/api/inbox/rooms/r1/read" && JSON.parse(String(call.body)).up_to_ts === "4.0")).toBe(true));

    fireEvent.click(body().getByRole("button", { name: /답글 1개/ }));
    const panel = await body().findByRole("complementary", { name: "스레드 · #pilot-launch" });
    expect(await within(panel).findByText("스레드 답글")).toBeTruthy();
    expect(within(panel).getByRole("textbox", { name: "답글 달기…" })).toBeTruthy();
    expect(document.querySelector(".scax-inbox-main--thread")).not.toBeNull();
    fireEvent.click(within(panel).getByRole("button", { name: "스레드 닫기" }));
    expect(body().queryByRole("complementary")).toBeNull();
  });

  it("방 안 이미지는 썸네일(?variant=thumb)로 그리고, 누르기·받기는 원본(FE 수정 판 5)", async () => {
    render(<Harness />);
    fireEvent.click(await rail().findByText("#pilot-launch"));
    const image = (await body().findByAltText("현장.png")) as HTMLImageElement;
    expect(image.getAttribute("src")).toBe("/api/inbox/rooms/r1/attachments/img1?variant=thumb");
    expect(image.closest("a")?.getAttribute("href")).toBe("/api/inbox/rooms/r1/attachments/img1");
    expect(body().getByRole("link", { name: "범위.pdf 받기" }).getAttribute("href")).toBe("/api/inbox/rooms/r1/attachments/doc1");
  });

  it("javascript: 링크는 링크로 서지 않고 눌러도 새 창이 열리지 않는다(검수 F-1)", async () => {
    const open = vi.fn();
    vi.stubGlobal("open", open);
    render(<Harness />);
    fireEvent.click(await rail().findByText("#pilot-launch"));
    const bait = await body().findByText(/미끼 링크/);
    expect(bait.closest("a")).toBeNull();
    fireEvent.click(bait);
    expect(open).not.toHaveBeenCalled();
    // 안전한 퍼머링크는 새 탭(noopener,noreferrer)으로 연다
    fireEvent.click(body().getByRole("link", { name: "슬랙에서 열기" }));
    await waitFor(() => expect(open).toHaveBeenCalledWith("https://noeul.slack.com/archives/C01", "_blank", "noopener,noreferrer"));
  });

  it("보내기 — 내 이름으로 바로 서고 「보내는 중」 · 실패 사건이면 「다시 보내기」는 같은 멱등 키로", async () => {
    render(<Harness />);
    fireEvent.click(await rail().findByText("#pilot-launch"));
    const input = await body().findByRole("textbox", { name: "#pilot-launch에 메시지 보내기" });
    fireEvent.change(input, { target: { value: "3장 수정본 올립니다" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(await body().findByText("3장 수정본 올립니다")).toBeTruthy();
    expect(body().getByText("유하람")).toBeTruthy();
    expect(body().getByText("보내는 중…")).toBeTruthy();
    await waitFor(() => expect(calls.some((call) => call.path === "/api/inbox/rooms/r1/reply")).toBe(true));
    const first = calls.find((call) => call.path === "/api/inbox/rooms/r1/reply")!;
    expect((first.body as FormData).get("text")).toBe("3장 수정본 올립니다");

    FakeSocket.all[0].emit({ v: 1, type: "inbox.reply_result", member_id: "me", data: { local_id: "L1", status: "failed" } });
    fireEvent.click(await body().findByRole("button", { name: "다시 보내기" }));
    await waitFor(() => expect(calls.filter((call) => call.path === "/api/inbox/rooms/r1/reply")).toHaveLength(2));
    const second = calls.filter((call) => call.path === "/api/inbox/rooms/r1/reply")[1];
    expect(second.headers["Idempotency-Key"]).toBe(first.headers["Idempotency-Key"]);
  });

  it("카톡은 조회 전용 — 입력창·스레드·외부 열기가 없고, 만료 첨부는 「만료됨」 · 동영상은 칩", async () => {
    render(<Harness />);
    fireEvent.click(await rail().findByText("박지윤", { selector: "h3" }));
    expect(await body().findByText("옛 사진이에요")).toBeTruthy();
    expect(body().getByText(/조회 전용 · Mac 앱에서 수집/)).toBeTruthy();
    expect(body().queryByRole("textbox")).toBeNull();
    expect(body().queryByRole("link", { name: "슬랙에서 열기" })).toBeNull();
    expect(body().getByText("사진 · 만료됨")).toBeTruthy();
    expect(body().queryByRole("link", { name: /IMG_1.jpg 받기/ })).toBeNull();
    expect(body().getByText("받지 않음 — 카카오톡에서 보기")).toBeTruthy();
  });
});
