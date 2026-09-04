import { useCallback, useEffect, useMemo, useState } from "react";
import { ActionItemCard, ActionItemDrawer } from "./ActionCenter";

import {
  acceptTaskAssignment,
  declineTaskAssignment,
  getActionItems,
  getTask,
  getMyWork,
  getSentTaskAssignments,
  getTaskAssignmentCandidates,
  getTasks,
  getWorkRequestAssigneeCandidates,
  getWorkRequestCcCandidates,
  getWorkRequests,
  transitionDirectTask,
  updateTask,
} from "./api";
import { dueDayText, formatDate, personName, seoulToday, taskStateLabel, workRequestStateLabel } from "./labels";
import { type ActionItemEnvelope, type DirectTask, type Persona, type TaskAssignment, type TaskPatch, type TaskState, type WorkRequest } from "./viewModels";
import {
  CreateWorkDrawer,
  DueText,
  StatusText,
  TaskDetailDrawer,
  TaskQuickActions,
  WorkRequestDetailDrawer,
  displayNameOf,
  type TaskAction,
} from "./WorkModals";
import { ChecklistCue, PersonChip, TaskCard, TaskKanban, TaskTimeline } from "./WorkViews";

type MyWorkPageProps = {
  personaId: string;
  personaName: string;
  personas: Persona[];
  canManageOwnTasks: boolean;
  canAssignTasks?: boolean;
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
};

type TaskFilter = "all" | "active" | TaskState;
type ViewMode = "list" | "timeline" | "kanban";

