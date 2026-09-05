import { useCallback, useEffect, useState } from "react";

import { getCalendarEntries, getMyWork, getTask, getTasks, transitionDirectTask, updateTask } from "./api";
import { formatDateTime, personName } from "./labels";
import { MeetingDrawer } from "./MeetingDrawer";
import type { CalendarEntry, DirectTask, Persona, TaskPatch } from "./viewModels";
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

export function CalendarPage({ personaId, personaName, canManageOwnTasks, onAskAboutTask, onNotice, onError, onRegisterRefresh }: CalendarPageProps) {
  const [tasks, setTasks] = useState<DirectTask[]>([]);
  const [mode, setMode] = useState<"week" | "month">("month");
  const [selected, setSelected] = useState<DirectTask | null>(null);
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState<"calendar" | "meetings">("calendar");
  const [entries, setEntries] = useState<CalendarEntry[] | null>(null);
  const [selectedMeeting, setSelectedMeeting] = useState<string | null>(null);

  const loadMeetings = useCallback(async () => {
    setEntries(await getCalendarEntries());
  }, []);

  useEffect(() => {
    let cancelled = false;
    void getCalendarEntries()
      .then((rows) => {
        if (!cancelled) setEntries(rows);
      })
      .catch(() => {
        if (!cancelled) setEntries([]);
      });
    return () => {
      cancelled = true;
    };
  }, [personaName]);

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
          <p>업무는 계획한 날짜로만 표시합니다. 시작일과 기한이 모두 있으면 그 기간, 하나만 있으면 그 날 하루, 없으면 캘린더에 두지 않습니다. 회의는 SCAX가 직접 소유합니다.</p>
        </div>
        <div className="page-head-actions">
          <div aria-label="캘린더 표시 방식" className="segmented" role="tablist">
            <button aria-selected={view === "calendar"} onClick={() => setView("calendar")} role="tab" type="button">
              캘린더 보기
            </button>
            <button aria-selected={view === "meetings"} onClick={() => setView("meetings")} role="tab" type="button">
              회의 목록
            </button>
          </div>
        </div>
      </div>
      {view === "calendar" ? (
        <TaskCalendar mode={mode} onModeChange={setMode} onOpen={setSelected} tasks={tasks} />
      ) : (
        <MeetingList entries={entries} onOpen={setSelectedMeeting} />
      )}
      {selectedMeeting && (
        <MeetingDrawer
          meetingId={selectedMeeting}
          onChanged={loadMeetings}
          onClose={() => setSelectedMeeting(null)}
          onError={onError}
          onNotice={onNotice}
          onOpenTask={(taskId) => {
            // The work a followup became is opened here, where the person already is.
            setSelectedMeeting(null);
            void getTask(taskId)
              .then(setSelected)
              .catch((error: unknown) => onError(error instanceof Error ? error.message : "업무를 열지 못했습니다."));
          }}
          personaId={personaId}
        />
      )}
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

/**
 * The meeting list shows exactly what the server allowed. A concealed private meeting arrives as a busy block with a
 * time and nothing else, and is rendered as such rather than as a meeting with hidden fields.
 */
function MeetingList({ entries, onOpen }: { entries: CalendarEntry[] | null; onOpen: (meetingId: string) => void }) {
  if (entries === null) return <p className="t-meta">회의를 불러오는 중…</p>;
  if (entries.length === 0) {
    return (
      <div className="empty-state">
        <b>회의가 없습니다</b>
        <p>일정이 잡히면 여기에 쌓입니다.</p>
      </div>
    );
  }
  return (
    <ul aria-label="회의 목록" className="meeting-list">
      {entries.map((entry, index) =>
        entry.kind === "meeting" ? (
          <li className="meeting-row openable" data-meeting-id={entry.meeting_id} key={entry.meeting_id} onClick={() => onOpen(entry.meeting_id)}>
            <div className="cell-main">
              <b>{entry.title}</b>
              <small>
                {formatDateTime(entry.starts_at)} · {entry.visibility === "public" ? "조직 공개" : "비공개"} ·{" "}
                {entry.attendees.map((attendee) => personName(attendee.display_name)).join(", ") || "참석자 없음"}
              </small>
            </div>
            <button className="btn h30 ghost" onClick={() => onOpen(entry.meeting_id)} type="button">
              상세보기
            </button>
          </li>
        ) : (
          <li className="meeting-row busy" key={`busy-${index}`}>
            <div className="cell-main">
              <b>다른 일정</b>
              <small>
                {formatDateTime(entry.starts_at)} · 이 시간에 다른 일정이 있습니다
              </small>
            </div>
          </li>
        ),
      )}
    </ul>
  );
}
