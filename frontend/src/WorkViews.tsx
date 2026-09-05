import { useMemo, useState, type DragEvent, type ReactNode } from "react";

import { addDays, dayDifference, dueDayText, formatDate, formatMonth, isOverdue, isoDateInSeoul, seoulToday, taskStateLabel } from "./labels";
import type { DirectTask, TaskState } from "./viewModels";
import { StatusText, type TaskAction } from "./WorkModals";

/* ---------------------------------------------------------------- shared small pieces */

export function MetricCard({ label, value, icon, onClick }: { label: string; value: number; icon?: string; onClick?: () => void }) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag className="metric-card" onClick={onClick} type={onClick ? "button" : undefined}>
      <span className="metric-label">
        {icon && <span aria-hidden className="metric-icon">{icon}</span>}
        {label}
      </span>
      <b className="metric-value">{value}</b>
    </Tag>
  );
}

export function TaskCard({
  kicker,
  title,
  memo,
  memoTone,
  date,
  people,
  status,
  badge,
  onOpen,
  actions,
  as: Tag = "article",
  ...rest
}: {
  kicker: string;
  title: string;
  memo?: string | null;
  memoTone?: "danger";
  date?: string | null;
  people?: ReactNode;
  status: ReactNode;
  badge?: ReactNode;
  onOpen?: () => void;
  actions?: ReactNode;
  as?: "article" | "li";
  [key: `data-${string}`]: string | undefined;
}) {
  return (
    <Tag
      className={onOpen ? "task-card openable" : "task-card"}
      onClick={(event) => {
        if (!onOpen) return;
        if ((event.target as HTMLElement).closest("button, input, select, textarea, a, label")) return;
        onOpen();
      }}
      {...rest}
    >
      <div className="task-card-top">
        <small className="task-card-kicker">{kicker}</small>
        {status}
      </div>
      <b className="task-card-title">
        {badge}
        {title}
      </b>
      {memo && <p className={memoTone === "danger" ? "task-card-memo danger-text" : "task-card-memo"}>{memo}</p>}
      <div className="task-card-foot">
        <span>{date ?? "—"}</span>
        <span className="task-card-people">{people}</span>
      </div>
      {actions && <div className="task-card-actions">{actions}</div>}
    </Tag>
  );
}

export function PersonChip({ name, arrowTo }: { name: string; arrowTo?: string }) {
  return (
    <span className="person-chip">
      <span className="avatar xs" aria-hidden>
        {name.slice(0, 1)}
      </span>
      {name}
      {arrowTo && (
        <>
          <span aria-hidden className="person-arrow">
            →
          </span>
          <span className="avatar xs" aria-hidden>
            {arrowTo.slice(0, 1)}
          </span>
          {arrowTo}
        </>
      )}
    </span>
  );
}

export function CollapsibleGroup({
  title,
  count,
  defaultOpen = true,
  children,
}: {
  title: string;
  count: number;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="group">
      <button aria-expanded={open} className="group-head" onClick={() => setOpen((value) => !value)} type="button">
        <span aria-hidden className={open ? "caret open" : "caret"}>
          ▾
        </span>
        {title}
        <span className="count-badge">{count}</span>
      </button>
      {open && <div className="group-body">{children}</div>}
    </section>
  );
}

/* ---------------------------------------------------------------- list rows used on home */

/** Compact cue that a Task has steps inside it, and how far along they are. Hidden when there are none. */
export function ChecklistCue({ progress }: { progress?: { done: number; total: number } }) {
  if (!progress || progress.total === 0) return null;
  const complete = progress.done === progress.total;
  return (
    <span className={complete ? "checklist-cue complete" : "checklist-cue"} title={`체크리스트 ${progress.done}/${progress.total}`}>
      {complete ? "☑" : "☐"} {progress.done}/{progress.total}
    </span>
  );
}

