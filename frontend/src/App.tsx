import { useCallback, useEffect, useRef, useState } from "react";
import type React from "react";

import { Button } from "./ds/Button";
import { BrowserOperationScope, hasPendingBrowserOperation } from "./lib/browserOperationGuard";
import { getMemberDirectory, getMyWork, getSession, getWorkRequests, logout, setAssistantCharacterPreference } from "./lib/api";
import { AppBody, AppHeader, AppShell } from "./shell/AppShell";
import { AssistantLauncher } from "./features/assistant/AssistantCharacter";
import { AssistantCharacterPicker } from "./features/assistant/AssistantCharacterPicker";
import {
  advanceAssistantCompletionObservation,
  deriveAssistantPresentationState,
  initialAssistantCompletionObservation,
} from "./features/assistant/assistantPresentation";
import { CalendarPage } from "./features/calendar/CalendarPage";
import { BrowserInteractionPage } from "./features/browser/BrowserInteractionPage";
import { ChatDrawer, contextKey, type LabeledContextReference } from "./features/chat/ChatDrawer";
import { NEW_DRAFT_KEY, useConversations } from "./features/chat/useConversations";
import { DailyReportPage } from "./features/report/DailyReportPage";
import { personName } from "./lib/labels";
import { LoginPage } from "./features/auth/LoginPage";
import { MeetingWorkspace } from "./features/meetings/MeetingWorkspace";
import { Toast } from "./ds/Modal";
import { MyWorkPage } from "./features/work/MyWorkPage";
import { OrgPage } from "./features/org/OrgPage";
import { ProjectPage } from "./features/project/ProjectPage";
import { RelationGraphPage } from "./features/graph/RelationGraphPage";
import { SideNav } from "./shell/SideNav";
import { TodayPage } from "./features/today/TodayPage";
import type { ConversationContextReference, DirectTask, OrganizationProfile, Persona, ProductSurface } from "./lib/viewModels";
import { type IconName } from "./ds/icons/Icon";

/* 목록 정본은 우리 `ProductSurface` 8종이다 (바퀴 2 D-F). 새 DS 시안의 `nav.js` 는 예시일 뿐이라
   거기 있는 「수신함·진행 현황·자료」는 만들지 않는다 — 갈 화면이 없다.
   순서·아이콘·그룹 나눔은 시안을 따르고, 글리프 이름도 DS 이름이다. */
const navigation: ReadonlyArray<{ id: ProductSurface; label: string; icon: IconName }> = [
  { id: "today", label: "오늘", icon: "home" },
  { id: "calendar", label: "캘린더", icon: "calendar" },
  { id: "meetings", label: "회의", icon: "persons" },
  { id: "work", label: "내 업무", icon: "square-check" },
  { id: "report", label: "보고", icon: "document" },
  { id: "project", label: "프로젝트", icon: "business-bag" },
  { id: "org", label: "조직", icon: "company" },
  { id: "graph", label: "관계 탐색", icon: "link" },
];

