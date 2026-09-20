import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type React from "react";
import { CalendarRail } from "../../shell/CalendarRail";
import { Chip } from "../../ds/Chip";
import { ActionItemDrawer } from "../action/ActionCenter";
import { InboxRail } from "../../shell/InboxRail";
import { Tabs } from "../../ds/SegmentedControl";

import {
  decideWorkRequest,
  generateDailyReportDraft,
  getWorkRequestInbox,
  getTask,
  getMyWork,
  getSentTaskAssignments,
  getTaskAssignmentCandidates,
  getTasks,
  getWorkRequestAssigneeCandidates,
  getWorkRequestCcCandidates,
  getWorkRequest,
  getWorkRequests,
  hideWorkRequestListEntry,
  markWorkRequestRead,
  transitionDirectTask,
  updateTask,
  withdrawWorkRequest,
} from "../../lib/api";
import { Button } from "../../ds/Button";
import { SegmentedControl } from "../../ds/SegmentedControl";
import {
  ccWorkChips,
  doneWorkChips,
  emptyActionLabel,
  emptyValue,
  myWorkChips,
  personName,
  selectLabel,
  sentWorkChips,
  seoulToday,
  taskStateLabel,
  workChipLabel,
  workTabLabel,
  type WorkChip,
} from "../../lib/labels";
import {
  type ActionItemEnvelope,
  type DirectTask,
  type Persona,
  type ProductSurface,
  type TaskAssignment,
  type TaskPatch,
  type TaskState,
  type WorkRequest,
} from "../../lib/viewModels";
import {
  CompletionReportModal,
  CreateWorkModal,
  ReasonPrompt,
  StatusText,
  TaskDetailDrawer,
  TaskQuickActions,
  WorkRequestDetailDrawer,
  BlockReasonPrompt,
  allowedTaskTransitions,
  displayNameOf,
  type TaskAction,
} from "./WorkModals";
import { requestInboxItems } from "./requestInbox";
import { TaskTimeline } from "./WorkViews";
import { ConfirmModal } from "../../ds/Modal";
import { Empty, EmptyValue } from "../../ds/Empty";
import { DataTable, Td, Th, TrOpenable } from "../../ds/DataTable";
import { Select } from "../../ds/Select";
import { Icon } from "../../ds/icons/Icon";
import {
  CcTaskTable,
  DoneTaskTable,
  OrganizationTaskTable,
  SentTaskTable,
  TaskTable,
  doneGroupsOf,
  sentStateOf,
  type CcRow,
  type SentRow,
} from "./WorkTables";
import { chipCounts, isOpenRequest, isRequestOwner, isRequestRecordRequester, isRequestTask, matchesChip, myWorkRows, type WorkRow } from "./workRows";

type MyWorkPageProps = {
  personaId: string;
  personaName: string;
  personas: Persona[];
  canManageOwnTasks: boolean;
  canAssignTasks?: boolean;
  /** Whether this person may read the work other people in the organization are holding. */
  canReadOrganizationWork?: boolean;
  canCreateWorkRequests: boolean;
  canDecideWorkRequests: boolean;
  canReadActions: boolean;
  onAskAboutTask: (task: DirectTask) => void;
  onNotice: (message: string) => void;
  /** Settles every projection an approved effect may have changed. Never throws; returns false when a read failed. */
  onDecided: () => Promise<boolean>;
  onError: (message: string | null) => void;
  /** Registers this surface's reload so the shell can await it after an approved AX effect (no remount). */
  onRegisterRefresh?: (refresh: (() => Promise<void>) | null) => void;
  /** 바퀴 5a J-2: 머리가 한 줄로 합쳐져서, 이 화면의 액션은 셸의 AppHeader 에 등록해 그린다. */
  onRegisterHeaderActions?: (actions: React.ReactNode) => void;
  /** 바퀴 5b: 좌·우 레일을 셸의 AppBody 슬롯에 등록해 그린다. */
  onRegisterRails?: (rails: { left?: React.ReactNode; right?: React.ReactNode }) => void;
  /** 일일보고를 «만드는» 권한. 이 화면에는 생성 커맨드가 없어서 보고 화면으로 «이동» 하는 데만 쓴다 (J-1). */
  canGenerateDailyReport?: boolean;
  onNavigate?: (surface: ProductSurface) => void;
  /** Open this Task as soon as the page mounts — how another surface hands a person over to the work itself. */
  focusTaskId?: string | null;
  onFocusHandled?: () => void;
  /** Open this WorkRequest through its own authorized read, without making the caller carry a stale projection. */
  focusWorkRequestId?: string | null;
  onRequestFocusHandled?: () => void;
};

/**
 * 탭 셋 — **소유·종결 축이다** (SPEC-003 §2.1).
 *
 * 업무를 만드는 세 «행위»(본인 생성 · 요청 · 배정)와 1:1 이 아니고, **그것이 어긋남은 아니다.**
 * 수락 전 요청은 좌측 업무 요청 수신함에서 응답하고, 수락한 Task만 내 업무에 선다.
 */
type WorkTab = "mine" | "sent" | "done" | "cc";
type ViewMode = "list" | "timeline";

// Waiting on someone else's confirmation sits with the work in flight, not with what is finished.
const stateOrder: Record<TaskState, number> = { blocked: 0, in_progress: 1, open: 3, done: 4, cancelled: 5 };
const views: Array<{ id: ViewMode; label: string }> = [
  { id: "list", label: "목록" },
  { id: "timeline", label: "타임라인" },
];

const chipsForTab: Record<WorkTab, ReadonlyArray<WorkChip>> = {
  mine: myWorkChips,
  sent: sentWorkChips,
  done: doneWorkChips,
  cc: ccWorkChips,
};

/**
 * 열려 있는 **한 겹**의 내용 (4차 발주 3).
 *
 * 지금까지는 상세마다 상태가 따로 있어서(`selectedTask`·`relatedTask`·`selectedRequest`·
 * `requestDerivedTask`·`selectedActionItem`) 둘이 동시에 설 수 있었고, 그때 스크림이 두 번 덮였다.
 * 이제 **쌓기 하나**가 그 다섯을 대신한다 — 화면에 서는 것은 늘 맨 위 하나이고, 「뒤로」는 한 칸
 * 물러설 뿐 겹을 새로 열지 않는다. 그래서 중첩 오버레이가 구조적으로 생길 수 없다.
 */
type DetailEntry =
  | {
      kind: "task";
      taskId: string;
      task: DirectTask;
      /** 이 겹에서 명령을 부를 수 있나. 파생·조직 업무처럼 읽기로 여는 자리는 `false` 다. */
      manage: boolean;
      /**
       * 목록에서 연 것인가 — `true` 면 새로고침에서 그 행이 사라졌을 때 겹도 함께 닫는다.
       * 파생 업무처럼 **목록 밖에서** 읽어 온 것은 목록에 없는 것이 정상이라 닫지 않는다.
       */
      tracked: boolean;
    }
  | { kind: "request"; requestId: string; request: WorkRequest; readOnly: boolean }
  | { kind: "action"; actionItemId: string };

