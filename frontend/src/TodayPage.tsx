import { useCallback, useEffect, useMemo, useState } from "react";

import {
  decideAction,
  decideWorkRequest,
  getActionInbox,
  getActions,
  getDailyReportStatus,
  getMyWork,
  getWorkRequestAssigneeCandidates,
  getWorkRequests,
  transitionDirectTask,
  updateTask,
} from "./api";
import { dueDayText, formatLongDate, formatMonthDay, isoDateInSeoul, personName, seoulToday, workRequestStateLabel } from "./labels";
import {
  isDirectTask,
  type ActionItem,
  type DailyReportStatus,
  type DirectTask,
  type Persona,
  type ProductSurface,
  type TaskPatch,
  type WorkRequest,
} from "./viewModels";
import {
  CreateWorkDrawer,
  StatusText,
  TaskDetailDrawer,
  WorkRequestDetailDrawer,
  displayNameOf,
  type TaskAction,
} from "./WorkModals";
import { CollapsibleGroup, MetricCard, PersonChip, TaskCard, TaskListRow } from "./WorkViews";

type TodayPageProps = {
  personaId: string;
  personaName: string;
  personas: Persona[];
  canReadActions: boolean;
  canDecideActions: boolean;
  canDecideWorkRequests: boolean;
  canManageOwnTasks: boolean;
  canCreateWorkRequests: boolean;
  canGenerateDailyReport: boolean;
  onAskAx: (message: string) => void;
  onAskAboutTask: (task: DirectTask) => void;
  onNotice: (message: string) => void;
  onError: (message: string | null) => void;
  onNavigate: (surface: ProductSurface) => void;
};

const defaultPrompt = "오늘 업무를 정리해줘";

