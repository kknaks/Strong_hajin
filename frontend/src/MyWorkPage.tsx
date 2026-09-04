import { useCallback, useEffect, useMemo, useState } from "react";
import { ActionCommandButtons, ActionPreviewDetails, actionKicker, actionSubject } from "./ActionPreview";

import {
  acceptTaskAssignment,
  decideAction,
  declineTaskAssignment,
  getActionInbox,
  getActions,
  getMyWork,
  getSentTaskAssignments,
  getTaskAssignmentCandidates,
  getTaskAssignmentInbox,
  getTasks,
  getWorkRequestAssigneeCandidates,
  getWorkRequestCcCandidates,
  getWorkRequests,
  transitionDirectTask,
  updateTask,
} from "./api";
import { dueDayText, formatDate, isoDateInSeoul, personName, seoulToday, taskStateLabel, workRequestStateLabel } from "./labels";
import { type ActionItem, type DirectTask, type Persona, type TaskAssignment, type TaskPatch, type TaskState, type WorkRequest } from "./viewModels";
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
  const [inbox, setInbox] = useState<WorkRequest[]>([]);
  const [actions, setActions] = useState<ActionItem[]>([]);
  const [allRequests, setAllRequests] = useState<WorkRequest[]>([]);
  const [assigneeCandidates, setAssigneeCandidates] = useState<Persona[]>([]);
  const [assignCandidates, setAssignCandidates] = useState<Persona[]>([]);
  const [ccCandidates, setCcCandidates] = useState<Persona[]>([]);
  const [assignmentInbox, setAssignmentInbox] = useState<TaskAssignment[]>([]);
  const [sentAssignments, setSentAssignments] = useState<TaskAssignment[]>([]);
  const [declining, setDeclining] = useState<{ id: string; reason: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState<TaskFilter>("active");
  const [view, setView] = useState<ViewMode>("list");
  const [tab, setTab] = useState<"mine" | "sent">("mine");
  const [selectedTask, setSelectedTask] = useState<DirectTask | null>(null);
  const [selectedRequest, setSelectedRequest] = useState<WorkRequest | null>(null);
  const [isCreating, setIsCreating] = useState(false);

  const reload = useCallback(async () => {
    const [work, closed, nextInbox, nextActions, requests, nextAssignmentInbox, nextSent] = await Promise.all([
      getMyWork(),
      getTasks(true).catch(() => [] as DirectTask[]),
      canDecideWorkRequests ? getActionInbox() : Promise.resolve([]),
      canReadActions ? getActions() : Promise.resolve([]),
      getWorkRequests().catch(() => [] as WorkRequest[]),
      canManageOwnTasks ? getTaskAssignmentInbox().catch(() => [] as TaskAssignment[]) : Promise.resolve([] as TaskAssignment[]),
      canAssignTasks ? getSentTaskAssignments().catch(() => [] as TaskAssignment[]) : Promise.resolve([] as TaskAssignment[]),
    ]);
    const merged = new Map<string, DirectTask>();
    for (const task of [...work, ...closed]) merged.set(task.task_id, { ...merged.get(task.task_id), ...task });
    const nextTasks = [...merged.values()];
    setTasks(nextTasks);
    setInbox(nextInbox);
    setActions(nextActions.filter((action) => action.state === "pending"));
    setAllRequests(requests);
    setAssignmentInbox(nextAssignmentInbox);
    setSentAssignments(nextSent);
    setSelectedTask((current) => (current ? nextTasks.find((task) => task.task_id === current.task_id) ?? null : null));
    setSelectedRequest((current) =>
      current ? [...nextInbox, ...requests].find((request) => request.request_id === current.request_id) ?? null : null,
    );
  }, [canAssignTasks, canDecideWorkRequests, canManageOwnTasks, canReadActions, personaId]);

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

  const answerAssignment = async (assignment: TaskAssignment, decision: "accept" | "decline", reason?: string) => {
    setBusy(true);
    try {
      if (decision === "accept") await acceptTaskAssignment(assignment.assignment_id);
      else await declineTaskAssignment(assignment.assignment_id, reason ?? "");
      setDeclining(null);
      await reload();
      onError(null);
      onNotice(decision === "accept" ? `'${assignment.task.title}' 배정을 수락했습니다. 내 업무에 들어왔습니다.` : `'${assignment.task.title}' 배정을 거절했습니다.`);
    } catch (error) {
      onError(error instanceof Error ? error.message : "배정을 처리하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

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

  const decideAiAction = async (action: ActionItem, decision: string) => {
    setBusy(true);
    try {
      await decideAction(action.action_id, action.version, decision as "approve" | "reject");
    } catch (error) {
      onError(error instanceof Error ? error.message : "제안을 처리하지 못했습니다.");
      setBusy(false);
      return;
    }
    onError(null);
    // The decision is persisted. Wait for every affected projection to settle before claiming it is reflected;
    // a refresh failure surfaces as a retryable stale banner, not as a failed decision.
    const reflected = await onDecided();
    const subject = actionSubject(action);
    onNotice(
      decision === "approve"
        ? reflected
          ? `'${subject}' 제안을 승인해 반영했습니다.`
          : `'${subject}' 제안을 승인했습니다.`
        : `'${subject}' 제안을 거절했습니다.`,
    );
    setBusy(false);
  };

  const requesterByTask = useMemo(
    () => Object.fromEntries(allRequests.filter((request) => request.task_id && request.requester_id).map((request) => [request.task_id as string, request.requester_id as string])),
    [allRequests],
  );
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
  const sorted = useMemo(() => [...tasks].sort((left, right) => stateOrder[left.state] - stateOrder[right.state]), [tasks]);
  const visibleTasks = useMemo(() => {
    if (filter === "all") return sorted;
    if (filter === "active") return sorted.filter((task) => task.state !== "done" && task.state !== "cancelled");
    return sorted.filter((task) => task.state === filter);
  }, [filter, sorted]);
  const decisionCount = inbox.length + actions.length + assignmentInbox.length;
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
                <p>동료의 요청, 관리자의 배정, AX 제안이 오면 여기에 쌓입니다.</p>
              </div>
            ) : (
              <div className="card-stack">
                {assignmentInbox.map((assignment) => (
                  <TaskCard
                    actions={
                      declining?.id === assignment.assignment_id ? (
                        <div className="inline-reason" style={{ padding: 0 }}>
                          <label className="sr-only" htmlFor={`decline-${assignment.assignment_id}`}>
                            거절 사유
                          </label>
                          <input
                            autoFocus
                            id={`decline-${assignment.assignment_id}`}
                            onChange={(event) => setDeclining({ id: assignment.assignment_id, reason: event.target.value })}
                            onKeyDown={(event) => {
                              if (event.key === "Enter" && declining.reason.trim()) void answerAssignment(assignment, "decline", declining.reason.trim());
                            }}
                            placeholder="거절 사유를 적어 주세요"
                            value={declining.reason}
                          />
                          <button className="btn h30 danger" disabled={busy || !declining.reason.trim()} onClick={() => void answerAssignment(assignment, "decline", declining.reason.trim())} type="button">
                            거절 확정
                          </button>
                          <button className="btn h30 ghost" onClick={() => setDeclining(null)} type="button">
                            취소
                          </button>
                        </div>
                      ) : (
                        <>
                          <button className="btn h30 primary" disabled={busy} onClick={() => void answerAssignment(assignment, "accept")} type="button">
                            배정 수락
                          </button>
                          <button className="btn h30" disabled={busy} onClick={() => setDeclining({ id: assignment.assignment_id, reason: "" })} type="button">
                            거절
                          </button>
                        </>
                      )
                    }
                    date={assignment.task.due_date ? `기한 ${formatDate(assignment.task.due_date)} (${dueDayText(assignment.task.due_date, today)})` : formatDate(isoDateInSeoul(assignment.created_at))}
                    key={assignment.assignment_id}
                    kicker="업무 배정"
                    memo={assignment.task.description}
                    people={<PersonChip arrowTo={me} name={displayNameOf(people, assignment.assigned_by, "관리자")} />}
                    status={<StatusText label="수락 대기" state="pending" />}
                    title={assignment.task.title}
                  />
                ))}
                {inbox.map((request) => (
                  <TaskCard
                    actions={
                      <button className="btn h30 primary" onClick={() => setSelectedRequest(request)} type="button">
                        판단하기
                      </button>
                    }
                    date={request.due_date ? `기한 ${formatDate(request.due_date)} (${dueDayText(request.due_date, today)})` : formatDate(today)}
                    key={request.request_id}
                    kicker="업무 요청"
                    memo={request.description}
                    onOpen={() => setSelectedRequest(request)}
                    people={<PersonChip arrowTo={me} name={displayNameOf(people, request.requester_id, "동료")} />}
                    status={<StatusText label={workRequestStateLabel[request.state]} state={request.state} />}
                    title={request.title}
                  />
                ))}
                {actions.map((action) => (
                  <TaskCard
                    actions={
                      <>
                        <ActionPreviewDetails action={action} />
                        <ActionCommandButtons commands={action.commands} disabled={busy} onCommand={(commandId) => void decideAiAction(action, commandId)} />
                      </>
                    }
                    badge={<span className="badge ai">AI</span>}
                    data-action-id={action.action_id}
                    date={formatDate(today)}
                    key={action.action_id}
                    kicker={actionKicker(action)}
                    people={<PersonChip arrowTo={me} name="AX" />}
                    status={<StatusText label="확인 필요" state="pending" />}
                    title={actionSubject(action)}
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
              emptyTitle="내게 요청된 업무가 없습니다"
              hint="판단이 끝난 요청도 기록으로 남습니다"
              label="내게 요청된 업무"
              onOpen={setSelectedRequest}
              people={people}
              personaId={personaId}
              requests={requestsToMe}
            />
            {canCreateWorkRequests && (
              <RequestRelationSection
                emptyHint="새 업무 추가에서 동료에게 요청할 수 있습니다."
                emptyTitle="내가 요청한 업무가 없습니다"
                hint="조정 요청을 받으면 상세에서 내용을 고쳐 재상신합니다"
                label="내가 요청한 업무"
                onOpen={setSelectedRequest}
                people={people}
                personaId={personaId}
                requests={sentRequests}
              />
            )}
            {canAssignTasks && (
              <section aria-label="내가 배정한 업무" className="sent-section">
                <h2 className="section-title">
                  내가 배정한 업무 <small>수락하면 그 사람의 업무가 됩니다</small>
                </h2>
                <table className="plain-table">
                  <thead>
                    <tr>
                      <th>업무명</th>
                      <th className="center">배정 상태</th>
                      <th className="center">업무 상태</th>
                      <th className="center">담당자</th>
                      <th className="center">기한</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sentAssignments.length === 0 && (
                      <tr>
                        <td colSpan={5}>
                          <div className="empty-state">
                            <b>내가 배정한 업무가 없습니다</b>
                            <p>새 업무 추가에서 담당자를 팀원으로 고르면 배정됩니다.</p>
                          </div>
                        </td>
                      </tr>
                    )}
                    {sentAssignments.map((assignment) => (
                      <tr key={assignment.assignment_id}>
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
                      requester={
                        requesterByTask[task.task_id]
                          ? displayNameOf(people, requesterByTask[task.task_id])
                          : task.assignment?.kind === "direct"
                            ? `${displayNameOf(people, task.assignment.assigned_by, "관리자")} (배정)`
                            : "—"
                      }
                      startDate={formatDate(task.start_date ?? isoDateInSeoul(task.created_at))}
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
          requesterName={
            requesterByTask[selectedTask.task_id]
              ? displayNameOf(people, requesterByTask[selectedTask.task_id])
              : selectedTask.assignment?.kind === "direct"
                ? `${displayNameOf(people, selectedTask.assignment.assigned_by, "관리자")} (배정)`
                : null
          }
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
}: {
  label: string;
  hint: string;
  requests: WorkRequest[];
  emptyTitle: string;
  emptyHint: string;
  people: Persona[];
  personaId: string;
  onOpen: (request: WorkRequest) => void;
}) {
  const who = (memberId: string | null | undefined, fallback: string) => (memberId === personaId ? "나" : displayNameOf(people, memberId, fallback));
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
            <th className="center">담당자</th>
            <th className="center">요청자</th>
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
              <td className="center">{who(request.assignee_id, "담당자")}</td>
              <td className="center">{who(request.requester_id, "요청자")}</td>
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