export function MyWorkPage({
  personaId,
  personaName,
  personas,
  canManageOwnTasks,
  canAssignTasks = false,
  canReadOrganizationWork = false,
  canCreateWorkRequests,
  canGenerateDailyReport = false,
  onNavigate,
  onRegisterHeaderActions,
  onRegisterRails,
  canDecideWorkRequests,
  onAskAboutTask,
  onNotice,
  onDecided,
  onError,
  onRegisterRefresh,
  focusTaskId,
  onFocusHandled,
  focusWorkRequestId,
  onRequestFocusHandled,
}: MyWorkPageProps) {
  const me = personName(personaName);
  const [tasks, setTasks] = useState<DirectTask[]>([]);
  const [actionItems, setActionItems] = useState<ActionItemEnvelope[]>([]);
  const [selectedActionItem, setSelectedActionItem] = useState<ActionItemEnvelope | null>(null);
  const [relatedTask, setRelatedTask] = useState<DirectTask | null>(null);
  const [inboxRequests, setInboxRequests] = useState<WorkRequest[]>([]);
  const [inboxState, setInboxState] = useState<"loading" | "error" | "ready">("loading");
  const [allRequests, setAllRequests] = useState<WorkRequest[]>([]);
  const [assigneeCandidates, setAssigneeCandidates] = useState<Persona[]>([]);
  const [assignCandidates, setAssignCandidates] = useState<Persona[]>([]);
  const [ccCandidates, setCcCandidates] = useState<Persona[]>([]);
  const [sentAssignments, setSentAssignments] = useState<TaskAssignment[]>([]);
  const [busy, setBusy] = useState(false);
  const [chip, setChip] = useState<WorkChip>("all");
  const [view, setView] = useState<ViewMode>("list");
  const [tab, setTab] = useState<WorkTab>("mine");
  const [organizationTasks, setOrganizationTasks] = useState<DirectTask[]>([]);
  const [generating, setGenerating] = useState(false);
  const [selectedTask, setSelectedTask] = useState<DirectTask | null>(null);
  const [selectedRequest, setSelectedRequest] = useState<WorkRequest | null>(null);
  const [requestDerivedTask, setRequestDerivedTask] = useState<DirectTask | null>(null);
  const [requestReadOnly, setRequestReadOnly] = useState(false);
  /**
   * 열려 있는 겹 — **늘 하나이고, 안의 내용만 갈아끼워진다** (4차 발주 3).
   *
   * 「요청 상세 → 파생 업무 상세 → 그 업무의 하위」로 들어가도 오버레이는 계속 하나다. 맨 위만 그리고,
   * 「뒤로」는 한 칸 물러선다. 바닥까지 물러서면 닫힌다.
   */
  const [detailStack, setDetailStack] = useState<DetailEntry[]>([]);
  /** 지금 그려지는 겹 — 맨 위 하나다. */
  const detail = detailStack.length > 0 ? detailStack[detailStack.length - 1] : null;
  const [isCreating, setIsCreating] = useState(false);
  /** 보내려는 요청의 「다시 요청」 — 이전 요청을 이어 새 요청을 만든다(재요청 · V-12). */
  const [resending, setResending] = useState<WorkRequest | null>(null);
  /** 사유를 받아야 하는 요청 명령 — 거절과 철회다. */
  const [requestPrompt, setRequestPrompt] = useState<{ request: WorkRequest; command: "reject" | "withdraw" } | null>(null);
  /** 「숨긴 항목 보기」 — 목록 정리는 숨김이고 이력은 남는다 (F-6 · OQ-202 의 화면 선택). */
  const [showHidden, setShowHidden] = useState(false);
  const [hiddenLocally, setHiddenLocally] = useState<string[]>([]);
  /** 지금 읽는 중인 참고 항목 — 연타가 두 번째 명령이 되지 않게 그 단추를 잠근다. */
  const [reading, setReading] = useState<string[]>([]);
  /**
   * 이 세션에서 읽은 참고 항목 (WORK-003 Phase 5).
   *
   * **읽음 필터는 서버의 수신함 조회가 갖는다** (SPEC-001 §4 — `reference` 갈래 하나에만 걸린다).
   * 그런데 이 화면의 수신함 목록은 서버 수신함과 **요청 목록 둘**에서 조립된다(`requestInboxItems`
   * 가 `category` 를 내지 않는 서버를 보완하는 자리다). 요청 목록에는 읽음 필터가 **없으므로**,
   * 그쪽에서 방금 읽은 항목이 다시 올라온다 — 접은 카드가 도로 서는 것이 그 모습이다.
   * 그래서 이 세션이 읽은 것을 여기 들고 조립에서 뺀다. 새로고침 뒤의 판정은 서버 것이다.
   */
  const [readLocally, setReadLocally] = useState<string[]>([]);
  /** React state 는 같은 tick 의 두 이벤트 사이를 못 막는다 — 그 창을 이 ref 가 닫는다. */
  const readingNow = useRef(new Set<string>());
  /** 방금 읽기에서 «숨긴 항목까지» 를 서버가 거절했나. 목록을 세운 뒤 한 번 말하는 데만 쓴다. */
  const hiddenReadFailed = useRef(false);
  // 볼 것이 있을 때만 펼친다. 사람이 접거나 편 뒤에는 그 선택이 이긴다.
  /* K-5: 칸마다 default/empty/loading/error 를 그리려면 그 상태가 화면에 있어야 한다.
     새로 읽는 것이 아니라, 지금까지 전역 오류 배너로만 나가던 reload 의 결과를 레일도 읽게 드러낸 것뿐이다. */
  const [loadState, setLoadState] = useState<"loading" | "error" | "ready">("loading");

  const reloadInbox = useCallback(async () => {
    if (!canDecideWorkRequests) {
      setInboxRequests([]);
      setInboxState("ready");
      return [] as WorkRequest[];
    }
    setInboxState("loading");
    try {
      const requests = await getWorkRequestInbox();
      setInboxRequests(requests);
      setInboxState("ready");
      return requests;
    } catch {
      setInboxState("error");
      return [] as WorkRequest[];
    }
  }, [canDecideWorkRequests]);

  const reload = useCallback(async () => {
    const [work, closed, incoming, requests, nextSent] = await Promise.all([
      getMyWork(),
      getTasks(true).catch(() => [] as DirectTask[]),
      reloadInbox(),
      /* 숨긴 항목까지 함께 읽는다 — 숨김은 «목록에서 빼는 것» 이지 삭제가 아니라, 「숨긴 항목 보기」가
         새로고침 뒤에도 서려면 서버가 그 행을 계속 내주어야 한다(L-6 · F-6).

         **첫 실패를 말없이 삼키지 않는다** (검수 W-3): 서버가 `include_removed` 를 거절하면 일반
         목록으로 내려가되 **그 사실을 화면에 남긴다** — 그러지 않으면 사람은 「숨긴 항목 보기」가
         이 세션에만 사는 것을 모른 채 영속이 된 줄 안다. 일반 목록이 «성공» 했을 때도 경고한다. */
      getWorkRequests(true)
        .then((rows) => {
          hiddenReadFailed.current = false;
          return rows;
        })
        .catch(() =>
          getWorkRequests()
            .then((rows) => {
              hiddenReadFailed.current = true;
              return rows;
            })
            .catch(() => [] as WorkRequest[]),
        ),
      canAssignTasks ? getSentTaskAssignments().catch(() => [] as TaskAssignment[]) : Promise.resolve([] as TaskAssignment[]),
    ]);
    // 할일 is what this person holds. Someone who may read the organization's work sees the rest in its own section,
    // never mixed into their own list.
    const held = new Set(work.map((task) => task.task_id));
    const mine = closed.filter((task) => held.has(task.task_id) || (task.assignee?.member_id ?? personaId) === personaId);
    setOrganizationTasks(closed.filter((task) => !held.has(task.task_id) && (task.assignee?.member_id ?? personaId) !== personaId));
    const merged = new Map<string, DirectTask>();
    for (const task of [...work, ...mine]) merged.set(task.task_id, { ...merged.get(task.task_id), ...task });
    const pendingTaskIds = new Set([...requests, ...incoming].filter(isOpenRequest).map((request) => request.task_id));
    const nextTasks = [...merged.values()].filter((task) => !pendingTaskIds.has(task.task_id));
    setTasks(nextTasks);
    setAllRequests(requests);
    setSentAssignments(nextSent);
    /* 열려 있는 겹도 방금 읽은 값으로 따라간다 — 상세가 혼자 옛 회차를 들고 있으면 저장이 409 로 튄다.
       목록에서 연 업무(`tracked`)가 목록에서 사라지면 그 겹은 닫는다. 파생·조직 업무는 애초에 목록
       밖에서 읽어 온 것이라 목록에 없는 것이 정상이고, 닫지 않는다. */
    setDetailStack((stack) =>
      stack
        .filter((entry) => !(entry.kind === "task" && entry.tracked && !nextTasks.some((task) => task.task_id === entry.taskId)))
        .map((entry) => {
          if (entry.kind === "task") {
            const next = nextTasks.find((task) => task.task_id === entry.taskId);
            return next ? { ...entry, task: next } : entry;
          }
          if (entry.kind === "request") {
            const next = requests.find((request) => request.request_id === entry.requestId);
            // A list refresh must not throw away the permission-safe references loaded by the detail read.
            return next ? { ...entry, request: { ...entry.request, ...next } } : entry;
          }
          return entry;
        }),
    );
  }, [canAssignTasks, personaId, reloadInbox]);

  /**
   * 읽기가 끝난 뒤 배너를 정리한다 — **반쪽으로 온 것이 있으면 지우지 않고 그것을 남긴다.**
   *
   * 명령이 성공하면 지금까지 `onError(null)` 로 배너를 비웠는데, 그러면 「숨긴 항목까지 읽지 못했다」가
   * 성공 한 번에 지워져 사람은 영속이 깨진 것을 끝내 모른다. 성공은 성공대로 말하되 **아직 참인 경고는
   * 남긴다.**
   */
  const settleError = useCallback(() => {
    onError(hiddenReadFailed.current ? "숨긴 항목까지 읽지 못했습니다 — 「숨긴 항목 보기」가 이 세션에만 적용됩니다." : null);
  }, [onError]);

  useEffect(() => {
    let cancelled = false;
    setLoadState("loading");
    void reload()
      .then(() => {
        if (cancelled) return;
        setLoadState("ready");
        settleError();
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setLoadState("error");
        onError(error instanceof Error ? error.message : "내 업무를 불러오지 못했습니다.");
      });
    return () => {
      cancelled = true;
    };
  }, [onError, reload, settleError]);

  /* 바퀴 5b: 「볼 것이 있으면 펼치고 없으면 접는다」는 판단 패널이 «본문 위» 에 있어서 목록을 아래로
     밀던 시절의 규칙이었다. 이제 좌 레일이라 접든 펴든 목록을 밀지 않으므로 그 규칙 자체가 사라졌다 —
     볼 것이 없을 때는 레일이 빈 상태를 낸다(src/shell/InboxRail.tsx). */

  // The shell awaits this to know the visible projection has settled; re-reading in place keeps filter/view state.
  useEffect(() => {
    onRegisterRefresh?.(reload);
    return () => onRegisterRefresh?.(null);
  }, [onRegisterRefresh, reload]);

  /**
   * **참조 후보는 요청 권한에 매달리지 않는다** (최종 프레임 FE 정리).
   *
   * 한동안 이 목록 둘이 한 `useEffect` 안에 있었고, `work_request.create` 가 없으면 **둘 다** 안
   * 읽었다. 그런데 참조자(`cc_member_ids`)와 결재자(`approver_id`)는 **`POST /api/tasks` 본인
   * 갈래도 받는 값**이라, 업무만 만들 수 있는 사람의 생성 창에서 두 칸이 통째로 사라지고 있었다
   * (창은 후보가 비면 그 칸을 그리지 않는다). 그 사람이 고를 수 있는 것을 권한 없어 못 고르는
   * 것처럼 감춘 셈이다.
   *
   * 그래서 **둘을 갈랐다**: 참조 후보는 «만들 수 있는 사람» 이면 읽고, 담당 후보(요청 수신자)는
   * 지금까지대로 요청 권한이 있을 때만 읽는다 — 그쪽은 정말로 요청 갈래에서만 쓰는 목록이다.
   *
   * ⚠️ **BE API gap (권한 확장 필요).** 서버의 `GET /api/work-request-cc-candidates` 는 아직
   * `work_request.create` 를 요구한다(`WorkRequestService.cc_candidates`). 그래서 `task.self_manage`
   * 만 가진 사람은 이 호출에서 403 을 받고 목록이 비며, 창은 여전히 두 칸을 그리지 않는다.
   * **화면이 감추는 일은 여기서 끝났고, 남은 절반은 서버에 있다** — 그 capability 가 `task.self_manage`
   * 로도 열리거나(또는 업무용 참조 후보 입구가 따로 열리면) 두 칸이 그대로 선다. 403 을 오류 배너로
   * 올리지 않는 것은 의도다: 아직 못 읽는 것이 사람의 잘못이 아니다.
   */
  const canCreateAnything = canManageOwnTasks || canCreateWorkRequests;
  useEffect(() => {
    if (!canCreateAnything) {
      setCcCandidates([]);
      return;
    }
    let cancelled = false;
    void getWorkRequestCcCandidates()
      .then((candidates) => {
        if (!cancelled) setCcCandidates(candidates);
      })
      .catch(() => {
        if (!cancelled) setCcCandidates([]);
      });
    return () => {
      cancelled = true;
    };
  }, [canCreateAnything, personaId]);

  useEffect(() => {
    if (!canCreateWorkRequests) {
      setAssigneeCandidates([]);
      return;
    }
    let cancelled = false;
    void getWorkRequestAssigneeCandidates()
      .then((candidates) => {
        if (!cancelled) setAssigneeCandidates(candidates);
      })
      .catch((error) => {
        if (!cancelled) onError(error instanceof Error ? error.message : "업무 대상 후보를 불러오지 못했습니다.");
      });
    return () => {
      cancelled = true;
    };
  }, [canCreateWorkRequests, onError, personaId]);

  useEffect(() => {
    if (!canAssignTasks) {
      setAssignCandidates([]);
      return;
    }
    let cancelled = false;
    void getTaskAssignmentCandidates()
      .then((candidates) => {
        if (!cancelled) setAssignCandidates(candidates);
      })
      .catch(() => {
        if (!cancelled) setAssignCandidates([]);
      });
    return () => {
      cancelled = true;
    };
  }, [canAssignTasks, personaId]);

  /**
   * 전이를 보낸다. **서버가 받아들였는지를 돌려준다** — 사유를 받는 자리(취소·막힘)가 거절당했을 때
   * 사람이 쓴 문장을 지우지 않으려면, 부르는 쪽이 «됐나» 를 알아야 한다. 돌려준 값을 쓰지 않는
   * 호출부는 지금까지와 똑같이 동작한다.
   */
  const transitionTask = async (task: DirectTask, action: TaskAction, reason?: string): Promise<boolean> => {
    setBusy(true);
    try {
      await transitionDirectTask(task.task_id, action, task.version, reason);
      await reload();
      settleError();
      onNotice(
        action === "start" ? "업무를 시작했습니다." : action === "complete" ? "완료 처리했습니다." : action === "block" ? "막힘으로 표시했습니다." : action === "resume" ? "업무를 재개했습니다." : "업무를 취소했습니다.",
      );
      return true;
    } catch (error) {
      onError(error instanceof Error ? error.message : "업무 상태를 바꾸지 못했습니다.");
      return false;
    } finally {
      setBusy(false);
    }
  };

  const updateTaskFields = async (task: DirectTask, patch: TaskPatch) => {
    setBusy(true);
    try {
      await updateTask(task.task_id, task.version, patch);
      await reload();
      settleError();
      onNotice("업무 내용을 저장했습니다.");
    } catch (error) {
      onError(error instanceof Error ? error.message : "업무를 저장하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  /**
   * 받은 요청에 답한다 — **수락은 같은 Task 의 담당 확정이고 새 Task 를 만들지 않는다**(V-10).
   * 거절은 사유가 필수이고, 그 Task 는 「취소됨 — 요청 거절」로 간다(V-11 · `cancel_reason`).
   */
  const decideRequest = useCallback(async (request: WorkRequest, action: "accept" | "reject", reason?: string) => {
    setBusy(true);
    try {
      await decideWorkRequest(request.request_id, action, request.version, reason);
      await reload();
      settleError();
      onNotice(action === "accept" ? "요청을 수락했습니다. 이제 내 업무입니다." : "요청을 거절했습니다. 보낸 사람에게 사유가 전달됩니다.");
      return true;
    } catch (error) {
      onError(error instanceof Error ? error.message : "요청에 답하지 못했습니다.");
      return false;
    } finally {
      setBusy(false);
    }
  }, [reload, settleError, onNotice, onError]);

  /**
   * 수락 전 철회 — 요청은 `withdrawn`, 그 업무는 취소된다. 상위 연결과 로그는 남는다.
   * 서버 입력 모델이 **회차만** 받으므로 사유 칸을 열지 않는다.
   */
  const withdrawRequest = async (request: WorkRequest) => {
    setBusy(true);
    try {
      await withdrawWorkRequest(request.request_id, request.version);
      await reload();
      settleError();
      onNotice("요청을 철회했습니다.");
    } catch (error) {
      onError(error instanceof Error ? error.message : "요청을 철회하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  /**
   * 목록 정리 (L-6 · F-6) — **「숨기기」다.**
   *
   * 「삭제」로 부르면 로그가 남는 사실과 어긋나고, 「보관」은 새 보관함 화면을 요구한다.
   * 서버가 이 명령을 아직 내지 않으면 **화면에서만 접는다** — 그때도 이력은 그대로이고,
   * 「숨긴 항목 보기」로 언제든 되돌아온다.
   */
  const hideFromList = async (request: WorkRequest) => {
    setBusy(true);
    try {
      await hideWorkRequestListEntry(request.request_id);
      await reload();
      settleError();
      onNotice("목록에서 숨겼습니다. 이력에는 그대로 남습니다.");
    } catch (error) {
      /* 서버가 이 명령을 아직 내지 않으면 **이 세션에서만** 접는다 — 그것은 영속이 아니므로
         그렇게 말한다. 새로고침 뒤에도 남으려면 서버의 `list_entry_hidden` 이 있어야 한다. */
      setHiddenLocally((current) => (current.includes(request.request_id) ? current : [...current, request.request_id]));
      onError(error instanceof Error ? error.message : "목록 정리를 저장하지 못했습니다.");
      onNotice("이 화면에서만 숨겼습니다 — 새로고침하면 다시 나타납니다.");
    } finally {
      setBusy(false);
    }
  };

  // cc·배정 후보는 로그인 계정 목록에 없는 구성원(예: 관리자)을 포함하므로 이름 해석용 목록을 합친다.
  const people = useMemo(() => {
    const merged = new Map(personas.map((persona) => [persona.id, persona]));
    for (const candidate of [...ccCandidates, ...assignCandidates, ...assigneeCandidates]) if (!merged.has(candidate.id)) merged.set(candidate.id, candidate);
    return [...merged.values()];
  }, [assignCandidates, assigneeCandidates, ccCandidates, personas]);
  // Relationship projections over the server-authorized list: nothing here widens what the API already returned.
  const requestsToMe = allRequests.filter((request) => request.assignee_id === personaId);
  const sentRequests = allRequests.filter((request) => request.requester_id === personaId);
  const ccRequests = allRequests.filter((request) => request.cc_member_ids?.includes(personaId));
  /* ── 겹 하나를 여닫는 네 손잡이 (4차 발주 3) ──
     `open` 은 쌓기를 새로 세우고(목록에서 여는 자리), `push` 는 그 위에 한 칸 얹는다(상세 «안에서»
     따라 들어가는 자리). `back` 은 한 칸 물러서고, `close` 는 전부 접는다. */
  const openDetail = useCallback((entry: DetailEntry) => setDetailStack([entry]), []);
  const pushDetail = useCallback(
    (entry: DetailEntry) => setDetailStack((stack) => (stack.length === 0 ? [entry] : [...stack, entry])),
    [],
  );
  const backDetail = useCallback(() => setDetailStack((stack) => stack.slice(0, -1)), []);
  const closeDetail = useCallback(() => setDetailStack([]), []);

  /** 목록에서 내 업무 하나를 연다 — 여기서만 명령을 부를 수 있다. */
  const openMyTask = useCallback(
    (task: DirectTask) => openDetail({ kind: "task", taskId: task.task_id, task, manage: canManageOwnTasks, tracked: true }),
    [canManageOwnTasks, openDetail],
  );

  /**
   * 상세 «안에서» 다른 업무로 넘어간다 — 겹을 새로 열지 않고 한 칸 얹는다 (3차 발주 3 · 4차 발주 3).
   *
   * 읽기는 지금까지와 같은 서버 조회 하나다(`getTask`). 「뒤로」가 방금 보던 요청·업무로 되돌린다.
   */
  const pushDerivedTask = async (taskId: string) => {
    onError(null);
    try {
      const task = await getTask(taskId);
      pushDetail({ kind: "task", taskId, task, manage: false, tracked: false });
    } catch (error) {
      onError(error instanceof Error ? error.message : "파생 업무를 열지 못했습니다.");
    }
  };

  /** Open the Task a request produced. The server decides what this principal may see; the client only asks. */
  const openDerivedTask = async (taskId: string) => {
    onError(null);
    try {
      const task = await getTask(taskId);
      openDetail({ kind: "task", taskId, task, manage: false, tracked: false });
    } catch (error) {
      onError(error instanceof Error ? error.message : "파생 업무를 열지 못했습니다.");
    }
  };

  const openWorkRequest = useCallback(async (requestId: string, readOnly = false) => {
    onError(null);
    try {
      const request = await getWorkRequest(requestId);
      openDetail({ kind: "request", requestId, request, readOnly });
    } catch (error) {
      onError(error instanceof Error ? error.message : "업무 요청 상세를 불러오지 못했습니다.");
    }
  }, [onError, openDetail]);

  // Another surface handed this person to one piece of work: open it, once, and let that surface forget it.
  useEffect(() => {
    if (!focusTaskId) return;
    void openDerivedTask(focusTaskId).finally(() => onFocusHandled?.());
  }, [focusTaskId]);

  useEffect(() => {
    if (!focusWorkRequestId) return;
    void openWorkRequest(focusWorkRequestId).finally(() => onRequestFocusHandled?.());
  }, [focusWorkRequestId]);

  /**
   * Follow a Task back to whatever the server said its source is. Only sources it allowed ever reach here.
   *
   * 출처도 **같은 겹 안에서** 연다 — 「뒤로」로 보던 업무가 되돌아온다 (4차 발주 3).
   */
  const openSource = async (source: { type: string; id: string }) => {
    onError(null);
    try {
      if (source.type === "action_item") {
        pushDetail({ kind: "action", actionItemId: source.id });
        return;
      }
      if (source.type !== "work_request") return;
      const request = await getWorkRequest(source.id);
      pushDetail({ kind: "request", requestId: source.id, request, readOnly: false });
    } catch (error) {
      onError(error instanceof Error ? error.message : "출처를 열지 못했습니다.");
    }
  };

  /**
   * 열려 있는 업무의 **요청 원장 행** — 요청자 자리 판정이 여기서 나온다 (검수 F-1).
   *
   * `origin.actor` 로 판정하지 않는다: `_origin_projection` 이 그 자리에 싣는 값은 `requester_id` 라,
   * **회의 승격 요청에서는 시스템 id** 가 오고 누른 사람(`promoted_by_member_id`)이 자기 요청에서
   * 빠진다. 원장 행을 직접 보면 서버(`_is_request_owner`)와 같은 답이 나온다.
   *
   * 못 찾으면 `null` 이고 판정은 `false` 다 — 내가 요청자라면 그 요청은 **내 목록에 있다**
   * (`getWorkRequests` 가 요청자·수신자·참조자 관계로 낸다). 없다는 것은 내 자리가 아니라는 뜻이다.
   */
  const requestOf = useCallback(
    (task: DirectTask | null) => {
      if (!task) return null;
      const sourceId = task.lineage?.source_work_request_id ?? (task.origin?.source?.type === "work_request" ? task.origin.source.id : null);
      if (sourceId) return allRequests.find((row) => row.request_id === sourceId) ?? null;
      return allRequests.find((row) => row.task_id === task.task_id) ?? null;
    },
    [allRequests],
  );

  const today = seoulToday();
  const sorted = useMemo(() => [...tasks].sort((left, right) => (stateOrder[left.state] ?? 9) - (stateOrder[right.state] ?? 9)), [tasks]);
  /** 끝난 것은 「완료 업무」 탭의 것이다 — 「내 업무」에 섞지 않는다 (§2.1). */
  const liveTasks = useMemo(() => sorted.filter((task) => task.state !== "done" && task.state !== "cancelled"), [sorted]);
  const closedTasks = useMemo(() => sorted.filter((task) => task.state === "done" || task.state === "cancelled"), [sorted]);

  const mineRows = useMemo(() => myWorkRows(liveTasks, requestsToMe), [liveTasks, allRequests, personaId]);
  const chips = chipsForTab[tab];
  const activeChip = chips.includes(chip) ? chip : "all";
  const mineCounts = useMemo(() => chipCounts(mineRows, myWorkChips, today), [mineRows, today]);
  const visibleRows = useMemo(
    () => (tab === "mine" ? mineRows.filter((row) => matchesChip(row, activeChip, today)) : mineRows),
    [activeChip, mineRows, tab, today],
  );
  /* 칸반·타임라인은 Task 를 받는다 — 아직 수락하지 않아 Task 가 없는 행은 그 판에 놓을 자리가 없다. */
  const visibleTasks = useMemo(() => visibleRows.flatMap((row) => (row.task ? [row.task] : [])), [visibleRows]);

  /* ── 보낸 업무 ── 요청과 배정을 함께 담되 «행에서» 구분한다 (§2.1 · V-2 · M-1). */
  const hiddenRequestIds = useMemo(
    () => new Set([...hiddenLocally, ...sentRequests.filter((request) => request.list_entry_hidden).map((request) => request.request_id)]),
    [hiddenLocally, allRequests],
  );
  const sentRows = useMemo<SentRow[]>(() => {
    const rows: SentRow[] = sentRequests
      .filter((request) => showHidden || !hiddenRequestIds.has(request.request_id))
      .map((request) => {
        const task = request.task_id ? tasks.find((row) => row.task_id === request.task_id) ?? null : null;
        const state = sentStateOf(request, null);
        return {
          id: `request-${request.request_id}`,
          title: request.title,
          originKind: "work_request" as const,
          assigneeName: displayNameOf(people, request.assignee_id, "담당자"),
          dueDate: request.due_date ?? null,
          stateLabel: state.label,
          stateTone: state.tone,
          request,
          task,
          taskId: request.task_id,
        };
      });
    for (const assignment of sentAssignments) {
      const state = sentStateOf(null, assignment.status);
      rows.push({
        id: `assignment-${assignment.assignment_id}`,
        title: assignment.task.title,
        originKind: "direct_assignment",
        assigneeName: displayNameOf(people, assignment.assignee_id, "담당자"),
        dueDate: assignment.task.due_date ?? null,
        stateLabel: state.label,
        stateTone: state.tone,
        request: null,
        task: assignment.task,
        taskId: assignment.task.task_id,
      });
    }
    return rows;
  }, [allRequests, hiddenRequestIds, people, sentAssignments, showHidden, tasks]);
  const sentAsWorkRows = useMemo<WorkRow[]>(
    () =>
      sentRows.map((row) => ({
        id: row.id,
        title: row.title,
        task: row.task,
        request: row.request,
        dueDate: row.dueDate,
        /* 담당이 실제로 섰는지는 **서버의 `assignment_state`** 가 말한다 — 요청 상태(축이 다르다)나
           Task 존재로 추론하지 않는다. 값이 없는 응답에서만 요청 축으로 되돌아 읽는다. */
        awaitingAcceptance: row.request
          ? row.request.assignment_state
            ? row.request.assignment_state === "pending"
            : isOpenRequest(row.request)
          : false,
        approval: row.task?.derived?.approval ?? null,
      })),
    [sentRows],
  );
  const sentCounts = useMemo(() => chipCounts(sentAsWorkRows, sentWorkChips, today), [sentAsWorkRows, today]);
  const visibleSentRows = useMemo(() => {
    if (tab !== "sent" || activeChip === "all") return sentRows;
    const keep = new Set(sentAsWorkRows.filter((row) => matchesChip(row, activeChip, today)).map((row) => row.id));
    return sentRows.filter((row) => keep.has(row.id));
  }, [activeChip, sentAsWorkRows, sentRows, tab, today]);

  /* ── 완료 업무 ── `done` 과 `cancelled` 를 다르게, 요청 업무의 승인 전 `done` 은 「확인 대기」로 (U-5). */
  const closedSentTasks = useMemo(() => {
    const ids = new Set(sentRows.flatMap((row) => (row.taskId ? [row.taskId] : [])));
    return closedTasks.filter((task) => ids.has(task.task_id));
  }, [closedTasks, sentRows]);
  const closedMineTasks = useMemo(() => {
    const sentIds = new Set(closedSentTasks.map((task) => task.task_id));
    return closedTasks.filter((task) => !sentIds.has(task.task_id));
  }, [closedSentTasks, closedTasks]);
  const doneAsWorkRows = useMemo<WorkRow[]>(
    () =>
      closedTasks.map((task) => ({
        id: task.task_id,
        title: task.title,
        task,
        request: null,
        dueDate: task.due_date ?? null,
        awaitingAcceptance: false,
        approval: task.derived?.approval ?? null,
      })),
    [closedTasks],
  );
  const doneCounts = useMemo(() => chipCounts(doneAsWorkRows, doneWorkChips, today), [doneAsWorkRows, today]);
  const keepDone = (task: DirectTask) =>
    tab !== "done" || activeChip === "all" || matchesChip({ id: task.task_id, title: task.title, task, request: null, dueDate: task.due_date ?? null, awaitingAcceptance: false, approval: task.derived?.approval ?? null }, activeChip, today);
  const doneGroups = useMemo(
    () =>
      doneGroupsOf(closedMineTasks.filter(keepDone), closedSentTasks.filter(keepDone), (task) =>
        task.assignee ? personName(task.assignee.display_name) : task.origin?.actor ? personName(task.origin.actor.display_name) : emptyValue,
      ),
    [activeChip, closedMineTasks, closedSentTasks, tab, today],
  );

  /* ── 참조 업무 ── 남이 나를 참조자로 넣은 요청이다. **읽고 논의하는 자리이고 판단은 없다** (4차 발주 1). */
  const ccRows = useMemo<CcRow[]>(
    () =>
      ccRequests.map((request) => {
        const state = sentStateOf(request, null);
        const who = (memberId: string | null | undefined, fallback: string) =>
          memberId === personaId ? "나" : displayNameOf(people, memberId, fallback);
        return {
          id: request.request_id,
          title: request.title,
          counterpart: `${who(request.requester_id, "보낸 사람")} → ${who(request.assignee_id, "담당자")}`,
          dueDate: request.due_date ?? null,
          stateLabel: state.label,
          stateTone: state.tone,
          request,
        };
      }),
    [allRequests, people, personaId],
  );
  const ccAsWorkRows = useMemo<WorkRow[]>(
    () =>
      ccRows.map((row) => ({
        id: row.id,
        title: row.title,
        task: null,
        request: row.request,
        dueDate: row.dueDate,
        /* 참조자에게는 수락 대기가 «내 차례» 가 아니다 — 칩이 판단을 걸지 않게 늘 `false` 로 둔다. */
        awaitingAcceptance: false,
        approval: null,
      })),
    [ccRows],
  );
  const ccCounts = useMemo(() => chipCounts(ccAsWorkRows, ccWorkChips, today), [ccAsWorkRows, today]);
  const visibleCcRows = useMemo(() => {
    if (tab !== "cc" || activeChip === "all") return ccRows;
    const keep = new Set(ccAsWorkRows.filter((row) => matchesChip(row, activeChip, today)).map((row) => row.id));
    return ccRows.filter((row) => keep.has(row.id));
  }, [activeChip, ccAsWorkRows, ccRows, tab, today]);

  const chipCountsForTab = tab === "mine" ? mineCounts : tab === "sent" ? sentCounts : tab === "cc" ? ccCounts : doneCounts;
  const canCreate = canManageOwnTasks || canCreateWorkRequests;

  /* 시안의 「일일보고 생성」 — 만들고 나서 그 초안이 열린 보고 화면으로 넘긴다.
     실패하면 넘기지 않는다. 무엇이 안 됐는지 먼저 말해야 한다. */
  const generateDailyReport = useCallback(async () => {
    if (!onNavigate) return;
    setGenerating(true);
    try {
      /* 바퀴 12: main(#10)이 초안 생성을 «큐에 넣는» 비동기로 바꿨다 — 돌아오는 것은 초안이 아니라
         생성 상태다. 그래서 「만들었다」가 아니라 「시작했다」로 말하고, 진행은 보고 화면이 폴링해 보여 준다. */
      const status = await generateDailyReportDraft(today);
      onError(null);
      onNotice(
        status.generation_status === "completed"
          ? "오늘 일일보고 초안을 만들었습니다."
          : "오늘 일일보고 초안을 만들고 있습니다. 준비되면 보고 화면에 나타납니다.",
      );
      onNavigate("report");
    } catch (error) {
      onError(error instanceof Error ? error.message : "일일보고 초안을 만들지 못했습니다.");
    } finally {
      setGenerating(false);
    }
  }, [onError, onNavigate, onNotice, today]);

  /*
   * 바퀴 5a J-2: 머리 두 줄(전역 breadcrumb 줄 + 페이지 .page-head)을 AppHeader 한 줄로 합쳤다.
   * 그래서 이 화면의 액션을 셸 머리에 «등록» 한다. 떠날 때 null 로 지운다.
   *
   * 시안의 머리 액션은 둘(「일일보고 생성」 + 「업무 만들기」)이다.
   */
  useEffect(() => {
    if (!onRegisterHeaderActions) return;
    onRegisterHeaderActions(
      <>
        {canGenerateDailyReport && onNavigate && (
          <Button disabled={generating} onClick={() => void generateDailyReport()} size="sm" tone="primary" type="button" variant="outlined">
            {generating ? "초안을 만드는 중…" : "일일보고 생성"}
          </Button>
        )}
        {canCreate && (
          <Button onClick={() => setIsCreating(true)} size="sm" tone="primary" type="button" variant="solid">
            업무 만들기
          </Button>
        )}
      </>,
    );
    return () => onRegisterHeaderActions(null);
  }, [canCreate, canGenerateDailyReport, generateDailyReport, generating, onNavigate, onRegisterHeaderActions]);

  const inboxItems = useMemo(
    () => requestInboxItems(inboxRequests, allRequests, personaId).filter((item) => !readLocally.includes(item.request_id)),
    [inboxRequests, allRequests, personaId, readLocally],
  );

  /**
   * 참고 항목을 읽는다 (SPEC-001 U-12 · WORK-003 Phase 5).
   *
   * **수신함에서만 접는다.** `allRequests` 는 건드리지 않으므로 「참조 업무」 탭에는 그대로 남고,
   * CC 관계도 업무 상세도 그대로다 — 읽음은 그 사람에게만 있는 사실이다.
   *
   * **멱등이다.** 서버가 두 번째 호출에도 `200` 을 주지만, 연타가 두 번 나가는 것 자체를 여기서
   * 먼저 막는다. 실패하면 목록에서 빼지 않는다 — 읽지 못한 것을 읽은 것처럼 접으면 그 항목이
   * 새로고침 한 번에 되살아나 사람이 두 번 읽는다.
   */
  const readReference = useCallback(async (request: WorkRequest) => {
    if (readingNow.current.has(request.request_id)) return;
    readingNow.current.add(request.request_id);
    setReading((current) => (current.includes(request.request_id) ? current : [...current, request.request_id]));
    try {
      await markWorkRequestRead(request.request_id);
      // 뱃지는 «필터가 걸린 수신함 건수» 그대로다 — 목록에서 빠지면 건수도 함께 준다.
      setInboxRequests((current) => current.filter((row) => row.request_id !== request.request_id));
      setReadLocally((current) => (current.includes(request.request_id) ? current : [...current, request.request_id]));
      settleError();
    } catch (error) {
      onError(error instanceof Error ? error.message : "읽음으로 표시하지 못했습니다.");
    } finally {
      readingNow.current.delete(request.request_id);
      setReading((current) => current.filter((id) => id !== request.request_id));
    }
  }, [onError, settleError]);

  /* 바퀴 5b K-6: 레일 두 칸을 셸의 AppBody 슬롯으로 넘긴다. 본문 안에 직접 그리지 않는다 —
     안 넘기면 그 칸이 렌더되지 않는 것이 셸 규약이라, 떠날 때 빈 것으로 되돌려 다음 화면이 두 칸으로 돌아간다. */
  useEffect(() => {
    if (!onRegisterRails) return;
    onRegisterRails({
      left: (
        <InboxRail
          items={inboxItems}
          busy={busy}
          personaId={personaId}
          onRead={(request) => void readReference(request)}
          reading={reading}
          canDecide={canDecideWorkRequests}
          onAccept={(request) => void decideRequest(request, "accept")}
          onReject={(request) => setRequestPrompt({ request, command: "reject" })}
          personas={people}
          onOpen={(request) => void openWorkRequest(request.request_id, request.category === "reference")}
          onRetry={() => void reloadInbox()}
          state={loadState === "loading" ? "loading" : inboxState}
        />
      ),
      right: <CalendarRail onOpen={openMyTask} onRetry={() => void reload()} state={loadState} tasks={tasks} />,
    });
    return () => onRegisterRails({});
  }, [busy, canDecideWorkRequests, decideRequest, inboxItems, inboxState, openMyTask, people, openWorkRequest, personaId, readReference, reading, reloadInbox, loadState, onRegisterRails, reload, tasks]);

  /**
   * 열려 있는 겹을 **전부** 접는다 (3차 발주 4).
   *
   * 오버레이는 이 화면의 상태로만 서므로, 뒤 화면이 바뀌는데 상태가 남으면 스크림이 그대로 화면을
   * 덮은 채 남는다 — 「모달을 닫았는데 화면이 계속 흐리다」의 정체가 그것이다. 탭은 «다른 목록으로
   * 가는» 이동이라 지금 열린 상세는 그 목록의 것이 아니다. 명령을 취소하지 않는다: 여는 자리만 닫는다.
   */
  const closeOverlays = useCallback(() => {
    closeDetail();
    setRequestPrompt(null);
    setIsCreating(false);
    setResending(null);
  }, [closeDetail]);

  /**
   * 사람이 바뀌면 열려 있던 겹도 그 사람 것이 아니다 (3차 발주 4).
   *
   * 이 화면은 페르소나가 바뀌어도 «다시 마운트되지 않는다» — 목록만 새로 읽는다. 그래서 탭 이동과
   * 똑같은 잔류가 여기서도 생긴다: 앞사람의 상세가 열린 채 남아 스크림이 뒷사람의 목록을 계속 덮는다.
   * 처음 렌더에서는 닫을 것도 없고, 다른 화면이 넘겨 준 `focusTaskId` 가 여는 겹을 스스로 덮을 수도
   * 있어서 **바뀐 때만** 접는다.
   */
  const lastPersonaId = useRef(personaId);
  useEffect(() => {
    if (lastPersonaId.current === personaId) return;
    lastPersonaId.current = personaId;
    closeOverlays();
  }, [personaId, closeOverlays]);

  /** 한 행이 지금 부를 수 있는 것 — 받은 요청이면 수락·거절, 내가 맡은 행이면 다음 한 걸음이다. */
  const rowActions = (row: WorkRow) => {
    if (row.awaitingAcceptance && row.request && canDecideWorkRequests) {
      return (
        <>
          <Button disabled={busy} onClick={() => void decideRequest(row.request!, "accept")} size="sm" tone="primary" type="button" variant="outlined">
            수락
          </Button>
          <Button disabled={busy} onClick={() => setRequestPrompt({ request: row.request!, command: "reject" })} size="sm" tone="neutral" type="button" variant="outlined">
            거절
          </Button>
        </>
      );
    }
    // 수락 전인데 내가 답할 자리가 아니면 부를 명령이 없다 — 없는 명령의 단추를 그리지 않는다.
    if (row.awaitingAcceptance || !row.task) return null;
    return canManageOwnTasks ? (
      <TaskQuickActions busy={busy} onChanged={reload} onError={onError} onNotice={onNotice} onTransition={transitionTask} task={row.task} />
    ) : null;
  };

  return (
    <section className="page-surface my-work-page">
      <div className="work-layout">
        {/* 바퀴 5b K-2: 「판단이 필요한 업무」 패널은 여기 있었다. 시안대로 «좌 레일»로 옮겼다 —
            복제가 아니라 이동이라, 이 자리에는 아무것도 남지 않는다 (src/shell/InboxRail.tsx). */}
        <div>
          {/*
            * 시안의 탭 넷 — 내 업무 / 보낸 업무 / 완료 업무 / 참조 업무 (WORK-002 Phase 7-B · 4차 발주 1).
            * 「보낸 업무」는 «내가 보낸 것» 을 담으므로 요청과 배정이 함께 서고, 행에서 갈린다.
            * 「참조 업무」는 «남이 나를 참조자로 넣은 것» 이라 보낸 업무와 다른 자리이고, 판단 명령이 없다.
            */}
          <Tabs
            ariaLabel="업무 관점"
            onChange={(next) => {
              setTab(next);
              setChip("all");
              // 탭을 옮기면 열려 있던 겹은 그 목록의 것이 아니다 — 스크림이 남지 않게 함께 접는다.
              closeOverlays();
            }}
            options={[
              { value: "mine" as const, label: workTabLabel.mine },
              { value: "sent" as const, label: workTabLabel.sent },
              { value: "done" as const, label: workTabLabel.done },
              /* 넷째 탭 — 지금까지 「보낸 업무」 안의 구획이던 참조 목록이다 (4차 발주 1). */
              { value: "cc" as const, label: workTabLabel.cc },
            ]}
            value={tab}
          />
          <div className="scax-chip-bar">
            {/*
              * 칩은 상태 나열이 아니라 «파생 조건» 이다 (§2.6). 건수는 그 칩이 거는 필터의 건수 그대로이고,
              * 읽을 수 없는 것은 서버 목록에 애초에 없으므로 건수에도 들어가지 않는다 (U-15).
              */}
            <span aria-label="업무 필터" className="my-work-page__filters" role="group">
              {chips.map((option) => (
                <Chip
                  key={option}
                  label={`${workChipLabel[option]} ${chipCountsForTab[option] ?? 0}`}
                  on={activeChip === option}
                  onClick={() => setChip(option)}
                />
              ))}
            </span>
            {/* J-5: 시안에 자리가 없다는 이유로 보기 방식을 없애지 않는다. 「WBS 보기」는 여는 화면이
                시안에 없어서 두지 않는다 (M-24 미정 — 뒤집히면 이 단추만 바뀐다). */}
            <span className="scax-chip-bar__end">
              {tab === "mine" && (
                <SegmentedControl
                  ariaLabel="보기 방식"
                  onChange={setView}
                  options={views.map((item) => ({ value: item.id, label: item.label }))}
                  value={view}
                />
              )}
              {tab === "sent" && hiddenRequestIds.size > 0 && (
                <Button onClick={() => setShowHidden((current) => !current)} size="sm" tone="neutral" type="button" variant="outlined">
                  {showHidden ? "숨긴 항목 감추기" : `숨긴 항목 보기 (${hiddenRequestIds.size})`}
                </Button>
              )}
            </span>
          </div>

          {tab === "mine" ? (
            view === "timeline" ? (
              <TaskTimeline onOpen={openMyTask} tasks={visibleTasks} />
            ) : (
              <TaskTable
                actions={rowActions}
                filtered={activeChip !== "all"}
                onClearFilter={() => setChip("all")}
                onCreate={canManageOwnTasks ? () => setIsCreating(true) : undefined}
                onOpen={(row) => {
                  if (row.task) openMyTask(row.task);
                  else if (row.request) void openWorkRequest(row.request.request_id);
                }}
                onRetry={() => void reload()}
                requesterName={(row) =>
                  row.task?.origin?.actor
                    ? personName(row.task.origin.actor.display_name)
                    : row.request
                      ? displayNameOf(people, row.request.requester_id, emptyValue)
                      : emptyValue
                }
                rows={visibleRows}
                state={loadState}
                statusCell={(row) =>
                  row.task ? (
                    <TaskStateCell
                      busy={busy}
                      canManage={canManageOwnTasks && !row.awaitingAcceptance}
                      onChanged={reload}
                      onError={onError}
                      onNotice={onNotice}
                      onTransition={transitionTask}
                      task={row.task}
                    />
                  ) : (
                    <StatusText label="수락 대기" state="pending" />
                  )
                }
                today={today}
              />
            )
          ) : tab === "sent" ? (
            <>
              <SentTaskTable
                actions={(row) => (
                  <>
                    {/* 「다시 요청」은 **재요청**이다 — `supersedes_request_id` 를 실은 새 요청·새 Task (V-12).
                        독촉(`reminders`)과 다른 것이고, 같은 단추가 둘 다일 수는 없다. */}
                    {row.request && !isOpenRequest(row.request) && canCreateWorkRequests && (
                      <Button disabled={busy} onClick={() => setResending(row.request)} size="sm" tone="neutral" type="button" variant="outlined">
                        다시 요청
                      </Button>
                    )}
                    {row.request && isOpenRequest(row.request) && (
                      <Button disabled={busy} onClick={() => setRequestPrompt({ request: row.request!, command: "withdraw" })} size="sm" tone="neutral" type="button" variant="outlined">
                        철회
                      </Button>
                    )}
                    {/* 취소로 끝난 항목만 정리한다 — 목록에서 빠지고 이력은 남는다 (L-6). */}
                    {row.request && (row.request.state === "rejected" || row.request.state === "withdrawn" || row.request.state === "cancelled_by_agreement") && !hiddenRequestIds.has(row.request.request_id) && (
                      <Button disabled={busy} onClick={() => void hideFromList(row.request!)} size="sm" type="button" variant="text">
                        숨기기
                      </Button>
                    )}
                  </>
                )}
                filtered={activeChip !== "all"}
                onClearFilter={() => setChip("all")}
                onOpen={(row) => {
                  if (row.request) void openWorkRequest(row.request.request_id);
                  else if (row.taskId) void openDerivedTask(row.taskId);
                }}
                onRetry={() => void reload()}
                rows={visibleSentRows}
                state={loadState}
                today={today}
              />
              {/* 4차 발주 1: 「참조된 업무」 구획은 여기 있었다. **넷째 탭으로 옮겼다** — 복제가 아니라
                  이동이라 이 자리에는 아무것도 남지 않는다. */}
              {canReadOrganizationWork && (
                <section aria-label="조직 업무 구획" className="sent-section">
                  <h2 className="section-title">
                    조직 업무 <small>조직 사람들이 지금 들고 있는 업무입니다. 읽기만 하며, 옮기고 끝내는 것은 담당자의 몫입니다</small>
                  </h2>
                  {/* 4차 발주 2: 같은 탭 안이라 **같은 표 골격**을 쓴다 — 열 폭도 제목 시작 위치도 위와 같다. */}
                  <OrganizationTaskTable
                    assigneeName={(task) => displayNameOf(people, task.assignee?.member_id, "담당자 없음")}
                    onOpen={(task) => openDetail({ kind: "task", taskId: task.task_id, task, manage: false, tracked: false })}
                    onRetry={() => void reload()}
                    state={loadState}
                    tasks={organizationTasks}
                    today={today}
                  />
                </section>
              )}
            </>
          ) : tab === "cc" ? (
            /* 참조 업무 — 읽고 논의하는 자리다. 여는 상세도 읽기 전용이라 수락·거절이 서지 않는다. */
            <CcTaskTable
              filtered={activeChip !== "all"}
              onClearFilter={() => setChip("all")}
              onOpen={(row) => void openWorkRequest(row.request.request_id, true)}
              onRetry={() => void reload()}
              rows={visibleCcRows}
              state={loadState}
              today={today}
            />
          ) : (
            <DoneTaskTable
              filtered={activeChip !== "all"}
              groups={doneGroups}
              onClearFilter={() => setChip("all")}
              onOpen={(taskId) => void openDerivedTask(taskId)}
              onRetry={() => void reload()}
              state={loadState}
            />
          )}
        </div>
      </div>

      {/*
        * **오버레이는 늘 한 겹이다** (4차 발주 3). 업무 상세·요청 상세·판단 상세가 «같은 가운데 모달»
        * 안에서 서로 갈아끼워지고, 「뒤로」는 한 칸 물러설 뿐 새 겹을 열지 않는다. 오른쪽 드로어는
        * 이 화면에서 더 이상 열리지 않는다 — 넷 다 `presentation="modal"` 이다.
        */}
      {detail?.kind === "task" && (
        <TaskDetailDrawer
          onBack={detailStack.length > 1 ? backDetail : undefined}
          backLabel={detailStack.length > 1 && detailStack[detailStack.length - 2]?.kind === "request" ? "업무 요청 상세로 돌아가기" : "이전 상세로 돌아가기"}
          busy={busy}
          canManage={detail.manage}
          onAskAx={onAskAboutTask}
          onClose={closeDetail}
          onError={onError}
          onNotice={onNotice}
          canAssign={detail.manage && Boolean(canAssignTasks)}
          onChanged={reload}
          onOpenTask={(taskId) => void pushDerivedTask(taskId)}
          onTransition={detail.manage ? transitionTask : async () => undefined}
          onOpenSource={detail.task.origin?.source ? (source) => void openSource(source) : undefined}
          onUpdate={detail.manage ? updateTaskFields : async () => undefined}
          ownerName={detail.task.assignee ? personName(detail.task.assignee.display_name) : detail.manage ? me : "미할당"}
          personaId={personaId}
          personas={people}
          task={detail.task}
          viewerIsRequester={isRequestOwner(requestOf(detail.task), personaId)}
          viewerIsRecordRequester={isRequestRecordRequester(requestOf(detail.task), personaId)}
        />
      )}
      {detail?.kind === "action" && (
        <ActionItemDrawer
          presentation="modal"
          onBack={detailStack.length > 1 ? backDetail : undefined}
          backLabel="이전 상세로 돌아가기"
          principalId={personaId}
          actionItemId={detail.actionItemId}
          key={detail.actionItemId}
          onClose={closeDetail}
          onDone={onDecided}
          onError={onError}
          onNotice={onNotice}
          onOpenDerivedTask={(taskId) => void pushDerivedTask(taskId)}
          personas={people}
        />
      )}
      {detail?.kind === "request" && (
        <WorkRequestDetailDrawer
          canDecide={canDecideWorkRequests}
          readOnly={detail.readOnly}
          onBack={detailStack.length > 1 ? backDetail : undefined}
          onOpenDerivedTask={(taskId) => void pushDerivedTask(taskId)}
          onChanged={async () => { await reload(); settleError(); }}
          onClose={closeDetail}
          onError={onError}
          onNotice={onNotice}
          personaId={personaId}
          personas={people}
          request={detail.request}
        />
      )}
      {requestPrompt?.command === "reject" && (
        <ReasonPrompt
          busy={busy}
          confirmLabel="거절"
          danger
          description={`'${requestPrompt.request.title}' 요청을 거절합니다. 그 업무는 「취소됨 — 요청 거절」로 남고 상위 연결과 로그는 그대로입니다.`}
          fieldLabel="거절 사유"
          heading="거절 사유를 남겨 주세요"
          label="거절 사유"
          onClose={() => setRequestPrompt(null)}
          onSubmit={async (reason) => {
            const accepted = await decideRequest(requestPrompt.request, "reject", reason);
            if (accepted) setRequestPrompt(null);
            return accepted;
          }}
        />
      )}
      {requestPrompt?.command === "withdraw" && (
        /* 철회는 사유를 받지 않는다 — 서버 입력 모델이 회차만 받는다. 묻지 않을 것을 칸으로 열지 않는다. */
        <ConfirmModal
          busy={busy}
          cancelLabel="돌아가기"
          closeLabel="닫기"
          confirmLabel="철회"
          danger
          description={`'${requestPrompt.request.title}' 요청을 수락 전에 철회합니다. 그 업무는 취소되고 이력은 남습니다.`}
          onClose={() => setRequestPrompt(null)}
          onConfirm={() => {
            const { request } = requestPrompt;
            setRequestPrompt(null);
            void withdrawRequest(request);
          }}
          title="요청을 철회할까요?"
        />
      )}
      {(isCreating || resending) && (
        <CreateWorkModal
          assignCandidates={canAssignTasks ? assignCandidates : []}
          assigneeCandidates={assigneeCandidates}
          ccCandidates={ccCandidates}
          canCreateRequest={canCreateWorkRequests}
          canCreateTask={canManageOwnTasks && !resending}
          initial={
            resending
              ? {
                  title: resending.title,
                  description: resending.description ?? null,
                  dueDate: resending.due_date ?? null,
                  checklist: resending.checklist ?? [],
                  assigneeId: resending.assignee_id ?? undefined,
                  parentTaskId: resending.parent_task_id ?? undefined,
                  supersedesRequestId: resending.request_id,
                }
              : undefined
          }
          onClose={() => {
            setIsCreating(false);
            setResending(null);
          }}
          onCreated={async (message, outcome) => {
            setResending(null);
            await reload();
            onNotice(message);
            // 남의 업무가 된 것은 「보낸 업무」에 선다 — 문구가 아니라 만든 쪽이 말해 주는 갈래로 고른다.
            if (outcome?.assignedToOther) setTab("sent");
          }}
          onError={onError}
          onOpenTask={(taskId) => void openDerivedTask(taskId)}
          ownerName={me}
        />
      )}
    </section>
  );
}

/**
 * 표의 «상태» 칸 — 시안대로 알약 트리거(26px)의 `Select` 팝오버다 (바퀴 5c §8-A).
 *
 * - **무엇을 보여줄까**는 세션 봉투(`canManageOwnTasks`)가 정한다 — 읽기 전용인 사람에게는 예전처럼 글자다.
 * - **어디로 갈 수 있나**는 `allowedTaskTransitions` 한 자리가 정한다(`WorkModals.tsx`). 갈 데가
 *   없는 상태(승인 대기 · 취소)는 고를 것이 없으니 역시 글자로 선다.
 * - **저장**은 표·칸반·드로어가 같이 쓰는 `transitionTask` 그대로다. 낙관적 갱신을 하지 않는다.
 */
const stateTriggerTone: Record<TaskState, string> = {
  open: " scax-select__trigger--neutral",
  in_progress: "",
  blocked: " scax-select__trigger--danger",
  done: " scax-select__trigger--positive",
  cancelled: " scax-select__trigger--neutral",
};

function TaskStateCell({
  task,
  canManage,
  busy,
  onTransition,
  onChanged,
  onNotice,
  onError,
}: {
  task: DirectTask;
  canManage: boolean;
  busy: boolean;
  /** 전이를 보낸다. 돌려주는 값(받아들여졌나)은 이 자리가 쓰지 않는다 — 사유 자리만 그것을 읽는다. */
  onTransition: (task: DirectTask, action: TaskAction, reason?: string) => Promise<boolean | void>;
  onChanged: () => Promise<void> | void;
  onNotice: (message: string) => void;
  onError: (message: string | null) => void;
}) {
  const [blocking, setBlocking] = useState(false);
  /** 상태 칸에서 「완료」를 고른 요청 업무 — 전이가 아니라 보고다 (5차 발주). */
  const [reporting, setReporting] = useState(false);
  const transitions = allowedTaskTransitions(task);
  if (!canManage || transitions.length === 0) return <StatusText state={task.state} />;

  return (
    <>
      <Select
            emptyActionLabel={emptyActionLabel.filter}
            labels={selectLabel}
        label={`${task.title} 상태`}
        onChange={(next) => {
          if (next === task.state) return;
          const picked = transitions.find((transition) => transition.to === next);
          if (!picked) return;
          // 막힘은 사유를 받아야 한다 — 고른 자리에서 바로 보내지 않는다
          if (picked.action === "block") {
            setBlocking(true);
            return;
          }
          /* 요청 업무의 「완료」도 고른 자리에서 바로 보내지 않는다 — 그 전이는 서버가 늘 거절하고
             (`이 업무는 요청자의 확인이 필요합니다`), 끝나는 길은 완료 보고 하나다 (5차 발주). */
          if (picked.action === "complete" && isRequestTask(task)) {
            setReporting(true);
            return;
          }
          void onTransition(task, picked.action);
        }}
        /* 지금 상태도 목록에 둔다 — 무엇이 골라져 있는지 보이고, 다시 고르면 아무 일도 일어나지 않는다 */
        options={[
          { value: task.state, label: taskStateLabel[task.state] },
          ...transitions.map((transition) => ({ value: transition.to, label: taskStateLabel[transition.to] })),
        ]}
        trigger={({ label, props }) => (
          <button {...props} className={`scax-select__trigger${stateTriggerTone[task.state] ?? ""}`} disabled={busy}>
            {label}
            <Icon name="chevron-down" size={12} />
          </button>
        )}
        value={task.state}
      />
      {reporting && (
        <CompletionReportModal
          busy={busy}
          onClose={() => setReporting(false)}
          onError={onError}
          onNotice={onNotice}
          onSubmitted={async () => {
            setReporting(false);
            await onChanged();
          }}
          requesterName={task.origin?.actor ? personName(task.origin.actor.display_name) : "요청자"}
          task={task}
        />
      )}
      {blocking && (
        <BlockReasonPrompt
          busy={busy}
          onClose={() => setBlocking(false)}
          onSubmit={(reason) => {
            void onTransition(task, "block", reason);
            setBlocking(false);
          }}
          task={task}
        />
      )}
    </>
  );
}

/* 4차 발주 1: `RequestRelationSection`(참조된 업무 구획)은 여기 있었다. 넷째 탭의 `CcTaskTable` 이
   그 자리를 받았고 — 같은 격자를 쓰는 표 한 벌이라 — 이 부품은 호출부가 0 이 되어 지웠다. */
