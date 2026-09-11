import { useEffect, useMemo, useRef, useState } from "react";

import { AssistantCharacter } from "../AssistantCharacter";
import { deriveAssistantPresentationState, type AssistantPresentationState } from "../assistantPresentation";
import { personName } from "../labels";
import type { AnswerResource, Conversation, ConversationContextReference, FollowUpCandidate } from "../viewModels";
import { MessageList } from "./MessageList";
import type { ListStatus, LocalFragment } from "./useConversations";
import { Skeleton } from "../Skeleton";
import { Icon } from "../Icon";
import { getNotifications, markNotificationRead } from "../api";
import type { Notification } from "../viewModels";

export type LabeledContextReference = ConversationContextReference & { label?: string; pinned?: boolean };
type UtilityView = "search" | "history" | "notifications";

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

function HeaderIcon({ kind }: { kind: "search" | "history" | "new" | "notification" | "close" }) {
  const path = {
    search: <><circle cx="10" cy="10" r="5" /><path d="m14 14 4 4" /></>,
    history: <><circle cx="12" cy="12" r="8" /><path d="M12 7v5l3 2" /></>,
    new: <><path d="M5 6.5A2.5 2.5 0 0 1 7.5 4h9A2.5 2.5 0 0 1 19 6.5v7a2.5 2.5 0 0 1-2.5 2.5H11l-4 3v-3.2A2.5 2.5 0 0 1 5 13.5Z" /><path d="M12 7v6M9 10h6" /></>,
    notification: <><path d="M6.5 9.5a5.5 5.5 0 0 1 11 0c0 6 2.5 6 2.5 7.5H4c0-1.5 2.5-1.5 2.5-7.5Z" /><path d="M10 20h4" /></>,
    close: <path d="m7 7 10 10M17 7 7 17" />,
  }[kind];
  return <svg aria-hidden className="ax-header-icon" fill="none" viewBox="0 0 24 24">{path}</svg>;
}

