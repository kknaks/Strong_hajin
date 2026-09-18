import { useMemo, useState } from "react";

import { Button } from "../ds/Button";
import { Empty } from "../ds/Empty";
import { SegmentedControl } from "../ds/SegmentedControl";
import { Skeleton } from "../ds/Skeleton";
import { StatusNote } from "../ds/StatusNote";
import { Badge } from "../ds/Badge";
import { Icon } from "../ds/icons/Icon";
import { addDays, dayDifference, formatDate, formatMonth, isOverdue, seoulToday, taskStateLabel, weekdayNames } from "../lib/labels";
import { type DirectTask } from "../lib/viewModels";

/**
 * 우 레일 — 캘린더 (바퀴 5b · WORK-002 Phase 7-D).
 *
 * **새 데이터 원천을 만들지 않는다**(K-3) — 본문 목록이 쓰는 `tasks` 그대로다.
 *
 * 바퀴 5b 는 주·월을 본문용 `TaskCalendar`(폭 넓은 격자)로 때웠다. 7-D 가 시안 모양으로 옮긴다 —
 * 주는 **`CalendarNav` + 요일 스트립 + 날짜별 접이식 섹션**, 월은 **7×N 그리드 + 고른 날의 목록**이다.
 * 좁은 레일(280px)에 본문 격자를 넣으면 막대가 겹쳐 읽히지 않는다.
 *
 * ── 시안에 있는데 안 그린 것 (부재의 종류를 가른다) ──
 * - **근무 시간**(`.scax-calendar-rail__hours`): 백엔드·프론트 어디에도 그 개념이 **없다**. 새 계약이다 (M-23).
 * - **회의 일정**: `GET /api/meetings` 가 값을 이미 내므로 **BE 계약은 필요 없다.** 그러나 다른 도메인의
 *   데이터를 업무 캘린더로 끌어오는 것은 **범위 결정**이고 SPEC-003 §2.9·§6·§7 이 이번 범위에서 뺐다.
 *   **기술적으로 가능하다는 사실을 범위 승인으로 바꿔 읽지 않는다.**
 */

export type CalendarRailState = "loading" | "error" | "ready";

type Range = "today" | "week" | "month";

const RANGES: ReadonlyArray<{ value: Range; label: string }> = [
  { value: "today", label: "오늘" },
  { value: "week", label: "주간" },
  { value: "month", label: "월간" },
];

/** 기한이 이 날짜인 업무 — 취소된 것은 일정이 아니다. */
function dueOn(tasks: DirectTask[], date: string): DirectTask[] {
  return tasks.filter((task) => task.due_date === date && task.state !== "cancelled");
}

export function CalendarRail({
  tasks,
  state,
  onOpen,
  onRetry,
}: {
  tasks: DirectTask[];
  state: CalendarRailState;
  onOpen: (task: DirectTask) => void;
  onRetry: () => void;
}) {
  const [range, setRange] = useState<Range>("today");
  const today = seoulToday();
  const [anchor, setAnchor] = useState(today);

  let body;
  if (state === "loading") {
    body = <Skeleton label="일정을 불러오는 중" rows={3} />;
  } else if (state === "error") {
    body = (
      <StatusNote tone="danger">
        일정을 불러오지 못했습니다.{" "}
        <Button onClick={onRetry} size="sm" type="button" variant="text">
          다시 시도
        </Button>
      </StatusNote>
    );
  } else if (range === "today") {
    const items = dueOn(tasks, today);
    body =
      items.length === 0 ? (
        <Empty title="오늘 기한인 업무가 없습니다" />
      ) : (
        <>
          <div className="scax-calendar-rail__day">
            <p className="scax-calendar-rail__date">{formatDate(today)}</p>
            {/* 시안은 여기에 「근무 시간」을 두지만 그 값을 담는 계약이 우리에게 없다 (M-23). */}
          </div>
          <div className="scax-calendar-rail__list">
            {items.map((task) => (
              <AgendaItem key={task.task_id} onOpen={onOpen} task={task} today={today} />
            ))}
          </div>
        </>
      );
  } else if (range === "week") {
    body = <WeekView anchor={anchor} onAnchor={setAnchor} onOpen={onOpen} tasks={tasks} today={today} />;
  } else {
    body = <MonthView anchor={anchor} onAnchor={setAnchor} onOpen={onOpen} tasks={tasks} today={today} />;
  }

  return (
    <section aria-label="캘린더" className="scax-calendar-rail">
      <header className="scax-calendar-rail__header">
        <h2 className="scax-calendar-rail__title">캘린더</h2>
        <SegmentedControl ariaLabel="캘린더 범위" onChange={setRange} options={RANGES} value={range} />
      </header>
      {body}
    </section>
  );
}

/** 기간 라벨 + 이전/다음 — 주·월이 공유한다 (시안 `CalendarNav`). */
function CalendarNav({ label, onPrev, onNext, prevLabel, nextLabel }: { label: string; onPrev: () => void; onNext: () => void; prevLabel: string; nextLabel: string }) {
  return (
    <div className="scax-calendar-nav">
      <span className="scax-calendar-nav__label">{label}</span>
      <button aria-label={prevLabel} className="scax-icon-button" onClick={onPrev} type="button">
        <Icon name="chevron-left-small" size={20} />
      </button>
      <button aria-label={nextLabel} className="scax-icon-button" onClick={onNext} type="button">
        <Icon name="chevron-right-small" size={20} />
      </button>
    </div>
  );
}

