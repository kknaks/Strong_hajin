import { useEffect, useRef, useState } from "react";

import { formatMonthDay, isoDateInSeoul } from "../labels";
import type { ActionItem, Conversation, ConversationTurn, MaterialEvidence } from "../viewModels";
import type { LocalFragment } from "./useConversations";

const BOTTOM_SLACK_PX = 24;

type Tools = Conversation["tool_invocations"];

/**
 * Turn timeline with bottom-aware autoscroll: follows new events only while the reader is at the bottom;
 * otherwise a `새 메시지` affordance offers the jump and never steals the scroll position.
 *
 * One turn renders in a fixed order: user request → execution rail (live state + chronological tool receipts,
 * collapsed to one line once terminal) → assistant answer (streaming/final/failed/cancelled) → evidence →
 * canonical action result cards. The answer is always the last, primary content.
 */
export function MessageList({
  conversation,
  localFragments,
  onDecide,
  onRetryTurn,
  onRetryFragment,
  onDiscardFragment,
}: {
  conversation: Conversation | null;
  localFragments: LocalFragment[];
  onDecide: (actionId: string, expectedVersion: number, decision: string) => Promise<void>;
  onRetryTurn: (turnId: string) => void;
  onRetryFragment: (fragment: LocalFragment) => void;
  onDiscardFragment: (localId: string) => void;
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
    <div className="ax-messages-wrap">
      <div className="ax-messages" onScroll={onScroll} ref={scroller}>
        {conversation ? (
          <ConversationTimeline
            conversation={conversation}
            localFragments={localFragments}
            onDecide={onDecide}
            onDiscardFragment={onDiscardFragment}
            onRetryFragment={onRetryFragment}
            onRetryTurn={onRetryTurn}
          />
        ) : null}
      </div>
      {hasNew && !following && (
        <button className="ax-new-messages" onClick={jumpToBottom} type="button">
          ↓ 새 메시지
        </button>
      )}
    </div>
  );
}

