import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type React from "react";
import { CalendarRail } from "../../shell/CalendarRail";
import { Chip } from "../../ds/Chip";
import { InboxRail } from "../../shell/InboxRail";
import { Tabs } from "../../ds/SegmentedControl";
import { ActionItemDrawer } from "../action/ActionCenter";

import {
  acceptTaskAssignment,
  declineTaskAssignment,
  generateDailyReportDraft,
  getActionItems,
  getTask,
  getMyWork,
  getSentTaskAssignments,
  getTaskAssignmentCandidates,
  getTasks,
  getWorkRequestAssigneeCandidates,
  getWorkRequestCcCandidates,
  getWorkRequest,
  getWorkRequests,
  transitionDirectTask,
  updateTask,
} from "../../lib/api";
import { Button } from "../../ds/Button";
import { SegmentedControl } from "../../ds/SegmentedControl";
import { dayDifference, emptyActionLabel, emptyValue, formatDate, isOverdue, personName, selectLabel, seoulToday, taskFilterLabel, taskFilterOptions, taskStateLabel, workRequestStateLabel } from "../../lib/labels";
import { type ActionItemEnvelope, type DirectTask, type Persona, type ProductSurface, type TaskAssignment, type TaskPatch, type TaskState, type WorkRequest } from "../../lib/viewModels";
import {
  CreateWorkModal,
  DueText,
  StatusText,
  TaskDetailDrawer,
  TaskQuickActions,
  WorkRequestDetailDrawer,
  BlockReasonPrompt,
  allowedTaskTransitions,
  displayNameOf,
  type TaskAction,
} from "./WorkModals";
import { ChecklistCue, PersonChip, TaskCard, TaskKanban, TaskTimeline } from "./WorkViews";
import { Empty, EmptyValue } from "../../ds/Empty";
import { DataTable, Td, Th, TrOpenable } from "../../ds/DataTable";
import { Select } from "../../ds/Select";
import { Icon } from "../../ds/icons/Icon";

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

type TaskFilter = "all" | "active" | TaskState;
type ViewMode = "list" | "timeline" | "kanban";

