import { useCallback, useEffect, useRef, useState } from "react";

import { getMemberDirectory, getMyWork, getSession, getWorkRequests, logout } from "./api";
import { CalendarPage } from "./CalendarPage";
import { ChatDrawer, contextKey, type LabeledContextReference } from "./chat/ChatDrawer";
import { NEW_DRAFT_KEY, useConversations } from "./chat/useConversations";
import { DailyReportPage } from "./DailyReportPage";
import { personName } from "./labels";
import { LoginPage } from "./LoginPage";
import { Toast } from "./Modal";
import { MyWorkPage } from "./MyWorkPage";
import { OrgPage } from "./OrgPage";
import { RelationGraphPage } from "./RelationGraphPage";
import { TodayPage } from "./TodayPage";
import type { ConversationContextReference, DirectTask, OrganizationProfile, Persona, ProductSurface } from "./viewModels";

const navigation: ReadonlyArray<{ id: ProductSurface; label: string }> = [
  { id: "today", label: "오늘" },
  { id: "calendar", label: "캘린더" },
  { id: "work", label: "내 업무" },
  { id: "report", label: "보고" },
  { id: "org", label: "조직" },
  { id: "graph", label: "관계 탐색" },
];

const surfaceLabel: Record<ProductSurface, string> = {
  today: "오늘",
  calendar: "캘린더",
  work: "내 업무",
  report: "보고",
  org: "조직",
  graph: "관계 탐색",
};

