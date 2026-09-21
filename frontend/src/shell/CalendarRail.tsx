import { useEffect, useMemo, useState } from "react";

import { Button } from "../ds/Button";
import { Empty } from "../ds/Empty";
import { SegmentedControl } from "../ds/SegmentedControl";
import { Skeleton } from "../ds/Skeleton";
import { StatusNote } from "../ds/StatusNote";
import { Badge } from "../ds/Badge";
import { Icon } from "../ds/icons/Icon";
import { addDays, dayDifference, isOverdue, isoDateInSeoul, meetingClock, meetingScreen, seoulToday, taskStateLabel, weekdayNames } from "../lib/labels";
import { type CalendarMeetingRow, type DirectTask } from "../lib/viewModels";

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
 * - ~~**회의 일정**~~ — **WORK-004 FE-4 가 들였다** (확정 — 증보 K23). 아래.
 *
 * ── 회의가 선다 (증보 K23·K24) ──
 *
 * **축이 둘이고 서로 다르다.**
 *
 * | 무엇 | 어디서 오나 |
 * |---|---|
 * | **업무** | **그 탭이 들고 있는 것을 그대로** — 페이지가 넘기는 `tasks` 다. **지금 동작 그대로**이고 이 부품은 여전히 **자기 질의가 없다** |
 * | **회의** | **탭과 무관하게 항상 「내 회의」**(§D `board` 축) — 페이지가 **합본 조회의 회의 절반**으로 채워 넘긴다 |
 *
 * **왜 업무를 합본 조회로 바꾸지 않는가.** 합본 조회의 업무 축은 `my_work`(지금 내가 활성 담당)다.
 * 그것으로 갈아타면 **「보낸 업무」 탭에서 보낸 업무가 사라진다** — 회의는 업무의 분류 축
 * (내 업무 · 보낸 업무 · 완료 업무 · 참조)에 **속하지 않으므로** 그 축을 건드릴 이유가 없다.
 *
 * **새 표면을 만들지 않았다** — 쓰는 것은 캘린더 화면이 이미 쓰는 `GET /api/calendar` 하나다.
 * **범위는 이 부품이 정하고 질의는 페이지가 한다**(`onRange`) — 그래야 레일이 그리는 날과
 * 받아 온 회의의 범위가 **어긋날 수 없다**(캘린더 화면의 K18 과 같은 규율).
 *
 * **회의 줄은 읽기 전용이다** — 누를 수 없다. 캘린더 화면의 회의 카드와 **같은 규율**이고(§2.4),
 * 여기서 회의를 여는 것은 **다른 화면으로 가는 일**이라 K23(「회의도 보이게」)이 요구한 것이 아니다.
 */

export type CalendarRailState = "loading" | "error" | "ready";

type Range = "today" | "week" | "month";

const RANGES: ReadonlyArray<{ value: Range; label: string }> = [
  { value: "today", label: "오늘" },
  { value: "week", label: "주" },
  { value: "month", label: "월" },
];

/** 레일 표기는 시안의 한국어 날짜 형식을 쓰며 저장된 ISO 날짜는 바꾸지 않는다. */
function railDate(date: string): string {
  const [, month, day] = date.split("-").map(Number);
  const weekday = weekdayNames[new Date(`${date}T00:00:00Z`).getUTCDay()];
  return `${month}월 ${day}일 ${weekday}요일`;
}

function weekLabel(date: string): string {
  const [year, month, day] = date.split("-").map(Number);
  const firstWeekday = new Date(Date.UTC(year, month - 1, 1)).getUTCDay();
  return `${year}년 ${month}월 ${Math.ceil((day + firstWeekday) / 7)}주 차`;
}

/** 기한이 이 날짜인 업무 — 취소된 것은 일정이 아니다. */
function dueOn(tasks: DirectTask[], date: string): DirectTask[] {
  return tasks.filter((task) => task.due_date === date && task.state !== "cancelled");
}

/**
 * 그 날 열리는 회의 — **시작이 이른 것부터**.
 *
 * `starts_at` 은 **UTC ISO** 라 사무실 시간대로 옮겨 날을 가른다(업무의 `due_date` 는 이미 날짜다).
 * 취소된 회의는 일정이 아니다 — **업무 쪽의 `cancelled` 를 빼는 것과 같은 규칙**이다.
 */
function meetingsOn(meetings: CalendarMeetingRow[], date: string): CalendarMeetingRow[] {
  return meetings
    .filter((meeting) => meeting.status !== "cancelled" && (isoDateInSeoul(meeting.starts_at) ?? "") === date)
    .sort((left, right) => (left.starts_at < right.starts_at ? -1 : left.starts_at > right.starts_at ? 1 : 0));
}

/** 그 날에 무엇이든 서나 — 점과 섹션이 같은 답을 쓴다. */
function busyOn(tasks: DirectTask[], meetings: CalendarMeetingRow[], date: string): boolean {
  return dueOn(tasks, date).length > 0 || meetingsOn(meetings, date).length > 0;
}