function ConversationTimeline({
  conversation,
  localFragments,
  onDecide,
  onRetryTurn,
  onRetryFragment,
  onDiscardFragment,
}: {
  conversation: Conversation;
  localFragments: LocalFragment[];
  onDecide: (actionId: string, expectedVersion: number, decision: string) => Promise<void>;
  onRetryTurn: (turnId: string) => void;
  onRetryFragment: (fragment: LocalFragment) => void;
  onDiscardFragment: (localId: string) => void;
}) {
  const queuedMessages = conversation.messages.filter((item) => item.state === "queued");

  if (conversation.turns.length === 0 && queuedMessages.length === 0 && localFragments.length === 0) {
    return <p className="ax-empty">아직 발화가 없습니다. 아래에 요청을 적어 보내 주세요.</p>;
  }

  return (
    <>
      {conversation.turns.map((turn) => {
        const messages = conversation.messages.filter((item) => item.turn_id === turn.turn_id);
        const tools = conversation.tool_invocations.filter((tool) => tool.turn_id === turn.turn_id);
        const evidence = (conversation.material_evidence ?? []).filter((item) => item.turn_id === turn.turn_id);
        const actions = (conversation.actions ?? []).filter((action) => action.turn_id === turn.turn_id);
        const retried = conversation.turns.find((item) => item.retry_of_turn_id === turn.turn_id);
        return (
          <section className="ax-turn" data-turn-id={turn.turn_id} key={turn.turn_id}>
            {turn.retry_of_turn_id && <span className="ax-turn-lineage">이전 실패한 요청의 다시 시도</span>}
            {messages
              .filter((item) => item.role === "user")
              .map((item) => (
                <p className="user" key={item.message_id}>
                  {item.body}
                </p>
              ))}
            <ExecutionRail onRetry={retried ? undefined : () => onRetryTurn(turn.turn_id)} tools={tools} turn={turn} />
            {messages
              .filter((item) => item.role === "assistant" && (item.body || item.body_state === "streaming"))
              .map((item) => (
                <p className={`assistant ${item.body_state ?? "final"}`} data-body-state={item.body_state ?? "final"} key={item.message_id}>
                  {item.body}
                  {item.body_state === "streaming" && (
                    <span aria-hidden className="ax-streaming-mark">
                      ▍
                    </span>
                  )}
                  {item.body_state === "failed" && <small className="ax-body-note">답변이 완성되지 않았습니다</small>}
                  {item.body_state === "cancelled" && <small className="ax-body-note">취소 시점까지의 답변</small>}
                </p>
              ))}
            <EvidenceCards evidence={evidence} />
            {actions.map((action) => (
              <ActionResultCard action={action} key={action.action_id} onDecide={onDecide} />
            ))}
          </section>
        );
      })}
      {queuedMessages.length > 0 && (
        <ol aria-label="대기열" className="ax-queue">
          {queuedMessages.map((item, index) => (
            <li className="user queued" key={item.message_id}>
              <span className="ax-queue-index">대기 {index + 1}</span>
              {item.body} <small>대기 중</small>
            </li>
          ))}
        </ol>
      )}
      {localFragments.map((fragment) => (
        <p className={`user local ${fragment.state}`} data-local-id={fragment.local_id} key={fragment.local_id}>
          {fragment.body}
          {fragment.state === "sending" && <small>접수 중…</small>}
          {fragment.state === "accepted" && <small>접수됨 · 반영 중</small>}
          {fragment.state === "failed" && (
            <span className="ax-fragment-actions">
              <small>접수 실패{fragment.error ? ` · ${fragment.error}` : ""}</small>
              <button className="btn h30" onClick={() => onRetryFragment(fragment)} type="button">
                다시 보내기
              </button>
              <button className="btn h30 ghost" onClick={() => onDiscardFragment(fragment.local_id)} type="button">
                삭제
              </button>
            </span>
          )}
        </p>
      ))}
    </>
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

function formatSeconds(ms: number | null | undefined): string | null {
  if (ms === null || ms === undefined) return null;
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

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

/**
 * Compact, in-place execution rail: one status line (icon + step phrase + elapsed) and one line per tool receipt in
 * observed order. It updates the same element instead of appending rows, so layout stays stable; once the turn is
 * terminal it collapses into a one-line summary so the answer is the main content.
 */
export function ExecutionRail({ turn, tools, onRetry }: { turn: ConversationTurn; tools: Tools; onRetry?: () => void }) {
  const progress = turn.progress_state ?? (turn.state === "pending" ? "queued" : turn.state === "running" ? "preparing" : (turn.state as string));
  const terminal = progress === "completed" || progress === "failed" || progress === "cancelled";
  const now = useNow(!terminal);
  const reduced = prefersReducedMotion();
  const startedAt = turn.execution_started_at ? Date.parse(turn.execution_started_at) : null;
  const queuedAt = turn.queued_at ? Date.parse(turn.queued_at) : null;
  const liveElapsed = startedAt !== null ? now - startedAt : queuedAt !== null ? now - queuedAt : null;
  const elapsedText = terminal ? formatSeconds(turn.run_ms) : liveElapsed !== null ? formatSeconds(Math.max(0, liveElapsed)) : null;
  const waitText = formatSeconds(turn.queue_wait_ms);
  const phrase =
    progress === "tool_running" && turn.current_tool_display_name
      ? `${stateLabel.tool_running} · ${turn.current_tool_display_name}`
      : progress === "retrying" && turn.attempt
        ? `${stateLabel.retrying} (${turn.attempt}번째)`
        : stateLabel[progress] ?? progress;
  const icon = progress === "completed" ? "✓" : progress === "failed" ? "✕" : progress === "cancelled" ? "⊘" : "◌";

  // Timings tick every second and are aria-hidden; only semantic phase/tool changes are announced.
  const receipts = tools.map((tool) => {
    const startedMs = tool.started_at ? Date.parse(tool.started_at) : null;
    const running = tool.state === "running" || tool.state === "pending";
    const timing = running
      ? startedMs !== null
        ? formatSeconds(Math.max(0, now - startedMs))
        : null
      : formatSeconds(tool.latency_ms);
    return (
      <li className={`ax-rail-tool ${tool.state}`} key={`${tool.turn_id}-${tool.sequence}`} title={tool.input_summary}>
        <span className="ax-rail-tool-name">{tool.display_name}</span>
        <span className="ax-rail-tool-state">{toolStateLabel[tool.state] ?? tool.state}</span>
        <span className="ax-rail-tool-result">{running ? tool.input_summary : tool.result_summary ?? tool.error_summary ?? ""}</span>
        {timing && (
          <span aria-hidden className="ax-rail-tool-time">
            {timing}
          </span>
        )}
      </li>
    );
  });

  const outcome = [`${icon} ${stateLabel[progress] ?? progress}`, tools.length ? `도구 ${tools.length}개` : null].filter(Boolean).join(" · ");
  const timings = [elapsedText ? `실행 ${elapsedText}` : null, waitText ? `대기 ${waitText}` : null].filter(Boolean).join(" · ");

  if (terminal) {
    return (
      <div className={`ax-rail terminal ${progress}`} data-progress={progress} role="group">
        <details className="ax-rail-details">
          <summary>
            <span aria-live="polite" className="ax-rail-summary">
              {outcome}
            </span>
            {timings && (
              <span aria-hidden className="ax-rail-timings">
                · {timings}
              </span>
            )}
            {turn.error && <span className="ax-rail-error">{turn.error}</span>}
          </summary>
          {tools.length > 0 ? <ol className="ax-rail-tools">{receipts}</ol> : <p className="ax-rail-none">도구를 사용하지 않았습니다.</p>}
        </details>
        {(progress === "failed" || progress === "cancelled") && onRetry && (
          <button className="btn h30 primary" onClick={onRetry} type="button">
            다시 시도
          </button>
        )}
      </div>
    );
  }

  return (
    <div className={`ax-rail ${progress}`} data-motion={reduced ? "reduced" : "normal"} data-progress={progress} role="group">
      <div className="ax-rail-head">
        <span aria-hidden className={`ax-rail-icon ${reduced ? "" : "spin"}`}>
          {icon}
        </span>
        <span aria-live="polite" className="ax-rail-phrase">
          {phrase}
        </span>
        <span aria-hidden className="ax-rail-elapsed">
          {elapsedText ?? ""}
        </span>
      </div>
      {tools.length > 0 && (
        <ol aria-live="polite" className="ax-rail-tools">
          {receipts}
        </ol>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- canonical action result card */

function ActionResultCard({ action, onDecide }: { action: ActionItem; onDecide: (actionId: string, expectedVersion: number, decision: string) => Promise<void> }) {
  const commands = action.commands ?? [];
  return (
    <section className="ax-action-card" data-action-id={action.action_id} data-state={action.state}>
      <span className="ax-card-kicker">{action.state === "pending" ? "AX 제안 · 승인 필요" : "AX 제안 결과"}</span>
      <b>{action.title}</b>
      <p>{action.payload_summary}</p>
      <small className={action.state}>
        {action.state === "pending" ? "확인 필요 · 승인해야 반영됩니다" : action.state === "approved" ? "승인됨 · 원장에 반영됨" : "거절됨"}
      </small>
      {commands.length > 0 && (
        <div>
          {commands.map((command) => (
            <button
              className={`btn h30 ${command.tone === "primary" ? "primary" : command.tone === "danger" ? "danger" : ""}`}
              key={command.id}
              onClick={() => void onDecide(action.action_id, action.version, command.id)}
              type="button"
            >
              {command.label}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

/** Material excerpts the turn actually retrieved through the authorized search; each card opens the same origin as the Task drawer. */
function EvidenceCards({ evidence }: { evidence: MaterialEvidence[] }) {
  if (evidence.length === 0) return null;
  return (
    <section aria-label="근거 자료" className="ax-evidence">
      <b>
        근거 자료 {evidence.length}개 <small>· 첨부 내용에서 실제로 읽은 구간</small>
      </b>
      <ol className="ax-evidence-list">
        {evidence.map((item) => (
          <li className="ax-evidence-card" data-material-id={item.material_id} key={item.evidence_id}>
            <div className="ax-evidence-head">
              <span className="ax-evidence-name">{item.name}</span>
              <span className="t-meta">
                {item.page ? `${item.page}쪽 · ` : ""}
                {item.integrity_ref.replace("sha256:", "").slice(0, 8)} · {formatMonthDay(isoDateInSeoul(item.recorded_at))}
              </span>
              <a className="btn h30 ghost" href={item.origin} rel="noreferrer" target="_blank">
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
