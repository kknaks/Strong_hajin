import { describe, expect, it } from "vitest";
import { mondayOf, weekWindow } from "./weekWindow";

/* SPEC-005 §2.4 「기본 범위 규칙」 — 간트(L-53 · L-54)와 내 업무 타임라인(U-16)이 함께 쓰는 계산. */
describe("weekWindow — 기준 주 W0 의 W-1 ~ W+3", () => {
  it("오늘이 2026-10-01(목)이면 2026-09-21(월) ~ 2026-10-25(일) 다섯 주다", () => {
    const window = weekWindow("2026-10-01");
    expect(window.from).toBe("2026-09-21");
    expect(window.to).toBe("2026-10-25");
    expect(window.days).toHaveLength(35);
    expect(window.days[0]).toBe("2026-09-21");
    expect(window.days.at(-1)).toBe("2026-10-25");
  });

  it("월요일 경계 — 월요일은 자기 주가 W0, 일요일은 앞 월요일의 주가 W0 이다", () => {
    expect(mondayOf("2026-09-28")).toBe("2026-09-28"); // 월
    expect(mondayOf("2026-10-04")).toBe("2026-09-28"); // 일
    expect(mondayOf("2026-10-05")).toBe("2026-10-05"); // 월
    expect(weekWindow("2026-09-28").from).toBe("2026-09-21");
    expect(weekWindow("2026-10-04").from).toBe("2026-09-21");
    expect(weekWindow("2026-10-04").to).toBe("2026-10-25");
    expect(weekWindow("2026-10-05").from).toBe("2026-09-28");
  });

  it("해·달을 넘는 주도 월요일에서 시작한다", () => {
    // 2027-01-01 은 금요일 → 그 주 월요일 2026-12-28
    expect(mondayOf("2027-01-01")).toBe("2026-12-28");
    expect(weekWindow("2027-01-01").from).toBe("2026-12-21");
  });

  it("범위 밖 기간이 있으면 주 경계까지 넓힌다 — 앞쪽은 시작날의 월요일, 뒤쪽은 끝날의 일요일", () => {
    const window = weekWindow("2026-10-01", [
      { start: "2026-09-10", end: "2026-09-12" },
      { start: "2026-10-30", end: "2026-11-04" },
    ]);
    expect(window.from).toBe("2026-09-07");
    expect(window.to).toBe("2026-11-08");
    expect(window.days).toHaveLength(63);
  });

  it("범위 안 기간은 아무것도 바꾸지 않는다 — 끝이 경계에 닿아도 그대로다", () => {
    const window = weekWindow("2026-10-01", [{ start: "2026-09-21", end: "2026-10-25" }]);
    expect(window.from).toBe("2026-09-21");
    expect(window.to).toBe("2026-10-25");
  });
});
