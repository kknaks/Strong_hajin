import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Conversation, ConversationTurn } from "../../lib/viewModels";
import { ChatDrawer } from "./ChatDrawer";
import { AssistantMarkdown } from "./AssistantMarkdown";
import { ExecutionRail, MessageList } from "./MessageList";
import type { LocalFragment } from "./useConversations";

vi.mock("../../lib/api", () => ({
  cancelConversation: vi.fn(),
  createConversation: vi.fn(),
  decideAction: vi.fn(),
  getConversation: vi.fn(),
  getConversations: vi.fn(),
  retryConversationTurn: vi.fn(),
  sendConversationMessage: vi.fn(),
}));

import * as api from "../../lib/api";
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
    has_more_messages: false,
    first_user_message_excerpt: firstMessage,
    user_message_count: 1,
    has_final_answer: false,
    queued_message_count: 0,
    latest_turn_state: "completed",
    ...extra,
  } as Conversation;
}

function answeredConversation(id: string, title: string, firstMessage: string): Conversation {
  const base = conversation(id, title, firstMessage);
  return {
    ...base,
    messages: [
      ...base.messages,
      { message_id: `${id}-m2`, turn_id: `${id}-t1`, role: "assistant", body: "답변", body_state: "final", sequence: 2, state: "accepted" },
    ],
    has_final_answer: true,
  } as Conversation;
}

const noop = async () => undefined;

function renderDrawer(overrides: Partial<Parameters<typeof ChatDrawer>[0]> = {}) {
  const conversations = [
    answeredConversation("c1", "견적 검토", "견적서 납기일을 알려줘"),
    answeredConversation("c2", "새 대화", "오늘 할 일을 정리해줘"),
    answeredConversation("c3", "보고 초안", "일일보고 초안을 만들어줘"),
  ];
  const props: Parameters<typeof ChatDrawer>[0] = {
    personaId: "mina",
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
    onFollowUpCandidate: vi.fn(async () => true),
    onRetryList: vi.fn(),
    ...overrides,
  };
  return { ...render(<ChatDrawer {...props} />), props };
}

/**
 * 채팅은 그래프 UI 를 그리지 않는다 — 걸어간 영수증의 제목이 화면 어디에도 나오지 않는 것으로 본다.
 *
 * 바퀴 7 전에는 `section.ax-search-path`/`section.ax-turn-graph` 가 null 인지를 봤는데 그 두 클래스는
 * 소스 어디에도 없다(지워진 프로토타입의 이름이다). 늘 통과하던 «빈 검사» 라 실제 값을 짚게 고쳤다.
 */
function expectNoGraphUi(container: HTMLElement): void {
  expect(within(container).queryByText("분기 마감")).toBeNull();
  expect(within(container).queryByText("분기 마감 요청")).toBeNull();
}

/** 한 말차례 안의 차례 — BEM 에서는 첫 클래스가 아니라 modifier 가 역할을 말한다. */
function turnOrder(container: HTMLElement): string[] {
  return [...container.querySelectorAll(".scax-turn > *")].map((element) => (
    element.classList.contains("scax-rail") ? "rail"
      : element.classList.contains("scax-msg--user") ? "user"
        : element.classList.contains("scax-msg--assistant") ? "assistant"
          : element.className
  ));
}

describe("graph receipt presentation", () => {
  afterEach(cleanup);

  it("keeps graph receipts out of the chat presentation", async () => {
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
    expectNoGraphUi(container);

    rerender(<MessageList
        conversation={base as never}
        localFragments={[]}
        onDecide={vi.fn()}
        onDiscardFragment={vi.fn()}
        onRetryFragment={vi.fn()}
        onRetryTurn={vi.fn()}
      />);
    expectNoGraphUi(container);
  });
});

describe("ChatDrawer session switcher", () => {
  afterEach(cleanup);

  it("replaces the conversation and composer with the history/search utility screen", () => {
    const { props } = renderDrawer();

    fireEvent.click(screen.getByRole("button", { name: "대화 히스토리" }));
    expect(screen.getByRole("region", { name: "대화 히스토리 화면" })).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "AX 메시지" })).toBeNull();
    expect(screen.getByRole("searchbox", { name: "대화 검색" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "대화 히스토리" }));
    expect(screen.getByRole("textbox", { name: "AX 메시지" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "대화 히스토리" }));
    fireEvent.click(screen.getByRole("button", { name: "보고 초안" }));
    expect(props.onSelect).toHaveBeenCalledWith(expect.objectContaining({ conversation_id: "c3" }));
    expect(screen.getByRole("textbox", { name: "AX 메시지" })).toBeTruthy();
  });

  it("lists conversations vertically and filters them by title or first message", () => {
    renderDrawer();
    fireEvent.click(screen.getByRole("button", { name: "대화 히스토리" }));
    expect(within(screen.getByRole("list", { name: "대화 히스토리" })).getAllByRole("button")).toHaveLength(3);
    expect(screen.getByRole("button", { name: "견적 검토" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "오늘 할 일을 정리해줘" })).toBeTruthy();

    fireEvent.change(screen.getByRole("searchbox", { name: "대화 검색" }), { target: { value: "일일보고" } });
    const buttons = within(screen.getByRole("list")).getAllByRole("button");
    expect(buttons).toHaveLength(1);
    expect(buttons[0].getAttribute("aria-label")).toBe("보고 초안");

    fireEvent.change(screen.getByRole("searchbox", { name: "대화 검색" }), { target: { value: "없는 대화" } });
    expect(screen.getByText("검색 결과가 없습니다.")).toBeTruthy();
  });

  it("distinguishes first-load, empty, and error states without hiding the new-conversation action", () => {
    const { rerender, props } = renderDrawer({ conversations: [], activeConversation: null, listStatus: "loading" });
    fireEvent.click(screen.getByRole("button", { name: "대화 히스토리" }));
    expect(props.onRetryList).toHaveBeenCalledTimes(1);
    // 첫 로딩은 글자가 아니라 올 목록의 자리로 기다린다 (v2 10 STATE)
    expect(screen.getByRole("status").getAttribute("aria-busy")).toBe("true");
    expect(screen.getByText("대화를 불러오는 중")).toBeTruthy();
    expect(screen.getByRole("button", { name: "새 AX 대화" })).toBeTruthy();

    rerender(<ChatDrawer {...props} conversations={[]} activeConversation={null} listStatus="error" />);
    fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    expect(props.onRetryList).toHaveBeenCalledTimes(2);

    rerender(<ChatDrawer {...props} conversations={[]} activeConversation={null} listStatus="ready" />);
    expect(screen.getByText("아직 완료된 대화가 없습니다.")).toBeTruthy();
    expect(screen.queryByPlaceholderText("메시지를 입력해 주세요.")).toBeNull();
  });

  it("keeps unanswered and empty conversations out of history", () => {
    const answered = answeredConversation("answered", "새 대화", "답변이 끝난 질문");
    const pending = conversation("pending", "새 대화", "아직 답변 중", {
      turns: [turn("pending-t1", { state: "running", progress_state: "composing", execution_completed_at: null })],
    });
    const empty = conversation("empty", "새 대화", "", { messages: [], turns: [] });
    renderDrawer({ conversations: [empty, pending, answered], activeConversation: pending });

    fireEvent.click(screen.getByRole("button", { name: "대화 히스토리" }));
    const history = screen.getByRole("list", { name: "대화 히스토리" });
    expect(within(history).getAllByRole("button")).toHaveLength(1);
    expect(within(history).getByRole("button", { name: "답변이 끝난 질문" })).toBeTruthy();
    expect(within(history).queryByRole("button", { name: "아직 답변 중" })).toBeNull();
  });

  it("uses the header controls for history and new chat, and shows the visual start state only without an active conversation", () => {
    const { props } = renderDrawer({ activeConversation: null, message: "" });
    expect(screen.getByText("새로운 대화")).toBeTruthy();
    expect(screen.getByRole("heading", { name: /무엇을 도와드릴까요\?/ })).toBeTruthy();
    expect(screen.queryByRole("searchbox", { name: "대화 검색" })).toBeNull();
    expect(screen.queryByRole("list", { name: "대화 히스토리" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "대화 히스토리" }));
    expect(props.onRetryList).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("list", { name: "대화 히스토리" })).toBeTruthy();
    expect(screen.getByRole("searchbox", { name: "대화 검색" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "새 AX 대화" }));
    expect(props.onStart).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "이번 주 내 업무를 정리해줘" }));
    expect(props.onSend).toHaveBeenCalledWith("이번 주 내 업무를 정리해줘");
    expect(props.onMessageChange).not.toHaveBeenCalled();
  });

  it("allows the first message to be sent from the blank new-chat state", () => {
    const { props } = renderDrawer({ activeConversation: null, message: "오늘 내 회의를 알려줘" });
    const send = screen.getByRole("button", { name: "보내기" }) as HTMLButtonElement;
    expect(send.disabled).toBe(false);
    fireEvent.click(send);
    expect(props.onSend).toHaveBeenCalledTimes(1);
  });

  it("submits a keyboard shortcut once when Enter auto-repeats during the same key press", () => {
    const { props } = renderDrawer({ message: "한 번만 보내줘" });
    const composer = screen.getByRole("textbox", { name: "AX 메시지" });

    fireEvent.keyDown(composer, { key: "Enter", ctrlKey: true });
    fireEvent.keyDown(composer, { key: "Enter", ctrlKey: true, repeat: true });

    expect(props.onSend).toHaveBeenCalledTimes(1);
  });

  it("keeps the blank new-chat send control active and returns an empty click to the composer", () => {
    const { props } = renderDrawer({ activeConversation: null, message: "" });
    const composer = screen.getByRole("textbox", { name: "AX 메시지" });
    const send = screen.getByRole("button", { name: "보내기" }) as HTMLButtonElement;

    expect(send.disabled).toBe(false);
    fireEvent.click(send);

    expect(document.activeElement).toBe(composer);
    expect(props.onSend).not.toHaveBeenCalled();
  });

  it("renders an explicitly created empty conversation as the same visual start state", () => {
    const empty = conversation("empty", "새 대화", "", { messages: [], turns: [], actions: [] });
    renderDrawer({ conversations: [empty], activeConversation: empty });
    expect(screen.getByText("새로운 대화")).toBeTruthy();
    expect(screen.getByRole("heading", { name: /무엇을 도와드릴까요\?/ })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "추천 대화" })).toBeNull();
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

