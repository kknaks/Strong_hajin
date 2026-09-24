import { useEffect, useRef, useState, type DragEvent } from "react";

import { calendarDow, calendarHourLabel, calendarScreen } from "../../lib/labels";
import { laneSeats, MIN_BLOCK_MINUTES, packBlocks, packLanes, type CalendarSegment, type TimedBlock } from "./calendarModel";
import { snapClock, type DateEdge } from "./calendarWrites";
import { EventHandle } from "./EventBar";
import { debugCalendarDnd } from "./calendarDndDebug";

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
const SNAP = 30;

/**
 * 포인터가 그 칸의 어느 «분» 에 있나 — 30분 눈금으로 접는다.
 *
 * 좌표가 없는 이벤트(합성 이벤트 · 보조기술이 만든 것)에서는 **0 시로 읽는다** —
 * 유한하지 않은 값이 그대로 흘러가면 `"NaN:NaN"` 이 서버까지 간다.
 */
function minutesAt(column: Element, clientY: number): number {
  const box = column.getBoundingClientRect();
  const raw = ((clientY - box.top) / WEEK_ROW) * 60;
  if (!Number.isFinite(raw)) return 0;
  return Math.max(0, Math.min(DAY_MINUTES, Math.round(raw / SNAP) * SNAP));
}

type SlotDrag = { key: string; edge: DateEdge; column: Element; minutes: number };

/**
 * 주 격자 (G-CAL-06) — **종일 칸 + 0~24시 시간 격자**.
 *
 * 위 칸(요일 머리 + 종일)은 고정, 아래 시간 격자만 스크롤한다. 두 구역이 **같은 날짜 열 7개**를
 * 공유하므로 날짜를 고르면 위아래가 함께 칠해진다.
 *
 * **FE-2 가 쓰기 셋을 얹었다** — 종일 칸 드롭(R1) · 종일 띠의 좌우 손잡이(R3·R4) ·
 * 시간 격자 드롭(R5)과 시간 블록의 세로 손잡이(R6).
 *
 * 규율 둘:
 *
 * - **회의 블록에는 손잡이를 붙이지 않는다** (§F). 시안은 붙이지만, 회의에는 참석자·회의실 예약이
 *   딸려 있어 캘린더에서 조용히 시각을 바꾸면 안 된다. 회의 시각은 **회의 화면**이 바꾼다.
 * - **놓을 때 한 번만 부른다.** 끄는 동안에는 여기서 미리 그려 보여 줄 뿐이다 — 모든 쓰기가
 *   낙관적 잠금이라 끌 때마다 부르면 회차가 매번 어긋난다(§2.3 R6).
 *
 * **FE-3 이 시간 격자에 줄 배치를 얹었다** (증보 K21) — 겹치는 블록이 **밑에 깔리지 않고 나란히**
 * 앉는다. 줄 수와 자리는 `packBlocks` 가 계산하고 여기서는 **좌우 폭으로만** 옮긴다.
 * **줄은 «저장된» 시각으로 잡는다** — 세로 손잡이를 끄는 동안 줄이 바뀌면 블록이 좌우로 튄다.
 */