const stateOrder: Record<TaskState, number> = { blocked: 0, in_progress: 1, open: 2, done: 3, cancelled: 4 };
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
  canCreateWorkRequests,
  canDecideWorkRequests,
  canReadActions,
  onAskAboutTask,
  onNotice,
  onDecided,
  onError,
  onRegisterRefresh,
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
  const [tab, setTab] = useState<"mine" | "sent">("mine");
  const [selectedTask, setSelectedTask] = useState<DirectTask | null>(null);
  const [selectedRequest, setSelectedRequest] = useState<WorkRequest | null>(null);
  const [isCreating, setIsCreating] = useState(false);

  const reload = useCallback(async () => {
    const [work, closed, judgements, requests, nextSent] = await Promise.all([
      getMyWork(),
      getTasks(true).catch(() => [] as DirectTask[]),
      getActionItems(),
      getWorkRequests().catch(() => [] as WorkRequest[]),
      canAssignTasks ? getSentTaskAssignments().catch(() => [] as TaskAssignment[]) : Promise.resolve([] as TaskAssignment[]),
    ]);
    const merged = new Map<string, DirectTask>();
    for (const task of [...work, ...closed]) merged.set(task.task_id, { ...merged.get(task.task_id), ...task });
    const nextTasks = [...merged.values()];
    setTasks(nextTasks);
    setActionItems(judgements);
    setAllRequests(requests);
    setSentAssignments(nextSent);
    setSelectedTask((current) => (current ? nextTasks.find((task) => task.task_id === current.task_id) ?? null : null));
    setSelectedRequest((current) => (current ? requests.find((request) => request.request_id === current.request_id) ?? null : null));
  }, [canAssignTasks, personaId]);

  useEffect(() => {
    let cancelled = false;
    void reload()
      .then(() => {
        if (!cancelled) onError(null);
      })
      .catch((error: unknown) => {
        if (!cancelled) onError(error instanceof Error ? error.message : "내 업무를 불러오지 못했습니다.");
      });
    return () => {
      cancelled = true;
    };
  }, [onError, reload]);

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
      const request = (await getWorkRequests()).find((row) => row.request_id === source.id) ?? null;
      setSelectedTask(null);
      setRelatedTask(null);
      setSelectedRequest(request);
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
  const decisionCount = actionItems.length;
  const canCreate = canManageOwnTasks || canCreateWorkRequests;
  const today = seoulToday();

  return (
    <section className="page-surface">
      <div className="page-head">
        <h1>내 업무</h1>
        <div className="page-head-actions">
          {canCreate && (
            <button className="btn primary" onClick={() => setIsCreating(true)} type="button">
              새 업무 추가
            </button>
          )}
        </div>
      </div>

      <div className="work-layout">
        <aside>
          <div className="column-head">
            <h2>판단이 필요한 업무</h2>
            {decisionCount > 0 && <span className="count-badge">{decisionCount}</span>}
          </div>
          <div className="decision-panel">
            {actionItems.length === 0 ? (
              <div className="empty-state">
                <b>판단할 항목이 없습니다</b>
                <p>동료의 요청, 관리자의 배정, AX 제안이 오면 여기에 쌓입니다.</p>
              </div>
            ) : (
              <div className="card-stack">
                {actionItems.map((item) => (
                  <ActionItemCard item={item} key={item.action_item_id} personas={people} onOpen={setSelectedActionItem} />
                ))}
              </div>
            )}
          </div>
        </aside>

        <div>
          <div className="list-toolbar">
            <div className="page-tabs" role="tablist" aria-label="업무 관점">
              <button aria-selected={tab === "mine"} onClick={() => setTab("mine")} role="tab" type="button">
                할일
              </button>
              {(canCreateWorkRequests || canAssignTasks || requestsToMe.length > 0 || ccRequests.length > 0) && (
                <button aria-selected={tab === "sent"} onClick={() => setTab("sent")} role="tab" type="button">
                  요청·배정
                </button>
              )}
            </div>
            {tab === "mine" && (
              <div className="toolbar-group">
                <label className="sr-only" htmlFor="task-state-filter">
                  상태 필터
                </label>
                <select id="task-state-filter" onChange={(event) => setFilter(event.target.value as TaskFilter)} value={filter}>
                  <option value="active">진행 중·시작 전·막힘</option>
                  <option value="all">전체 상태</option>
                  {(["open", "in_progress", "blocked", "done", "cancelled"] as const).map((state) => (
                    <option key={state} value={state}>
                      {taskStateLabel[state]}
                    </option>
                  ))}
                </select>
                <div aria-label="보기 방식" className="segmented" role="tablist">
                  {views.map((item) => (
                    <button aria-selected={view === item.id} key={item.id} onClick={() => setView(item.id)} role="tab" type="button">
                      {item.label}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>

          {tab === "sent" ? (
            <>
            <RequestRelationSection
              emptyHint="동료가 보낸 요청이 도착하면 여기에 쌓입니다."
              emptyTitle="받은 업무가 없습니다"
              counterpart="requester"
              hint="판단이 끝난 요청도 기록으로 남습니다"
              label="받은 업무"
              onOpen={setSelectedRequest}
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
                onOpen={setSelectedRequest}
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
                <table className="plain-table">
                  <thead>
                    <tr>
                      <th>업무명</th>
                      <th className="center">수락 상태</th>
                      <th className="center">업무 상태</th>
                      <th className="center">담당자</th>
                      <th className="center">기한</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sentAssignments.length === 0 && (
                      <tr>
                        <td colSpan={4}>
                          <div className="empty-state">
                            <b>내가 담당자를 지정한 업무가 없습니다</b>
                            <p>새 업무 추가에서 담당자를 팀원으로 고르면 그 사람에게 갑니다.</p>
                          </div>
                        </td>
                      </tr>
                    )}
                    {sentAssignments.map((assignment) => (
                      <tr className="openable" key={assignment.assignment_id} onClick={() => void openDerivedTask(assignment.task.task_id)}>
                        <td className="title-cell">{assignment.task.title}</td>
                        <td className="center">
                          <StatusText
                            label={assignment.status === "pending" ? "수락 대기" : assignment.status === "active" ? "수락됨" : assignment.status === "declined" ? `거절됨${assignment.decline_reason ? ` · ${assignment.decline_reason}` : ""}` : assignment.status}
                            state={assignment.status === "pending" ? "pending" : assignment.status === "active" ? "accepted" : "rejected"}
                          />
                        </td>
                        <td className="center">
                          <StatusText state={assignment.task.state} />
                        </td>
                        <td className="center">{displayNameOf(people, assignment.assignee_id, "담당자")}</td>
                        <td className="center">{assignment.task.due_date ? formatDate(assignment.task.due_date) : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            )}
            {ccRequests.length > 0 && (
              <RequestRelationSection
                emptyHint=""
                emptyTitle=""
                counterpart="both"
                hint="읽고 논의할 수 있지만 판단은 담당자가 합니다"
                label="참조된 업무"
                onOpen={setSelectedRequest}
                people={people}
                personaId={personaId}
                requests={ccRequests}
              />
            )}
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
            <table className="plain-table">
              <thead>
                <tr>
                  <th>업무명</th>
                  <th className="center">상태</th>
                  <th className="center">시작일</th>
                  <th className="center">기한</th>
                  <th className="center">담당자</th>
                  <th className="center">출처</th>
                  <th className="end">액션</th>
                </tr>
              </thead>
              <tbody>
                {visibleTasks.length === 0 && (
                  <tr>
                    <td colSpan={7}>
                      <div className="empty-state">
                        <b>{filter === "active" || filter === "all" ? "등록된 업무가 없습니다" : "조건에 맞는 업무가 없습니다"}</b>
                        <p>{filter === "active" || filter === "all" ? "오늘 할 일을 등록하면 여기에 쌓입니다." : "다른 상태를 선택해 보세요."}</p>
                        {filter !== "active" && filter !== "all" ? (
                          <button className="btn" onClick={() => setFilter("active")} type="button">
                            필터 초기화
                          </button>
                        ) : (
                          canManageOwnTasks && (
                            <button className="btn" onClick={() => setIsCreating(true)} type="button">
                              첫 업무 만들기
                            </button>
                          )
                        )}
                      </div>
                    </td>
                  </tr>
                )}
                {visibleTasks.map((task) => {
                  return (
                    <TaskTableRow
                      actions={canManageOwnTasks && <TaskQuickActions busy={busy} onTransition={transitionTask} task={task} />}
                      key={task.task_id}
                      onOpen={() => setSelectedTask(task)}
                      requester={task.origin?.actor ? personName(task.origin.actor.display_name) : "—"}
                      startDate={task.start_date ? formatDate(task.start_date) : "—"}
                      task={task}
                      today={today}
                    />
                  );
                })}
              </tbody>
            </table>
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
        <CreateWorkDrawer
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

function TaskTableRow({
  task,
  startDate,
  today,
  requester,
  actions,
  onOpen,
}: {
  task: DirectTask;
  startDate: string;
  today: string;
  requester: string;
  actions: React.ReactNode;
  onOpen: () => void;
}) {
  return (
    <tr
      className="progress-row openable"
      onClick={(event) => {
        if ((event.target as HTMLElement).closest("button, input, select, textarea, a, label")) return;
        onOpen();
      }}
    >
      <td className="title-cell">
        <div className="cell-main">
          <b className={task.state === "cancelled" ? "cancelled-title" : ""}>
            {task.title}
            <ChecklistCue progress={task.checklist_progress} />
          </b>
          {task.block_reason && <small className="reason">막힘 사유: {task.block_reason}</small>}
        </div>
      </td>
      <td className="center">
        <StatusText state={task.state} />
      </td>
      <td className="center">{startDate}</td>
      <td className="center">
        <DueText task={task} today={today} />
      </td>
      <td className="center">나</td>
      <td className="center">{requester}</td>
      <td className="end">
        <div className="task-actions">{actions}</div>
      </td>
    </tr>
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
      <table className="plain-table">
        <thead>
          <tr>
            <th>업무명</th>
            <th className="center">상태</th>
            <th className="center">{counterpartLabel}</th>
            <th className="end">액션</th>
          </tr>
        </thead>
        <tbody>
          {requests.length === 0 && (
            <tr>
              <td colSpan={5}>
                <div className="empty-state">
                  <b>{emptyTitle}</b>
                  <p>{emptyHint}</p>
                </div>
              </td>
            </tr>
          )}
          {requests.map((request) => (
            <tr className="openable" key={request.request_id} onClick={() => onOpen(request)}>
              <td className="title-cell">{request.title}</td>
              <td className="center">
                <StatusText label={workRequestStateLabel[request.state]} state={request.state} />
              </td>
              <td className="center">
                {counterpart === "both"
                  ? `${who(request.requester_id, "보낸 사람")} → ${who(request.assignee_id, "담당자")}`
                  : who(counterpart === "requester" ? request.requester_id : request.assignee_id, counterpartLabel)}
              </td>
              <td className="end">
                <button className="btn h30 ghost" onClick={() => onOpen(request)} type="button">
                  상세보기
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
