import { useCallback, useEffect, useRef, useState } from "react";

import { getMemberDirectory, getMyWork, getSession, getWorkRequests, logout, setAssistantCharacterPreference } from "./api";
import { BrowserOperationScope, hasPendingBrowserOperation } from './browserOperationGuard';
import { AssistantLauncher } from "./AssistantCharacter";
import { AssistantCharacterPicker } from "./AssistantCharacterPicker";
import {
  advanceAssistantCompletionObservation,
  deriveAssistantPresentationState,
  initialAssistantCompletionObservation,
} from "./assistantPresentation";
import { CalendarPage } from "./CalendarPage";
import { BrowserInteractionPage } from './BrowserInteractionPage';
import { ChatDrawer, contextKey, type LabeledContextReference } from "./chat/ChatDrawer";
import { NEW_DRAFT_KEY, useConversations } from "./chat/useConversations";
import { DailyReportPage } from "./DailyReportPage";
import { personName } from "./labels";
import { LoginPage } from "./LoginPage";
import { MeetingDetailPage } from "./meetings/MeetingDetailPage";
import { MeetingListPage } from "./meetings/MeetingListPage";
import { Toast } from "./Modal";
import { MyWorkPage } from "./MyWorkPage";
import { OrgPage } from "./OrgPage";
import { ProjectPage } from "./ProjectPage";
import { RelationGraphPage } from "./RelationGraphPage";
import { TodayPage } from "./TodayPage";
import type { ConversationContextReference, DirectTask, OrganizationProfile, Persona, ProductSurface } from "./viewModels";
import { Icon } from "./Icon";

const navigation: ReadonlyArray<{ id: ProductSurface; label: string }> = [
  { id: "today", label: "오늘" },
  { id: "calendar", label: "캘린더" },
  { id: "meetings", label: "회의 목록" },
  { id: "work", label: "내 업무" },
  { id: "report", label: "보고" },
  { id: "project", label: "프로젝트" },
  { id: "org", label: "조직" },
  { id: "graph", label: "관계 탐색" },
];

const surfaceLabel: Record<ProductSurface, string> = {
  today: "오늘",
  calendar: "캘린더",
  meetings: "회의 목록",
  work: "내 업무",
  report: "보고",
  project: "프로젝트",
  org: "조직",
  graph: "관계 탐색",
};

