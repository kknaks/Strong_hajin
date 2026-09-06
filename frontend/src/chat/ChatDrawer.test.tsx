import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Conversation, ConversationTurn } from "../viewModels";
import { ChatDrawer } from "./ChatDrawer";
import { AssistantMarkdown } from "./AssistantMarkdown";
import { ExecutionRail, MessageList } from "./MessageList";
import type { LocalFragment } from "./useConversations";

vi.mock("../api", () => ({
  cancelConversation: vi.fn(),
  createConversation: vi.fn(),
  decideAction: vi.fn(),
  getConversation: vi.fn(),
  getConversations: vi.fn(),
  retryConversationTurn: vi.fn(),
  sendConversationMessage: vi.fn(),
}));

import * as api from "../api";
import { useConversations } from "./useConversations";

const turn = (id: string, extra: Partial<ConversationTurn> = {}): ConversationTurn => ({
  turn_id: id,
  state: "completed",
  progress_state: "completed",
  provider_run_ref: null,
  provider_session_ref: null,
  error: null,
  queued_at: "2026-09-04T00:00:00Z",
  execution_started_at: "2026-09-04T00:00:01Z",
  execution_completed_at: "2026-09-04T00:00:13Z",
  queue_wait_ms: 1000,
  run_ms: 12300,
  ...extra,
});

function conversation(id: string, title: string, firstMessage: string, extra: Partial<Conversation> = {}): Conversation {
  return {
    conversation_id: id,
    title,
    version: 1,
    messages: [{ message_id: `${id}-m1`, turn_id: `${id}-t1`, role: "user", body: firstMessage, sequence: 1, state: "accepted", idempotency_key: `${id}-k1` }],
    turns: [turn(`${id}-t1`)],
    context_references: [],
    tool_invocations: [],
    ...extra,
  } as Conversation;
}

const noop = async () => undefined;

function renderDrawer(overrides: Partial<Parameters<typeof ChatDrawer>[0]> = {}) {
  const conversations = [
    conversation("c1", "견적 검토", "견적서 납기일을 알려줘"),
    conversation("c2", "새 대화", "오늘 할 일을 정리해줘"),
    conversation("c3", "보고 초안", "일일보고 초안을 만들어줘"),
  ];
  const props: Parameters<typeof ChatDrawer>[0] = {
    personaName: "민아 (구성원)",
    surfaceLabel: "내 업무",
    conversations,
    activeConversation: conversations[0],
    listStatus: "ready",
    isProcessing: false,
    localFragments: [],
    message: "",
    selectedContext: undefined,
    onMessageChange: vi.fn(),
    onClearContext: vi.fn(),
    onClose: vi.fn(),
    onStart: vi.fn(),
    onSelect: vi.fn(),
    onSend: vi.fn(),
    onCancel: vi.fn(),
    onDecide: noop,
    onRetryTurn: vi.fn(),
    onRetryFragment: vi.fn(),
    onDiscardFragment: vi.fn(),
    onRetryList: vi.fn(),
    ...overrides,
  };
  return { ...render(<ChatDrawer {...props} />), props };
}

describe("찾아본 연결", () => {
  afterEach(cleanup);

  it("shows the steps a turn actually walked, and nothing when it walked none", async () => {
    const base = conversation("c9", "관계 질문", "이 업무가 어디서 왔는지 알려줘");
    const walked = {
      ...base,
      graph_receipts: [
        { receipt_id: "r1", turn_id: base.turns[0].turn_id, sequence: 1, kind: "node" as const, node_ref: "task:t1", node_title: "분기 마감", observed_at: "2026-09-06T00:00:00Z" },
        {
          receipt_id: "r2",
          turn_id: base.turns[0].turn_id,
          sequence: 2,
          kind: "edge" as const,
          edge_kind: "produced",
          from_ref: "work_request:r1",
          from_title: "분기 마감 요청",
          to_ref: "task:t1",
          to_title: "분기 마감",
          observed_at: "2026-09-06T00:00:01Z",
        },
      ],
    };
    const { container, rerender } = render(
      <MessageList
        conversation={walked as never}
        localFragments={[]}
        onDecide={vi.fn()}
        onDiscardFragment={vi.fn()}
        onRetryFragment={vi.fn()}
        onRetryTurn={vi.fn()}
      />,
    );
    // The path is how the answer was found, so it lives in the execution rail — folded away once the turn is done.
    const rail = container.querySelector(".ax-rail") as HTMLElement;
    const path = rail.querySelector("section.ax-search-path") as HTMLElement;
    expect(path.textContent).toContain("분기 마감 요청 → 분기 마감");
    expect(path.textContent).toContain("만든 업무");
    // The picture of the answer stays with the answer.
    expect(container.querySelector("section.ax-turn-graph")).not.toBeNull();

    rerender(<MessageList
        conversation={base as never}
        localFragments={[]}
        onDecide={vi.fn()}
        onDiscardFragment={vi.fn()}
        onRetryFragment={vi.fn()}
        onRetryTurn={vi.fn()}
      />);
    expect(container.querySelector("section.ax-search-path")).toBeNull();
    expect(container.querySelector("section.ax-turn-graph")).toBeNull();
  });
});