export function conversationExcerpt(conversation: Conversation): string {
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
  return conversation.messages.some((message) => (
    message.role === "assistant" && message.body_state === "final" && message.body.trim() !== ""
  ));
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
}) {
  const visibleAssistantState = assistantState ?? deriveAssistantPresentationState({
    conversations: activeConversation ? [activeConversation] : [],
    answerReady: false,
    hovered: false,
  });
  const [query, setQuery] = useState("");
  const [utilityView, setUtilityView] = useState<UtilityView | null>(null);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [notificationStatus, setNotificationStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [notificationActionError, setNotificationActionError] = useState<string | null>(null);
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
    if (utilityView === "search") searchInput.current?.focus();
  }, [utilityView]);
  useEffect(() => {
    if (utilityView !== "notifications") return;
    let current = true;
    setNotificationActionError(null);
    setNotificationStatus("loading");
    void getNotifications().then(
      (items) => {
        if (!current) return;
        setNotifications(items);
        setNotificationStatus("ready");
      },
      () => {
        if (current) setNotificationStatus("error");
      },
    );
    return () => { current = false; };
  }, [personaId, utilityView]);
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
  const toggleConversationUtility = (view: Extract<UtilityView, "search" | "history">) => {
    if (utilityView === view) {
      setUtilityView(null);
      return;
    }
    setUtilityView(view);
    onRetryList();
  };

  return (
    <aside aria-label="AX 대화" className="ax-drawer">
      <header>
        <div className="ax-drawer-identity">
          <AssistantCharacter characterKey={characterKey} size="header" state={visibleAssistantState} />
          <div>
            <b>{isStartState ? "새로운 대화" : conversationDisplayTitle(activeConversation!)}</b>
            <small>{isStartState ? "방금 전" : `${surfaceLabel} · ${conversationSummary(activeConversation!)}`}</small>
          </div>
        </div>
        <div className="ax-header-actions">
          <button
            aria-label="대화 검색"
            aria-pressed={utilityView === "search"}
            className="ax-header-action"
            onClick={() => toggleConversationUtility("search")}
            type="button"
          ><HeaderIcon kind="search" /></button>
          <button
            aria-label="대화 히스토리"
            aria-pressed={utilityView === "history"}
            className="ax-header-action"
            onClick={() => toggleConversationUtility("history")}
            type="button"
          ><HeaderIcon kind="history" /></button>
          <button
            aria-label="새 AX 대화"
            className="ax-header-action"
            onClick={() => {
              setUtilityView(null);
              setQuery("");
              onStart();
            }}
            type="button"
          ><HeaderIcon kind="new" /></button>
          <button
            aria-label="알림"
            aria-pressed={utilityView === "notifications"}
            className="ax-header-action"
            onClick={() => setUtilityView((current) => current === "notifications" ? null : "notifications")}
            type="button"
          >
            <HeaderIcon kind="notification" />
          </button>
          <span aria-hidden className="ax-header-divider" />
          <button aria-label="닫기" className="ax-header-action close" onClick={onClose} type="button">
            <HeaderIcon kind="close" />
          </button>
        </div>
      </header>

      {utilityView ? (
        <section
          aria-label={`${utilityView === "search" ? "대화 검색" : utilityView === "history" ? "대화 히스토리" : "알림"} 화면`}
          className={`ax-utility-view ${utilityView}`}
        >
          <div className="ax-utility-heading">
            <h2>{utilityView === "search" ? "대화 검색" : utilityView === "history" ? "대화 히스토리" : "알림"}</h2>
            <p>{utilityView === "search" ? "지난 대화의 제목이나 첫 메시지를 검색하세요." : utilityView === "history" ? "이어서 볼 대화를 선택하세요." : "새로운 소식을 여기에서 확인하세요."}</p>
          </div>
          {utilityView === "notifications" ? (
            notificationStatus === "loading" ? (
              <Skeleton label="알림을 불러오는 중" rows={3} />
            ) : notificationStatus === "error" ? (
              <p className="ax-sessions-state error">알림을 불러오지 못했습니다.</p>
            ) : notifications.length === 0 ? (
              <div className="ax-utility-empty">
                <HeaderIcon kind="notification" />
                <b>새 알림이 없습니다.</b>
                <p>새로운 알림이 도착하면 이 화면에 표시됩니다.</p>
              </div>
            ) : (
              <>
                {notificationActionError ? <p className="ax-notification-error" role="alert">{notificationActionError}</p> : null}
                <ul aria-label="알림 목록" className="ax-notification-list">
                  {notifications.map((notification) => (
                    <li key={notification.notification_id}>
                      <button
                        className={notification.read_at ? "ax-notification read" : "ax-notification"}
                        onClick={() => {
                          setNotificationActionError(null);
                          void markNotificationRead(notification.notification_id).then((read) => {
                            setNotifications((items) => items.map((item) => (
                              item.notification_id === read.notification_id ? read : item
                            )));
                            onOpenResource?.({
                              reference_id: `notification:${read.notification_id}`,
                              turn_id: "",
                              sequence: 0,
                              resource_type: read.resource.type,
                              resource_id: read.resource.id,
                              resource_version: read.resource.version,
                              title: read.resource.title,
                              state: null,
                            });
                          }, () => {
                            setNotificationActionError("알림을 열지 못했습니다. 다시 시도해 주세요.");
                          });
                        }}
                        type="button"
                      >
                        <span aria-hidden className="ax-notification-dot" />
                        <span>
                          <b>{notification.resource.title}</b>
                          <span>{notification.summary}</span>
                        </span>
                        <time dateTime={notification.created_at}>{new Date(notification.created_at).toLocaleString("ko-KR")}</time>
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            )
          ) : (
            <>
              {utilityView === "search" && (
              <input
                aria-label="대화 검색"
                className="ax-sessions-search search-input-box"
                onChange={(event) => setQuery(event.target.value)}
                placeholder="대화 검색…"
                ref={searchInput}
                type="search"
                value={query}
              />
              )}
              {listStatus === "loading" && conversations.length === 0 ? (
                <Skeleton label="대화를 불러오는 중" rows={3} />
              ) : listStatus === "error" && conversations.length === 0 ? (
                <p className="ax-sessions-state error">
                  대화 목록을 불러오지 못했습니다.
                  <button className="btn h30" onClick={onRetryList} type="button">
                    다시 시도
                  </button>
                </p>
              ) : historyConversations.length === 0 ? (
                <p className="ax-sessions-state">아직 완료된 대화가 없습니다.</p>
              ) : filtered.length === 0 ? (
                <p className="ax-sessions-state">검색 결과가 없습니다.</p>
              ) : (
                <ul aria-label="대화 히스토리" className="ax-conversation-list">
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
                        <span className="ax-session-excerpt">{conversationExcerpt(conversation)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </>
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
              />
            </>
          ) : (
            <div className="ax-messages-wrap ax-new-conversation">
              <div className="ax-empty-state">
                <AssistantCharacter characterKey={characterKey} state={visibleAssistantState} />
                <h2>{personName(personaName)}님 무엇을 도와드릴까요?</h2>
                <p>원하는 작업을 입력하거나 아래에서 선택해 보세요.</p>
                <div aria-label="추천 시작 대화" className="ax-starter-prompts">
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

          <div className="ax-composer">
            {selectedContext && (
              <div aria-label="참고 자료" className="ax-context-row">
                <div className="ax-context-chip">
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
            <div className="ax-composer-actions">
              {isProcessing && activeConversation ? (
                <button className="btn h30 ghost" onClick={onCancel} type="button">
                  실행 취소
                </button>
              ) : (
                <span className="t-meta">⌘/Ctrl+Enter로 보내기</span>
              )}
              <button className="btn primary" onClick={submitMessage} type="button">
                {isProcessing ? "대기열에 보내기" : "보내기"}
              </button>
            </div>
          </div>
        </>
      )}
    </aside>
  );
}
