import { useCallback, useEffect, useRef, useState } from "react";
import type React from "react";

import { Button } from "./ds/Button";
import { BrowserOperationScope, hasPendingBrowserOperation } from "./lib/browserOperationGuard";
import { getMemberDirectory, getNavBadges, getSession, isStaleActionError, logout, markNotificationRead, setAssistantCharacterPreference } from "./lib/api";
import { AppBody, AppHeader, AppShell } from "./shell/AppShell";
import { AssistantLauncher } from "./features/assistant/AssistantCharacter";
import {
  advanceAssistantCompletionObservation,
  deriveAssistantPresentationState,
  initialAssistantCompletionObservation,
} from "./features/assistant/assistantPresentation";
import { CalendarPage } from "./features/calendar/CalendarPage";
import { BrowserInteractionPage } from "./features/browser/BrowserInteractionPage";
import { ChatDrawer } from "./features/chat/ChatDrawer";
import { NEW_DRAFT_KEY, useConversations } from "./features/chat/useConversations";
import { DailyReportPage } from "./features/report/DailyReportPage";
import { InboxPage } from "./features/inbox/InboxPage";
import { NotificationsPage } from "./features/notifications/NotificationsPage";
import { SettingsPage, type SettingsTab } from "./features/settings/SettingsPage";
import { axDraftCard, inboxScreen, notificationScreen, personName, shellDownload } from "./lib/labels";
import { hasShellNotifications, notifyPermission, notifyShow, onShellDownload, onShellNotificationClick, openExternal } from "./lib/shell";
import { isViewingTarget } from "./lib/currentView";
import { createOsNotifier } from "./lib/osNotifier";
import { LoginPage } from "./features/auth/LoginPage";
import { MeetingWorkspace } from "./features/meetings/MeetingWorkspace";
import { Toast } from "./ds/Modal";
import { MyWorkPage } from "./features/work/MyWorkPage";
import { OrgPage } from "./features/org/OrgPage";
import { ProjectPage } from "./features/project/ProjectPage";
import { RelationGraphPage } from "./features/graph/RelationGraphPage";
import { SideNav } from "./shell/SideNav";
import { TodayPage } from "./features/today/TodayPage";
import type { ConversationContextReference, NavBadges, Notification, NotificationTarget, OrganizationProfile, Persona, ProductSurface, TaskOriginMessage } from "./lib/viewModels";
import { type IconName } from "./ds/icons/Icon";
import { shellNav } from "./lib/labels";
import { forgetScreenCache, scopeScreenCache } from "./lib/screenCache";
import { EventStreamProvider, useEventStream } from "./lib/eventStreamContext";

/* 표시 순서는 시안에 맞추고 기존 화면 id·권한 필터·동작은 유지한다. */
const navigation: ReadonlyArray<{ id: ProductSurface | "materials"; label: string; icon: IconName; disabled?: boolean }> = [
  { id: "today", label: "홈", icon: "home" },
  { id: "work", label: "업무", icon: "square-check" },
  { id: "calendar", label: "캘린더", icon: "calendar" },
  { id: "project", label: "프로젝트", icon: "folder" },
  // 자료함 독립 화면은 아직 없다. 기존 자료 열기 경로를 새 라우트로 대체하지 않는다.
  { id: "materials", label: "자료함", icon: "document", disabled: true },
  { id: "meetings", label: "회의", icon: "persons" },
  /* 메시지함 — 메일·슬랙·카톡이 쌓이는 독립 화면(WORK-011 FE-a · SPEC-008 §2 Placement). 「업무 > 수신함」과 다르다(D-06). */
  { id: "inbox", label: "메시지", icon: "inbox" },
  { id: "org", label: "조직", icon: "company" },
  { id: "report", label: "보고", icon: "document" },
  // 데모 기간에는 사이드 탭에서 닫는다. 화면 자체와 다른 진입 경로는 그대로 둔다.
  { id: "graph", label: "관계 탐색", icon: "link", disabled: true },
];

const surfaceLabel: Record<ProductSurface, string> = {
  today: "오늘",
  calendar: "캘린더",
  meetings: "회의",
  work: "업무",
  report: "보고",
  project: "프로젝트",
  org: "조직",
  graph: "관계 탐색",
  inbox: "메시지",
  settings: "설정",
  notifications: "알림",
};

/**
 * 첫 화면을 주소의 쿼리로 고른다 — OAuth 콜백이 `?surface=settings&tab=…&connect=ok|denied` 로 돌아온다
 * (SPEC-008 §4.2 N-2 · 라우터 없는 SPA 라 쿼리다). 읽은 뒤 주소에서 지운다 — 새로고침이 결과를 다시 알리지 않게.
 */