export default function App() {
  const [browserInteractionId, setBrowserInteractionId] = useState(() => new URLSearchParams(window.location.search).get('interaction'));
  const [session, setSession] = useState<OrganizationProfile | null | undefined>(undefined);
  const [focusTaskId, setFocusTaskId] = useState<string | null>(null);
  const [focusWorkRequestId, setFocusWorkRequestId] = useState<string | null>(null);
  const personaId = session?.member_id ?? "";
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [graphFocus, setGraphFocus] = useState<string | null>(null);
  // 회의는 「회의 목록」 아래 전체 화면 둘이다 — 열린 회의가 있으면 상세, 없으면 목록.
  const [openMeetingId, setOpenMeetingId] = useState<string | null>(null);
  const [openMeetingTitle, setOpenMeetingTitle] = useState("");
  // 고치던 것이 있는 채로 브레드크럼을 누르면 상세가 한 번 묻는다 (SCR-106-T11).
  const meetingLeaveGuard = useRef<((proceed: () => void) => void) | null>(null);
  const registerMeetingLeaveGuard = useCallback((guard: ((proceed: () => void) => void) | null) => {
    meetingLeaveGuard.current = guard;
  }, []);
  const closeMeeting = useCallback(() => {
    const proceed = () => {
      setOpenMeetingId(null);
      setOpenMeetingTitle("");
    };
    if (meetingLeaveGuard.current) meetingLeaveGuard.current(proceed);
    else proceed();
  }, []);
  const capabilities = session?.capabilities ?? null;
  const organizationNames = session?.organizations.map((organization) => organization.name) ?? [];
  const [surface, changeSurface] = useState<ProductSurface>("today");
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  function canNavigate(scope?: 'workspace' | 'chat') {
    if (!hasPendingBrowserOperation(scope)) return true;
    setToast('파일 업로드나 녹음이 끝난 뒤 이동할 수 있습니다.');
    return false;
  }
  const setSurface = (next: ProductSurface) => { if (canNavigate('workspace')) changeSurface(next); };
  // 1280 단에서만 쓰이는 사이드바 덮개. 그 위 폭에서는 CSS 가 사이드바를 늘 보이게 해서 값이 무시된다.
  const [railOpen, setRailOpen] = useState(false);
  const [isAxOpen, setIsAxOpen] = useState(false);
  const [isCharacterPickerOpen, setIsCharacterPickerOpen] = useState(false);
  const [characterPreferenceBusy, setCharacterPreferenceBusy] = useState(false);
  const [characterPreferenceError, setCharacterPreferenceError] = useState<string | null>(null);
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
  const [assistantObservation, setAssistantObservation] = useState(initialAssistantCompletionObservation);

  useEffect(() => {
    setAssistantObservation((current) => advanceAssistantCompletionObservation(current, chat.conversations, isAxOpen));
  }, [chat.conversations, isAxOpen]);

  useEffect(() => setAssistantObservation(initialAssistantCompletionObservation), [personaId]);

  const assistantState = deriveAssistantPresentationState({
    conversations: chat.conversations,
    answerReady: !isAxOpen && assistantObservation.answerReadyTurnId !== null,
    hovered: false,
  });

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
    setOpenMeetingId(null);
    setOpenMeetingTitle("");
    setIsAxOpen(false);
    setContextOptions([]);
    setSelectedContextKey("");
    setError(null);
  }

  async function endSession() {
    if (!canNavigate()) return;
    try {
      await logout();
    } catch {
      // The cookie is cleared server-side on a best-effort basis; the client forgets the session regardless.
    }
    resetWorkspace();
    setSession(null);
  }

  async function chooseAssistantCharacter(characterKey: string) {
    if (!session || characterPreferenceBusy) return;
    const previous = session.assistant_character ?? { character_key: "cream-cat", version: 0 };
    setCharacterPreferenceError(null);
    setCharacterPreferenceBusy(true);
    setSession({ ...session, assistant_character: { ...previous, character_key: characterKey } });
    try {
      const saved = await setAssistantCharacterPreference(characterKey, previous.version);
      setSession((current) => current?.member_id === session.member_id
        ? { ...current, assistant_character: saved }
        : current);
    } catch (reason) {
      setSession((current) => current?.member_id === session.member_id
        ? { ...current, assistant_character: previous }
        : current);
      setCharacterPreferenceError(reason instanceof Error ? reason.message : "AX 캐릭터 설정을 저장하지 못했습니다.");
    } finally {
      setCharacterPreferenceBusy(false);
    }
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
    await chat.start();
    await chat.sendCurrent(text, []);
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

  async function sendMessage(bodyOverride?: string) {
    const body = bodyOverride ?? chat.draft;
    if (!body.trim()) return;
    const selectedContext = contextOptions.find((item) => contextKey(item) === selectedContextKey);
    // The optimistic fragment carries the text from here; a rejected send keeps its own retry/discard controls.
    await chat.sendCurrent(body, selectedContext ? [stripLabel(selectedContext)] : []);
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

  async function decideConversationAction(
    actionId: string,
    expectedVersion: number,
    decision: string,
    payload: { base_submission_version?: number; draft?: Record<string, unknown> } = {},
  ) {
    try {
      await chat.decide(actionId, expectedVersion, decision, payload);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "AX 확인 항목을 처리하지 못했습니다.");
      throw reason;
    }
    // The decision is persisted; claim it is reflected on screen only when every affected projection settled.
    const reflected = await refreshProjections();
    setToast(
      decision === "cancel_assignment"
        ? "업무 요청을 취소했습니다."
        : decision === "approve" || decision === "confirm"
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

  if (browserInteractionId) return <BrowserInteractionPage key={browserInteractionId} interactionId={browserInteractionId} onClose={() => {
    const url = new URL(window.location.href);
    url.searchParams.delete('interaction');
    window.history.replaceState(null, '', url);
    setBrowserInteractionId(null);
  }} />;

  return (
    <main className="thesc-shell">
      {/* 1280 단에서는 사이드바가 화면을 덮으므로, 그 뒤를 덮는 스크림도 함께 온다 (v2 15) */}
      {railOpen && <div aria-hidden className="rail-scrim" onMouseDown={() => setRailOpen(false)} />}
      <aside className={railOpen ? "rail open" : "rail"}>
        <div className="wordmark">
          <span aria-hidden className="wordmark-mark">
            SC
          </span>
          SCAX
        </div>
        <button
          aria-label="내 AX 캐릭터"
          className="profile"
          onClick={() => {
            setCharacterPreferenceError(null);
            setIsCharacterPickerOpen(true);
          }}
          type="button"
        >
          <span className="avatar md">{personName(currentPersonaName).slice(0, 1)}</span>
          <div>
            <b>{personName(currentPersonaName)}</b>
            {/* 소속이 여럿이면 한 줄에 다 담기지 않는다. 잘라서 보여 주고 전체는 hover로 읽는다. */}
            <small title={organizationNames.join(" · ")}>
              {organizationNames.length > 0 ? organizationNames.join(" · ") : "소속 없음"}
            </small>
          </div>
        </button>
        <div className="profile-menu">
          <button
            className="btn h30 ghost"
            onClick={() => {
              setCharacterPreferenceError(null);
              setIsCharacterPickerOpen(true);
            }}
            type="button"
          >
            설정
          </button>
          <button className="btn h30 ghost" onClick={() => void endSession()} type="button">
            로그아웃
          </button>
        </div>
        <nav aria-label="제품 탐색">
          {visibleNavigation.map((item) => (
            <button
              className={surface === item.id ? "active" : ""}
              key={item.id}
              onClick={() => {
                setSurface(item.id);
                setRailOpen(false);
              }}
              type="button"
            >
              {item.label}
            </button>
          ))}
        </nav>
        <p className="rail-foot">SCAX · 업무 운영 시스템</p>
      </aside>

      <section className={surface === "meetings" ? "canvas full-height" : "canvas"}>
        <header className="canvas-topbar">
          <button
            aria-expanded={railOpen}
            aria-label="탐색 열기"
            className="rail-toggle"
            onClick={() => setRailOpen((open) => !open)}
            type="button"
          >
            <Icon name="list" />
          </button>
          <nav aria-label="현재 위치" className="breadcrumb">
            {surface === "today" ? (
              <b>홈</b>
            ) : (
              <>
                <button className="btn link" onClick={() => setSurface("today")} type="button">
                  홈
                </button>
                <span aria-hidden>›</span>
                {surface === "meetings" && openMeetingId ? (
                  <>
                    <button className="btn link" onClick={closeMeeting} type="button">
                      {surfaceLabel[surface]}
                    </button>
                    <span aria-hidden>›</span>
                    <b>{openMeetingTitle}</b>
                  </>
                ) : (
                  <b>{surfaceLabel[surface]}</b>
                )}
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
        {surface === "calendar" && <CalendarPage {...pageProps} {...sharedWorkProps} />}
        {surface === "meetings" &&
          (openMeetingId ? (
            <MeetingDetailPage
              meetingId={openMeetingId}
              onBack={closeMeeting}
              onError={setError}
              onNotice={setToast}
              canCreateWorkRequests={has("work_request.create")}
              onOpenMeeting={setOpenMeetingId}
              onSessionLost={() => {
                // 스트림이 인증으로 닫혔다 — 쿠키가 죽었으므로 로그인 화면으로 돌려보낸다.
                resetWorkspace();
                setSession(null);
              }}
              onRegisterLeaveGuard={registerMeetingLeaveGuard}
              onRegisterRefresh={registerSurfaceRefresh}
              onTitleChange={setOpenMeetingTitle}
              ownerName={currentPersonaName}
            />
          ) : (
            <MeetingListPage
              onError={setError}
              onNotice={setToast}
              onOpenMeeting={setOpenMeetingId}
              onRegisterRefresh={registerSurfaceRefresh}
            />
          ))}
        {surface === "work" && (
          <MyWorkPage
            {...pageProps}
            {...sharedWorkProps}
            focusTaskId={focusTaskId}
            focusWorkRequestId={focusWorkRequestId}
            onFocusHandled={() => setFocusTaskId(null)}
            onRequestFocusHandled={() => setFocusWorkRequestId(null)}
          />
        )}
        {surface === "report" && <DailyReportPage {...pageProps} personaName={currentPersonaName} />}
        {surface === "project" && <ProjectPage {...pageProps} />}
        {surface === "org" && <OrgPage {...pageProps} />}
        {surface === "graph" && (
          <RelationGraphPage
            onError={setError}
            onOpenNode={(node) => {
              // Each kind opens where it lives; the surface reads it again with this person's access.
              if (node.kind === "meeting") {
                setOpenMeetingId(node.id);
                setSurface("meetings");
                return;
              }
              if (node.kind === "person" || node.kind === "team") {
                setSurface("org");
                return;
              }
              if (node.kind === "report") {
                setSurface("report");
                return;
              }
              if (node.kind === "work_request" || node.kind === "material") setSurface("work");
            }}
            onOpenTask={(taskId) => {
              setSurface("work");
              setFocusTaskId(taskId);
            }}
          />
        )}

        {!isAxOpen && (
          <AssistantLauncher
            characterKey={session.assistant_character?.character_key}
            onOpen={() => setIsAxOpen(true)}
            onPrefill={(prompt) => chat.setDraft(prompt, chat.activeConversation?.conversation_id ?? NEW_DRAFT_KEY)}
            state={assistantState}
          />
        )}
      </section>

      {toast && <Toast message={toast} onClose={() => setToast(null)} />}

      {isCharacterPickerOpen && (
        <AssistantCharacterPicker
          busy={characterPreferenceBusy}
          currentKey={session.assistant_character?.character_key ?? "cream-cat"}
          error={characterPreferenceError}
          onClose={() => {
            setCharacterPreferenceError(null);
            setIsCharacterPickerOpen(false);
          }}
          onSelect={(characterKey) => void chooseAssistantCharacter(characterKey)}
        />
      )}

      {isAxOpen && (
        <BrowserOperationScope.Provider value="chat">
        <ChatDrawer
          activeConversation={chat.activeConversation}
          assistantState={assistantState}
          characterKey={session.assistant_character?.character_key}
          conversations={chat.conversations}
          isProcessing={chat.isProcessing}
          listStatus={chat.listStatus}
          localFragments={chat.localFragments}
          message={chat.draft}
          onCancel={() => void chat.cancelActive()}
          onClearContext={() => setSelectedContextKey("")}
          onClose={() => { if (canNavigate('chat')) setIsAxOpen(false); }}
          onDecide={decideConversationAction}
          onDiscardFragment={chat.discardFragment}
          onFollowUpCandidate={(candidate) => {
            const conversationId = chat.activeConversation?.conversation_id;
            return conversationId
              ? chat.send(conversationId, candidate.user_text, [], undefined, candidate.candidate_id)
              : Promise.resolve(false);
          }}
          onMessageChange={(value) => chat.setDraft(value)}
          onRetryFragment={(fragment) => void chat.retryFragment(fragment)}
          onOpenResource={(resource) => {
            if (!canNavigate()) return;
            // Each item opens where it lives. The surface reads it again with this person's access.
            setIsAxOpen(false);
            if (resource.resource_type === "task") {
              setFocusWorkRequestId(null);
              setSurface("work");
              setFocusTaskId(resource.resource_id);
              return;
            }
            if (resource.resource_type === "work_request") {
              setFocusTaskId(null);
              setSurface("work");
              setFocusWorkRequestId(resource.resource_id);
              return;
            }
            if (resource.resource_type === "meeting") {
              setOpenMeetingId(resource.resource_id);
              setSurface("meetings");
              return;
            }
            if (resource.resource_type === "material") {
              const taskContext = resource.source_contexts?.find((context) => context.resource_type === "task");
              if (taskContext) {
                setSurface("work");
                setFocusTaskId(taskContext.resource_id);
                return;
              }
            }
            if (resource.resource_type === "material" && resource.origin) {
              window.open(resource.origin, "_blank", "noopener,noreferrer");
              return;
            }
            if (resource.resource_type === "report") setSurface("report");
          }}
          onOpenTask={(taskId) => {
            if (!canNavigate()) return;
            setIsAxOpen(false);
            setSurface("work");
            setFocusTaskId(taskId);
          }}
          /* 채팅이 「회의 열기」를 누르면 회의 상세로 간다 (WP-006) — main 이 쓰던 달력 + MeetingDrawer 자리다 */
          onOpenMeeting={(meetingId) => {
            if (!canNavigate()) return;
            setIsAxOpen(false);
            setSurface("meetings");
            setOpenMeetingId(meetingId);
          }}
          onRetryList={() => void chat.refreshConversations().catch(() => setError("AX 대화를 불러오지 못했습니다."))}
          onRetryTurn={(turnId) => void chat.retryTurn(turnId)}
          onSelect={(conversation) => { if (canNavigate('chat')) chat.select(conversation); }}
          onSend={(body) => void sendMessage(body)}
          onStart={() => { if (canNavigate('chat')) void chat.start(); }}
          personaId={personaId}
          personaName={currentPersonaName}
          selectedContext={selectedContext}
          surfaceLabel={surfaceLabel[surface]}
        />
        </BrowserOperationScope.Provider>
      )}
    </main>
  );
}

function stripLabel(reference: LabeledContextReference): ConversationContextReference {
  const { label: _label, pinned: _pinned, ...rest } = reference;
  return rest;
}
