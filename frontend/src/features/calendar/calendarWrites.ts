import { createIdempotencyKey } from "../../lib/idempotency";
import { addDays, calendarDeny, calendarDone, dayDifference } from "../../lib/labels";
import type { CalendarTaskRow, ScheduleRelease, TaskPatch } from "../../lib/viewModels";

/**
 * 캘린더가 **쓰기를 만들 때** 하는 계산 전부 — 부품이 아니라 값만 다룬다 (WORK-004 FE-2).
 *
 * 규율 셋이 이 파일에 걸려 있다:
 *
 * - **가드는 `span_from`·`span_to` 로 판정한다** (K14). 원본 `start_date`/`due_date` 로 막으면
 *   뒤집힌 업무에서 **서버가 받아들이는 날을 화면이 막는다.**
 * - **조용한 거절이 없다** (§I). 거절하는 함수는 전부 **문구를 돌려준다** — `null`/`false` 만 주고
 *   부르는 쪽이 알아서 침묵하게 두지 않는다.
 * - **문구는 우리 것이다.** 서버 본문은 `{"detail": "<문장>"}` 뿐이고 `code` 가 없으며,
 *   `WORK_SCHEDULE_START_AFTER_DUE` 는 **영문**이다. 무엇을 낼지는 **상태 코드 + 명령**으로 고른다.
 */

/** 어느 명령이 거절당했나. 본문에 `code` 가 없으므로 이것이 상태 코드의 짝이다. */
export type ScheduleCommand = "task_dates" | "schedule_create" | "schedule_update";

/** 띠의 어느 끝인가. **정체는 화면의 좌우가 아니라 «필드»다** (WARN-A). */
export type DateEdge = "start" | "end";

export type Span = { from: string; to: string };

const DAY_MINUTES = 24 * 60;
const SNAP = 30;
/** 시간 격자에 떨어뜨렸을 때의 기본 길이 (SPEC §2.3 R5). */
const DEFAULT_SLOT = 60;

/** 서버가 정규화한 구간. **화면이 다시 계산하지 않는다** (K14). */
export function spanOf(row: CalendarTaskRow): Span | null {
  return row.span_from && row.span_to ? { from: row.span_from, to: row.span_to } : null;
}

/**
 * R1 — 날짜 칸에 떨어뜨리면 **기간이 옮겨 간다.**
 *
 * 「길이」는 **span 길이**다(WARN-A) — 원본 두 날짜의 차이가 아니다. 뒤집힌 업무에서 raw 차이를
 * 쓰면 음수가 나오고, 뒤집힌 채로 옮기면 서버의 `validate_schedule` 이 거절한다.
 * 그래서 옮긴 결과는 **언제나 뒤집히지 않은 같은 길이의 기간**이다.
 *
 * 한쪽만 있던 업무는 **있는 쪽만** 옮긴다 — 없는 날짜를 지어내지 않는다.
 * 둘 다 없으면 그 날 하루짜리 기간이 **생긴다**(R2 가 시작되는 자리).
 */
export function moveTaskDates(row: CalendarTaskRow, date: string): TaskPatch {
  if (row.start_date && row.due_date) {
    const span = spanOf(row);
    const length = span ? dayDifference(span.from, span.to) : 0;
    return { start_date: date, due_date: addDays(date, length) };
  }
  if (row.due_date) return { due_date: date };
  if (row.start_date) return { start_date: date };
  return { start_date: date, due_date: date };
}

/**
 * R3·R4 — 좌우 손잡이.
 *
 * **`start` 손잡이는 `start_date` 를, `end` 손잡이는 `due_date` 를 쓴다 — 뒤집힌 업무에서도 그렇다**
 * (WARN-A). 띠 위에서 그 둘의 좌우가 바뀌어 보일 수 있다. 손잡이의 정체가 **필드**여야
 * 「왼쪽을 끌었는데 마감이 바뀐다」가 예측 가능해진다 — 화면 위치에 물리면 같은 손잡이가
 * 업무마다 다른 필드를 바꾼다.
 *
 * **R4** 가 여기서 난다 — 마감만 있던 업무에 `start` 손잡이를 끌면 `start_date` 가 채워지고
 * 그 순간 기간이 선다.
 *
 * 시안은 역전을 `min`/`max` 로 **조용히 접는다.** 우리는 접지 않고 **말한다**(§I) —
 * 서버 규칙(`validate_schedule`)과 같은 판정을 화면에서 미리 해서 같은 문장을 낸다.
 */
