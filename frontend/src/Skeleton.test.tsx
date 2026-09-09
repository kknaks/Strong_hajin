import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Skeleton } from "./Skeleton";

afterEach(cleanup);

describe("Skeleton", () => {
  it("리스트는 기본 5행으로 깐다 — v2 10 STATE", () => {
    const { container } = render(<Skeleton />);
    expect(container.querySelectorAll(".skeleton-bar")).toHaveLength(5);
  });

  it("올 내용의 개수를 알면 그 개수로 깐다", () => {
    const { container } = render(<Skeleton rows={2} />);
    expect(container.querySelectorAll(".skeleton-bar")).toHaveLength(2);
  });

  it("무엇을 기다리는지 스크린리더에 말한다 — 막대는 읽을 것이 없다", () => {
    render(<Skeleton label="회의를 불러오는 중" />);
    const status = screen.getByRole("status");
    expect(status.getAttribute("aria-busy")).toBe("true");
    expect(within(status).getByText("회의를 불러오는 중")).toBeTruthy();
  });

  it("막대 자체는 보조기술에서 감춘다", () => {
    const { container } = render(<Skeleton rows={1} />);
    expect(container.querySelector(".skeleton-bar")?.getAttribute("aria-hidden")).toBe("true");
  });
});
