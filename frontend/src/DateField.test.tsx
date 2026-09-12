import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DateField } from "./DateField";

describe("date field", () => {
  afterEach(cleanup);

  it("칸 하나다 — 값은 ISO 표기이고 달력 아이콘이 그 칸 안에 선다 (DS-17)", () => {
    render(<DateField id="due" label="기한" onChange={vi.fn()} value="2026-09-30" />);

    const field = screen.getByRole("button", { name: "기한 달력 열기" });
    // 값은 하이픈 표기이고, 아이콘은 칸 «안» 오른쪽이다 — 바깥에 따로 선 단추가 아니다
    expect(field.textContent).toBe("2026-09-30");
    expect(field.querySelector("svg")).toBeTruthy();
    expect(screen.getAllByRole("button")).toHaveLength(1);
    // 브라우저 기본 달력을 쓰지 않는다 — 같은 칸이 사람마다 다른 글자 순서로 보이던 이유였다
    expect(document.querySelector('input[type="date"]')).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(document.body.textContent).not.toMatch(/mm\/dd\/yyyy/i);
  });

  it("값이 없으면 자리표시가 선다", () => {
    render(<DateField id="due" label="기한" onChange={vi.fn()} value="" />);
    expect(screen.getByRole("button", { name: "기한 달력 열기" }).textContent).toBe("YYYY-MM-DD");
  });

  // AX 카드는 점 구분자와 「▾」로 낸다 — 보이는 글자만 바뀌고 오가는 값은 그대로 ISO 다 (main #3)
  it("can use the dotted task-card presentation without changing the ISO boundary", () => {
    const onChange = vi.fn();
    render(
      <DateField
        displaySeparator="."
        id="task-due"
        label="기한"
        onChange={onChange}
        pickerIcon="chevron-down"
        required
        value="2026-09-30"
      />,
    );
    const field = screen.getByRole("button", { name: "기한 달력 열기" });
    expect(field.textContent).toBe("2026.09.30");
    // 필수 표시는 레이블에 선다
    expect(screen.getByText("기한").textContent).toContain("*");

    fireEvent.click(field);
    // 9월 격자는 8/30~10/10 이라 10/05 가 그 안에 선다
    fireEvent.click(screen.getByRole("group", { name: "기한" }).querySelector('[data-date="2026-10-05"]') as HTMLElement);
    // 화면 글자가 점이어도 경계를 넘는 값은 ISO 하나다
    expect(onChange).toHaveBeenCalledWith("2026-10-05");
  });

  it("값이 없으면 그 화면의 구분자로 자리표시를 낸다", () => {
    render(<DateField displaySeparator="." id="task-due" label="기한" onChange={vi.fn()} value="" />);
    expect(screen.getByRole("button", { name: "기한 달력 열기" }).textContent).toBe("YYYY.MM.DD");
  });

  it("칸을 누르면 우리 달력이 열리고 고른 날이 ISO 로 넘어간다", () => {
    const onChange = vi.fn();
    render(<DateField id="when" label="일시" onChange={onChange} value="2026-09-11" />);

    fireEvent.click(screen.getByRole("button", { name: "일시 달력 열기" }));
    const panel = screen.getByRole("group", { name: "일시" });
    expect(panel).toBeTruthy();

    fireEvent.click(panel.querySelector('[data-date="2026-09-15"]') as HTMLElement);
    expect(onChange).toHaveBeenCalledWith("2026-09-15");
    // 고르면 닫힌다 — 고르기는 팝오버 한 번이다
    expect(screen.queryByRole("group", { name: "일시" })).toBeNull();
  });

  it("비활성이면 달력이 열리지 않는다", () => {
    render(<DateField disabled id="due" label="기한" onChange={vi.fn()} value="2026-09-30" />);
    const field = screen.getByRole("button", { name: "기한 달력 열기" });
    expect((field as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(field);
    expect(screen.queryByRole("group", { name: "기한" })).toBeNull();
  });
});