describe("assistant identity", () => {
  afterEach(cleanup);

  it("keeps the selected AX character in the header without repeating it beside answers", () => {
    const active = conversation("c1", "프로필", "질문", {
      messages: [
        { message_id: "m1", turn_id: "c1-t1", role: "user", body: "질문", sequence: 1, state: "accepted" },
        { message_id: "m2", turn_id: "c1-t1", role: "assistant", body: "답변", sequence: 2, state: "accepted", body_state: "final" },
      ],
    });
    renderDrawer({ characterKey: "red-panda", conversations: [active], activeConversation: active });
    expect(screen.getAllByRole("img", { name: "AX assistant · 레서판다 · 대기 중" })).toHaveLength(1);
    expect(document.querySelector(".scax-msg--assistant .scax-character")).toBeNull();
  });
});

describe("ExecutionRail", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows a quiet request-check timeline while the answer is being prepared", () => {
    const running = turn("t1", { state: "running", progress_state: "tool_running", current_tool_display_name: "관련 업무 히스토리 확인", execution_completed_at: null });
    const tools: Conversation["tool_invocations"] = [
      { turn_id: "t1", sequence: 1, provider_call_id: "c1", tool_name: "project_list", display_name: "프로젝트 목록 조회", input_summary: "입력 없음", state: "completed", result_summary: "프로젝트 3건", error_summary: null, latency_ms: 80, started_at: null, completed_at: null, target_resource_id: null, target_resource_version: null, audit_ref: null },
      { turn_id: "t1", sequence: 2, provider_call_id: "c2", tool_name: "task_history", display_name: "관련 업무 히스토리 확인", input_summary: "선택 업무", state: "running", result_summary: null, error_summary: null, latency_ms: null, started_at: null, completed_at: null, target_resource_id: null, target_resource_version: null, audit_ref: null },
    ];
    const { container } = render(<ExecutionRail tools={tools} turn={running} />);

    expect(screen.getByText("요청 내용 확인...")).toBeTruthy();
    const timeline = screen.getByRole("list", { name: "요청 처리 단계" });
    const steps = within(timeline).getAllByRole("listitem");
    expect(steps).toHaveLength(2);
    expect(steps[0].textContent).toContain("프로젝트 목록 조회");
    expect(steps[0].querySelector(".scax-rail__time")?.textContent).toBe("80ms");
    expect(steps[0].getAttribute("aria-label")).toBe("프로젝트 목록 조회 · 완료");
    expect(steps[1].textContent).toContain("관련 업무 히스토리 확인");
    expect(steps[1].getAttribute("aria-label")).toBe("관련 업무 히스토리 확인 · 실행 중");
    expect(container.querySelector(".scax-rail__step--completed .scax-rail__check")).toBeTruthy();
    expect(container.querySelector(".scax-rail__step--running .scax-rail__check")).toBeTruthy();
    // 레일은 도구의 «결과» 를 옮기지 않는다 — 이름과 상태만 말한다.
    expect(within(timeline).queryByText(/프로젝트 3건/)).toBeNull();
    // 시작을 못 본 도구에는 시간을 붙이지 않는다 (steps[1] 은 started_at 이 null 이다).
    expect(steps[1].querySelector(".scax-rail__time")).toBeNull();
  });

  it("announces only the phase and tool changes; the ticking elapsed time is hidden from assistive tech", () => {
    vi.stubGlobal("matchMedia", () => ({ matches: true }) as MediaQueryList);
    const running = turn("t1", { state: "running", progress_state: "tool_running", current_tool_display_name: "task list", execution_completed_at: null, run_ms: null, attempt: 1 });
    const tools: Conversation["tool_invocations"] = [
      { turn_id: "t1", sequence: 1, provider_call_id: "c1", tool_name: "task_list", display_name: "task list", input_summary: "입력 없음", state: "running", result_summary: null, error_summary: null, latency_ms: null, started_at: new Date(Date.now() - 2500).toISOString(), completed_at: null, target_resource_id: null, target_resource_version: null, audit_ref: null },
    ];
    const { container } = render(<ExecutionRail tools={tools} turn={running} />);
    const rail = container.querySelector(".scax-rail") as HTMLElement;
    expect(rail.getAttribute("aria-live")).toBeNull(); // the whole rail is not a live region
    expect(rail.getAttribute("data-progress")).toBe("tool_running");
    expect(rail.getAttribute("data-motion")).toBe("reduced");
    expect(container.querySelector(".scax-rail__icon.spin")).toBeNull(); // no spinner under prefers-reduced-motion
    const phrase = container.querySelector(".scax-rail__phrase") as HTMLElement;
    expect(phrase.getAttribute("aria-hidden")).toBe("true");
    expect(phrase.textContent).toBe("요청 내용 확인...");
    const liveStatus = container.querySelector(".scax-rail__live-status") as HTMLElement;
    expect(liveStatus.getAttribute("aria-live")).toBe("polite");
    expect(liveStatus.textContent).toBe("도구 실행 중 · task list");
    expect((container.querySelector(".scax-rail__steps") as HTMLElement).getAttribute("aria-live")).toBe("polite");
    // 흐르는 경과 시간은 보이되 보조기기에는 안 읽힌다 — 1초마다 다시 읽히면 안 된다.
    expect((container.querySelector(".scax-rail__time") as HTMLElement).getAttribute("aria-hidden")).toBe("true");
    expect(within(rail).getByText("task list")).toBeTruthy();
    expect(within(rail).getByRole("listitem", { name: "task list · 실행 중" })).toBeTruthy();
  });

  it("does not show a tool duration when the start was never observed", () => {
    const done = turn("t1");
    const tools: Conversation["tool_invocations"] = [
      { turn_id: "t1", sequence: 1, provider_call_id: "c1", tool_name: "task_get", display_name: "task get", input_summary: "입력: task_id=1", state: "completed", result_summary: "결과: state=open", error_summary: null, latency_ms: null, started_at: null, completed_at: "2026-09-04T00:00:05Z", target_resource_id: null, target_resource_version: null, audit_ref: null },
    ];
    const { container } = render(<ExecutionRail tools={tools} turn={done} />);
    expect(container.querySelector(".scax-rail__time")).toBeNull();
  });

  it("collapses the A-style completed timeline and offers retry for failures", () => {
    const completedTool: Conversation["tool_invocations"][number] = {
      turn_id: "t2", sequence: 1, provider_call_id: "c1", tool_name: "meeting_list", display_name: "회의 목록 조회",
      input_summary: "오늘", state: "completed", result_summary: "회의 2건", error_summary: null, latency_ms: 84,
      started_at: null, completed_at: null, target_resource_id: null, target_resource_version: null, audit_ref: null,
    };
    const completed = render(<ExecutionRail tools={[completedTool]} turn={turn("t2")} />);
    const completedRail = completed.container.querySelector(".scax-rail--terminal") as HTMLElement;
    expect(within(completedRail).queryByText("요청 내용 확인 완료")).toBeNull();
    expect(completedRail.querySelector(".scax-rail__icon")).toBeNull();
    expect(completedRail.querySelector("summary")?.textContent).toContain("도구 호출 1회");
    const completedDetails = completedRail.querySelector("details") as HTMLDetailsElement;
    expect(completedDetails.open).toBe(false);
    fireEvent.click(completedDetails.querySelector("summary")!);
    expect(within(completedRail).getByRole("listitem", { name: "회의 목록 조회 · 완료" })).toBeTruthy();
    expect(completedRail.querySelector(".scax-rail__time")?.textContent).toBe("84ms");
    expect(completedRail.querySelector("code")?.textContent).toBe("meeting_list");
    completed.unmount();

    const onRetry = vi.fn();
    const failed = turn("t1", { state: "failed", progress_state: "failed", error: "provider failed" });
    const { container } = render(<ExecutionRail onRetry={onRetry} tools={[]} turn={failed} />);
    const rail = container.querySelector(".scax-rail") as HTMLElement;
    expect(rail.className).toContain("terminal");
    expect(within(rail).getByText("요청 처리 실패")).toBeTruthy();
    expect((container.querySelector(".scax-rail__timings") as HTMLElement).textContent).toBe("실행 12s · 대기 1s");
    expect((container.querySelector(".scax-rail__timings") as HTMLElement).getAttribute("aria-hidden")).toBe("true");
    expect(within(rail).getByText("provider failed")).toBeTruthy();
    expect(rail.querySelector("details")).toBeNull();
    fireEvent.click(within(rail).getByRole("button", { name: "다시 시도" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("treats a terminal turn state as final even while the last progress snapshot still says composing", () => {
    const staleProgress = turn("t3", { state: "completed", progress_state: "composing" });
    const { container } = render(<ExecutionRail tools={[]} turn={staleProgress} />);
    expect(container.querySelector(".scax-rail--terminal.scax-rail--completed")).not.toBeNull();
    expect(screen.queryByText("요청 내용 확인 완료")).toBeNull();
    expect(container.querySelector(".scax-rail__timings")?.textContent).toContain("실행 12s");
    expect(container.querySelector("details")).toBeNull();
  });

  it("groups only adjacent repeated tools while preserving later execution stages", () => {
    const calls = [40, 60, 80].map((latency, index) => ({
      turn_id: "t4", sequence: index + 1, provider_call_id: `c${index}`, tool_name: "task_material_search",
      display_name: "자료 내용 검색", input_summary: `검색 ${index + 1}`, state: "completed" as const,
      result_summary: "찾음", error_summary: null, latency_ms: latency, started_at: null, completed_at: null,
      target_resource_id: null, target_resource_version: null, audit_ref: null,
    }));
    calls.push({ ...calls[0], sequence: 4, provider_call_id: "c4", state: "failed", latency_ms: 20, error_summary: "실패" } as never);
    calls.push({ ...calls[0], sequence: 5, provider_call_id: "c5", tool_name: "task_get", display_name: "업무 상세 확인", latency_ms: 30 } as never);
    calls.push({ ...calls[0], sequence: 6, provider_call_id: "c6", latency_ms: 25 } as never);

    const { container } = render(<ExecutionRail tools={calls} turn={turn("t4")} />);
    expect(container.querySelector("summary")?.textContent).toContain("도구 호출 6회");
    fireEvent.click(container.querySelector("summary")!);
    const timeline = screen.getByRole("list", { name: "요청 처리 단계" });
    expect(within(timeline).getAllByRole("listitem")).toHaveLength(4);
    expect(within(timeline).getByRole("listitem", { name: "자료 내용 검색 3회 · 완료" }).textContent).toContain("180ms");
    expect(within(timeline).getAllByText("task_material_search")).toHaveLength(3);
    expect(within(timeline).getByRole("listitem", { name: "자료 내용 검색 · 실패" })).toBeTruthy();
    expect(within(timeline).getByRole("listitem", { name: "업무 상세 확인 · 완료" })).toBeTruthy();
  });
});

describe("MessageList", () => {
  afterEach(cleanup);

  const listProps = { onDecide: noop, onRetryTurn: vi.fn(), onRetryFragment: vi.fn(), onDiscardFragment: vi.fn(), onFollowUpCandidate: vi.fn(async () => true) };

  it("opens an explicitly referenced prior-turn resource from a restored follow-up", () => {
    const onOpenResource = vi.fn();
    const resource = { reference_id: "receipt-1", turn_id: "t1", sequence: 1, resource_type: "task" as const, resource_id: "task-1", resource_version: 1, title: "다시 확인할 업무", state: "open" };
    const active = conversation("c1", "후속 대화", "그 업무를 다시 보여줘", {
      turns: [turn("t1"), turn("t2")],
      messages: [{ message_id: "m2", turn_id: "t2", role: "assistant", body: "{{task}}를 확인하세요.", sequence: 2, state: "accepted", body_state: "final",
        answer_document: { version: 1, elements: [{ key: "task", type: "resource_reference", ref: "receipt-1" }] } }],
      answer_resources: [resource],
    });
    const { container } = render(<MessageList {...listProps} conversation={active} localFragments={[]} onOpenResource={onOpenResource} />);
    /* 바퀴 12: main(#11)이 쓴 구 클래스(.ax-turn · .ax-assistant-body)를 바퀴 7 이 갈아 둔 이름으로
       바꿔 짚는다. 짚는 것은 그대로 — 「지난 턴의 자료를 눌러 연다」다. */
    const body = container.querySelector('.scax-turn[data-turn-id="t2"] .scax-msg__body') as HTMLElement;
    fireEvent.click(within(body).getByRole("button", { name: resource.title }));
    expect(onOpenResource).toHaveBeenCalledWith(resource);
  });

  it("keeps the live rail under the request, then moves the terminal rail below the answer", () => {
    const active = conversation("c1", "견적 검토", "첫 발화", {
      messages: [
        { message_id: "m1", turn_id: "t1", role: "user", body: "첫 발화", sequence: 1, state: "accepted" },
        { message_id: "m2", turn_id: "t1", role: "assistant", body: "부분 답변", sequence: 2, state: "accepted", body_state: "cancelled" },
      ],
      turns: [turn("t1", { state: "cancelled", progress_state: "cancelled" })],
    });
    const { container } = render(<MessageList {...listProps} conversation={active} localFragments={[]} />);
    expect(turnOrder(container)).toEqual(["user", "assistant", "rail"]);
    expect(screen.getByText("부분 답변")).toBeTruthy();
    expect(screen.getByText("취소 시점까지의 답변")).toBeTruthy();
    expect(screen.getByText("요청 처리 취소")).toBeTruthy();
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
    const streaming = screen.getByText("작성 중인 답").closest(".scax-msg--assistant") as HTMLElement;
    expect(streaming.getAttribute("data-body-state")).toBe("streaming");
    expect(screen.getByText("답변 작성 중")).toBeTruthy();
  });

  it("submits a completed answer's follow-up once, disables siblings while sending, and restores retry after failure", async () => {
    let settle: ((accepted: boolean) => void) | undefined;
    const onFollowUpCandidate = vi.fn(
      () => new Promise<boolean>((resolve) => { settle = resolve; }),
    );
    const active = conversation("c1", "회의 후속", "회의 내용을 알려줘", {
      messages: [
        { message_id: "m1", turn_id: "t1", role: "user", body: "회의 내용을 알려줘", sequence: 1, state: "accepted" },
        { message_id: "m2", turn_id: "t1", role: "assistant", body: "결정 사항입니다.", sequence: 2, state: "accepted", body_state: "final" },
      ],
      turns: [turn("t1", {
        follow_up_candidates: [
          { candidate_id: "f1", source_turn_id: "t1", label: "회의에서 나온 아주 긴 후속 업무를 담당자별로 정리하기", user_text: "회의에서 나온 업무를 담당자별로 정리해줘", selected_message_id: null },
          { candidate_id: "f2", source_turn_id: "t1", label: "다음 회의 잡기", user_text: "다음 회의 일정을 잡아줘", selected_message_id: null },
        ],
      })],
    });
    const { container } = render(
      <MessageList {...listProps} conversation={active} localFragments={[]} onFollowUpCandidate={onFollowUpCandidate} />,
    );
    const first = screen.getByRole("button", { name: "회의에서 나온 업무를 담당자별로 정리해줘" });
    const second = screen.getByRole("button", { name: "다음 회의 일정을 잡아줘" });
    fireEvent.click(first);
    fireEvent.click(first);
    expect(onFollowUpCandidate).toHaveBeenCalledTimes(1);
    expect(onFollowUpCandidate).toHaveBeenCalledWith(active.turns[0].follow_up_candidates?.[0]);
    expect(first.getAttribute("data-state")).toBe("sending");
    expect(first.getAttribute("aria-pressed")).toBe("true");
    expect((second as HTMLButtonElement).disabled).toBe(true);
    expect(container.querySelector(".scax-followup")?.textContent).toContain("전송 중");

    await act(async () => settle?.(false));
    expect((first as HTMLButtonElement).disabled).toBe(false);
    expect(first.getAttribute("data-state")).toBe("failed");
    expect(container.querySelector(".scax-followup")?.textContent).toContain("다시 시도");
  });

  it("hides follow-ups before completion and reconstructs the selected candidate from history", () => {
    const candidate = { candidate_id: "f1", source_turn_id: "t1", label: "후속 업무 정리", user_text: "후속 업무를 정리해줘", selected_message_id: null };
    const streaming = conversation("c1", "회의 후속", "질문", {
      turns: [turn("t1", { state: "running", progress_state: "composing", follow_up_candidates: [candidate] })],
    });
    const { rerender } = render(<MessageList {...listProps} conversation={streaming} localFragments={[]} />);
    expect(screen.queryByRole("button", { name: "후속 업무를 정리해줘" })).toBeNull();

    const selected = {
      ...streaming,
      messages: [
        ...streaming.messages,
        { message_id: "m2", turn_id: "t1", role: "assistant" as const, body: "완료 답변", sequence: 2, state: "accepted" as const, body_state: "final" as const },
      ],
      turns: [turn("t1", { follow_up_candidates: [{ ...candidate, selected_message_id: "m3" }] })],
    };
    rerender(<MessageList {...listProps} conversation={selected} localFragments={[]} />);
    const button = screen.getByRole("button", { name: "후속 업무를 정리해줘 · 선택됨" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(button.getAttribute("aria-pressed")).toBe("true");
  });

  it("hides the previous answer's suggestions as soon as a new user message starts", () => {
    const candidate = { candidate_id: "f1", source_turn_id: "t1", label: "후속 업무 정리", user_text: "후속 업무를 정리해줘", selected_message_id: null };
    const previous = conversation("c1", "회의 후속", "첫 질문", {
      messages: [
        { message_id: "m1", turn_id: "t1", role: "user", body: "첫 질문", sequence: 1, state: "accepted" },
        { message_id: "m2", turn_id: "t1", role: "assistant", body: "첫 답변", sequence: 2, state: "accepted", body_state: "final" },
        { message_id: "m3", turn_id: "t2", role: "user", body: "새 질문", sequence: 3, state: "accepted" },
      ],
      turns: [
        turn("t1", { follow_up_candidates: [candidate, { ...candidate, candidate_id: "f2", user_text: "다음 회의를 준비해줘" }] }),
        turn("t2", { state: "running", progress_state: "preparing", execution_completed_at: null }),
      ],
    });

    render(<MessageList {...listProps} conversation={previous} localFragments={[]} />);

    expect(screen.queryByRole("region", { name: "추천 대화" })).toBeNull();
  });

  it("projects the latest completed turn as stacked cards above the composer and supports directional keys", () => {
    const previous = {
      candidate_id: "old-1", source_turn_id: "t1", label: "이전 후보", user_text: "이전 답변을 더 설명해줘", selected_message_id: null,
    };
    const latest = [
      { candidate_id: "new-1", source_turn_id: "t2", label: "짧은 이름", user_text: "회의에서 나온 후속 업무를 담당자별 우선순위와 마감일 기준으로 자세히 정리해줘", selected_message_id: null },
      { candidate_id: "new-2", source_turn_id: "t2", label: "다음 후보", user_text: "다음 회의 안건을 준비해줘", selected_message_id: null },
    ];
    const active = conversation("c1", "회의 후속", "첫 질문", {
      messages: [
        { message_id: "m1", turn_id: "t1", role: "user", body: "첫 질문", sequence: 1, state: "accepted" },
        { message_id: "m2", turn_id: "t1", role: "assistant", body: "첫 답변", sequence: 2, state: "accepted", body_state: "final" },
        { message_id: "m3", turn_id: "t2", role: "user", body: "둘째 질문", sequence: 3, state: "accepted" },
        { message_id: "m4", turn_id: "t2", role: "assistant", body: "둘째 답변", sequence: 4, state: "accepted", body_state: "final" },
      ],
      turns: [turn("t1", { follow_up_candidates: [previous, { ...previous, candidate_id: "old-2", user_text: "이전 후속을 정리해줘" }] }), turn("t2", { follow_up_candidates: latest })],
      actions: [{
        action_id: "a1", conversation_id: "c1", turn_id: "t2", action_type: "task.create_self",
        title: "업무 생성 확인", state: "pending", version: 1, payload_summary: "업무 생성", result: null, audit_ref: null, commands: [],
      }],
    });
    const { container, rerender } = render(<MessageList {...listProps} conversation={active} localFragments={[]} />);
    const rails = screen.getAllByRole("region", { name: "추천 대화" });
    expect(rails).toHaveLength(1);
    expect(rails[0].closest(".scax-turn")?.getAttribute("data-turn-id")).toBe("t2");
    expect(rails[0].previousElementSibling?.classList.contains("scax-actioncard")).toBe(true);
    expect(rails[0].closest(".scax-turn")?.lastElementChild).toBe(rails[0]);
    expect(within(rails[0]).getByText("이렇게 물어볼 수 있어요")).toBeTruthy();
    expect(within(rails[0]).getByText("선택하면 바로 전송돼요")).toBeTruthy();
    expect(rails[0].querySelector(".scax-followup__list")).toBeTruthy();
    expect(rails[0].querySelectorAll(".scax-followup__arrow")).toHaveLength(latest.length);
    expect(screen.queryByRole("button", { name: previous.user_text })).toBeNull();
    const first = screen.getByRole("button", { name: latest[0].user_text });
    const second = screen.getByRole("button", { name: latest[1].user_text });
    expect(first.textContent).toContain(latest[0].user_text);
    first.focus();
    fireEvent.keyDown(first, { key: "ArrowDown" });
    expect(document.activeElement).toBe(second);
    fireEvent.keyDown(second, { key: "ArrowUp" });
    expect(document.activeElement).toBe(first);

    rerender(<MessageList
      {...listProps}
      conversation={{
        ...active,
        messages: [...active.messages, { message_id: "m5", turn_id: "t3", role: "assistant", body: "후속 후보가 필요 없는 답변", sequence: 5, state: "accepted", body_state: "final" }],
        turns: [...active.turns, turn("t3", { follow_up_candidates: [] })],
      }}
      localFragments={[]}
    />);
    expect(screen.queryByRole("region", { name: "추천 대화" })).toBeNull();
  });

  it("sends a composer-rail candidate without replacing the manual composer draft", () => {
    const candidate = { candidate_id: "f1", source_turn_id: "c1-t1", label: "후속 업무", user_text: "후속 업무를 정리해줘", selected_message_id: null };
    const active = conversation("c1", "회의 후속", "질문", {
      messages: [
        { message_id: "m1", turn_id: "c1-t1", role: "user", body: "질문", sequence: 1, state: "accepted" },
        { message_id: "m2", turn_id: "c1-t1", role: "assistant", body: "답변", sequence: 2, state: "accepted", body_state: "final" },
      ],
      turns: [turn("c1-t1", { follow_up_candidates: [candidate, { ...candidate, candidate_id: "f2", user_text: "다음 회의를 준비해줘" }] })],
    });
    const onMessageChange = vi.fn();
    const onFollowUpCandidate = vi.fn(async () => true);
    renderDrawer({ conversations: [active], activeConversation: active, message: "작성 중인 수동 초안", onMessageChange, onFollowUpCandidate });
    fireEvent.click(screen.getByRole("button", { name: candidate.user_text }));
    expect((screen.getByLabelText("AX 메시지") as HTMLTextAreaElement).value).toBe("작성 중인 수동 초안");
    expect(onMessageChange).not.toHaveBeenCalled();
    expect(onFollowUpCandidate).toHaveBeenCalledWith(candidate);
  });

  it("clears a finished rail's local send state when a newer completed turn supplies candidates", async () => {
    const first = { candidate_id: "f1", source_turn_id: "t1", label: "첫 후보", user_text: "첫 후속 질문", selected_message_id: null };
    const next = { candidate_id: "f2", source_turn_id: "t2", label: "새 후보", user_text: "새 후속 질문", selected_message_id: null };
    const onFollowUpCandidate = vi.fn(async () => true);
    const initial = conversation("c1", "후속 전환", "질문", {
      messages: [{ message_id: "m1", turn_id: "t1", role: "assistant", body: "답변", sequence: 1, state: "accepted", body_state: "final" }],
      turns: [turn("t1", { follow_up_candidates: [first, { ...first, candidate_id: "f1b", user_text: "다른 첫 후속 질문" }] })],
    });
    const { rerender } = render(<MessageList {...listProps} conversation={initial} localFragments={[]} onFollowUpCandidate={onFollowUpCandidate} />);
    fireEvent.click(screen.getByRole("button", { name: first.user_text }));
    await waitFor(() => expect(onFollowUpCandidate).toHaveBeenCalledTimes(1));

    rerender(<MessageList
      {...listProps}
      conversation={{
        ...initial,
        messages: [...initial.messages, { message_id: "m2", turn_id: "t2", role: "assistant", body: "새 답변", sequence: 2, state: "accepted", body_state: "final" }],
        turns: [...initial.turns, turn("t2", { follow_up_candidates: [next, { ...next, candidate_id: "f2b", user_text: "다른 새 후속 질문" }] })],
      }}
      localFragments={[]}
      onFollowUpCandidate={onFollowUpCandidate}
    />);
    const nextButton = screen.getByRole("button", { name: next.user_text });
    expect((nextButton as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(nextButton);
    await waitFor(() => expect(onFollowUpCandidate).toHaveBeenCalledTimes(2));
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
    const first = document.querySelector('.scax-actioncard[data-action-id="a1"]') as HTMLElement;
    const second = document.querySelector('.scax-actioncard[data-action-id="a2"]') as HTMLElement;
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
    const queuedItems = within(queue).getAllByRole("listitem");
    expect(queuedItems.map((item) => within(item).getByText(/째 발화/).textContent)).toEqual(["둘째 발화", "셋째 발화"]);
    expect(queuedItems.map((item) => within(item).getByRole("status").textContent)).toEqual(["✦요청 내용 확인...", "✦요청 내용 확인..."]);
    fireEvent.click(screen.getByRole("button", { name: "다시 보내기" }));
    expect(onRetryFragment).toHaveBeenCalledWith(failed);
    fireEvent.click(screen.getByRole("button", { name: "삭제" }));
    expect(onDiscardFragment).toHaveBeenCalledWith("l1");
  });

  it("keeps a local request bubble stable and changes only the status copy after transport acceptance", () => {
    const active = conversation("c1", "견적 검토", "첫 발화");
    const sending: LocalFragment = { local_id: "l2", conversation_id: "c1", body: "새 요청", state: "sending", context: [], idempotency_key: "k2" };
    const accepted: LocalFragment = { ...sending, local_id: "l3", state: "accepted", idempotency_key: "k3" };
    const { rerender } = render(<MessageList {...listProps} conversation={active} localFragments={[sending]} />);

    const sendingFragment = document.querySelector('[data-local-id="l2"]') as HTMLElement;
    expect(within(sendingFragment).getByText("새 요청").classList.contains("scax-msg--user")).toBe(true);
    expect(within(sendingFragment).getByRole("status").textContent).toContain("요청을 접수하는 중...");
    expect(within(sendingFragment).queryByText("접수 중…")).toBeNull();

    rerender(<MessageList {...listProps} conversation={active} localFragments={[accepted]} />);
    const acceptedFragment = document.querySelector('[data-local-id="l3"]') as HTMLElement;
    expect(within(acceptedFragment).getByText("새 요청").classList.contains("scax-msg--user")).toBe(true);
    expect(within(acceptedFragment).getByRole("status").textContent).toContain("요청 내용 확인...");
  });

  it("offers a new-message jump instead of stealing the scroll position when the reader is above the bottom", () => {
    const first = conversation("c1", "견적 검토", "첫 발화");
    const { rerender, container } = render(<MessageList {...listProps} conversation={first} localFragments={[]} />);
    const scroller = container.querySelector(".scax-chat__messages") as HTMLDivElement;
    Object.defineProperty(scroller, "scrollHeight", { configurable: true, value: 1000 });
    Object.defineProperty(scroller, "clientHeight", { configurable: true, value: 300 });
    scroller.scrollTop = 0;
    fireEvent.scroll(scroller);
    const second = { ...first, version: 2, messages: [...first.messages, { message_id: "m2", turn_id: "c1-t1", role: "assistant" as const, body: "답변입니다", sequence: 2, state: "accepted" as const }] };
    rerender(<MessageList {...listProps} conversation={second} localFragments={[]} />);
    expect(scroller.scrollTop).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: "새 메시지" }));
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
    const scroller = container.querySelector(".scax-chat__messages") as HTMLDivElement;
    Object.defineProperty(scroller, "scrollHeight", { configurable: true, value: 1000 });
    Object.defineProperty(scroller, "clientHeight", { configurable: true, value: 300 });
    scroller.scrollTop = 0;
    fireEvent.scroll(scroller);
    // Same progress state, longer partial body → new content.
    rerender(<MessageList {...listProps} conversation={{ ...running, messages: [running.messages[0], { ...running.messages[1], body: "부분 답변이 더 길어짐" }] }} localFragments={[]} />);
    expect(screen.getByRole("button", { name: "새 메시지" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "새 메시지" }));
    // Same progress state, a tool receipt appears → new content again.
    const tool = { turn_id: "c1-t1", sequence: 1, provider_call_id: "c", tool_name: "task_list", display_name: "task list", input_summary: "입력 없음", state: "running", result_summary: null, error_summary: null, latency_ms: null, started_at: null, completed_at: null, target_resource_id: null, target_resource_version: null, audit_ref: null };
    scroller.scrollTop = 0;
    fireEvent.scroll(scroller);
    rerender(<MessageList {...listProps} conversation={{ ...running, tool_invocations: [tool] }} localFragments={[]} />);
    expect(screen.getByRole("button", { name: "새 메시지" })).toBeTruthy();
  });
});

describe("useConversations", () => {
  const onError = vi.fn(); // stable like App's callback; a fresh function per render would re-run the load effect forever
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    vi.useRealTimers();
  });

  it("keeps refreshing a started Turn after the drawer closes until the server projects completion", async () => {
    vi.useFakeTimers();
    const running = conversation("c1", "닫힌 동안 완료", "질문", {
      turns: [turn("c1-t1", { state: "running", progress_state: "composing", execution_completed_at: null })],
    });
    const completed = conversation("c1", "닫힌 동안 완료", "질문", {
      version: 2,
      turns: [turn("c1-t1")],
    });
    vi.mocked(api.getConversations).mockResolvedValue([running]);
    vi.mocked(api.getConversation).mockResolvedValue(completed);
    const { result, rerender } = renderHook(
      ({ open }) => useConversations({ personaId: "mina", isOpen: open, onError }),
      { initialProps: { open: true } },
    );
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.isProcessing).toBe(true);
    rerender({ open: false });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800);
    });
    expect(api.getConversation).toHaveBeenCalledWith("c1");
    expect(result.current.activeConversation?.turns[0].state).toBe("completed");
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

  it("retries a failed follow-up through the ordinary message API with one stable candidate identity", async () => {
    const base = conversation("c1", "회의 후속", "이전 발화");
    const onError = vi.fn();
    vi.mocked(api.getConversations).mockResolvedValue([base]);
    vi.mocked(api.getConversation).mockResolvedValue(base);
    vi.mocked(api.sendConversationMessage)
      .mockRejectedValueOnce(new Error("response lost"))
      .mockResolvedValueOnce({ conversation_id: "c1", message_id: "m9", turn_id: "t9", queued: false, queue_size: 0 });
    const { result } = renderHook(() => useConversations({ personaId: "mina", isOpen: true, onError }));
    await waitFor(() => expect(result.current.activeConversation?.conversation_id).toBe("c1"));

    await act(async () => {
      expect(await result.current.send("c1", "다음 회의 일정을 잡아줘", [], undefined, "candidate-1")).toBe(false);
    });
    const [failed] = result.current.localFragments;
    expect(failed.follow_up_candidate_id).toBe("candidate-1");
    expect(failed.idempotency_key).toBe("follow-up:candidate-1");

    await act(async () => {
      await result.current.retryFragment(failed);
    });
    expect(api.sendConversationMessage).toHaveBeenNthCalledWith(
      1, "c1", "다음 회의 일정을 잡아줘", [], "follow-up:candidate-1", "candidate-1",
    );
    expect(api.sendConversationMessage).toHaveBeenNthCalledWith(
      2, "c1", "다음 회의 일정을 잡아줘", [], "follow-up:candidate-1", "candidate-1",
    );
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
    vi.mocked(api.sendConversationMessage).mockResolvedValue({ conversation_id: "c3", message_id: "m3", turn_id: "t3", queued: false, queue_size: 0 });
    vi.mocked(api.getConversation).mockResolvedValue(conversation("c3", "새 대화", "아직 대화 없음"));
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
    expect(api.createConversation).not.toHaveBeenCalled();
    expect(result.current.activeConversation).toBeNull();
    expect(result.current.draft).toBe("아직 대화 없음");

    await act(async () => {
      expect(await result.current.sendCurrent("아직 대화 없음", [])).toBe(true);
    });
    expect(api.createConversation).toHaveBeenCalledTimes(1);
    expect(result.current.activeConversation?.conversation_id).toBe("c3");
    expect(result.current.draft).toBe("");
  });

  it("keeps existing history while explicit new chat opens only a local blank state", async () => {
    const previous = conversation("c1", "기존 대화", "이전 질문");
    vi.mocked(api.getConversations).mockResolvedValue([previous]);
    const { result } = renderHook(() => useConversations({ personaId: "mina", isOpen: true, onError }));
    await waitFor(() => expect(result.current.activeConversation?.conversation_id).toBe("c1"));

    await act(async () => {
      expect(await result.current.start()).toBeNull();
    });
    expect(api.createConversation).not.toHaveBeenCalled();
    expect(result.current.activeConversation).toBeNull();
    expect(result.current.conversations.map((item) => item.conversation_id)).toEqual(["c1"]);
  });

  it("does not auto-open a legacy conversation with no utterance", async () => {
    const empty = conversation("empty", "새 대화", "", { messages: [], turns: [] });
    const answered = answeredConversation("answered", "기존 답변", "이전 질문");
    vi.mocked(api.getConversations).mockResolvedValue([empty, answered]);

    const { result } = renderHook(() => useConversations({ personaId: "mina", isOpen: true, onError }));

    await waitFor(() => expect(result.current.listStatus).toBe("ready"));
    expect(result.current.activeConversation?.conversation_id).toBe("answered");
  });

  it("creates a conversation before sending from a no-history start state", async () => {
    const created = conversation("c2", "새 대화", "", { messages: [], turns: [] });
    const projected = conversation("c2", "오늘 내 회의를 알려줘", "오늘 내 회의를 알려줘");
    vi.mocked(api.getConversations).mockResolvedValue([]);
    vi.mocked(api.createConversation).mockResolvedValue(created);
    vi.mocked(api.sendConversationMessage).mockResolvedValue({ conversation_id: "c2", message_id: "m2", turn_id: "t2", queued: false, queue_size: 0 });
    vi.mocked(api.getConversation).mockResolvedValue(projected);
    const { result } = renderHook(() => useConversations({ personaId: "mina", isOpen: true, onError }));
    await waitFor(() => expect(result.current.listStatus).toBe("ready"));
    expect(result.current.activeConversation).toBeNull();

    await act(async () => {
      expect(await result.current.sendCurrent("오늘 내 회의를 알려줘", [])).toBe(true);
    });
    expect(api.createConversation).toHaveBeenCalledTimes(1);
    expect(api.sendConversationMessage).toHaveBeenCalledWith("c2", "오늘 내 회의를 알려줘", [], expect.any(String), undefined);
    expect(result.current.activeConversation?.conversation_id).toBe("c2");
  });

  it("creates and submits the first turn exactly once when send is clicked twice rapidly", async () => {
    const created = conversation("c2", "새 대화", "", { messages: [], turns: [] });
    const projected = conversation("c2", "새 대화", "첫 질문");
    vi.mocked(api.getConversations).mockResolvedValue([]);
    vi.mocked(api.createConversation).mockResolvedValue(created);
    vi.mocked(api.sendConversationMessage).mockResolvedValue({ conversation_id: "c2", message_id: "m2", turn_id: "t2", queued: false, queue_size: 0 });
    vi.mocked(api.getConversation).mockResolvedValue(projected);
    const { result } = renderHook(() => useConversations({ personaId: "mina", isOpen: true, onError }));
    await waitFor(() => expect(result.current.listStatus).toBe("ready"));

    await act(async () => {
      await Promise.all([
        result.current.sendCurrent("첫 질문", []),
        result.current.sendCurrent("첫 질문", []),
      ]);
    });

    expect(api.createConversation).toHaveBeenCalledTimes(1);
    expect(api.sendConversationMessage).toHaveBeenCalledTimes(1);
  });

  it("reuses one in-flight submission when the same active-conversation message fires twice", async () => {
    const active = conversation("c1", "기존 대화", "이전 질문");
    const projected = conversation("c1", "기존 대화", "같은 질문");
    vi.mocked(api.getConversations).mockResolvedValue([active]);
    vi.mocked(api.sendConversationMessage).mockResolvedValue({ conversation_id: "c1", message_id: "m2", turn_id: "t2", queued: false, queue_size: 0 });
    vi.mocked(api.getConversation).mockResolvedValue(projected);
    const { result } = renderHook(() => useConversations({ personaId: "mina", isOpen: true, onError }));
    await waitFor(() => expect(result.current.activeConversation?.conversation_id).toBe("c1"));

    await act(async () => {
      await Promise.all([
        result.current.sendCurrent("같은 질문", []),
        result.current.sendCurrent("같은 질문", []),
      ]);
    });

    expect(api.sendConversationMessage).toHaveBeenCalledTimes(1);
  });
});

