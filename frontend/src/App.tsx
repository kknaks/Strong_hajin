import { useCallback, useEffect, useState } from "react";

import { getActionInbox, getDeveloperPersonas, getMyWork, getSession, logout } from "./api";
import { CalendarPage } from "./CalendarPage";
import { ChatDrawer, contextKey, type LabeledContextReference } from "./chat/ChatDrawer";
import { useConversations } from "./chat/useConversations";
import { DailyReportPage } from "./DailyReportPage";
import { personName } from "./labels";
import { LoginPage } from "./LoginPage";
import { Toast } from "./Modal";
import { MyWorkPage } from "./MyWorkPage";
import { OrgPage } from "./OrgPage";
import { TodayPage } from "./TodayPage";
import type { ConversationContextReference, DirectTask, OrganizationProfile, Persona, ProductSurface } from "./viewModels";

const navigation: ReadonlyArray<{ id: ProductSurface; label: string }> = [
  { id: "today", label: "오늘" },
  { id: "calendar", label: "캘린더" },
  { id: "work", label: "내 업무" },
  { id: "report", label: "보고" },
  { id: "org", label: "조직" },
];

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
  const [message, setMessage] = useState("");
  const [contextOptions, setContextOptions] = useState<LabeledContextReference[]>([]);
  const [selectedContextKey, setSelectedContextKey] = useState("");
  const reportError = useCallback((text: string) => setError(text), []);
  const chat = useConversations({ personaId, isOpen: isAxOpen, onError: reportError });

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
    chat.reset();
    setSurface("today");
    setIsAxOpen(false);
    setMessage("");
    setContextOptions([]);
    setSelectedContextKey("");
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

  // Current-screen context references: typed resource pointers the server re-validates; the browser never sends content.
  useEffect(() => {
    if (!isAxOpen) return;
    let cancelled = false;
    const loadContextOptions = async () => {
      const wantsTasks = surface === "today" || surface === "work" || surface === "calendar" || surface === "report";
      const wantsRequests = (surface === "today" || surface === "work") && (capabilities?.includes("work_request.decide") ?? false);
      const [tasks, requests] = await Promise.all([
        wantsTasks ? getMyWork().catch(() => []) : Promise.resolve([]),
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

  async function askAx(text: string) {
    setIsAxOpen(true);
    const conversation = await chat.start();
    if (conversation) await chat.send(conversation.conversation_id, text, []);
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
    if (!message.trim() || !chat.activeConversation) return;
    const selectedContext = contextOptions.find((item) => contextKey(item) === selectedContextKey);
    const body = message;
    setMessage("");
    const accepted = await chat.send(chat.activeConversation.conversation_id, body, selectedContext ? [stripLabel(selectedContext)] : []);
    if (!accepted) setMessage((current) => current || body);
  }

  async function decideConversationAction(actionId: string, expectedVersion: number, decision: "approve" | "reject") {
    try {
      await chat.decide(actionId, expectedVersion, decision);
      setToast(decision === "approve" ? "제안을 승인해 반영했습니다." : "제안을 거절했습니다.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "AX 확인 항목을 처리하지 못했습니다.");
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
    canAssignTasks: has("task.assign"),
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
        <ChatDrawer
          activeConversation={chat.activeConversation}
          canDecideActions={canDecideActions}
          conversations={chat.conversations}
          isProcessing={chat.isProcessing}
          listStatus={chat.listStatus}
          localFragments={chat.localFragments}
          message={message}
          onCancel={() => void chat.cancelActive()}
          onClearContext={() => setSelectedContextKey("")}
          onClose={() => setIsAxOpen(false)}
          onDecide={decideConversationAction}
          onDiscardFragment={chat.discardFragment}
          onMessageChange={setMessage}
          onRetryFragment={(fragment) => void chat.retryFragment(fragment)}
          onRetryList={() => void chat.refreshConversations().catch(() => setError("AX 대화를 불러오지 못했습니다."))}
          onSelect={chat.select}
          onSend={() => void sendMessage()}
          onStart={() => void chat.start()}
          personaName={currentPersonaName}
          selectedContext={selectedContext}
          surfaceLabel={surfaceLabel[surface]}
        />
      )}
    </main>
  );
}

function stripLabel(reference: LabeledContextReference): ConversationContextReference {
  const { label: _label, pinned: _pinned, ...rest } = reference;
  return rest;
}
