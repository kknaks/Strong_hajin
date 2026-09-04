import { describe, expect, it } from "vitest";

import { formatDate, formatDateTime, formatDuration, formatLongDate, formatMonth } from "./labels";

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

describe("shared date formatters cover every read-only rendering", () => {
  it("renders a timestamp as YYYY/MM/DD HH:MM in Seoul instead of a raw locale string", () => {
    // 2026-09-04T01:30:00Z is 10:30 in Seoul on the same day.
    expect(formatDateTime("2026-09-04T01:30:00Z")).toBe("2026/09/04 10:30");
    // Late UTC rolls into the next Seoul day; the date part must follow Seoul, not UTC.
    expect(formatDateTime("2026-09-04T20:00:00Z")).toBe("2026/09/05 05:00");
    expect(formatDateTime(null)).toBe("—");
    expect(formatDateTime("not-a-timestamp")).toBe("not-a-timestamp");
    // Never the browser locale grammar that this replaced.
    expect(formatDateTime("2026-09-04T01:30:00Z")).not.toMatch(/[년월일]|오전|오후/);
  });

  it("renders a month header as YYYY/MM with zero padding", () => {
    expect(formatMonth(2026, 9)).toBe("2026/09");
    expect(formatMonth(2026, 12)).toBe("2026/12");
    expect(formatMonth(2026, 9)).not.toMatch(/[년월]/);
  });
});