describe("AssistantMarkdown", () => {
  const listProps = { onDecide: noop, onRetryTurn: vi.fn(), onRetryFragment: vi.fn(), onDiscardFragment: vi.fn() };
  afterEach(cleanup);

  const taskResource = {
    reference_id: "task-ref",
    turn_id: "turn-1",
    sequence: 1,
    resource_type: "task" as const,
    resource_id: "task-1",
    resource_version: 3,
    title: "한글 업무 제목",
    state: "in_progress",
  };

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

  it("opens explicit canonical task, meeting, and request API links in product details instead of JSON tabs", () => {
    const onOpenResource = vi.fn();
    const meetingResource = { ...taskResource, reference_id: "meeting-ref", sequence: 2, resource_type: "meeting" as const, resource_id: "meeting-1", title: "한글 회의 제목" };
    const requestResource = { ...taskResource, reference_id: "request-ref", sequence: 3, resource_type: "work_request" as const, resource_id: "request-1", title: "한글 요청 제목" };
    render(
      <AssistantMarkdown
        body="[업무 링크](/api/tasks/task-1?from=ax#activity) [회의 링크](/api/meetings/meeting-1) [요청 링크](/api/work-requests/request-1/)"
        onOpenResource={onOpenResource}
        resources={[taskResource, meetingResource, requestResource]}
      />,
    );

    for (const name of ["업무 링크", "회의 링크", "요청 링크"]) {
      expect(screen.queryByRole("link", { name })).toBeNull();
      fireEvent.click(screen.getByRole("button", { name }));
    }
    expect(onOpenResource).toHaveBeenNthCalledWith(1, taskResource);
    expect(onOpenResource).toHaveBeenNthCalledWith(2, meetingResource);
    expect(onOpenResource).toHaveBeenNthCalledWith(3, requestResource);
  });

  it("does not infer unknown API ids or intercept material content and external links", () => {
    const onOpenResource = vi.fn();
    const { container } = render(
      <AssistantMarkdown
        body={[
          "[모르는 업무](/api/tasks/not-observed)",
          "[내부 명령](/api/tasks/task-1/start)",
          "[알 수 없는 content](/api/unknown/content)",
          "[파일 원본](/api/tasks/task-1/materials/material-1/content?download=1)",
          "[외부 자료](https://example.com/reference?q=한글#section)",
          "[외부 업무 API](https://example.com/api/tasks/task-1)",
        ].join(" ")}
        onOpenResource={onOpenResource}
        resources={[taskResource]}
      />,
    );

    expect(screen.queryByRole("link", { name: "모르는 업무" })).toBeNull();
    expect(screen.queryByRole("button", { name: "모르는 업무" })).toBeNull();
    expect(container.textContent).toContain("모르는 업무");
    expect(screen.queryByRole("link", { name: "내부 명령" })).toBeNull();
    expect(screen.queryByRole("button", { name: "내부 명령" })).toBeNull();
    expect(screen.queryByRole("link", { name: "알 수 없는 content" })).toBeNull();
    expect(screen.getByRole("link", { name: "파일 원본" }).getAttribute("href")).toBe("/api/tasks/task-1/materials/material-1/content?download=1");
    expect(screen.getByRole("link", { name: "외부 자료" }).getAttribute("href")).toBe("https://example.com/reference?q=%ED%95%9C%EA%B8%80#section");
    expect(screen.getByRole("link", { name: "외부 업무 API" }).getAttribute("href")).toBe("https://example.com/api/tasks/task-1");
    expect(onOpenResource).not.toHaveBeenCalled();
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
    const bubble = container.querySelector(".scax-msg--assistant[data-body-state='streaming']") as HTMLElement;
    expect(bubble.querySelector(".scax-md strong")?.textContent).toBe("부분");
    expect(bubble.querySelector(".scax-md .scax-msg__caret")).toBeNull();
    expect(bubble.querySelector(":scope > .scax-msg__body > .scax-msg__caret")).toBeTruthy();
    const cancelled = { ...streaming, messages: [streaming.messages[0], { ...streaming.messages[1], body_state: "cancelled" as const }], turns: [turn("c1-t1", { state: "cancelled", progress_state: "cancelled" })] };
    cleanup();
    const second = render(<MessageList {...listProps} conversation={cancelled} localFragments={[]} />).container;
    const note = second.querySelector(".scax-msg--assistant .scax-msg__note") as HTMLElement;
    expect(note.textContent).toBe("취소 시점까지의 답변");
    expect(note.closest(".scax-md")).toBeNull();
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
    const card = container.querySelector(".scax-actioncard") as HTMLElement;
    expect(card.querySelector("b")?.textContent).toBe("견적서 재검토");
    expect(card.querySelector(".scax-badge")?.textContent).toBe("AX 제안 · 업무 요청 · 승인 필요");
    expect(within(card).queryByText("업무 요청 생성 확인")).toBeNull(); // the operation title is not the subject
    const rows = Array.from(card.querySelectorAll(".scax-preview__row")).map((row) => [row.querySelector("dt")?.textContent, row.querySelector("dd")?.textContent]);
    expect(rows).toEqual([
      ["설명", "9월 견적 재검토"],
      ["요청 대상", "지호 (팀장)"],
      ["기한", "2026/09/30"],
    ]);
    expect((card.querySelector(".scax-preview") as HTMLDetailsElement).open).toBe(true); // pending: open for review
    // Rows mix created fields with linked grounds, so the summary must not claim every row gets applied.
    expect((card.querySelector(".scax-preview > summary") as HTMLElement).textContent).toBe("상세 보기 · 승인 전 확인할 3개 항목");
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
    const card = container.querySelector(".scax-actioncard") as HTMLElement;
    const row = card.querySelector(".scax-preview__row--evidence") as HTMLElement;
    expect(row.querySelector("dt")?.textContent).toBe("근거 자료");
    expect(row.querySelector("dd")?.textContent).toBe("견적.md, 계약서.pdf");
    // The row is rendered verbatim: the client neither reformats file names nor invents a link target.
    expect(row.querySelector("a")).toBeNull();
    expect(card.querySelectorAll(".scax-preview__row")).toHaveLength(2);
  });

  it("does not infer preview rows or commands from the action type when the server sends none", () => {
    const bare = conversation("c1", "새 대화", "업무 만들어줘", {
      actions: [
        { action_id: "a2", conversation_id: "c1", turn_id: "c1-t1", action_type: "task.create_self", title: "업무 생성 확인", state: "approved", version: 2, payload_summary: "업무 생성 확인", result: { task_id: "t1" }, audit_ref: "a2", commands: [] },
      ],
    });
    const { container } = render(<MessageList {...listProps} conversation={bare} localFragments={[]} />);
    const card = container.querySelector(".scax-actioncard") as HTMLElement;
    expect(card.querySelector("b")?.textContent).toBe("업무 생성 확인"); // falls back to the canonical title only
    expect(card.querySelector(".scax-preview")).toBeNull();
    expect(card.querySelectorAll("button")).toHaveLength(0);
    expect(card.querySelector(".scax-badge")?.textContent).toBe("AX 제안");
  });

  it("shows the server's partial batch outcome instead of claiming every item was applied", () => {
    const partial = conversation("c1", "새 대화", "업무 진행을 기록해줘", {
      actions: [
        {
          action_id: "batch-1",
          conversation_id: "c1",
          turn_id: "c1-t1",
          action_type: "task.progress.batch",
          title: "업무 진행 일괄 반영 확인",
          subject: "업무 진행 2건",
          operation_label: "업무 진행 일괄 반영",
          preview: [
            { id: "operation_1", label: "CPA 데이터 취합", value: "체크리스트 완료 · 반영됨", kind: "state" },
            { id: "operation_2", label: "플레이스 순위", value: "진행 메모 · 확인 중 · 대상 변경", kind: "state" },
          ],
          state: "approved",
          version: 2,
          payload_summary: "업무 진행 2건",
          result: { batch_state: "partial", applied_count: 1, total_count: 2 },
          result_summary: "1/2건 반영됨 · 나머지 항목 확인 필요",
          audit_ref: "batch-1",
          commands: [],
        },
      ],
    });
    const { container } = render(<MessageList {...listProps} conversation={partial} localFragments={[]} />);
    const card = container.querySelector(".scax-actioncard") as HTMLElement;
    expect(card.querySelector("small.approved")?.textContent).toBe("1/2건 반영됨 · 나머지 항목 확인 필요");
    expect(within(card).queryByText("승인됨 · 원장에 반영됨")).toBeNull();
  });

  it("edits a progress note inside one batch card and confirms the server-owned draft", async () => {
    const onDecide = vi.fn().mockResolvedValue(undefined);
    const pending = conversation("c1", "업무 일지", "업무 진행을 기록해줘", {
      actions: [
        {
          action_id: "batch-edit",
          conversation_id: "c1",
          turn_id: "c1-t1",
          action_type: "task.progress.batch",
          title: "업무 진행 일괄 반영 확인",
          subject: "업무 진행 2건",
          operation_label: "업무 진행 일괄 반영",
          preview: [
            { id: "operation_1", label: "CPA 데이터 취합", value: "체크리스트 완료", kind: "state" },
            { id: "operation_2", label: "플레이스 순위", value: "진행 메모 · 확인 중", kind: "state" },
          ],
          state: "pending",
          version: 1,
          payload_summary: "업무 진행 2건",
          result: null,
          audit_ref: null,
          commands: [
            { id: "confirm", label: "이 내용으로 반영", tone: "primary" },
            { id: "reject", label: "거절", tone: "neutral" },
          ],
          edit_contract: {
            editor: "task_progress_batch",
            base_submission_version: 1,
            fields: [],
            values: {
              operations: [
                { effect_id: "one", kind: "checklist.update", task_id: "task-1", item_id: "step-1", expected_version: 1, done: true },
                { effect_id: "two", kind: "progress.note", task_id: "task-2", expected_version: 1, summary: "확인 중" },
              ],
            },
          },
        },
      ],
    });
    const { container } = render(
      <MessageList {...listProps} onDecide={onDecide} conversation={pending} localFragments={[]} />,
    );
    const card = container.querySelector(".action-progress-batch-card") as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: "수정" }));
    fireEvent.change(within(card).getByLabelText("진행 내용 - 플레이스 순위"), { target: { value: "검수 중" } });
    fireEvent.click(within(card).getByRole("button", { name: "저장" }));
    await waitFor(() => expect(onDecide).toHaveBeenCalledWith(
      "batch-edit",
      1,
      "confirm",
      expect.objectContaining({
        base_submission_version: 1,
        draft: expect.objectContaining({
          operations: expect.arrayContaining([expect.objectContaining({ kind: "progress.note", summary: "검수 중" })]),
        }),
      }),
    ));
  });
});

