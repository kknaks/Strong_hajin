import { describe, expect, it } from "vitest";

import { formatDate, formatDuration, formatLongDate } from "./labels";

describe("formatDuration", () => {
  it("shows milliseconds below one second and whole seconds from one second on", () => {
    expect(formatDuration(0)).toBe("0ms");
    expect(formatDuration(21)).toBe("21ms");
    expect(formatDuration(999)).toBe("999ms");
    expect(formatDuration(1000)).toBe("1s");
    expect(formatDuration(1999)).toBe("1s"); // floored: never claims more than was observed
    expect(formatDuration(12300)).toBe("12s");
    expect(formatDuration(12614)).toBe("12s");
  });

  it("returns null when nothing was observed and never formats a negative value", () => {
    expect(formatDuration(null)).toBeNull();
    expect(formatDuration(undefined)).toBeNull();
    expect(formatDuration(-5)).toBe("0ms");
  });
});

describe("read-only dates", () => {
  it("renders YYYY/MM/DD and keeps the ISO input untouched", () => {
    const iso = "2026-09-04";
    expect(formatDate(iso)).toBe("2026/09/04");
    expect(iso).toBe("2026-09-04");
    expect(formatDate(null)).toBe("—");
    expect(formatDate("not-a-date")).toBe("not-a-date");
  });

  it("adds the weekday only as a secondary cue on the long form", () => {
    expect(formatLongDate("2026-09-04")).toBe("2026/09/04 금요일");
  });
});