function DayCell({ date, muted, selected, dot, onSelect }: { date: string; muted?: boolean; selected?: boolean; dot?: boolean; onSelect: () => void }) {
  const classes = ["scax-day-cell", selected ? "scax-day-cell--selected" : "", muted ? "scax-day-cell--muted" : ""].filter(Boolean).join(" ");
  return (
    <button aria-pressed={Boolean(selected)} className={classes} onClick={onSelect} type="button">
      {Number(date.slice(8))}
      {dot && <span className="scax-day-cell__dot" />}
    </button>
  );
}

/** 한 업무를 일정 한 줄로. 기한 초과는 **표시만** 바꾼다 (U-14). */
function AgendaItem({ task, today, onOpen }: { task: DirectTask; today: string; onOpen: (task: DirectTask) => void }) {
  const overdueDays = task.derived?.overdue_days ?? (isOverdue(task, today) ? dayDifference(task.due_date!, today) : 0);
  return (
    <article className="scax-agenda-item openable" onClick={() => onOpen(task)}>
      <div className="scax-agenda-item__top">
        <Badge tone={task.state === "blocked" ? "danger" : "neutral"}>{taskStateLabel[task.state] ?? task.state}</Badge>
        {overdueDays > 0 && <span className="scax-task-table__due-extra">+{overdueDays}</span>}
      </div>
      <p className="scax-agenda-item__title">{task.title}</p>
      {task.block_reason && <p className="scax-agenda-item__sub">막힘 사유: {task.block_reason}</p>}
    </article>
  );
}

/** 요일 스트립 + 날짜별 접이식 섹션 (시안 `WeekView`). 비어 있는 날은 섹션을 세우지 않는다. */
function WeekView({ anchor, onAnchor, tasks, today, onOpen }: { anchor: string; onAnchor: (date: string) => void; tasks: DirectTask[]; today: string; onOpen: (task: DirectTask) => void }) {
  const weekday = new Date(`${anchor}T00:00:00Z`).getUTCDay();
  const start = addDays(anchor, -weekday);
  const days = useMemo(() => Array.from({ length: 7 }, (_, index) => addDays(start, index)), [start]);
  const sections = days.map((date) => ({ date, items: dueOn(tasks, date) })).filter((section) => section.items.length > 0);
  const [closed, setClosed] = useState<Record<string, boolean>>({});

  return (
    <>
      <CalendarNav
        label={`${formatDate(days[0])} – ${formatDate(days[6])}`}
        nextLabel="다음 주"
        onNext={() => onAnchor(addDays(anchor, 7))}
        onPrev={() => onAnchor(addDays(anchor, -7))}
        prevLabel="이전 주"
      />
      <div className="scax-day-strip">
        {weekdayNames.map((name) => (
          <span className="scax-day-strip__head" key={name}>
            {name}
          </span>
        ))}
        {days.map((date) => (
          <DayCell date={date} dot={dueOn(tasks, date).length > 0} key={date} onSelect={() => onAnchor(date)} selected={date === anchor || date === today} />
        ))}
      </div>
      <div className="scax-calendar-rail__list">
        {sections.length === 0 ? (
          <Empty title="이번 주에 기한인 업무가 없습니다" />
        ) : (
          sections.map((section) => (
            <section className="scax-day-section" key={section.date}>
              <button
                aria-expanded={!closed[section.date]}
                className="scax-day-section__head"
                onClick={() => setClosed((current) => ({ ...current, [section.date]: !current[section.date] }))}
                type="button"
              >
                <span className="scax-day-section__label">
                  {formatDate(section.date)} ({section.items.length})
                </span>
                <Icon name={closed[section.date] ? "chevron-right" : "chevron-down"} size={16} />
              </button>
              {!closed[section.date] && (
                <div className="scax-day-section__body">
                  {section.items.map((task) => (
                    <AgendaItem key={task.task_id} onOpen={onOpen} task={task} today={today} />
                  ))}
                </div>
              )}
            </section>
          ))
        )}
      </div>
    </>
  );
}

/** 7×N 그리드 + 고른 날의 일정 (시안 `MonthView`). */
function MonthView({ anchor, onAnchor, tasks, today, onOpen }: { anchor: string; onAnchor: (date: string) => void; tasks: DirectTask[]; today: string; onOpen: (task: DirectTask) => void }) {
  const [year, month] = anchor.slice(0, 7).split("-").map(Number);
  const cursor = anchor.slice(0, 7);
  const first = `${cursor}-01`;
  const firstWeekday = new Date(`${first}T00:00:00Z`).getUTCDay();
  const gridStart = addDays(first, -firstWeekday);
  const days = useMemo(() => Array.from({ length: 42 }, (_, index) => addDays(gridStart, index)), [gridStart]);
  const selected = dueOn(tasks, anchor);

  const shift = (delta: number) => {
    const next = new Date(Date.UTC(year, month - 1 + delta, 1));
    onAnchor(next.toISOString().slice(0, 10));
  };

  return (
    <>
      <CalendarNav label={formatMonth(year, month)} nextLabel="다음 달" onNext={() => shift(1)} onPrev={() => shift(-1)} prevLabel="이전 달" />
      <div className="scax-month-grid">
        {weekdayNames.map((name) => (
          <span className="scax-month-grid__head" key={name}>
            {name}
          </span>
        ))}
        {days.map((date) => (
          <DayCell
            date={date}
            dot={dueOn(tasks, date).length > 0}
            key={date}
            muted={!date.startsWith(cursor)}
            onSelect={() => onAnchor(date)}
            selected={date === anchor}
          />
        ))}
      </div>
      <div className="scax-calendar-rail__list">
        {selected.length === 0 ? (
          <Empty title={`${formatDate(anchor)}에 기한인 업무가 없습니다`} />
        ) : (
          selected.map((task) => <AgendaItem key={task.task_id} onOpen={onOpen} task={task} today={today} />)
        )}
      </div>
    </>
  );
}