describe("답변이 가리키는 것", () => {
  afterEach(cleanup);

  it("lists each thing the turn read as its own way in, and nothing when it read none", async () => {
    const base = conversation("c10", "오늘 업무", "제품팀장이 오늘 하는 업무 알려줘");
    const named = {
      ...base,
      messages: [
        ...base.messages,
        { message_id: "c10-m2", turn_id: base.turns[0].turn_id, role: "assistant", body: "분기 마감 정리와 주간 회의를 확인했습니다.", body_state: "final", sequence: 2, state: "accepted" },
      ],
      answer_resources: [
        {
          reference_id: "a1",
          turn_id: base.turns[0].turn_id,
          sequence: 1,
          resource_type: "task" as const,
          resource_id: "task-1",
          resource_version: 3,
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
    const listed = container.querySelector("section.scax-sources") as HTMLElement;
    expect(listed.textContent).toContain("분기 마감 정리");
    expect(listed.textContent).toContain("주간 회의");
    fireEvent.click(screen.getByRole("button", { name: "주간 회의" }));
    expect(onOpenResource).toHaveBeenCalledWith(expect.objectContaining({ resource_type: "meeting", resource_id: "meeting-1" }));
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
    expect(container.querySelector("section.scax-sources")).toBeNull();
  });
});

describe("근거", () => {
  afterEach(cleanup);

  it("says in one line what the answer stands on, and why each thing is there once opened", async () => {
    const base = conversation("c12", "관계 질문", "이 업무가 어디서 왔는지 알려줘");
    const turnId = base.turns[0].turn_id;
    const grounded = {
      ...base,
      graph_receipts: [
        {
          receipt_id: "r1",
          turn_id: turnId,
          sequence: 1,
          kind: "edge" as const,
          edge_kind: "produced",
          from_ref: "work_request:wr1",
          from_title: "분기 마감 요청",
          to_ref: "task:t1",
          to_title: "분기 마감",
          observed_at: "2026-09-06T00:00:00Z",
        },
      ],
      answer_resources: [
        {
          reference_id: "a1",
          turn_id: turnId,
          sequence: 1,
          resource_type: "task" as const,
          resource_id: "t1",
          resource_version: 2,
          title: "분기 마감",
          state: "in_progress",
        },
      ],
    };
    const { container } = render(
      <MessageList
        conversation={grounded as never}
        localFragments={[]}
        onDecide={vi.fn()}
        onDiscardFragment={vi.fn()}
        onRetryFragment={vi.fn()}
        onRetryTurn={vi.fn()}
      />,
    );
    // 답이 먼저 읽히도록 기본은 접힘이고, 한 줄이 딛고 있는 것의 크기를 말한다.
    const panel = container.querySelector("details.scax-rail__details") as HTMLDetailsElement;
    expect(panel.open).toBe(false);
    expect(panel.querySelector("summary")?.textContent).toContain("근거 1건");
    expect(panel.querySelector("summary")?.textContent).not.toContain("연결");
    // 제목만으로는 근거가 아니다: 실제로 걸어간 edge가 그 자리에 문장으로 붙는다.
    const row = panel.querySelector('li[data-resource="task:t1"]') as HTMLElement;
    expect(row.querySelector(".scax-sources__why")?.textContent).toBe("이 업무를 만든 요청 · 분기 마감 요청");
  });

  it("counts only what reached the screen, and stays away entirely when a turn stood on nothing", async () => {
    const base = conversation("c13", "인사", "안녕");
    const { container } = render(
      <MessageList
        conversation={base as never}
        localFragments={[]}
        onDecide={vi.fn()}
        onDiscardFragment={vi.fn()}
        onRetryFragment={vi.fn()}
        onRetryTurn={vi.fn()}
      />,
    );
    expect(container.querySelector("details.scax-rail__details")).toBeNull();
  });
});

describe("근거 상세 이동", () => {
  afterEach(cleanup);

  it("상세 열기를 누르면 중간 peek 없이 정본 상세 화면으로 바로 넘긴다", () => {
    const named = {
      reference_id: "a1",
      turn_id: "c1-t1",
      sequence: 1,
      resource_type: "task" as const,
      resource_id: "task-1",
      resource_version: 2,
      title: "분기 마감",
      state: "in_progress",
    };
    const openFully = vi.fn();
    const { props } = renderDrawer({
      message: "쓰다 만 문장",
      onOpenResource: openFully,
      activeConversation: {
        ...conversation("c1", "견적 검토", "견적서 납기일을 알려줘"),
        answer_resources: [named],
      } as never,
    });
    expect(props.message).toBe("쓰다 만 문장");

    // 근거는 접혀 있다 — 답이 먼저 읽히도록.
    const panel = document.querySelector("details.scax-rail__details") as HTMLDetailsElement;
    fireEvent.click(panel.querySelector("summary") as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: "상세 열기" }));
    expect(screen.queryByLabelText("근거 상세")).toBeNull();
    expect(openFully).toHaveBeenCalledWith(expect.objectContaining({ resource_type: "task", resource_id: "task-1" }));
  });
});

describe("근거가 딛고 선 것", () => {
  afterEach(cleanup);

  it("원문의 어디였는지를 말하고, 답변 뒤에 바뀌었으면 열기 전에 알려 준다", async () => {
    const base = conversation("c14", "자료 질문", "견적서에 납기일이 뭐라고 되어 있어?");
    const turnId = base.turns[0].turn_id;
    const grounded = {
      ...base,
      answer_resources: [
        {
          reference_id: "a1",
          turn_id: turnId,
          sequence: 1,
          resource_type: "material" as const,
          resource_id: "m1",
          resource_version: null,
          title: "견적서.pdf",
          state: null,
          source_locator: { page: 12 },
        },
        {
          reference_id: "a2",
          turn_id: turnId,
          sequence: 2,
          resource_type: "task" as const,
          resource_id: "t1",
          resource_version: 3,
          title: "분기 마감",
          state: "in_progress",
          current_version: 5,
          changed_since: true,
        },
      ],
    };
    const { container } = render(
      <MessageList
        conversation={grounded as never}
        localFragments={[]}
        onDecide={vi.fn()}
        onDiscardFragment={vi.fn()}
        onRetryFragment={vi.fn()}
        onRetryTurn={vi.fn()}
      />,
    );
    const material = container.querySelector('li[data-resource="material:m1"]') as HTMLElement;
    expect(material.textContent).toContain("12쪽");
    // 자리만 말하고 원문은 오지 않는다. 발췌는 근거 카드가 갖는다.
    expect(material.textContent).not.toContain("납기일");

    const task = container.querySelector('li[data-resource="task:t1"]') as HTMLElement;
    expect(task.querySelector(".scax-sources__changed")?.textContent).toBe("답변 뒤 바뀜");
    // 바뀌지 않은 것에는 그 말이 붙지 않는다.
    expect(material.querySelector(".scax-sources__changed")).toBeNull();
  });
});

describe("실행 영수증 위치", () => {
  afterEach(cleanup);

  it("does not render graph UI and places the completed timeline after the answer", async () => {
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
    expectNoGraphUi(container);
    expect(container.querySelector(".scax-rail details")).toBeNull();

    const done = conversation("c11", "관계 질문", "이 업무가 어디서 왔는지 알려줘");
    done.messages.push({ message_id: "c11-m2", turn_id: done.turns[0].turn_id, role: "assistant", body: "분기 마감 업무입니다.", body_state: "final", sequence: 2, state: "accepted" });
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
    expect(turnOrder(container)).toEqual(["user", "assistant", "rail"]);
    expect(container.querySelector(".scax-rail--terminal details")).toBeNull();
    expectNoGraphUi(container);
  });
});
