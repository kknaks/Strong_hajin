import { describe, expect, it } from "vitest";

import {
  advanceAssistantCompletionObservation,
  deriveAssistantPresentationState,
  initialAssistantCompletionObservation,
} from "./assistantPresentation";
import type { Conversation } from "../../lib/viewModels";


function conversation(overrides: Partial<Conversation> = {}): Conversation {
  return {
    conversation_id: "conversation-1",
    title: "상태 확인",
    version: 1,
    messages: [],
    turns: [],
    context_references: [],
    tool_invocations: [],
    actions: [],
    ...overrides,
  };
}

describe("assistant presentation state", () => {
  it("uses the approved priority without inventing state outside Turn and Action projections", () => {
    const projected = conversation({
      messages: [{
        message_id: "message-1",
        turn_id: "turn-1",
        role: "user",
        body: "업무를 만들어줘",
        sequence: 1,
        state: "accepted",
      }],
      turns: [{
        turn_id: "turn-1",
        state: "running",
        progress_state: "tool_running",
        provider_run_ref: null,
        provider_session_ref: null,
        error: null,
      }],
      actions: [{
        action_id: "action-1",
        conversation_id: "conversation-1",
        turn_id: "turn-1",
        action_type: "task.create_self",
        title: "업무 생성 확인",
        subject: "검토",
        operation_label: "업무 생성",
        state: "pending",
        version: 1,
        payload_summary: "업무 생성: 검토",
        result: null,
        audit_ref: null,
        commands: [{ id: "confirm", label: "이 내용으로 업무 생성", tone: "primary" }],
        preview: [],
      }],
    });

    expect(deriveAssistantPresentationState({ conversations: [projected], answerReady: true, hovered: true }).kind)
      .toBe("attention");
    expect(deriveAssistantPresentationState({
      conversations: [{ ...projected, actions: [] }],
      answerReady: true,
      hovered: true,
    }).kind).toBe("working");
    expect(deriveAssistantPresentationState({ conversations: [], answerReady: true, hovered: true }).kind)
      .toBe("answer-ready");
    expect(deriveAssistantPresentationState({ conversations: [], answerReady: false, hovered: true }).kind)
      .toBe("hover");
    expect(deriveAssistantPresentationState({ conversations: [], answerReady: false, hovered: false }).kind)
      .toBe("idle");
  });

  it("shows error only for a failed Turn that does not already have a retry", () => {
    const failed = {
      turn_id: "turn-failed",
      state: "failed",
      progress_state: "failed" as const,
      provider_run_ref: null,
      provider_session_ref: null,
      error: "provider failed",
    };
    expect(deriveAssistantPresentationState({
      conversations: [conversation({ turns: [failed] })],
      answerReady: false,
      hovered: false,
    }).kind).toBe("error");

    const retry = {
      ...failed,
      turn_id: "turn-retry",
      state: "running",
      progress_state: "retrying" as const,
      retry_of_turn_id: failed.turn_id,
      error: null,
    };
    expect(deriveAssistantPresentationState({
      conversations: [conversation({ turns: [failed, retry] })],
      answerReady: false,
      hovered: false,
    }).kind).toBe("working");
  });

  it("raises answer-ready only when an observed active Turn completes while the drawer is closed", () => {
    const running = conversation({ turns: [{
      turn_id: "turn-1",
      state: "running",
      progress_state: "composing",
      provider_run_ref: null,
      provider_session_ref: null,
      error: null,
    }] });
    const first = advanceAssistantCompletionObservation(
      initialAssistantCompletionObservation,
      [running],
      false,
    );
    expect(first.answerReadyTurnId).toBeNull();

    const completed = conversation({ turns: [{ ...running.turns[0], state: "completed", progress_state: "completed" }] });
    const ready = advanceAssistantCompletionObservation(first, [completed], false);
    expect(ready.answerReadyTurnId).toBe("turn-1");
    expect(advanceAssistantCompletionObservation(ready, [completed], true).answerReadyTurnId).toBeNull();

    const initiallyCompleted = advanceAssistantCompletionObservation(
      initialAssistantCompletionObservation,
      [completed],
      false,
    );
    expect(initiallyCompleted.answerReadyTurnId).toBeNull();
  });
});
