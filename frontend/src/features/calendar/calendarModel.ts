import { addDays, calendarScreen, dayDifference, formatDate, isoDateInSeoul, meetingScreen, personName } from "../../lib/labels";
import type { CalendarEntry, CalendarMeetingRow, CalendarTaskRow } from "../../lib/viewModels";

/**
 * 캘린더가 격자와 레일을 그리기 위해 하는 **계산 전부** — 화면 부품이 아니라 값만 다룬다.
 *
 * 규율 하나를 이 파일이 지킨다: **업무의 기간을 여기서 계산하지 않는다** (K14).
 * 합본 조회가 정규화한 `span_from`·`span_to` 를 그대로 쓰고, 원본 `start_date`·`due_date` 는
 * **카드의 말**(「…마감」·「기한 없음」)에만 닿는다 — 뒤집힌 업무에서는 `start_date > due_date` 가
 * 실제로 오기 때문에 그 둘로 띠를 그리면 서버와 다른 구간이 선다.
 * `features/work` 의 `taskSpan()` 은 `TaskTimeline` 과 공유라 **여기서 부르지 않는다.**
 *
 * 날짜는 전부 ISO `YYYY-MM-DD` 문자열이고, 셈은 UTC 자정으로 한다 —
 * 로컬 시간대의 서머타임이 하루를 먹지 않게 하는 자리다(`lib/labels` 의 `addDays` 와 같은 규칙).
 */

export type CalendarView = "month" | "week";
export type CalendarTab = "all" | "meeting" | "task";

export type MonthDay = { date: string; out: boolean };

/** 0=일 … 6=토. */
export function weekdayIndex(isoDate: string): number {
  const [year, month, day] = isoDate.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day)).getUTCDay();
}

function isoOf(year: number, month: number, day: number): string {
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.toISOString().slice(0, 10);
}

/**
 * 그 달이 걸친 주를 일요일 시작으로 모두 채운다.
 *
 * **완전히 달 밖인 마지막 주는 지운다**(6주 → 5주 — SPEC §2.1). 시안의 `day: number` 대신
 * ISO 를 들고 다니므로 달 밖 칸도 자기 날짜를 갖는다 — 달 경계를 넘는 이동이 허용이기 때문이다(§2.2).
 */
export function monthGridDays(year: number, month: number): MonthDay[] {
  const first = isoOf(year, month, 1);
  const start = addDays(first, -weekdayIndex(first));
  const days: MonthDay[] = [];
  for (let index = 0; index < 42; index += 1) {
    const date = addDays(start, index);
    days.push({ date, out: Number(date.slice(5, 7)) !== month || Number(date.slice(0, 4)) !== year });
  }
  while (days.length > 35 && days[days.length - 1].out && days[days.length - 7].out) days.length -= 7;
  return days;
}

/** 그 날이 속한 주의 일요일부터 이레. */
export function weekGridDays(anchor: string): string[] {
  const sunday = addDays(anchor, -weekdayIndex(anchor));
  return Array.from({ length: 7 }, (_, index) => addDays(sunday, index));
}

/** 달을 옮긴다 — 그 달 1일로 간다. 말일이 없는 달로 밀려 날짜가 흘러넘치는 것을 막는다. */
export function shiftMonth(anchor: string, delta: number): string {
  const year = Number(anchor.slice(0, 4));
  const month = Number(anchor.slice(5, 7));
  const moved = new Date(Date.UTC(year, month - 1 + delta, 1));
  return moved.toISOString().slice(0, 10);
}

export function shiftWeek(anchor: string, delta: number): string {
  return addDays(anchor, delta * 7);
}

/* ---- 사무실 시간대 ----
   회의는 UTC ISO 로 오고 업무 배정은 `on_date` + 벽시계 `HH:MM` 으로 온다. 격자는 한 축이어야 하므로
   회의 쪽을 서울로 옮겨 **같은 꼴**로 만든다. */

export function seoulDate(iso: string): string {
  return isoDateInSeoul(iso) ?? iso.slice(0, 10);
}

