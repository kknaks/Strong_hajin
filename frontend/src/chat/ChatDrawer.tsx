import { useEffect, useMemo, useRef, useState } from "react";

import { personName } from "../labels";
import type { Conversation, ConversationContextReference } from "../viewModels";
import { MessageList } from "./MessageList";
import type { ListStatus, LocalFragment } from "./useConversations";

export type LabeledContextReference = ConversationContextReference & { label?: string; pinned?: boolean };

export function contextKey(reference: ConversationContextReference): string {
  return `${reference.resource_type}:${reference.resource_id}:${reference.resource_version}`;
}

const summaryStateLabel: Record<string, string> = {
  pending: "접수됨",
  running: "실행 중",
  completed: "완료",
  failed: "실패",
  cancelled: "취소됨",
};

export function conversationExcerpt(conversation: Conversation): string {
  const firstUserMessage = conversation.messages.find((item) => item.role === "user");
  if (!firstUserMessage) return conversation.title;
  return firstUserMessage.body.replace(/\s+/g, " ").trim();
}

export function conversationSummary(conversation: Conversation): string {
  const userMessages = conversation.messages.filter((item) => item.role === "user").length;
  if (userMessages === 0) return "발화 없음";
  const queued = conversation.messages.filter((item) => item.state === "queued").length;
  if (queued > 0) return `발화 ${userMessages} · 대기열 ${queued}`;
  const latestTurn = conversation.turns.at(-1);
  const state = latestTurn ? summaryStateLabel[latestTurn.state] ?? latestTurn.state : "접수됨";
  return `발화 ${userMessages} · ${state}`;
}

/**
 * The AX drawer shell: header, vertical session switcher with search, timeline, and composer.
 * It renders server projections only; every state transition and approval command goes through the ledger API.
 */
