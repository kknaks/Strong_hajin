import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { emptyActionLabel } from "../lib/labels";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Empty } from "./Empty";

afterEach(cleanup);

describe("Empty", () => {
  it("제목과 설명을 그린다", () => {
    render(<Empty description="오늘 할 일을 등록하면 여기에 쌓입니다." title="등록된 업무가 없습니다" />);
    expect(screen.getByText("등록된 업무가 없습니다")).toBeTruthy();
    expect(screen.getByText("오늘 할 일을 등록하면 여기에 쌓입니다.")).toBeTruthy();
  });

  /* 바퀴 11: 예전에는 「행동만 넘기면 기본 variant 는 이름을 안 지어낸다」를 여기서 지켰다.
     이제 이름 없이 행동만 넘기는 것은 **타입이 막는다**(행동과 이름이 한 쌍이다). 검사가 지키던 뜻은
     그대로 두되 — 줄 것이 없으면 단추도 없다 — 컴파일이 막는 쪽을 골라 행동 자체를 안 넘긴다. */
  it("줄 행동이 없으면 단추도 없다 — 부품이 행동을 지어내지 않는다", () => {
    render(<Empty title="비었습니다" />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("필터 때문에 비었으면 초기화를 함께 둔다 — '비어 있음'과 '찾지 못함'은 다르다", () => {
    const reset = vi.fn();
    render(<Empty actionLabel={emptyActionLabel.filter} onAction={reset} title="조건에 맞는 업무가 없습니다" variant="filter" />);
    fireEvent.click(screen.getByRole("button", { name: "필터 초기화" }));
    expect(reset).toHaveBeenCalledOnce();
  });

  it("불러오지 못한 것이면 다시 시도를 두고, 실패를 알린다", () => {
    const retry = vi.fn();
    render(<Empty actionLabel={emptyActionLabel.error} onAction={retry} title="불러오지 못했습니다" variant="error" />);
    expect(screen.getByRole("alert")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("행동 이름은 부를 때 덮어쓸 수 있다", () => {
    render(<Empty actionLabel="첫 업무 만들기" onAction={vi.fn()} title="등록된 업무가 없습니다" />);
    expect(screen.getByRole("button", { name: "첫 업무 만들기" })).toBeTruthy();
  });

  /* G-CAL-02 (WORK-004): `icon` 은 **더한 것**이다 — 안 넘기면 예전 그대로 `variant` 가 고른다.
     이 둘이 같이 있어야 「소비처 19곳을 고치지 않는다」가 검사로 지켜진다. */
  it("아이콘을 안 주면 variant 가 고르던 글리프 그대로다", () => {
    const { container } = render(<Empty title="비었습니다" />);
    const filtered = render(<Empty title="조건에 맞는 것이 없습니다" variant="filter" />);
    expect(container.querySelector(".scax-empty__icon svg")).toBeTruthy();
    expect(filtered.container.querySelector(".scax-empty__icon svg")?.innerHTML).not.toBe(
      container.querySelector(".scax-empty__icon svg")?.innerHTML,
    );
  });

  it("아이콘을 주면 그것을 그린다 — 「못 불러왔다」 자리는 그래도 경고 글리프를 지킨다", () => {
    const chosen = render(<Empty icon="calendar" title="해당 일정이 없습니다" />);
    const fallback = render(<Empty title="해당 일정이 없습니다" />);
    expect(chosen.container.querySelector(".scax-empty__icon svg")?.innerHTML).not.toBe(
      fallback.container.querySelector(".scax-empty__icon svg")?.innerHTML,
    );
    const failed = render(<Empty icon="calendar" title="불러오지 못했습니다" variant="error" />);
    expect(failed.container.querySelector(".scax-status-note__icon svg")?.innerHTML).toBe(
      render(<Empty title="불러오지 못했습니다" variant="error" />).container.querySelector(".scax-status-note__icon svg")?.innerHTML,
    );
  });

  it("아이콘 자리는 두되 읽을 것을 두지 않는다 — 글리프는 보조기술에서 감춘다", () => {
    const { container } = render(<Empty title="비었습니다" />);
    const slot = container.querySelector(".scax-empty__icon");
    expect(slot).toBeTruthy();
    expect(slot?.textContent).toBe("");
  });
});
