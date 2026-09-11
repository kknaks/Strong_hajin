import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { MinWidthNotice } from "./MinWidthNotice";
import { minWidthNotice } from "./labels";

afterEach(cleanup);

describe("MinWidthNotice", () => {
  it("keeps the legacy design-system export compatible", () => {
    render(<MinWidthNotice />);
    const notice = screen.getByRole("alert");
    expect(notice.textContent).toContain(minWidthNotice.title);
    expect(notice.textContent).toContain(minWidthNotice.description);
  });
});