export function TodayPage({
  personaId,
  personaName,
  personas,
  canReadActions,
  canDecideActions,
  canDecideWorkRequests,
  canManageOwnTasks,
  canCreateWorkRequests,
  canGenerateDailyReport,
  onAskAx,
  onAskAboutTask,
  onNotice,
  onError,
  onNavigate,
}: TodayPageProps) {
  const today = seoulToday();
  const me = personName(personaName);
  const [tasks, setTasks] = useState<DirectTask[]>([]);
  const [requests, setRequests] = useState<WorkRequest[]>([]);
  const [actions, setActions] = useState<ActionItem[]>([]);
  const [requesterByTask, setRequesterByTask] = useState<Record<string, string>>({});
  const [reportStatus, setReportStatus] = useState<DailyReportStatus | null>(null);
  const [prompt, setPrompt] = useState("");
  const [selectedTask, setSelectedTask] = useState<DirectTask | null>(null);
  const [selectedRequest, setSelectedRequest] = useState<WorkRequest | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [assigneeCandidates, setAssigneeCandidates] = useState<Persona[]>([]);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    const [work, inbox, pendingActions, related] = await Promise.all([
      getMyWork(),
      canDecideWorkRequests ? getActionInbox() : Promise.resolve([]),
      canReadActions ? getActions() : Promise.resolve([]),
      getWorkRequests().catch(() => [] as WorkRequest[]),
    ]);
    const nextTasks = work.filter(isDirectTask).filter((task) => ["open", "in_progress", "blocked"].includes(task.state));
    setTasks(nextTasks);
    setRequests(inbox);
    setActions(pendingActions.filter((action) => action.state === "pending"));
    setRequesterByTask(
      Object.fromEntries(related.filter((request) => request.task_id && request.requester_id).map((request) => [request.task_id as string, request.requester_id as string])),
    );
    setSelectedTask((current) => (current ? nextTasks.find((task) => task.task_id === current.task_id) ?? null : null));
    setSelectedRequest((current) => (current ? inbox.find((request) => request.request_id === current.request_id) ?? null : null));
    setReportStatus(canGenerateDailyReport ? await getDailyReportStatus(today) : null);
  }, [canDecideWorkRequests, canGenerateDailyReport, canReadActions, personaId, today]);

  useEffect(() => {
    let cancelled = false;
    void reload()
      .then(() => {
        if (!cancelled) onError(null);
      })
      .catch((error: unknown) => {
        if (!cancelled) onError(error instanceof Error ? error.message : "업무를 불러오지 못했습니다.");
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
      .catch(() => {
        if (!cancelled) setAssigneeCandidates([]);
      });
    return () => {
      cancelled = true;
    };
  }, [canCreateWorkRequests, personaId]);

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

  const acceptRequest = async (request: WorkRequest) => {
    setBusy(true);
    try {
      await decideWorkRequest(request.request_id, "accept", request.version);
      await reload();
      onError(null);
      onNotice(`'${request.title}' 요청을 수락했습니다. 내 업무에 생성되었습니다.`);
    } catch (error) {
      onError(error instanceof Error ? error.message : "요청을 수락하지 못했습니다.");
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

  const groups = useMemo(
    () => ({
      inProgress: tasks.filter((task) => task.state === "in_progress"),
      blocked: tasks.filter((task) => task.state === "blocked"),
      startingToday: tasks.filter((task) => task.state === "open"),
    }),
    [tasks],
  );
  const decisionCount = requests.length + actions.length;
  const canCreate = canManageOwnTasks || canCreateWorkRequests;

  return (
    <>
      <section className="hero">
        <div className="hero-row">
          <div className="hero-identity">
            <span aria-hidden className="avatar xl">
              {me.slice(0, 1)}
            </span>
            <div>
              <h1 className="hero-title">{formatLongDate(today)}</h1>
              <p>반갑습니다 {me}님! 오늘은 어떤 업무를 도와드릴까요?</p>
            </div>
          </div>
          {canCreate && (
            <button className="btn primary" onClick={() => setIsCreating(true)} type="button">
              새 업무 추가
            </button>
          )}
        </div>
        <form
          className="ax-prompt"
          onSubmit={(event) => {
            event.preventDefault();
            onAskAx(prompt.trim() || defaultPrompt);
            setPrompt("");
          }}
        >
          <span aria-hidden className="spark">
            ✦
          </span>
          <label className="sr-only" htmlFor="today-ax-prompt">
            AX에게 오늘 업무 묻기
          </label>
          <input id="today-ax-prompt" onChange={(event) => setPrompt(event.target.value)} placeholder={defaultPrompt} value={prompt} />
          <button aria-label="질문하기" type="submit">
            ➤
          </button>
        </form>
      </section>

      <div className="metric-row">
        <MetricCard icon="→" label="오늘 나에게 요청된 업무" onClick={() => onNavigate("work")} value={decisionCount} />
        <MetricCard icon="▶" label="진행 중인 업무" onClick={() => onNavigate("work")} value={groups.inProgress.length} />
        <MetricCard icon="!" label="막힌 업무" onClick={() => onNavigate("work")} value={groups.blocked.length} />
        <MetricCard icon="○" label="시작 전 업무" onClick={() => onNavigate("work")} value={groups.startingToday.length} />
      </div>

      <div className="home-columns dashboard-columns">
        <section>
          <div className="column-head">
            <h2>
              <span aria-hidden>☑</span> 오늘 나에게 요청된 업무
            </h2>
            <button className="btn link" onClick={() => onNavigate("work")} type="button">
              전체보기
            </button>
          </div>
          {decisionCount === 0 ? (
            <div className="decision-panel">
              <div className="empty-state">
                <b>요청된 업무가 없습니다</b>
                <p>동료의 요청과 AX 제안이 오면 여기에 쌓입니다.</p>
              </div>
            </div>
          ) : (
            <ul className="card-stack surface-card-list">
              {requests.map((request) => (
                <TaskCard
                  actions={
                    canDecideWorkRequests && (
                      <>
                        <button className="btn h30 primary" disabled={busy} onClick={() => void acceptRequest(request)} type="button">
                          수락
                        </button>
                        <button className="btn h30" onClick={() => setSelectedRequest(request)} type="button">
                          검토하기
                        </button>
                      </>
                    )
                  }
                  as="li"
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
                  as="li"
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
            </ul>
          )}
        </section>

        <section>
          <div className="column-head">
            <h2>
              <span aria-hidden>▤</span> 오늘의 업무
            </h2>
            <button className="btn link" onClick={() => onNavigate("work")} type="button">
              전체보기
            </button>
          </div>
          <div className="decision-panel">
            {tasks.length === 0 ? (
              <div className="empty-state">
                <b>등록된 업무가 없습니다</b>
                <p>오늘 할 일을 등록하면 여기에 쌓입니다.</p>
                {canManageOwnTasks && (
                  <button className="btn" onClick={() => setIsCreating(true)} type="button">
                    첫 업무 만들기
                  </button>
                )}
              </div>
            ) : (
              <>
                {groups.blocked.length > 0 && (
                  <CollapsibleGroup count={groups.blocked.length} title="막힌 업무">
                    <ul className="group-body">
                      {groups.blocked.map((task) => (
                        <TaskListRow key={task.task_id} onOpen={() => setSelectedTask(task)} task={task} />
                      ))}
                    </ul>
                  </CollapsibleGroup>
                )}
                <CollapsibleGroup count={groups.inProgress.length} title="진행 중인 업무">
                  <ul className="group-body">
                    {groups.inProgress.length === 0 && <li className="empty-row">진행 중인 업무가 없습니다.</li>}
                    {groups.inProgress.map((task) => (
                      <TaskListRow key={task.task_id} onOpen={() => setSelectedTask(task)} task={task} />
                    ))}
                  </ul>
                </CollapsibleGroup>
                <CollapsibleGroup count={groups.startingToday.length} title="시작 전 업무">
                  <ul className="group-body">
                    {groups.startingToday.length === 0 && <li className="empty-row">시작 전 업무가 없습니다.</li>}
                    {groups.startingToday.map((task) => (
                      <TaskListRow
                        key={task.task_id}
                        onOpen={() => setSelectedTask(task)}
                        right={
                          canManageOwnTasks && (
                            <button className="btn h30 primary" disabled={busy} onClick={() => void transitionTask(task, "start")} type="button">
                              시작
                            </button>
                          )
                        }
                        task={task}
                      />
                    ))}
                  </ul>
                </CollapsibleGroup>
              </>
            )}
          </div>
          {canGenerateDailyReport && (
            <div className="decision-panel schedule">
              <div className="reminder-row">
                <div>
                  <b className="t-item">보고 리마인드</b>
                  <p>{reportReminder(reportStatus)}</p>
                </div>
                <button className="btn primary" onClick={() => onNavigate("report")} type="button">
                  일일보고 작성
                </button>
              </div>
            </div>
          )}
        </section>
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
          }}
          onError={onError}
          ownerName={me}
        />
      )}
    </>
  );
}

function reportReminder(status: DailyReportStatus | null): string {
  if (status?.status === "submitted") return "오늘 보고는 제출되었습니다. 제출 이력을 확인할 수 있습니다.";
  if (status?.status === "draft") return "초안을 편집하거나 제출할 수 있습니다.";
  return "오늘의 업무 기록을 확인한 뒤 일일보고 초안을 만들 수 있습니다.";
}