export function seoulClock(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso.slice(11, 16);
  return date.toLocaleTimeString("en-GB", { timeZone: "Asia/Seoul", hour: "2-digit", minute: "2-digit" });
}

/** `HH:MM` → 자정부터의 분. */
export function clockMinutes(clock: string): number {
  const [hour, minute] = clock.split(":").map(Number);
  return (hour || 0) * 60 + (minute || 0);
}

const DAY_MINUTES = 24 * 60;

function meetingTitle(row: CalendarMeetingRow): string {
  return row.title ?? meetingScreen.noTitle;
}

/* ---- 격자 조각 ---- */

/**
 * 격자에 서는 한 조각.
 *
 * **상태를 싣지 않는다** — 격자의 색은 유형 둘(업무·회의)로만 칠한다(SPEC §2.6).
 * `time` 이 있으면 「그 날 몇 시」짜리 한 칸이고, 없으면 `from`~`to` 를 잇는 날짜 띠다.
 */
export type CalendarSegment = {
  key: string;
  kind: "task" | "meeting";
  title: string;
  from: string;
  to: string;
  time: string | null;
  /** 업무 띠·업무 배정이면 그 업무. 회의는 `null` 이다 — 캘린더는 회의에 쓰기를 내지 않는다(§2.4). */
  taskId: string | null;
};

function taskBar(row: CalendarTaskRow): CalendarSegment | null {
  // 기간이 없는 업무는 격자에 자리가 없다. 레일에는 그대로 선다(R2 — 「날짜부터」).
  if (!row.span_from || !row.span_to) return null;
  return { key: `task:${row.task_id}`, kind: "task", title: row.title, from: row.span_from, to: row.span_to, time: null, taskId: row.task_id };
}

function taskSlots(row: CalendarTaskRow): CalendarSegment[] {
  return row.schedules.map((schedule) => ({
    key: `schedule:${schedule.schedule_id}`,
    kind: "task" as const,
    title: row.title,
    from: schedule.on_date,
    to: schedule.on_date,
    time: schedule.starts_at,
    taskId: row.task_id,
  }));
}

function meetingChip(row: CalendarMeetingRow): CalendarSegment {
  return {
    key: `meeting:${row.meeting_id}`,
    kind: "meeting",
    title: meetingTitle(row),
    from: seoulDate(row.starts_at),
    to: seoulDate(row.ends_at),
    time: seoulClock(row.starts_at),
    taskId: null,
  };
}

/**
 * 탭은 레일과 격자를 **동시에** 가른다 — 다만 대칭이 아니다(SPEC §2.1).
 * 격자의 「업무」 탭은 **업무 날짜 띠와 업무 시간 배정 둘 다**를 낸다.
 */
export function gridSegments(entries: CalendarEntry[], tab: CalendarTab): CalendarSegment[] {
  const segments: CalendarSegment[] = [];
  for (const entry of entries) {
    if (entry.kind === "task") {
      if (tab === "meeting") continue;
      const bar = taskBar(entry);
      if (bar) segments.push(bar);
      segments.push(...taskSlots(entry));
    } else if (tab !== "task") {
      segments.push(meetingChip(entry));
    }
  }
  return segments;
}

/** 주 뷰 **종일 칸** — 날짜 단위인 업무 띠만 선다. 시간이 붙은 것은 아래 시간 격자가 받는다. */
export function spanSegments(entries: CalendarEntry[], tab: CalendarTab): CalendarSegment[] {
  if (tab === "meeting") return [];
  return entries.flatMap((entry) => (entry.kind === "task" ? [taskBar(entry)].filter(Boolean) as CalendarSegment[] : []));
}

/**
 * 한 주 안에서 조각을 줄로 나눈다 — **같은 항목은 그 주 내내 같은 줄에 앉는다**(SPEC §2.1).
 * 그렇지 않으면 여러 날 띠가 칸마다 다른 높이에 서서 끊긴다.
 */