describe("ChatDrawer session switcher", () => {
  afterEach(cleanup);

  it("lists conversations vertically and filters them by title or first message", () => {
    renderDrawer();
    expect(within(screen.getByRole("list")).getAllByRole("button")).toHaveLength(3);
    expect(screen.getByRole("button", { name: "견적 검토" }).getAttribute("aria-pressed")).toBe("true");

    fireEvent.change(screen.getByLabelText("대화 검색"), { target: { value: "일일보고" } });
    const buttons = within(screen.getByRole("list")).getAllByRole("button");
    expect(buttons).toHaveLength(1);
    expect(buttons[0].getAttribute("aria-label")).toBe("보고 초안");

    fireEvent.change(screen.getByLabelText("대화 검색"), { target: { value: "없는 대화" } });
    expect(screen.getByText("검색 결과가 없습니다.")).toBeTruthy();
  });

  it("distinguishes first-load, empty, and error states without hiding the new-conversation action", () => {
    const { rerender, props } = renderDrawer({ conversations: [], activeConversation: null, listStatus: "loading" });
    expect(screen.getByText("대화를 불러오는 중…")).toBeTruthy();
    expect(screen.getByRole("button", { name: "새 AX 대화" })).toBeTruthy();

    rerender(<ChatDrawer {...props} conversations={[]} activeConversation={null} listStatus="error" />);
    fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    expect(props.onRetryList).toHaveBeenCalledTimes(1);

    rerender(<ChatDrawer {...props} conversations={[]} activeConversation={null} listStatus="ready" />);
    expect(screen.getByText("아직 대화가 없습니다. 새 대화로 시작하세요.")).toBeTruthy();
    expect(screen.getByPlaceholderText("먼저 새 대화를 만들어 주세요")).toBeTruthy();
  });

  it("keeps the composer usable while a turn runs, auto-grows it, and shows the queueing send label", () => {
    renderDrawer({ isProcessing: true, message: "이어서 질문" });
    const composer = screen.getByLabelText("AX 메시지") as HTMLTextAreaElement;
    expect(composer.hasAttribute("disabled")).toBe(false);
    expect(composer.style.height).not.toBe("");
    expect(screen.getByRole("button", { name: "대기열에 보내기" }).hasAttribute("disabled")).toBe(false);
    expect(screen.getByRole("button", { name: "실행 취소" })).toBeTruthy();
  });
});