export function TaskListRow({ task, onOpen, right }: { task: DirectTask; onOpen: () => void; right?: ReactNode }) {
  return (
    <li className="task-row openable" onClick={onOpen}>
      <div className="cell-main">
        <b>
          {task.title}
          <ChecklistCue progress={task.checklist_progress} />
        </b>
        <small className={task.block_reason ? "reason" : ""}>
          {task.block_reason
            ? `막힘 사유: ${task.block_reason}`
            : task.due_date
              ? `기한 ${formatDate(task.due_date)} (${dueDayText(task.due_date, seoulToday())})`
              : task.start_date
                ? `${formatDate(task.start_date)} 시작`
                : `${formatDate(isoDateInSeoul(task.created_at))} 등록`}
        </small>
      </div>
      <div className="task-row-right">
        {isOverdue(task, seoulToday()) && <span className="badge danger">기한 초과</span>}
        <StatusText state={task.state} />
        {right}
      </div>
    </li>
  );
}

/* ---------------------------------------------------------------- calendar view (month) */

/**
 * Where a Task sits on a calendar, from its planned dates alone.
 *
 * Both dates give a range that includes both ends; one date gives that single day. A Task with neither is not on the
 * calendar at all — when it was written down is not a plan, and "until today" is not a deadline.
 */
export function taskSpan(task: DirectTask): { start: string; end: string } | null {
  const start = task.start_date ?? task.due_date ?? null;
  const end = task.due_date ?? task.start_date ?? null;
  if (!start || !end) return null;
  // The server refuses a start after a due date; the renderer does not quietly correct one.
  return { start, end: end < start ? start : end };
}

export type CalendarSegment = {
  task: DirectTask;
  /** 1-based grid column of the first day this week shows, and how many days it covers here. */
  column: number;
  length: number;
  lane: number;
  continuesBefore: boolean;
  continuesAfter: boolean;
  span: { start: string; end: string };
};

/**
 * One bar per Task per week: clipped to the week, marked where it continues, and placed in a lane it keeps for as
 * long as nothing else needs it. Bars past `maxLanes` are not dropped silently — each day counts what it is hiding.
 */
export function weekSegments(
  tasks: DirectTask[],
  days: string[],
  maxLanes: number,
): { segments: CalendarSegment[]; hiddenByDay: Record<string, number> } {
  const weekStart = days[0];
  const weekEnd = days[days.length - 1];
  const placed: CalendarSegment[] = [];
  const hiddenByDay: Record<string, number> = {};
  // Longest first, then by start, so the bars that shape the week take the top lanes and stay put.
  const candidates = tasks
    .map((task) => ({ task, span: taskSpan(task) }))
    .filter((row): row is { task: DirectTask; span: { start: string; end: string } } => row.span !== null)
    .filter((row) => row.span.start <= weekEnd && row.span.end >= weekStart);
  const lanes: Array<string | null> = [];

  for (const { task, span } of candidates) {
    const from = span.start < weekStart ? weekStart : span.start;
    const to = span.end > weekEnd ? weekEnd : span.end;
    const column = days.indexOf(from) + 1;
    const length = days.indexOf(to) - days.indexOf(from) + 1;
    let lane = lanes.findIndex((occupiedUntil) => occupiedUntil === null || occupiedUntil < from);
    if (lane === -1) {
      lane = lanes.length;
      lanes.push(null);
    }
    if (lane >= maxLanes) {
      for (let offset = 0; offset < length; offset += 1) {
        const day = days[column - 1 + offset];
        hiddenByDay[day] = (hiddenByDay[day] ?? 0) + 1;
      }
      continue;
    }
    lanes[lane] = to;
    placed.push({
      task,
      column,
      length,
      lane,
      continuesBefore: span.start < weekStart,
      continuesAfter: span.end > weekEnd,
      span,
    });
  }
  return { segments: placed, hiddenByDay };
}

/** What a bar says out loud: the work, what it covers, and where it stands. */
function segmentLabel(segment: CalendarSegment): string {
  const { span, task } = segment;
  const dates =
    span.start === span.end
      ? task.due_date && !task.start_date
        ? `${formatDate(span.start)} 기한`
        : `${formatDate(span.start)} 시작`
      : `${formatDate(span.start)} – ${formatDate(span.end)}`;
  return `${task.title} · ${dates} · ${taskStateLabel[task.state]}`;
}

