import { useCallback, useEffect, useMemo, useState } from "react";

import {
  decideAction,
  getActionInbox,
  getActions,
  getMyWork,
  getTasks,
  getWorkRequestAssigneeCandidates,
  getWorkRequests,
  transitionDirectTask,
  updateTask,
} from "./api";
import { dueDayText, formatMonthDay, isoDateInSeoul, personName, seoulToday, taskStateLabel, workRequestStateLabel } from "./labels";
import { isDirectTask, type ActionItem, type DirectTask, type Persona, type TaskPatch, type TaskState, type WorkRequest } from "./viewModels";
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
import { PersonChip, TaskCard, TaskKanban, TaskTimeline } from "./WorkViews";

type MyWorkPageProps = {
  personaId: string;
  personaName: string;
  personas: Persona[];
  canManageOwnTasks: boolean;
  canCreateWorkRequests: boolean;
  canDecideWorkRequests: boolean;
  canReadActions: boolean;
  canDecideActions: boolean;
  onAskAboutTask: (task: DirectTask) => void;
  onNotice: (message: string) => void;
  onError: (message: string | null) => void;
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
  canCreateWorkRequests,
  canDecideWorkRequests,
  canReadActions,
  canDecideActions,
  onAskAboutTask,
  onNotice,
  onError,
}: MyWorkPageProps) {
  const me = personName(personaName);
  const [tasks, setTasks] = useState<DirectTask[]>([]);
  const [inbox, setInbox] = useState<WorkRequest[]>([]);
  const [actions, setActions] = useState<ActionItem[]>([]);
  const [allRequests, setAllRequests] = useState<WorkRequest[]>([]);
  const [assigneeCandidates, setAssigneeCandidates] = useState<Persona[]>([]);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState<TaskFilter>("active");
  const [view, setView] = useState<ViewMode>("list");
  const [tab, setTab] = useState<"mine" | "sent">("mine");
  const [selectedTask, setSelectedTask] = useState<DirectTask | null>(null);
  const [selectedRequest, setSelectedRequest] = useState<WorkRequest | null>(null);
  const [isCreating, setIsCreating] = useState(false);

  const reload = useCallback(async () => {
    const [work, closed, nextInbox, nextActions, requests] = await Promise.all([
      getMyWork(),
      getTasks(true).catch(() => [] as DirectTask[]),
      canDecideWorkRequests ? getActionInbox() : Promise.resolve([]),
      canReadActions ? getActions() : Promise.resolve([]),
      getWorkRequests().catch(() => [] as WorkRequest[]),
    ]);
    const merged = new Map<string, DirectTask>();
    for (const task of [...work.filter(isDirectTask), ...closed]) merged.set(task.task_id, { ...merged.get(task.task_id), ...task });
    const nextTasks = [...merged.values()];
    setTasks(nextTasks);
    setInbox(nextInbox);
    setActions(nextActions.filter((action) => action.state === "pending"));
    setAllRequests(requests);
    setSelectedTask((current) => (current ? nextTasks.find((task) => task.task_id === current.task_id) ?? null : null));
    setSelectedRequest((current) =>
      current ? [...nextInbox, ...requests].find((request) => request.request_id === current.request_id) ?? null : null,
    );
  }, [canDecideWorkRequests, canReadActions, personaId]);

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

  const decideAiAction = async (action: ActionItem, decision: "approve" | "reject") => {
    setBusy(true);
    try {
      await decideAction(action.action_id, action.version, decision);
      await reload();
      onError(null);
      onNotice(decision === "approve" ? `'${action.title}' 제안을 승인해 반영했습니다.` : `'${action.title}' 제안을 거절했습니다.`);
    } catch (error) {
      onError(error instanceof Error ? error.message : "제안을 처리하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  const requesterByTask = useMemo(
    () => Object.fromEntries(allRequests.filter((request) => request.task_id && request.requester_id).map((request) => [request.task_id as string, request.requester_id as string])),
    [allRequests],
  );
  const sentRequests = allRequests.filter((request) => request.requester_id === personaId);
  const sorted = useMemo(() => [...tasks].sort((left, right) => stateOrder[left.state] - stateOrder[right.state]), [tasks]);
  const visibleTasks = useMemo(() => {
    if (filter === "all") return sorted;
    if (filter === "active") return sorted.filter((task) => task.state !== "done" && task.state !== "cancelled");
    return sorted.filter((task) => task.state === filter);
  }, [filter, sorted]);
  const decisionCount = inbox.length + actions.length;
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
            {decisionCount === 0 ? (
              <div className="empty-state">
                <b>판단할 항목이 없습니다</b>
                <p>동료의 요청과 AX 제안이 오면 여기에 쌓입니다.</p>
              </div>
            ) : (
              <div className="card-stack">
                {inbox.map((request) => (
                  <TaskCard
                    actions={
                      <button className="btn h30 primary" onClick={() => setSelectedRequest(request)} type="button">
                        판단하기
                      </button>
                    }
                    date={request.due_date ? `기한 ${formatMonthDay(request.due_date)} (${dueDayText(request.due_date, today)})` : formatMonthDay(today)}
                    key={request.request_id}
                    kicker="업무 요청"
                    memo={request.description}
                    onOpen={() => setSelectedRequest(request)}
                    people={<PersonChip arrowTo={me} name={displayNameOf(personas, request.requester_id, "동료")} />}
                    status={<StatusText label={workRequestStateLabel[request.state]} state={request.state} />}
                    title={request.title}
                  />
                ))}
                {actions.map((action) => (
                  <TaskCard
                    actions={
                      canDecideActions && (
                        <>
                          <button className="btn h30 primary" disabled={busy} onClick={() => void decideAiAction(action, "approve")} type="button">
                            승인
                          </button>
                          <button className="btn h30" disabled={busy} onClick={() => void decideAiAction(action, "reject")} type="button">
                            거절
                          </button>
                        </>
                      )
                    }
                    badge={<span className="badge ai">AI</span>}
                    data-action-id={action.action_id}
                    date={formatMonthDay(today)}
                    key={action.action_id}
                    kicker="AX 제안"
                    memo={action.payload_summary}
                    people={<PersonChip arrowTo={me} name="AX" />}
                    status={<StatusText label="확인 필요" state="pending" />}
                    title={action.title}
                  />
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
              {canCreateWorkRequests && (
                <button aria-selected={tab === "sent"} onClick={() => setTab("sent")} role="tab" type="button">
                  보낸 업무
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
            <table className="plain-table">
              <thead>
                <tr>
                  <th>업무명</th>
                  <th className="center">상태</th>
                  <th className="center">담당자</th>
                  <th className="center">요청자</th>
                  <th className="end">액션</th>
                </tr>
              </thead>
              <tbody>
                {sentRequests.length === 0 && (
                  <tr>
                    <td colSpan={5}>
                      <div className="empty-state">
                        <b>보낸 요청이 없습니다</b>
                        <p>새 업무 추가에서 동료에게 요청할 수 있습니다.</p>
                      </div>
                    </td>
                  </tr>
                )}
                {sentRequests.map((request) => (
                  <tr className="openable" key={request.request_id} onClick={() => setSelectedRequest(request)}>
                    <td className="title-cell">{request.title}</td>
                    <td className="center">
                      <StatusText label={workRequestStateLabel[request.state]} state={request.state} />
                    </td>
                    <td className="center">{displayNameOf(personas, request.assignee_id, "담당자")}</td>
                    <td className="center">나</td>
                    <td className="end">
                      <button className="btn h30 ghost" onClick={() => setSelectedRequest(request)} type="button">
                        상세보기
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
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
                  <th className="center">요청자</th>
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
                      requester={requesterByTask[task.task_id] ? displayNameOf(personas, requesterByTask[task.task_id]) : "—"}
                      startDate={formatMonthDay(task.start_date ?? isoDateInSeoul(task.created_at))}
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
          onTransition={transitionTask}
          onUpdate={updateTaskFields}
          ownerName={me}
          requesterName={requesterByTask[selectedTask.task_id] ? displayNameOf(personas, requesterByTask[selectedTask.task_id]) : null}
          task={selectedTask}
        />
      )}
      {selectedRequest && (
        <WorkRequestDetailDrawer
          canDecide={canDecideWorkRequests}
          onChanged={reload}
          onClose={() => setSelectedRequest(null)}
          onError={onError}
          onNotice={onNotice}
          personaId={personaId}
          personas={personas}
          request={selectedRequest}
        />
      )}
      {isCreating && (
        <CreateWorkDrawer
          assigneeCandidates={assigneeCandidates}
          canCreateRequest={canCreateWorkRequests}
          canCreateTask={canManageOwnTasks}
          onClose={() => setIsCreating(false)}
          onCreated={async (message) => {
            await reload();
            onNotice(message);
            if (message.includes("요청을")) setTab("sent");
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
          <b className={task.state === "cancelled" ? "cancelled-title" : ""}>{task.title}</b>
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
