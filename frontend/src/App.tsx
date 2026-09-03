import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  createConversation,
  cancelConversation,
  decideAction,
  getActionInbox,
  getConversation,
  getConversations,
  getDeveloperPersonas,
  getMyWork,
  getSession,
  logout,
  sendConversationMessage,
} from "./api";
import { CalendarPage } from "./CalendarPage";
import { DailyReportPage } from "./DailyReportPage";
import { executionStateText, personName } from "./labels";
import { LoginPage } from "./LoginPage";
import { Toast } from "./Modal";
import { MyWorkPage } from "./MyWorkPage";
import { OrgPage } from "./OrgPage";
import { TodayPage } from "./TodayPage";
import {
  isDirectTask,
  type Conversation,
  type ConversationContextReference,
  type DirectTask,
  type OrganizationProfile,
  type Persona,
  type ProductSurface,
} from "./viewModels";

const navigation: ReadonlyArray<{ id: ProductSurface; label: string }> = [
  { id: "today", label: "오늘" },
  { id: "calendar", label: "캘린더" },
  { id: "work", label: "내 업무" },
  { id: "report", label: "보고" },
  { id: "org", label: "조직" },
];

type LabeledContextReference = ConversationContextReference & { label?: string; pinned?: boolean };

const surfaceLabel: Record<ProductSurface, string> = {
  today: "오늘",
  calendar: "캘린더",
  work: "내 업무",
  report: "보고",
  org: "조직",
};