// Waiting on someone else's confirmation sits with the work in flight, not with what is finished.
const stateOrder: Record<TaskState, number> = { blocked: 0, in_progress: 1, completion_submitted: 2, open: 3, done: 4, cancelled: 5 };
const views: Array<{ id: ViewMode; label: string }> = [
  { id: "list", label: "목록" },
  { id: "kanban", label: "칸반" },
  { id: "timeline", label: "타임라인" },
];

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
  canReadActions,
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
  const [allRequests, setAllRequests] = useState<WorkRequest[]>([]);
  const [assigneeCandidates, setAssigneeCandidates] = useState<Persona[]>([]);
  const [assignCandidates, setAssignCandidates] = useState<Persona[]>([]);
  const [ccCandidates, setCcCandidates] = useState<Persona[]>([]);
  const [sentAssignments, setSentAssignments] = useState<TaskAssignment[]>([]);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState<TaskFilter>("active");
  const [view, setView] = useState<ViewMode>("list");
  const [tab, setTab] = useState<"mine" | "sent" | "organization">("mine");
  const [organizationTasks, setOrganizationTasks] = useState<DirectTask[]>([]);
  const [generating, setGenerating] = useState(false);
  const [selectedTask, setSelectedTask] = useState<DirectTask | null>(null);
  const [selectedRequest, setSelectedRequest] = useState<WorkRequest | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  // 볼 것이 있을 때만 펼친다. 사람이 접거나 편 뒤에는 그 선택이 이긴다.
  /* K-5: 칸마다 default/empty/loading/error 를 그리려면 그 상태가 화면에 있어야 한다.
     새로 읽는 것이 아니라, 지금까지 전역 오류 배너로만 나가던 reload 의 결과를 레일도 읽게 드러낸 것뿐이다. */
  const [loadState, setLoadState] = useState<"loading" | "error" | "ready">("loading");

  const reload = useCallback(async () => {
    const [work, closed, judgements, requests, nextSent] = await Promise.all([
      getMyWork(),
      getTasks(true).catch(() => [] as DirectTask[]),
      getActionItems(),
      getWorkRequests().catch(() => [] as WorkRequest[]),
      canAssignTasks ? getSentTaskAssignments().catch(() => [] as TaskAssignment[]) : Promise.resolve([] as TaskAssignment[]),
    ]);
    // 할일 is what this person holds. Someone who may read the organization's work sees the rest in its own tab,
    // never mixed into their own list.
    const held = new Set(work.map((task) => task.task_id));
    const mine = closed.filter((task) => held.has(task.task_id) || (task.assignee?.member_id ?? personaId) === personaId);
    setOrganizationTasks(closed.filter((task) => !held.has(task.task_id) && (task.assignee?.member_id ?? personaId) !== personaId));
    const merged = new Map<string, DirectTask>();
    for (const task of [...work, ...mine]) merged.set(task.task_id, { ...merged.get(task.task_id), ...task });
    const nextTasks = [...merged.values()];
    setTasks(nextTasks);
    setActionItems(judgements);
    setAllRequests(requests);
    setSentAssignments(nextSent);
    setSelectedTask((current) => (current ? nextTasks.find((task) => task.task_id === current.task_id) ?? null : null));
    setSelectedRequest((current) => {
      if (!current) return null;
      const next = requests.find((request) => request.request_id === current.request_id);
      // A list refresh must not throw away the permission-safe references loaded by the detail read.
      return next ? { ...current, ...next } : null;
    });
  }, [canAssignTasks, personaId]);

  useEffect(() => {
    let cancelled = false;
    setLoadState("loading");
    void reload()
      .then(() => {
        if (cancelled) return;
        setLoadState("ready");
        onError(null);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setLoadState("error");
        onError(error instanceof Error ? error.message : "내 업무를 불러오지 못했습니다.");
      });
    return () => {
      cancelled = true;
    };
  }, [onError, reload]);

  /* 바퀴 5b: 「볼 것이 있으면 펼치고 없으면 접는다」는 판단 패널이 «본문 위» 에 있어서 목록을 아래로
     밀던 시절의 규칙이었다. 이제 좌 레일이라 접든 펴든 목록을 밀지 않으므로 그 규칙 자체가 사라졌다 —
     볼 것이 없을 때는 레일이 빈 상태를 낸다(src/InboxRail.tsx). */

  // The shell awaits this to know the visible projection has settled; re-reading in place keeps filter/view state.
  useEffect(() => {
    onRegisterRefresh?.(reload);
    return () => onRegisterRefresh?.(null);
  }, [onRegisterRefresh, reload]);

  useEffect(() => {
    if (!canCreateWorkRequests) {
      setAssigneeCandidates([]);
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


  const transitionTask = async (task: DirectTask, action: TaskAction, reason?: string) => {
    setBusy(true);
    try {
      await transitionDirectTask(task.task_id, action, task.version, reason);
      await reload();
      onError(null);
      onNotice(
        action === "start" ? "업무를 시작했습니다." : action === "complete" ? "완료 처리했습니다." : action === "block" ? "막힘으로 표시했습니다." : action === "resume" ? "업무를 재개했습니다." : "업무를 취소했습니다.",
      );
    } catch (error) {
      onError(error instanceof Error ? error.message : "업무 상태를 바꾸지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  const updateTaskFields = async (task: DirectTask, patch: TaskPatch) => {
    setBusy(true);
    try {
      await updateTask(task.task_id, task.version, patch);
      await reload();
      onError(null);
      onNotice("업무 내용을 저장했습니다.");
    } catch (error) {
      onError(error instanceof Error ? error.message : "업무를 저장하지 못했습니다.");
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
  /** Open the Task a request produced. The server decides what this principal may see; the client only asks. */
  const openDerivedTask = async (taskId: string) => {
    onError(null);
    try {
      setRelatedTask(await getTask(taskId));
      setSelectedRequest(null);
    } catch (error) {
      onError(error instanceof Error ? error.message : "파생 업무를 열지 못했습니다.");
    }
  };

  const openWorkRequest = async (requestId: string) => {
    onError(null);
    try {
      setSelectedRequest(await getWorkRequest(requestId));
    } catch (error) {
      onError(error instanceof Error ? error.message : "업무 요청 상세를 불러오지 못했습니다.");
    }
  };

  // Another surface handed this person to one piece of work: open it, once, and let that surface forget it.
  useEffect(() => {
    if (!focusTaskId) return;
    void openDerivedTask(focusTaskId).finally(() => onFocusHandled?.());
  }, [focusTaskId]);

  useEffect(() => {
    if (!focusWorkRequestId) return;
    void openWorkRequest(focusWorkRequestId).finally(() => onRequestFocusHandled?.());
  }, [focusWorkRequestId]);

  /** Follow a Task back to whatever the server said its source is. Only sources it allowed ever reach here. */
  const openSource = async (source: { type: string; id: string }) => {
    onError(null);
    try {
      if (source.type === "action_item") {
        setSelectedTask(null);
        setRelatedTask(null);
        setSelectedActionItem({ action_item_id: source.id } as ActionItemEnvelope);
        return;
      }
      if (source.type !== "work_request") return;
      setSelectedTask(null);
      setRelatedTask(null);
      await openWorkRequest(source.id);
    } catch (error) {
      onError(error instanceof Error ? error.message : "출처를 열지 못했습니다.");
    }
  };

  const sorted = useMemo(() => [...tasks].sort((left, right) => stateOrder[left.state] - stateOrder[right.state]), [tasks]);
  const visibleTasks = useMemo(() => {
    if (filter === "all") return sorted;
    if (filter === "active") return sorted.filter((task) => task.state !== "done" && task.state !== "cancelled");
    return sorted.filter((task) => task.state === filter);
  }, [filter, sorted]);
  const canCreate = canManageOwnTasks || canCreateWorkRequests;
  const today = seoulToday();

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
   *
   * **바퀴 5c: 5a 의 판정을 뒤집었다.** 5a 는 「`generateDailyReportDraft` 는 보고 화면이 날짜를 들고
   * 부르는 것이라 여기서 못 부른다」며 단순 «이동» 단추로 줄였다. 다시 확인해 보니 **부를 수 있다** —
   * 그 함수가 받는 것은 날짜 문자열 하나뿐이고(`api.ts`), 이 화면에는 이미 `today`(`seoulToday()`)가 있다.
   * 보고 화면의 `reportDate` 기본값도 같은 `seoulToday()` 이고, 그 화면은 마운트할 때 그 날짜의 **최신
   * 초안을 다시 읽는다**(`loadReport`). 그래서 여기서 만들고 넘어가면 만든 초안이 그대로 열린다.
   * 새 엔드포인트도, 새 상태도 만들지 않았다.
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

  /* 바퀴 5b K-6: 레일 두 칸을 셸의 AppBody 슬롯으로 넘긴다. 본문 안에 직접 그리지 않는다 —
     안 넘기면 그 칸이 렌더되지 않는 것이 셸 규약이라, 떠날 때 빈 것으로 되돌려 다음 화면이 두 칸으로 돌아간다. */
  useEffect(() => {
    if (!onRegisterRails) return;
    onRegisterRails({
      left: (
        <InboxRail
          items={actionItems}
          onOpen={setSelectedActionItem}
          onRetry={() => void reload()}
          state={loadState}
        />
      ),
      right: <CalendarRail onOpen={setSelectedTask} onRetry={() => void reload()} state={loadState} tasks={tasks} />,
    });
    return () => onRegisterRails({});
  }, [actionItems, loadState, onRegisterRails, reload, tasks]);

  return (
    <section className="page-surface">
      <div className="work-layout">
        {/* 바퀴 5b K-2: 「판단이 필요한 업무」 패널은 여기 있었다. 시안대로 «좌 레일»로 옮겼다 —
            복제가 아니라 이동이라, 이 자리에는 아무것도 남지 않는다 (src/InboxRail.tsx). */}
        <div>
          {/*
            * 바퀴 5a J-4·J-8: 탭과 필터가 «같은 줄» 이던 것을 시안대로 두 줄로 갈랐다.
            * 탭은 시안의 모양(.scax-tabs · 52px · 밑줄 2px)을 쓰되, **뜨고 지는 조건은 우리 것 그대로**다.
            * 이름도 뜻이 맞는 것만 시안을 따랐다 — 「할일」은 시안의 「내 업무」와 같은 것이라 바꿨고,
            * 「요청·배정」은 받은·보낸·참조를 함께 담는 우리 탭이라 시안의 「보낸 업무」로 부르면 틀린다.
            * 시안의 「완료 업무」 탭은 우리가 상태 필터로 하는 일이라 탭을 새로 만들지 않았다.
            */}
          <Tabs
            ariaLabel="업무 관점"
            onChange={setTab}
            options={[
              { value: "mine" as const, label: "내 업무" },
              ...(canCreateWorkRequests || canAssignTasks || requestsToMe.length > 0 || ccRequests.length > 0
                ? [{ value: "sent" as const, label: "요청·배정" }]
                : []),
              ...(canReadOrganizationWork ? [{ value: "organization" as const, label: "조직 업무" }] : []),
            ]}
            value={tab}
          />
          {tab === "mine" && (
            <div className="scax-chip-bar">
              {/*
                * J-8: 상태 필터가 팝오버 하나이던 것을 시안대로 Chip 나열로 폈다. 옵션·뜻은 우리 것 그대로다.
                * 팝오버였을 때는 radiogroup 이라 「무엇 중 하나를 고르는가」가 이름으로 읽혔다. 칩은
                * aria-pressed 토글이라 그 묶음이 사라지므로, 묶음에 이름을 붙여 그 자리를 지킨다.
                */}
              <span aria-label="상태 필터" role="group">
                {taskFilterOptions.map((option) => (
                  <Chip key={option} label={taskFilterLabel[option]} on={filter === option} onClick={() => setFilter(option)} />
                ))}
              </span>
              {/* J-5: 시안에 자리가 없다는 이유로 보기 방식을 없애지 않는다. 시안의 오른쪽 끝 자리에 둔다 (G-14). */}
              <span className="scax-chip-bar__end">
                <SegmentedControl
                  ariaLabel="보기 방식"
                  onChange={setView}
                  options={views.map((item) => ({ value: item.id, label: item.label }))}
                  value={view}
                />
              </span>
            </div>
          )}

          {tab === "organization" ? (
            <section aria-label="조직 업무" className="sent-section">
              <h2 className="section-title">
                조직 업무 <small>조직 사람들이 지금 들고 있는 업무입니다. 읽기만 하며, 옮기고 끝내는 것은 담당자의 몫입니다</small>
              </h2>
              <DataTable>
                <thead>
                  <tr>
                    <Th>업무명</Th>
                    <Th align="center">담당자</Th>
                    <Th align="center">상태</Th>
                    <Th align="center">기한</Th>
                  </tr>
                </thead>
                <tbody>
                  {organizationTasks.length === 0 && (
                    <tr>
                      <Td colSpan={4}>
                        <Empty description="누군가 업무를 맡으면 여기에서 보입니다." title="조직에 진행 중인 다른 업무가 없습니다" />
                      </Td>
                    </tr>
                  )}
                  {organizationTasks.map((task) => (
                    <TrOpenable data-organization-task={task.task_id} key={task.task_id} onClick={() => setSelectedTask(task)}>
                      <Td title>{task.title}</Td>
                      <Td align="center">{displayNameOf(people, task.assignee?.member_id, "담당자 없음")}</Td>
                      <Td align="center">{taskStateLabel[task.state] ?? task.state}</Td>
                      <Td align="center">{task.due_date ?? <EmptyValue />}</Td>
                    </TrOpenable>
                  ))}
                </tbody>
              </DataTable>
            </section>
          ) : tab === "sent" ? (
            <>
            <RequestRelationSection
              emptyHint="동료가 보낸 요청이 도착하면 여기에 쌓입니다."
              emptyTitle="받은 업무가 없습니다"
              counterpart="requester"
              hint="판단이 끝난 요청도 기록으로 남습니다"
              label="받은 업무"
              onOpen={(request) => void openWorkRequest(request.request_id)}
              people={people}
              personaId={personaId}
              requests={requestsToMe}
            />
            {canCreateWorkRequests && (
              <RequestRelationSection
                counterpart="assignee"
                emptyHint="새 업무 추가에서 동료에게 업무를 보낼 수 있습니다."
                emptyTitle="보낸 업무가 없습니다"
                hint="조정 요청을 받으면 상세에서 내용을 고쳐 재상신합니다"
                label="보낸 업무"
                onOpen={(request) => void openWorkRequest(request.request_id)}
                people={people}
                personaId={personaId}
                requests={sentRequests}
              />
            )}
            {canAssignTasks && (
              <section aria-label="내가 지정한 업무" className="sent-section">
                <h2 className="section-title">
                  내가 지정한 업무 <small>이미 넘긴 업무입니다. 수락하면 그 사람의 업무가 됩니다</small>
                </h2>
                <DataTable>
                  <thead>
                    <tr>
                      <Th>업무명</Th>
                      <Th align="center">수락 상태</Th>
                      <Th align="center">업무 상태</Th>
                      <Th align="center">담당자</Th>
                      <Th align="center">기한</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {sentAssignments.length === 0 && (
                      <tr>
                        <Td colSpan={4}>
                          <Empty description="새 업무 추가에서 담당자를 팀원으로 고르면 그 사람에게 갑니다." title="내가 담당자를 지정한 업무가 없습니다" />
                        </Td>
                      </tr>
                    )}
                    {sentAssignments.map((assignment) => (
                      <TrOpenable key={assignment.assignment_id} onClick={() => void openDerivedTask(assignment.task.task_id)}>
                        <Td title>{assignment.task.title}</Td>
                        <Td align="center">
                          <StatusText
                            label={assignment.status === "pending" ? "수락 대기" : assignment.status === "active" ? "수락됨" : assignment.status === "declined" ? `거절됨${assignment.decline_reason ? ` · ${assignment.decline_reason}` : ""}` : assignment.status}
                            state={assignment.status === "pending" ? "pending" : assignment.status === "active" ? "accepted" : "rejected"}
                          />
                        </Td>
                        <Td align="center">
                          <StatusText state={assignment.task.state} />
                        </Td>
                        <Td align="center">{displayNameOf(people, assignment.assignee_id, "담당자")}</Td>
                        <Td align="center">{assignment.task.due_date ? formatDate(assignment.task.due_date) : "—"}</Td>
                      </TrOpenable>
                    ))}
                  </tbody>
                </DataTable>
              </section>
            )}
            {/* 참조는 이 제품이 가진 관계 하나이지 있을 때만 생기는 것이 아니다. 옆의 두 덩어리와 같은 규칙으로
                자리를 지켜야, 참조로 받은 요청이 아직 없는 사람도 그런 자리가 있다는 것을 안다. */}
            <RequestRelationSection
              counterpart="both"
              emptyHint="동료가 나를 참조자로 넣어 보낸 요청이 여기에 쌓입니다."
              emptyTitle="참조된 업무가 없습니다"
              hint="읽고 논의할 수 있지만 판단은 담당자가 합니다"
              label="참조된 업무"
              onOpen={(request) => void openWorkRequest(request.request_id)}
              people={people}
              personaId={personaId}
              requests={ccRequests}
            />
            </>
          ) : view === "kanban" ? (
            <TaskKanban
              busy={busy}
              canManage={canManageOwnTasks}
              onInvalidMove={onNotice}
              onOpen={setSelectedTask}
              onTransition={transitionTask}
              tasks={filter === "active" ? sorted : visibleTasks}
            />
          ) : view === "timeline" ? (
            <TaskTimeline onOpen={setSelectedTask} tasks={filter === "active" ? sorted : visibleTasks} />
          ) : (
            <div aria-label="내 업무" className="scax-task-table scax-task-table--nostar" role="table">
              {/*
                * 바퀴 5a J-6: 시맨틱 <table> 을 시안대로 div + CSS grid 로 바꿨다 — 머리는 고정이고 본문만 스크롤한다.
                * role 로 표의 «뜻» 은 지킨다(table/row/columnheader/cell) — 보조기술에는 여전히 표다.
                *
                * **열이 7 → 5 로 준다.** 시안은 6열(별표·제목·요청자·기한·상태·액션)인데 별표는 5열로 접었다.
                * 바퀴 5c 가 J-1(「BE 계약이 없으면 안 그린다」)을 폐기했지만 **별표는 그 폐기의 유일한 예외다** —
                * `starred` 필드도 저장 엔드포인트도 없어서 눌러도 아무 데도 안 남는다. 컨트롤을 보여줄지 말지가
                * 아니라 «값이 사라지는» 문제라 그리지 않는다. 필드가 생기면 modifier 를 떼고 DS 기본 6열로 돌아간다.
                * 시안에 없어서 «빠지는» 것: 시작일 · 출처. 담당자 열도 이 표에서는 늘 「나」라 시안대로 접었다.
                */}
              <div className="scax-task-table__head" role="row">
                <span role="columnheader">업무명</span>
                <span className="scax-task-table__cell--center" role="columnheader">요청자</span>
                <span className="scax-task-table__cell--center" role="columnheader">기한</span>
                <span className="scax-task-table__cell--center" role="columnheader">상태</span>
                <span className="scax-task-table__cell--center" role="columnheader">액션</span>
              </div>
              <div className="scax-task-table__body">
                {visibleTasks.length === 0 &&
                  (filter !== "active" && filter !== "all" ? (
                    <Empty
            actionLabel={emptyActionLabel.filter}
                      description="다른 상태를 선택해 보세요."
                      onAction={() => setFilter("active")}
                      title="조건에 맞는 업무가 없습니다"
                      variant="filter"
                    />
                  ) : (
                    <Empty
                      actionLabel="첫 업무 만들기"
                      description="오늘 할 일을 등록하면 여기에 쌓입니다."
                      onAction={canManageOwnTasks ? () => setIsCreating(true) : undefined}
                      title="등록된 업무가 없습니다"
                    />
                  ))}
                {visibleTasks.map((task) => (
                  <TaskTableRow
                    actions={canManageOwnTasks && <TaskQuickActions busy={busy} onTransition={transitionTask} task={task} />}
                    key={task.task_id}
                    onOpen={() => setSelectedTask(task)}
                    requester={task.origin?.actor ? personName(task.origin.actor.display_name) : emptyValue}
                    statusCell={
                      <TaskStateCell
                        busy={busy}
                        canManage={canManageOwnTasks}
                        onTransition={transitionTask}
                        task={task}
                      />
                    }
                    task={task}
                    today={today}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {selectedTask && (
        <TaskDetailDrawer
          busy={busy}
          canManage={canManageOwnTasks}
          onAskAx={onAskAboutTask}
          onClose={() => setSelectedTask(null)}
          onError={onError}
          onNotice={onNotice}
          canAssign={Boolean(canAssignTasks)}
          onChanged={reload}
          onOpenTask={(taskId) => void openDerivedTask(taskId)}
          onTransition={transitionTask}
          onOpenSource={selectedTask.origin?.source ? (source) => void openSource(source) : undefined}
          onUpdate={updateTaskFields}
          ownerName={selectedTask.assignee ? personName(selectedTask.assignee.display_name) : me}
          task={selectedTask}
        />
      )}
      {relatedTask && (
        <TaskDetailDrawer
          busy={busy}
          canManage={false}
          onClose={() => setRelatedTask(null)}
          onError={onError}
          onNotice={onNotice}
          onOpenSource={relatedTask.origin?.source ? (source) => void openSource(source) : undefined}
          onTransition={async () => undefined}
          onUpdate={async () => undefined}
          ownerName={relatedTask.assignee ? personName(relatedTask.assignee.display_name) : "미할당"}
          task={relatedTask}
        />
      )}
      {selectedActionItem && (
        <ActionItemDrawer
          principalId={personaId}
          actionItemId={selectedActionItem?.action_item_id ?? ""}
          key={selectedActionItem?.action_item_id}
          onClose={() => setSelectedActionItem(null)}
          onDone={onDecided}
          onError={onError}
          onNotice={onNotice}
          onOpenDerivedTask={(taskId) => {
            setSelectedActionItem(null);
            void openDerivedTask(taskId);
          }}
          personas={people}
        />
      )}
      {selectedRequest && (
        <WorkRequestDetailDrawer
          canDecide={canDecideWorkRequests}
          onOpenDerivedTask={(taskId) => void openDerivedTask(taskId)}
          onChanged={reload}
          onClose={() => setSelectedRequest(null)}
          onError={onError}
          onNotice={onNotice}
          personaId={personaId}
          personas={people}
          request={selectedRequest}
        />
      )}
      {isCreating && (
        <CreateWorkModal
          assignCandidates={canAssignTasks ? assignCandidates : []}
          assigneeCandidates={assigneeCandidates}
          ccCandidates={ccCandidates}
          canCreateRequest={canCreateWorkRequests}
          canCreateTask={canManageOwnTasks}
          onClose={() => setIsCreating(false)}
          onCreated={async (message) => {
            await reload();
            onNotice(message);
            if (message.includes("요청을") || message.includes("배정했습니다")) setTab("sent");
          }}
          onError={onError}
          ownerName={me}
        />
      )}
    </section>
  );
}

/**
 * 표의 «상태» 칸 — 시안대로 알약 트리거(26px)의 `Select` 팝오버다 (바퀴 5c §8-A).
 *
 * 바퀴 5a 는 여기에 읽기 글자만 두었다. 「`allowed_commands` 가 없으니 그리지 마라」고 했는데 그게
 * 틀렸다 — 권한 필드가 하는 일은 «이 컨트롤을 보여줄까» 뿐이고 실제 차단은 서버가 한다. 그리고 우리
 * 앱은 이미 같은 근거(업무의 지금 상태)로 액션 열의 커맨드 단추를 그리고 있었다. 새 위반이 아니라
 * 있는 방식을 쓰는 것이다.
 *
 * - **무엇을 보여줄까**는 세션 봉투(`canManageOwnTasks`)가 정한다 — 읽기 전용인 사람에게는 예전처럼 글자다.
 * - **어디로 갈 수 있나**는 `allowedTaskTransitions` 한 자리가 정한다(`WorkModals.tsx`). 갈 데가
 *   없는 상태(완료 확인 대기 · 취소)는 고를 것이 없으니 역시 글자로 선다.
 * - **저장**은 표·칸반·드로어가 같이 쓰는 `transitionTask` 그대로다. 낙관적 갱신을 하지 않는다 —
 *   부르고 나서 다시 읽는다(N-3: 기존 방식과 같게).
 *
 * 「막힘」만 사유를 받아야 해서(`transitionDirectTask` 의 `reason`) 고른 즉시 보내지 않고 한 줄 칸을
 * 연다 — `TaskQuickActions` 가 쓰던 것과 같은 `.inline-reason` 이다.
 */
const stateTriggerTone: Record<TaskState, string> = {
  open: " scax-select__trigger--neutral",
  in_progress: "",
  blocked: " scax-select__trigger--danger",
  completion_submitted: "",
  done: " scax-select__trigger--positive",
  cancelled: " scax-select__trigger--neutral",
};

function TaskStateCell({
  task,
  canManage,
  busy,
  onTransition,
}: {
  task: DirectTask;
  canManage: boolean;
  busy: boolean;
  onTransition: (task: DirectTask, action: TaskAction, reason?: string) => Promise<void>;
}) {
  const [blocking, setBlocking] = useState(false);
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
          void onTransition(task, picked.action);
        }}
        /* 지금 상태도 목록에 둔다 — 무엇이 골라져 있는지 보이고, 다시 고르면 아무 일도 일어나지 않는다 */
        options={[
          { value: task.state, label: taskStateLabel[task.state] },
          ...transitions.map((transition) => ({ value: transition.to, label: taskStateLabel[transition.to] })),
        ]}
        trigger={({ label, props }) => (
          <button {...props} className={`scax-select__trigger${stateTriggerTone[task.state]}`} disabled={busy}>
            {label}
            <Icon name="chevron-down" size={12} />
          </button>
        )}
        value={task.state}
      />
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

function TaskTableRow({
  task,
  today,
  requester,
  actions,
  statusCell,
  onOpen,
}: {
  task: DirectTask;
  today: string;
  requester: string;
  actions: React.ReactNode;
  /** 상태 열에 들어가는 것 — 고칠 수 있으면 알약 Select, 아니면 읽기 글자다 (`TaskStateCell` 이 정한다) */
  statusCell: React.ReactNode;
  onOpen: () => void;
}) {
  /* J-7: 기한이 지난 만큼을 빨간 「+N」으로 낸다. due_date 와 오늘로 계산되니 서버가 필요 없다. */
  const overdueDays = isOverdue(task, today) ? dayDifference(task.due_date!, today) : 0;
  return (
    <div
      className="scax-task-table__row openable"
      onClick={(event) => {
        if ((event.target as HTMLElement).closest("button, input, select, textarea, a, label")) return;
        onOpen();
      }}
      role="row"
    >
      <span className="scax-task-table__cell--title" role="cell">
        <span className="cell-main">
          <span className={task.state === "cancelled" ? "scax-task-table__title cancelled-title" : "scax-task-table__title"}>
            {task.title}
          </span>
          <ChecklistCue progress={task.checklist_progress} />
          {task.block_reason && <small className="reason">막힘 사유: {task.block_reason}</small>}
        </span>
      </span>
      <span className="scax-task-table__cell--center scax-task-table__cell--muted" role="cell">
        {requester}
      </span>
      <span className="scax-task-table__cell--center" role="cell">
        {task.due_date ? (
          <span className="scax-task-table__due">
            {formatDate(task.due_date)}
            {overdueDays > 0 && <span className="scax-task-table__due-extra">+{overdueDays}</span>}
          </span>
        ) : (
          <EmptyValue />
        )}
      </span>
      <span className="scax-task-table__cell--center" role="cell">
        {statusCell}
      </span>
      <span className="scax-task-table__cell--actions" role="cell">
        {actions}
      </span>
    </div>
  );
}

/**
 * One canonical WorkRequest relationship, listed persistently. These are not AX Actions: they live in the ledger, keep
 * their resolved history, and every row opens the same detail drawer where the round history and decisions are shown.
 */
function RequestRelationSection({
  label,
  hint,
  requests,
  emptyTitle,
  emptyHint,
  people,
  personaId,
  onOpen,
  counterpart,
}: {
  label: string;
  hint: string;
  requests: WorkRequest[];
  emptyTitle: string;
  emptyHint: string;
  people: Persona[];
  personaId: string;
  onOpen: (request: WorkRequest) => void;
  /** Which side of the request the reader is not on. A list names the other party, never a fixed role column. */
  counterpart: "requester" | "assignee" | "both";
}) {
  const who = (memberId: string | null | undefined, fallback: string) => (memberId === personaId ? "나" : displayNameOf(people, memberId, fallback));
  const counterpartLabel = counterpart === "requester" ? "보낸 사람" : counterpart === "assignee" ? "담당자" : "보낸 사람 → 담당자";
  return (
    <section aria-label={label} className="sent-section">
      <h2 className="section-title">
        {label} {hint && <small>{hint}</small>}
      </h2>
      <DataTable>
        <thead>
          <tr>
            <Th>업무명</Th>
            <Th align="center">상태</Th>
            <Th align="center">{counterpartLabel}</Th>
            <Th align="end">액션</Th>
          </tr>
        </thead>
        <tbody>
          {requests.length === 0 && (
            <tr>
              <Td colSpan={5}>
                <Empty description={emptyHint} title={emptyTitle} />
              </Td>
            </tr>
          )}
          {requests.map((request) => (
            <TrOpenable key={request.request_id} onClick={() => onOpen(request)}>
              <Td title>{request.title}</Td>
              <Td align="center">
                <StatusText label={workRequestStateLabel[request.state]} state={request.state} />
              </Td>
              <Td align="center">
                {counterpart === "both"
                  ? `${who(request.requester_id, "보낸 사람")} → ${who(request.assignee_id, "담당자")}`
                  : who(counterpart === "requester" ? request.requester_id : request.assignee_id, counterpartLabel)}
              </Td>
              <Td align="end">
                <Button variant="text" size="sm" onClick={() => onOpen(request)} type="button">
                  상세보기
                </Button>
              </Td>
            </TrOpenable>
          ))}
        </tbody>
      </DataTable>
    </section>
  );
}
