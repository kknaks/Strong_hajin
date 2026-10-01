import { addDays } from "./labels";

/**
 * 기간을 그리는 화면의 기본 범위 — **오늘 기준 W-1 ~ W+3** (SPEC-005 §2.4 「기본 범위 규칙」 · WORK-008 F-03 · D-01).
 *
 * 주는 **월요일에 시작하는 달력 주**이고 기준 주가 W0 이다 — 다섯 주. 오늘이 2026-10-01(목)이면
 * 2026-09-21(월) ~ 2026-10-25(일)이다.
 *
 * **두 화면이 범위를 따로 계산하지 않는다** — 프로젝트 간트와 내 업무 타임라인이 이 함수 하나를 쓴다.
 * 간트는 늘 오늘이 기준 주이고, 타임라인은 `‹` `›` 로 기준 주를 ±1 한 같은 계산을 쓴다.
 */

/** 기간 한 덩어리. 두 끝 모두 ISO 날짜(YYYY-MM-DD)이고 `start <= end` 다. */
export type DaySpan = { start: string; end: string };

export type WeekWindow = { from: string; to: string; days: string[] };

/** 그 날이 속한 주의 월요일. */
export function mondayOf(isoDate: string): string {
  const [year, month, day] = isoDate.split("-").map(Number);
  const weekday = new Date(Date.UTC(year, month - 1, day)).getUTCDay(); // 0=일 … 6=토
  return addDays(isoDate, -((weekday + 6) % 7));
}

/**
 * 기준 주(W0)가 든 날 하나를 받아 W-1 월요일 ~ W+3 일요일을 낸다.
 *
 * `spans` 가 그 범위 밖으로 나가면 **넓혀 자르지 않는다** — 넓히는 단위는 **주 경계**다: 앞쪽은
 * 시작날이 속한 주의 월요일, 뒤쪽은 끝날이 속한 주의 일요일까지 (SPEC-005 OQ-608).
 * 넓히지 않을 자리(타임라인의 민 창 — SPEC-001 OQ-P)는 `spans` 를 넘기지 않는다.
 */
export function weekWindow(baseWeek: string, spans: readonly DaySpan[] = []): WeekWindow {
  const monday = mondayOf(baseWeek);
  let from = addDays(monday, -7);
  let to = addDays(monday, 4 * 7 - 1);
  for (const span of spans) {
    if (span.start < from) from = mondayOf(span.start);
    if (span.end > to) to = addDays(mondayOf(span.end), 6);
  }
  const days: string[] = [];
  for (let date = from; date <= to; date = addDays(date, 1)) days.push(date);
  return { from, to, days };
}