export function resizeTaskDates(row: CalendarTaskRow, edge: DateEdge, date: string): { patch: TaskPatch } | { deny: string } {
  if (edge === "start") {
    if (row.due_date && date > row.due_date) return { deny: calendarDeny.startAfterDue };
    return { patch: { start_date: date } };
  }
  if (row.start_date && row.start_date > date) return { deny: calendarDeny.startAfterDue };
  return { patch: { due_date: date } };
}

/**
 * 날짜 칸 드롭의 가드 — **기간 가드가 없다**(§2.3 R1). 기한 없는 업무도 떨어진다.
 * 거기서 R2 의 「날짜부터」가 실제로 일어난다.
 */
export function dropGuard(row: CalendarTaskRow, canManage: boolean): string | null {
  return canManage ? null : calendarDeny.notMine;
}

/**
 * R5 시간 격자 드롭의 가드 — **`span_from`·`span_to` 로 판정한다** (K14).
 *
 * 화면 가드는 **정본이 아니다** — 서버가 같은 규칙을 다시 본다. 여기서 막는 이유는
 * 「왜 안 되는지」를 **끌기 전에** 말하기 위해서다.
 */
export function slotGuard(row: CalendarTaskRow, date: string, canManage: boolean): string | null {
  if (!canManage) return calendarDeny.notMine;
  const span = spanOf(row);
  if (!span) return calendarDeny.unscheduled;
  if (date < span.from || date > span.to) return calendarDeny.outOfRange(span.from, span.to);
  return null;
}

/** 30분 눈금. **DB 가 강제하지 않는다** — 화면 편의다(§J). 자정을 넘지 않는다(§A). */
export function snapClock(minutes: number): string {
  if (!Number.isFinite(minutes)) return clock(0);
  return clock(Math.max(0, Math.min(DAY_MINUTES - SNAP, Math.round(minutes / SNAP) * SNAP)));
}

function clock(minutes: number): string {
  // 유한하지 않은 값은 **절대 서버로 나가지 않는다** — `"NaN:NaN"` 은 그대로 422 이고,
  // 포인터 좌표가 없는 환경(합성 이벤트 · 보조기술)에서 실제로 그런 값이 들어온다.
  const safe = Number.isFinite(minutes) ? minutes : 0;
  return `${String(Math.floor(safe / 60)).padStart(2, "0")}:${String(safe % 60).padStart(2, "0")}`;
}

/**
 * 떨어뜨린 자리에서 **한 시간**짜리 한 칸 (SPEC §2.3 R5).
 *
 * ⚠ **`24:00` 을 만들지 않는다.** 서버가 받는 것은 `datetime.time` 이라 하루의 마지막 순간이
 * **`23:59`** 다 — 자정은 저장 타입이 애초에 표현하지 못한다(§A). 그래서 하루 끝에 떨어뜨리면
 * 한 시간이 다 안 차고 `23:59` 에 붙는다. 시안은 `24:00` 을 만들지만 시안에는 서버가 없다.
 */
export function defaultSlot(minutes: number): { starts_at: string; ends_at: string } {
  const at = Number.isFinite(minutes) ? minutes : 0;
  const start = Math.max(0, Math.min(DAY_MINUTES - SNAP, Math.round(at / SNAP) * SNAP));
  return { starts_at: clock(start), ends_at: clock(Math.min(start + DEFAULT_SLOT, DAY_MINUTES - 1)) };
}