describe("ExecutionRail", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("announces only the phase and tool changes; the ticking elapsed time is hidden from assistive tech", () => {
    vi.stubGlobal("matchMedia", () => ({ matches: true }) as MediaQueryList);
    const running = turn("t1", { state: "running", progress_state: "tool_running", current_tool_display_name: "task list", execution_completed_at: null, run_ms: null, attempt: 1 });
    const tools: Conversation["tool_invocations"] = [
      { turn_id: "t1", sequence: 1, provider_call_id: "c1", tool_name: "task_list", display_name: "task list", input_summary: "입력 없음", state: "running", result_summary: null, error_summary: null, latency_ms: null, started_at: new Date(Date.now() - 2500).toISOString(), completed_at: null, target_resource_id: null, target_resource_version: null, audit_ref: null },
    ];
    const { container } = render(<ExecutionRail tools={tools} turn={running} />);
    const rail = container.querySelector(".ax-rail") as HTMLElement;
    expect(rail.getAttribute("aria-live")).toBeNull(); // the whole rail is not a live region
    expect(rail.getAttribute("data-progress")).toBe("tool_running");
    expect(rail.getAttribute("data-motion")).toBe("reduced");
    expect(container.querySelector(".ax-rail-icon.spin")).toBeNull(); // no spinner under prefers-reduced-motion
    const phrase = container.querySelector(".ax-rail-phrase") as HTMLElement;
    expect(phrase.getAttribute("aria-live")).toBe("polite");
    expect(phrase.textContent).toBe("도구 실행 중 · task list");
    expect((container.querySelector(".ax-rail-elapsed") as HTMLElement).getAttribute("aria-hidden")).toBe("true");
    expect((container.querySelector(".ax-rail-tools") as HTMLElement).getAttribute("aria-live")).toBe("polite");
    const time = container.querySelector(".ax-rail-tool-time") as HTMLElement;
    expect(time.getAttribute("aria-hidden")).toBe("true");
    expect(time.textContent).toMatch(/s$/); // elapsed since the observed start
    expect(within(rail).getByText("task list")).toBeTruthy();
    expect(within(rail).getByText("실행 중")).toBeTruthy();
  });

  it("does not show a tool duration when the start was never observed", () => {
    const done = turn("t1");
    const tools: Conversation["tool_invocations"] = [
      { turn_id: "t1", sequence: 1, provider_call_id: "c1", tool_name: "task_get", display_name: "task get", input_summary: "입력: task_id=1", state: "completed", result_summary: "결과: state=open", error_summary: null, latency_ms: null, started_at: null, completed_at: "2026-09-04T00:00:05Z", target_resource_id: null, target_resource_version: null, audit_ref: null },
    ];
    const { container } = render(<ExecutionRail tools={tools} turn={done} />);
    expect(container.querySelector(".ax-rail-tool-time")).toBeNull();
  });

  it("collapses into a one-line summary once terminal and offers retry for failures", () => {
    const onRetry = vi.fn();
    const failed = turn("t1", { state: "failed", progress_state: "failed", error: "provider failed" });
    const { container } = render(<ExecutionRail onRetry={onRetry} tools={[]} turn={failed} />);
    const rail = container.querySelector(".ax-rail") as HTMLElement;
    expect(rail.className).toContain("terminal");
    expect(within(rail).getByText("✕ 실패")).toBeTruthy();
    expect((container.querySelector(".ax-rail-timings") as HTMLElement).textContent).toBe("· 실행 12s · 대기 1s");
    expect((container.querySelector(".ax-rail-timings") as HTMLElement).getAttribute("aria-hidden")).toBe("true");
    expect(within(rail).getByText("provider failed")).toBeTruthy();
    expect((rail.querySelector("details") as HTMLDetailsElement).open).toBe(false);
    fireEvent.click(within(rail).getByRole("button", { name: "다시 시도" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    cleanup();
    render(<ExecutionRail tools={[]} turn={turn("t2")} />);
    expect(screen.getByText("✓ 완료")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "다시 시도" })).toBeNull();
  });
});

