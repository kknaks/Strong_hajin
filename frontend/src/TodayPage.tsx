import { useCallback, useEffect, useMemo, useState } from "react";
import { ActionItemCard, ActionItemDrawer } from "./ActionCenter";

import {
  decideWorkRequest,
  getActionItems,
  getDailyReportStatus,
  getMyWork,
  getWorkRequestAssigneeCandidates,
  getWorkRequests,
  transitionDirectTask,
  updateTask,
} from "./api";
import { dueDayText, formatLongDate, formatDate, isoDateInSeoul, personName, seoulToday, workRequestStateLabel } from "./labels";
import {
  type ActionItemEnvelope,
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
  canDecideWorkRequests: boolean;
  canManageOwnTasks: boolean;
  canCreateWorkRequests: boolean;
  canGenerateDailyReport: boolean;
  onAskAx: (message: string) => void;
  onAskAboutTask: (task: DirectTask) => void;
  onNotice: (message: string) => void;
  /** Settles every projection an approved effect may have changed. Never throws; returns false when a read failed. */
  onDecided: () => Promise<boolean>;
  onError: (message: string | null) => void;
  /** Registers this surface's reload so the shell can await it after an approved AX effect (no remount). */
  onRegisterRefresh?: (refresh: (() => Promise<void>) | null) => void;
  onNavigate: (surface: ProductSurface) => void;
};

const defaultPrompt = "오늘 업무를 정리해줘";

export function TodayPage({
  personaId,
  personaName,
  personas,
  canReadActions,
  canDecideWorkRequests,
  canManageOwnTasks,
  canCreateWorkRequests,
  canGenerateDailyReport,
  onAskAx,
  onAskAboutTask,
  onNotice,
  onDecided,
  onError,
  onRegisterRefresh,
  onNavigate,
}: TodayPageProps) {
  const today = seoulToday();
  const me = personName(personaName);
  const [tasks, setTasks] = useState<DirectTask[]>([]);
  const [actionItems, setActionItems] = useState<ActionItemEnvelope[]>([]);
  const [selectedActionItem, setSelectedActionItem] = useState<ActionItemEnvelope | null>(null);
  const [requesterByTask, setRequesterByTask] = useState<Record<string, string>>({});
  const [reportStatus, setReportStatus] = useState<DailyReportStatus | null>(null);
  const [prompt, setPrompt] = useState("");
  const [selectedTask, setSelectedTask] = useState<DirectTask | null>(null);
  const [selectedRequest, setSelectedRequest] = useState<WorkRequest | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [assigneeCandidates, setAssigneeCandidates] = useState<Persona[]>([]);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    const [work, judgements, related] = await Promise.all([
      getMyWork(),
      getActionItems(),
      getWorkRequests().catch(() => [] as WorkRequest[]),
    ]);
    const nextTasks = work.filter((task) => ["open", "in_progress", "blocked"].includes(task.state));
    setTasks(nextTasks);
    setActionItems(judgements);
    setRequesterByTask(
      Object.fromEntries(related.filter((request) => request.task_id && request.requester_id).map((request) => [request.task_id as string, request.requester_id as string])),
    );
    setSelectedTask((current) => (current ? nextTasks.find((task) => task.task_id === current.task_id) ?? null : null));
    setSelectedRequest((current) => (current ? related.find((request) => request.request_id === current.request_id) ?? null : null));
    setReportStatus(canGenerateDailyReport ? await getDailyReportStatus(today) : null);
  }, [canGenerateDailyReport, personaId, today]);

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


  const groups = useMemo(
    () => ({
      inProgress: tasks.filter((task) => task.state === "in_progress"),
      blocked: tasks.filter((task) => task.state === "blocked"),
      startingToday: tasks.filter((task) => task.state === "open"),
    }),
    [tasks],
  );
  const decisionCount = actionItems.length;
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
              {actionItems.map((item) => (
                <ActionItemCard item={item} key={item.action_item_id} personas={personas} onOpen={setSelectedActionItem} />
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
      {selectedActionItem && (
        <ActionItemDrawer
          actionItemId={selectedActionItem?.action_item_id ?? ""}
          key={selectedActionItem?.action_item_id}
          onClose={() => setSelectedActionItem(null)}
          onDone={onDecided}
          onError={onError}
          onNotice={onNotice}
          personas={personas}
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