export default function App() {
  const [session, setSession] = useState<OrganizationProfile | null | undefined>(undefined);
  const personaId = session?.member_id ?? "";
  const [personas, setPersonas] = useState<Persona[]>([]);
  const capabilities = session?.capabilities ?? null;
  const organizationNames = session?.organizations.map((organization) => organization.name) ?? [];
  const [surface, setSurface] = useState<ProductSurface>("today");
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [isAxOpen, setIsAxOpen] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversation, setActiveConversation] = useState<Conversation | null>(null);
  const [message, setMessage] = useState("");
  const [contextOptions, setContextOptions] = useState<LabeledContextReference[]>([]);
  const [selectedContextKey, setSelectedContextKey] = useState("");
  const activeConversationRef = useRef<Conversation | null>(null);
  const listRequestGeneration = useRef(0);
  const detailRequestGeneration = useRef(0);

  useEffect(() => {
    let cancelled = false;
    void getSession()
      .then((profile) => {
        if (!cancelled) setSession(profile);
      })
      .catch(() => {
        if (!cancelled) {
          setSession(null);
          setError("세션을 확인하지 못했습니다. 서버 연결을 확인해 주세요.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!session) return;
    let cancelled = false;
    void getDeveloperPersonas()
      .then((availablePersonas) => {
        if (!cancelled) setPersonas(availablePersonas.filter((persona) => persona.id !== "demo-admin"));
      })
      .catch(() => {
        if (!cancelled) setPersonas([]);
      });
    return () => {
      cancelled = true;
    };
  }, [session]);

  function resetWorkspace() {
    listRequestGeneration.current += 1;
    detailRequestGeneration.current += 1;
    setSurface("today");
    setConversations([]);
    activeConversationRef.current = null;
    setActiveConversation(null);
    setIsAxOpen(false);
    setError(null);
  }

  async function endSession() {
    try {
      await logout();
    } catch {
      // The cookie is cleared server-side on a best-effort basis; the client forgets the session regardless.
    }
    resetWorkspace();
    setSession(null);
  }

  const refreshConversations = useCallback(async () => {
    const requestGeneration = ++listRequestGeneration.current;
    const items = await getConversations();
    if (requestGeneration !== listRequestGeneration.current) return;
    const current = activeConversationRef.current;
    const matchingItem = current ? items.find((item) => item.conversation_id === current.conversation_id) : undefined;
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
        current && item.conversation_id === current.conversation_id && item.version <= current.version ? current : item,
      ),
    );
  }, [personaId]);

  const refreshActiveConversation = useCallback(async () => {
    const active = activeConversationRef.current;
    if (!active) return;
    const conversationId = active.conversation_id;
    const requestGeneration = ++detailRequestGeneration.current;
    const next = await getConversation(conversationId);
    if (requestGeneration !== detailRequestGeneration.current) return;
    const current = activeConversationRef.current;
    if (current?.conversation_id !== conversationId) return;
    const projection = next.version >= current.version ? next : current;
    activeConversationRef.current = projection;
    setActiveConversation(projection);
    setConversations((items) =>
      items.map((item) => (item.conversation_id === conversationId && item.version <= projection.version ? projection : item)),
    );
  }, [personaId]);

  useEffect(() => {
    if (!isAxOpen) return;
    void refreshConversations().catch(() => setError("AX 대화를 불러오지 못했습니다."));
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
      const wantsTasks = surface === "today" || surface === "work" || surface === "calendar" || surface === "report";
      const wantsRequests = (surface === "today" || surface === "work") && (capabilities?.includes("work_request.decide") ?? false);
      const [tasks, requests] = await Promise.all([
        wantsTasks ? getMyWork().then((items) => items.filter(isDirectTask)).catch(() => []) : Promise.resolve([]),
        wantsRequests ? getActionInbox().catch(() => []) : Promise.resolve([]),
      ]);
      if (cancelled) return;
      const next: LabeledContextReference[] = [
        ...tasks.map((task) => ({ resource_type: "task" as const, resource_id: task.task_id, resource_version: task.version, included: true, label: task.title })),
        ...requests.map((request) => ({ resource_type: "work_request" as const, resource_id: request.request_id, resource_version: request.version, included: true, label: request.title })),
      ];
      setContextOptions((current) => {
        const pinned = current.filter((item) => item.pinned && !next.some((candidate) => contextKey(candidate) === contextKey(item)));
        return [...pinned, ...next];
      });
    };
    void loadContextOptions().catch(() => {
      if (!cancelled) setError("현재 화면의 AX 참고 자료를 불러오지 못했습니다.");
    });
    return () => {
      cancelled = true;
    };
  }, [capabilities, isAxOpen, personaId, surface]);

  useEffect(() => {
    if (!contextOptions.some((item) => contextKey(item) === selectedContextKey)) {
      setSelectedContextKey("");
    }
  }, [contextOptions, selectedContextKey]);

  function adoptConversation(conversation: Conversation) {
    listRequestGeneration.current += 1;
    detailRequestGeneration.current += 1;
    activeConversationRef.current = conversation;
    setConversations((items) => [conversation, ...items.filter((item) => item.conversation_id !== conversation.conversation_id)]);
    setActiveConversation(conversation);
  }

  async function startConversation() {
    try {
      adoptConversation(await createConversation());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "새 대화를 만들지 못했습니다.");
    }
  }

  async function askAx(text: string) {
    setIsAxOpen(true);
    try {
      const conversation = await createConversation();
      adoptConversation(conversation);
      await sendConversationMessage(conversation.conversation_id, text, [], createIdempotencyKey());
      await refreshActiveConversation();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "AX에게 질문을 보내지 못했습니다.");
    }
  }

  function askAboutTask(task: DirectTask) {
    const reference: LabeledContextReference = {
      resource_type: "task",
      resource_id: task.task_id,
      resource_version: task.version,
      included: true,
      label: task.title,
      pinned: true,
    };
    setContextOptions((current) => (current.some((item) => contextKey(item) === contextKey(reference)) ? current : [reference, ...current]));
    setSelectedContextKey(contextKey(reference));
    setMessage((current) => (current.trim() ? current : `'${task.title}' 업무에 대해 알려줘.`));
    setIsAxOpen(true);
  }

  async function sendMessage() {
    if (!message.trim() || !activeConversation) return;
    try {
      const selectedContext = contextOptions.find((item) => contextKey(item) === selectedContextKey);
      await sendConversationMessage(activeConversation.conversation_id,
        message,
        selectedContext ? [stripLabel(selectedContext)] : [],
        createIdempotencyKey(),
      );
      setMessage("");
      await refreshActiveConversation();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "AX 메시지를 접수하지 못했습니다.");
    }
  }

  async function decideConversationAction(actionId: string, expectedVersion: number, decision: "approve" | "reject") {
    try {
      await decideAction(actionId, expectedVersion, decision);
      await refreshActiveConversation();
      setToast(decision === "approve" ? "제안을 승인해 반영했습니다." : "제안을 거절했습니다.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "AX 확인 항목을 처리하지 못했습니다.");
    }
  }

  async function cancelActiveConversation() {
    if (!activeConversation) return;
    try {
      await cancelConversation(activeConversation.conversation_id, activeConversation.version);
      await refreshActiveConversation();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "AX 실행을 취소하지 못했습니다.");
    }
  }

  const currentPersonaName = session?.display_name ?? "사용자";
  const selectedContext = contextOptions.find((item) => contextKey(item) === selectedContextKey);
  const has = (capability: string) => capabilities?.includes(capability) ?? false;
  const canReadActions = has("action.read");
  const canDecideActions = has("action.decide");
  const visibleNavigation = navigation.filter((item) => item.id !== "report" || has("daily_report.generate"));
  const pageProps = { personaId, onError: setError };
  const sharedWorkProps = {
    personaName: currentPersonaName,
    personas,
    canCreateWorkRequests: has("work_request.create"),
    canDecideActions,
    canDecideWorkRequests: has("work_request.decide"),
    canManageOwnTasks: has("task.self_manage"),
    canReadActions,
    onAskAboutTask: askAboutTask,
    onNotice: setToast,
  };

  if (session === undefined) {
    return (
      <main className="login-shell" aria-busy="true">
        <section className="login-panel">
          <p className="login-lead">세션을 확인하는 중…</p>
        </section>
      </main>
    );
  }
  if (session === null) {
    return (
      <LoginPage
        onLoggedIn={(profile) => {
          resetWorkspace();
          setSession(profile);
        }}
      />
    );
  }

  return (
    <main className="thesc-shell">
      <aside className="rail">
        <div className="wordmark">
          <span aria-hidden className="wordmark-mark">
            SC
          </span>
          SCAX
        </div>
        <div className="profile">
          <span className="avatar md">{personName(currentPersonaName).slice(0, 1)}</span>
          <div>
            <b>{personName(currentPersonaName)}</b>
            <small>{organizationNames.length > 0 ? organizationNames.join(" · ") : "소속 없음"}</small>
          </div>
        </div>
        <div className="profile-menu">
          <button className="btn h30 ghost" onClick={() => void endSession()} type="button">
            로그아웃
          </button>
        </div>
        <nav aria-label="제품 탐색">
          {visibleNavigation.map((item) => (
            <button className={surface === item.id ? "active" : ""} key={item.id} onClick={() => setSurface(item.id)} type="button">
              {item.label}
            </button>
          ))}
        </nav>
        <p className="rail-foot">SCAX · 업무 운영 시스템</p>
      </aside>

      <section className="canvas">
        <header className="canvas-topbar">
          <nav aria-label="현재 위치" className="breadcrumb">
            {surface === "today" ? (
              <b>홈</b>
            ) : (
              <>
                <button className="btn link" onClick={() => setSurface("today")} type="button">
                  홈
                </button>
                <span aria-hidden>›</span>
                <b>{surfaceLabel[surface]}</b>
              </>
            )}
          </nav>
          <span className="t-meta">{currentPersonaName}</span>
        </header>

        {error && (
          <div className="error-banner" role="alert">
            {error}
            <button className="btn h30 ghost" onClick={() => setError(null)} type="button">
              닫기
            </button>
          </div>
        )}

        {surface === "today" && (
          <TodayPage
            {...pageProps}
            {...sharedWorkProps}
            canGenerateDailyReport={has("daily_report.generate")}
            onAskAx={(text) => void askAx(text)}
            onNavigate={setSurface}
          />
        )}
        {surface === "calendar" && <CalendarPage {...pageProps} {...sharedWorkProps} />}
        {surface === "work" && <MyWorkPage {...pageProps} {...sharedWorkProps} />}
        {surface === "report" && <DailyReportPage {...pageProps} personaName={currentPersonaName} />}
        {surface === "org" && <OrgPage {...pageProps} />}
      </section>

      {toast && <Toast message={toast} onClose={() => setToast(null)} />}

      {!isAxOpen && (
        <button className="ax-launcher" onClick={() => setIsAxOpen(true)} type="button">
          <span aria-hidden>✦</span> AX
        </button>
      )}
      {isAxOpen && (
        <aside aria-label="AX 대화" className="ax-drawer">
          <header>
            <div>
              <b>AX 에이전트</b>
              <small>
                {surfaceLabel[surface]} 화면 · {personName(currentPersonaName)} · 허용된 업무 기능만 조회하고 변경은 승인 뒤 반영됩니다
              </small>
            </div>
            <button className="btn h30 ghost" onClick={() => setIsAxOpen(false)} type="button">
              닫기
            </button>
          </header>
          <div className="ax-composer-actions">
            <button aria-label="새 AX 대화" className="btn h30 ai" onClick={() => void startConversation()} type="button">
              ✦ 새 대화
            </button>
          </div>
          <div className="ax-conversation-list">
            {conversations.map((conversation) => (
              <button
                aria-label={conversation.title}
                aria-pressed={activeConversation?.conversation_id === conversation.conversation_id}
                data-conversation-id={conversation.conversation_id}
                key={conversation.conversation_id}
                onClick={() => {
                  detailRequestGeneration.current += 1;
                  activeConversationRef.current = conversation;
                  setActiveConversation(conversation);
                }}
                title={conversationExcerpt(conversation)}
                type="button"
              >
                <b>{conversation.title}</b>
                <small>{conversationSummary(conversation)}</small>
              </button>
            ))}
          </div>
          <div className="ax-messages">
            {activeConversation ? (
              <ConversationTimeline canDecideActions={canDecideActions} conversation={activeConversation} onDecide={decideConversationAction} />
            ) : (
              <p className="ax-empty">
                안녕하세요 {personName(currentPersonaName)}님!
                <br />
                새 대화를 만들고 업무에 대해 무엇이든 물어보세요.
              </p>
            )}
          </div>
          <div className="ax-composer">
            {selectedContext && (
              <div className="ax-context-chip">
                <span aria-hidden>📎</span>
                <span>
                  {selectedContext.resource_type === "task" ? "업무" : "업무 요청"} · {selectedContext.label ?? selectedContext.resource_id.slice(0, 8)}
                </span>
                <button aria-label="참고 자료 떼기" onClick={() => setSelectedContextKey("")} type="button">
                  ×
                </button>
              </div>
            )}
            <label className="sr-only" htmlFor="ax-message">
              AX 메시지
            </label>
            <textarea
              id="ax-message"
              onChange={(event) => setMessage(event.target.value)}
              placeholder={activeConversation ? "업무에 대해 질문하세요" : "먼저 새 대화를 만들어 주세요"}
              value={message}
            />
            <div className="ax-composer-actions">
              {isProcessing && activeConversation ? (
                <button className="btn h30 ghost" onClick={() => void cancelActiveConversation()} type="button">
                  실행 취소
                </button>
              ) : (
                <span />
              )}
              <button className="btn primary" disabled={!activeConversation || !message.trim()} onClick={() => void sendMessage()} type="button">
                {isProcessing ? "대기열에 보내기" : "보내기"}
              </button>
            </div>
          </div>
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

  if (conversation.turns.length === 0 && queuedMessages.length === 0) {
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
          <ToolTimeline tools={conversation.tool_invocations.filter((tool) => tool.turn_id === turn.turn_id)} />
          {(conversation.actions ?? [])
            .filter((action) => action.turn_id === turn.turn_id)
            .map((action) => (
              <section className="ax-action-card" data-action-id={action.action_id} key={action.action_id}>
                <b>{action.title}</b>
                <p>{action.payload_summary}</p>
                <small className={action.state}>
                  {action.state === "pending" ? "확인 필요 · 승인해야 반영됩니다" : action.state === "approved" ? "승인됨" : "거절됨"}
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
      {queuedMessages.map((item) => (
        <p className="user queued" key={item.message_id}>
          {item.body} <small>대기 중</small>
        </p>
      ))}
    </>
  );
}

function ToolTimeline({ tools }: { tools: Conversation["tool_invocations"] }) {
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
          </li>
        ))}
      </ol>
    </details>
  );
}

const summaryStateLabel: Record<string, string> = {
  pending: "접수됨",
  running: "실행 중",
  completed: "완료",
  failed: "실패",
  cancelled: "취소됨",
};

function conversationExcerpt(conversation: Conversation): string {
  const firstUserMessage = conversation.messages.find((item) => item.role === "user");
  if (!firstUserMessage) return conversation.title;
  return firstUserMessage.body.replace(/\s+/g, " ").trim();
}

function conversationSummary(conversation: Conversation): string {
  const userMessages = conversation.messages.filter((item) => item.role === "user").length;
  if (userMessages === 0) return "발화 없음";
  const queued = conversation.messages.filter((item) => item.state === "queued").length;
  if (queued > 0) return `발화 ${userMessages} · 대기열 ${queued}`;
  const latestTurn = conversation.turns.at(-1);
  const state = latestTurn ? summaryStateLabel[latestTurn.state] ?? latestTurn.state : "접수됨";
  return `발화 ${userMessages} · ${state}`;
}

function stripLabel(reference: LabeledContextReference): ConversationContextReference {
  const { label: _label, pinned: _pinned, ...rest } = reference;
  return rest;
}

function contextKey(reference: ConversationContextReference): string {
  return `${reference.resource_type}:${reference.resource_id}:${reference.resource_version}`;
}

function createIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `ax-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
