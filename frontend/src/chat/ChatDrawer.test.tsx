import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Conversation } from "../viewModels";
import { ChatDrawer } from "./ChatDrawer";
import { MessageList } from "./MessageList";
import type { LocalFragment } from "./useConversations";

function conversation(id: string, title: string, firstMessage: string, extra: Partial<Conversation> = {}): Conversation {
  return {
    conversation_id: id,
    title,
    version: 1,
    messages: [{ message_id: `${id}-m1`, turn_id: `${id}-t1`, role: "user", body: firstMessage, sequence: 1, state: "accepted" }],
    turns: [{ turn_id: `${id}-t1`, state: "completed", provider_run_ref: null, provider_session_ref: null, error: null }],
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
    canDecideActions: true,
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
    onRetryFragment: vi.fn(),
    onDiscardFragment: vi.fn(),
    onRetryList: vi.fn(),
    ...overrides,
  };
  return { ...render(<ChatDrawer {...props} />), props };
}

describe("ChatDrawer session switcher", () => {
  afterEach(cleanup);

  it("lists conversations vertically and filters them by title or first message", () => {
    renderDrawer();
    const list = screen.getByRole("list", { hidden: true }).closest(".ax-conversation-list") ? screen.getByRole("list") : screen.getAllByRole("list")[0];
    expect(within(list).getAllByRole("button")).toHaveLength(3);
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

  it("keeps the composer usable while a turn runs and labels the send as queueing", () => {
    renderDrawer({ isProcessing: true, message: "이어서 질문" });
    const composer = screen.getByLabelText("AX 메시지") as HTMLTextAreaElement;
    expect(composer.hasAttribute("disabled")).toBe(false);
    expect(screen.getByRole("button", { name: "대기열에 보내기" }).hasAttribute("disabled")).toBe(false);
    expect(screen.getByRole("button", { name: "실행 취소" })).toBeTruthy();
  });
});

describe("MessageList", () => {
  afterEach(cleanup);

  const listProps = {
    canDecideActions: true,
    onDecide: noop,
    onRetryFragment: vi.fn(),
    onDiscardFragment: vi.fn(),
  };

  it("shows queued fragments in order and a failed local fragment with retry and discard", () => {
    const active = conversation("c1", "견적 검토", "첫 발화", {
      messages: [
        { message_id: "m1", turn_id: "t1", role: "user", body: "첫 발화", sequence: 1, state: "accepted" },
        { message_id: "m2", turn_id: null, role: "user", body: "둘째 발화", sequence: 2, state: "queued" },
        { message_id: "m3", turn_id: null, role: "user", body: "셋째 발화", sequence: 3, state: "queued" },
      ],
      turns: [{ turn_id: "t1", state: "running", provider_run_ref: null, provider_session_ref: null, error: null }],
    });
    const failed: LocalFragment = { local_id: "l1", conversation_id: "c1", body: "네 번째 발화", state: "failed", error: "네트워크 오류", context: [], idempotency_key: "k1" };
    const onRetryFragment = vi.fn();
    const onDiscardFragment = vi.fn();
    render(<MessageList {...listProps} conversation={active} localFragments={[failed]} onDiscardFragment={onDiscardFragment} onRetryFragment={onRetryFragment} />);

    const queue = screen.getByRole("list", { name: "대기열" });
    const items = within(queue).getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual(["대기 1둘째 발화 대기 중", "대기 2셋째 발화 대기 중"]);
    expect(screen.getByText(/접수 실패 · 네트워크 오류/)).toBeTruthy();
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
    scroller.scrollTop = 0; // reader scrolled up
    fireEvent.scroll(scroller);
    expect(screen.queryByRole("button", { name: "↓ 새 메시지" })).toBeNull();

    const second = { ...first, version: 2, messages: [...first.messages, { message_id: "m2", turn_id: "t1", role: "assistant" as const, body: "답변입니다", sequence: 2, state: "accepted" as const }] };
    rerender(<MessageList {...listProps} conversation={second} localFragments={[]} />);
    expect(scroller.scrollTop).toBe(0);
    const jump = screen.getByRole("button", { name: "↓ 새 메시지" });
    fireEvent.click(jump);
    expect(scroller.scrollTop).toBe(1000);
    expect(screen.queryByRole("button", { name: "↓ 새 메시지" })).toBeNull();
  });

  it("renders tools as a compact receipt whose expanded rows carry redacted input and latency", () => {
    const withTool = conversation("c1", "견적 검토", "내 업무 수 알려줘", {
      tool_invocations: [
        {
          turn_id: "c1-t1", sequence: 1, provider_call_id: "call-1", tool_name: "task_list", display_name: "task list", input_summary: "입력 없음",
          state: "completed", result_summary: "결과: 3건 조회", error_summary: null, latency_ms: 321, target_resource_id: null, target_resource_version: null, audit_ref: null,
        },
      ],
    });
    render(<MessageList {...listProps} conversation={withTool} localFragments={[]} />);
    const receipt = screen.getByText(/도구 1개 실행 · 완료/).closest("details") as HTMLDetailsElement;
    expect(receipt.open).toBe(false);
    expect(screen.getByText("task list · 완료")).toBeTruthy();
    expect(screen.getByText("입력 없음 · 321ms")).toBeTruthy();
  });
});
