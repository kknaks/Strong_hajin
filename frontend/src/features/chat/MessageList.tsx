import { useEffect, useRef, useState } from "react";

import { CommandConfirmationForm } from "../action/CommandConfirmationForm";
import { ActionCommandButtons, ActionPreviewDetails, actionKicker, actionSubject } from "../action/ActionPreview";
import { ActionTaskCard } from "../action/ActionTaskCard";
import { ActionMeetingCard } from "../action/ActionMeetingCard";
import { ActionProgressBatchCard } from "../action/ActionProgressBatchCard";
import { Badge } from "../../ds/Badge";
import { Button } from "../../ds/Button";
import { formatDate, formatDuration, isoDateInSeoul, taskStateLabel } from "../../lib/labels";
import { AssistantMarkdown } from "./AssistantMarkdown";
import { EDGE_SENTENCE } from "../graph/GraphCanvas";
import type { ActionItem, AnswerResource, Conversation, ConversationTurn, FollowUpCandidate, GraphReceipt, MaterialEvidence } from "../../lib/viewModels";
import type { LocalFragment } from "./useConversations";
import { Icon, type IconName } from "../../ds/icons/Icon";

const BOTTOM_SLACK_PX = 24;

type Tools = Conversation["tool_invocations"];
type Tool = Tools[number];

function terminalToolRows(tools: Tools): Array<{ tool: Tool; count: number; latencyMs: number | null }> {
  const rows: Array<{ tool: Tool; count: number; latencyMs: number; hasLatency: boolean }> = [];
  for (const tool of tools) {
    // Only adjacent calls collapse. The same tool appearing later remains a new row so execution order stays legible;
    // successful and failed calls also stay separate so grouping never hides a partial failure.
    const previous = rows.at(-1);
    const row = previous?.tool.tool_name === tool.tool_name && previous.tool.state === tool.state
      ? previous
      : { tool, count: 0, latencyMs: 0, hasLatency: false };
    if (row !== previous) rows.push(row);
    row.count += 1;
    if (tool.latency_ms !== null) {
      row.latencyMs += tool.latency_ms;
      row.hasLatency = true;
    }
  }
  return rows.map(({ tool, count, latencyMs, hasLatency }) => ({
    tool,
    count,
    latencyMs: hasLatency ? latencyMs : null,
  }));
}

/**
 * Turn timeline with bottom-aware autoscroll: follows new events only while the reader is at the bottom;
 * otherwise a `새 메시지` affordance offers the jump and never steals the scroll position.
 *
 * One turn renders live work below the request. Once terminal, a compact receipt moves below the assistant answer;
 * opening it reveals the A-style tool timeline and answer evidence together, then action cards and follow-ups continue.
 */