export function ChatDrawer({
  personaName,
  surfaceLabel,
  conversations,
  activeConversation,
  listStatus,
  isProcessing,
  localFragments,
  message,
  selectedContext,
  onMessageChange,
  onClearContext,
  onClose,
  onStart,
  onSelect,
  onSend,
  onCancel,
  onDecide,
  onRetryTurn,
  onRetryFragment,
  onDiscardFragment,
  onRetryList,
  onOpenGraph,
}: {
  personaName: string;
  surfaceLabel: string;
  conversations: Conversation[];
  activeConversation: Conversation | null;
  listStatus: ListStatus;
  isProcessing: boolean;
  localFragments: LocalFragment[];
  /** The unsent draft of the active conversation (kept per conversation by the owner). */
  message: string;
  selectedContext: LabeledContextReference | undefined;
  onMessageChange: (value: string) => void;
  onClearContext: () => void;
  onClose: () => void;
  onStart: () => void;
  onSelect: (conversation: Conversation) => void;
  onSend: () => void;
  onCancel: () => void;
  onDecide: (actionId: string, expectedVersion: number, decision: string) => Promise<void>;
  onRetryTurn: (turnId: string) => void;
  onRetryFragment: (fragment: LocalFragment) => void;
  onDiscardFragment: (localId: string) => void;
  onRetryList: () => void;
  /** Continue a turn's fixed picture on the full graph surface. */
  onOpenGraph?: (nodeRef: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [switcherOpen, setSwitcherOpen] = useState(true);
  const composer = useRef<HTMLTextAreaElement>(null);
  // Auto-grow: the browser resize handle is off; height follows content up to the CSS max-height, then scrolls inside.
  useEffect(() => {
    const element = composer.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${element.scrollHeight}px`;
  }, [message, activeConversation?.conversation_id]);
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return conversations;
    return conversations.filter((item) => item.title.toLowerCase().includes(needle) || conversationExcerpt(item).toLowerCase().includes(needle));
  }, [conversations, query]);

  return (
    <aside aria-label="AX 대화" className="ax-drawer">
      <header>
        <div>
          <b>AX 에이전트</b>
          <small>
            {surfaceLabel} 화면 · {personName(personaName)} · 허용된 업무 기능만 조회하고 변경은 승인 뒤 반영됩니다
          </small>
        </div>
        <button className="btn h30 ghost" onClick={onClose} type="button">
          닫기
        </button>
      </header>

      <section aria-label="대화 목록" className="ax-sessions">
        <div className="ax-sessions-head">
          <button
            aria-expanded={switcherOpen}
            className="btn link ax-sessions-toggle"
            onClick={() => setSwitcherOpen((open) => !open)}
            type="button"
          >
            {switcherOpen ? "▾" : "▸"} 대화 {conversations.length}개
          </button>
          <button aria-label="새 AX 대화" className="btn h30 ai" onClick={onStart} type="button">
            ✦ 새 대화
          </button>
        </div>
        {switcherOpen && (
          <>
            {conversations.length > 2 && (
              <input
                aria-label="대화 검색"
                className="ax-sessions-search"
                onChange={(event) => setQuery(event.target.value)}
                placeholder="대화 검색…"
                type="search"
                value={query}
              />
            )}
            {listStatus === "loading" && conversations.length === 0 ? (
              <p className="ax-sessions-state">대화를 불러오는 중…</p>
            ) : listStatus === "error" && conversations.length === 0 ? (
              <p className="ax-sessions-state error">
                대화 목록을 불러오지 못했습니다.
                <button className="btn h30" onClick={onRetryList} type="button">
                  다시 시도
                </button>
              </p>
            ) : conversations.length === 0 ? (
              <p className="ax-sessions-state">아직 대화가 없습니다. 새 대화로 시작하세요.</p>
            ) : filtered.length === 0 ? (
              <p className="ax-sessions-state">검색 결과가 없습니다.</p>
            ) : (
              <ul className="ax-conversation-list">
                {filtered.map((conversation) => (
                  <li key={conversation.conversation_id}>
                    <button
                      aria-label={conversation.title}
                      aria-pressed={activeConversation?.conversation_id === conversation.conversation_id}
                      data-conversation-id={conversation.conversation_id}
                      onClick={() => onSelect(conversation)}
                      title={conversationExcerpt(conversation)}
                      type="button"
                    >
                      <b>{conversation.title}</b>
                      <small>{conversationSummary(conversation)}</small>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </section>

      {activeConversation ? (
        <MessageList
          conversation={activeConversation}
          localFragments={localFragments}
          onDecide={onDecide}
          onDiscardFragment={onDiscardFragment}
          onOpenGraph={onOpenGraph}
          onRetryFragment={onRetryFragment}
          onRetryTurn={onRetryTurn}
        />
      ) : (
        <div className="ax-messages-wrap">
          <div className="ax-messages">
            <p className="ax-empty">
              안녕하세요 {personName(personaName)}님!
              <br />
              새 대화를 만들고 업무에 대해 무엇이든 물어보세요.
            </p>
          </div>
        </div>
      )}

      <div className="ax-composer">
        {selectedContext && (
          <div aria-label="참고 자료" className="ax-context-row">
            <div className="ax-context-chip">
              <span aria-hidden>📎</span>
              <span>
                {selectedContext.resource_type === "task" ? "업무" : "업무 요청"} · {selectedContext.label ?? selectedContext.resource_id.slice(0, 8)}
              </span>
              <button aria-label="참고 자료 떼기" onClick={onClearContext} type="button">
                ×
              </button>
            </div>
            <small>현재 화면 자료 · 서버가 버전을 확인해 요약만 전달합니다</small>
          </div>
        )}
        <label className="sr-only" htmlFor="ax-message">
          AX 메시지
        </label>
        <textarea
          id="ax-message"
          ref={composer}
          rows={2}
          onChange={(event) => onMessageChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
              event.preventDefault();
              onSend();
            }
          }}
          placeholder={activeConversation ? (isProcessing ? "실행 중에도 이어서 보낼 수 있습니다 · 순서대로 처리됩니다" : "업무에 대해 질문하세요") : "먼저 새 대화를 만들어 주세요"}
          value={message}
        />
        <div className="ax-composer-actions">
          {isProcessing && activeConversation ? (
            <button className="btn h30 ghost" onClick={onCancel} type="button">
              실행 취소
            </button>
          ) : (
            <span className="t-meta">⌘/Ctrl+Enter로 보내기</span>
          )}
          <button className="btn primary" disabled={!activeConversation || !message.trim()} onClick={onSend} type="button">
            {isProcessing ? "대기열에 보내기" : "보내기"}
          </button>
        </div>
      </div>
    </aside>
  );
}
