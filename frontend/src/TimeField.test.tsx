import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TimeField, TimeRangeField, formatTime, parseTime, timeSlots } from "./TimeField";

afterEach(cleanup);

describe("시각 문자열", () => {
  it("24시간 HH:MM 으로만 쓴다 — 오전/오후가 낄 자리가 없다", () => {
    expect(formatTime(0)).toBe("00:00");
    expect(formatTime(13 * 60 + 30)).toBe("13:30");
    expect(formatTime(23 * 60 + 59)).toBe("23:59");
    // 자정을 넘기면 앞으로 돈다
    expect(formatTime(24 * 60 + 30)).toBe("00:30");
    expect(formatTime(-30)).toBe("23:30");
  });

  it("시각이 아닌 값은 걸러진다", () => {
    expect(parseTime("14:30")).toBe(870);
    expect(parseTime("25:70")).toBeNull();
    expect(parseTime("오후 2:30")).toBeNull();
    expect(parseTime("")).toBeNull();
  });

  it("30분 간격이면 하루가 48칸이고, min·max 로 잘린다", () => {
    expect(timeSlots(30).length).toBe(48);
    expect(timeSlots(30)[0]).toBe("00:00");
    expect(timeSlots(30, "09:00", "20:00")).toEqual(expect.arrayContaining(["09:00", "20:00"]));
    expect(timeSlots(30, "09:00", "20:00").length).toBe(23);
  });
});

describe("TimeField", () => {
  it("네이티브 input[type=time] 이 아니라 팝오버 목록이다", () => {
    render(<TimeField label="회의 시각" onChange={vi.fn()} value="14:30" />);
    expect(document.querySelector('input[type="time"]')).toBeNull();
    const trigger = screen.getByLabelText("회의 시각");
    expect(trigger.textContent).toContain("14:30");

    fireEvent.click(trigger);
    expect(screen.getByRole("listbox", { name: "회의 시각" })).toBeTruthy();
    expect(screen.getAllByRole("option").length).toBe(48);
  });

  it("min·max 안의 30분 칸만 뜨고, 고르면 HH:MM 이 그대로 넘어온다", () => {
    const onChange = vi.fn();
    render(<TimeField label="회의 시각" max="20:00" min="09:00" onChange={onChange} value="" />);
    fireEvent.click(screen.getByLabelText("회의 시각"));
    expect(screen.getAllByRole("option").length).toBe(23);

    fireEvent.click(screen.getByRole("option", { name: "10:30" }));
    expect(onChange).toHaveBeenCalledWith("10:30");
  });

  it("48칸이라 검색창이 저절로 붙는다", () => {
    render(<TimeField label="회의 시각" onChange={vi.fn()} value="" />);
    fireEvent.click(screen.getByLabelText("회의 시각"));
    const search = screen.getByLabelText("검색");
    expect(search.getAttribute("placeholder")).toBe("14:30");
    fireEvent.change(search, { target: { value: "14:" } });
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual(["14:00", "14:30"]);
  });

  it("간격에 걸리지 않는 값도 목록에 남는다 — 열자마자 사라지지 않게", () => {
    render(<TimeField label="회의 시각" onChange={vi.fn()} value="14:17" />);
    fireEvent.click(screen.getByLabelText("회의 시각"));
    expect(screen.getByRole("option", { name: "14:17" }).getAttribute("aria-selected")).toBe("true");
  });

  it("값이 없으면 placeholder 가 서고 비활성이면 열리지 않는다", () => {
    render(<TimeField disabled label="회의 시각" onChange={vi.fn()} value="" />);
    const trigger = screen.getByLabelText("회의 시각");
    expect(trigger.textContent).toContain("시각 선택");
    expect((trigger as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("TimeRangeField", () => {
  it("한 쌍이 나란히 서고 각각 제 이름으로 읽힌다", () => {
    render(<TimeRangeField end="15:00" label="회의 시간" onChange={vi.fn()} start="14:00" />);
    expect(screen.getByLabelText("회의 시간 시작 시각").textContent).toContain("14:00");
    expect(screen.getByLabelText("회의 시간 종료 시각").textContent).toContain("15:00");
  });

  it("시작을 옮기면 종료가 같은 간격만큼 따라간다", () => {
    const onChange = vi.fn();
    render(<TimeRangeField end="15:00" label="회의 시간" onChange={onChange} start="14:00" />);
    fireEvent.click(screen.getByLabelText("회의 시간 시작 시각"));
    fireEvent.click(screen.getByRole("option", { name: "16:30" }));
    expect(onChange).toHaveBeenCalledWith({ start: "16:30", end: "17:30" });
  });

  it("간격을 알 수 없으면 기본 60분으로 선다", () => {
    const onChange = vi.fn();
    render(<TimeRangeField end="" label="회의 시간" onChange={onChange} start="" />);
    fireEvent.click(screen.getByLabelText("회의 시간 시작 시각"));
    fireEvent.click(screen.getByRole("option", { name: "09:00" }));
    expect(onChange).toHaveBeenCalledWith({ start: "09:00", end: "10:00" });
  });

  it("defaultDuration 을 주면 그만큼 벌어진다", () => {
    const onChange = vi.fn();
    render(<TimeRangeField defaultDuration={30} end="" label="회의 시간" onChange={onChange} start="" />);
    fireEvent.click(screen.getByLabelText("회의 시간 시작 시각"));
    fireEvent.click(screen.getByRole("option", { name: "09:00" }));
    expect(onChange).toHaveBeenCalledWith({ start: "09:00", end: "09:30" });
  });

  it("종료를 옮기는 것은 시작을 건드리지 않는다", () => {
    const onChange = vi.fn();
    render(<TimeRangeField end="15:00" label="회의 시간" onChange={onChange} start="14:00" />);
    fireEvent.click(screen.getByLabelText("회의 시간 종료 시각"));
    fireEvent.click(screen.getByRole("option", { name: "16:00" }));
    expect(onChange).toHaveBeenCalledWith({ start: "14:00", end: "16:00" });
  });

  it("종료가 시작보다 빠르면 한 줄로 말하고 종료 쪽 보더만 갈아입는다", () => {
    render(<TimeRangeField end="13:00" label="회의 시간" onChange={vi.fn()} start="14:00" />);
    expect(screen.getByRole("alert").textContent).toBe("종료가 시작보다 빠릅니다");
    expect(screen.getByLabelText("회의 시간 종료 시각").getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByLabelText("회의 시간 시작 시각").getAttribute("aria-invalid")).toBeNull();
  });

  it("한쪽이 비어 있는 것은 잘못된 것이 아니다", () => {
    render(<TimeRangeField end="" label="회의 시간" onChange={vi.fn()} start="14:00" />);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
