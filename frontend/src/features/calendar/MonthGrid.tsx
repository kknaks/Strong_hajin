import { calendarDow, calendarScreen, calendarCursorText } from "../../lib/labels";
import { EventBar, EventGhost, EventMore } from "./EventBar";
import { laneSeats, packLanes, type CalendarSegment, type MonthDay } from "./calendarModel";

/** 한 칸에 세우는 줄의 상한. 넘치면 「+N건 더」로 접는다 (SPEC §2.1 — 시안 `CAL_CELL_LIMIT`). */
const MONTH_LANE_LIMIT = 4;

/**
 * 월 격자 (G-CAL-06).
 *
 * **기존 `TaskCalendar`(`features/work/WorkViews.tsx`)를 고치지 않고 새로 세웠다** — 시그니처도
 * 앵커도 주 뷰 성격도 전부 다르고, 그쪽은 업무 목록 화면이 계속 쓴다.
 *
 * 시안의 `day: number`(달 안의 일 번호) 대신 **ISO 날짜**를 들고 다닌다. 그래서 달 밖 칸도 자기
 * 날짜를 갖고, 「달 밖 칸에는 핸들러가 없다」는 시안의 제약이 사라진다 — 달 경계를 넘는 이동은
 * 허용이기 때문이다(SPEC §2.2). FE-1 은 읽기까지라 드롭·손잡이는 아직 붙지 않는다.
 */
export function MonthGrid({
  days,
  segments,
  selected,
  onSelect,
  today,
  year,
  month,
}: {
  days: MonthDay[];
  segments: CalendarSegment[];
  selected: string | null;
  onSelect: (date: string | null) => void;
  today: string;
  year: number;
  month: number;
}) {
  /* 줄 나누기는 «주» 단위다 — 같은 항목이 그 주 내내 같은 줄에 앉아야 여러 날 띠가 끊기지 않는다. */
  const weeks = Array.from({ length: Math.ceil(days.length / 7) }, (_, index) => days.slice(index * 7, index * 7 + 7));
  const weekLanes = weeks.map((week) => packLanes(segments, week.map((day) => day.date)));

  return (
    <section aria-label={`${calendarCursorText.yearMonth(year, month)} ${calendarScreen.monthLabel}`} className="scax-month">
      <div className="scax-month__head">
        {calendarDow.map((name, index) => (
          <span className={`scax-month__dow${index === 0 ? " scax-month__dow--sun" : ""}`} key={name}>
            {name}
          </span>
        ))}
      </div>
      <div className="scax-month__grid">
        {days.map((day, index) => {
          const weekIndex = Math.floor(index / 7);
          const week = weeks[weekIndex];
          const lanes = weekLanes[weekIndex] ?? [];
          const visible = lanes.slice(0, MONTH_LANE_LIMIT);
          const rest = lanes
            .slice(MONTH_LANE_LIMIT)
            .reduce((count, lane) => count + lane.filter((segment) => segment.from <= day.date && day.date <= segment.to).length, 0);
          const seats = laneSeats(visible, day.date);
          const number = Number(day.date.slice(8, 10));
          const sunday = index % 7 === 0;
          const isToday = day.date === today;
          const inside = (
            <>
              <div className="scax-month__daytop">
                <span
                  className={`scax-month__date${sunday && !day.out ? " scax-month__date--sun" : ""}${isToday ? " scax-month__date--today" : ""}`}
                >
                  {number}
                </span>
                {isToday ? <span className="scax-month__today-label">{calendarScreen.today}</span> : null}
              </div>
              {seats.map((segment, lane) =>
                segment ? (
                  <EventBar
                    date={day.date}
                    key={`${lane}:${segment.key}`}
                    segment={segment}
                    weekFirst={week[0].date}
                    weekLast={week[week.length - 1].date}
                  />
                ) : (
                  <EventGhost key={`ghost-${lane}`} />
                ),
              )}
              {rest > 0 ? <EventMore count={rest} /> : null}
            </>
          );
          /* 달 밖 칸도 날짜를 갖지만 «고를» 대상은 아니다 — 레일을 그 날로 좁히는 일은 이 달의 칸이 한다. */
          if (day.out) {
            return (
              <div className="scax-month__cell scax-month__cell--out" key={day.date}>
                <div className="scax-month__daytop">
                  <span className="scax-month__date">{number}</span>
                </div>
              </div>
            );
          }
          return (
            <button
              aria-pressed={selected === day.date}
              className={`scax-month__cell${selected === day.date ? " scax-month__cell--selected" : ""}`}
              data-date={day.date}
              key={day.date}
              onClick={() => onSelect(selected === day.date ? null : day.date)}
              type="button"
            >
              {inside}
            </button>
          );
        })}
      </div>
    </section>
  );
}
