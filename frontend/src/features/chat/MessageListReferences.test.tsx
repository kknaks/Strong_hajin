import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Conversation, ConversationContextReference } from "../../lib/viewModels";
import { MessageList } from "./MessageList";

/*
 * WORK-012 WP4-FE — 말풍선 아래 참고 자료 한 줄(SPEC-008 §2.9 ③-2 · OQ-816 · WP4 계약 고정 1).
 * 대화 응답 `context_references[]` 의 `turn_id` · `label`(서버가 조합 때 만든 «실제 실은 범위») — `inbox_message` 이고 같은 턴이면
 * 그 턴의 사용자 말풍선 아래에 그대로 그린다. `label` 이 아직 없으면(조합 전) 줄을 그리지 않는다.
 */

const turn = (id: string) => ({
  turn_id: id,
  state: "completed",
  progress_state: "completed",
  provider_run_ref: null,
  provider_session_ref: null,
  error: null,
  queued_at: "2026-10-07T00:00:00Z",
  execution_started_at: "2026-10-07T00:00:01Z",
  execution_completed_at: "2026-10-07T00:00:05Z",
  queue_wait_ms: 1000,
  run_ms: 4000,
});

function conversation(references: ConversationContextReference[]): Conversation {
  return {
    conversation_id: "c1",
    title: "대화",
    version: 1,
    messages: [
      { message_id: "m1", turn_id: "t1", role: "user", body: "이 메시지 읽고 업무를 생성해 줘", sequence: 1, state: "accepted", idempotency_key: "k1" },
      { message_id: "m2", turn_id: "t2", role: "user", body: "다른 질문", sequence: 2, state: "accepted", idempotency_key: "k2" },
    ],
    turns: [turn("t1"), turn("t2")],
    context_references: references,
    tool_invocations: [],
    has_more_messages: false,
    first_user_message_excerpt: "이 메시지 읽고 업무를 생성해 줘",
    user_message_count: 2,
    has_final_answer: false,
    queued_message_count: 0,
    latest_turn_state: "completed",
    actions: [],
  } as unknown as Conversation;
}

const ref = (over: Partial<ConversationContextReference>): ConversationContextReference => ({
  resource_type: "inbox_message",
  resource_id: "msg-7",
  resource_version: 1,
  included: true,
  turn_id: "t1",
  label: "슬랙 · #pilot-launch · 10/07 09:10~10/07 11:42 · 57건",
  ...over,
});

function renderList(references: ConversationContextReference[]) {
  render(
    <MessageList conversation={conversation(references)} localFragments={[]} onDecide={vi.fn()} onDiscardFragment={vi.fn()} onRetryFragment={vi.fn()} onRetryTurn={vi.fn()} />,
  );
}

const turnSection = (id: string) => document.querySelector(`.scax-turn[data-turn-id="${id}"]`) as HTMLElement;

afterEach(cleanup);

describe("말풍선 아래 참고 자료 한 줄", () => {
  it("그 턴의 사용자 말풍선 바로 아래에 서버 글자 그대로 — 다른 턴에는 서지 않는다", () => {
    renderList([ref({})]);
    const list = within(turnSection("t1")).getByRole("list", { name: "참고 자료" });
    expect(list.textContent).toBe("슬랙 · #pilot-launch · 10/07 09:10~10/07 11:42 · 57건");
    // 말풍선 다음 자리다
    expect(list.previousElementSibling?.classList.contains("scax-msg--user")).toBe(true);
    expect(within(turnSection("t2")).queryByRole("list", { name: "참고 자료" })).toBeNull();
  });

  it("스레드 · 메일 · 카톡 모양도 글자 그대로", () => {
    renderList([
      ref({ resource_id: "a", label: "슬랙 · 스레드 · 4건" }),
      ref({ resource_id: "b", label: "메일 · 2차 파일럿 일정표" }),
      ref({ resource_id: "c", label: "카톡 · 박지윤 · 10/06 08:00~10/07 09:00 · 12건" }),
    ]);
    const items = within(turnSection("t1")).getAllByRole("listitem").map((item) => item.textContent);
    expect(items).toEqual(["슬랙 · 스레드 · 4건", "메일 · 2차 파일럿 일정표", "카톡 · 박지윤 · 10/06 08:00~10/07 09:00 · 12건"]);
  });

  it("`label` 이 아직 없으면(조합 전) 줄이 없다 · 업무·요청 참고 자료는 이 줄을 쓰지 않는다", () => {
    renderList([ref({ label: null }), ref({ resource_type: "task", resource_id: "task-1", label: "업무 · 무엇" })]);
    expect(screen.queryByRole("list", { name: "참고 자료" })).toBeNull();
  });
});
