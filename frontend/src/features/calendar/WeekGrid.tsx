import { useEffect, useRef, useState } from "react";

import { calendarDow, calendarHourLabel, calendarScreen } from "../../lib/labels";
import { laneSeats, packLanes, type CalendarSegment, type TimedBlock } from "./calendarModel";

/**
 * ⚠ 이 값은 `styles/calendar.css` 의 `.scax-week__hours` 배경
 * `repeating-linear-gradient(… 0 1px, transparent 1px 56px)` 과 **짝**이다.
 * 한쪽만 고치면 시간 눈금과 블록이 어긋난다 (시안 `week.jsx:6`).
 */
const WEEK_ROW = 56;
/** 처음 보여 줄 시각 — 주 뷰는 **8시에 맞춰 열린다** (SPEC §2.1). */
const WEEK_OPEN = 8;
/** 종일 칸의 줄 상한. 넘치면 「N건 펴기 / 접기」 (SPEC §2.1). */
const WEEK_LANES = 3;
const DAY_MINUTES = 24 * 60;

/**
 * 주 격자 (G-CAL-06) — **종일 칸 + 0~24시 시간 격자**.
 *
 * 위 칸(요일 머리 + 종일)은 고정, 아래 시간 격자만 스크롤한다. 두 구역이 **같은 날짜 열 7개**를
 * 공유하므로 날짜를 고르면 위아래가 함께 칠해진다.
 *
 * FE-1 은 읽기까지다 — 드롭·손잡이·고스트는 FE-2 가 이 위에 얹는다.
 * **회의 블록에는 손잡이를 붙이지 않는다**(§F) — 캘린더는 회의에 쓰기 명령을 내지 않는다.
 */
export function WeekGrid({
  days,
  spans,
  blocks,
  selected,
  onSelect,
  today,
}: {
  days: string[];
  /** 종일 칸에 서는 업무 날짜 띠. 시간이 붙은 것은 아래 `blocks` 가 받는다. */
  spans: CalendarSegment[];
  blocks: TimedBlock[];
  selected: string | null;
  onSelect: (date: string | null) => void;
  today: string;
}) {
  const scroller = useRef<HTMLDivElement | null>(null);
  const [unfolded, setUnfolded] = useState(false);
  const lanes = packLanes(spans, days);
  const hidden = Math.max(0, lanes.length - WEEK_LANES);
  const shownLanes = unfolded ? lanes : lanes.slice(0, WEEK_LANES);
  /* 종일 칸의 높이는 줄 수가 정한다 — 22px 띠 + 4px 사이 + 위아래 여백 16px (시안 `week.jsx:54`). */
  const alldayHeight = (shownLanes.length + (hidden ? 1 : 0)) * 22 + shownLanes.length * 4 + 16;
  const px = (minutes: number) => (minutes / 60) * WEEK_ROW;

  // 하루를 0시부터 펴 두고 «처음 보이는 자리»만 8시로 맞춘다 — 그 앞 시간도 올려서 볼 수 있다.
  useEffect(() => {
    const open = () => {
      if (scroller.current) scroller.current.scrollTop = WEEK_OPEN * WEEK_ROW;
    };
    open();
    const frame = typeof requestAnimationFrame === "function" ? requestAnimationFrame(open) : null;
    return () => {
      if (frame !== null) cancelAnimationFrame(frame);
    };
  }, []);

  return (
    <section aria-label={calendarScreen.weekLabel} className="scax-week">
      <div className="scax-week__top">
        <div className="scax-week__ruler-top">
          <div className="scax-week__ruler-head" />
          <div className="scax-week__ruler-allday" style={{ height: alldayHeight }}>
            <span>{calendarScreen.allDay}</span>
            {hidden ? (
              <button className="scax-week__fold" onClick={() => setUnfolded((open) => !open)} type="button">
                {unfolded ? calendarScreen.fold : calendarScreen.unfold(hidden)}
              </button>
            ) : null}
          </div>
        </div>
        <div className="scax-week__days">
          {days.map((date, index) => {
            const seats = laneSeats(shownLanes, date);
            const picked = selected === date;
            return (
              <div
                className={`scax-week__day${picked ? " scax-week__day--picked" : ""}`}
                data-date={date}
                key={date}
                onClick={() => onSelect(picked ? null : date)}
                role="presentation"
              >
                <div className="scax-week__day-head">
                  <span className={`scax-week__day-dow${index === 0 ? " scax-week__day-dow--sun" : ""}`}>
                    {calendarDow[index]}
                  </span>
                  <span className={`scax-week__day-date${date === today ? " scax-week__day-date--today" : ""}`}>
                    {Number(date.slice(8, 10))}
                  </span>
                </div>
                <div className="scax-week__day-allday" style={{ height: alldayHeight }}>
                  {seats.map((segment, lane) =>
                    segment ? (
                      <span
                        className={`scax-event scax-event--task scax-week__band${date === segment.from ? " scax-week__band--head" : ""}${date === segment.to ? " scax-week__band--tail" : ""}`}
                        key={`${lane}:${segment.key}`}
                      >
                        {/* 제목은 띠의 머리 칸이나 그 주의 첫 칸에서 한 번만 — 칸마다 쓰면 이레 내내 반복된다. */}
                        {date === segment.from || date === days[0] ? (
                          <span className="scax-event__label">{segment.title}</span>
                        ) : null}
                      </span>
                    ) : (
                      <span aria-hidden className="scax-week__band scax-week__band--empty" key={`ghost-${lane}`} />
                    ),
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="scax-week__scroll" ref={scroller}>
        <div className="scax-week__ruler-hours">
          {Array.from({ length: 24 }, (_, hour) => (
            <span className="scax-week__ruler-hour" key={hour} style={{ height: WEEK_ROW }}>
              {calendarHourLabel(hour)}
            </span>
          ))}
        </div>
        <div className="scax-week__days">
          {days.map((date) => {
            const picked = selected === date;
            return (
              <div
                className={`scax-week__hours${picked ? " scax-week__hours--picked" : ""}`}
                data-date={date}
                key={date}
                onClick={() => onSelect(picked ? null : date)}
                role="presentation"
                style={{ height: 24 * WEEK_ROW }}
              >
                {blocks
                  .filter((block) => block.date === date)
                  .map((block) => (
                    <span
                      className={`scax-event scax-event--${block.kind} scax-week__slot`}
                      key={block.key}
                      style={{
                        top: px(Math.min(block.startMin, DAY_MINUTES)),
                        // 30분보다 짧은 배정도 읽을 수 있는 높이는 갖는다.
                        height: Math.max(px(30), px(block.endMin - block.startMin)) - 2,
                      }}
                    >
                      <span className="scax-week__slot-title">{block.title}</span>
                      <span className="scax-week__slot-time">
                        {calendarScreen.clockRange(block.startLabel, block.endLabel)}
                      </span>
                    </span>
                  ))}
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