/**
 * **연타는 같은 키로 보낸다** (K12).
 *
 * 키를 매번 새로 만들면 두 번째 클릭이 **`409`** 를 맞는다 — 그 날은 이미 찼기 때문이다.
 * 같은 키면 서버가 **`200` 영수증**(본문이 `201` 과 같다)을 돌려준다. 그래서 키의 유효 범위는
 * **「이 업무의 이 날 이 시각」이라는 하나의 제출 의도**다. 내용이 달라지면 다른 제출이므로 키도 다르다.
 */
export function scheduleKey(
  cache: Map<string, string>,
  taskId: string,
  input: { on_date: string; starts_at: string; ends_at: string },
): string {
  const at = `${taskId}|${input.on_date}|${input.starts_at}|${input.ends_at}`;
  const known = cache.get(at);
  if (known) return known;
  const fresh = createIdempotencyKey();
  cache.set(at, fresh);
  return fresh;
}

/**
 * 서버가 거절했을 때 낼 말.
 *
 * 본문에 `code` 가 없으므로 **상태 코드 + 어떤 명령을 불렀는지**로 가른다.
 *
 * `POST` 의 `409` 는 계약상 둘(`TASK_SCHEDULE_DAY_TAKEN` · `TASK_SCHEDULE_TASK_CLOSED`)인데,
 * **끝난 업무는 캘린더에 애초에 실리지 않으므로**(§B 읽기 필터) 화면에서 올 수 있는 것은 앞엣것뿐이다.
 * 그리고 정상 흐름에서는 그것도 안 보인다 — 그 날에 배정이 있으면 화면이 처음부터 `PATCH` 를 부른다(K10).
 * 여기 남는 것은 **경합**이다.
 */
export function denyMessage(command: ScheduleCommand, status: number, span: Span | null): string {
  if (status === 403) return calendarDeny.notMine;
  if (status === 404) return calendarDeny.notFound;
  if (status === 409) return command === "schedule_create" ? calendarDeny.dayTaken : calendarDeny.versionConflict;
  if (status === 422) {
    if (command === "task_dates") return calendarDeny.startAfterDue;
    if (command === "schedule_update") return calendarDeny.invalidRange;
    return span ? calendarDeny.outOfRange(span.from, span.to) : calendarDeny.unscheduled;
  }
  return command === "task_dates" ? calendarDeny.datesFailed : calendarDeny.scheduleFailed;
}

/**
 * K3 — 기간이 줄어 **닫힌 배정의 건수**를 말한다.
 *
 * **0 건이면 아무 말도 하지 않는다.** 칸 자체가 없는 응답에서도 마찬가지다 —
 * `schedule_release` 를 싣는 표면은 셋뿐이고(`PATCH /api/tasks` · `POST …/start` ·
 * `POST …/proposals/{id}/respond`), `/block`·`/resume`·`/complete`·`/cancel` 에는 **없다.**
 */
export function releaseNotice(release: ScheduleRelease | null | undefined): string | null {
  if (!release || release.released_count <= 0) return null;
  return calendarDone.released(release.released_count);
}

/** 손잡이를 잡고 있는 동안의 상태. `date` 는 포인터가 지나는 칸이고, **아직 보내지 않았다.** */
export type HandleGrab = { taskId: string; edge: DateEdge; date: string | null };

/**
 * 끄는 동안 띠를 **미리 그려 보여 준다** — 보내지는 않는다.
 *
 * 잡은 끝만 포인터가 지나는 칸까지 당기고, 맞은편을 넘지 않게 접는다. 여기서 접는 것은 **그림**이고,
 * 실제로 보낼 값이 역전이면 `resizeTaskDates` 가 **말로 거절한다**(§I) — 그림의 접기가 거절을 삼키지 않는다.
 */
export function previewResize<T extends { from: string; to: string; time: string | null; taskId: string | null }>(
  segments: T[],
  grab: HandleGrab | null,
): T[] {
  if (!grab?.date) return segments;
  const at = grab.date;
  return segments.map((segment) => {
    if (segment.taskId !== grab.taskId || segment.time !== null) return segment;
    if (grab.edge === "start") return at > segment.to ? segment : { ...segment, from: at };
    return at < segment.from ? segment : { ...segment, to: at };
  });
}
