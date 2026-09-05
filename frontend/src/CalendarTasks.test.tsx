import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DirectTask } from "./viewModels";
import { TaskCalendar, taskSpan, weekSegments } from "./WorkViews";

const task = (overrides: Partial<DirectTask> & { task_id: string; title: string }): DirectTask => ({
  state: "open",
  version: 1,
  block_reason: null,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
  ...overrides,
});

describe("what a task's dates mean on a calendar", () => {
  it("uses the planned dates and never invents one from when the row was written", () => {
    // Both dates: the range is exactly those days, ends included.
    expect(taskSpan(task({ task_id: "t1", title: "기간 업무", start_date: "2026-09-07", due_date: "2026-09-09" }))).toEqual({
      start: "2026-09-07",
      end: "2026-09-09",
    });
    // Only a due date: one day, at the deadline. Not "since it was created", not "until today".
    expect(taskSpan(task({ task_id: "t2", title: "기한만", due_date: "2026-09-10" }))).toEqual({
      start: "2026-09-10",
      end: "2026-09-10",
    });
    // Only a start date: one day, at the start.
    expect(taskSpan(task({ task_id: "t3", title: "시작만", start_date: "2026-09-02" }))).toEqual({
      start: "2026-09-02",
      end: "2026-09-02",
    });
    // Neither: it is not on the calendar at all.
    expect(taskSpan(task({ task_id: "t4", title: "날짜 없음" }))).toBeNull();
  });
});

const week = (start: string) => Array.from({ length: 7 }, (_, index) => {
  const date = new Date(`${start}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + index);
  return date.toISOString().slice(0, 10);
});

describe("laying work out across a week", () => {
  it("makes one bar per task per week, clipped to the week and marked where it continues", () => {
    const crossing = task({ task_id: "t1", title: "주를 넘는 업무", start_date: "2026-09-04", due_date: "2026-09-08" });
    const first = weekSegments([crossing], week("2026-08-30"), 3);
    const second = weekSegments([crossing], week("2026-09-06"), 3);

    // Sun 2026-08-30 … Sat 2026-09-05: the bar starts on Friday and runs to the edge.
    expect(first.segments).toHaveLength(1);
    expect(first.segments[0]).toMatchObject({ column: 6, length: 2, continuesAfter: true, continuesBefore: false, lane: 0 });
    // The next week picks it up on Sunday and ends on Tuesday.
    expect(second.segments[0]).toMatchObject({ column: 1, length: 3, continuesBefore: true, continuesAfter: false });
    // It is the same task on both sides, never two rows pretending to be two tasks.
    expect(first.segments[0].task.task_id).toBe(second.segments[0].task.task_id);
  });

  it("keeps overlapping work in stable lanes instead of moving it around", () => {
    const rows = [
      task({ task_id: "a", title: "A", start_date: "2026-09-07", due_date: "2026-09-09" }),
      task({ task_id: "b", title: "B", start_date: "2026-09-08", due_date: "2026-09-10" }),
      task({ task_id: "c", title: "C", start_date: "2026-09-11", due_date: "2026-09-11" }),
    ];
    const laid = weekSegments(rows, week("2026-09-06"), 3);
    const lanes = Object.fromEntries(laid.segments.map((segment) => [segment.task.task_id, segment.lane]));
    expect(lanes).toEqual({ a: 0, b: 1, c: 0 }); // C reuses the free lane it does not overlap
  });

  it("counts what a cell cannot show rather than dropping it", () => {
    const rows = ["a", "b", "c", "d"].map((id) =>
      task({ task_id: id, title: id.toUpperCase(), start_date: "2026-09-08", due_date: "2026-09-08" }),
    );
    const laid = weekSegments(rows, week("2026-09-06"), 3);
    expect(laid.segments.map((segment) => segment.task.task_id)).toEqual(["a", "b", "c"]);
    expect(laid.hiddenByDay["2026-09-08"]).toBe(1);
  });
});

describe("the calendar a person reads", () => {
  afterEach(cleanup);

  it("draws one bar for a range, one for a deadline, and nothing for a task without dates", () => {
    const onOpen = vi.fn();
    render(
      <TaskCalendar
        anchorDate="2026-09-08"
        mode="month"
        onOpen={onOpen}
        tasks={[
          task({ task_id: "t1", title: "기간 업무", start_date: "2026-09-07", due_date: "2026-09-09", state: "in_progress" }),
          task({ task_id: "t2", title: "기한만 업무", due_date: "2026-09-10" }),
          task({ task_id: "t3", title: "날짜 없는 업무" }),
        ]}
      />,
    );
    const grid = screen.getByRole("grid", { name: "업무 캘린더" });
    const range = within(grid).getByRole("button", { name: /기간 업무/ });
    // The label says what the bar covers, in the product's date format.
    expect(range.getAttribute("aria-label")).toContain("2026/09/07 – 2026/09/09");
    expect(range.getAttribute("aria-label")).toContain("진행 중");
    expect(range.style.gridColumn).toBe("2 / span 3"); // Mon–Wed, with Sunday as the first column

    const deadline = within(grid).getByRole("button", { name: /기한만 업무/ });
    expect(deadline.getAttribute("aria-label")).toContain("2026/09/10 기한");
    expect(deadline.style.gridColumn).toBe("5 / span 1");

    expect(within(grid).queryByText("날짜 없는 업무")).toBeNull();

    fireEvent.click(range);
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ task_id: "t1" }));
  });
});
