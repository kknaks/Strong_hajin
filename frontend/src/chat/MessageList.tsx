import { useEffect, useRef, useState } from "react";

import { ActionCommandButtons, ActionPreviewDetails, actionKicker, actionSubject } from "../ActionPreview";
import { formatDate, formatDuration, isoDateInSeoul, taskStateLabel } from "../labels";
import { AssistantMarkdown } from "./AssistantMarkdown";
import { EDGE_LABEL, EDGE_SENTENCE, GraphCanvas } from "../GraphCanvas";
import type { ActionItem, AnswerResource, Conversation, ConversationTurn, GraphEdge, GraphNode, GraphReceipt, MaterialEvidence } from "../viewModels";
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
  onOpenGraph,
  onOpenResource,
}: {
  conversation: Conversation | null;
  localFragments: LocalFragment[];
  onDecide: (actionId: string, expectedVersion: number, decision: string) => Promise<void>;
  onRetryTurn: (turnId: string) => void;
  onRetryFragment: (fragment: LocalFragment) => void;
  onDiscardFragment: (localId: string) => void;
  /** Continue this turn's picture on the full graph surface, centred on one node. */
  onOpenGraph?: (nodeRef: string) => void;
  /** Open one thing the answer points at, in the surface that owns it. */
  onOpenResource?: (resource: AnswerResource) => void;
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
            onOpenGraph={onOpenGraph}
            onOpenResource={onOpenResource}
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
  onOpenGraph,
  onOpenResource,
}: {
  conversation: Conversation;
  localFragments: LocalFragment[];
  onDecide: (actionId: string, expectedVersion: number, decision: string) => Promise<void>;
  onRetryTurn: (turnId: string) => void;
  onRetryFragment: (fragment: LocalFragment) => void;
  onDiscardFragment: (localId: string) => void;
  onOpenGraph?: (nodeRef: string) => void;
  onOpenResource?: (resource: AnswerResource) => void;
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
        const walked = (conversation.graph_receipts ?? []).filter((item) => item.turn_id === turn.turn_id);
        const actions = (conversation.actions ?? []).filter((action) => action.turn_id === turn.turn_id);
        const named = (conversation.answer_resources ?? []).filter((item) => item.turn_id === turn.turn_id);
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
            <ExecutionRail onRetry={retried ? undefined : () => onRetryTurn(turn.turn_id)} steps={walked} tools={tools} turn={turn} />
            {messages
              .filter((item) => item.role === "assistant" && (item.body || item.body_state === "streaming"))
              .map((item) => (
                <div className={`assistant ${item.body_state ?? "final"}`} data-body-state={item.body_state ?? "final"} key={item.message_id}>
                  <AssistantMarkdown body={item.body} />
                  {item.body_state === "streaming" && (
                    <span aria-hidden className="ax-streaming-mark">
                      ▍
                    </span>
                  )}
                  {item.body_state === "failed" && <small className="ax-body-note">답변이 완성되지 않았습니다</small>}
                  {item.body_state === "cancelled" && <small className="ax-body-note">취소 시점까지의 답변</small>}
                </div>
              ))}
            <AnswerEvidence
              evidence={evidence}
              onOpen={onOpenResource}
              onOpenGraph={onOpenGraph}
              resources={named}
              steps={walked}
            />
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
export function ExecutionRail({
  turn,
  tools,
  steps = [],
  onRetry,
}: {
  turn: ConversationTurn;
  tools: Tools;
  /** Where this turn walked, shown as it arrives and folded away with the tools once the turn is done. */
  steps?: GraphReceipt[];
  onRetry?: () => void;
}) {
  const progress = turn.progress_state ?? (turn.state === "pending" ? "queued" : turn.state === "running" ? "preparing" : (turn.state as string));
  const terminal = progress === "completed" || progress === "failed" || progress === "cancelled";
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
  const icon = progress === "completed" ? "✓" : progress === "failed" ? "✕" : progress === "cancelled" ? "⊘" : "◌";

  // Timings tick every second and are aria-hidden; only semantic phase/tool changes are announced.
  const receipts = tools.map((tool) => {
    const startedMs = tool.started_at ? Date.parse(tool.started_at) : null;
    const running = tool.state === "running" || tool.state === "pending";
    const timing = running
      ? startedMs !== null
        ? formatDuration(Math.max(0, now - startedMs))
        : null
      : formatDuration(tool.latency_ms);
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

  const path = <SearchPathSteps steps={steps} />;
  const outcome = [
    `${icon} ${stateLabel[progress] ?? progress}`,
    tools.length ? `도구 ${tools.length}개` : null,
    steps.length ? `연결 ${steps.length}단계` : null,
  ]
    .filter(Boolean)
    .join(" · ");
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
          {path}
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
      {path}
    </div>
  );
}

/* ---------------------------------------------------------------- canonical action result card */

function ActionResultCard({ action, onDecide }: { action: ActionItem; onDecide: (actionId: string, expectedVersion: number, decision: string) => Promise<void> }) {
  return (
    <section className="ax-action-card" data-action-id={action.action_id} data-state={action.state}>
      <span className="ax-card-kicker">
        {actionKicker(action)}
        {action.state === "pending" ? " · 승인 필요" : ""}
      </span>
      <b>{actionSubject(action)}</b>
      <ActionPreviewDetails action={action} defaultOpen={action.state === "pending"} />
      <small className={action.state}>
        {action.state === "pending" ? "확인 필요 · 승인해야 반영됩니다" : action.state === "approved" ? "승인됨 · 원장에 반영됨" : "거절됨"}
      </small>
      {(action.commands?.length ?? 0) > 0 && (
        <div>
          <ActionCommandButtons commands={action.commands} onCommand={(commandId) => void onDecide(action.action_id, action.version, commandId)} />
        </div>
      )}
    </section>
  );
}

/**
 * 찾아본 연결: the steps this turn actually took, in the order the tools returned them.
 *
 * It appears inside the execution rail — live while the turn runs, folded into the one-line receipt once it is done —
 * because it is how the answer was found, not the answer. Nothing here is inferred: a connection is listed only
 * because a graph tool returned it for this persona, and it is restored from the server on re-entry.
 */
function SearchPathSteps({ steps }: { steps: GraphReceipt[] }) {
  if (steps.length === 0) return null;
  const found = steps.filter((step) => step.kind === "node");
  const edges = steps.filter((step) => step.kind === "edge");
  return (
    <section aria-label="찾아본 연결" className="ax-search-path">
      <b>
        찾아본 연결 {edges.length + found.length}단계 <small>· 실제로 조회한 것만</small>
      </b>
      <ol className="ax-path-list">
        {found.length > 0 && (
          <li key="found">
            <span className="t-meta">찾음</span> {found.map((step) => step.node_title).filter(Boolean).join(", ")}
          </li>
        )}
        {edges.map((step) => (
          <li key={step.receipt_id}>
            <span className="t-meta">{EDGE_LABEL[step.edge_kind ?? ""] ?? step.edge_kind}</span>{" "}
            {step.from_title ?? step.from_ref} → {step.to_title ?? step.to_ref}
          </li>
        ))}
      </ol>
    </section>
  );
}

/**
 * 이 답의 그림: the same receipt, drawn once and left alone.
 *
 * It stays with the answer rather than folding away with the execution, because it is what the answer is about. It is
 * a picture of one turn — nothing to pan, zoom or filter — and `전체 그래프로 보기` hands the centre to the full
 * surface, which applies this person's access again from the start.
 */
function TurnGraph({ steps, onOpenGraph }: { steps: GraphReceipt[]; onOpenGraph?: (nodeRef: string) => void }) {
  if (steps.length === 0) return null;
  const seen = new Map<string, GraphNode>();
  const add = (ref: string | null | undefined, title: string | null | undefined) => {
    if (!ref || seen.has(ref)) return;
    const [kind, ...rest] = ref.split(":");
    seen.set(ref, { kind: kind as GraphNode["kind"], id: rest.join(":"), title: title ?? ref, state: null });
  };
  for (const step of steps) {
    add(step.node_ref, step.node_title);
    add(step.from_ref, step.from_title);
    add(step.to_ref, step.to_title);
  }
  const edges: GraphEdge[] = steps
    .filter((step) => step.kind === "edge" && step.from_ref && step.to_ref)
    .map((step) => ({
      kind: step.edge_kind ?? "",
      from: String(step.from_ref),
      to: String(step.to_ref),
      label: EDGE_LABEL[step.edge_kind ?? ""] ?? step.edge_kind ?? "",
    }));
  if (seen.size === 0) return null;
  const center = steps.find((step) => step.kind === "node")?.node_ref ?? edges[0]?.from;
  return (
    <section aria-label="이 답의 관계" className="ax-turn-graph">
      <GraphCanvas edges={edges} height={180} interactive={false} nodes={[...seen.values()]} />
      {onOpenGraph && center && (
        <button className="btn h30" onClick={() => onOpenGraph(String(center))} type="button">
          전체 그래프로 보기
        </button>
      )}
    </section>
  );
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
    <section aria-label="답변이 가리키는 것" className="ax-answer-resources">
      <b>
        읽은 정본 {resources.length}개 <small>· 실제로 조회한 것</small>
      </b>
      <ol className="ax-resource-list">
        {shown.map((resource) => {
          const ref = `${resource.resource_type}:${resource.resource_id}`;
          const why = provenance?.get(ref);
          return (
            <li data-resource={ref} key={resource.reference_id}>
              <span className="ax-resource-kind">{RESOURCE_LABEL[resource.resource_type] ?? resource.resource_type}</span>
              <span className="ax-resource-title">{resource.title}</span>
              {resource.state && <span className="t-meta">{taskStateLabel[resource.state as keyof typeof taskStateLabel] ?? resource.state}</span>}
              {onOpen && (
                <button className="btn h30" onClick={() => onOpen(resource)} type="button">
                  상세 열기
                </button>
              )}
              {why && <span className="ax-resource-why t-meta">{why}</span>}
            </li>
          );
        })}
      </ol>
      {resources.length > shown.length && (
        <button className="btn link" onClick={() => setShowAll(true)} type="button">
          {resources.length - shown.length}개 더 보기
        </button>
      )}
    </section>
  );
}

/**
 * 이 답이 무엇 위에 서 있는지, 한 줄로 먼저.
 *
 * 근거는 답 아래 따로따로 쌓이는 세 덩어리가 아니라 답에 붙은 한 줄이다. 접힌 상태에서 그 줄은 얼마나 많은 것을
 * 딛고 있는지만 말하고, 펼치면 읽은 정본·인용한 구간·걸어간 경로가 같은 자리에서 이어진다. 답이 언제나 먼저
 * 읽히도록 기본은 접힘이다.
 *
 * 세는 것은 화면에 실제로 도달한 것뿐이다. 지금 이 사람이 볼 수 없는 것은 서버에서 아예 오지 않으므로 여기에서도
 * 세지 않는다. 볼 수 없는 것의 개수는 그 자체로 존재를 알리는 말이 된다.
 */
function AnswerEvidence({
  resources,
  evidence,
  steps,
  onOpen,
  onOpenGraph,
}: {
  resources: AnswerResource[];
  evidence: MaterialEvidence[];
  steps: GraphReceipt[];
  onOpen?: (resource: AnswerResource) => void;
  onOpenGraph?: (nodeRef: string) => void;
}) {
  if (resources.length === 0 && evidence.length === 0 && steps.length === 0) return null;
  const counts = [
    resources.length ? `정본 ${resources.length}건` : null,
    evidence.length ? `인용 ${evidence.length}곳` : null,
    steps.length ? `연결 ${steps.length}단계` : null,
  ].filter(Boolean);
  return (
    <details className="ax-answer-evidence">
      <summary>
        <span className="ax-evidence-kicker">근거</span>
        <span className="ax-evidence-counts">{counts.join(" · ")}</span>
      </summary>
      <div className="ax-evidence-body">
        <AnswerResources onOpen={onOpen} provenance={walkedTo(steps)} resources={resources} />
        <EvidenceCards evidence={evidence} />
        <TurnGraph onOpenGraph={onOpenGraph} steps={steps} />
      </div>
    </details>
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
                {item.integrity_ref.replace("sha256:", "").slice(0, 8)} · {formatDate(isoDateInSeoul(item.recorded_at))}
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