export function MessageList({
  personaId = "",
  conversation,
  localFragments,
  onDecide,
  onRetryTurn,
  onRetryFragment,
  onDiscardFragment,
  onFollowUpCandidate,
  onOpenResource,
  onOpenTask,
  onOpenMeeting,
}: {
  personaId?: string;
  conversation: Conversation | null;
  localFragments: LocalFragment[];
  onDecide: (
    actionId: string,
    expectedVersion: number,
    decision: string,
    payload?: { base_submission_version?: number; draft?: Record<string, unknown> },
  ) => Promise<void>;
  onRetryTurn: (turnId: string) => void;
  onRetryFragment: (fragment: LocalFragment) => void;
  onDiscardFragment: (localId: string) => void;
  onFollowUpCandidate?: (candidate: FollowUpCandidate) => Promise<boolean>;
  /** Open one thing the answer points at, in the surface that owns it. */
  onOpenResource?: (resource: AnswerResource) => void;
  /** Open the Task named by a persisted Action receipt. */
  onOpenTask?: (taskId: string) => void;
  /** Open the Meeting named by a persisted Action receipt. */
  onOpenMeeting?: (meetingId: string) => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const [following, setFollowing] = useState(true);
  const [hasNew, setHasNew] = useState(false);
  // Content signature: anything that changes visible height must be here, including partial-text growth and
  // tool receipts appearing/finishing under an unchanged progress state.
  const contentKey = conversation
    ? [
        conversation.conversation_id,
        conversation.version,
        conversation.messages.map((item) => `${item.message_id}:${item.body_state ?? "final"}:${item.body.length}`).join("|"),
        conversation.turns.map((turn) => `${turn.turn_id}:${turn.state}/${turn.progress_state ?? ""}/${turn.current_tool_display_name ?? ""}`).join("|"),
        conversation.tool_invocations.map((tool) => `${tool.turn_id}:${tool.sequence}:${tool.state}`).join("|"),
        (conversation.actions ?? []).map((action) => `${action.action_id}:${action.state}`).join("|"),
        localFragments.map((item) => `${item.local_id}:${item.state}`).join("|"),
      ].join("#")
    : "";
  const lastConversationId = useRef<string | null>(null);
  const latestTurn = conversation?.turns.at(-1);
  const latestFollowUpTurn = conversation
    && localFragments.length === 0
    && !conversation.messages.some((message) => message.state === "queued")
    && latestTurn?.state === "completed"
    && conversation.messages.some((message) => (
      message.turn_id === latestTurn.turn_id && message.role === "assistant" && message.body_state === "final"
    ))
      ? latestTurn
      : undefined;

  useEffect(() => {
    const element = scroller.current;
    if (!element) return;
    const switched = lastConversationId.current !== (conversation?.conversation_id ?? null);
    lastConversationId.current = conversation?.conversation_id ?? null;
    if (switched || following) {
      element.scrollTop = element.scrollHeight;
      setHasNew(false);
      if (switched) setFollowing(true);
    } else {
      setHasNew(true);
    }
  }, [contentKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const onScroll = () => {
    const element = scroller.current;
    if (!element) return;
    const atBottom = element.scrollHeight - element.scrollTop - element.clientHeight <= BOTTOM_SLACK_PX;
    setFollowing(atBottom);
    if (atBottom) setHasNew(false);
  };

  const jumpToBottom = () => {
    const element = scroller.current;
    if (!element) return;
    element.scrollTop = element.scrollHeight;
    setFollowing(true);
    setHasNew(false);
  };

  return (
    <div className="scax-chat__stream">
      <div className="scax-chat__messages" onScroll={onScroll} ref={scroller}>
        {conversation ? (
          <ConversationTimeline
            personaId={personaId}
            conversation={conversation}
            followUpTurnId={latestFollowUpTurn?.follow_up_candidates?.length ? latestFollowUpTurn.turn_id : null}
            localFragments={localFragments}
            onDecide={onDecide}
            onDiscardFragment={onDiscardFragment}
            onFollowUpCandidate={onFollowUpCandidate}
            onOpenResource={onOpenResource}
            onOpenTask={onOpenTask}
            onOpenMeeting={onOpenMeeting}
            onRetryFragment={onRetryFragment}
            onRetryTurn={onRetryTurn}
          />
        ) : null}
      </div>
      {hasNew && !following && (
        <button className="scax-chat__jump" onClick={jumpToBottom} type="button">
          <Icon name="arrow-down" size={12} /> 새 메시지
        </button>
      )}
    </div>
  );
}

function ConversationTimeline({
  personaId,
  conversation,
  followUpTurnId,
  localFragments,
  onDecide,
  onRetryTurn,
  onRetryFragment,
  onDiscardFragment,
  onFollowUpCandidate,
  onOpenResource,
  onOpenTask,
  onOpenMeeting,
}: {
  personaId: string;
  conversation: Conversation;
  followUpTurnId: string | null;
  localFragments: LocalFragment[];
  onDecide: (
    actionId: string,
    expectedVersion: number,
    decision: string,
    payload?: { base_submission_version?: number; draft?: Record<string, unknown> },
  ) => Promise<void>;
  onRetryTurn: (turnId: string) => void;
  onRetryFragment: (fragment: LocalFragment) => void;
  onDiscardFragment: (localId: string) => void;
  onFollowUpCandidate?: (candidate: FollowUpCandidate) => Promise<boolean>;
  onOpenResource?: (resource: AnswerResource) => void;
  onOpenTask?: (taskId: string) => void;
  onOpenMeeting?: (meetingId: string) => void;
}) {
  const queuedMessages = conversation.messages.filter((item) => item.state === "queued");

  if (conversation.turns.length === 0 && queuedMessages.length === 0 && localFragments.length === 0) {
    return <p className="scax-chat__empty">아직 발화가 없습니다. 아래에 요청을 적어 보내 주세요.</p>;
  }

  return (
    <>
      {conversation.turns.map((turn) => {
        const messages = conversation.messages.filter((item) => item.turn_id === turn.turn_id);
        const tools = conversation.tool_invocations.filter((tool) => tool.turn_id === turn.turn_id);
        const evidence = (conversation.material_evidence ?? []).filter((item) => item.turn_id === turn.turn_id);
        const walked = (conversation.graph_receipts ?? []).filter((item) => item.turn_id === turn.turn_id);
        const actions = (conversation.actions ?? []).filter((action) => action.turn_id === turn.turn_id);
        const named = (conversation.answer_resources ?? []).filter((item) => item.turn_id === turn.turn_id);
        const retried = conversation.turns.find((item) => item.retry_of_turn_id === turn.turn_id);
        const executionTerminal = isTerminalTurn(turn);
        return (
          <section className="scax-turn" data-turn-id={turn.turn_id} key={turn.turn_id}>
            {turn.retry_of_turn_id && <span className="scax-turn__lineage">이전 실패한 요청의 다시 시도</span>}
            {messages
              .filter((item) => item.role === "user")
              .map((item) => (
                <p className="scax-msg scax-msg--user" key={item.message_id}>
                  {item.body}
                </p>
              ))}
            {!executionTerminal && <ExecutionRail onRetry={retried ? undefined : () => onRetryTurn(turn.turn_id)} tools={tools} turn={turn} />}
            {/* 바퀴 12: 껍데기는 우리 것(.scax-msg*, 바퀴 7), 안에 넘기는 값은 main(#11)의 새 답변 문서다 */}
            {messages
              .filter((item) => item.role === "assistant" && (item.body || item.body_state === "streaming"))
              .map((item) => (
                <div className={`scax-msg scax-msg--assistant scax-msg--${item.body_state ?? "final"}`} data-body-state={item.body_state ?? "final"} key={item.message_id}>
                  <div className="scax-msg__body">
                    <AssistantMarkdown
                      body={item.body}
                      document={item.answer_document}
                      onOpenResource={onOpenResource}
                      resources={item.answer_document ? conversation.answer_resources ?? [] : named}
                    />
                    {item.body_state === "streaming" && (
                      <span aria-hidden className="scax-msg__caret">
                        ▍
                      </span>
                    )}
                    {item.body_state === "failed" && <small className="scax-msg__note">답변이 완성되지 않았습니다</small>}
                    {item.body_state === "cancelled" && <small className="scax-msg__note">취소 시점까지의 답변</small>}
                  </div>
                </div>
              ))}
            {executionTerminal && (
              <ExecutionRail
                evidence={evidence}
                onOpenResource={onOpenResource}
                onRetry={retried ? undefined : () => onRetryTurn(turn.turn_id)}
                resources={named}
                steps={walked}
                tools={tools}
                turn={turn}
              />
            )}
            {actions.map((action) => action.edit_contract?.editor === "command" ? (
              <section className="ax-action-card" key={`${personaId}:${action.action_id}`} data-action-id={action.action_id}>
                <span className="ax-card-kicker">{actionKicker(action)}</span>
                <b>{actionSubject(action)}</b>
                <ActionPreviewDetails action={action} defaultOpen />
                <CommandConfirmationForm actionId={action.action_id} principalId={personaId} contract={action.edit_contract} commands={action.commands ?? []} onCommand={(command, payload) => onDecide(action.action_id, action.version, command, payload)} />
              </section>
            ) : action.edit_contract?.editor === "task_progress_batch" ? (
              <ActionProgressBatchCard
                action={action}
                key={action.action_id}
                onCommand={(command, payload) => onDecide(action.action_id, action.version, command, payload)}
              />
            ) : action.edit_contract?.editor === "task" ? (
              <ActionTaskCard
                action={action}
                key={`${personaId}:${action.action_id}`}
                onCommand={(command, payload) => onDecide(action.action_id, action.version, command, payload)}
                onOpenTask={onOpenTask}
                principalId={personaId}
              />
            ) : action.edit_contract?.editor === "meeting" ? (
              <ActionMeetingCard
                action={action}
                key={`${personaId}:${action.action_id}`}
                onCommand={(command, payload) => onDecide(action.action_id, action.version, command, payload)}
                onOpenMeeting={onOpenMeeting}
                principalId={personaId}
              />
            ) : (
              <ActionResultCard action={action} key={action.action_id} onDecide={onDecide} />
            ))}
            {turn.turn_id === followUpTurnId && (
              <FollowUpCandidates candidates={turn.follow_up_candidates ?? []} onChoose={onFollowUpCandidate} />
            )}
          </section>
        );
      })}
      {queuedMessages.length > 0 && (
        <ol aria-label="대기열" className="scax-chat__queue">
          {queuedMessages.map((item) => (
            <li className="scax-chat__queued" key={item.message_id}>
              <p className="scax-msg scax-msg--user">{item.body}</p>
              <div className="scax-rail scax-rail--pending" role="status">
                <div className="scax-rail__head">
                  <span aria-hidden className="scax-rail__icon">✦</span>
                  <span className="scax-rail__phrase">요청 내용 확인...</span>
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}
      {localFragments.map((fragment) => fragment.state === "failed" ? (
        <p className="scax-msg scax-msg--user scax-msg--local scax-msg--failed" data-local-id={fragment.local_id} key={fragment.local_id}>
          {fragment.body}
          <span className="scax-chat__fragment-actions">
            <small>접수 실패{fragment.error ? ` · ${fragment.error}` : ""}</small>
            <Button size="sm" onClick={() => onRetryFragment(fragment)} type="button">
              다시 보내기
            </Button>
            <Button variant="text" size="sm" onClick={() => onDiscardFragment(fragment.local_id)} type="button">
              삭제
            </Button>
          </span>
        </p>
      ) : (
        <section className={`scax-chat__fragment scax-chat__fragment--${fragment.state}`} data-local-id={fragment.local_id} key={fragment.local_id}>
          <p className="scax-msg scax-msg--user">{fragment.body}</p>
          <div className="scax-rail scax-rail--pending" role="status">
            <div className="scax-rail__head">
              <span aria-hidden className="scax-rail__icon">✦</span>
              <span className="scax-rail__phrase">{fragment.state === "sending" ? "요청을 접수하는 중..." : "요청 내용 확인..."}</span>
            </div>
          </div>
        </section>
      ))}
    </>
  );
}

function FollowUpCandidates({
  candidates,
  onChoose,
}: {
  candidates: FollowUpCandidate[];
  onChoose?: (candidate: FollowUpCandidate) => Promise<boolean>;
}) {
  const selected = candidates.find((candidate) => candidate.selected_message_id !== null);
  const pendingRef = useRef<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [failedId, setFailedId] = useState<string | null>(null);
  const sourceTurnId = candidates[0]?.source_turn_id ?? null;

  useEffect(() => {
    pendingRef.current = null;
    setPendingId(null);
    setFailedId(null);
  }, [sourceTurnId]);

  if (candidates.length === 0 || !onChoose) return null;

  const choose = async (candidate: FollowUpCandidate) => {
    if (pendingRef.current || selected) return;
    pendingRef.current = candidate.candidate_id;
    setPendingId(candidate.candidate_id);
    setFailedId(null);
    let accepted = false;
    try {
      accepted = await onChoose(candidate);
    } catch {
      accepted = false;
    } finally {
      if (!accepted) {
        pendingRef.current = null;
        setPendingId(null);
        setFailedId(candidate.candidate_id);
      }
    }
  };

  return (
    <section aria-label="추천 대화" className="scax-followup">
      <div className="scax-followup__head">
        <strong>이렇게 물어볼 수 있어요</strong>
        <small>선택하면 바로 전송돼요</small>
      </div>
      <div className="scax-followup__list">
        {candidates.map((candidate, index) => {
          const isSelected = candidate.selected_message_id !== null;
          const isPending = pendingId === candidate.candidate_id;
          const isFailed = failedId === candidate.candidate_id;
          const state = isSelected ? "selected" : isPending ? "sending" : isFailed ? "failed" : "ready";
          const suffix = isSelected ? " · 선택됨" : isPending ? " · 전송 중" : isFailed ? " · 다시 시도" : "";
          return (
            <button
              aria-label={`${candidate.user_text}${suffix}`}
              aria-pressed={isSelected || isPending}
              className="scax-followup__item"
              data-state={state}
              disabled={Boolean(selected || pendingId)}
              key={candidate.candidate_id}
              onClick={() => void choose(candidate)}
              onKeyDown={(event) => {
                if (!["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
                const buttons = Array.from(event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>("button") ?? []);
                const targetIndex = event.key === "Home"
                  ? 0
                  : event.key === "End"
                    ? buttons.length - 1
                    : (index + (["ArrowDown", "ArrowRight"].includes(event.key) ? 1 : -1) + buttons.length) % buttons.length;
                event.preventDefault();
                buttons[targetIndex]?.focus();
                buttons[targetIndex]?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
              }}
              title={candidate.user_text}
              type="button"
            >
              <span>{candidate.user_text}</span>
              {suffix && <small aria-hidden>{suffix.slice(3)}</small>}
              <span aria-hidden className="scax-followup__arrow">↘</span>
            </button>
          );
        })}
      </div>
      <span aria-live="polite" className="sr-only">
        {pendingId ? "후속 질문 전송 중" : failedId ? "전송하지 못했습니다. 같은 후보를 다시 시도할 수 있습니다." : selected ? "선택한 후속 질문을 보냈습니다." : ""}
      </span>
    </section>
  );
}

/* ---------------------------------------------------------------- execution rail */

const stateLabel: Record<string, string> = {
  queued: "대기열에서 기다리는 중",
  preparing: "요청을 준비하는 중",
  tool_running: "도구 실행 중",
  composing: "답변 작성 중",
  retrying: "다시 시도 중",
  completed: "완료",
  failed: "실패",
  cancelled: "취소됨",
};

const toolStateLabel: Record<string, string> = { running: "실행 중", pending: "대기", completed: "완료", failed: "실패", denied: "거부" };

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  return now;
}

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function turnProgress(turn: ConversationTurn): string {
  if (["completed", "failed", "cancelled"].includes(String(turn.state))) return String(turn.state);
  return turn.progress_state ?? (turn.state === "pending" ? "queued" : turn.state === "running" ? "preparing" : String(turn.state));
}

function isTerminalTurn(turn: ConversationTurn): boolean {
  return ["completed", "failed", "cancelled"].includes(turnProgress(turn));
}

/**
 * In-place execution timeline. While live it sits beneath the request; once terminal its caller moves a quiet B-style
 * receipt below the answer. The receipt opens into the A-style tool timeline and the evidence that supported the answer.
 */
export function ExecutionRail({
  turn,
  tools,
  resources = [],
  evidence = [],
  steps = [],
  onOpenResource,
  onRetry,
}: {
  turn: ConversationTurn;
  tools: Tools;
  resources?: AnswerResource[];
  evidence?: MaterialEvidence[];
  steps?: GraphReceipt[];
  onOpenResource?: (resource: AnswerResource) => void;
  onRetry?: () => void;
}) {
  const progress = turnProgress(turn);
  const terminal = isTerminalTurn(turn);
  const now = useNow(!terminal);
  const reduced = prefersReducedMotion();
  const startedAt = turn.execution_started_at ? Date.parse(turn.execution_started_at) : null;
  const queuedAt = turn.queued_at ? Date.parse(turn.queued_at) : null;
  const liveElapsed = startedAt !== null ? now - startedAt : queuedAt !== null ? now - queuedAt : null;
  const elapsedText = terminal ? formatDuration(turn.run_ms) : liveElapsed !== null ? formatDuration(Math.max(0, liveElapsed)) : null;
  const waitText = formatDuration(turn.queue_wait_ms);
  const phrase =
    progress === "tool_running" && turn.current_tool_display_name
      ? `${stateLabel.tool_running} · ${turn.current_tool_display_name}`
      : progress === "retrying" && turn.attempt
        ? `${stateLabel.retrying} (${turn.attempt}번째)`
        : stateLabel[progress] ?? progress;
  const presentedTools = terminal ? terminalToolRows(tools) : tools.map((tool) => ({ tool, count: 1, latencyMs: tool.latency_ms }));
  const liveSteps = presentedTools.map(({ tool, count, latencyMs }, rowIndex) => {
    const status = toolStateLabel[tool.state] ?? tool.state;
    const mark = tool.state === "completed" ? "✓" : tool.state === "failed" || tool.state === "denied" ? "×" : tool.state === "running" ? "…" : "";
    const displayName = count > 1 ? `${tool.display_name} ${count}회` : tool.display_name;
    const startedMs = tool.started_at ? Date.parse(tool.started_at) : null;
    const running = tool.state === "running" || tool.state === "pending";
    const timing = running
      ? startedMs !== null
        ? formatDuration(Math.max(0, now - startedMs))
        : null
      : formatDuration(latencyMs);
    return (
      <li
        aria-label={`${displayName} · ${status}`}
        className={`scax-rail__step scax-rail__step--${tool.state}`}
        key={terminal ? `${tool.tool_name}-${tool.state}-${rowIndex}` : `${tool.turn_id}-${tool.sequence}`}
      >
        <span aria-hidden className="scax-rail__check">{mark}</span>
        <span>{displayName}</span>
        {terminal && <code className="scax-rail__code">{tool.tool_name}</code>}
        {timing && <span aria-hidden className="scax-rail__time">{timing}</span>}
      </li>
    );
  });

  const timings = [elapsedText ? `실행 ${elapsedText}` : null, waitText ? `대기 ${waitText}` : null].filter(Boolean).join(" · ");

  if (terminal) {
    const terminalPhrase = progress === "completed" ? "요청 내용 확인 완료" : progress === "failed" ? "요청 처리 실패" : "요청 처리 취소";
    const evidenceCount = resources.length + evidence.length;
    const hasExpandableContent = tools.length > 0 || evidenceCount > 0;
    const summaryMeta = [timings, tools.length ? `도구 호출 ${tools.length}회` : null, evidenceCount ? `근거 ${evidenceCount}건` : null].filter(Boolean).join(" · ");
    const heading = progress === "completed" ? (
      summaryMeta && <span aria-live="polite" className="scax-rail__timings">{summaryMeta}</span>
    ) : (
      <>
        <span aria-hidden className="scax-rail__icon">✦</span>
        <span aria-live="polite" className="scax-rail__phrase">{terminalPhrase}</span>
        {summaryMeta && <span aria-hidden className="scax-rail__timings">{summaryMeta}</span>}
      </>
    );
    return (
      <div className={`scax-rail scax-rail--terminal scax-rail--${progress}`} data-motion={reduced ? "reduced" : "normal"} data-progress={progress} role="group">
        {hasExpandableContent ? (
          <details className="scax-rail__details">
            <summary className="scax-rail__head">{heading}</summary>
            <div className="scax-rail__expanded">
              {tools.length > 0 && (
                <section aria-label="실행 단계" className="scax-rail__execution">
                  <b>실행 단계</b>
                  <ol aria-label="요청 처리 단계" className="scax-rail__steps">{liveSteps}</ol>
                </section>
              )}
              {evidenceCount > 0 && (
                <section aria-label="답변 근거" className="scax-rail__evidence">
                  <b>답변 근거</b>
                  <AnswerResources onOpen={onOpenResource} provenance={walkedTo(steps)} resources={resources} />
                  <EvidenceCards evidence={evidence} />
                </section>
              )}
            </div>
          </details>
        ) : <div className="scax-rail__head scax-rail__head--static">{heading}</div>}
        {turn.error && <span className="scax-rail__error">{turn.error}</span>}
        {(progress === "failed" || progress === "cancelled") && onRetry && (
          <Button variant="solid" tone="primary" size="sm" className="scax-rail__retry" onClick={onRetry} type="button">
            다시 시도
          </Button>
        )}
      </div>
    );
  }

  return (
    <div className={`scax-rail scax-rail--${progress}`} data-motion={reduced ? "reduced" : "normal"} data-progress={progress} role="group">
      <div className="scax-rail__head">
        <span aria-hidden className="scax-rail__icon">✦</span>
        <span aria-hidden className="scax-rail__phrase">요청 내용 확인...</span>
        <span aria-live="polite" className="sr-only scax-rail__live-status">{phrase}</span>
      </div>
      {tools.length > 0 && (
        <ol aria-label="요청 처리 단계" aria-live="polite" className="scax-rail__steps">
          {liveSteps}
        </ol>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- canonical action result card */

function ActionResultCard({ action, onDecide }: { action: ActionItem; onDecide: (actionId: string, expectedVersion: number, decision: string) => Promise<void> }) {
  return (
    <section className="scax-actioncard" data-action-id={action.action_id} data-state={action.state}>
      <Badge tone="accent">
        {actionKicker(action)}
        {action.state === "pending" ? " · 승인 필요" : ""}
      </Badge>
      <b>{actionSubject(action)}</b>
      <ActionPreviewDetails action={action} defaultOpen={action.state === "pending"} />
      <small className={action.state}>
        {action.state === "pending"
          ? "확인 필요 · 승인해야 반영됩니다"
          : action.state === "approved"
            ? action.result_summary ?? "승인됨 · 원장에 반영됨"
            : "거절됨"}
      </small>
      {(action.commands?.length ?? 0) > 0 && (
        <div>
          <ActionCommandButtons commands={action.commands} onCommand={(commandId) => void onDecide(action.action_id, action.version, commandId)} />
        </div>
      )}
    </section>
  );
}

/** 원문의 어디였는지를 사람이 읽는 말로. 도구가 말해 준 자리만 쓰고 없으면 아무것도 쓰지 않는다. */
function locatorText(locator: AnswerResource["source_locator"]): string {
  if (!locator) return "";
  const parts = [
    locator.page ? `${locator.page}쪽` : "",
    locator.sheet ?? "",
    locator.cell ?? "",
    locator.section ?? "",
  ].filter(Boolean);
  return parts.join(" · ");
}

const RESOURCE_LABEL: Record<string, string> = {
  task: "업무",
  meeting: "회의",
  work_request: "업무 요청",
  material: "자료",
  report: "보고",
};

/**
 * 답변이 가리키는 것들: canonical ids the turn actually read, in the order it read them.
 *
 * Each row is one resource with its own way in — nothing here was parsed out of the answer text, and a reference the
 * reader may no longer open never arrives from the server at all.
 */
function AnswerResources({
  resources,
  onOpen,
  provenance,
}: {
  resources: AnswerResource[];
  onOpen?: (resource: AnswerResource) => void;
  /** For each canonical ref, the connection the turn actually walked to reach it. Absent when it was reached directly. */
  provenance?: Map<string, string>;
}) {
  const [showAll, setShowAll] = useState(false);
  if (resources.length === 0) return null;
  // A turn can read a lot. The answer stays the main content: the rest are one press away, never hidden.
  const shown = showAll ? resources : resources.slice(0, 6);
  return (
    <section aria-label="답변이 가리키는 것" className="scax-sources">
      <b>
        읽은 정본 {resources.length}개 <small>· 실제로 조회한 것</small>
      </b>
      <ol className="scax-sources__list">
        {shown.map((resource) => {
          const ref = `${resource.resource_type}:${resource.resource_id}`;
          const why = provenance?.get(ref);
          return (
            <li data-resource={ref} key={resource.reference_id}>
              <Badge tone="neutral">{RESOURCE_LABEL[resource.resource_type] ?? resource.resource_type}</Badge>
              <span className="scax-sources__title">{resource.title}</span>
              {locatorText(resource.source_locator) && (
                <span className="t-meta">{locatorText(resource.source_locator)}</span>
              )}
              {resource.state && <span className="t-meta">{taskStateLabel[resource.state as keyof typeof taskStateLabel] ?? resource.state}</span>}
              {/* 답이 딛고 선 뒤로 바뀌었으면 링크를 열기 전에 말한다. 무엇이 달라졌는지는 상세가 말한다. */}
              {resource.changed_since && <span className="scax-sources__changed">답변 뒤 바뀜</span>}
              {onOpen && (
                <Button size="sm" onClick={() => onOpen(resource)} type="button">
                  상세 열기
                </Button>
              )}
              {why && <span className="scax-sources__why t-meta">{why}</span>}
            </li>
          );
        })}
      </ol>
      {resources.length > shown.length && (
        <button className="scax-sources__more" onClick={() => setShowAll(true)} type="button">
          {resources.length - shown.length}개 더 보기
        </button>
      )}
    </section>
  );
}

/**
 * 각 정본을 어떤 연결로 만났는지. 걸어간 edge 그대로이며, 없으면 줄도 없다.
 *
 * 제목만 있는 목록은 봤다는 주장이지 근거가 아니다. 여기서 붙는 한 줄은 tool이 돌려준 edge를 사람이 읽는 말로
 * 옮긴 것뿐이고, 추론해서 만든 관계는 하나도 들어가지 않는다. 같은 것에 여러 연결이 닿으면 먼저 걸어간 것이 그
 * 자리를 갖는다.
 */
function walkedTo(steps: GraphReceipt[]): Map<string, string> {
  const lines = new Map<string, string>();
  for (const step of steps) {
    if (step.kind !== "edge" || !step.from_ref || !step.to_ref) continue;
    const kind = step.edge_kind ?? "";
    const sentence = EDGE_SENTENCE[kind] ?? { incoming: kind, outgoing: kind };
    const to = String(step.to_ref);
    const from = String(step.from_ref);
    if (!lines.has(to)) lines.set(to, `${sentence.incoming} · ${step.from_title ?? from}`);
    if (!lines.has(from)) lines.set(from, `${sentence.outgoing} · ${step.to_title ?? to}`);
  }
  return lines;
}

function EvidenceCards({ evidence }: { evidence: MaterialEvidence[] }) {
  if (evidence.length === 0) return null;
  return (
    <section aria-label="근거 자료" className="scax-evidence">
      <b>
        근거 자료 {evidence.length}개 <small>· 첨부 내용에서 실제로 읽은 구간</small>
      </b>
      <ol className="scax-evidence__list">
        {evidence.map((item) => (
          <li className="scax-evidence__card" data-material-id={item.material_id} key={item.evidence_id}>
            <div className="scax-evidence__head">
              <span className="scax-evidence__name">{item.name}</span>
              <span className="t-meta">
                {item.page ? `${item.page}쪽 · ` : ""}
                {item.integrity_ref.replace("sha256:", "").slice(0, 8)} · {formatDate(isoDateInSeoul(item.recorded_at))}
              </span>
              <a className="scax-button scax-button--text-neutral scax-button--sm" href={item.origin} rel="noreferrer" target="_blank">
                원본 열기
              </a>
            </div>
            <blockquote>{item.excerpt}</blockquote>
          </li>
        ))}
      </ol>
    </section>
  );
}
