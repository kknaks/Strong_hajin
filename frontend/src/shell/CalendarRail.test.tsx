import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CalendarRail } from "./CalendarRail";
import { calendarRailTasks } from "./fixtures/calendarRail";

vi.mock("../lib/labels", async (importOriginal) => ({
  ...await importOriginal<typeof import("../lib/labels")>(),
  seoulToday: () => "2026-09-18",
}));
afterEach(cleanup);

it.each(["주", "월"])("%s: 빈 일정에서도 오늘 dot이 있고 다른 날짜 선택·기간 이동과 구분된다", (range) => {
  const { container } = render(<CalendarRail tasks={[]} state="ready" onOpen={vi.fn()} onRetry={vi.fn()} />);
  fireEvent.click(screen.getByRole("tab", { name: range }));
  const today = container.querySelector('[aria-current="date"]')!;
  expect(today.textContent).toBe("18");
  expect(today.querySelector(".scax-day-cell__dot")).not.toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "19" }));
  expect(container.querySelector('[aria-current="date"]')).toBe(today);
  expect(container.querySelectorAll(".scax-day-cell__dot")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: range === "주" ? "다음 주" : "다음 달" }));
  expect(container.querySelector('[aria-current="date"]')).toBeNull();
});

it("기존 일정 dot과 업무 열기 동작을 유지하고 취소 업무를 제외한다", () => {
  const tasks = calendarRailTasks("2026-09-18");
  const onOpen = vi.fn();
  const { container } = render(<CalendarRail tasks={tasks} state="ready" onOpen={onOpen} onRetry={vi.fn()} />);
  fireEvent.click(screen.getByText("오늘 업무 검토"));
  expect(onOpen).toHaveBeenCalledWith(tasks[0]);
  fireEvent.click(screen.getByRole("tab", { name: "월" }));
  expect(container.querySelectorAll(".scax-day-cell__dot")).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "20" }));
  expect(screen.queryByText(tasks[3].title)).toBeNull();
});

it("formats Korean dates and initially folds past days while keeping today open", () => {
  const tasks = calendarRailTasks("2026-09-18");
  tasks.push({ ...tasks[0], task_id: "past", title: "어제 업무", due_date: "2026-09-17" });
  render(<CalendarRail tasks={tasks} state="ready" onOpen={vi.fn()} onRetry={vi.fn()} />);
  expect(screen.getByText("9월 18일 금요일")).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "주" }));
  expect(screen.getByText("2026년 9월 3주 차")).toBeTruthy();
  const past = screen.getByRole("button", { name: "9월 17일 목요일 (1)" });
  expect(past.getAttribute("aria-expanded")).toBe("false");
  expect(screen.getByRole("button", { name: "9월 18일 금요일 (2)" }).getAttribute("aria-expanded")).toBe("true");
  expect(screen.queryByText("어제 업무")).toBeNull();
  fireEvent.click(past);
  expect(screen.getByText("어제 업무")).toBeTruthy();
  fireEvent.click(past);
  expect(screen.queryByText("어제 업무")).toBeNull();
  fireEvent.click(screen.getByRole("tab", { name: "월" }));
  expect(screen.getByText("2026년 9월")).toBeTruthy();
});
