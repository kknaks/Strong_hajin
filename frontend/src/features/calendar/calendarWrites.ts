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

/** 겹침을 볼 때 필요한 한 칸. `TimedBlock` 이 이 모양을 만족한다. */
export type BusySpan = { key: string; date: string; startMin: number; endMin: number };

/**
 * **반열림 `[시작, 끝)`** — 경계가 닿는 것은 겹침이 **아니다** (증보 K22).
 *
 * 10:00–11:00 과 11:00–12:00 은 **둘 다 선다.** 서버의 `modules/time_blocks.overlaps` 와
 * **같은 규칙**이어야 한다 — 화면이 더 엄하면 **서버가 받는 것을 화면이 막는다.**
 */
export function overlapsMinutes(left: { startMin: number; endMin: number }, right: { startMin: number; endMin: number }): boolean {
  return left.startMin < right.endMin && right.startMin < left.endMin;
}

/**
 * 그 자리가 **내 다른 일정과 겹치나** — 겹치면 **문구**를 돌려준다 (§I 조용한 거절 0개).
 *
 * **왜 화면이 먼저 보는가.** 서버가 같은 것을 다시 본다(그쪽이 정본이다). 여기서 보는 이유는
 * 겹침이 **정상적인 손놀림**이기 때문이다 — 빈 줄처럼 보이는 자리에 떨어뜨렸는데 왜 안 되는지를
 * **보내기 전에** 말해야 한다. 서버까지 갔다 오면 그 사이에 화면이 한 번 흔들린다.
 *
 * **화면은 서버보다 엄하지 않다** — `blocks` 는 `calendarModel.blockingBlocks()` 가 고른
 * **서버의 블록 집합과 같은 것**이고(K25·K26·K27), 판정은 위의 반열림 하나다.
 * 자정을 넘는 회의처럼 화면이 **덜** 아는 자리는 남지만, 그쪽은 서버가 말한다 — 안전한 방향이다.
 *
 * `ignoreKey` 는 **고치는 중인 자기 자신**을 뺀다. 없으면 10:00–11:00 을 10:30–11:30 으로 옮기는 것이
 * **자기와 겹쳐** 거절된다 — 서버의 `ignore_schedule_id` 와 같은 자리다.
 */
export function overlapGuard(
  blocks: BusySpan[],
  at: { date: string; startMin: number; endMin: number },
  ignoreKey: string | null = null,
): string | null {
  const hit = blocks.some((block) => block.date === at.date && block.key !== ignoreKey && overlapsMinutes(block, at));
  return hit ? calendarDeny.overlap : null;
}

/** `HH:MM` → 자정부터의 분. 가드가 벽시계와 눈금을 같은 축에서 보게 한다. */
export function minutesOfClock(clock: string): number {
  const [hour, minute] = clock.split(":").map(Number);
  return (Number.isFinite(hour) ? hour : 0) * 60 + (Number.isFinite(minute) ? minute : 0);
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
 * **본문에 `code` 가 없다.** 겹침 `409` 의 `detail` 은 **문자열**이고(방예약 `409` 들만 `{code, message}`
 * 객체다), 그래서 `detail.code` 를 읽으면 `undefined` 다. 가를 수 있는 것은
 * **상태 코드 + 어떤 명령을 불렀는지** 둘뿐이다.
 *
 * **`409` 의 갈래 — 무엇이 실제로 올 수 있나.**
 *
 * | 명령 | 계약상 올 수 있는 것 | 화면에서 실제로 |
 * |---|---|---|
 * | `schedule_create` | `DAY_TAKEN` · `TASK_CLOSED` · **`OVERLAP`** | 앞의 둘은 **구조적으로 안 온다** — 끝난 업무는 합본 조회에 없고(§B), 그 날에 배정이 있으면 화면이 처음부터 `PATCH` 를 부른다(K10). 남는 것이 **겹침**이다 |
 * | `schedule_update` | `VERSION_CONFLICT` · **`OVERLAP`** | **둘을 가를 수 없다.** 문구는 회차 쪽을 쓴다 — 아래 |
 *
 * **`schedule_update` 의 `409` 에 겹침 문구를 쓰지 않는 이유.** 겹침은 **보내기 전에**
 * `overlapGuard` 가 잡아 그 문장을 이미 말했다. 여기까지 온 것은 **그 사이에 무언가 바뀐 것**이고,
 * 회차가 어긋났든 남이 그 시간을 방금 채웠든 **답은 같다 — 다시 읽어야 한다.**
 * 「다른 곳에서 먼저 바뀌었습니다」가 두 경우 모두에 참이다. **가를 수 없는 것을 가른 척하지 않는다.**
 */
export function denyMessage(command: ScheduleCommand, status: number, span: Span | null): string {
  if (status === 403) return calendarDeny.notMine;
  if (status === 404) return calendarDeny.notFound;
  if (status === 409) return command === "schedule_create" ? calendarDeny.overlap : calendarDeny.versionConflict;
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