/**
 * 그 날 서는 것 전부 — **업무가 먼저, 회의는 시간순**.
 *
 * 업무의 기한은 날짜뿐이라 **종일에 가깝고** 회의에는 시각이 있다. 캘린더 화면이 월 뷰 셀에서
 * 쓰는 순서(종일 먼저 · 시간 이른 것부터 — 증보 K20)와 **같은 규칙**이라 두 화면이 어긋나지 않는다.
 */
function DayItems({
  tasks,
  meetings,
  date,
  today,
  onOpen,
}: {
  tasks: DirectTask[];
  meetings: CalendarMeetingRow[];
  date: string;
  today: string;
  onOpen: (task: DirectTask) => void;
}) {
  return (
    <>
      {dueOn(tasks, date).map((task) => (
        <AgendaItem key={task.task_id} onOpen={onOpen} task={task} today={today} />
      ))}
      {meetingsOn(meetings, date).map((meeting) => (
        <MeetingItem key={meeting.meeting_id} meeting={meeting} />
      ))}
    </>
  );
}

/** 회의 한 줄 — **누를 수 없다**(§2.4). 업무 줄과 같은 마크업을 쓰되 `openable` 을 붙이지 않는다. */
function MeetingItem({ meeting }: { meeting: CalendarMeetingRow }) {
  return (
    <article className="scax-agenda-item">
      <div className="scax-agenda-item__top">
        <Badge tone="neutral">회의</Badge>
        <span className="t-meta">
          {meetingClock(meeting.starts_at)}–{meetingClock(meeting.ends_at)}
        </span>
      </div>
      {/* 제목 없는 회의의 말은 **회의 화면과 같은 것**을 쓴다 — 여기서 새 문자열을 만들지 않는다. */}
      <p className="scax-agenda-item__title">{meeting.title ?? meetingScreen.noTitle}</p>
      {meeting.location && <p className="scax-agenda-item__sub">{meeting.location}</p>}
    </article>
  );
}

