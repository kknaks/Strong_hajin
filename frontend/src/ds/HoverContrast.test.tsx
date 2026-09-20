import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Modal } from "./Modal";

/**
 * 3차 발주 1 — **hover 에서 글자가 사라지지 않는다.**
 *
 * 증상은 하나지만 원인은 CSS 특정도다. 켜진 상태(`--on`)를 클래스 하나(0,1,0)로 칠해 두고 hover 를
 * `:hover`(0,2,0)로 걸면, **hover 가 켜진 상태를 이긴다** — 면만 밝아지고 잉크는 켜졌을 때의 값
 * (검은 면 위 흰 글자)으로 남아 대비가 0 이 된다. 그래서 검사는 두 가지를 본다.
 *
 * 1. 꺼진 것에만 거는 hover 는 `:not(--on)` 으로 좁혀져 있다.
 * 2. 켜진 것에는 «면과 잉크를 함께» 주는 자기 hover 규칙이 따로 있다.
 *
 * jsdom 은 cascade 를 계산하지 않으므로 «그려진 색» 이 아니라 **규칙이 서 있는지** 를 잰다 — 이 리포가
 * 포커스 링 회귀를 막는 방식과 같다 (`MeetingList.test.tsx`).
 */

async function flatCss(path: string): Promise<string> {
  // @ts-expect-error — 이 리포는 @types/node 를 두지 않는다.
  const { readFileSync } = await import("node:fs");
  return (readFileSync(path, "utf8") as string).replace(/\s+/g, "");
}

