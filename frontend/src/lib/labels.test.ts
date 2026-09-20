import { describe, expect, it } from "vitest";

import { formatDate, formatDateTime, formatDuration, formatLongDate, formatMonth, meetingElapsed, workRequestStateLabel, workRequestStateTone } from "./labels";

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

describe("meetingElapsed", () => {
  it("회의 시작에서 흐른 시간을 mm:ss 로, 한 시간을 넘으면 h:mm:ss 로 읽는다 (D50)", () => {
    expect(meetingElapsed(0)).toBe("00:00");
    expect(meetingElapsed(61_000)).toBe("01:01");
    expect(meetingElapsed(120_000)).toBe("02:00");
    // 59:59 까지는 두 칸, 한 시간부터 시간 칸이 선다
    expect(meetingElapsed(3_599_000)).toBe("59:59");
    expect(meetingElapsed(3_600_000)).toBe("1:00:00");
    expect(meetingElapsed(3_930_000)).toBe("1:05:30");
    // 기준점이 없거나 숫자가 아니면 눈금을 비운다 — 시각을 지어내지 않는다
    expect(meetingElapsed(null)).toBe("");
    expect(meetingElapsed(Number.NaN)).toBe("");
    // 음수는 시작 자리로 본다
    expect(meetingElapsed(-5_000)).toBe("00:00");
  });
});

/*
 * W1 — 요청 **출처 상태**의 라벨 (WORK-001 Phase 4·7).
 * 신규 경로는 사람의 판단 없이 업무와 활성 담당을 세운다. 그 사실을 「수락됨」으로 적으면 하지 않은
 * 판단을 기록하는 것이 되고, 「판단 대기」로 적으면 아무도 기다리지 않는데 기다리는 것처럼 읽힌다.
 */
describe("요청 출처 상태 라벨", () => {
  it("`assigned` 는 「즉시 배정됨」이고 「수락됨」과 섞지 않는다", () => {
    expect(workRequestStateLabel.assigned).toBe("즉시 배정됨");
    expect(workRequestStateLabel.assigned).not.toBe(workRequestStateLabel.accepted);
    expect(workRequestStateLabel.assigned).not.toMatch(/수락|대기/);
  });

  it("과거 판단 경로의 다섯 값은 뜻도 문구도 그대로다", () => {
    expect(workRequestStateLabel.pending).toBe("판단 대기");
    expect(workRequestStateLabel.negotiating).toBe("협의 중");
    expect(workRequestStateLabel.accepted).toBe("수락됨");
    expect(workRequestStateLabel.rejected).toBe("거절됨");
    expect(workRequestStateLabel.withdrawn).toBe("철회됨");
  });

  it("톤도 여섯 값 전부에 있다 — 기다림(warning)도 사람의 판단(success)도 아니다", () => {
    expect(Object.keys(workRequestStateTone).sort()).toEqual(Object.keys(workRequestStateLabel).sort());
    expect(workRequestStateTone.assigned).not.toBe(workRequestStateTone.pending);
    expect(workRequestStateTone.assigned).not.toBe(workRequestStateTone.accepted);
  });
});
