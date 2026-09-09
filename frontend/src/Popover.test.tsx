import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Popover } from "./Popover";

const restorers: Array<() => void> = [];

afterEach(() => {
  cleanup();
  while (restorers.length) restorers.pop()?.();
});

/**
 * jsdom 은 배치를 하지 않아 모든 높이가 0 이다. 팝오버가 재는 세 가지(뷰포트 높이 · 트리거 자리 ·
 * 내용 높이)만 대신 말해 주면, 남은 자리를 어떻게 계산하는지는 그대로 확인할 수 있다.
 */
function stubLayout({ viewportH, triggerTop, triggerBottom, contentH }: { viewportH: number; triggerTop: number; triggerBottom: number; contentH: number }) {
  const rect = Element.prototype.getBoundingClientRect;
  const scrollHeight = Object.getOwnPropertyDescriptor(Element.prototype, "scrollHeight");
  const innerHeight = Object.getOwnPropertyDescriptor(window, "innerHeight");
  Object.defineProperty(window, "innerHeight", { configurable: true, value: viewportH, writable: true });
  Element.prototype.getBoundingClientRect = function bounds(this: Element) {
    if (this.classList.contains("popover-root")) return { top: triggerTop, bottom: triggerBottom } as DOMRect;
    return rect.call(this);
  };
  Object.defineProperty(Element.prototype, "scrollHeight", {
    configurable: true,
    get(this: Element) {
      return this.classList.contains("popover") ? contentH : 0;
    },
  });
  restorers.push(() => {
    Element.prototype.getBoundingClientRect = rect;
    if (scrollHeight) Object.defineProperty(Element.prototype, "scrollHeight", scrollHeight);
    else Reflect.deleteProperty(Element.prototype, "scrollHeight");
    if (innerHeight) Object.defineProperty(window, "innerHeight", innerHeight);
  });
}

/** 열린 패널. */
const panel = () => screen.getByRole("group", { name: "상태 필터" });
const maxHeightOf = () => Number.parseInt(panel().style.maxHeight, 10);

function renderPopover() {
  return render(
    <div>
      <button type="button">바깥 단추</button>
      <Popover label="상태 필터" trigger={({ props }) => <button {...props}>여는 단추</button>}>
        {(close) => (
          <button onClick={close} type="button">
            고르기
          </button>
        )}
      </Popover>
    </div>,
  );
}

