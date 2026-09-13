import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { MinWidthNotice } from "./MinWidthNotice";
import { minWidthNotice } from "../lib/labels";

afterEach(cleanup);

describe("MinWidthNotice", () => {
  it("keeps the legacy design-system export compatible", () => {
    render(<MinWidthNotice description={minWidthNotice.description} title={minWidthNotice.title} />);
    const notice = screen.getByRole("alert");
    expect(notice.textContent).toContain(minWidthNotice.title);
    expect(notice.textContent).toContain(minWidthNotice.description);
  });
});
