import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { CalendarMeetingRow } from "../lib/viewModels";
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

/* ──────────────────────────────────────────────────────────────────────────────
   증보 K23·K24 — 업무 탭 우측 캘린더에 «회의도» 선다.
   축이 둘이고 다르다: 업무는 그 탭이 들고 있는 것 그대로, 회의는 탭과 무관하게 내 회의다.
   ────────────────────────────────────────────────────────────────────────────── */

const meeting = (over: Partial<CalendarMeetingRow> = {}): CalendarMeetingRow => ({
  kind: "meeting",
  meeting_id: "m1",
  title: "주간 회의",
  // 2026-09-18 01:00Z = 서울 10:00. 레일은 **사무실 시간대**로 날을 가른다.
  starts_at: "2026-09-18T01:00:00+00:00",
  ends_at: "2026-09-18T02:00:00+00:00",
  location: "3층 회의실",
  status: "scheduled",
  viewer_relation: "attendee",
  created_by: "mina",
  attendee_count: 2,
  created_by_display_name: "민아 (구성원)",
  ...over,
});

it("회의가 선다 — 업무가 하나도 없어도 그 날이 비어 보이지 않는다 (K23)", () => {
  render(<CalendarRail tasks={[]} meetings={[meeting()]} state="ready" onOpen={vi.fn()} onRetry={vi.fn()} />);
  expect(screen.getByText("주간 회의")).toBeTruthy();
  expect(screen.getByText("10:00–11:00")).toBeTruthy();
  expect(screen.getByText("3층 회의실")).toBeTruthy();
  expect(screen.queryByText("오늘 일정이 없습니다")).toBeNull();
});

it("회의 줄은 누를 수 없다 — 캘린더는 회의에 명령을 내지 않는다 (§2.4)", () => {
  const { container } = render(<CalendarRail tasks={[]} meetings={[meeting()]} state="ready" onOpen={vi.fn()} onRetry={vi.fn()} />);
  const row = [...container.querySelectorAll(".scax-agenda-item")].find((node) => node.textContent?.includes("주간 회의"))!;
  expect(row.classList.contains("openable")).toBe(false);
});

it("업무가 먼저, 회의는 시간순 — 캘린더 화면의 셀 순서와 같은 규칙이다 (K20)", () => {
  const { container } = render(
    <CalendarRail
      tasks={calendarRailTasks("2026-09-18")}
      meetings={[
        meeting({ meeting_id: "late", title: "오후 회의", starts_at: "2026-09-18T05:00:00+00:00", ends_at: "2026-09-18T06:00:00+00:00" }),
        meeting({ meeting_id: "early", title: "오전 회의" }),
      ]}
      state="ready"
      onOpen={vi.fn()}
      onRetry={vi.fn()}
    />,
  );
  const titles = [...container.querySelectorAll(".scax-agenda-item__title")].map((node) => node.textContent);
  expect(titles.slice(-2)).toEqual(["오전 회의", "오후 회의"]);
  expect(titles[0]).toBe("오늘 업무 검토");
});

it("취소된 회의는 일정이 아니다 — 취소 업무를 빼는 것과 같은 규칙이다", () => {
  render(<CalendarRail tasks={[]} meetings={[meeting({ status: "cancelled" })]} state="ready" onOpen={vi.fn()} onRetry={vi.fn()} />);
  expect(screen.getByText("오늘 일정이 없습니다")).toBeTruthy();
});

it("회의만 있는 날에도 점이 찍힌다 — 점과 목록이 같은 답을 쓴다", () => {
  const { container } = render(
    <CalendarRail
      tasks={[]}
      meetings={[meeting({ meeting_id: "m2", starts_at: "2026-09-19T01:00:00+00:00", ends_at: "2026-09-19T02:00:00+00:00" })]}
      state="ready"
      onOpen={vi.fn()}
      onRetry={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("tab", { name: "주" }));
  // 19일 칸의 점 — 오늘(18일)의 점과 별개다.
  expect(container.querySelectorAll(".scax-day-cell__dot")).toHaveLength(2);
  expect(screen.getByRole("button", { name: /9월 19일 토요일 \(1\)/ })).toBeTruthy();
});

it("그리는 범위를 페이지에 알린다 — 질의는 페이지가 한다 (K24)", () => {
  const onRange = vi.fn();
  render(<CalendarRail tasks={[]} meetings={[]} state="ready" onOpen={vi.fn()} onRange={onRange} onRetry={vi.fn()} />);
  expect(onRange).toHaveBeenLastCalledWith("2026-09-18", "2026-09-18");
  fireEvent.click(screen.getByRole("tab", { name: "주" }));
  expect(onRange).toHaveBeenLastCalledWith("2026-09-13", "2026-09-19");
  // 같은 주 안에서 날짜만 고르면 범위가 그대로라 **다시 묻지 않는다.**
  const calls = onRange.mock.calls.length;
  fireEvent.click(screen.getByRole("button", { name: "17" }));
  expect(onRange.mock.calls.length).toBe(calls);
  fireEvent.click(screen.getByRole("tab", { name: "월" }));
  // 월은 7×N 격자가 42칸이라 **달 밖 칸까지** 받는다 — 그 칸도 점을 찍는다.
  expect(onRange).toHaveBeenLastCalledWith("2026-08-30", "2026-10-10");
});

it("회의만 못 읽은 것을 «없다»로 보여 주지 않는다", () => {
  render(<CalendarRail tasks={[]} meetings={[]} meetingsFailed state="ready" onOpen={vi.fn()} onRetry={vi.fn()} />);
  expect(screen.getByText("회의를 불러오지 못했습니다.")).toBeTruthy();
});