export function TaskCalendar({
  tasks,
  onOpen,
  mode = "month",
  onModeChange,
  anchorDate,
}: {
  tasks: DirectTask[];
  onOpen: (task: DirectTask) => void;
  mode?: "week" | "month";
  onModeChange?: (mode: "week" | "month") => void;
  /** Where the grid opens. Defaults to today; tests and deep links can start elsewhere. */
  anchorDate?: string;
}) {
  const today = seoulToday();
  const [anchor, setAnchor] = useState(anchorDate ?? today);
  const [year, month] = anchor.slice(0, 7).split("-").map(Number);
  const cursor = anchor.slice(0, 7);
  const first = `${cursor}-01`;
  const firstWeekday = new Date(Date.UTC(year, month - 1, 1)).getUTCDay();
  const anchorWeekday = new Date(Date.UTC(year, month - 1, Number(anchor.slice(8)))).getUTCDay();
  const gridStart = mode === "week" ? addDays(anchor, -anchorWeekday) : addDays(first, -firstWeekday);
  const days = Array.from({ length: mode === "week" ? 7 : 42 }, (_, index) => addDays(gridStart, index));
  const maxLanes = mode === "week" ? 8 : 3;
  const weeks = useMemo(() => {
    const rows: string[][] = [];
    for (let index = 0; index < days.length; index += 7) rows.push(days.slice(index, index + 7));
    return rows.map((week) => ({ days: week, ...weekSegments(tasks, week, maxLanes) }));
  }, [tasks, days.join(","), maxLanes]);

  const shift = (delta: number) => {
    if (mode === "week") {
      setAnchor(addDays(anchor, delta * 7));
      return;
    }
    const date = new Date(Date.UTC(year, month - 1 + delta, 1));
    setAnchor(date.toISOString().slice(0, 10));
  };
  const rangeLabel =
    mode === "week" ? `${formatDate(days[0])} – ${formatDate(days[6])}` : formatMonth(year, month);

  return (
    <div className="calendar">
      <div className="calendar-toolbar">
        <div className="stepper">
          <button aria-label={mode === "week" ? "이전 주" : "이전 달"} className="btn h30 icon" onClick={() => shift(-1)} type="button">
            ‹
          </button>
          <b>{rangeLabel}</b>
          <button aria-label={mode === "week" ? "다음 주" : "다음 달"} className="btn h30 icon" onClick={() => shift(1)} type="button">
            ›
          </button>
        </div>
        <div className="toolbar-group">
          {onModeChange && (
            <div aria-label="캘린더 보기" className="segmented" role="tablist">
              <button aria-selected={mode === "week"} onClick={() => onModeChange("week")} role="tab" type="button">
                주
              </button>
              <button aria-selected={mode === "month"} onClick={() => onModeChange("month")} role="tab" type="button">
                월
              </button>
            </div>
          )}
          <button className="btn h30" onClick={() => setAnchor(today)} type="button">
            오늘
          </button>
        </div>
      </div>
      <div className="calendar-weekdays">
        {["일", "월", "화", "수", "목", "금", "토"].map((label) => (
          <span key={label}>{label}</span>
        ))}
      </div>
      <div aria-label="업무 캘린더" className={mode === "week" ? "calendar-grid week" : "calendar-grid"} role="grid">
        {weeks.map((week) => (
          <div className="calendar-week" key={week.days[0]}>
            <div className="calendar-days">
              {week.days.map((day) => {
                const inMonth = mode === "week" || day.startsWith(cursor);
                const hidden = inMonth ? week.hiddenByDay[day] ?? 0 : 0;
                return (
                  <div
                    className={["calendar-cell", inMonth ? "" : "outside", day === today ? "today" : ""].filter(Boolean).join(" ")}
                    key={day}
                    role="gridcell"
                  >
                    <span className="calendar-day">{Number(day.slice(8))}</span>
                    {hidden > 0 && <span className="calendar-more">+{hidden}개 더</span>}
                  </div>
                );
              })}
            </div>
            {/* One bar per task per week, laid over the day cells so a range reads as a single piece of work. */}
            <div className="calendar-bars">
              {week.segments
                .filter((segment) => mode === "week" || week.days[segment.column - 1].startsWith(cursor) || segment.length > 1)
                .map((segment) => (
                  <button
                    aria-label={segmentLabel(segment)}
                    className={[
                      "calendar-chip",
                      segment.task.state,
                      segment.continuesBefore ? "continues-before" : "",
                      segment.continuesAfter ? "continues-after" : "",
                    ]
                      .filter(Boolean)
                      .join(" ")}
                    key={`${segment.task.task_id}-${segment.column}`}
                    onClick={() => onOpen(segment.task)}
                    style={{ gridColumn: `${segment.column} / span ${segment.length}`, gridRow: segment.lane + 1 }}
                    title={segmentLabel(segment)}
                    type="button"
                  >
                    {segment.continuesBefore ? "‹ " : ""}
                    {segment.task.title}
                    {segment.continuesAfter ? " ›" : ""}
                  </button>
                ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- timeline view (2 weeks) */

export function TaskTimeline({ tasks, onOpen }: { tasks: DirectTask[]; onOpen: (task: DirectTask) => void }) {
  const today = seoulToday();
  const [offset, setOffset] = useState(0);
  const windowStart = addDays(today, -7 + offset * 14);
  const days = Array.from({ length: 14 }, (_, index) => addDays(windowStart, index));
  const windowEnd = days[days.length - 1];

  return (
    <div className="timeline">
      <div className="calendar-toolbar">
        <div className="stepper">
          <button aria-label="이전 2주" className="btn h30 icon" onClick={() => setOffset((value) => value - 1)} type="button">
            ‹
          </button>
          <b>
            {formatDate(windowStart)} – {formatDate(windowEnd)}
          </b>
          <button aria-label="다음 2주" className="btn h30 icon" onClick={() => setOffset((value) => value + 1)} type="button">
            ›
          </button>
        </div>
        <div className="timeline-legend">
          <span className="status in_progress">진행 중</span>
          <span className="status blocked">막힘</span>
          <span className="status done">완료</span>
          <span className="status open">시작 전</span>
        </div>
      </div>
      <div className="timeline-grid" style={{ ["--days" as string]: days.length }}>
        <div className="timeline-head">
          <span className="timeline-label-col">업무명</span>
          {days.map((day) => (
            <span className={day === today ? "today" : ""} key={day}>
              {Number(day.slice(8))}
            </span>
          ))}
        </div>
        {tasks.length === 0 && <p className="empty-row">이 기간에 표시할 업무가 없습니다.</p>}
        {tasks.map((task) => {
          // A bar needs planned dates. Work nobody has scheduled still belongs on this list, without a made-up span.
          const span = taskSpan(task);
          const startIndex = span ? Math.max(0, dayDifference(windowStart, span.start)) : 0;
          const endIndex = span ? Math.min(days.length - 1, dayDifference(windowStart, span.end)) : -1;
          const visible = Boolean(span) && startIndex <= days.length - 1 && endIndex >= 0;
          return (
            <div className="timeline-row" key={task.task_id}>
              <button className="timeline-label-col timeline-title" onClick={() => onOpen(task)} type="button">
                <b className={task.state === "cancelled" ? "cancelled-title" : ""}>{task.title}</b>
                <small>
                  {span
                    ? span.start === span.end
                      ? formatDate(span.start)
                      : `${formatDate(span.start)} → ${formatDate(span.end)}`
                    : "날짜 없음"}
                  {task.due_date && ` · ${dueDayText(task.due_date, today)}`}
                </small>
              </button>
              <div className="timeline-track">
                {days.map((day, index) => (
                  <span className={day === today ? "timeline-day today" : "timeline-day"} key={day} style={{ gridColumn: index + 1 }} />
                ))}
                {visible && (
                  <button
                    className={`timeline-bar ${task.state}`}
                    onClick={() => onOpen(task)}
                    style={{ gridColumn: `${startIndex + 1} / ${endIndex + 2}` }}
                    title={`${task.title} · ${taskStateLabel[task.state]}`}
                    type="button"
                  >
                    {taskStateLabel[task.state]}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- kanban board */

const kanbanColumns: Array<{ state: TaskState; title: string }> = [
  { state: "open", title: "시작 전" },
  { state: "in_progress", title: "진행 중" },
  { state: "blocked", title: "막힘" },
  { state: "done", title: "완료" },
];

const transitionFor: Partial<Record<TaskState, Partial<Record<TaskState, TaskAction>>>> = {
  open: { in_progress: "start" },
  in_progress: { blocked: "block", done: "complete" },
  blocked: { in_progress: "resume" },
  done: { in_progress: "resume" },
};

export function TaskKanban({
  tasks,
  canManage,
  busy,
  onOpen,
  onTransition,
  onInvalidMove,
}: {
  tasks: DirectTask[];
  canManage: boolean;
  busy: boolean;
  onOpen: (task: DirectTask) => void;
  onTransition: (task: DirectTask, action: TaskAction, reason?: string) => Promise<void>;
  onInvalidMove: (message: string) => void;
}) {
  const [dragging, setDragging] = useState<string | null>(null);
  const [blockTarget, setBlockTarget] = useState<DirectTask | null>(null);
  const [blockReason, setBlockReason] = useState("");

  const drop = (event: DragEvent, target: TaskState) => {
    event.preventDefault();
    const task = tasks.find((item) => item.task_id === dragging);
    setDragging(null);
    if (!task || task.state === target) return;
    const action = transitionFor[task.state]?.[target];
    if (!action) {
      onInvalidMove(`${taskStateLabel[task.state]} 업무는 ${taskStateLabel[target]}(으)로 바로 옮길 수 없습니다.`);
      return;
    }
    if (action === "block") {
      setBlockTarget(task);
      setBlockReason("");
      return;
    }
    void onTransition(task, action);
  };

  return (
    <div className="kanban">
      {kanbanColumns.map((column) => {
        const items = tasks.filter((task) => task.state === column.state);
        return (
          <section
            className={dragging ? "kanban-column droppable" : "kanban-column"}
            key={column.state}
            onDragOver={(event) => {
              if (canManage) event.preventDefault();
            }}
            onDrop={(event) => drop(event, column.state)}
          >
            <header className="kanban-head">
              <StatusText state={column.state} label={column.title} />
              <span className="count-badge">{items.length}</span>
            </header>
            <div className="kanban-body">
              {items.length === 0 && <p className="kanban-empty">없음</p>}
              {items.map((task) => (
                <article
                  className={dragging === task.task_id ? "kanban-card dragging" : "kanban-card"}
                  draggable={canManage && !busy}
                  key={task.task_id}
                  onClick={() => onOpen(task)}
                  onDragEnd={() => setDragging(null)}
                  onDragStart={(event) => {
                    setDragging(task.task_id);
                    event.dataTransfer.effectAllowed = "move";
                  }}
                >
                  <b>{task.title}</b>
                  <small className={task.block_reason ? "reason" : ""}>
                    {task.block_reason ? `막힘 사유: ${task.block_reason}` : `${formatDate(isoDateInSeoul(task.created_at))} 시작 · v${task.version}`}
                  </small>
                </article>
              ))}
            </div>
          </section>
        );
      })}
      {blockTarget && (
        <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && setBlockTarget(null)}>
          <section aria-label="막힘 사유" aria-modal="true" className="modal" role="dialog">
            <header className="modal-head">
              <h3>막힘 사유를 남겨 주세요</h3>
            </header>
            <div className="modal-body">
              <p>'{blockTarget.title}' 업무를 막힘으로 옮깁니다. 사유는 팀장 화면과 일일보고 근거에 남습니다.</p>
              <div className="field">
                <label htmlFor="kanban-block-reason">막힘 사유</label>
                <input
                  autoFocus
                  id="kanban-block-reason"
                  onChange={(event) => setBlockReason(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && blockReason.trim()) {
                      void onTransition(blockTarget, "block", blockReason.trim());
                      setBlockTarget(null);
                    }
                    if (event.key === "Escape") setBlockTarget(null);
                  }}
                  placeholder="무엇 때문에 막혔는지 적어 주세요"
                  value={blockReason}
                />
              </div>
            </div>
            <footer className="modal-foot">
              <button className="btn h40 ghost" onClick={() => setBlockTarget(null)} type="button">
                돌아가기
              </button>
              <button
                className="btn h40 primary"
                disabled={!blockReason.trim() || busy}
                onClick={() => {
                  void onTransition(blockTarget, "block", blockReason.trim());
                  setBlockTarget(null);
                }}
                type="button"
              >
                막힘 처리
              </button>
            </footer>
          </section>
        </div>
      )}
    </div>
  );
}
