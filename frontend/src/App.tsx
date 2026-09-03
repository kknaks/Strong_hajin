import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  createConversation,
  cancelConversation,
  decideAction,
  getActionInbox,
  getConversation,
  getConversations,
  getDeveloperPersonas,
  getMyOrganizationProfile,
  getMyWork,
  sendConversationMessage,
} from "./api";
import { ActionInboxPage } from "./ActionInboxPage";
import { DailyReportPage } from "./DailyReportPage";
import { MyWorkPage } from "./MyWorkPage";
import { OrgPage } from "./OrgPage";
import { TodayPage } from "./TodayPage";
import {
  isDirectTask,
  type Conversation,
  type ConversationContextReference,
  type Persona,
  type ProductSurface,
} from "./viewModels";

import "./task.css";

const navigation: ReadonlyArray<{ id: ProductSurface; label: string }> = [
  { id: "today", label: "오늘" },
  { id: "work", label: "내 업무" },
  { id: "inbox", label: "판단" },
  { id: "report", label: "보고" },
  { id: "org", label: "조직" },
];

export default function App() {
  const [personaId, setPersonaId] = useState("mina");
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [capabilities, setCapabilities] = useState<string[] | null>(null);
  const [surface, setSurface] = useState<ProductSurface>("today");
  const [error, setError] = useState<string | null>(null);
  const [isAxOpen, setIsAxOpen] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversation, setActiveConversation] = useState<Conversation | null>(null);
  const [message, setMessage] = useState("");
  const [contextOptions, setContextOptions] = useState<ConversationContextReference[]>([]);
  const [selectedContextKey, setSelectedContextKey] = useState("");
  const activeConversationRef = useRef<Conversation | null>(null);
  const listRequestGeneration = useRef(0);
  const detailRequestGeneration = useRef(0);

  useEffect(() => {
    let cancelled = false;

    void getDeveloperPersonas()
      .then((availablePersonas) => {
        if (cancelled) return;
        setPersonas(availablePersonas.filter((persona) => persona.id !== "demo-admin"));
      })
      .catch(() => {
        if (!cancelled) setError("사용자 정보를 불러오지 못했습니다.");
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    void getMyOrganizationProfile(personaId)
      .then((profile) => {
        if (!cancelled) setCapabilities(profile.capabilities);
      })
      .catch(() => {
        if (!cancelled) setCapabilities([]);
      });

    return () => {
      cancelled = true;
    };
  }, [personaId]);

  const refreshConversations = useCallback(async () => {
    const requestGeneration = ++listRequestGeneration.current;
    const items = await getConversations(personaId);
    if (requestGeneration !== listRequestGeneration.current) return;
    const current = activeConversationRef.current;
    const matchingItem = current
      ? items.find((item) => item.conversation_id === current.conversation_id)
      : undefined;
    const nextActive = !current
      ? items[0] ?? null
      : !matchingItem
        ? items[0] ?? null
        : matchingItem.version > current.version
          ? matchingItem
          : current;
    if (current && !matchingItem) detailRequestGeneration.current += 1;
    activeConversationRef.current = nextActive;
    setActiveConversation(nextActive);
    setConversations(
      items.map((item) =>
        current && item.conversation_id === current.conversation_id && item.version <= current.version
          ? current
          : item,
      ),
    );
  }, [personaId]);

  const refreshActiveConversation = useCallback(async () => {
    const active = activeConversationRef.current;
    if (!active) return;
    const conversationId = active.conversation_id;
    const requestGeneration = ++detailRequestGeneration.current;
    const next = await getConversation(personaId, conversationId);
    if (requestGeneration !== detailRequestGeneration.current) return;
    const current = activeConversationRef.current;
    if (current?.conversation_id !== conversationId) return;
    const projection = next.version >= current.version ? next : current;
    activeConversationRef.current = projection;
    setActiveConversation(projection);
    setConversations((items) =>
      items.map((item) =>
        item.conversation_id === conversationId && item.version <= projection.version ? projection : item,
      ),
    );
  }, [personaId]);

  useEffect(() => {
    if (!isAxOpen) return;
    void refreshConversations()
      .catch(() => setError("AX 대화를 불러오지 못했습니다."));
  }, [isAxOpen, refreshConversations]);

  const isProcessing = useMemo(
    () =>
      activeConversation?.turns.some((turn) => turn.state === "pending" || turn.state === "running") ||
      activeConversation?.messages.some((item) => item.state === "queued"),
    [activeConversation],
  );

  useEffect(() => {
    if (!isAxOpen || !isProcessing) return;
    const timer = window.setInterval(() => {
      void refreshActiveConversation().catch(() => setError("AX 상태를 갱신하지 못했습니다."));
    }, 800);
    return () => window.clearInterval(timer);
  }, [isAxOpen, isProcessing, refreshActiveConversation]);

  useEffect(() => {
    if (!isAxOpen) return;
    let cancelled = false;
    const loadContextOptions = async () => {
      if (surface === "work") {
        const tasks = (await getMyWork(personaId)).filter(isDirectTask);
        if (!cancelled) {
          setContextOptions(
            tasks.map((task) => ({
              resource_type: "task",
              resource_id: task.task_id,
              resource_version: task.version,
              included: true,
            })),
          );
        }
        return;
      }
      if (surface === "inbox") {
        const requests = await getActionInbox(personaId);
        if (!cancelled) {
          setContextOptions(
            requests.map((request) => ({
              resource_type: "work_request",
              resource_id: request.request_id,
              resource_version: request.version,
              included: true,
            })),
          );
        }
        return;
      }
      if (!cancelled) setContextOptions([]);
    };
    void loadContextOptions().catch(() => {
      if (!cancelled) setError("현재 화면의 AX 참고 자료를 불러오지 못했습니다.");
    });
    return () => {
      cancelled = true;
    };
  }, [isAxOpen, personaId, surface]);

  useEffect(() => {
    if (!contextOptions.some((item) => contextKey(item) === selectedContextKey)) {
      setSelectedContextKey("");
    }
  }, [contextOptions, selectedContextKey]);

  async function startConversation() {
    try {
      const conversation = await createConversation(personaId);
      listRequestGeneration.current += 1;
      detailRequestGeneration.current += 1;
      activeConversationRef.current = conversation;
      setConversations((items) => [
        conversation,
        ...items.filter((item) => item.conversation_id !== conversation.conversation_id),
      ]);
      setActiveConversation(conversation);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "새 대화를 만들지 못했습니다.");
    }
  }

  async function sendMessage() {
    if (!message.trim() || !activeConversation) return;
    try {
      const selectedContext = contextOptions.find((item) => contextKey(item) === selectedContextKey);
      await sendConversationMessage(
        personaId,
        activeConversation.conversation_id,
        message,
        selectedContext ? [selectedContext] : [],
        createIdempotencyKey(),
      );
      setMessage("");
      await refreshActiveConversation();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "AX 메시지를 접수하지 못했습니다.");
    }
  }

  async function decideConversationAction(
    actionId: string,
    expectedVersion: number,
    decision: "approve" | "reject",
  ) {
    try {
      await decideAction(personaId, actionId, expectedVersion, decision);
      await refreshActiveConversation();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "AX 확인 항목을 처리하지 못했습니다.");
    }
  }

  async function cancelActiveConversation() {
    if (!activeConversation) return;
    try {
      await cancelConversation(personaId, activeConversation.conversation_id, activeConversation.version);
      await refreshActiveConversation();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "AX 실행을 취소하지 못했습니다.");
    }
  }

  const currentPersona = personas.find((persona) => persona.id === personaId);
  const currentPersonaName = currentPersona?.display_name ?? "사용자";
  const pageProps = { personaId, onError: setError };
  const canReadActions = capabilities?.includes("action.read") ?? false;
  const canDecideActions = capabilities?.includes("action.decide") ?? false;
  const visibleNavigation = navigation.filter(
    (item) => item.id !== "report" || capabilities?.includes("daily_report.generate"),
  );

  return (
    <main className="thesc-shell">
      <aside className="rail">
        <div className="wordmark">
          <span className="wordmark-mark" />
          SCAX AX
        </div>
        <div className="profile">
          <span className="avatar">{currentPersonaName.slice(0, 1)}</span>
          <div>
            <b>{currentPersonaName}</b>
            <small>워크스페이스</small>
          </div>
        </div>
        <nav aria-label="제품 탐색">
          {visibleNavigation.map((item) => (
            <button
              className={surface === item.id ? "active" : ""}
              key={item.id}
              onClick={() => setSurface(item.id)}
              type="button"
            >
              {item.label}
            </button>
          ))}
        </nav>
      </aside>

      <section className="canvas">
        <header className="canvas-topbar">
          <span className="date-chip">2026년 9월 3일</span>
          <label className="persona-picker">
            사용자
            <select
              onChange={(event) => {
                listRequestGeneration.current += 1;
                detailRequestGeneration.current += 1;
                setSurface("today");
                setCapabilities(null);
                setConversations([]);
                activeConversationRef.current = null;
                setActiveConversation(null);
                setPersonaId(event.target.value);
              }}
              value={personaId}
            >
              {personas.map((persona) => (
                <option key={persona.id} value={persona.id}>
                  {persona.display_name}
                </option>
              ))}
            </select>
          </label>
        </header>

        {error && (
          <div className="error-banner" role="alert">
            {error}
            <button onClick={() => setError(null)} type="button">
              닫기
            </button>
          </div>
        )}

        {surface === "today" && (
          <TodayPage
            {...pageProps}
            canReadActions={canReadActions}
            canDecideWorkRequests={capabilities?.includes("work_request.decide") ?? false}
            canGenerateDailyReport={capabilities?.includes("daily_report.generate") ?? false}
            onNavigate={setSurface}
          />
        )}
        {surface === "work" && (
          <MyWorkPage
            {...pageProps}
            canCreateWorkRequests={capabilities?.includes("work_request.create") ?? false}
            canManageOwnTasks={capabilities?.includes("task.self_manage") ?? false}
          />
        )}
        {surface === "inbox" && (
          <ActionInboxPage
            {...pageProps}
            canDecideActions={canDecideActions}
            canDecideWorkRequests={capabilities?.includes("work_request.decide") ?? false}
            canReadActions={canReadActions}
          />
        )}
        {surface === "report" && <DailyReportPage {...pageProps} />}
        {surface === "org" && <OrgPage {...pageProps} />}
      </section>
      <button className="ax-launcher" onClick={() => setIsAxOpen(true)} type="button">
        AX
      </button>
      {isAxOpen && (
        <aside aria-label="AX 대화" className="ax-drawer">
          <header>
            <b>AX</b>
            <button onClick={() => setIsAxOpen(false)} type="button">
              닫기
            </button>
          </header>
          <button
            aria-label="새 AX 대화"
            className="primary"
            onClick={() => void startConversation()}
            type="button"
          >
            새 대화
          </button>
          <div className="ax-conversation-list">
            {conversations.map((conversation) => (
              <button
                aria-pressed={activeConversation?.conversation_id === conversation.conversation_id}
                data-conversation-id={conversation.conversation_id}
                key={conversation.conversation_id}
                onClick={() => {
                  detailRequestGeneration.current += 1;
                  activeConversationRef.current = conversation;
                  setActiveConversation(conversation);
                }}
                type="button"
              >
                {conversation.title}
              </button>
            ))}
          </div>
          <div className="ax-messages">
            {activeConversation && (
              <ConversationTimeline
                canDecideActions={canDecideActions}
                conversation={activeConversation}
                onDecide={decideConversationAction}
              />
            )}
          </div>
          {isProcessing && activeConversation && (
            <button onClick={() => void cancelActiveConversation()} type="button">
              실행 취소
            </button>
          )}
          {contextOptions.length > 0 && (
            <label className="ax-context" htmlFor="ax-context">
              현재 화면 참고 자료
              <select
                id="ax-context"
                onChange={(event) => setSelectedContextKey(event.target.value)}
                value={selectedContextKey}
              >
                <option value="">첨부하지 않음</option>
                {contextOptions.map((item) => (
                  <option key={contextKey(item)} value={contextKey(item)}>
                    {item.resource_type === "task" ? "업무" : "업무 요청"} · {item.resource_id.slice(0, 8)}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="sr-only" htmlFor="ax-message">
            AX 메시지
          </label>
          <textarea id="ax-message" onChange={(event) => setMessage(event.target.value)} value={message} />
          <button className="primary" disabled={!activeConversation || !message.trim()} onClick={() => void sendMessage()} type="button">
            {isProcessing ? "대기열에 보내기" : "보내기"}
          </button>
        </aside>
      )}
    </main>
  );
}

function ConversationTimeline({
  canDecideActions,
  conversation,
  onDecide,
}: {
  canDecideActions: boolean;
  conversation: Conversation;
  onDecide: (actionId: string, expectedVersion: number, decision: "approve" | "reject") => Promise<void>;
}) {
  const queuedMessages = conversation.messages.filter((item) => item.state === "queued");

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
          <small className={`ax-turn-state ${turn.state}`}>{turn.state === "failed" ? "실패" : turn.state === "running" ? "실행 중" : turn.state === "pending" ? "대기 중" : "완료"}</small>
          {turn.error && <p className="ax-turn-error">{turn.error}</p>}
          {conversation.tool_invocations
            .filter((tool) => tool.turn_id === turn.turn_id)
            .map((tool) => (
              <details key={`${tool.turn_id}-${tool.sequence}`}>
                <summary>{tool.display_name} · {tool.state}</summary>
                <p>{tool.input_summary}</p>
                <p>{tool.result_summary ?? tool.error_summary ?? "실행 중"}</p>
                <p>{tool.latency_ms === null ? "소요 시간 기록 없음" : `${tool.latency_ms}ms`}</p>
              </details>
            ))}
          {(conversation.actions ?? [])
            .filter((action) => action.turn_id === turn.turn_id)
            .map((action) => (
              <section className="ax-action-card" data-action-id={action.action_id} key={action.action_id}>
                <b>{action.title}</b>
                <p>{action.payload_summary}</p>
                <small>{action.state === "pending" ? "확인 필요" : action.state === "approved" ? "승인됨" : "거절됨"}</small>
                {action.state === "pending" && canDecideActions && (
                  <div>
                    <button onClick={() => void onDecide(action.action_id, action.version, "approve")} type="button">
                      승인
                    </button>
                    <button onClick={() => void onDecide(action.action_id, action.version, "reject")} type="button">
                      거절
                    </button>
                  </div>
                )}
              </section>
            ))}
        </section>
      ))}
      {queuedMessages.map((item) => (
        <p className="user queued" key={item.message_id}>
          {item.body} <small>대기 중</small>
        </p>
      ))}
    </>
  );
}

function contextKey(reference: ConversationContextReference): string {
  return `${reference.resource_type}:${reference.resource_id}:${reference.resource_version}`;
}

function createIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `ax-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
