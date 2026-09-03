import { useCallback, useEffect, useState } from "react";

import { createDirectTask, getMyWork, transitionDirectTask } from "./api";
import { isDirectTask, type DirectTask, type TaskState } from "./viewModels";

type MyWorkPageProps = {
  personaId: string;
  onError: (message: string | null) => void;
};

type TaskAction = "start" | "block" | "resume" | "complete" | "cancel";
type TaskFilter = "all" | TaskState;

const taskStateLabel: Record<TaskState, string> = {
  open: "열림",
  in_progress: "진행 중",
  blocked: "막힘",
  done: "완료",
  cancelled: "취소",
};

export function MyWorkPage({ personaId, onError }: MyWorkPageProps) {
  const [tasks, setTasks] = useState<DirectTask[]>([]);
  const [title, setTitle] = useState("");
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [filter, setFilter] = useState<TaskFilter>("all");

  const reload = useCallback(async () => {
    try {
      const items = await getMyWork(personaId);
      setTasks(items.filter(isDirectTask));
      onError(null);
    } catch (error) {
      onError(error instanceof Error ? error.message : "내 업무를 불러오지 못했습니다.");
    }
  }, [onError, personaId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const createTask = async () => {
    const trimmedTitle = title.trim();
    if (!trimmedTitle) {
      onError("업무 제목을 입력해 주세요.");
      return;
    }

    setBusyAction("create");
    try {
      await createDirectTask(personaId, trimmedTitle);
      setTitle("");
      await reload();
    } catch (error) {
      onError(error instanceof Error ? error.message : "업무를 만들지 못했습니다.");
    } finally {
      setBusyAction(null);
    }
  };

  const transitionTask = async (task: DirectTask, action: TaskAction) => {
    const reason = action === "block" ? window.prompt("막힘 사유를 입력해 주세요.")?.trim() : undefined;
    if (action === "block" && !reason) return;

    setBusyAction(task.task_id);
    try {
      await transitionDirectTask(personaId, task.task_id, action, task.version, reason);
      await reload();
    } catch (error) {
      onError(error instanceof Error ? error.message : "업무 상태를 바꾸지 못했습니다.");
    } finally {
      setBusyAction(null);
    }
  };

  const visibleTasks = filter === "all" ? tasks : tasks.filter((task) => task.state === filter);

  return (
    <section className="page-surface">
      <div className="card-title">
        <div>
          <p className="kicker">MY WORK</p>
          <h2>내 업무</h2>
          <p>직접 생성한 업무의 상태를 관리합니다.</p>
        </div>
        <button onClick={() => void reload()} type="button">
          새로고침
        </button>
      </div>

      <div className="task-create">
        <label className="sr-only" htmlFor="task-title">
          업무 제목
        </label>
        <input
          id="task-title"
          onChange={(event) => setTitle(event.target.value)}
          placeholder="직접 시작할 업무 제목"
          value={title}
        />
        <button className="primary" disabled={busyAction !== null} onClick={() => void createTask()} type="button">
          {busyAction === "create" ? "추가 중" : "업무 추가"}
        </button>
      </div>

      <div className="work-tabs" role="group" aria-label="업무 상태 필터">
        {(["all", "open", "in_progress", "blocked", "done", "cancelled"] as const).map((state) => (
          <button
            className={filter === state ? "selected-filter" : ""}
            key={state}
            onClick={() => setFilter(state)}
            type="button"
          >
            {state === "all" ? "전체" : taskStateLabel[state]}
          </button>
        ))}
      </div>

      <div className="surface-card">
        {visibleTasks.length === 0 ? (
          <p className="empty-row">표시할 직접 생성 업무가 없습니다.</p>
        ) : (
          visibleTasks.map((task) => (
            <TaskRow busy={busyAction !== null} key={task.task_id} onTransition={transitionTask} task={task} />
          ))
        )}
      </div>
    </section>
  );
}

type TaskRowProps = {
  busy: boolean;
  task: DirectTask;
  onTransition: (task: DirectTask, action: TaskAction) => Promise<void>;
};

function TaskRow({ busy, task, onTransition }: TaskRowProps) {
  return (
    <article className="progress-row">
      <div>
        <b>{task.title}</b>
        {task.block_reason && <small>막힘 사유: {task.block_reason}</small>}
      </div>
      <span className="status completed">{taskStateLabel[task.state]}</span>
      <div className="task-actions">
        {task.state === "open" && (
          <button disabled={busy} onClick={() => void onTransition(task, "start")} type="button">
            시작
          </button>
        )}
        {task.state === "in_progress" && (
          <>
            <button disabled={busy} onClick={() => void onTransition(task, "block")} type="button">
              막힘
            </button>
            <button className="primary" disabled={busy} onClick={() => void onTransition(task, "complete")} type="button">
              완료
            </button>
          </>
        )}
        {task.state === "blocked" && (
          <button className="primary" disabled={busy} onClick={() => void onTransition(task, "resume")} type="button">
            재개
          </button>
        )}
        {task.state !== "done" && task.state !== "cancelled" && (
          <button disabled={busy} onClick={() => void onTransition(task, "cancel")} type="button">
            취소
          </button>
        )}
      </div>
    </article>
  );
}