export function WeekGrid({
  days,
  spans,
  blocks,
  selected,
  onSelect,
  today,
  onDropTask,
  onDropSlot,
  onGrabHandle,
  onResizeSlot,
  ghostAt,
  dragging = false,
  grabbing = false,
}: {
  days: string[];
  /** 종일 칸에 서는 업무 날짜 띠. 시간이 붙은 것은 아래 `blocks` 가 받는다. */
  spans: CalendarSegment[];
  blocks: TimedBlock[];
  selected: string | null;
  onSelect: (date: string | null) => void;
  today: string;
  /** R1 — 종일 칸에 떨어뜨렸다. */
  onDropTask?: (taskId: string, date: string) => void;
  /** R5 — 시간 격자에 떨어뜨렸다. 분은 30분 눈금으로 이미 접혀 있다. */
  onDropSlot?: (taskId: string, date: string, minutes: number) => void;
  /** R3·R4 — 종일 띠의 끝을 잡았다. */
  onGrabHandle?: (taskId: string, edge: DateEdge) => void;
  /** R6 — 시간 블록의 세로 손잡이를 **놓았다**. 끄는 동안에는 불리지 않는다. */
  onResizeSlot?: (block: TimedBlock, edge: DateEdge, minutes: number) => void;
  /** 끌고 있는 것을 «여기» 놓을 수 있나 — 고스트를 그릴지 고르는 데만 쓴다. 거절은 화면이 말로 한다. */
  ghostAt?: (date: string) => boolean;
  dragging?: boolean;
  grabbing?: boolean;
}) {
  const scroller = useRef<HTMLDivElement | null>(null);
  const [unfolded, setUnfolded] = useState(false);
  const [ghost, setGhost] = useState<{ date: string; minutes: number } | null>(null);
  const [slotDrag, setSlotDrag] = useState<SlotDrag | null>(null);
  const lanes = packLanes(spans, days);
  const hidden = Math.max(0, lanes.length - WEEK_LANES);
  const shownLanes = unfolded ? lanes : lanes.slice(0, WEEK_LANES);
  /* 종일 칸의 높이는 줄 수가 정한다 — 22px 띠 + 4px 사이 + 위아래 여백 16px (시안 `week.jsx:54`). */
  const alldayHeight = (shownLanes.length + (hidden ? 1 : 0)) * 22 + shownLanes.length * 4 + 16;
  const px = (minutes: number) => (minutes / 60) * WEEK_ROW;
  const taskIdFromDrop = (event: DragEvent): string | null => {
    // `text/plain` is the product contract. `text` is the WebKit alias published
    // by ScheduleCard for Tauri's native drag implementation.
    const transfer = event.dataTransfer;
    for (const type of ["text/plain", "text"]) {
      const value = transfer?.getData(type).trim() ?? "";
      if (value) return value;
    }
    return null;
  };

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

  /* 세로 손잡이 — 끄는 동안에는 «여기서만» 움직이고, 손을 뗄 때 한 번 위로 올린다. */
  useEffect(() => {
    if (!slotDrag) return undefined;
    const move = (event: PointerEvent) =>
      setSlotDrag((current) => (current ? { ...current, minutes: minutesAt(current.column, event.clientY) } : current));
    const up = () => {
      setSlotDrag((current) => {
        if (current) {
          const block = blocks.find((row) => row.key === current.key);
          if (block) onResizeSlot?.(block, current.edge, current.minutes);
        }
        return null;
      });
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, [blocks, onResizeSlot, slotDrag]);

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
                className={`scax-week__day${picked ? " scax-week__day--picked" : ""}${grabbing ? " scax-month__cell--grabbing" : ""}`}
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
                <div
                  className="scax-week__day-allday"
                  /* 언제나 받는다 — 안 되는 이유는 화면이 말로 한다 (§I). */
                  onDragOver={(event) => {
                    debugCalendarDnd("dragover", event.dataTransfer);
                    event.preventDefault();
                  }}
                  onDrop={(event) => {
                    debugCalendarDnd("drop", event.dataTransfer);
                    event.preventDefault();
                    const taskId = taskIdFromDrop(event);
                    if (taskId) onDropTask?.(taskId, date);
                  }}
                  style={{ height: alldayHeight }}
                >
                  {seats.map((segment, lane) =>
                    segment ? (
                      <span
                        className={`scax-event scax-event--task scax-week__band${date === segment.from ? " scax-week__band--head" : ""}${date === segment.to ? " scax-week__band--tail" : ""}${onGrabHandle ? " scax-event--resizable" : ""}`}
                        key={`${lane}:${segment.key}`}
                      >
                        {/* 제목은 띠의 머리 칸이나 그 주의 첫 칸에서 한 번만 — 칸마다 쓰면 이레 내내 반복된다. */}
                        {date === segment.from || date === days[0] ? (
                          <span className="scax-event__label">{segment.title}</span>
                        ) : null}
                        {/* 그 주 «밖»에서 시작·종료하는 끝에는 손잡이를 안 단다 (§2.3 R3) —
                            `segment.from` 이 이 주에 없으면 어느 칸도 그것과 같지 않다. */}
                        {onGrabHandle && segment.taskId && date === segment.from ? (
                          <EventHandle edge="start" onGrab={onGrabHandle} taskId={segment.taskId} />
                        ) : null}
                        {onGrabHandle && segment.taskId && date === segment.to ? (
                          <EventHandle edge="end" onGrab={onGrabHandle} taskId={segment.taskId} />
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
            const showGhost = dragging && ghost?.date === date && (ghostAt?.(date) ?? true);
            return (
              <div
                className={`scax-week__hours${picked ? " scax-week__hours--picked" : ""}`}
                data-date={date}
                key={date}
                onClick={() => onSelect(picked ? null : date)}
                onDragLeave={() => setGhost((current) => (current?.date === date ? null : current))}
                /* 고스트는 «놓을 수 있는 자리» 에만 뜨지만(R5), 받는 것은 언제나 받는다 —
                   못 받는 자리에서 조용히 튕기면 사람이 이유를 못 듣는다 (§I). */
                onDragOver={(event) => {
                  debugCalendarDnd("dragover", event.dataTransfer);
                  event.preventDefault();
                  setGhost({ date, minutes: minutesAt(event.currentTarget, event.clientY) });
                }}
                onDrop={(event) => {
                  debugCalendarDnd("drop", event.dataTransfer);
                  event.preventDefault();
                  const minutes = minutesAt(event.currentTarget, event.clientY);
                  setGhost(null);
                  const taskId = taskIdFromDrop(event);
                  if (taskId) onDropSlot?.(taskId, date, minutes);
                }}
                role="presentation"
                style={{ height: 24 * WEEK_ROW }}
              >
                {showGhost && ghost ? (
                  <span className="scax-week__ghost" style={{ top: px(ghost.minutes), height: px(60) - 2 }}>
                    {snapClock(ghost.minutes)} – {snapClock(Math.min(ghost.minutes + 60, DAY_MINUTES - 1))}
                  </span>
                ) : null}
                {packBlocks(blocks.filter((block) => block.date === date)).map((block) => {
                  const held = slotDrag?.key === block.key ? slotDrag : null;
                  const startMin = held?.edge === "start" ? held.minutes : block.startMin;
                  const endMin = held?.edge === "end" ? held.minutes : block.endMin;
                  // 손잡이는 **업무 배정에만**. 회의 블록에는 붙지 않는다 (§F).
                  const resizable = Boolean(onResizeSlot) && block.scheduleId !== null;
                  /* 겹치는 것끼리 칸을 나눠 쓴다 (K21). 혼자면 `lanes === 1` 이라
                     `left:3px`·`width:calc(100% - 6px)` 가 되어 **예전 그대로**다 — 회귀가 없다.
                     3px 은 `styles/calendar.css` 가 쓰던 좌우 여백 그대로다. */
                  const share = 100 / block.lanes;
                  return (
                    <span
                      className={`scax-event scax-event--${block.kind} scax-week__slot`}
                      key={block.key}
                      style={{
                        top: px(Math.min(startMin, DAY_MINUTES)),
                        // 30분보다 짧은 배정도 읽을 수 있는 높이는 갖는다 — 겹침 판정과 같은 상수다.
                        height: Math.max(px(MIN_BLOCK_MINUTES), px(endMin - startMin)) - 2,
                        left: `calc(${block.lane * share}% + 3px)`,
                        width: `calc(${share}% - 6px)`,
                      }}
                    >
                      <span className="scax-week__slot-title">{block.title}</span>
                      <span className="scax-week__slot-time">
                        {held
                          ? calendarScreen.clockRange(snapClock(startMin), snapClock(endMin))
                          : calendarScreen.clockRange(block.startLabel, block.endLabel)}
                      </span>
                      {resizable
                        ? (["start", "end"] as const).map((edge) => (
                            <span
                              className={`scax-week__slot-handle scax-week__slot-handle--${edge}`}
                              key={edge}
                              onClick={(event) => event.stopPropagation()}
                              onPointerDown={(event) => {
                                event.stopPropagation();
                                event.preventDefault();
                                const column = (event.currentTarget as HTMLElement).closest(".scax-week__hours");
                                if (!column) return;
                                setSlotDrag({
                                  column,
                                  edge,
                                  key: block.key,
                                  minutes: edge === "start" ? block.startMin : block.endMin,
                                });
                              }}
                              role="presentation"
                              title={edge === "start" ? calendarScreen.grabSlotStart : calendarScreen.grabSlotEnd}
                            />
                          ))
                        : null}
                    </span>
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