export default function App() {
  const [session, setSession] = useState<OrganizationProfile | null | undefined>(undefined);
  const [focusTaskId, setFocusTaskId] = useState<string | null>(null);
  const personaId = session?.member_id ?? "";
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [graphFocus, setGraphFocus] = useState<string | null>(null);
  const [focusMeetingId, setFocusMeetingId] = useState<string | null>(null);
  const capabilities = session?.capabilities ?? null;
  const organizationNames = session?.organizations.map((organization) => organization.name) ?? [];
  const [surface, setSurface] = useState<ProductSurface>("today");
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [isAxOpen, setIsAxOpen] = useState(false);
  const [contextOptions, setContextOptions] = useState<LabeledContextReference[]>([]);
  const [selectedContextKey, setSelectedContextKey] = useState("");
  // Settlement seam: the visible surface registers its own reload here, so an approved AX effect can re-read every
  // affected projection in place and the shell can await the result. No page remount, so filters and views survive.
  const surfaceRefresh = useRef<(() => Promise<void>) | null>(null);
  const registerSurfaceRefresh = useCallback((refresh: (() => Promise<void>) | null) => {
    surfaceRefresh.current = refresh;
  }, []);
  const [staleProjection, setStaleProjection] = useState<string | null>(null);
  const contextGeneration = useRef(0);
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
    void getMemberDirectory()
      .then((members) => {
        if (!cancelled) setPersonas(members);
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
  // Failures propagate to the caller: a post-approval refresh must not quietly present an empty candidate list.
  const loadContextOptions = useCallback(async () => {
    const generation = ++contextGeneration.current;
    const wantsTasks = surface === "today" || surface === "work" || surface === "calendar" || surface === "report";
    const wantsRequests = (surface === "today" || surface === "work") && (capabilities?.includes("work_request.decide") ?? false);
    const [tasks, requests] = await Promise.all([
      wantsTasks ? getMyWork() : Promise.resolve([]),
      wantsRequests ? getWorkRequests() : Promise.resolve([]),
    ]);
    if (generation !== contextGeneration.current) return;
    const next: LabeledContextReference[] = [
      ...tasks.map((task) => ({ resource_type: "task" as const, resource_id: task.task_id, resource_version: task.version, included: true, label: task.title })),
      ...requests.map((request) => ({ resource_type: "work_request" as const, resource_id: request.request_id, resource_version: request.version, included: true, label: request.title })),
    ];
    setContextOptions((current) => {
      const pinned = current.filter((item) => item.pinned && !next.some((candidate) => contextKey(candidate) === contextKey(item)));
      return [...pinned, ...next];
    });
  }, [capabilities, surface]);

  useEffect(() => {
    if (!isAxOpen) return;
    let cancelled = false;
    void loadContextOptions().catch(() => {
      // A persona switch invalidates this read; its failure must not surface against the new persona.
      if (!cancelled) setError("현재 화면의 AX 참고 자료를 불러오지 못했습니다.");
    });
    return () => {
      cancelled = true;
    };
  }, [isAxOpen, loadContextOptions, personaId]);

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
    // Prefill only when that conversation's draft is empty; drafts stay per conversation.
    if (!chat.draft.trim()) chat.setDraft(`'${task.title}' 업무에 대해 알려줘.`, chat.activeConversation?.conversation_id ?? NEW_DRAFT_KEY);
    setIsAxOpen(true);
  }

  async function sendMessage() {
    if (!chat.draft.trim() || !chat.activeConversation) return;
    const selectedContext = contextOptions.find((item) => contextKey(item) === selectedContextKey);
    const conversationId = chat.activeConversation.conversation_id;
    const body = chat.draft;
    chat.setDraft("", conversationId);
    // The optimistic fragment carries the text from here; a rejected send keeps its own retry/discard controls.
    await chat.send(conversationId, body, selectedContext ? [stripLabel(selectedContext)] : []);
  }

  /**
   * Settles every projection a decided Action may have changed: the visible surface, the AX context candidates, and
   * the active conversation. Never throws, so a read failure is never mistaken for a failed decision. Returns whether
   * every read settled; callers use that to choose between reflected-on-screen and persisted-only wording, and a
   * failure raises the retryable stale-screen banner.
   */
  const refreshProjections = useCallback(async () => {
    const settled = await Promise.allSettled([
      surfaceRefresh.current ? surfaceRefresh.current() : Promise.resolve(),
      loadContextOptions(),
      chat.refreshActiveConversation(),
    ]);
    const failed = settled.some((result) => result.status === "rejected");
    setStaleProjection(failed ? "판단은 저장되었지만 화면을 갱신하지 못했습니다." : null);
    return !failed;
  }, [chat, loadContextOptions]);

  async function decideConversationAction(actionId: string, expectedVersion: number, decision: string) {
    try {
      await chat.decide(actionId, expectedVersion, decision);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "AX 확인 항목을 처리하지 못했습니다.");
      return;
    }
    // The decision is persisted; claim it is reflected on screen only when every affected projection settled.
    const reflected = await refreshProjections();
    setToast(
      decision === "approve"
        ? reflected
          ? "제안을 승인해 반영했습니다."
          : "제안을 승인했습니다."
        : "제안을 거절했습니다.",
    );
  }

  const currentPersonaName = session?.display_name ?? "사용자";
  const selectedContext = contextOptions.find((item) => contextKey(item) === selectedContextKey);
  const has = (capability: string) => capabilities?.includes(capability) ?? false;
  const canReadActions = has("action.read");
  const visibleNavigation = navigation.filter((item) => {
    if (item.id === "report") return has("daily_report.generate");
    // Following how work connects is a read like any other: everyone who may read work may follow it, and the
    // server still answers only with what that person could already reach.
    if (item.id === "graph") return has("task.read") || has("work_request.read");
    return true;
  });
  const pageProps = { personaId, onError: setError, onRegisterRefresh: registerSurfaceRefresh };
  const sharedWorkProps = {
    personaName: currentPersonaName,
    personas,
    canCreateWorkRequests: has("work_request.create"),
    canDecideWorkRequests: has("work_request.decide"),
    canManageOwnTasks: has("task.self_manage"),
    canAssignTasks: has("task.assign"),
    canReadOrganizationWork: has("work.read.all"),
    canReadActions,
    onAskAboutTask: askAboutTask,
    onNotice: setToast,
    onDecided: refreshProjections,
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
        {staleProjection && (
          <div className="error-banner stale" role="status">
            {staleProjection}
            <button className="btn h30" onClick={() => void refreshProjections()} type="button">
              다시 불러오기
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
        {surface === "calendar" && (
          <CalendarPage
            {...pageProps}
            {...sharedWorkProps}
            focusMeetingId={focusMeetingId}
            onMeetingFocusHandled={() => setFocusMeetingId(null)}
          />
        )}
        {surface === "work" && (
          <MyWorkPage
            {...pageProps}
            {...sharedWorkProps}
            focusTaskId={focusTaskId}
            onFocusHandled={() => setFocusTaskId(null)}
          />
        )}
        {surface === "report" && <DailyReportPage {...pageProps} personaName={currentPersonaName} />}
        {surface === "org" && <OrgPage {...pageProps} />}
        {surface === "graph" && (
          <RelationGraphPage
            focusNodeRef={graphFocus}
            onError={setError}
            onFocusHandled={() => setGraphFocus(null)}
            onOpenTask={(taskId) => {
              setSurface("work");
              setFocusTaskId(taskId);
            }}
          />
        )}
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
          conversations={chat.conversations}
          isProcessing={chat.isProcessing}
          listStatus={chat.listStatus}
          localFragments={chat.localFragments}
          message={chat.draft}
          onCancel={() => void chat.cancelActive()}
          onClearContext={() => setSelectedContextKey("")}
          onClose={() => setIsAxOpen(false)}
          onDecide={decideConversationAction}
          onDiscardFragment={chat.discardFragment}
          onMessageChange={(value) => chat.setDraft(value)}
          onRetryFragment={(fragment) => void chat.retryFragment(fragment)}
          onOpenResource={(resource) => {
            // Each item opens where it lives. The surface reads it again with this person's access.
            if (resource.resource_type === "task") {
              setSurface("work");
              setFocusTaskId(resource.resource_id);
              return;
            }
            if (resource.resource_type === "work_request") {
              setSurface("work");
              return;
            }
            if (resource.resource_type === "meeting") {
              setSurface("calendar");
              setFocusMeetingId(resource.resource_id);
              return;
            }
            if (resource.resource_type === "material" && resource.parent_resource_id) {
              setSurface("work");
              setFocusTaskId(resource.parent_resource_id);
              return;
            }
            if (resource.resource_type === "report") setSurface("report");
          }}
          onOpenGraph={(nodeRef) => {
            // The card is one turn's picture; the surface re-applies this person's access to whatever it draws next.
            setGraphFocus(nodeRef);
            setSurface("graph");
          }}
          onRetryList={() => void chat.refreshConversations().catch(() => setError("AX 대화를 불러오지 못했습니다."))}
          onRetryTurn={(turnId) => void chat.retryTurn(turnId)}
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
