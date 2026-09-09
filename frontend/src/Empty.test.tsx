import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Empty } from "./Empty";

afterEach(cleanup);

describe("Empty", () => {
  it("제목과 설명을 그린다", () => {
    render(<Empty description="오늘 할 일을 등록하면 여기에 쌓입니다." title="등록된 업무가 없습니다" />);
    expect(screen.getByText("등록된 업무가 없습니다")).toBeTruthy();
    expect(screen.getByText("오늘 할 일을 등록하면 여기에 쌓입니다.")).toBeTruthy();
  });

  it("기본 variant 는 행동을 스스로 만들지 않는다 — 화면마다 다르기 때문", () => {
    render(<Empty onAction={vi.fn()} title="비었습니다" />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("필터 때문에 비었으면 초기화를 함께 둔다 — '비어 있음'과 '찾지 못함'은 다르다", () => {
    const reset = vi.fn();
    render(<Empty onAction={reset} title="조건에 맞는 업무가 없습니다" variant="filter" />);
    fireEvent.click(screen.getByRole("button", { name: "필터 초기화" }));
    expect(reset).toHaveBeenCalledOnce();
  });

  it("불러오지 못한 것이면 다시 시도를 두고, 실패를 알린다", () => {
    const retry = vi.fn();
    render(<Empty onAction={retry} title="불러오지 못했습니다" variant="error" />);
    expect(screen.getByRole("alert")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("행동 이름은 부를 때 덮어쓸 수 있다", () => {
    render(<Empty actionLabel="첫 업무 만들기" onAction={vi.fn()} title="등록된 업무가 없습니다" />);
    expect(screen.getByRole("button", { name: "첫 업무 만들기" })).toBeTruthy();
  });

  it("아이콘 자리는 두되 비워 둔다 — 규격이 생기면 여기에 들어온다", () => {
    const { container } = render(<Empty title="비었습니다" />);
    const slot = container.querySelector(".empty-icon");
    expect(slot).toBeTruthy();
    expect(slot?.textContent).toBe("");
  });
});
