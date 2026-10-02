import { useEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from "react";
import { Avatar } from "../../ds/Avatar";

import { Badge } from "../../ds/Badge";
import { Button, IconButton } from "../../ds/Button";
import { SegmentedControl } from "../../ds/SegmentedControl";
import { addDays, dayDifference, dueDayText, formatDate, formatMonth, isOverdue, isoDateInSeoul, seoulToday, taskStateLabel } from "../../lib/labels";
import type { DirectTask, TaskState } from "../../lib/viewModels";
import { mondayOf, weekWindow } from "../../lib/weekWindow";
import { allowedTaskTransitions, BlockReasonPrompt, StatusText, type TaskAction } from "./WorkModals";
import { EmptyValue } from "../../ds/Empty";
import { Icon, type IconName } from "../../ds/icons/Icon";

/* ---------------------------------------------------------------- shared small pieces */

export function MetricCard({ label, value, icon, onClick }: { label: string; value: number; icon?: IconName; onClick?: () => void }) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag className="metric-card" onClick={onClick} type={onClick ? "button" : undefined}>
      <span className="metric-label">
        {icon && <span aria-hidden className="metric-icon"><Icon name={icon} /></span>}
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
        <span>{date ?? <EmptyValue />}</span>
        <span className="task-card-people">{people}</span>
      </div>
      {actions && <div className="task-card-actions">{actions}</div>}
    </Tag>
  );
}

