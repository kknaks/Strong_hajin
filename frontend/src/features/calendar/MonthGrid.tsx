import { useState } from "react";

import { calendarDow, calendarScreen, calendarCursorText } from "../../lib/labels";
import { EventBar, EventGhost, EventMore } from "./EventBar";
import { laneSeats, packLanes, type CalendarSegment, type MonthDay } from "./calendarModel";
import type { DateEdge } from "./calendarWrites";
import { debugCalendarDnd } from "./calendarDndDebug";

/** 한 칸에 세우는 줄의 상한. 넘치면 「+N건 더」로 접는다 (SPEC §2.1 — 시안 `CAL_CELL_LIMIT`). */
const MONTH_LANE_LIMIT = 4;

/**
 * 월 격자 (G-CAL-06).
 *
 * **기존 `TaskCalendar`(`features/work/WorkViews.tsx`)를 고치지 않고 새로 세웠다** — 시그니처도
 * 앵커도 주 뷰 성격도 전부 다르고, 그쪽은 업무 목록 화면이 계속 쓴다.
 *
 * 시안의 `day: number`(달 안의 일 번호) 대신 **ISO 날짜**를 들고 다닌다.
 *
 * **여기 서는 것은 업무 날짜 띠와 회의뿐이다** (확정 — 증보 K20). 업무의 **시간 배정은 안 그린다** —
 * 둘을 다 그리면 같은 업무가 한 칸에 두 번 뜬다. 무엇이 오는지는 `monthSegments` 가 정하고
 * 이 부품은 **받은 것을 그릴 뿐**이다. **셀 안의 순서는 줄 순서**이고, 그 줄을 `packLanes` 가
 * **시간순**으로 나눈다.
 *
 * **FE-2 가 쓰기를 얹었다** — 날짜 칸 드롭(R1)과 띠의 좌우 손잡이(R3·R4).
 * 거절은 이 부품이 하지 않는다: **떨어뜨린 사실을 그대로 위로 올리고**(`onDropTask`) 판단과 문구는
 * 화면이 갖는다 — **조용한 거절이 없어야** 하므로 「못 떨어뜨리게」 막는 대신 「왜 안 되는지」를 말한다(§I).
 * 그래서 `onDragOver` 가 **언제나** `preventDefault` 를 부른다(시안은 `canDrop` 이 거짓이면 안 부른다).
 *
 * 달 밖 칸은 **시안대로 비워 둔다** — 일정을 그리지도, 고를 수도, 받지도 않는다.
 * 다른 달로 옮기는 길은 막히지 않는다: 그 달로 넘겨서 떨어뜨리면 된다(§2.2 가 허용한 것).
 */
export function MonthGrid({
  days,
  segments,
  selected,
  onSelect,
  today,
  year,
  month,
  onDropTask,
  onGrabHandle,
  dragging = false,
  grabbing = false,
}: {
  days: MonthDay[];
  segments: CalendarSegment[];
  selected: string | null;
  onSelect: (date: string | null) => void;
  today: string;
  year: number;
  month: number;
  /** R1 — 그 업무를 이 날로 옮긴다. 가드도 문구도 부르는 쪽 몫이다. */
  onDropTask?: (taskId: string, date: string) => void;
  /** R3·R4 — 띠의 끝을 잡았다. 실제 전송은 놓을 때 화면이 한다. */
  onGrabHandle?: (taskId: string, edge: DateEdge) => void;
  /** 지금 무언가를 끌고 있나 — 칸에 받는 자리 표시를 낸다. */
  dragging?: boolean;
  /** 지금 손잡이를 잡고 있나 — 칸의 커서를 좌우 화살표로 바꾼다. */
  grabbing?: boolean;
}) {
  const [over, setOver] = useState<string | null>(null);
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
                    onGrab={onGrabHandle}
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
              className={[
                "scax-month__cell",
                selected === day.date ? "scax-month__cell--selected" : "",
                dragging && over === day.date ? "scax-month__cell--drop" : "",
                grabbing ? "scax-month__cell--grabbing" : "",
              ]
                .filter(Boolean)
                .join(" ")}
              data-date={day.date}
              key={day.date}
              onClick={() => onSelect(selected === day.date ? null : day.date)}
              onDragLeave={() => setOver((current) => (current === day.date ? null : current))}
              /* 시안은 받을 수 없으면 `preventDefault` 를 안 불러 «브라우저가 말없이» 막는다.
                 우리는 언제나 받고, 안 되는 이유를 화면이 말한다 (§I 조용한 거절 0개). */
              onDragOver={(event) => {
                debugCalendarDnd("dragover", event.dataTransfer);
                event.preventDefault();
                setOver(day.date);
              }}
              onDrop={(event) => {
                debugCalendarDnd("drop", event.dataTransfer);
                event.preventDefault();
                setOver(null);
                const taskId = event.dataTransfer.getData("text/plain");
                if (taskId) onDropTask?.(taskId, day.date);
              }}
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