export function packLanes(segments: CalendarSegment[], days: string[]): CalendarSegment[][] {
  if (!days.length) return [];
  const first = days[0];
  const last = days[days.length - 1];
  const inWeek = segments
    .filter((segment) => segment.to >= first && segment.from <= last)
    .sort((left, right) => {
      if (left.from !== right.from) return left.from < right.from ? -1 : 1;
      const leftSpan = length(left);
      const rightSpan = length(right);
      if (leftSpan !== rightSpan) return rightSpan - leftSpan;
      return left.key < right.key ? -1 : 1;
    });
  const lanes: CalendarSegment[][] = [];
  for (const segment of inWeek) {
    const free = lanes.findIndex((lane) => lane.every((seated) => seated.to < segment.from || seated.from > segment.to));
    if (free === -1) lanes.push([segment]);
    else lanes[free].push(segment);
  }
  return lanes;
}

function length(segment: CalendarSegment): number {
  return Number(new Date(`${segment.to}T00:00:00Z`)) - Number(new Date(`${segment.from}T00:00:00Z`));
}

/** 그 날을 덮는 조각을 줄마다 하나씩 — 없는 줄은 `null` 이라 빈 자리가 높이를 지킨다. */
export function laneSeats(lanes: CalendarSegment[][], date: string): Array<CalendarSegment | null> {
  return lanes.map((lane) => lane.find((segment) => segment.from <= date && date <= segment.to) ?? null);
}

/* ---- 주 뷰 시간 격자 ---- */

export type TimedBlock = {
  key: string;
  kind: "task" | "meeting";
  title: string;
  date: string;
  startMin: number;
  endMin: number;
  startLabel: string;
  endLabel: string;
  /** 업무 배정일 때만 — 시각 변경(FE-2)이 이 둘을 쓴다. */
  scheduleId: string | null;
  version: number | null;
  taskId: string | null;
};

export function timedBlocks(entries: CalendarEntry[], tab: CalendarTab): TimedBlock[] {
  const blocks: TimedBlock[] = [];
  for (const entry of entries) {
    if (entry.kind === "task") {
      if (tab === "meeting") continue;
      for (const schedule of entry.schedules) {
        blocks.push({
          key: `schedule:${schedule.schedule_id}`,
          kind: "task",
          title: entry.title,
          date: schedule.on_date,
          startMin: clockMinutes(schedule.starts_at),
          endMin: clockMinutes(schedule.ends_at),
          startLabel: schedule.starts_at,
          endLabel: schedule.ends_at,
          scheduleId: schedule.schedule_id,
          version: schedule.version,
          taskId: entry.task_id,
        });
      }
      continue;
    }
    if (tab === "task") continue;
    const date = seoulDate(entry.starts_at);
    // 자정을 넘는 회의는 그 날 끝까지만 그린다 — 다음 날 칸으로 흘리지 않는다.
    const endsSameDay = seoulDate(entry.ends_at) === date;
    blocks.push({
      key: `meeting:${entry.meeting_id}`,
      kind: "meeting",
      title: meetingTitle(entry),
      date,
      startMin: clockMinutes(seoulClock(entry.starts_at)),
      endMin: endsSameDay ? clockMinutes(seoulClock(entry.ends_at)) : DAY_MINUTES,
      startLabel: seoulClock(entry.starts_at),
      endLabel: seoulClock(entry.ends_at),
      scheduleId: null,
      version: null,
      taskId: null,
    });
  }
  return blocks;
}

/* ---- 좌측 일정 레일 ---- */

/**
 * 레일 카드 하나.
 *
 * **상태를 싣지 않는다** (K15) — 합본 조회의 `state` 는 `completion_submitted` 를 `"done"` 으로
 * 투영하고 행에 `derived` 가 없어서, 카드가 상태를 내면 승인 대기인 업무를 「완료」라고 말한다.
 * 유형 배지 하나만 낸다. 상태는 카드를 열어 상세에서 읽는다.
 */
export type RailCard = {
  key: string;
  kind: "task" | "meeting";
  id: string;
  title: string;
  when: string;
  meta: string[];
  sort: string;
};