describe("MessageList", () => {
  afterEach(cleanup);

  const listProps = { onDecide: noop, onRetryTurn: vi.fn(), onRetryFragment: vi.fn(), onDiscardFragment: vi.fn() };

  it("orders a turn as request → rail → answer and keeps partial text with its terminal state", () => {
    const active = conversation("c1", "견적 검토", "첫 발화", {
      messages: [
        { message_id: "m1", turn_id: "t1", role: "user", body: "첫 발화", sequence: 1, state: "accepted" },
        { message_id: "m2", turn_id: "t1", role: "assistant", body: "부분 답변", sequence: 2, state: "accepted", body_state: "cancelled" },
      ],
      turns: [turn("t1", { state: "cancelled", progress_state: "cancelled" })],
    });
    const { container } = render(<MessageList {...listProps} conversation={active} localFragments={[]} />);
    const order = [...container.querySelectorAll(".ax-turn > *")].map((element) => element.className.split(" ")[0]);
    expect(order).toEqual(["user", "ax-rail", "assistant"]);
    expect(screen.getByText("부분 답변")).toBeTruthy();
    expect(screen.getByText("취소 시점까지의 답변")).toBeTruthy();
    expect(screen.getByText("⊘ 취소됨")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    expect(listProps.onRetryTurn).toHaveBeenCalledWith("t1");
  });

  it("renders a streaming assistant body ahead of the final answer and hides retry once a retry turn exists", () => {
    const active = conversation("c1", "견적 검토", "첫 발화", {
      messages: [
        { message_id: "m1", turn_id: "t1", role: "user", body: "첫 발화", sequence: 1, state: "accepted" },
        { message_id: "m2", turn_id: "t1", role: "assistant", body: "", sequence: 2, state: "accepted", body_state: "failed" },
        { message_id: "m3", turn_id: "t2", role: "user", body: "첫 발화", sequence: 3, state: "accepted" },
        { message_id: "m4", turn_id: "t2", role: "assistant", body: "작성 중인 답", sequence: 4, state: "accepted", body_state: "streaming" },
      ],
      turns: [turn("t1", { state: "failed", progress_state: "failed", error: "boom" }), turn("t2", { state: "running", progress_state: "composing", retry_of_turn_id: "t1", execution_completed_at: null, run_ms: null })],
    });
    render(<MessageList {...listProps} conversation={active} localFragments={[]} />);
    expect(screen.queryByRole("button", { name: "다시 시도" })).toBeNull(); // a retry already exists
    expect(screen.getByText("이전 실패한 요청의 다시 시도")).toBeTruthy();
    const streaming = screen.getByText("작성 중인 답").closest(".assistant") as HTMLElement;
    expect(streaming.getAttribute("data-body-state")).toBe("streaming");
    expect(screen.getByText("답변 작성 중")).toBeTruthy();
  });

  it("renders only the server-provided approval commands on the canonical action card", () => {
    const onDecide = vi.fn(async () => undefined);
    const withAction = conversation("c1", "견적 검토", "업무 요청 만들어줘", {
      actions: [
        { action_id: "a1", conversation_id: "c1", turn_id: "c1-t1", action_type: "work_request.create", title: "업무 요청 생성 확인", state: "pending", version: 1, payload_summary: "업무 요청: 검토", result: null, audit_ref: null, commands: [{ id: "approve", label: "승인", tone: "primary" }, { id: "reject", label: "거절", tone: "neutral" }] },
        { action_id: "a2", conversation_id: "c1", turn_id: "c1-t1", action_type: "task.update", title: "업무 수정 확인", state: "pending", version: 1, payload_summary: "업무 수정", result: null, audit_ref: null, commands: [] },
      ],
    });
    render(<MessageList {...listProps} conversation={withAction} localFragments={[]} onDecide={onDecide} />);
    const first = document.querySelector('.ax-action-card[data-action-id="a1"]') as HTMLElement;
    const second = document.querySelector('.ax-action-card[data-action-id="a2"]') as HTMLElement;
    expect(within(first).getAllByRole("button").map((button) => button.textContent)).toEqual(["승인", "거절"]);
    expect(within(second).queryAllByRole("button")).toHaveLength(0); // no inferred controls
    fireEvent.click(within(first).getByRole("button", { name: "거절" }));
    expect(onDecide).toHaveBeenCalledWith("a1", 1, "reject");
  });

  it("shows queued fragments in order and a failed local fragment with retry and discard", () => {
    const active = conversation("c1", "견적 검토", "첫 발화", {
      messages: [
        { message_id: "m1", turn_id: "t1", role: "user", body: "첫 발화", sequence: 1, state: "accepted" },
        { message_id: "m2", turn_id: null, role: "user", body: "둘째 발화", sequence: 2, state: "queued" },
        { message_id: "m3", turn_id: null, role: "user", body: "셋째 발화", sequence: 3, state: "queued" },
      ],
      turns: [turn("t1", { state: "running", progress_state: "preparing", execution_completed_at: null, run_ms: null })],
    });
    const failed: LocalFragment = { local_id: "l1", conversation_id: "c1", body: "네 번째 발화", state: "failed", error: "네트워크 오류", context: [], idempotency_key: "k1" };
    const onRetryFragment = vi.fn();
    const onDiscardFragment = vi.fn();
    render(<MessageList {...listProps} conversation={active} localFragments={[failed]} onDiscardFragment={onDiscardFragment} onRetryFragment={onRetryFragment} />);
    const queue = screen.getByRole("list", { name: "대기열" });
    expect(within(queue).getAllByRole("listitem").map((item) => item.textContent)).toEqual(["대기 1둘째 발화 대기 중", "대기 2셋째 발화 대기 중"]);
    fireEvent.click(screen.getByRole("button", { name: "다시 보내기" }));
    expect(onRetryFragment).toHaveBeenCalledWith(failed);
    fireEvent.click(screen.getByRole("button", { name: "삭제" }));
    expect(onDiscardFragment).toHaveBeenCalledWith("l1");
  });

  it("offers a new-message jump instead of stealing the scroll position when the reader is above the bottom", () => {
    const first = conversation("c1", "견적 검토", "첫 발화");
    const { rerender, container } = render(<MessageList {...listProps} conversation={first} localFragments={[]} />);
    const scroller = container.querySelector(".ax-messages") as HTMLDivElement;
    Object.defineProperty(scroller, "scrollHeight", { configurable: true, value: 1000 });
    Object.defineProperty(scroller, "clientHeight", { configurable: true, value: 300 });
    scroller.scrollTop = 0;
    fireEvent.scroll(scroller);
    const second = { ...first, version: 2, messages: [...first.messages, { message_id: "m2", turn_id: "c1-t1", role: "assistant" as const, body: "답변입니다", sequence: 2, state: "accepted" as const }] };
    rerender(<MessageList {...listProps} conversation={second} localFragments={[]} />);
    expect(scroller.scrollTop).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: "↓ 새 메시지" }));
    expect(scroller.scrollTop).toBe(1000);
  });

  it("treats partial-text growth and new tool receipts as new content even when the progress state is unchanged", () => {
    const running = conversation("c1", "견적 검토", "첫 발화", {
      messages: [
        { message_id: "m1", turn_id: "c1-t1", role: "user", body: "첫 발화", sequence: 1, state: "accepted" },
        { message_id: "m2", turn_id: "c1-t1", role: "assistant", body: "부분", sequence: 2, state: "accepted", body_state: "streaming" },
      ],
      turns: [turn("c1-t1", { state: "running", progress_state: "composing", execution_completed_at: null, run_ms: null })],
    });
    const { rerender, container } = render(<MessageList {...listProps} conversation={running} localFragments={[]} />);
    const scroller = container.querySelector(".ax-messages") as HTMLDivElement;
    Object.defineProperty(scroller, "scrollHeight", { configurable: true, value: 1000 });
    Object.defineProperty(scroller, "clientHeight", { configurable: true, value: 300 });
    scroller.scrollTop = 0;
    fireEvent.scroll(scroller);
    // Same progress state, longer partial body → new content.
    rerender(<MessageList {...listProps} conversation={{ ...running, messages: [running.messages[0], { ...running.messages[1], body: "부분 답변이 더 길어짐" }] }} localFragments={[]} />);
    expect(screen.getByRole("button", { name: "↓ 새 메시지" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "↓ 새 메시지" }));
    // Same progress state, a tool receipt appears → new content again.
    const tool = { turn_id: "c1-t1", sequence: 1, provider_call_id: "c", tool_name: "task_list", display_name: "task list", input_summary: "입력 없음", state: "running", result_summary: null, error_summary: null, latency_ms: null, started_at: null, completed_at: null, target_resource_id: null, target_resource_version: null, audit_ref: null };
    scroller.scrollTop = 0;
    fireEvent.scroll(scroller);
    rerender(<MessageList {...listProps} conversation={{ ...running, tool_invocations: [tool] }} localFragments={[]} />);
    expect(screen.getByRole("button", { name: "↓ 새 메시지" })).toBeTruthy();
  });
});