function readLanding(): { surface: ProductSurface; tab?: SettingsTab; connect: "ok" | "denied" | "error" | null } {
  const params = new URLSearchParams(window.location.search);
  if (params.get("surface") !== "settings") return { surface: "today", connect: null };
  const tabParam = params.get("tab");
  const tab: SettingsTab | undefined =
    tabParam === "mail" || tabParam === "slack" || tabParam === "kakao" || tabParam === "account" || tabParam === "notify" ? tabParam : undefined;
  const connectParam = params.get("connect");
  /* `error` = 서버의 토큰 교환 실패(BE-1 `complete_callback` · 검수 W-1) — 조용히 버리지 않는다 */
  const connect = connectParam === "ok" || connectParam === "denied" || connectParam === "error" ? connectParam : null;
  const url = new URL(window.location.href);
  ["surface", "tab", "connect"].forEach((key) => url.searchParams.delete(key));
  window.history.replaceState(null, "", url);
  return { surface: "settings", tab, connect };
}

/**
 * 사이드바 점을 다시 읽을 때(SPEC-011 §2.2) — SSE `ready`·`resync` · `notification.upserted`·`notification.read` ·
 * `inbox.message_arrived`·`inbox.message_updated`. 사건 채널 Provider 안에서만 들을 수 있어 작은 부품으로 둔다.
 */
function NavBadgeWatcher({ onStale }: { onStale: () => void }) {
  useEventStream((signal) => {
    if (signal.kind === "ready" || signal.kind === "resync" || signal.kind === "notification.upserted" || signal.kind === "notification.read") onStale();
    else if (signal.kind === "inbox" && (signal.event.type === "inbox.message_arrived" || signal.event.type === "inbox.message_updated")) onStale();
  });
  return null;
}

/**
 * 새 알림 → OS 알림(WORK-013 WP4 · SPEC-011 §2.5). 데스크톱 셸이 `notification` 기능을 말할 때만 돈다 — 브라우저 · 옛 dmg 는 목록 · 점만.
 * 무엇을 띄우고 무엇을 묶는지는 `lib/osNotifier.ts`(D-39) — 여기서는 사건 채널과 「지금 보는 화면」 을 이어 줄 뿐이다.
 */
function OsNotificationBridge({ surface }: { surface: ProductSurface }) {
  const surfaceRef = useRef(surface);
  surfaceRef.current = surface;
  const enabled = useRef(false);
  const [notifier] = useState(() =>
    createOsNotifier({
      show: (input) => void notifyShow(input),
      isViewing: (target) => isViewingTarget(target, surfaceRef.current),
    }),
  );
  useEffect(() => {
    let alive = true;
    void hasShellNotifications().then((able) => {
      if (alive) enabled.current = able;
    });
    return () => {
      alive = false;
      notifier.dispose();
    };
  }, [notifier]);
  useEventStream((signal) => {
    if (signal.kind === "notification.upserted" && enabled.current) notifier.offer(signal.event);
  });
  return null;
}