export type RailScope = { from: string; to: string; selected: string | null };

function overlaps(from: string, to: string, scope: RailScope): boolean {
  if (to < scope.from || from > scope.to) return false;
  if (scope.selected && (scope.selected < from || scope.selected > to)) return false;
  return true;
}

function taskWhen(row: CalendarTaskRow): string {
  if (!row.span_from || !row.span_to) return calendarScreen.undated;
  // 「마감만 있다」와 「하루짜리 기간」은 정규화 구간이 같다 — 그 둘을 가르는 것은 원본 필드뿐이다.
  if (!row.start_date && row.due_date) return calendarScreen.dueOnly(row.due_date);
  return calendarScreen.range(row.span_from, row.span_to);
}

export function railCards(entries: CalendarEntry[], tab: CalendarTab, scope: RailScope): RailCard[] {
  const cards: RailCard[] = [];
  for (const entry of entries) {
    if (entry.kind === "task") {
      if (tab === "meeting") continue;
      const dated = Boolean(entry.span_from && entry.span_to);
      if (dated && !overlaps(entry.span_from!, entry.span_to!, scope)) continue;
      // 기한 없는 업무는 어느 날에도 걸치지 않는다 — 날짜를 고른 동안에는 빠진다.
      if (!dated && scope.selected) continue;
      cards.push({
        key: `task:${entry.task_id}`,
        kind: "task",
        id: entry.task_id,
        title: entry.title,
        when: taskWhen(entry),
        // 업무의 시간 배정은 자기 카드를 세우지 않고 이 줄로 접힌다 (SPEC §2.1).
        meta: entry.schedules.map((schedule) => calendarScreen.scheduleChip(schedule.on_date, schedule.starts_at)),
        sort: dated ? entry.span_from! : "9999-12-31",
      });
      continue;
    }
    if (tab === "task") continue;
    const date = seoulDate(entry.starts_at);
    if (!overlaps(date, seoulDate(entry.ends_at), scope)) continue;
    cards.push({
      key: `meeting:${entry.meeting_id}`,
      kind: "meeting",
      id: entry.meeting_id,
      title: meetingTitle(entry),
      when: `${formatDate(date)} ${calendarScreen.clockRange(seoulClock(entry.starts_at), seoulClock(entry.ends_at))}`,
      // 주최자는 **표시 이름**으로 낸다 — `created_by` 는 member id 라 화면에 내지 않는다 (K4).
      meta: [entry.location, personName(entry.created_by_display_name)].filter((value): value is string => Boolean(value)),
      sort: `${date}T${seoulClock(entry.starts_at)}`,
    });
  }
  return cards.sort((left, right) => (left.sort === right.sort ? (left.key < right.key ? -1 : 1) : left.sort < right.sort ? -1 : 1));
}

/** 그 조각이 이번 주 안에서 덮는 칸 수 — 띠 위의 글자가 흘러갈 폭이다. */
export function dayReach(segment: CalendarSegment, weekFirst: string, weekLast: string): number {
  const from = segment.from > weekFirst ? segment.from : weekFirst;
  const to = segment.to < weekLast ? segment.to : weekLast;
  return Math.max(1, dayDifference(from, to) + 1);
}

/**
 * 그 주가 어느 달의 몇째 주인가 — 머리줄이 쓴다.
 *
 * **그 주의 수요일이 속한 달**로 센다. 달을 걸치는 주에서 「2월 5주차」와 「3월 1주차」 중 하나를
 * 골라야 하는데, 이레 중 나흘 이상이 있는 쪽이 사람이 읽는 그 주의 달이다.
 */
export function weekOfMonth(days: string[]): { year: number; month: number; week: number } {
  const middle = days[3] ?? days[0];
  const year = Number(middle.slice(0, 4));
  const month = Number(middle.slice(5, 7));
  const first = `${middle.slice(0, 8)}01`;
  return { year, month, week: Math.ceil((Number(middle.slice(8, 10)) + weekdayIndex(first)) / 7) };
}