describe("useConversations", () => {
  const onError = vi.fn(); // stable like App's callback; a fresh function per render would re-run the load effect forever
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("keeps an optimistic fragment until the server projection carries its idempotency key", async () => {
    const base = conversation("c1", "새 대화", "이전 발화");
    vi.mocked(api.getConversations).mockResolvedValue([base]);
    vi.mocked(api.sendConversationMessage).mockResolvedValue({ conversation_id: "c1", message_id: "m9", turn_id: "t9", queued: false, queue_size: 0 });
    let projection = base;
    vi.mocked(api.getConversation).mockImplementation(async () => projection);
    const { result } = renderHook(() => useConversations({ personaId: "mina", isOpen: true, onError }));
    await waitFor(() => expect(result.current.activeConversation?.conversation_id).toBe("c1"));

    await act(async () => {
      await result.current.send("c1", "새 질문", []);
    });
    // 202 accepted, but the projection does not carry the key yet: the row stays, marked accepted.
    expect(result.current.localFragments.map((item) => [item.body, item.state])).toEqual([["새 질문", "accepted"]]);
    const key = vi.mocked(api.sendConversationMessage).mock.calls[0][3];
    projection = { ...base, version: 2, messages: [...base.messages, { message_id: "m9", turn_id: "t9", role: "user", body: "새 질문", sequence: 2, state: "accepted", idempotency_key: key }] };
    // Adopting the projection directly (as the poller does) must never expose both rows, not even for one render.
    act(() => result.current.adopt(projection));
    expect(result.current.localFragments).toEqual([]); // converged onto the server row, no duplicate
    expect(result.current.activeConversation?.messages.filter((item) => item.body === "새 질문")).toHaveLength(1);
  });

  it("keeps an unsent draft through a reload, and forgets it once the conversation is left behind", () => {
    window.localStorage.clear();
    const first = renderHook(() => useConversations({ personaId: "mina", isOpen: true, onError: vi.fn() }));
    act(() => first.result.current.setDraft("돌아와도 남는 초안", "c1"));

    // A fresh mount — the same person, the same browser — starts with what they had typed.
    const second = renderHook(() => useConversations({ personaId: "mina", isOpen: true, onError: vi.fn() }));
    expect(JSON.parse(window.localStorage.getItem("scax.ax.drafts") ?? "{}")).toEqual({ c1: "돌아와도 남는 초안" });
    act(() => second.result.current.setDraft("", "c1"));
    expect(JSON.parse(window.localStorage.getItem("scax.ax.drafts") ?? "{}")).toEqual({});
  });

  it("keeps one unsent draft per conversation and moves a pre-conversation draft onto the created session", async () => {
    const first = conversation("c1", "첫 대화", "a");
    const second = conversation("c2", "둘째 대화", "b");
    vi.mocked(api.getConversations).mockResolvedValue([first, second]);
    vi.mocked(api.createConversation).mockResolvedValue(conversation("c3", "새 대화", "", { messages: [], turns: [] }));
    const { result } = renderHook(() => useConversations({ personaId: "mina", isOpen: true, onError }));
    await waitFor(() => expect(result.current.activeConversation?.conversation_id).toBe("c1"));

    act(() => result.current.setDraft("첫 대화에 쓰던 글"));
    act(() => result.current.select(second));
    expect(result.current.draft).toBe("");
    act(() => result.current.setDraft("둘째 대화 초안"));
    act(() => result.current.select(first));
    expect(result.current.draft).toBe("첫 대화에 쓰던 글");
    act(() => result.current.select(second));
    expect(result.current.draft).toBe("둘째 대화 초안");

    // A draft typed before any conversation exists follows the newly created one.
    act(() => result.current.reset());
    act(() => result.current.setDraft("아직 대화 없음"));
    await act(async () => {
      await result.current.start();
    });
    expect(result.current.activeConversation?.conversation_id).toBe("c3");
    expect(result.current.draft).toBe("아직 대화 없음");
  });
});