describe("Popover", () => {
  it("닫혀 있을 때는 내용이 없다", () => {
    renderPopover();
    expect(screen.queryByRole("group", { name: "상태 필터" })).toBeNull();
    expect(screen.getByRole("button", { name: "여는 단추" }).getAttribute("aria-expanded")).toBe("false");
  });

  it("트리거를 누르면 열리고 aria 가 따라간다", () => {
    renderPopover();
    fireEvent.click(screen.getByRole("button", { name: "여는 단추" }));
    expect(screen.getByRole("group", { name: "상태 필터" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "여는 단추" }).getAttribute("aria-expanded")).toBe("true");
  });

  it("바깥을 누르면 닫힌다 — 스크림이 없으니 닫는 길을 우리가 놓는다 (v2 14)", () => {
    renderPopover();
    fireEvent.click(screen.getByRole("button", { name: "여는 단추" }));
    fireEvent.mouseDown(screen.getByRole("button", { name: "바깥 단추" }));
    expect(screen.queryByRole("group", { name: "상태 필터" })).toBeNull();
  });

  it("안쪽을 눌러도 닫히지 않는다", () => {
    renderPopover();
    fireEvent.click(screen.getByRole("button", { name: "여는 단추" }));
    fireEvent.mouseDown(screen.getByRole("button", { name: "고르기" }));
    expect(screen.getByRole("group", { name: "상태 필터" })).toBeTruthy();
  });

  it("Esc 로 닫고 포커스를 트리거로 돌려준다", () => {
    renderPopover();
    const trigger = screen.getByRole("button", { name: "여는 단추" });
    fireEvent.click(trigger);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("group", { name: "상태 필터" })).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("고른 뒤에는 호출부가 close 로 닫을 수 있다", () => {
    renderPopover();
    fireEvent.click(screen.getByRole("button", { name: "여는 단추" }));
    fireEvent.click(screen.getByRole("button", { name: "고르기" }));
    expect(screen.queryByRole("group", { name: "상태 필터" })).toBeNull();
  });
});

/**
 * v2 `14` 는 팝오버의 **높이를 정하지 않는다**. 그래서 규칙은 "뷰포트 안에서 잘리지 않는다"로 둔다 —
 * 패널은 트리거 옆에 실제로 남은 자리만큼만 자라고, 내용이 더 길면 패널 안에서 스크롤한다.
 */
describe("Popover — 내용이 길 때", () => {
  it("내용이 남은 자리를 넘으면 그 자리만큼 max-height 가 걸려 스크롤 컨테이너가 된다", () => {
    // 트리거는 위쪽(0~30), 뷰포트 600 → 아래로 600-30-8-8 = 554 가 남는다. 내용은 1200.
    stubLayout({ viewportH: 600, triggerTop: 0, triggerBottom: 30, contentH: 1200 });
    renderPopover();
    fireEvent.click(screen.getByRole("button", { name: "여는 단추" }));
    // 남은 자리(554)보다 상한(420)이 작으니 상한을 쓴다. 어느 쪽이든 내용(1200)보다 작아야 스크롤이 생긴다.
    expect(maxHeightOf()).toBe(420);
    expect(maxHeightOf()).toBeLessThan(panel().scrollHeight);
  });

  it("아래 자리가 모자라면 그 자리만큼만 연다 — 화면 밖으로 넘기지 않는다", () => {
    // 트리거가 아래쪽(500~530), 뷰포트 600 → 아래는 62 뿐, 위는 484. 위로 열고, 484 는 상한 420 으로 깎인다.
    stubLayout({ viewportH: 600, triggerTop: 500, triggerBottom: 530, contentH: 1200 });
    renderPopover();
    fireEvent.click(screen.getByRole("button", { name: "여는 단추" }));
    expect(panel().className).toContain("above");
    expect(maxHeightOf()).toBe(420);
  });

  it("위아래가 다 좁으면 넓은 쪽의 남은 자리를 그대로 쓴다", () => {
    // 뷰포트 400, 트리거 100~130 → 아래 254 · 위 84. 아래로 열고 254.
    stubLayout({ viewportH: 400, triggerTop: 100, triggerBottom: 130, contentH: 1200 });
    renderPopover();
    fireEvent.click(screen.getByRole("button", { name: "여는 단추" }));
    expect(panel().className).not.toContain("above");
    expect(maxHeightOf()).toBe(254);
  });

  it("자리가 아무리 좁아도 최소 높이는 지킨다 — 항목 서너 개는 보여야 고를 수 있다", () => {
    // 뷰포트 200, 트리거 90~120 → 아래 64 · 위 74. 둘 다 최소(160)보다 작다.
    stubLayout({ viewportH: 200, triggerTop: 90, triggerBottom: 120, contentH: 1200 });
    renderPopover();
    fireEvent.click(screen.getByRole("button", { name: "여는 단추" }));
    expect(maxHeightOf()).toBe(160);
  });

  it("창 크기가 바뀌면 남은 자리를 다시 잰다", () => {
    stubLayout({ viewportH: 900, triggerTop: 0, triggerBottom: 30, contentH: 1200 });
    renderPopover();
    fireEvent.click(screen.getByRole("button", { name: "여는 단추" }));
    expect(maxHeightOf()).toBe(420);
    act(() => {
      Object.defineProperty(window, "innerHeight", { configurable: true, value: 300, writable: true });
      window.dispatchEvent(new Event("resize"));
    });
    // 300 - 30 - 8 - 8 = 254
    expect(maxHeightOf()).toBe(254);
  });

  it("패널 자체가 포커스를 받을 수 있다 — 누를 것이 없는 내용도 키보드로 스크롤한다", () => {
    stubLayout({ viewportH: 600, triggerTop: 0, triggerBottom: 30, contentH: 1200 });
    renderPopover();
    fireEvent.click(screen.getByRole("button", { name: "여는 단추" }));
    // -1 이라 탭 순서는 건드리지 않는다.
    expect(panel().getAttribute("tabindex")).toBe("-1");
  });
});
