import { useEffect, useMemo, useRef, useState } from "react";

import { AssistantCharacter } from "../assistant/AssistantCharacter";
import { Button } from "../../ds/Button";
import { deriveAssistantPresentationState, type AssistantPresentationState } from "../assistant/assistantPresentation";
import { personName } from "../../lib/labels";
import type { AnswerResource, Conversation, ConversationContextReference, FollowUpCandidate } from "../../lib/viewModels";
import { MessageList } from "./MessageList";
import type { ListStatus, LocalFragment } from "./useConversations";
import { Skeleton } from "../../ds/Skeleton";
import { Icon } from "../../ds/icons/Icon";

export type LabeledContextReference = ConversationContextReference & { label?: string; pinned?: boolean };
type UtilityView = "history";

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

const starterPrompts = [
  "이번 주 내 업무를 정리해줘",
  "오늘 내 회의가 있는지 보여줘",
  "진행 중인 프로젝트를 알려줘",
] as const;
const DEFAULT_CONVERSATION_TITLE = "새 대화";
const DISPLAY_TITLE_LIMIT = 28;

function HeaderIcon({ kind }: { kind: "history" | "new" | "close" }) {
  const path = {
    history: <><circle cx="12" cy="12" r="8" /><path d="M12 7v5l3 2" /></>,
    new: <><path d="M5 6.5A2.5 2.5 0 0 1 7.5 4h9A2.5 2.5 0 0 1 19 6.5v7a2.5 2.5 0 0 1-2.5 2.5H11l-4 3v-3.2A2.5 2.5 0 0 1 5 13.5Z" /><path d="M12 7v6M9 10h6" /></>,
    close: <path d="m7 7 10 10M17 7 7 17" />,
  }[kind];
  return <svg aria-hidden className="scax-chat__tool-icon" fill="none" viewBox="0 0 24 24">{path}</svg>;
}

export function conversationExcerpt(conversation: Conversation): string {
  // The server-computed excerpt covers the whole conversation even when `messages` is windowed to the recent
  // page; older/mocked payloads that omit the field fall back to scanning the (then-complete) message list.
  if (conversation.first_user_message_excerpt !== undefined) {
    return conversation.first_user_message_excerpt?.replace(/\s+/g, " ").trim() || conversation.title;
  }
  const firstUserMessage = conversation.messages.find((item) => item.role === "user");
  if (!firstUserMessage) return conversation.title;
  return firstUserMessage.body.replace(/\s+/g, " ").trim();
}

export function conversationDisplayTitle(conversation: Conversation): string {
  const supplied = conversation.title.trim();
  if (supplied && supplied !== DEFAULT_CONVERSATION_TITLE) return supplied;
  const excerpt = conversationExcerpt(conversation).replace(/\s+/g, " ").trim();
  if (!excerpt || excerpt === DEFAULT_CONVERSATION_TITLE) return DEFAULT_CONVERSATION_TITLE;
  const characters = Array.from(excerpt);
  return characters.length > DISPLAY_TITLE_LIMIT ? `${characters.slice(0, DISPLAY_TITLE_LIMIT).join("")}…` : excerpt;
}

export function conversationHasAnswer(conversation: Conversation): boolean {
  if (conversation.has_final_answer !== undefined) return conversation.has_final_answer;
  return conversation.messages.some((message) => (
    message.role === "assistant" && message.body_state === "final" && message.body.trim() !== ""
  ));
}

export function conversationSummary(conversation: Conversation): string {
  const userMessages = conversation.user_message_count ?? conversation.messages.filter((item) => item.role === "user").length;
  if (userMessages === 0) return "발화 없음";
  // A list row never carries the full `messages`/`turns` arrays (see `queued_message_count`/`latest_turn_state`
  // on `ConversationView`); older/mocked payloads that omit them fall back to scanning the (then-complete) arrays.
  const queued = conversation.queued_message_count
    ?? conversation.messages.filter((item) => item.state === "queued").length;
  if (queued > 0) return `발화 ${userMessages} · 대기열 ${queued}`;
  const latestTurnState = conversation.latest_turn_state !== undefined
    ? conversation.latest_turn_state
    : conversation.turns.at(-1)?.state ?? null;
  const state = latestTurnState ? summaryStateLabel[latestTurnState] ?? latestTurnState : "접수됨";
  return `발화 ${userMessages} · ${state}`;
}

/**
 * The AX drawer shell: header, vertical session switcher with search, timeline, and composer.
 * It renders server projections only; every state transition and approval command goes through the ledger API.
 */
