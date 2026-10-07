import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/api", async (actual) => ({
  ...((await actual()) as object),
  readMeetingRooms: vi.fn(),
}));

import * as api from "../../lib/api";
import type { ActionItem, Conversation } from "../../lib/viewModels";
import { MessageList } from "./MessageList";

/*
 * WORK-012 WP3-FE — 채팅 카드 고르기(`MessageList.tsx` 갈래 · SPEC-010 §2.4 · §5 서버 「화면 카드 고르기에 갈래를 더한다」).
 * - 회의 생성(`meeting.reservation.create` + `meeting` 편집기) → 쪽 나눔 카드(`AxDraftCard`) — 예전 `ActionMeetingCard` 가 아니다
 * - 회의 수정(`meeting.info.update` + `meeting_update` 편집기) → 고칠 수 있는 수정 카드 — 예전 결과 카드(`ActionResultCard`)가 아니다
 */

const action = (over: Partial<ActionItem>): ActionItem => ({
  action_id: "a1",
  conversation_id: "c1",
  turn_id: "c1-t1",
  action_type: "meeting.reservation.create",
  title: "회의 생성 확인",
  subject: "주간 회의",
  operation_label: "회의 생성",
  state: "pending",
  version: 1,
  payload_summary: "",
  result: null,
  audit_ref: null,
  preview: [],
  commands: [
    { id: "confirm", label: "확정", tone: "primary" },
    { id: "reject", label: "거절", tone: "neutral" },
  ],
  ...over,
});

function conversationWith(actions: ActionItem[]): Conversation {
  return {
    conversation_id: "c1",
    title: "대화",
    version: 1,
    messages: [{ message_id: "m1", turn_id: "c1-t1", role: "user", body: "회의 잡아 줘", sequence: 1, state: "accepted", idempotency_key: "k1" }],
    turns: [
      {
        turn_id: "c1-t1",
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
      },
    ],
    context_references: [],
    tool_invocations: [],
    has_more_messages: false,
    first_user_message_excerpt: "회의 잡아 줘",
    user_message_count: 1,
    has_final_answer: false,
    queued_message_count: 0,
    latest_turn_state: "completed",
    actions,
  } as unknown as Conversation;
}

const meetingValues = { title: "주간 회의", starts_at: "2026-10-08T06:00:00Z", ends_at: "2026-10-08T07:00:00Z", attendee_ids: [], external_attendees: [], room_id: null };

function renderList(actions: ActionItem[]) {
  render(
    <MessageList
      conversation={conversationWith(actions)}
      localFragments={[]}
      onDecide={vi.fn(async () => undefined)}
      onDiscardFragment={vi.fn()}
      onRetryFragment={vi.fn()}
      onRetryTurn={vi.fn()}
    />,
  );
}

beforeEach(() => vi.mocked(api.readMeetingRooms).mockResolvedValue([]));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("채팅 카드 고르기 — 회의 생성 · 회의 수정", () => {
  it("회의 생성은 쪽 나눔 카드(AX 배지 · 회차 · 쪽 넷)로 선다", () => {
    renderList([action({ edit_contract: { editor: "meeting", base_submission_version: 1, values: meetingValues, fields: [] } })]);
    const card = screen.getByRole("region", { name: /AX 회의 생성/ });
    expect(card.classList.contains("ax-draft-card")).toBe(true);
    expect(card.querySelectorAll('[role="tab"]')).toHaveLength(4);
    expect(document.querySelector(".action-meeting-card:not(.action-meeting-update-card)")).toBeNull();
  });

  it("회의 수정은 고칠 수 있는 수정 카드로 선다 — 결과 카드가 아니다", () => {
    renderList([
      action({
        action_type: "meeting.info.update",
        operation_label: "회의 수정",
        edit_contract: { editor: "meeting_update", base_submission_version: 1, values: { ...meetingValues, meeting_id: "m1", room_name: null }, fields: [] },
      }),
    ]);
    const card = screen.getByRole("region", { name: /AX 회의 수정/ });
    expect(card.querySelector("input")).toBeTruthy();
    // 카드는 하나뿐이고 그것이 수정 카드다 — 결과 카드(`section.scax-actioncard` 만 단 것)가 따로 서지 않는다
    const cards = document.querySelectorAll("section.scax-actioncard");
    expect(cards).toHaveLength(1);
    expect(cards[0]).toBe(card);
    expect(cards[0].classList.contains("action-meeting-update-card")).toBe(true);
  });
});