const surfaceLabel: Record<ProductSurface, string> = {
  today: "오늘",
  calendar: "캘린더",
  meetings: "회의",
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
  /* 바퀴 6a M-1: 회의는 이제 «한 화면 4칸» 이다. 어느 회의를 보고 있는지는 그 화면의 선택 상태라
     여기서 들지 않는다. 밖(채팅·관계 그래프)에서 회의를 열어 주는 길만 남긴다 — focusTaskId 와 같은 꼴이다.
     이탈 가드도 그 화면으로 옮겨 갔다(M-2) — 이제 «선택을 바꿀 때» 묻는다. */
  const [focusMeetingId, setFocusMeetingId] = useState<string | null>(null);
  const capabilities = session?.capabilities ?? null;
  const organizationNames = session?.organizations.map((organization) => organization.name) ?? [];
  const [surface, changeSurface] = useState<ProductSurface>("today");
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  /* main(#10): 파일 업로드·녹음이 도는 중에는 화면을 못 옮긴다.
     ★ 바퀴 12: 이 둘은 **`useCallback` 이어야 한다.** 화면이 자기 머리 액션을 셸에 등록하는 자리
     (바퀴 5a 가 만든 seam)가 `onNavigate` 를 의존성에 두기 때문에, 매 렌더 새 함수가 되면
     등록 → App 상태 변경 → 렌더 → 다시 등록 으로 무한 루프가 돈다. main 쪽에는 그 seam 이
     없어서 평범한 함수였고, 합치는 순간 루프가 됐다 (App.test 가 멎는 것으로 드러났다). */
  const canNavigate = useCallback((scope?: "workspace" | "chat") => {
    if (!hasPendingBrowserOperation(scope)) return true;
    setToast("파일 업로드나 녹음이 끝난 뒤 이동할 수 있습니다.");
    return false;
  }, []);
  const setSurface = useCallback(
    (next: ProductSurface) => {
      if (canNavigate("workspace")) changeSurface(next);
    },
    [canNavigate],
  );
  // 새 셸의 내비는 덮개가 아니라 180 ↔ 65px 접힘이다 (바퀴 2).
  const [navCollapsed, setNavCollapsed] = useState(false);
  const [isAxOpen, setIsAxOpen] = useState(false);
  const [isCharacterPickerOpen, setIsCharacterPickerOpen] = useState(false);
  const [characterPreferenceBusy, setCharacterPreferenceBusy] = useState(false);
  const [characterPreferenceError, setCharacterPreferenceError] = useState<string | null>(null);
  const [contextOptions, setContextOptions] = useState<LabeledContextReference[]>([]);
  const [selectedContextKey, setSelectedContextKey] = useState("");
  // Settlement seam: the visible surface registers its own reload here, so an approved AX effect can re-read every
  // affected projection in place and the shell can await the result. No page remount, so filters and views survive.
  /* 바퀴 5a J-2: 머리가 두 줄(전역 .canvas-topbar + 페이지 .page-head)이던 것을 AppHeader 한 줄로 합쳤다.
     그래서 «페이지의» 액션이 «셸의» 머리에 서야 한다 — 화면이 자기 액션을 여기 등록하고, 떠날 때 지운다.
     surfaceRefresh 와 같은 결의 seam 이다(화면을 리마운트하지 않고 셸이 값을 집어 간다). */
  const [surfaceActions, setSurfaceActions] = useState<React.ReactNode>(null);
  /* 바퀴 5b: 셸이 세 칸이라 «화면의» 레일이 «셸의» AppBody 슬롯에 서야 한다. 머리 액션과 같은 seam 이다.
     레일을 안 넘기면 그 칸이 아예 렌더되지 않는 것이 셸 규약이라, 레일 없는 화면은 예전 그대로 본문만 남는다. */
  const [surfaceRails, setSurfaceRails] = useState<{ left?: React.ReactNode; right?: React.ReactNode }>({});
  const registerSurfaceRails = useCallback((rails: { left?: React.ReactNode; right?: React.ReactNode }) => setSurfaceRails(rails), []);
  /* 지우는 것은 «화면이 떠날 때» 그 화면이 한다(등록 effect 의 cleanup). 여기서 surface 를 보고
     지우면 안 된다 — 자식 effect 가 부모보다 먼저 도므로, 새 화면이 방금 등록한 것을 부모가 덮어 지운다. */
  const registerSurfaceActions = useCallback((node: React.ReactNode) => setSurfaceActions(node), []);
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
    setFocusMeetingId(null);
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
    <AppShell
      nav={
        <SideNav
          activeId={surface}
          collapsed={navCollapsed}
          items={visibleNavigation.map((item) => ({ id: item.id, label: item.label, icon: item.icon }))}
          label="제품 탐색"
          logo="SCAX"
          onCollapse={() => setNavCollapsed((collapsed) => !collapsed)}
          onSelect={(id) => setSurface(id as ProductSurface)}
          onUserClick={() => {
            setCharacterPreferenceError(null);
            setIsCharacterPickerOpen(true);
          }}
          user={{
            name: personName(currentPersonaName),
            // 소속이 여럿이면 한 줄에 다 담기지 않는다. 잘라서 보여 주고 전체는 hover 로 읽는다.
            role: organizationNames.length > 0 ? organizationNames.join(" · ") : "소속 없음",
          }}
          userActionLabel="내 AX 캐릭터"
          /* 계정 행동은 머리줄이 아니라 기둥 바닥에 선다 — 머리줄은 화면 자기 행동만 갖는다 */
          footerActions={
            <>
              <Button
                variant="text"
                size="sm"
                onClick={() => {
                  setCharacterPreferenceError(null);
                  setIsCharacterPickerOpen(true);
                }}
                type="button"
              >
                설정
              </Button>
              <Button variant="text" size="sm" onClick={() => void endSession()} type="button">
                로그아웃
              </Button>
            </>
          }
        />
      }
    >
      <AppHeader
        actions={surfaceActions}
        /* 바퀴 6a M-3: 브레드크럼을 지웠다. 회의 상세에서 목록으로 돌아가는 유일한 길이라 바퀴 2 가
           살려 뒀던 것인데, 이제 목록 칸이 상시 옆에 서서 돌아갈 길이 UI 에 들어 있다. 시안도 머리는 한 줄이다. */
        title={surfaceLabel[surface]}
      />
      <AppBody railLeft={surfaceRails.left} railRight={surfaceRails.right}>
        {/* 오류·재조회 띠는 스크롤 밖에 선다 — 본문을 아무리 내려도 접히지 않는다 */}
        <div className="scax-page-notices">
          {error && (
            <div className="error-banner" role="alert">
              {error}
              <Button variant="text" size="sm" onClick={() => setError(null)} type="button">
                닫기
              </Button>
            </div>
          )}
          {staleProjection && (
            <div className="error-banner stale" role="status">
              {staleProjection}
              <Button size="sm" onClick={() => void refreshProjections()} type="button">
                다시 불러오기
              </Button>
            </div>
          )}
        </div>
        {/* 셸이 overflow:hidden 이라 본문이 자기 스크롤 기둥을 갖는다 (바퀴 2 D-B).
           회의는 «한 화면에 갇히는» 화면이라 스크롤은 안쪽 패널이 갖는다 — 여기서는 잡지 않는다. */}
        <div className={surface === "meetings" ? "scax-page-scroll scax-page-scroll--fixed" : "scax-page-scroll"}>
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
          {surface === "meetings" && (
            <MeetingWorkspace
              canCreateWorkRequests={has("work_request.create")}
              focusMeetingId={focusMeetingId}
              onError={setError}
              onFocusHandled={() => setFocusMeetingId(null)}
              onNotice={setToast}
              onRegisterHeaderActions={registerSurfaceActions}
              onRegisterRails={registerSurfaceRails}
              onRegisterRefresh={registerSurfaceRefresh}
              onSessionLost={() => {
                // 스트림이 인증으로 닫혔다 — 쿠키가 죽었으므로 로그인 화면으로 돌려보낸다.
                resetWorkspace();
                setSession(null);
              }}
              ownerName={currentPersonaName}
            />
          )}
          {surface === "work" && (
            <MyWorkPage
              {...pageProps}
              {...sharedWorkProps}
              canGenerateDailyReport={has("daily_report.generate")}
              onNavigate={setSurface}
              onRegisterHeaderActions={registerSurfaceActions}
              onRegisterRails={registerSurfaceRails}
              focusTaskId={focusTaskId}
              focusWorkRequestId={focusWorkRequestId}
              onFocusHandled={() => setFocusTaskId(null)}
              onRequestFocusHandled={() => setFocusWorkRequestId(null)}
            />
          )}
          {surface === "report" && (
            <DailyReportPage {...pageProps} onRegisterHeaderActions={registerSurfaceActions} personaName={currentPersonaName} />
          )}
          {surface === "project" && <ProjectPage {...pageProps} />}
          {surface === "org" && <OrgPage {...pageProps} />}
          {surface === "graph" && (
            <RelationGraphPage
              onError={setError}
              onOpenNode={(node) => {
                // Each kind opens where it lives; the surface reads it again with this person's access.
                if (node.kind === "meeting") {
                  setFocusMeetingId(node.id);
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
        </div>
      </AppBody>

      {/* 런처는 position:fixed 라 스크롤 기둥 밖에 선다 — 본문을 내려도 자리를 지킨다 */}
      {!isAxOpen && (
        <AssistantLauncher
          characterKey={session.assistant_character?.character_key}
          onOpen={() => setIsAxOpen(true)}
          onPrefill={(prompt) => chat.setDraft(prompt, chat.activeConversation?.conversation_id ?? NEW_DRAFT_KEY)}
          state={assistantState}
        />
      )}

      {toast && <Toast
          closeLabel="알림 지우기" message={toast} onClose={() => setToast(null)} />}

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
              setFocusMeetingId(resource.resource_id);
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
            setFocusMeetingId(meetingId);
          }}
          onRetryList={() => void chat.refreshConversations().catch(() => setError("AX 대화를 불러오지 못했습니다."))}
          onRetryTurn={(turnId) => void chat.retryTurn(turnId)}
          onLoadOlderMessages={() => void chat.loadOlderMessages()}
          loadingOlderMessages={chat.loadingOlderMessages}
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
    </AppShell>
  );
}

function stripLabel(reference: LabeledContextReference): ConversationContextReference {
  const { label: _label, pinned: _pinned, ...rest } = reference;
  return rest;
}