export function ChatDrawer({
  assistantState,
  characterKey,
  personaId,
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
  onFollowUpCandidate,
  onRetryList,
  onOpenResource,
  onOpenTask,
  onOpenMeeting,
  onLoadOlderMessages,
  loadingOlderMessages,
}: {
  assistantState?: AssistantPresentationState;
  characterKey?: string | null;
  personaId: string;
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
  onSend: (bodyOverride?: string) => void;
  onCancel: () => void;
  onDecide: (
    actionId: string,
    expectedVersion: number,
    decision: string,
    payload?: { base_submission_version?: number; draft?: Record<string, unknown> },
  ) => Promise<void>;
  onRetryTurn: (turnId: string) => void;
  onRetryFragment: (fragment: LocalFragment) => void;
  onDiscardFragment: (localId: string) => void;
  onFollowUpCandidate: (candidate: FollowUpCandidate) => Promise<boolean>;
  onRetryList: () => void;
  /** Open one thing an answer points at, in the surface that owns it. */
  onOpenResource?: (resource: AnswerResource) => void;
  /** Open the Task recorded in an Action receipt without replaying the Action. */
  onOpenTask?: (taskId: string) => void;
  /** Open the Meeting recorded in an Action receipt without replaying the Action. */
  onOpenMeeting?: (meetingId: string) => void;
  /** Scroll-up pagination: fetch and prepend the batch just before the oldest message currently held. */
  onLoadOlderMessages?: () => void;
  loadingOlderMessages?: boolean;
}) {
  const visibleAssistantState = assistantState ?? deriveAssistantPresentationState({
    conversations: activeConversation ? [activeConversation] : [],
    answerReady: false,
    hovered: false,
  });
  const [query, setQuery] = useState("");
  const [utilityView, setUtilityView] = useState<UtilityView | null>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  // Auto-grow: the browser resize handle is off; height follows content up to the CSS max-height, then scrolls inside.
  useEffect(() => {
    const element = composer.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${element.scrollHeight}px`;
  }, [message, activeConversation?.conversation_id, utilityView]);
  useEffect(() => {
    if (utilityView === "history") searchInput.current?.focus();
  }, [utilityView]);
  const historyConversations = useMemo(() => conversations.filter(conversationHasAnswer), [conversations]);
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return historyConversations;
    return historyConversations.filter((item) => conversationDisplayTitle(item).toLowerCase().includes(needle) || conversationExcerpt(item).toLowerCase().includes(needle));
  }, [historyConversations, query]);
  const isStartState = !activeConversation || (
    activeConversation.messages.length === 0
    && activeConversation.turns.length === 0
    && (activeConversation.actions?.length ?? 0) === 0
    && localFragments.length === 0
  );
  const submitMessage = () => {
    if (!message.trim()) {
      composer.current?.focus();
      return;
    }
    onSend();
  };
  const toggleHistory = () => {
    if (utilityView === "history") {
      setUtilityView(null);
      return;
    }
    setUtilityView("history");
    onRetryList();
  };

  return (
    <aside aria-label="AX 대화" className="scax-drawer scax-drawer--sm scax-drawer--chat">
      <header className="scax-drawer__head scax-chat__head">
        <div className="scax-chat__identity">
          <AssistantCharacter characterKey={characterKey} size="header" state={visibleAssistantState} />
          <div>
            <b>{isStartState ? "새로운 대화" : conversationDisplayTitle(activeConversation!)}</b>
            <small>{isStartState ? "방금 전" : `${surfaceLabel} · ${conversationSummary(activeConversation!)}`}</small>
          </div>
        </div>
        <div className="scax-chat__tools">
          <button
            aria-label="대화 히스토리"
            aria-pressed={utilityView === "history"}
            className="scax-chat__tool"
            onClick={toggleHistory}
            type="button"
          ><HeaderIcon kind="history" /></button>
          <button
            aria-label="새 AX 대화"
            className="scax-chat__tool"
            onClick={() => {
              setUtilityView(null);
              setQuery("");
              onStart();
            }}
            type="button"
          ><HeaderIcon kind="new" /></button>
          <span aria-hidden className="scax-chat__tool-divider" />
          <button aria-label="닫기" className="scax-chat__tool scax-chat__tool--close" onClick={onClose} type="button">
            <HeaderIcon kind="close" />
          </button>
        </div>
      </header>

      {utilityView ? (
        <section aria-label="대화 히스토리 화면" className="scax-chat__utility scax-chat__utility--history">
          <div className="scax-chat__utility-head">
            <h2>대화 히스토리</h2>
            <p>지난 대화의 제목이나 첫 메시지로 검색하거나, 이어서 볼 대화를 선택하세요.</p>
          </div>
          <input
            aria-label="대화 검색"
            className="scax-chat__search search-input-box"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="대화 검색…"
            ref={searchInput}
            type="search"
            value={query}
          />
          {listStatus === "loading" && conversations.length === 0 ? (
            <Skeleton label="대화를 불러오는 중" rows={3} />
          ) : listStatus === "error" && conversations.length === 0 ? (
            <p className="scax-chat__list-note scax-chat__list-note--error">
              대화 목록을 불러오지 못했습니다.
              <Button size="sm" onClick={onRetryList} type="button">
                다시 시도
              </Button>
            </p>
          ) : historyConversations.length === 0 ? (
            <p className="scax-chat__list-note">아직 완료된 대화가 없습니다.</p>
          ) : filtered.length === 0 ? (
            <p className="scax-chat__list-note">검색 결과가 없습니다.</p>
          ) : (
            <ul aria-label="대화 히스토리" className="scax-chat__history">
              {filtered.map((conversation) => (
                <li key={conversation.conversation_id}>
                  <button
                    aria-label={conversationDisplayTitle(conversation)}
                    aria-pressed={activeConversation?.conversation_id === conversation.conversation_id}
                    data-conversation-id={conversation.conversation_id}
                    onClick={() => {
                      setUtilityView(null);
                      onSelect(conversation);
                    }}
                    title={conversationExcerpt(conversation)}
                    type="button"
                  >
                    <b>{conversationDisplayTitle(conversation)}</b>
                    <small>{conversationSummary(conversation)}</small>
                    <span className="scax-chat__history-excerpt">{conversationExcerpt(conversation)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : (
        <>
          {!isStartState && activeConversation ? (
            <>
              <MessageList
                personaId={personaId}
                conversation={activeConversation}
                localFragments={localFragments}
                onDecide={onDecide}
                onDiscardFragment={onDiscardFragment}
                onFollowUpCandidate={onFollowUpCandidate}
                // 근거의 `상세 열기`와 답변 안의 정본 제목은 소유 화면으로 바로 이동한다.
                onOpenResource={onOpenResource}
                onOpenTask={onOpenTask}
                onOpenMeeting={onOpenMeeting}
                onRetryFragment={onRetryFragment}
                onRetryTurn={onRetryTurn}
                onLoadOlderMessages={onLoadOlderMessages}
                loadingOlderMessages={loadingOlderMessages}
              />
            </>
          ) : (
            <div className="scax-chat__stream scax-chat__start">
              <div className="scax-chat__start-card">
                <AssistantCharacter characterKey={characterKey} state={visibleAssistantState} />
                <h2>{personName(personaName)}님 무엇을 도와드릴까요?</h2>
                <p>원하는 작업을 입력하거나 아래에서 선택해 보세요.</p>
                <div aria-label="추천 시작 대화" className="scax-chat__starters">
                  {starterPrompts.map((prompt) => (
                    <button
                      key={prompt}
                      onClick={() => onSend(prompt)}
                      type="button"
                    >
                      <span aria-hidden>＋</span>{prompt}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}

          <div className="scax-chat__composer">
            {selectedContext && (
              <div aria-label="참고 자료" className="scax-chat__context">
                <div className="scax-chat__context-chip">
                  <Icon name="paperclip" size={14} />
                  <span>
                    {selectedContext.resource_type === "task" ? "업무" : "업무 요청"} · {selectedContext.label ?? selectedContext.resource_id.slice(0, 8)}
                  </span>
                  <button aria-label="참고 자료 떼기" onClick={onClearContext} type="button">
                    <Icon name="close" />
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
                  if (event.repeat || event.nativeEvent.isComposing) return;
                  submitMessage();
                }
              }}
              placeholder={activeConversation && isProcessing ? "실행 중에도 이어서 보낼 수 있습니다 · 순서대로 처리됩니다" : "메시지를 입력해 주세요."}
              value={message}
            />
            <div className="scax-chat__composer-actions">
              {isProcessing && activeConversation ? (
                <Button variant="text" size="sm" onClick={onCancel} type="button">
                  실행 취소
                </Button>
              ) : (
                <span className="t-meta">⌘/Ctrl+Enter로 보내기</span>
              )}
              <Button variant="solid" tone="primary" size="sm" onClick={submitMessage} type="button">
                {isProcessing ? "대기열에 보내기" : "보내기"}
              </Button>
            </div>
          </div>
        </>
      )}
    </aside>
  );
}