export function CalendarRail({
  tasks,
  meetings = [],
  meetingsFailed = false,
  state,
  onOpen,
  onRetry,
  onRange,
}: {
  tasks: DirectTask[];
  /** 내 회의 (증보 K23·K24) — **탭과 무관하다.** 페이지가 합본 조회의 회의 절반으로 채운다. */
  meetings?: CalendarMeetingRow[];
  /** 회의만 못 읽었다 — 업무는 멀쩡하다. **비어 있는 것과 못 읽은 것을 가른다.** */
  meetingsFailed?: boolean;
  state: CalendarRailState;
  onOpen: (task: DirectTask) => void;
  onRetry: () => void;
  /** 지금 그리는 날의 범위. **질의는 페이지가 한다** — 이 부품은 자기 질의를 갖지 않는다(K24). */
  onRange?: (from: string, to: string) => void;
}) {
  const [range, setRange] = useState<Range>("today");
  const today = seoulToday();
  const [anchor, setAnchor] = useState(today);

  /* 이 레일이 **실제로 그리는 날들**을 그대로 회의 조회의 범위로 쓴다 — 두 값을 따로 계산하지
     않으므로 「레일에는 있는데 못 받아 온 회의」가 생길 수 없다(캘린더 화면의 K18 과 같은 규율).
     월은 7×N 격자가 42칸이라 **달 밖 칸까지** 받는다: 그 칸도 점을 찍기 때문이다. */
  const [from, to] = useMemo((): [string, string] => {
    if (range === "today") return [today, today];
    if (range === "week") {
      const start = addDays(anchor, -new Date(`${anchor}T00:00:00Z`).getUTCDay());
      return [start, addDays(start, 6)];
    }
    const first = `${anchor.slice(0, 7)}-01`;
    const gridStart = addDays(first, -new Date(`${first}T00:00:00Z`).getUTCDay());
    return [gridStart, addDays(gridStart, 41)];
  }, [anchor, range, today]);

  useEffect(() => {
    onRange?.(from, to);
  }, [from, onRange, to]);

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
    body = !busyOn(tasks, meetings, today) ? (
      <Empty title="오늘 일정이 없습니다" />
    ) : (
      <>
        <div className="scax-calendar-rail__day">
          <p className="scax-calendar-rail__date">{railDate(today)}</p>
          {/* 시안은 여기에 「근무 시간」을 두지만 그 값을 담는 계약이 우리에게 없다 (M-23). */}
        </div>
        <div className="scax-calendar-rail__list">
          <DayItems date={today} meetings={meetings} onOpen={onOpen} tasks={tasks} today={today} />
        </div>
      </>
    );
  } else if (range === "week") {
    body = <WeekView anchor={anchor} meetings={meetings} onAnchor={setAnchor} onOpen={onOpen} tasks={tasks} today={today} />;
  } else {
    body = <MonthView anchor={anchor} meetings={meetings} onAnchor={setAnchor} onOpen={onOpen} tasks={tasks} today={today} />;
  }

  return (
    <section aria-label="캘린더" className="scax-calendar-rail">
      <header className="scax-calendar-rail__header">
        <h2 className="scax-calendar-rail__title">캘린더</h2>
        <SegmentedControl ariaLabel="캘린더 범위" onChange={setRange} options={RANGES} value={range} />
      </header>
      {/* 업무는 읽었는데 회의만 못 읽었다 — 「없다」로 보이게 두지 않는다. 업무 목록은 그대로 선다. */}
      {meetingsFailed && state === "ready" && <p className="t-meta">회의를 불러오지 못했습니다.</p>}
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

function DayCell({ date, muted, selected, dot, isToday, onSelect }: { date: string; muted?: boolean; selected?: boolean; dot?: boolean; isToday?: boolean; onSelect: () => void }) {
  const classes = ["scax-day-cell", selected ? "scax-day-cell--selected" : "", muted ? "scax-day-cell--muted" : ""].filter(Boolean).join(" ");
  return (
    <button aria-current={isToday ? "date" : undefined} aria-pressed={Boolean(selected)} className={classes} onClick={onSelect} type="button">
      {Number(date.slice(8))}
      {(dot || isToday) && <span aria-hidden className="scax-day-cell__dot" />}
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
function WeekView({ anchor, onAnchor, tasks, meetings, today, onOpen }: { anchor: string; onAnchor: (date: string) => void; tasks: DirectTask[]; meetings: CalendarMeetingRow[]; today: string; onOpen: (task: DirectTask) => void }) {
  const weekday = new Date(`${anchor}T00:00:00Z`).getUTCDay();
  const start = addDays(anchor, -weekday);
  const days = useMemo(() => Array.from({ length: 7 }, (_, index) => addDays(start, index)), [start]);
  const sections = days
    .map((date) => ({ date, count: dueOn(tasks, date).length + meetingsOn(meetings, date).length }))
    .filter((section) => section.count > 0);
  const [closed, setClosed] = useState<Record<string, boolean>>({});

  return (
    <>
      <CalendarNav
        label={weekLabel(anchor)}
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
          <DayCell date={date} isToday={date === today} dot={busyOn(tasks, meetings, date)} key={date} onSelect={() => onAnchor(date)} selected={date === anchor || date === today} />
        ))}
      </div>
      <div className="scax-calendar-rail__list">
        {sections.length === 0 ? (
          <Empty title="이번 주에 일정이 없습니다" />
        ) : (
          sections.map((section) => (
            <section className="scax-day-section" key={section.date}>
              <button
                aria-expanded={!(closed[section.date] ?? section.date < today)}
                className="scax-day-section__head"
                onClick={() => setClosed((current) => ({ ...current, [section.date]: !(current[section.date] ?? section.date < today) }))}
                type="button"
              >
                <span className="scax-day-section__label">
                  {railDate(section.date)} ({section.count})
                </span>
                <Icon className="scax-day-section__caret" name="chevron-down" size={16} />
              </button>
              {!(closed[section.date] ?? section.date < today) && (
                <div className="scax-day-section__body">
                  <DayItems date={section.date} meetings={meetings} onOpen={onOpen} tasks={tasks} today={today} />
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
function MonthView({ anchor, onAnchor, tasks, meetings, today, onOpen }: { anchor: string; onAnchor: (date: string) => void; tasks: DirectTask[]; meetings: CalendarMeetingRow[]; today: string; onOpen: (task: DirectTask) => void }) {
  const [year, month] = anchor.slice(0, 7).split("-").map(Number);
  const cursor = anchor.slice(0, 7);
  const first = `${cursor}-01`;
  const firstWeekday = new Date(`${first}T00:00:00Z`).getUTCDay();
  const gridStart = addDays(first, -firstWeekday);
  const days = useMemo(() => Array.from({ length: 42 }, (_, index) => addDays(gridStart, index)), [gridStart]);
  const picked = busyOn(tasks, meetings, anchor);

  const shift = (delta: number) => {
    const next = new Date(Date.UTC(year, month - 1 + delta, 1));
    onAnchor(next.toISOString().slice(0, 10));
  };

  return (
    <>
      <CalendarNav label={`${year}년 ${month}월`} nextLabel="다음 달" onNext={() => shift(1)} onPrev={() => shift(-1)} prevLabel="이전 달" />
      <div className="scax-month-grid">
        {weekdayNames.map((name) => (
          <span className="scax-month-grid__head" key={name}>
            {name}
          </span>
        ))}
        {days.map((date) => (
          <DayCell
            date={date}
            isToday={date === today}
            dot={busyOn(tasks, meetings, date)}
            key={date}
            muted={!date.startsWith(cursor)}
            onSelect={() => onAnchor(date)}
            selected={date === anchor}
          />
        ))}
      </div>
      <div className="scax-calendar-rail__list">
        {picked ? (
          <DayItems date={anchor} meetings={meetings} onOpen={onOpen} tasks={tasks} today={today} />
        ) : (
          <Empty title={`${railDate(anchor)}에 일정이 없습니다`} />
        )}
      </div>
    </>
  );
}
