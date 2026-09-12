import { useCallback, useEffect, useState } from "react";

import { getMyWork, getTasks, transitionDirectTask, updateTask } from "./api";
import { personName } from "./labels";
import type { DirectTask, Persona, TaskPatch } from "./viewModels";
import { TaskDetailDrawer, type TaskAction } from "./WorkModals";
import { TaskCalendar } from "./WorkViews";

type CalendarPageProps = {
  personaId: string;
  personaName: string;
  personas: Persona[];
  canManageOwnTasks: boolean;
  onAskAboutTask: (task: DirectTask) => void;
  onNotice: (message: string) => void;
  onError: (message: string | null) => void;
  /** Registers this surface's reload so the shell can await it after an approved AX effect (no remount). */
  onRegisterRefresh?: (refresh: (() => Promise<void>) | null) => void;
};

export function CalendarPage({
  personaName,
  canManageOwnTasks,
  onAskAboutTask,
  onNotice,
  onError,
  onRegisterRefresh,
}: CalendarPageProps) {
  const [tasks, setTasks] = useState<DirectTask[]>([]);
  const [mode, setMode] = useState<"week" | "month">("month");
  const [selected, setSelected] = useState<DirectTask | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    const [work, closed] = await Promise.all([getMyWork(), getTasks(true).catch(() => [] as DirectTask[])]);
    const merged = new Map<string, DirectTask>();
    for (const task of [...work, ...closed]) merged.set(task.task_id, { ...merged.get(task.task_id), ...task });
    const next = [...merged.values()];
    setTasks(next);
    setSelected((current) => (current ? next.find((task) => task.task_id === current.task_id) ?? null : null));
  }, []);

  useEffect(() => {
    let cancelled = false;
    void reload()
      .then(() => {
        if (!cancelled) onError(null);
      })
      .catch((error: unknown) => {
        if (!cancelled) onError(error instanceof Error ? error.message : "캘린더를 불러오지 못했습니다.");
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

  const transition = async (task: DirectTask, action: TaskAction, reason?: string) => {
    setBusy(true);
    try {
      await transitionDirectTask(task.task_id, action, task.version, reason);
      await reload();
      onError(null);
      onNotice("상태를 바꿨습니다.");
    } catch (error) {
      onError(error instanceof Error ? error.message : "업무 상태를 바꾸지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  const update = async (task: DirectTask, patch: TaskPatch) => {
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

  return (
    <section className="page-surface">
      <div className="page-head">
        <div>
          <h1>캘린더</h1>
          {/* 회의는 이제 좌측 메뉴 「회의 목록」 아래 전체 화면 둘이 맡는다 — 이 화면은 업무만 낸다. */}
          <p>업무는 계획한 날짜로만 표시합니다. 시작일과 기한이 모두 있으면 그 기간, 하나만 있으면 그 날 하루, 없으면 캘린더에 두지 않습니다.</p>
        </div>
      </div>
      <TaskCalendar mode={mode} onModeChange={setMode} onOpen={setSelected} tasks={tasks} />
      {selected && (
        <TaskDetailDrawer
          busy={busy}
          canManage={canManageOwnTasks}
          onAskAx={onAskAboutTask}
          onClose={() => setSelected(null)}
          onError={onError}
          onNotice={onNotice}
          onTransition={transition}
          onUpdate={update}
          ownerName={personName(personaName)}
          task={selected}
        />
      )}
    </section>
  );
}
