import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Checkbox, FieldMessage } from "./FormControls";

afterEach(cleanup);

describe("Checkbox", () => {
  it("네이티브 체크박스로 남는다 — 키보드도 폼도 브라우저 것이다", () => {
    render(<Checkbox checked={false} onChange={vi.fn()}>산출물 고르기</Checkbox>);
    expect(screen.getByRole("checkbox")).toBeTruthy();
  });

  it("라벨을 누르면 켜진다", () => {
    const onChange = vi.fn();
    render(<Checkbox checked={false} onChange={onChange}>산출물 고르기</Checkbox>);
    fireEvent.click(screen.getByLabelText("산출물 고르기"));
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("켜진 것을 누르면 꺼진다", () => {
    const onChange = vi.fn();
    render(<Checkbox checked onChange={onChange}>산출물 고르기</Checkbox>);
    fireEvent.click(screen.getByRole("checkbox"));
    expect(onChange).toHaveBeenCalledWith(false);
  });

  it("비활성은 네이티브 disabled 로 내려간다 — 막는 일은 브라우저가 한다", () => {
    render(<Checkbox checked={false} disabled onChange={vi.fn()}>산출물 고르기</Checkbox>);
    expect((screen.getByRole("checkbox") as HTMLInputElement).disabled).toBe(true);
  });
});

describe("FieldMessage", () => {
  it("헬퍼만 있으면 헬퍼를 낸다", () => {
    render(<FieldMessage help="적어 둔 사유는 목록에 그대로 보입니다." />);
    expect(screen.getByText("적어 둔 사유는 목록에 그대로 보입니다.")).toBeTruthy();
  });

  it("에러가 뜨면 헬퍼를 대신한다 — 둘을 같이 쌓지 않는다 (v2 09)", () => {
    render(<FieldMessage error="막힘 사유를 적어 주세요." help="적어 둔 사유는 목록에 그대로 보입니다." />);
    expect(screen.getByText("막힘 사유를 적어 주세요.")).toBeTruthy();
    expect(screen.queryByText("적어 둔 사유는 목록에 그대로 보입니다.")).toBeNull();
  });

  it("에러는 알린다", () => {
    render(<FieldMessage error="막힘 사유를 적어 주세요." />);
    expect(screen.getByRole("alert").textContent).toBe("막힘 사유를 적어 주세요.");
  });

  it("줄 것이 없으면 아무것도 그리지 않는다", () => {
    const { container } = render(<FieldMessage />);
    expect(container.innerHTML).toBe("");
  });
});
