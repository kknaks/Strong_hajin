import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { MinWidthNotice } from "./MinWidthNotice";
import { minWidthNotice } from "./labels";

afterEach(cleanup);

describe("MinWidthNotice", () => {
  it("지원하지 않는 폭임을 알린다 — v2 15 는 1280 미만을 지원하지 않는다", () => {
    render(<MinWidthNotice />);
    const notice = screen.getByRole("alert");
    expect(notice.textContent).toContain(minWidthNotice.title);
    expect(notice.textContent).toContain(minWidthNotice.description);
  });

  it("보임 여부는 CSS 가 정하므로 항상 DOM 에 있다 — 폭을 재려고 리렌더하지 않는다", () => {
    const { container } = render(<MinWidthNotice />);
    expect(container.querySelector(".min-width-notice")).toBeTruthy();
  });
});
