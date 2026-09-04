import { useEffect, useRef, useState } from "react";

import { executionStateText, formatMonthDay, isoDateInSeoul } from "../labels";
import type { Conversation, MaterialEvidence } from "../viewModels";
import type { LocalFragment } from "./useConversations";

const BOTTOM_SLACK_PX = 24;

/**
 * Turn timeline with bottom-aware autoscroll: follows new events only while the reader is at the bottom;
 * otherwise a `새 메시지` affordance offers the jump and never steals the scroll position.
 */
export function MessageList({
  conversation,
  localFragments,
  canDecideActions,
  onDecide,
  onRetryFragment,
  onDiscardFragment,
}: {
  conversation: Conversation | null;
  localFragments: LocalFragment[];
  canDecideActions: boolean;
  onDecide: (actionId: string, expectedVersion: number, decision: "approve" | "reject") => Promise<void>;
  onRetryFragment: (fragment: LocalFragment) => void;
  onDiscardFragment: (localId: string) => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const [following, setFollowing] = useState(true);
  const [hasNew, setHasNew] = useState(false);
  const contentKey = conversation
    ? `${conversation.conversation_id}:${conversation.version}:${conversation.messages.length}:${conversation.turns.map((turn) => turn.state).join(",")}:${localFragments.length}`
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
            canDecideActions={canDecideActions}
            conversation={conversation}
            localFragments={localFragments}
            onDecide={onDecide}
            onDiscardFragment={onDiscardFragment}
            onRetryFragment={onRetryFragment}
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
  canDecideActions,
  conversation,
  localFragments,
  onDecide,
  onRetryFragment,
  onDiscardFragment,
}: {
  canDecideActions: boolean;
  conversation: Conversation;
  localFragments: LocalFragment[];
  onDecide: (actionId: string, expectedVersion: number, decision: "approve" | "reject") => Promise<void>;
  onRetryFragment: (fragment: LocalFragment) => void;
  onDiscardFragment: (localId: string) => void;
}) {
  const queuedMessages = conversation.messages.filter((item) => item.state === "queued");

  if (conversation.turns.length === 0 && queuedMessages.length === 0 && localFragments.length === 0) {
    return <p className="ax-empty">아직 발화가 없습니다. 아래에 요청을 적어 보내 주세요.</p>;
  }

  return (
    <>
      {conversation.turns.map((turn) => (
        <section className="ax-turn" key={turn.turn_id}>
          {conversation.messages
            .filter((item) => item.turn_id === turn.turn_id)
            .map((item) => (
              <p className={item.role} key={item.message_id}>
                {item.body}
              </p>
            ))}
          <small className={`ax-turn-state ${turn.state}`}>{executionStateText(turn.state)}</small>
          {turn.error && <p className="ax-turn-error">{turn.error}</p>}
          <ToolReceipt tools={conversation.tool_invocations.filter((tool) => tool.turn_id === turn.turn_id)} />
          <EvidenceCards evidence={(conversation.material_evidence ?? []).filter((item) => item.turn_id === turn.turn_id)} />
          {(conversation.actions ?? [])
            .filter((action) => action.turn_id === turn.turn_id)
            .map((action) => (
              <section className="ax-action-card" data-action-id={action.action_id} key={action.action_id}>
                <span className="ax-card-kicker">승인 필요한 변경</span>
                <b>{action.title}</b>
                <p>{action.payload_summary}</p>
                <small className={action.state}>
                  {action.state === "pending" ? "확인 필요 · 승인해야 반영됩니다" : action.state === "approved" ? "승인됨 · 원장에 반영됨" : "거절됨"}
                </small>
                {action.state === "pending" && canDecideActions && (
                  <div>
                    <button className="btn h30 primary" onClick={() => void onDecide(action.action_id, action.version, "approve")} type="button">
                      승인
                    </button>
                    <button className="btn h30" onClick={() => void onDecide(action.action_id, action.version, "reject")} type="button">
                      거절
                    </button>
                  </div>
                )}
              </section>
            ))}
        </section>
      ))}
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
          {fragment.state === "sending" ? (
            <small>접수 중…</small>
          ) : (
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

/** Compact receipt by default; expanding shows each tool's user-facing name, state, redacted input/result, and latency. */
function ToolReceipt({ tools }: { tools: Conversation["tool_invocations"] }) {
  if (tools.length === 0) return null;
  const failed = tools.filter((tool) => tool.state === "failed" || tool.state === "denied").length;
  const running = tools.filter((tool) => tool.state === "running" || tool.state === "pending").length;
  const overall = running > 0 ? "실행 중" : failed > 0 ? `실패 ${failed}건` : "완료";
  const names = [...new Set(tools.map((tool) => tool.display_name))].join(", ");
  return (
    <details className="ax-tools" open={running > 0}>
      <summary>
        도구 {tools.length}개 실행 · {overall}
        <small>{names}</small>
      </summary>
      <ol className="ax-tool-list">
        {tools.map((tool) => (
          <li
            className={`ax-tool ${tool.state}`}
            key={`${tool.turn_id}-${tool.sequence}`}
            title={`${tool.input_summary}${tool.latency_ms === null ? "" : ` · ${tool.latency_ms}ms`}`}
          >
            <b>
              {tool.display_name} · {executionStateText(tool.state)}
            </b>
            <p>{tool.result_summary ?? tool.error_summary ?? "실행 중"}</p>
            <span className="ax-tool-meta">
              {tool.input_summary}
              {tool.latency_ms === null ? "" : ` · ${tool.latency_ms}ms`}
            </span>
          </li>
        ))}
      </ol>
    </details>
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
