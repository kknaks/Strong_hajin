import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TaskTimeline } from "./WorkViews";
import type { DirectTask } from "../../lib/viewModels";
vi.mock("../../lib/labels", async (original) => ({ ...await original<typeof import("../../lib/labels")>(), seoulToday: () => "2026-09-29" }));
afterEach(cleanup);
it("aligns month boundaries and clips task spans on the same 14-day axis without inventing dates", () => {
  const tasks: DirectTask[] = [
    { task_id: "range", title: "월 경계 업무", state: "in_progress", version: 1, block_reason: null, start_date: "2026-09-29", due_date: "2026-10-03" },
    { task_id: "past", title: "왼쪽에서 이어짐", state: "open", version: 1, block_reason: null, start_date: "2026-09-01", due_date: "2026-09-24" },
    { task_id: "unscheduled", title: "날짜 없는 업무", state: "open", version: 1, block_reason: null },
  ];
  const onOpen = vi.fn();
  const { container } = render(<TaskTimeline tasks={tasks} onOpen={onOpen} />);
  expect((screen.getByText("2026년 9월") as HTMLElement).style.gridColumn).toBe("2 / span 9");
  expect((screen.getByText("2026년 10월") as HTMLElement).style.gridColumn).toBe("11 / span 5");
  const bars = container.querySelectorAll<HTMLElement>(".timeline-bar");
  expect(bars).toHaveLength(2);
  expect(bars[0].style.gridColumn).toBe("8 / 13");
  expect(bars[1].style.gridColumn).toBe("1 / 4");
  fireEvent.click(bars[0]);
  expect(onOpen).toHaveBeenCalledWith(tasks[0]);
  fireEvent.click(screen.getByRole("button", { name: "다음 2주" }));
  expect(container.querySelectorAll(".timeline-bar")).toHaveLength(0);
  expect(screen.getByRole("button", { name: /날짜 없는 업무/ })).toBeTruthy();
});
