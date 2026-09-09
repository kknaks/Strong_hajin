import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Icon } from "./Icon";
import { ProgressBar } from "./ProgressBar";

afterEach(cleanup);

describe("Icon", () => {
  it("v2 07 의 규격으로 그린다 — viewBox 16 · round · fill none", () => {
    const { container } = render(<Icon name="close" />);
    const svg = container.querySelector("svg")!;
    expect(svg.getAttribute("viewBox")).toBe("0 0 16 16");
    expect(svg.getAttribute("fill")).toBe("none");
    expect(svg.getAttribute("stroke-linecap")).toBe("round");
    expect(svg.getAttribute("stroke-linejoin")).toBe("round");
  });

  it("16·20 은 stroke 1.5, 14 이하는 1.3", () => {
    const { container: a } = render(<Icon name="check" size={16} />);
    expect(a.querySelector("svg")?.getAttribute("stroke-width")).toBe("1.5");
    const { container: b } = render(<Icon name="check" size={20} />);
    expect(b.querySelector("svg")?.getAttribute("stroke-width")).toBe("1.5");
    const { container: c } = render(<Icon name="check" size={14} />);
    expect(c.querySelector("svg")?.getAttribute("stroke-width")).toBe("1.3");
    const { container: d } = render(<Icon name="chevron-down" size={12} />);
    expect(d.querySelector("svg")?.getAttribute("stroke-width")).toBe("1.3");
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
    render(<ProgressBar done={2} total={5} />);
    const bar = screen.getByRole("progressbar");
    expect(bar.getAttribute("aria-valuenow")).toBe("2");
    expect(bar.getAttribute("aria-valuemax")).toBe("5");
    expect((bar.firstElementChild as HTMLElement).style.width).toBe("40%");
  });

  it("아무것도 없을 때 0으로 나눈 값을 그리지 않는다", () => {
    render(<ProgressBar done={0} total={0} />);
    expect((screen.getByRole("progressbar").firstElementChild as HTMLElement).style.width).toBe("0%");
  });

  it("세는 자리가 따로 없을 때만 머리줄을 붙인다", () => {
    const { rerender } = render(<ProgressBar done={1} total={4} />);
    expect(screen.queryByText("1 / 4")).toBeNull();
    rerender(<ProgressBar done={1} label="할일" total={4} />);
    expect(screen.getByText("1 / 4")).toBeTruthy();
  });
});