export default function App() {
  const [browserInteractionId, setBrowserInteractionId] = useState(() => new URLSearchParams(window.location.search).get('interaction'));
  const [session, setSession] = useState<OrganizationProfile | null | undefined>(undefined);
  const [focusTaskId, setFocusTaskId] = useState<string | null>(null);
  /** 업무 상세 「원래 메시지」 링크로 가는 중인 메시지 — 메시지함이 그 방·메일을 열고 짚은 뒤 비운다(SPEC-008 §2.9 ④). */
  const [inboxFocus, setInboxFocus] = useState<TaskOriginMessage | null>(null);
  const [focusWorkRequestId, setFocusWorkRequestId] = useState<string | null>(null);
  const personaId = session?.member_id ?? "";
  /*
   * 화면 데이터 기억의 주인 (WORK-008 Phase 2 · `lib/screenCache.ts`).
   *
   * 앱이 새로 서면 비우고 시작한다 — 남의 것도 지난 앱의 것도 비치지 않는다. 주인은 **렌더 중에** 정한다:
   * 자식 화면의 첫 렌더(`useState` 초깃값)와 진입 effect 가 이 값보다 먼저 돌기 때문이다. 사람이 바뀌거나
   * 비면(로그아웃·세션 만료) `scopeScreenCache` 가 통째로 버린다.
   */
  useState(() => {
    forgetScreenCache();
    scopeScreenCache(null);
    return null;
  });
  scopeScreenCache(personaId);
  useEffect(() => {
    scopeScreenCache(personaId);
  }, [personaId]);
  /* 언마운트 때 주인을 내리는 effect 는 두지 않는다 (fix1) — StrictMode 의 이중 effect 가 주인을 내렸다 세우며
     세대를 올리면, 이미 선 화면들의 세대가 어긋나 개발 모드에서 아무것도 기억하지 못한다. 앱이 새로 설 때
     위의 초깃값이 비우므로 지난 앱의 데이터는 남지 않는다. */
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [graphFocus, setGraphFocus] = useState<string | null>(null);
  /* 바퀴 6a M-1: 회의는 이제 «한 화면 4칸» 이다. 어느 회의를 보고 있는지는 그 화면의 선택 상태라
     여기서 들지 않는다. 밖(채팅·관계 그래프)에서 회의를 열어 주는 길만 남긴다 — focusTaskId 와 같은 꼴이다.
     이탈 가드도 그 화면으로 옮겨 갔다(M-2) — 이제 «선택을 바꿀 때» 묻는다. */
  const [focusMeetingId, setFocusMeetingId] = useState<string | null>(null);
  /** 밖(AX 답변의 프로젝트 참조)에서 열어 달라고 온 프로젝트 — focusMeetingId 와 같은 꼴이다 (WORK-008 Phase 5). */
  const [focusProjectId, setFocusProjectId] = useState<string | null>(null);
  const capabilities = session?.capabilities ?? null;
  const organizationNames = session?.organizations.map((organization) => organization.name) ?? [];
  const [landing] = useState(readLanding);
  const [surface, changeSurface] = useState<ProductSurface>(landing.surface);
  /**
   * 화면이 내는 알림 — **전부 토스트 한 자리다** (4차 발주 6).
   *
   * 지금까지 실패는 본문 안쪽의 빨간 띠(`.error-banner`)였고 성공은 토스트였다. 그래서 같은 명령의
   * 성공과 실패가 **다른 데서** 나타났고, 띠는 본문을 아래로 밀며 표의 첫 줄을 가렸다. 이제 셋
   * (실패 · 성공 · 갱신 실패)이 같은 통에 쌓인다.
   *
   * 갈래마다 **한 줄만** 산다 — 같은 갈래의 새 알림이 오면 앞엣것을 갈아치운다. 실패가 열 줄 쌓여
   * 화면을 덮는 일도, 성공이 실패를 밀어내는 일도 없다.
   */
  const [notices, setNotices] = useState<Array<{ id: number; kind: "error" | "success" | "stale"; message: string }>>([]);
  const noticeSeq = useRef(0);
  const putNotice = useCallback((kind: "error" | "success" | "stale", message: string | null) => {
    setNotices((current) => {
      const rest = current.filter((notice) => notice.kind !== kind);
      if (!message) return rest;
      noticeSeq.current += 1;
      return [...rest, { id: noticeSeq.current, kind, message }];
    });
  }, []);
  const setError = useCallback((message: string | null) => putNotice("error", message), [putNotice]);
  const setToast = useCallback((message: string | null) => putNotice("success", message), [putNotice]);
  const setStaleProjection = useCallback((message: string | null) => putNotice("stale", message), [putNotice]);
  const dismissNotice = useCallback((id: number) => setNotices((current) => current.filter((notice) => notice.id !== id)), []);
  /* 데스크톱 셸이 첨부 응답(회의 내보내기·자료·첨부)을 다운로드 폴더에 저장한 결과 — 셸은 사건만 보내고
     문구는 여기서 같은 토스트 통에 낸다(SPEC-006 U-5 5 · OQ-T12). 브라우저에서는 구독하지 않는다. */
  useEffect(
    () =>
      onShellDownload((result) => {
        if (!result.ok) putNotice("error", shellDownload.failed);
        else putNotice("success", result.filename ? shellDownload.saved(result.filename) : shellDownload.savedUnnamed);
      }),
    [putNotice],
  );
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
  /* 업무를 «여는» 길은 업무 화면의 드로어 하나뿐이다 — 밖에서 업무를 여는 화면(관계 그래프 · 프로젝트)이
     같은 seam 을 쓴다. ⚠ **인라인 화살표로 두면 안 된다**: 레일을 등록하는 화면(프로젝트)이 이것을
     의존성에 물고 있어서, 매 렌더 새 함수가 되면 등록 → App 상태 변경 → 렌더 → 다시 등록 으로
     무한히 돈다. 위 `canNavigate` 주석이 적어 둔 그 함정과 같은 자리다. */
  const openTaskInWork = useCallback(
    (taskId: string) => {
      setSurface("work");
      setFocusTaskId(taskId);
    },
    [setSurface],
  );
  // 새 셸의 내비는 덮개가 아니라 180 ↔ 65px 접힘이다 (바퀴 2).
  const [navCollapsed, setNavCollapsed] = useState(false);
  const [isAxOpen, setIsAxOpen] = useState(false);
  const [characterPreferenceBusy, setCharacterPreferenceBusy] = useState(false);
  const [characterPreferenceError, setCharacterPreferenceError] = useState<string | null>(null);
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
  /* 머리 제목을 화면이 바꿔 다는 자리 — 설정은 고른 메뉴 이름(「메일 연동」…)이 제목이다(시안). 떠날 때 지운다. */
  const [surfaceTitle, setSurfaceTitle] = useState<string | null>(null);
  const registerSurfaceTitle = useCallback((title: string | null) => setSurfaceTitle(title), []);
  /* 제목 바로 옆 자리(`AppHeader` `titleEnd`) — 알림 화면의 「안 읽음 N」 이 쓴다(WORK-013 WP3-FE). 떠날 때 지운다. */
  const [surfaceTitleEnd, setSurfaceTitleEnd] = useState<React.ReactNode>(null);
  const registerSurfaceTitleEnd = useCallback((node: React.ReactNode) => setSurfaceTitleEnd(node), []);
  const surfaceRefresh = useRef<(() => Promise<void>) | null>(null);
  const registerSurfaceRefresh = useCallback((refresh: (() => Promise<void>) | null) => {
    surfaceRefresh.current = refresh;
  }, []);
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
    // 로그아웃·다른 사람의 로그인·세션 만료 — 이전 사람이 받아 둔 화면 데이터를 남기지 않는다.
    forgetScreenCache();
    chat.reset();
    setSurface("today");
    setFocusMeetingId(null);
    setFocusProjectId(null);
    setIsAxOpen(false);
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

  /*
   * ~~현재 화면 참고 자료(`contextOptions` · `loadContextOptions`)~~ 는 **지웠다** (WORK-010 2b W2).
   * 업무를 AX 참고 자료로 붙이는 입구가 업무 상세의 「AX」 하나였고 그것이 없어졌다(SPEC-007 §2.10.1 결정 d).
   * 고를 입구가 없는데 AX 를 열거나 판단할 때마다 `getMyWork`·`getWorkRequests` 를 불러 실패 토스트·갱신 띠까지
   * 냈다 — 없는 기능 때문에 오류를 보였다. 보내는 말은 이제 참고 자료 없이 간다.
   */

  /**
   * 서랍을 열고 **새 대화로** 말풍선 하나를 보낸다 — 오늘 화면의 「AX 에게 묻기」 와 메시지함의 AX 업무 생성·AX 요약이 같은 입구다
   * (SPEC-008 §2.9 ③-1 · OQ-815 — 누를 때마다 새 대화). 메시지함은 그 메시지를 참고 자료(`inbox_message`)로 함께 싣는다.
   */
  async function askAx(text: string, context: ConversationContextReference[] = []) {
    setIsAxOpen(true);
    await chat.start();
    await chat.sendCurrent(text, context);
  }

  /** 업무 상세의 「원래 메시지」 — 메시지함으로 가서 그 메시지를 짚는다(OQ-817). */
  const openInboxMessage = useCallback(
    (message: TaskOriginMessage) => {
      if (!canNavigate("workspace")) return;
      setInboxFocus(message);
      changeSurface("inbox");
    },
    [canNavigate],
  );

  /** 설정을 그 탭으로 연다 — 알림의 「연동 끊김」 대상(SPEC-011 §4.5-2 3). 이미 설정이면 다시 세워 그 탭으로. */
  const [settingsFocus, setSettingsFocus] = useState<{ tab: SettingsTab; seq: number } | null>(null);

  /**
   * **대상을 고른 상태로 연다 — 한 함수다**(SPEC-011 §4.5-2 3 · D-31). 알림 줄(이 화면) · OS 알림 클릭(WP4) · AX 서랍의
   * `onOpenResource`(업무 · 요청 · 회의 · 프로젝트 · 보고)가 모두 이것을 부른다. 이동 가드(`canNavigate`)는 부르는 쪽이 한다.
   */
  const openFocus = useCallback(
    (target: NotificationTarget | { surface: "project"; project_id: string } | { surface: "report" }, label = "") => {
      switch (target.surface) {
        case "work":
          if (target.task_id) {
            setFocusWorkRequestId(null);
            setSurface("work");
            setFocusTaskId(target.task_id);
          } else if (target.work_request_id) {
            setFocusTaskId(null);
            setSurface("work");
            setFocusWorkRequestId(target.work_request_id);
          }
          return;
        case "meetings":
          setFocusMeetingId(target.meeting_id);
          setSurface("meetings");
          return;
        case "project":
          // 프로젝트 화면의 「보던 프로젝트」 경로로 그 프로젝트를 연다 — 화면이 이 사람의 권한으로 다시 읽는다.
          setFocusProjectId(target.project_id);
          setSurface("project");
          return;
        case "report":
          setSurface("report");
          return;
        case "inbox":
          // 메시지함은 출처 링크(SPEC-008 §2.9 ④)와 같은 길 — 그 방·메일을 열고, 메시지가 있으면 그 줄로 스크롤 · 잠깐 강조
          setInboxFocus({
            message_id: target.message_id ?? "",
            source_kind: target.source,
            room_id: target.source === "mail" ? null : target.room_id,
            label,
          });
          changeSurface("inbox");
          return;
        case "settings":
          setSettingsFocus((current) => ({ tab: target.tab, seq: (current?.seq ?? 0) + 1 }));
          setSurface("settings");
          return;
        case "notifications":
          setSurface("notifications");
          return;
      }
    },
    /* `changeSurface` 는 useState 의 setter 라 바뀌지 않지만, 안에서 부르므로 의존에 적는다(검수 W-5 — 낡은 클로저 방지) */
    [changeSurface, setSurface],
  );

  /* 사이드바 점 둘(SPEC-011 §2.2) — `GET /api/me/badges` 를 앱 시작 · 사건(아래 `NavBadgeWatcher`) · 이 화면의 읽음 API 뒤에 다시 읽는다.
     겹쳐 불리면 하나만 날리고, 그사이 또 불렸으면 끝난 뒤 한 번 더 읽는다. 못 읽으면 점을 그대로 둔다. */
  const [badges, setBadges] = useState<NavBadges>({ notifications: false, inbox: false });
  const badgeLoad = useRef({ running: false, again: false, owner: "" });
  const refreshBadges = useCallback(async () => {
    const slot = badgeLoad.current;
    if (slot.running) {
      slot.again = true;
      return;
    }
    slot.running = true;
    const owner = slot.owner;
    try {
      do {
        slot.again = false;
        try {
          const next = await getNavBadges();
          if (badgeLoad.current.owner === owner) setBadges({ notifications: Boolean(next?.notifications), inbox: Boolean(next?.inbox) });
        } catch {
          /* 점은 보조 정보다 — 못 읽으면 그대로 */
        }
      } while (slot.again && badgeLoad.current.owner === owner);
    } finally {
      slot.running = false;
    }
  }, []);
  useEffect(() => {
    badgeLoad.current.owner = personaId;
    setBadges({ notifications: false, inbox: false });
    if (personaId) void refreshBadges();
  }, [personaId, refreshBadges]);

  /* OS 알림 권한 — 로그인 뒤 첫 화면에서 한 번 묻는다(OQ-1102 제안). 이미 답했으면 OS 가 다시 묻지 않는다(거부면 그대로 · 목록·점만). */
  useEffect(() => {
    if (personaId) void notifyPermission(true);
  }, [personaId]);

  /**
   * 알림 하나를 연다(SPEC-011 §2.1 「누르면」) — ① 읽음(`notificationId` 가 있을 때 · 묶음 OS 알림은 없다) ② 대상을 고른 상태로.
   * `target` 이 `null` 이면(지금 열 수 없는 자원 · §4.5-4) 읽음만 하고 「열 수 없는 항목입니다」. OS 알림 클릭(WP4)도 이것이다.
   */
  const openNotification = useCallback(
    (notificationId: string | null, target: NotificationTarget | null, label = "") => {
      if (notificationId) {
        void markNotificationRead(notificationId).then(
          () => void refreshBadges(),
          () => setError(notificationScreen.readError),
        );
      }
      if (!target) {
        setError(notificationScreen.cannotOpen);
        return;
      }
      if (!canNavigate("workspace")) return;
      openFocus(target, label);
    },
    [canNavigate, openFocus, refreshBadges, setError],
  );
  /* OS 알림을 눌렀다(셸 → 웹 사건 · 셸이 창을 이미 앞으로 가져왔다) — 목록 줄을 누른 것과 같은 일. 묶음(`id: null`)은 읽음 없이 목록만. */
  useEffect(() => onShellNotificationClick((click) => openNotification(click.notification_id, click.target)), [openNotification]);

  async function sendMessage(bodyOverride?: string) {
    const body = bodyOverride ?? chat.draft;
    if (!body.trim()) return;
    // The optimistic fragment carries the text from here; a rejected send keeps its own retry/discard controls.
    await chat.sendCurrent(body, []);
  }

  /**
   * Settles every projection a decided Action may have changed: the visible surface and the active conversation. Never throws, so a read failure is never mistaken for a failed decision. Returns whether
   * every read settled; callers use that to choose between reflected-on-screen and persisted-only wording, and a
   * failure raises the retryable stale-screen banner.
   */
  const refreshProjections = useCallback(async () => {
    const settled = await Promise.allSettled([
      surfaceRefresh.current ? surfaceRefresh.current() : Promise.resolve(),
      chat.refreshActiveConversation(),
    ]);
    const failed = settled.some((result) => result.status === "rejected");
    setStaleProjection(failed ? "판단은 저장되었지만 화면을 갱신하지 못했습니다." : null);
    return !failed;
  }, [chat]);

  async function decideConversationAction(
    actionId: string,
    expectedVersion: number,
    decision: string,
    payload: { base_submission_version?: number; draft?: Record<string, unknown> } = {},
  ) {
    try {
      await chat.decide(actionId, expectedVersion, decision, payload);
    } catch (reason) {
      /* 초안 저장(`save_draft`)의 실패는 그 「수정」 창이 창 안에 낸다(SPEC-002 §2.9 · WORK-009 2a-1 fix0) — 전역 띠를
         함께 세우면 같은 실패가 두 자리에 선다. 확정·거절은 창이 없으니 지금처럼 띠에 낸다. */
      if (decision !== "save_draft") setError(reason instanceof Error ? reason.message : "AX 확인 항목을 처리하지 못했습니다.");
      /* 낡은 기준의 저장은 거부되고 **최신 회차를 다시 읽는다**(SPEC-002 §4 「낡은 저장」 · WORK-009 2a-1 fix1 W-1) — 카드가 새 회차로
         다시 그려지면 창의 다음 「저장」이 새 기준을 싣는다. 창의 입력은 그대로 남는다. 확정 낡음은 이번 범위 밖(기존 부채). */
      if (decision === "save_draft" && isStaleActionError(reason)) await refreshProjections();
      throw reason;
    }
    // The decision is persisted; claim it is reflected on screen only when every affected projection settled.
    const reflected = await refreshProjections();
    setToast(
      decision === "save_draft"
        ? axDraftCard.saved
        : decision === "cancel_assignment"
        ? "업무 요청을 취소했습니다."
        : decision === "approve" || decision === "confirm"
        ? reflected
          ? "제안을 승인해 반영했습니다."
          : "제안을 승인했습니다."
        : "제안을 거절했습니다.",
    );
  }

  const currentPersonaName = session?.display_name ?? "사용자";
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
    onNotice: setToast,
    onDecided: refreshProjections,
    onOpenInboxMessage: openInboxMessage,
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
    /*
     * 사용자 사건 채널(SSE)의 주인 — 로그인한 동안 연결 **하나**(SPEC-011 §4.1-6 · D-10). 화면이 바뀌어도 닫지 않고,
     * 로그아웃·세션 상실로 로그인 화면을 그리면 이 Provider 가 내려가며 닫는다. 세션 확인이 401 이면 회의 스트림의
     * 4401 과 같은 길(아래 `onSessionLost`)로 로그인 화면에 보낸다.
     */
    <EventStreamProvider
      onSessionLost={() => {
        resetWorkspace();
        setSession(null);
      }}
    >
    <NavBadgeWatcher onStale={() => void refreshBadges()} />
    <OsNotificationBridge surface={surface} />
    <AppShell
      nav={
        <SideNav
          activeId={surface}
          collapsed={navCollapsed}
          /* 메시지함 점 = 안 읽은 메시지가 하나라도(SPEC-011 §2.2 · 레일 숫자와 같은 셈) */
          items={visibleNavigation.map((item) => ({ id: item.id, label: item.label, icon: item.icon, disabled: item.disabled, dot: item.id === "inbox" && badges.inbox }))}
          label="제품 탐색"
          logo="MEDISOLVE"
          onCollapse={() => setNavCollapsed((collapsed) => !collapsed)}
          onSelect={(id) => {
            // 사이드바로 설정에 들어오면 알림이 고른 탭(연동 끊김)을 들고 가지 않는다
            if (id === "settings") setSettingsFocus(null);
            setSurface(id as ProductSurface);
          }}
          /*
           * 시안 31 의 기둥 «머리» — 신원 줄 아래에 알림·설정 두 줄이 서고 그 밑을 가로선이 가른다.
           * 자리는 `SideNav` 가 이미 갖고 있던 `utilityItems`(구분선 위 그룹)다 — 새로 만들지 않았다.
           *
           * · 알림: 알림 목록 화면이다(WORK-013 WP3-FE · SPEC-011 §2.1) — id 는 화면 이름 `notifications` 그대로(시안 `alert`).
           *   안 읽은 알림이 하나라도 있으면 점이 선다(§2.2 · `GET /api/me/badges`).
           * · 설정: 이제 **화면이다**(WORK-011 FE-b · SPEC-008 §5 프론트) — 메일·슬랙·카톡 연동과 프로필 설정.
           *   예전에 이 줄과 신원 줄이 열던 「내 AX 캐릭터」 모달은 **없앴다** — 캐릭터는 프로필 설정에서 고른다
           *   (D-48 · 검수 W-14 「진입점 둘 다」). 그래서 신원 줄은 누를 수 없는 글자로 선다.
           */
          utilityItems={[
            { id: "notifications", label: shellNav.notifications, icon: "bell", dot: badges.notifications },
            { id: "settings", label: shellNav.settings, icon: "setting" },
          ]}
          user={{
            name: personName(currentPersonaName),
            // 프로필 이미지(SPEC-008 §4.7 W-15) — 없으면 `SideNav` 가 이름 첫 글자를 그린다.
            avatar: session.profile_image_url ?? undefined,
            // 소속이 여럿이면 한 줄에 다 담기지 않는다. 잘라서 보여 주고 전체는 hover 로 읽는다.
            role: organizationNames.length > 0 ? organizationNames.join(" · ") : "소속 없음",
          }}
          /* 바닥에 남는 것은 로그아웃 하나다 — 설정은 시안대로 기둥 «머리» 로 옮겼다(위 `utilityItems`).
             두 자리에 같은 것을 두지 않는다. 로그아웃의 자리·동작은 그대로다. */
          footerActions={
            <Button variant="text" size="sm" onClick={() => void endSession()} type="button">
              {shellNav.signOut}
            </Button>
          }
        />
      }
    >
      <AppHeader
        actions={surfaceActions}
        titleEnd={surfaceTitleEnd}
        /* 바퀴 6a M-3: 브레드크럼을 지웠다. 회의 상세에서 목록으로 돌아가는 유일한 길이라 바퀴 2 가
           살려 뒀던 것인데, 이제 목록 칸이 상시 옆에 서서 돌아갈 길이 UI 에 들어 있다. 시안도 머리는 한 줄이다. */
        title={surfaceTitle ?? surfaceLabel[surface]}
      />
      <AppBody railLeft={surfaceRails.left} railRight={surfaceRails.right}>
        {/* 4차 발주 6: 오류·재조회 띠는 여기 있었다. 셋 다 화면 아래 공통 토스트로 갔다 — 본문을
            밀지 않고, 성공과 실패가 같은 자리에서 읽힌다. */}
        {/* 셸이 overflow:hidden 이라 본문이 자기 스크롤 기둥을 갖는다 (바퀴 2 D-B).
           회의는 «한 화면에 갇히는» 화면이라 스크롤은 안쪽 패널이 갖는다 — 여기서는 잡지 않는다. */}
        {/* 캘린더도 회의처럼 «한 화면에 갇히는» 화면이다 — 격자가 칸을 꽉 채우고 주 뷰의 시간 격자가
              자기 안에서 스크롤한다(`styles/calendar.css` 의 `.scax-cal-main`). 바깥이 스크롤하면 주인이 둘이 된다. */}
          {/* 프로젝트도 «한 화면에 갇히는» 화면이다 (WORK-005) — 요약 스트립과 진행 라인이 칸을 채우고
              `.scax-pj-view` 가 자기 안에서 스크롤한다. 바깥이 또 스크롤하면 주인이 둘이 된다. */}
          <div className={surface === "meetings" || surface === "calendar" || surface === "project" || surface === "inbox" || surface === "settings" || surface === "notifications" ? "scax-page-scroll scax-page-scroll--fixed" : surface === "work" ? "scax-page-scroll scax-page-scroll--work" : "scax-page-scroll"}>
          {surface === "today" && (
            <TodayPage
              {...pageProps}
              {...sharedWorkProps}
              canGenerateDailyReport={has("daily_report.generate")}
              onAskAx={(text) => void askAx(text)}
              onNavigate={setSurface}
            />
          )}
          {/* 바퀴 8-B 가 이 화면을 본문 한 칸으로 두었던 것을 WORK-004 FE-1 이 세 칸으로 넓혔다 —
              좌측 일정 레일이 셸의 AppBody 슬롯에 선다(오른쪽은 비운다). 선례는 아래 MyWorkPage 다. */}
          {surface === "calendar" && (
            <CalendarPage
              {...pageProps}
              {...sharedWorkProps}
              onRegisterHeaderActions={registerSurfaceActions}
              onRegisterRails={registerSurfaceRails}
            />
          )}
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
          {/* WORK-005 FE-1: 본문 한 칸이던 화면을 셋으로 넓혔다 — 좌 레일(프로젝트 셀렉터 + 업무 카드)과
              우 레일(선택 업무 상자)이 셸의 AppBody 슬롯에 선다. 관리 모달의 손잡이는 머리의 actions 다.
              업무를 «여는» 길은 업무 화면의 드로어 하나뿐이라 관계 그래프와 같은 seam 을 쓴다. */}
          {surface === "project" && (
            <ProjectPage
              {...pageProps}
              /* 상태 드롭다운의 게이트 하나만 **세션 봉투의 역량**이다 (WORK-005 FE-5 작업 0 · D-32).
                 ⚠ ~~「프로젝트 추가」의 `project.manage` 게이트~~ 는 **없앴다** (사용자 확정, 2026-09-22):
                 **만드는 것은 모든 사람이 한다.** 자격이 없으면 **서버가 거절하고 그 문구가 모달 안에 선다** —
                 버튼을 감춰 「왜 없지」를 만들지 않는다. 「프로젝트 관리」의 `may_manage` 는 **그대로**다.
                 ⚠ `sharedWorkProps` 를 통째로 넘기지 않는다: 이 화면은 업무 화면의 열 갈래 props 를
                 쓰지 않고, 새 조회도 만들지 않는다 — 셸이 이미 쥔 값을 그대로 내릴 뿐이다. */
              canManageOwnTasks={has("task.self_manage")}
              focusProjectId={focusProjectId}
              onFocusHandled={() => setFocusProjectId(null)}
              onNotice={setToast}
              onOpenTask={openTaskInWork}
              onRegisterHeaderActions={registerSurfaceActions}
              onRegisterRails={registerSurfaceRails}
            />
          )}
          {surface === "org" && <OrgPage {...pageProps} />}
          {/* 메시지함·설정 (WORK-011) — 둘 다 «한 화면에 갇히는» 화면이다. 좌 레일은 셸 슬롯에 서고 본문이 자기 안에서 스크롤한다. */}
          {surface === "inbox" && (
            <InboxPage
              focus={inboxFocus}
              meName={currentPersonaName}
              onAskAx={(text, context) => void askAx(text, context)}
              onError={setError}
              onFocusHandled={(found) => {
                setInboxFocus(null);
                if (!found) setError(inboxScreen.focusMissing);
              }}
              onRegisterHeaderActions={registerSurfaceActions}
              onRegisterRails={registerSurfaceRails}
              onReadChanged={() => void refreshBadges()}
              onRegisterRefresh={registerSurfaceRefresh}
            />
          )}
          {/* 알림 목록(WORK-013 WP3-FE · SPEC-011 §2.1) — 레일 없는 한 단 · 자기 안에서 스크롤한다 */}
          {surface === "notifications" && (
            <NotificationsPage
              onError={setError}
              onOpen={(item: Notification) => openNotification(item.notification_id, item.target, item.subject?.title ?? "")}
              onReadChanged={() => void refreshBadges()}
              onRegisterHeaderActions={registerSurfaceActions}
              onRegisterTitleEnd={registerSurfaceTitleEnd}
            />
          )}
          {surface === "settings" && (
            <SettingsPage
              characterBusy={characterPreferenceBusy}
              characterError={characterPreferenceError}
              connectResult={landing.connect}
              initialTab={settingsFocus?.tab ?? landing.tab}
              key={settingsFocus ? `focus-${settingsFocus.seq}` : "settings"}
              onChooseCharacter={(characterKey) => void chooseAssistantCharacter(characterKey)}
              onError={setError}
              onNotice={setToast}
              onProfileImage={(url) =>
                setSession((current) => (current ? { ...current, profile_image_url: url } : current))
              }
              onRegisterRails={registerSurfaceRails}
              onRegisterTitle={registerSurfaceTitle}
              session={session}
            />
          )}
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
          /*
           * 4차 발주 7: 업무 화면에서는 **말풍선을 접는다.**
           *
           * 캐릭터의 말풍선은 어두운 알약에 짧은 글 한 줄이라 업무 토스트와 거의 같은 모양이고,
           * 같은 화면 아래쪽에 함께 떠서 「방금 명령의 결과」로 읽혔다. 캐릭터 자체는 그대로 둔다 —
           * 오브를 누르면 지금까지처럼 AX 가 열린다. 이 화면의 알림은 토스트 하나로만 읽힌다.
           */
          bubble={surface !== "work"}
          characterKey={session.assistant_character?.character_key}
          onOpen={() => setIsAxOpen(true)}
          onPrefill={(prompt) => chat.setDraft(prompt, chat.activeConversation?.conversation_id ?? NEW_DRAFT_KEY)}
          state={assistantState}
        />
      )}

      {/*
        * 공통 알림 (4차 발주 6) — 실패도 성공도 여기로 온다.
        *
        * 갱신 실패만 **스스로 사라지지 않는다**: 함께 오는 「다시 불러오기」가 4초 뒤에 없어지면
        * 누를 자리가 사라지기 때문이다. 닫는 길(× )은 셋 다 같다.
        */}
      {notices.length > 0 && (
        <div className="scax-toast-stack">
          {notices.map((notice) => (
            <Toast
              action={notice.kind === "stale" ? { label: "다시 불러오기", onAction: () => void refreshProjections() } : undefined}
              closeLabel="알림 지우기"
              key={notice.id}
              message={notice.message}
              onClose={() => dismissNotice(notice.id)}
              persist={notice.kind === "stale"}
              tone={notice.kind === "success" ? "success" : "error"}
            />
          ))}
        </div>
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
            /* 업무 · 요청 · 회의 · 프로젝트 · 보고는 알림 대상과 **같은 함수**로 연다(`openFocus` · SPEC-011 §4.5-2 3) */
            if (resource.resource_type === "task") {
              openFocus({ surface: "work", task_id: resource.resource_id });
              return;
            }
            if (resource.resource_type === "work_request") {
              openFocus({ surface: "work", work_request_id: resource.resource_id });
              return;
            }
            if (resource.resource_type === "meeting") {
              openFocus({ surface: "meetings", meeting_id: resource.resource_id });
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
              /* U-4 — 셸이 있으면 OS 기본 브라우저로 넘긴다. 앱 창이 남의 사이트로 바뀌거나
                 앱 안에 두 번째 웹뷰가 앉지 않게 하는 것이 요점이다.
                 **셸이 없으면 지금 그대로** 웹이 연다(`E-01`). */
              void openExternal(resource.origin).then((outcome) => {
                /* 셸이 없을 때만 웹이 열던 그대로 연다(`E-01`).
                   셸이 있는데 실패한 경우(`E-14c`)는 **폴백하지 않는다** — 앱 창 안에
                   두 번째 웹뷰가 앉는 것이 U-4 가 막으려는 사고다. 실패 사실은
                   `shell.ts` 가 기록한다(조용히 삼키지 않는다). */
                if (outcome === "absent") window.open(resource.origin, "_blank", "noopener,noreferrer");
              });
              return;
            }
            if (resource.resource_type === "project") {
              openFocus({ surface: "project", project_id: resource.resource_id });
              return;
            }
            if (resource.resource_type === "report") openFocus({ surface: "report" });
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
          surfaceLabel={surfaceLabel[surface]}
        />
        </BrowserOperationScope.Provider>
      )}
    </AppShell>
    </EventStreamProvider>
  );
}