describe("AssistantMarkdown", () => {
  const listProps = { onDecide: noop, onRetryTurn: vi.fn(), onRetryFragment: vi.fn(), onDiscardFragment: vi.fn() };
  afterEach(cleanup);

  it("renders emphasis, lists, links, and code semantically instead of showing the syntax", () => {
    const body = "현재 내 업무는 **0개**입니다.\n\n- 첫째 `task_list`\n- 둘째\n\n1. 하나\n2. 둘\n\n[업무 보기](https://scax.example/tasks)\n\n```\nselect 1\n```";
    const { container } = render(<AssistantMarkdown body={body} />);
    expect(container.textContent).not.toContain("**");
    expect(screen.getByText("0개").tagName).toBe("STRONG");
    expect(container.querySelectorAll("ul > li")).toHaveLength(2);
    expect(container.querySelectorAll("ol > li")).toHaveLength(2);
    expect(screen.getByText("task_list").tagName).toBe("CODE");
    const link = screen.getByRole("link", { name: "업무 보기" }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("https://scax.example/tasks");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(container.querySelector("pre > code")?.textContent).toContain("select 1");
  });

  it("never renders injected HTML or script-bearing links", () => {
    const onerror = vi.fn();
    vi.stubGlobal("__pwned", onerror);
    const body = 'before <img src="x" onerror="window.__pwned()"> <script>window.__pwned()</script> after\n\n<a href="javascript:window.__pwned()">click</a>\n\n[js](javascript:window.__pwned()) [ok](https://example.com)\n\n<div onclick="window.__pwned()">tag</div>';
    const { container } = render(<AssistantMarkdown body={body} />);
    expect(container.querySelector("img, script, div[onclick], [onerror], [onclick]")).toBeNull();
    for (const anchor of Array.from(container.querySelectorAll("a"))) {
      expect(anchor.getAttribute("href") ?? "").not.toMatch(/^javascript:/i);
    }
    expect(screen.getByRole("link", { name: "ok" }).getAttribute("href")).toBe("https://example.com");
    expect(container.textContent).toContain("before");
    expect(container.textContent).toContain("after");
    expect(container.innerHTML).not.toContain("<script");
    expect(onerror).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it("keeps the streaming caret and the body-state note outside the Markdown content", () => {
    const streaming = conversation("c1", "새 대화", "질문", {
      messages: [
        { message_id: "m1", turn_id: "c1-t1", role: "user", body: "질문", sequence: 1, state: "accepted" },
        { message_id: "m2", turn_id: "c1-t1", role: "assistant", body: "**부분** 답변", sequence: 2, state: "accepted", body_state: "streaming" },
      ],
      turns: [turn("c1-t1", { state: "running", progress_state: "composing", execution_completed_at: null, run_ms: null })],
    });
    const { container } = render(<MessageList {...listProps} conversation={streaming} localFragments={[]} />);
    const bubble = container.querySelector(".assistant[data-body-state='streaming']") as HTMLElement;
    expect(bubble.querySelector(".ax-md strong")?.textContent).toBe("부분");
    expect(bubble.querySelector(".ax-md .ax-streaming-mark")).toBeNull();
    expect(bubble.querySelector(":scope > .ax-streaming-mark")).toBeTruthy();
    const cancelled = { ...streaming, messages: [streaming.messages[0], { ...streaming.messages[1], body_state: "cancelled" as const }], turns: [turn("c1-t1", { state: "cancelled", progress_state: "cancelled" })] };
    cleanup();
    const second = render(<MessageList {...listProps} conversation={cancelled} localFragments={[]} />).container;
    const note = second.querySelector(".assistant .ax-body-note") as HTMLElement;
    expect(note.textContent).toBe("취소 시점까지의 답변");
    expect(note.closest(".ax-md")).toBeNull();
  });
});

describe("ActionResultCard", () => {
  afterEach(cleanup);
  const listProps = { onDecide: noop, onRetryTurn: vi.fn(), onRetryFragment: vi.fn(), onDiscardFragment: vi.fn() };

  it("titles the card with the real work title, shows the server operation kicker, and lists only server preview rows", () => {
    const pending = conversation("c1", "새 대화", "업무 요청을 만들어줘", {
      actions: [
        {
          action_id: "a1",
          conversation_id: "c1",
          turn_id: "c1-t1",
          action_type: "work_request.create",
          title: "업무 요청 생성 확인",
          subject: "견적서 재검토",
          operation_label: "업무 요청",
          preview: [
            { id: "description", label: "설명", value: "9월 견적 재검토", kind: "text" },
            { id: "assignee", label: "요청 대상", value: "지호 (팀장)", kind: "person" },
            { id: "due_date", label: "기한", value: "2026-09-30", kind: "date" },
          ],
          state: "pending",
          version: 1,
          payload_summary: "업무 요청: 견적서 재검토",
          result: null,
          audit_ref: null,
          commands: [{ id: "approve", label: "승인", tone: "primary" }, { id: "reject", label: "거절", tone: "neutral" }],
        },
      ],
    });
    const { container } = render(<MessageList {...listProps} conversation={pending} localFragments={[]} />);
    const card = container.querySelector(".ax-action-card") as HTMLElement;
    expect(card.querySelector("b")?.textContent).toBe("견적서 재검토");
    expect(card.querySelector(".ax-card-kicker")?.textContent).toBe("AX 제안 · 업무 요청 · 승인 필요");
    expect(within(card).queryByText("업무 요청 생성 확인")).toBeNull(); // the operation title is not the subject
    const rows = Array.from(card.querySelectorAll(".ax-preview-row")).map((row) => [row.querySelector("dt")?.textContent, row.querySelector("dd")?.textContent]);
    expect(rows).toEqual([
      ["설명", "9월 견적 재검토"],
      ["요청 대상", "지호 (팀장)"],
      ["기한", "2026/09/30"],
    ]);
    expect((card.querySelector(".ax-preview") as HTMLDetailsElement).open).toBe(true); // pending: open for review
    // Rows mix created fields with linked grounds, so the summary must not claim every row gets applied.
    expect((card.querySelector(".ax-preview > summary") as HTMLElement).textContent).toBe("상세 보기 · 승인 전 확인할 3개 항목");
    expect(within(card).getByRole("button", { name: "승인" })).toBeTruthy();
  });

  it("shows the attachments the turn read as a linked evidence row, exactly as the server sent them", () => {
    const grounded = conversation("c1", "새 대화", "첨부를 근거로 제안해줘", {
      actions: [
        {
          action_id: "a3",
          conversation_id: "c1",
          turn_id: "c1-t1",
          action_type: "task.create_self",
          title: "업무 생성 확인",
          subject: "견적 재검토 후속",
          operation_label: "업무 생성",
          preview: [
            { id: "assignee", label: "담당", value: "민아 (구성원)", kind: "person" },
            { id: "evidence", label: "근거 자료", value: "견적.md, 계약서.pdf", kind: "evidence" },
          ],
          state: "pending",
          version: 1,
          payload_summary: "업무 생성 확인",
          result: null,
          audit_ref: null,
          commands: [{ id: "approve", label: "승인", tone: "primary" }],
        },
      ],
    });
    const { container } = render(<MessageList {...listProps} conversation={grounded} localFragments={[]} />);
    const card = container.querySelector(".ax-action-card") as HTMLElement;
    const row = card.querySelector(".ax-preview-row.evidence") as HTMLElement;
    expect(row.querySelector("dt")?.textContent).toBe("근거 자료");
    expect(row.querySelector("dd")?.textContent).toBe("견적.md, 계약서.pdf");
    // The row is rendered verbatim: the client neither reformats file names nor invents a link target.
    expect(row.querySelector("a")).toBeNull();
    expect(card.querySelectorAll(".ax-preview-row")).toHaveLength(2);
  });

  it("does not infer preview rows or commands from the action type when the server sends none", () => {
    const bare = conversation("c1", "새 대화", "업무 만들어줘", {
      actions: [
        { action_id: "a2", conversation_id: "c1", turn_id: "c1-t1", action_type: "task.create_self", title: "업무 생성 확인", state: "approved", version: 2, payload_summary: "업무 생성 확인", result: { task_id: "t1" }, audit_ref: "a2", commands: [] },
      ],
    });
    const { container } = render(<MessageList {...listProps} conversation={bare} localFragments={[]} />);
    const card = container.querySelector(".ax-action-card") as HTMLElement;
    expect(card.querySelector("b")?.textContent).toBe("업무 생성 확인"); // falls back to the canonical title only
    expect(card.querySelector(".ax-preview")).toBeNull();
    expect(card.querySelectorAll("button")).toHaveLength(0);
    expect(card.querySelector(".ax-card-kicker")?.textContent).toBe("AX 제안");
  });
});

describe("답변이 가리키는 것", () => {
  afterEach(cleanup);

  it("lists each thing the turn read as its own way in, and nothing when it read none", async () => {
    const base = conversation("c10", "오늘 업무", "제품팀장이 오늘 하는 업무 알려줘");
    const named = {
      ...base,
      answer_resources: [
        {
          reference_id: "a1",
          turn_id: base.turns[0].turn_id,
          sequence: 1,
          resource_type: "task" as const,
          resource_id: "task-1",
          resource_version: 3,
          parent_resource_id: null,
          title: "분기 마감 정리",
          state: "in_progress",
        },
        {
          reference_id: "a2",
          turn_id: base.turns[0].turn_id,
          sequence: 2,
          resource_type: "meeting" as const,
          resource_id: "meeting-1",
          resource_version: 1,
          parent_resource_id: null,
          title: "주간 회의",
          state: "private",
        },
      ],
    };
    const onOpenResource = vi.fn();
    const { container, rerender } = render(
      <MessageList
        conversation={named as never}
        localFragments={[]}
        onDecide={vi.fn()}
        onDiscardFragment={vi.fn()}
        onOpenResource={onOpenResource}
        onRetryFragment={vi.fn()}
        onRetryTurn={vi.fn()}
      />,
    );
    const listed = container.querySelector("section.ax-answer-resources") as HTMLElement;
    expect(listed.textContent).toContain("분기 마감 정리");
    expect(listed.textContent).toContain("주간 회의");
    // The item opens the canonical thing by its own id — not by anything parsed out of the answer text.
    fireEvent.click(listed.querySelectorAll("button")[0]);
    expect(onOpenResource).toHaveBeenCalledWith(expect.objectContaining({ resource_type: "task", resource_id: "task-1" }));

    rerender(<MessageList
        conversation={base as never}
        localFragments={[]}
        onDecide={vi.fn()}
        onDiscardFragment={vi.fn()}
        onRetryFragment={vi.fn()}
        onRetryTurn={vi.fn()}
      />);
    expect(container.querySelector("section.ax-answer-resources")).toBeNull();
  });
});

describe("실행 영수증", () => {
  afterEach(cleanup);

  it("shows the walk while the turn runs and folds it into one line once it is done", async () => {
    const running = conversation("c11", "관계 질문", "이 업무가 어디서 왔는지 알려줘");
    running.turns[0] = { ...running.turns[0], state: "running", progress_state: "tool_running" } as never;
    const steps = [
      { receipt_id: "r1", turn_id: running.turns[0].turn_id, sequence: 1, kind: "node" as const, node_ref: "task:t1", node_title: "분기 마감", observed_at: "2026-09-06T00:00:00Z" },
    ];
    const { container, rerender } = render(
      <MessageList
        conversation={{ ...running, graph_receipts: steps } as never}
        localFragments={[]}
        onDecide={vi.fn()}
        onDiscardFragment={vi.fn()}
        onRetryFragment={vi.fn()}
        onRetryTurn={vi.fn()}
      />,
    );
    // While it runs the path is open, with no summary to expand.
    expect(container.querySelector(".ax-rail:not(.terminal) section.ax-search-path")).not.toBeNull();
    expect(container.querySelector(".ax-rail details")).toBeNull();

    const done = conversation("c11", "관계 질문", "이 업무가 어디서 왔는지 알려줘");
    rerender(
      <MessageList
        conversation={{ ...done, graph_receipts: steps.map((step) => ({ ...step, turn_id: done.turns[0].turn_id })) } as never}
        localFragments={[]}
        onDecide={vi.fn()}
        onDiscardFragment={vi.fn()}
        onRetryFragment={vi.fn()}
        onRetryTurn={vi.fn()}
      />,
    );
    const receipt = container.querySelector(".ax-rail.terminal details") as HTMLDetailsElement;
    expect(receipt).not.toBeNull();
    expect(receipt.open).toBe(false);
    expect(receipt.querySelector("summary")?.textContent).toContain("연결 1단계");
    expect(receipt.querySelector("section.ax-search-path")).not.toBeNull();
  });
});
