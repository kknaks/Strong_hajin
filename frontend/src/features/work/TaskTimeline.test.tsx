import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TaskTimeline } from "./WorkViews";
import type { DirectTask } from "../../lib/viewModels";
// 오늘 = 2026-09-29(화). 그 주 월요일이 09-28 이라 기본 범위는 09-21 ~ 10-25 (W-1 ~ W+3).
vi.mock("../../lib/labels", async (original) => ({ ...await original<typeof import("../../lib/labels")>(), seoulToday: () => "2026-09-29" }));
afterEach(cleanup);

const task = (task_id: string, title: string, start_date?: string, due_date?: string): DirectTask => ({
  task_id, title, state: "in_progress", version: 1, block_reason: null, start_date, due_date,
});
const rangeText = () => (document.querySelector(".work-timeline .stepper b") as HTMLElement).textContent;
const scroller = () => document.querySelector(".timeline-scroll") as HTMLElement;

describe("내 업무 › 타임라인 — 범위·이동 (WORK-008 D-01 · SPEC-001 U-16)", () => {
  it("범위 안의 업무만 있으면 기본 범위 W-1 ~ W+3 다섯 주다", () => {
    const { container } = render(<TaskTimeline onOpen={vi.fn()} tasks={[task("in", "안쪽 업무", "2026-09-29", "2026-10-03")]} />);
    expect(rangeText()).toBe("2026/09/21 – 2026/10/25");
    expect(container.querySelectorAll(".timeline-head > span:not(.timeline-label-col)")).toHaveLength(35);
    expect((container.querySelector(".timeline-bar") as HTMLElement).style.gridColumn).toBe("9 / 14");
  });

  it("범위 밖 업무가 있으면 첫 화면을 주 경계까지 넓혀 자르지 않는다 — 막대가 잘리지 않는다", () => {
    const tasks: DirectTask[] = [
      task("range", "월 경계 업무", "2026-09-29", "2026-10-03"),
      // 09-01(화) 시작 → 그 주 월요일 08-31 까지 넓힌다
      task("past", "앞쪽 밖 업무", "2026-09-01", "2026-09-24"),
      // 11-04(수) 끝 → 그 주 일요일 11-08 까지 넓힌다
      task("late", "뒤쪽 밖 업무", "2026-10-20", "2026-11-04"),
      { task_id: "unscheduled", title: "날짜 없는 업무", state: "open", version: 1, block_reason: null },
    ];
    const onOpen = vi.fn();
    const { container } = render(<TaskTimeline onOpen={onOpen} tasks={tasks} />);
    expect(rangeText()).toBe("2026/08/31 – 2026/11/08");
    expect((screen.getByText("2026년 8월") as HTMLElement).style.gridColumn).toBe("2 / span 1");
    expect((screen.getByText("2026년 9월") as HTMLElement).style.gridColumn).toBe("3 / span 30");
    const bars = container.querySelectorAll<HTMLElement>(".timeline-bar");
    expect(bars).toHaveLength(3);
    expect(bars[0].style.gridColumn).toBe("30 / 35");
    // 시작(09-01 = 둘째 칸)부터 그대로 선다 — 왼쪽에서 잘린 1번 칸이 아니다.
    expect(bars[1].style.gridColumn).toBe("2 / 26");
    // 끝(11-04 = 66번째 칸)까지 그대로 선다.
    expect(bars[2].style.gridColumn).toBe("51 / 67");
    fireEvent.click(bars[0]);
    expect(onOpen).toHaveBeenCalledWith(tasks[0]);
    // 날짜 없는 업무도 행은 선다 — 막대만 없다.
    expect(screen.getByRole("button", { name: /날짜 없는 업무/ })).toBeTruthy();
  });

  it("‹ › 는 5주 창을 1주씩 밀고, 민 창에서는 넓히지 않는다", () => {
    const tasks = [task("past", "앞쪽 밖 업무", "2026-09-01", "2026-09-24")];
    const { container } = render(<TaskTimeline onOpen={vi.fn()} tasks={tasks} />);
    expect(rangeText()).toBe("2026/08/31 – 2026/10/25");

    fireEvent.click(screen.getByRole("button", { name: "다음 주" }));
    // 기준 주 10-05 → 09-28 ~ 11-01. 넓히지 않으므로 그 업무는 창 밖이라 막대가 없다.
    expect(rangeText()).toBe("2026/09/28 – 2026/11/01");
    expect(container.querySelectorAll(".timeline-head > span:not(.timeline-label-col)")).toHaveLength(35);
    expect(container.querySelectorAll(".timeline-bar")).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "이전 주" }));
    fireEvent.click(screen.getByRole("button", { name: "이전 주" }));
    // 기준 주 09-21 → 09-14 ~ 10-18. 오늘 기준 첫 화면이 아니므로 넓히지 않고 막대를 창에 맞춰 자른다.
    expect(rangeText()).toBe("2026/09/14 – 2026/10/18");
    expect((container.querySelector(".timeline-bar") as HTMLElement).style.gridColumn).toBe("1 / 12");
  });

  it("「오늘」은 오늘 기준 첫 화면(넓힘 포함)으로 돌아온다", () => {
    render(<TaskTimeline onOpen={vi.fn()} tasks={[task("past", "앞쪽 밖 업무", "2026-09-01", "2026-09-24")]} />);
    fireEvent.click(screen.getByRole("button", { name: "다음 주" }));
    fireEvent.click(screen.getByRole("button", { name: "다음 주" }));
    expect(rangeText()).toBe("2026/10/05 – 2026/11/08");
    fireEvent.click(screen.getByRole("button", { name: "오늘" }));
    expect(rangeText()).toBe("2026/08/31 – 2026/10/25");
  });

  it("처음 열 때 오늘이 보이게 스크롤하고, 민 뒤에는 되감지 않으며, 「오늘」이 다시 맞춘다", () => {
    render(<TaskTimeline onOpen={vi.fn()} tasks={[task("in", "안쪽 업무", "2026-09-29", "2026-10-03")]} />);
    // 축 09-21 ~ 에서 오늘(09-29)은 아홉째 칸(index 8) — 앞 두 날을 남겨 (8 - 2) × 34px = 204px.
    expect(scroller().scrollLeft).toBe(204);
    scroller().scrollLeft = 0;
    fireEvent.click(screen.getByRole("button", { name: "다음 주" }));
    expect(scroller().scrollLeft).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: "오늘" }));
    expect(scroller().scrollLeft).toBe(204);
  });
});

