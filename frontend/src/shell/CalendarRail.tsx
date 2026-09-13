import { useState } from "react";

import { Button } from "../ds/Button";
import { Empty } from "../ds/Empty";
import { SegmentedControl } from "../ds/SegmentedControl";
import { Skeleton } from "../ds/Skeleton";
import { StatusNote } from "../ds/StatusNote";
import { Badge } from "../ds/Badge";
import { formatDate, seoulToday, taskStateLabel } from "../lib/labels";
import { type DirectTask } from "../lib/viewModels";
import { TaskCalendar } from "../features/work/WorkViews";

/**
 * 우 레일 — 캘린더 (바퀴 5b).
 *
 * **새 데이터 원천을 만들지 않는다**(K-3) — 본문 목록이 쓰는 `tasks` 그대로다.
 * 주간·월간은 우리 `TaskCalendar`(`WorkViews.tsx`)를 그대로 세우고, 「오늘」만 시안의 일정 목록 모양으로 그린다.
 *
 * ── 시안에 있는데 안 그린 것 ──
 * **근무 시간**(`.scax-calendar-rail__hours`). 근무 시간을 담는 계약이 `api.ts`·`viewModels.ts`
 * 어디에도 없다 — 그리려면 값을 지어내야 하므로 그리지 않았다 (J-1 · K-4).
 *
 * 별도 화면 `CalendarPage.tsx` 는 이 바퀴가 건드리지 않는다(바퀴 8).
 */

export type CalendarRailState = "loading" | "error" | "ready";

type Range = "today" | "week" | "month";

const RANGES: ReadonlyArray<{ value: Range; label: string }> = [
  { value: "today", label: "오늘" },
  { value: "week", label: "주간" },
  { value: "month", label: "월간" },
];

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
  const dueToday = tasks.filter((task) => task.due_date === today && task.state !== "cancelled");

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
    body =
      dueToday.length === 0 ? (
        <Empty title="오늘 기한인 업무가 없습니다" />
      ) : (
        <>
          <div className="scax-calendar-rail__day">
            <p className="scax-calendar-rail__date">{formatDate(today)}</p>
            {/* 시안은 여기에 「근무 시간」을 두지만 그 값을 담는 계약이 우리에게 없다 (K-4). */}
          </div>
          <div className="scax-calendar-rail__list">
            {dueToday.map((task) => (
              <article className="scax-agenda-item openable" key={task.task_id} onClick={() => onOpen(task)}>
                <div className="scax-agenda-item__top">
                  <Badge tone={task.state === "blocked" ? "danger" : "neutral"}>
                    {taskStateLabel[task.state] ?? task.state}
                  </Badge>
                </div>
                <p className="scax-agenda-item__title">{task.title}</p>
                {task.block_reason && <p className="scax-agenda-item__sub">막힘 사유: {task.block_reason}</p>}
              </article>
            ))}
          </div>
        </>
      );
  } else {
    body = <TaskCalendar mode={range} onOpen={onOpen} tasks={tasks} />;
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