describe("고른 상태의 hover 대비 (3차 발주 1)", () => {
  it("업무 필터 칩 — 켜진 칩은 hover 에서 검은 면과 흰 잉크를 함께 지킨다", async () => {
    const css = await flatCss("src/styles/components.css");
    // 꺼진 칩에만 밝은 면을 준다: 이 `:not` 이 빠지면 켜진 칩의 흰 글자가 흰 면 위에 남는다.
    expect(css).toContain(".scax-chip:not(.scax-chip--on):hover{background:var(--scax-color-fill-weak)}");
    expect(css).not.toContain(".scax-chip:hover{background:var(--scax-color-fill-weak)}");
    // 켜진 칩은 면과 잉크를 «함께» 옮긴다 — 잉크만 남는 상태가 생기지 않는다.
    expect(css).toContain(
      ".scax-chip--on:hover{background:var(--scax-color-surface-inverse);border-color:var(--scax-color-surface-inverse);color:var(--scax-color-ink-inverse)}",
    );
  });

  it("세그먼트 — 고른 칸은 hover 에서 자기 면과 잉크를 잃지 않는다", async () => {
    const css = await flatCss("src/styles/components.css");
    expect(css).toContain(".scax-segmented__item--on:hover{background:var(--scax-color-surface);color:var(--scax-color-ink)}");
  });

  it("탭 — 켜진 탭의 잉크가 hover 에서 도로 흐려지지 않는다", async () => {
    const css = await flatCss("src/styles/components.css");
    expect(css).toContain(".scax-tabs__item:not(.scax-tabs__item--on):hover{color:var(--scax-color-ink-neutral)}");
    expect(css).toContain(".scax-tabs__item--on:hover{color:var(--scax-color-ink)}");
    expect(css).not.toContain(".scax-tabs__item:hover{color:var(--scax-color-ink-neutral)}");
  });

  it("액션 단추 — hover 규칙이 면을 바꿀 때 잉크를 함께 못 박는다", async () => {
    const css = await flatCss("src/styles/components.css");
    for (const rule of [
      ".scax-button--solid-primary:hover{background:var(--scax-color-accent-strong);color:#fff}",
      ".scax-button--solid-danger:hover{background:var(--scax-color-danger);color:#fff}",
      ".scax-button--outlined-primary:hover{background:var(--scax-color-accent-05);color:var(--scax-color-accent)}",
      ".scax-button--outlined-neutral:hover{background:var(--scax-color-fill-weak);color:var(--scax-color-ink-neutral)}",
      ".scax-button--text-neutral:hover{background:var(--scax-color-fill-weak);color:var(--scax-color-ink-neutral)}",
    ]) {
      expect(css).toContain(rule);
    }
  });

  it("달력의 고른 날 — 검은 면 위 흰 글자가 hover 에서 흰 면 위에 남지 않는다", async () => {
    const css = await flatCss("src/styles/components.css");
    // 이 hover 는 (0,3,0) 이라 고른 날 규칙 (0,2,0) 을 이긴다 — 고른 날을 빼지 않으면 잉크만 흰 채 남는다.
    // (이 구획은 압축 표기가 아니라 «닫기 전 세미콜론» 을 쓴다 — 파일이 쓰는 표기 그대로 잰다.)
    expect(css).toContain(
      '.date-picker-cell:hover:not(:disabled):not([aria-selected="true"]){background:var(--scax-color-surface-alt);}',
    );
    expect(css).not.toContain(".date-picker-cell:hover:not(:disabled){background:var(--scax-color-surface-alt);}");
    // 고른 날에도 hover 는 있다 — 면과 잉크를 «함께» 옮긴 한 단이다.
    expect(css).toContain(
      '.date-picker-cell[aria-selected="true"]:hover:not(:disabled){color:var(--scax-color-ink-inverse);background:var(--scax-color-surface-inverse);}',
    );
  });

  it("캘린더 날짜 칸 — 고른 날의 톤이 hover 에서 중립 면으로 덮이지 않는다", async () => {
    const css = await flatCss("src/styles/components.css");
    expect(css).toContain(".scax-day-cell:not(.scax-day-cell--selected):hover{background:var(--scax-color-fill-weak)}");
    expect(css).not.toContain(".scax-day-cell:hover{background:var(--scax-color-fill-weak)}");
    expect(css).toContain(".scax-day-cell--selected:hover{background:var(--scax-color-accent-20);color:var(--scax-color-accent)}");
  });

  it("사람 칩 — 고른 사람은 hover 에서 accent 를 면·잉크 함께 지킨다", async () => {
    const css = await flatCss("src/styles/components.css");
    expect(css).toContain(".scax-person-chip:not(.scax-person-chip--on):hover{background:var(--scax-color-fill-weak)}");
    expect(css).not.toContain(".scax-person-chip:hover{background:var(--scax-color-fill-weak)}");
    expect(css).toContain(
      ".scax-person-chip--on:hover{background:var(--scax-color-accent-08);border-color:var(--scax-color-accent-20);color:var(--scax-color-accent)}",
    );
  });

  it("상태 알약 — 톤을 칠해 둔 트리거가 hover 에서 남의 색을 뒤집어쓰지 않는다", async () => {
    const css = await flatCss("src/styles/components.css");
    expect(css).not.toContain(".scax-select__trigger:hover{background:var(--scax-color-accent-20)}");
    expect(css).toContain(".scax-select__trigger--danger:hover{background:var(--scax-color-danger-soft);color:var(--scax-color-danger)}");
    expect(css).toContain(".scax-select__trigger--positive:hover{background:rgba(0,163,88,.16);color:var(--scax-color-positive)}");
    expect(css).toContain(".scax-select__trigger--neutral:hover{background:var(--scax-color-fill);color:var(--scax-color-ink-alt)}");
  });
});

describe("Modal 의 「뒤로」 (3차 발주 3)", () => {
  afterEach(cleanup);

  it("안 넘기면 서지 않는다 — 지금까지 열리던 자리는 그대로다", () => {
    render(
      <Modal closeLabel="닫기" label="샘플" onClose={() => undefined} title="제목">
        본문
      </Modal>,
    );
    expect(screen.queryByRole("button", { name: "요청으로" })).toBeNull();
    expect(screen.getAllByRole("button").length).toBe(1);
  });

  it("넘기면 겹을 닫지 않고 부르는 쪽에 되돌리기만 알린다", () => {
    const onBack = vi.fn();
    const onClose = vi.fn();
    render(
      <Modal backLabel="요청으로" closeLabel="닫기" label="샘플" onBack={onBack} onClose={onClose} title="제목">
        본문
      </Modal>,
    );
    fireEvent.click(screen.getByRole("button", { name: "요청으로" }));
    expect(onBack).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();
  });
});
