import type { Conversation } from "./viewModels";


export type AssistantPresentationKind = "attention" | "error" | "working" | "answer-ready" | "hover" | "idle";

export type AssistantPresentationState = {
  kind: AssistantPresentationKind;
  label: string;
  prompt: string;
};

const PRESENTATION: Record<AssistantPresentationKind, AssistantPresentationState> = {
  attention: { kind: "attention", label: "결정 필요", prompt: "확인이 필요한 제안을 보여줘" },
  error: { kind: "error", label: "오류 확인", prompt: "실패한 요청을 확인해줘" },
  working: { kind: "working", label: "처리 중", prompt: "진행 중인 요청을 보여줘" },
  "answer-ready": { kind: "answer-ready", label: "답변 도착", prompt: "방금 답변에서 이어서 알려줘" },
  hover: { kind: "hover", label: "AX와 대화", prompt: "오늘 할 일을 정리해줘" },
  idle: { kind: "idle", label: "대기 중", prompt: "오늘 할 일을 정리해줘" },
};

type PresentationInput = {
  conversations: Conversation[];
  answerReady: boolean;
  hovered: boolean;
};

/**
 * One closed priority table for the assistant's visual state. It only reads canonical Turn and Action projections;
 * it never infers mood, urgency, or work that the server did not project.
 */
export function deriveAssistantPresentationState({
  conversations,
  answerReady,
  hovered,
}: PresentationInput): AssistantPresentationState {
  const turns = conversations.flatMap((conversation) => conversation.turns);
  const actions = conversations.flatMap((conversation) => conversation.actions ?? []);
  const retriedTurnIds = new Set(turns.map((turn) => turn.retry_of_turn_id).filter(Boolean));

  if (actions.some((action) => action.state === "pending" && (action.commands?.length ?? 0) > 0)) {
    return PRESENTATION.attention;
  }
  if (turns.some((turn) => turn.state === "failed" && !retriedTurnIds.has(turn.turn_id))) {
    return PRESENTATION.error;
  }
  if (
    turns.some((turn) => turn.state === "pending" || turn.state === "running")
    || conversations.some((conversation) => conversation.messages.some((message) => message.state === "queued"))
  ) {
    return PRESENTATION.working;
  }
  if (answerReady) return PRESENTATION["answer-ready"];
  if (hovered) return PRESENTATION.hover;
  return PRESENTATION.idle;
}

export type AssistantCompletionObservation = {
  turnStates: Record<string, string>;
  answerReadyTurnId: string | null;
};

export const initialAssistantCompletionObservation: AssistantCompletionObservation = {
  turnStates: {},
  answerReadyTurnId: null,
};

/** Keep the transient `answer-ready` signal tied to a real active→completed transition while chat is closed. */
export function advanceAssistantCompletionObservation(
  previous: AssistantCompletionObservation,
  conversations: Conversation[],
  drawerOpen: boolean,
): AssistantCompletionObservation {
  const turnStates = { ...previous.turnStates };
  let answerReadyTurnId = drawerOpen ? null : previous.answerReadyTurnId;
  for (const conversation of conversations) {
    for (const turn of conversation.turns) {
      const prior = previous.turnStates[turn.turn_id];
      if (!drawerOpen && (prior === "pending" || prior === "running") && turn.state === "completed") {
        answerReadyTurnId = turn.turn_id;
      }
      turnStates[turn.turn_id] = turn.state;
    }
  }
  return { turnStates, answerReadyTurnId };
}

/** Per-message profile state uses the same vocabulary without borrowing unrelated conversations. */
export function assistantTurnPresentationState(conversation: Conversation, turnId: string): AssistantPresentationState {
  const turn = conversation.turns.find((candidate) => candidate.turn_id === turnId);
  const actions = (conversation.actions ?? []).filter((action) => action.turn_id === turnId);
  return deriveAssistantPresentationState({
    conversations: [{ ...conversation, turns: turn ? [turn] : [], actions, messages: [] }],
    answerReady: false,
    hovered: false,
  });
}