export function PersonChip({ name, arrowTo }: { name: string; arrowTo?: string }) {
  return (
    <span className="person-chip">
      <Avatar name={name} size="xs" />
      {name}
      {arrowTo && (
        <>
          <span aria-hidden className="person-arrow">
            <Icon name="arrow-right" size={12} />
          </span>
          <Avatar name={arrowTo} size="xs" />
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
          <Icon name="chevron-down" size={12} />
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
      <Icon name={complete ? "square-check" : "blank"} size={12} /> {progress.done}/{progress.total}
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
        {isOverdue(task, seoulToday()) && <Badge tone="danger">기한 초과</Badge>}
        <StatusText state={task.state} />
        {right}
      </div>
    </li>
  );
}

/* ---------------------------------------------------------------- calendar view (month) */

/**
 * Where a Task sits on a calendar, from its stored date fields alone.
 *
 * Both dates give a range that includes both ends; one date gives that single day. A Task with neither is not on the
 * calendar at all — the renderer does not infer a date from state or creation history.
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
          <IconButton label={mode === "week" ? "이전 주" : "이전 달"} onClick={() => shift(-1)}>
            ‹
          </IconButton>
          <b>{rangeLabel}</b>
          <IconButton label={mode === "week" ? "다음 주" : "다음 달"} onClick={() => shift(1)}>
            ›
          </IconButton>
        </div>
        <div className="toolbar-group">
          {onModeChange && (
            <SegmentedControl
              ariaLabel="캘린더 보기"
              onChange={onModeChange}
              options={[
                { value: "week", label: "주" },
                { value: "month", label: "월" },
              ]}
              value={mode}
            />
          )}
          <Button size="sm" onClick={() => setAnchor(today)} type="button">
            오늘
          </Button>
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

/* ---------------------------------------------------------------- timeline view (W-1 ~ W+3) */

/** 타임라인 하루 칸의 최소 폭 — 프로젝트 간트의 하루 폭(34px)과 같다. CSS `.work-timeline` 의 34px 와 짝이다. */
const TIMELINE_DAY = 34;
/** 첫 화면에서 「오늘」 앞에 남겨 두는 날 수 — 간트의 `openLead` 와 같은 뜻이다. */
const TIMELINE_OPEN_LEAD = 2;

/**
 * 내 업무 › 타임라인 (SPEC-001 U-16 · WORK-008 D-01).
 *
 * 범위는 **프로젝트 간트와 같은 계산**(`weekWindow`)이다 — 오늘 기준 W-1 ~ W+3(월요일 시작 5주),
 * 범위 밖 업무면 주 경계까지 넓힌다. `‹` `›` 는 기준 주를 1주씩 밀고, **민 창에서는 넓히지 않는다** —
 * 넓힘은 오늘 기준 첫 화면에만 건다(OQ-P). 「오늘」은 그 첫 화면으로 돌아온다.
 */
export function TaskTimeline({ tasks, onOpen }: { tasks: DirectTask[]; onOpen: (task: DirectTask) => void }) {
  const today = seoulToday();
  /** 기준 주를 오늘의 주에서 몇 주 밀었나. 0 이 오늘 기준 첫 화면이다. */
  const [weekShift, setWeekShift] = useState(0);
  const baseWeek = addDays(mondayOf(today), weekShift * 7);
  const spans = weekShift === 0 ? tasks.flatMap((task) => taskSpan(task) ?? []) : [];
  const { from: windowStart, to: windowEnd, days } = weekWindow(baseWeek, spans);

  /*
   * 처음 열 때와 「오늘」을 누를 때 **오늘이 보이게** 가로 스크롤을 맞춘다 (SPEC-005 §2.4 첫 진입 · U-16).
   * 칸이 최소 폭(34px)으로 접혀야 스크롤이 생기므로 그 폭으로 잰다 — 칸이 넓어진 화면은 스크롤할 것이 없다.
   * 업무명 열은 왼쪽에 고정돼(`.work-timeline .timeline-label-col` sticky) 뷰포트 왼쪽 그 폭을 늘 덮는다.
   * 날짜 칸은 그 열 «뒤» 에서 시작하므로 오늘 칸의 x 는 `열 폭 + idx × 34` 이고, 고정 열 바로 오른쪽이
   * `scrollLeft + 열 폭` 이다 — 그래서 `scrollLeft = (idx - 2) × 34` 면 오늘이 고정 열 뒤에 숨지 않고 두 칸 오른쪽에 선다.
   * 자료가 갱신될 때마다 되감지 않는다 — 사람이 민 자리를 화면이 도로 빼앗지 않는다 (간트와 같은 규율).
   */
  const scroller = useRef<HTMLDivElement | null>(null);
  const [scrollRequest, setScrollRequest] = useState(0);
  const todayIndex = days.indexOf(today);
  const openLeft = todayIndex < 0 ? 0 : Math.max(0, (todayIndex - TIMELINE_OPEN_LEAD) * TIMELINE_DAY);
  const latestOpen = useRef(openLeft);
  useEffect(() => {
    latestOpen.current = openLeft;
  });
  useEffect(() => {
    if (scroller.current) scroller.current.scrollLeft = latestOpen.current;
  }, [scrollRequest]);
  const backToToday = () => {
    setWeekShift(0);
    setScrollRequest((count) => count + 1);
  };

  const months = days.reduce<Array<{ label: string; start: number; length: number }>>((groups, day, index) => {
    const [year, month] = day.split("-").map(Number);
    const label = `${year}년 ${month}월`;
    const last = groups.at(-1);
    if (last?.label === label) last.length += 1;
    else groups.push({ label, start: index, length: 1 });
    return groups;
  }, []);

  return (
    <div className="timeline work-timeline">
      <div className="calendar-toolbar">
        <div className="stepper">
          <IconButton label="이전 주" onClick={() => setWeekShift((value) => value - 1)}>
            <Icon name="chevron-left-small" size={20} />
          </IconButton>
          <b>
            {formatDate(windowStart)} – {formatDate(windowEnd)}
          </b>
          <IconButton label="다음 주" onClick={() => setWeekShift((value) => value + 1)}>
            <Icon name="chevron-right-small" size={20} />
          </IconButton>
          {/* 「오늘」 — 오늘 기준 첫 화면(넓힘 포함)으로 돌아온다. 모양은 캘린더 보기의 「오늘」과 같은 부품이다. */}
          <Button size="sm" onClick={backToToday} type="button">
            오늘
          </Button>
        </div>
        <div className="timeline-legend">
          <span className="status in_progress">진행 중</span>
          <span className="status blocked">막힘</span>
          <span className="status done">완료</span>
          <span className="status open">시작 전</span>
        </div>
      </div>
      <div className="timeline-scroll" ref={scroller}>
      <div className="timeline-grid" style={{ ["--days" as string]: days.length }}>
        <div className="timeline-months">
          <span className="timeline-label-col">기간</span>
          {months.map((month) => <span key={month.label} title={month.label} style={{ gridColumn: `${month.start + 2} / span ${month.length}` }}>{month.label}</span>)}
        </div>
        <div className="timeline-head">
          <span className="timeline-label-col">업무명</span>
          {days.map((day) => (
            <span aria-current={day === today ? "date" : undefined} className={day === today ? "today" : ""} key={day} title={formatDate(day)}>
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
                    aria-label={`${task.title} · ${formatDate(span!.start)} – ${formatDate(span!.end)} · ${taskStateLabel[task.state]}`}
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
    </div>
  );
}

/* ---------------------------------------------------------------- kanban board */

const kanbanColumns: Array<{ state: TaskState; title: string }> = [
  { state: "open", title: "시작 전" },
  { state: "in_progress", title: "진행 중" },
  { state: "blocked", title: "막힘" },
  /* 「완료 확인 대기」 칸은 없다 — 그것은 상태가 아니라 `derived.approval` 이다(SPEC-003 §2.2).
     보고가 들어간 업무는 밖으로 `done` 이라 「완료」 칸에 서고, 확인 대기인지는 행의 배지가 말한다. */
  { state: "done", title: "완료" },
];

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
  /** 전이를 보낸다. 돌려주는 값(받아들여졌나)은 이 자리가 쓰지 않는다 — 사유 자리만 그것을 읽는다. */
  onTransition: (task: DirectTask, action: TaskAction, reason?: string) => Promise<boolean | void>;
  onInvalidMove: (message: string) => void;
}) {
  const [dragging, setDragging] = useState<string | null>(null);
  const [blockTarget, setBlockTarget] = useState<DirectTask | null>(null);

  const drop = (event: DragEvent, target: TaskState) => {
    event.preventDefault();
    const task = tasks.find((item) => item.task_id === dragging);
    setDragging(null);
    if (!task || task.state === target) return;
    const action = allowedTaskTransitions(task).find((transition) => transition.to === target)?.action;
    if (!action) {
      onInvalidMove(`${taskStateLabel[task.state]} 업무는 ${taskStateLabel[target]}(으)로 바로 옮길 수 없습니다.`);
      return;
    }
    if (action === "block") {
      setBlockTarget(task);
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
        <BlockReasonPrompt
          busy={busy}
          onClose={() => setBlockTarget(null)}
          onSubmit={(reason) => {
            void onTransition(blockTarget, "block", reason);
            setBlockTarget(null);
          }}
          task={blockTarget}
        />
      )}
    </div>
  );
}
