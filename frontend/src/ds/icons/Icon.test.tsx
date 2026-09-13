import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Icon } from "./Icon";
import { ProgressBar } from "../ProgressBar";

afterEach(cleanup);

describe("Icon", () => {
  it("규격은 그대로다 — round · fill none · currentColor", () => {
    const { container } = render(<Icon name="close" />);
    const svg = container.querySelector("svg")!;
    expect(svg.getAttribute("fill")).toBe("none");
    expect(svg.getAttribute("stroke-linecap")).toBe("round");
    expect(svg.getAttribute("stroke-linejoin")).toBe("round");
  });

  /* 바퀴 4: 세트를 한 벌로 합쳤다. 대부분은 새 DS 의 24 그리드이고, DS 에 대응이 없어
     구 path 를 그대로 둔 여덟만 16 그리드로 남는다 (G-03 — 다시 그리는 것은 발명이다). */
  it("새 DS 글리프는 24 그리드로 선다", () => {
    const { container } = render(<Icon name="close" />);
    expect(container.querySelector("svg")?.getAttribute("viewBox")).toBe("0 0 24 24");
  });

  it("DS 에 없어 구 path 를 그대로 둔 것은 16 그리드로 남는다", () => {
    for (const name of ["paperclip", "arrow-up", "arrow-down", "sparkle", "folder", "circle", "ban", "pending"] as const) {
      const { container } = render(<Icon name={name} />);
      expect(container.querySelector("svg")?.getAttribute("viewBox"), name).toBe("0 0 16 16");
    }
  });

  it("16·20 은 stroke 1.5, 14 이하는 1.3 — 새 DS 의 24 그리드 기준값", () => {
    const { container: a } = render(<Icon name="check" size={16} />);
    expect(a.querySelector("svg")?.getAttribute("stroke-width")).toBe("1.5");
    const { container: b } = render(<Icon name="check" size={20} />);
    expect(b.querySelector("svg")?.getAttribute("stroke-width")).toBe("1.5");
    const { container: c } = render(<Icon name="check" size={14} />);
    expect(c.querySelector("svg")?.getAttribute("stroke-width")).toBe("1.3");
    const { container: d } = render(<Icon name="chevron-down" size={12} />);
    expect(d.querySelector("svg")?.getAttribute("stroke-width")).toBe("1.3");
  });

  /* 바퀴 4b: v2 07 이 정한 것은 «화면에 보이는 굵기» 이지 속성값이 아니다.
     viewBox 가 16 인 글리프에 24 그리드 기준값을 그대로 쓰면 같은 자리에서 50% 굵게 보인다.
     그래서 16 그리드 쪽만 16/24 로 줄인다 — 두 그리드가 화면에서 같은 두께로 선다. */
  it("16 그리드 글리프는 화면 두께가 24 그리드와 같아지도록 줄여 그린다", () => {
    const px = (svg: Element | null | undefined, grid: number) =>
      Number(svg?.getAttribute("stroke-width")) * (Number(svg?.getAttribute("width")) / grid);

    for (const size of [12, 14, 16, 20] as const) {
      const { container: ds } = render(<Icon name="check" size={size} />);
      const { container: legacy } = render(<Icon name="paperclip" size={size} />);
      const a = px(ds.querySelector("svg"), 24);
      const b = px(legacy.querySelector("svg"), 16);
      // 화면 두께가 같다 (반올림 오차만 허용)
      expect(Math.abs(a - b), `size ${size}: DS ${a} vs 구 ${b}`).toBeLessThan(0.01);
    }

    // 24 그리드 쪽 숫자는 건드리지 않았다
    const { container: keep } = render(<Icon name="check" size={16} />);
    expect(keep.querySelector("svg")?.getAttribute("stroke-width")).toBe("1.5");
  });

  it("clock 은 선으로만 그린 글리프다 — 채운 아이콘을 만들지 않는다", () => {
    const { container } = render(<Icon name="clock" size={16} />);
    const svg = container.querySelector("svg")!;
    expect(svg.getAttribute("fill")).toBe("none");
    expect(svg.querySelectorAll("[fill]").length).toBe(0);
    expect(svg.querySelector("circle")).toBeTruthy();
  });

  it("색은 글자를 따라간다 — 아이콘이 자기 색을 갖지 않는다", () => {
    const { container } = render(<Icon name="search" />);
    expect(container.querySelector("svg")?.getAttribute("stroke")).toBe("currentColor");
  });

  it("이름이 없으면 장식이라 보조기술에서 감춘다", () => {
    const { container } = render(<Icon name="close" />);
    expect(container.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
  });

  it("이름을 주면 그림으로 읽힌다", () => {
    render(<Icon name="close" title="닫기" />);
    expect(screen.getByRole("img", { name: "닫기" })).toBeTruthy();
  });
});

describe("ProgressBar", () => {
  it("진행률을 폭과 값으로 함께 말한다", () => {
    render(<ProgressBar ariaLabel="진행률" done={2} total={5} />);
    const bar = screen.getByRole("progressbar");
    expect(bar.getAttribute("aria-valuenow")).toBe("2");
    expect(bar.getAttribute("aria-valuemax")).toBe("5");
    expect((bar.firstElementChild as HTMLElement).style.width).toBe("40%");
  });

  it("아무것도 없을 때 0으로 나눈 값을 그리지 않는다", () => {
    render(<ProgressBar ariaLabel="진행률" done={0} total={0} />);
    expect((screen.getByRole("progressbar").firstElementChild as HTMLElement).style.width).toBe("0%");
  });

  it("세는 자리가 따로 없을 때만 머리줄을 붙인다", () => {
    const { rerender } = render(<ProgressBar ariaLabel="진행률" done={1} total={4} />);
    expect(screen.queryByText("1 / 4")).toBeNull();
    rerender(<ProgressBar ariaLabel="진행률" done={1} label="할일" total={4} />);
    expect(screen.getByText("1 / 4")).toBeTruthy();
  });
});