/* WORK-008 D-01 fix2 — 업무명 열이 가로 스크롤에 고정되고, 첫 진입의 오늘이 그 고정 열 뒤에 숨지 않는다. */
describe("업무명 열 틀고정 (D-01 fix2)", () => {
  async function screensCss(): Promise<string> {
    // @ts-expect-error — 이 리포는 @types/node 를 두지 않는다 (선례 `ds/HoverContrast.test.tsx:21`).
    const { readFileSync } = await import("node:fs");
    return (readFileSync("src/styles/screens-a.css", "utf8") as string).replace(/\/\*[\s\S]*?\*\//g, "").replace(/\s+/g, "");
  }

  it("업무명 열(머리줄의 기간·업무명 칸 포함)이 sticky·z-index·배경을 함께 갖고, 바깥 격자가 sticky 기준을 빼앗지 않는다", async () => {
    const { container } = render(<TaskTimeline onOpen={vi.fn()} tasks={[task("in", "안쪽 업무", "2026-09-29", "2026-10-03")]} />);
    // 고정 칸은 셋 다 같은 클래스다 — 머리줄 둘(기간·업무명)과 업무 행의 업무명.
    const labels = Array.from(container.querySelectorAll(".work-timeline .timeline-label-col")).map((cell) => cell.textContent);
    expect(labels).toEqual(["기간", "업무명", expect.stringContaining("안쪽 업무")]);
    const css = await screensCss();
    expect(css).toContain(".work-timeline.timeline-label-col{position:sticky;left:0;z-index:2;background:var(--scax-color-surface)}");
    expect(css).toContain(".work-timeline.timeline-grid{overflow:clip}");
  });

  it("첫 진입 스크롤은 고정 열 폭을 뺀 자리다 — 오늘 칸이 고정 열 뒤에 숨지 않는다", async () => {
    render(<TaskTimeline onOpen={vi.fn()} tasks={[task("in", "안쪽 업무", "2026-09-29", "2026-10-03")]} />);
    const css = await screensCss();
    const labelWidth = Number(/--timeline-label:(\d+)px/.exec(css)?.[1]);
    expect(labelWidth).toBe(200);
    const todayIndex = 8; // 축 09-21 ~ 에서 09-29
    const todayX = labelWidth + todayIndex * 34; // 오늘 칸 왼쪽 모서리(콘텐츠 좌표)
    const scrollLeft = scroller().scrollLeft;
    expect(scrollLeft).toBe(204);
    // 고정 열 바로 오른쪽(scrollLeft + 열 폭)보다 오늘이 오른쪽에 있다 — 두 칸(68px) 앞을 남긴다.
    expect(todayX - (scrollLeft + labelWidth)).toBe(68);
  });
});
