import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DatePicker } from "./DatePicker";
import { datePickerLabel, formatMonthLong, seoulToday, weekdayNames } from "../lib/labels";

afterEach(cleanup);

/** 2026-09-01 은 화요일 — 격자는 2026-08-30(일) 에서 시작해 42칸이다. */
const VALUE = "2026-09-15";

function open(props: Partial<Parameters<typeof DatePicker>[0]> = {}) {
  const onChange = props.onChange ?? vi.fn();
  render(<DatePicker formatMonth={formatMonthLong} labels={datePickerLabel} today={seoulToday()} weekdayNames={weekdayNames} label="종료일" onChange={onChange} value={VALUE} {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "종료일 달력 열기" }));
  return onChange;
}

describe("DatePicker", () => {
  it("닫혀 있을 때는 트리거뿐이다 — 화면에 달력이 없다", () => {
    render(<DatePicker formatMonth={formatMonthLong} labels={datePickerLabel} today={seoulToday()} weekdayNames={weekdayNames} label="종료일" onChange={vi.fn()} value={VALUE} />);
    const trigger = screen.getByRole("button", { name: "종료일 달력 열기" });
    expect(trigger.className).toContain("scax-button--text-neutral scax-button--sm");
    expect(trigger.querySelector("svg")?.getAttribute("width")).toBe("14");
    expect(screen.queryByRole("grid")).toBeNull();
  });

  it("열면 Popover 패널 안에 그 달의 격자가 뜬다 (v2 14 — 스크림 없음)", () => {
    open();
    const panel = screen.getByRole("group", { name: "종료일" });
    expect(panel.className).toContain("popover");
    expect(panel.style.width).toBe("280px");
    expect(screen.getByText("2026년 9월")).toBeTruthy();
    // 7 × 6 = 42 칸
    expect(screen.getAllByRole("gridcell").length).toBe(42);
    expect(screen.getAllByRole("row").length).toBe(6);
  });

  it("선택일이 표시되고 열릴 때 포커스가 거기에 선다", () => {
    open();
    const selected = screen.getByRole("gridcell", { name: "2026/09/15" });
    expect(selected.getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(selected);
  });

  it("값이 없으면 오늘로 포커스가 서고 고른 날은 없다", () => {
    open({ value: "" });
    const today = screen.getByRole("gridcell", { name: /./, current: "date" });
    expect(document.activeElement).toBe(today);
    expect(screen.queryAllByRole("gridcell", { selected: true }).length).toBe(0);
  });

  it("날짜를 고르면 ISO 로 올려 주고 닫는다", () => {
    const onChange = open();
    fireEvent.click(screen.getByRole("gridcell", { name: "2026/09/23" }));
    expect(onChange).toHaveBeenCalledWith("2026-09-23");
    expect(screen.queryByRole("grid")).toBeNull();
  });

  it("이번 달 밖 날짜는 흐리게 두되 고를 수는 있다", () => {
    const onChange = open();
    const outside = screen.getByRole("gridcell", { name: "2026/08/31" });
    expect(outside.className).toContain("outside");
    fireEvent.click(outside);
    expect(onChange).toHaveBeenCalledWith("2026-08-31");
  });

  it("달을 앞뒤로 넘긴다", () => {
    open();
    fireEvent.click(screen.getByRole("button", { name: "다음 달" }));
    expect(screen.getByText("2026년 10월")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "이전 달" }));
    fireEvent.click(screen.getByRole("button", { name: "이전 달" }));
    expect(screen.getByText("2026년 8월")).toBeTruthy();
  });

  it("min·max 밖은 누를 수 없다", () => {
    const onChange = open({ max: "2026-09-20", min: "2026-09-10" });
    const before = screen.getByRole("gridcell", { name: "2026/09/09" });
    const inside = screen.getByRole("gridcell", { name: "2026/09/11" });
    const after = screen.getByRole("gridcell", { name: "2026/09/21" });
    expect((before as HTMLButtonElement).disabled).toBe(true);
    expect((after as HTMLButtonElement).disabled).toBe(true);
    expect((inside as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(before);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("지우기는 빈 값을 올리고 닫는다", () => {
    const onChange = open();
    fireEvent.click(screen.getByRole("button", { name: "지우기" }));
    expect(onChange).toHaveBeenCalledWith("");
    expect(screen.queryByRole("grid")).toBeNull();
  });

  it("오늘은 오늘 날짜를 올린다", () => {
    const onChange = open();
    fireEvent.click(screen.getByRole("button", { name: "오늘" }));
    expect(onChange).toHaveBeenCalledWith(seoulToday());
  });

  it("방향키로 날짜를 옮기고 Enter 로 고른다", () => {
    const onChange = open();
    const grid = screen.getByRole("grid");
    fireEvent.keyDown(grid, { key: "ArrowRight" });
    expect(document.activeElement?.getAttribute("aria-label")).toBe("2026/09/16");
    fireEvent.keyDown(grid, { key: "ArrowDown" });
    expect(document.activeElement?.getAttribute("aria-label")).toBe("2026/09/23");
    fireEvent.keyDown(grid, { key: "ArrowUp" });
    fireEvent.keyDown(grid, { key: "ArrowLeft" });
    expect(document.activeElement?.getAttribute("aria-label")).toBe("2026/09/15");
    fireEvent.keyDown(grid, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith("2026-09-15");
  });

  it("방향키가 달을 넘으면 격자도 따라 넘어간다", () => {
    open({ value: "2026-09-30" });
    const grid = screen.getByRole("grid");
    fireEvent.keyDown(grid, { key: "ArrowRight" });
    expect(screen.getByText("2026년 10월")).toBeTruthy();
    expect(document.activeElement?.getAttribute("aria-label")).toBe("2026/10/01");
  });

  it("방향키는 min·max 를 넘지 않는다", () => {
    open({ min: "2026-09-14", value: "2026-09-15" });
    const grid = screen.getByRole("grid");
    fireEvent.keyDown(grid, { key: "ArrowUp" });
    expect(document.activeElement?.getAttribute("aria-label")).toBe("2026/09/14");
  });

  it("Esc 로 닫히고 포커스는 트리거로 돌아온다 (Popover 가 이미 하는 일)", () => {
    open();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("grid")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "종료일 달력 열기" }));
  });
});
