import { describe, expect, it } from "vitest";

import type { CalendarEntry, CalendarMeetingRow, CalendarTaskRow } from "../../lib/viewModels";
import {
  blockingBlocks,
  laneSeats,
  monthGridDays,
  monthSegments,
  packBlocks,
  packLanes,
  railCards,
  seoulClock,
  seoulDate,
  spanSegments,
  timedBlocks,
  weekGridDays,
} from "./calendarModel";

const task = (over: Partial<CalendarTaskRow> = {}): CalendarTaskRow => ({
  kind: "task",
  task_id: "t1",
  title: "업무 하나",
  state: "open",
  start_date: "2027-03-01",
  due_date: "2027-03-05",
  span_from: "2027-03-01",
  span_to: "2027-03-05",
  version: 1,
  approval: null,
  schedules: [],
  ...over,
});

const meeting = (over: Partial<CalendarMeetingRow> = {}): CalendarMeetingRow => ({
  kind: "meeting",
  meeting_id: "m1",
  title: "합본 확인 회의",
  starts_at: "2027-03-04T01:00:00+00:00",
  ends_at: "2027-03-04T02:00:00+00:00",
  location: null,
  status: "scheduled",
  viewer_relation: "attendee",
  created_by: "mina",
  attendee_count: 1,
  created_by_display_name: "민아 (구성원)",
  ...over,
});

describe("monthGridDays", () => {
  it("일요일에서 시작해 그 달이 걸친 주를 채운다", () => {
    const days = monthGridDays(2027, 3);
    expect(days[0].date).toBe("2027-02-28");
    expect(days[0].out).toBe(true);
    expect(days.length % 7).toBe(0);
    expect(days.find((day) => day.date === "2027-03-01")?.out).toBe(false);
  });

  it("완전히 달 밖인 마지막 주를 지운다 — 6주가 5주가 된다", () => {
    // 2027-08 은 일요일 시작이라 42칸이면 마지막 한 주가 통째로 9월이다.
    const days = monthGridDays(2027, 8);
    expect(days.length).toBe(35);
    expect(days.slice(-7).every((day) => day.out)).toBe(false);
  });
});

describe("weekGridDays", () => {
  it("어느 날을 주어도 그 주 일요일부터 이레를 낸다", () => {
    expect(weekGridDays("2027-03-04")).toEqual([
      "2027-02-28", "2027-03-01", "2027-03-02", "2027-03-03", "2027-03-04", "2027-03-05", "2027-03-06",
    ]);
  });
});

describe("monthSegments", () => {
  it("뒤집힌 업무의 띠를 서버가 준 구간 그대로 그린다 — start_date 를 쓰지 않는다", () => {
    const flipped = task({ start_date: "2027-09-06", due_date: "2027-09-04", span_from: "2027-09-04", span_to: "2027-09-06" });
    const [bar] = monthSegments([flipped], "all").filter((segment) => segment.time === null);
    expect([bar.from, bar.to]).toEqual(["2027-09-04", "2027-09-06"]);
  });

  it("기간 없는 업무는 격자에 띠를 내지 않는다", () => {
    const undated = task({ start_date: null, due_date: null, span_from: null, span_to: null });
    expect(monthSegments([undated], "all")).toEqual([]);
  });

  it("회의는 UTC 를 서울 날짜·시각으로 옮겨 놓는다", () => {
    const [segment] = monthSegments([meeting()], "meeting");
    expect(segment.from).toBe("2027-03-04");
    expect(segment.time).toBe("10:00");
  });

  /* K20 — 앞 판은 여기서 띠와 배정을 **둘 다** 냈다(업무 탭 2건 · 전체 3건).
     같은 업무가 한 칸에 두 번 뜨는 자리라 1건 · 2건으로 바뀐다. */
  it("월 뷰는 업무의 시간 배정을 그리지 않는다 — 같은 업무가 두 번 뜨지 않는다 (K20)", () => {
    const withSchedule = task({ schedules: [{ schedule_id: "s1", on_date: "2027-03-03", starts_at: "10:00", ends_at: "11:30", version: 1 }] });
    const segments = monthSegments([withSchedule], "all");
    expect(segments).toHaveLength(1);
    expect(segments[0].time).toBeNull();
    expect(segments.some((segment) => segment.key.startsWith("schedule:"))).toBe(false);
  });

  it("회의는 월 뷰에도 그려진다 — 회의는 그 자체가 일정이다 (K20)", () => {
    const withSchedule = task({ schedules: [{ schedule_id: "s1", on_date: "2027-03-03", starts_at: "10:00", ends_at: "11:30", version: 1 }] });
    const entries: CalendarEntry[] = [withSchedule, meeting()];
    expect(monthSegments(entries, "all").map((segment) => segment.key)).toEqual(["task:t1", "meeting:m1"]);
  });

  it("탭이 격자를 가른다 — 업무 탭에 회의가 서지 않고, 회의 탭에 업무 띠가 서지 않는다", () => {
    const withSchedule = task({ schedules: [{ schedule_id: "s1", on_date: "2027-03-03", starts_at: "10:00", ends_at: "11:30", version: 1 }] });
    const entries: CalendarEntry[] = [withSchedule, meeting()];
    expect(monthSegments(entries, "task").every((segment) => segment.kind === "task")).toBe(true);
    expect(monthSegments(entries, "task")).toHaveLength(1);
    expect(monthSegments(entries, "meeting").every((segment) => segment.kind === "meeting")).toBe(true);
    expect(monthSegments(entries, "all")).toHaveLength(2);
  });

  it("격자 조각은 상태를 싣지 않는다 — 유형 둘뿐이다", () => {
    const segment = monthSegments([task({ state: "blocked" })], "all")[0] as unknown as Record<string, unknown>;
    expect(Object.keys(segment)).not.toContain("state");
    expect(Object.keys(segment)).not.toContain("approval");
  });
});

describe("spanSegments", () => {
  it("주 뷰 종일 칸은 업무 띠만 받는다 — 시간이 붙은 것은 빠진다", () => {
    const withSchedule = task({ schedules: [{ schedule_id: "s1", on_date: "2027-03-03", starts_at: "10:00", ends_at: "11:30", version: 1 }] });
    const spans = spanSegments([withSchedule, meeting()], "all");
    expect(spans).toHaveLength(1);
    expect(spans[0].time).toBeNull();
  });
});

describe("packLanes", () => {
  it("겹치지 않는 둘은 같은 줄에 앉고 겹치는 둘은 갈라 앉는다", () => {
    const week = weekGridDays("2027-03-03");
    const lanes = packLanes(
      [
        { key: "a", kind: "task", title: "a", from: "2027-02-28", to: "2027-03-01", time: null, taskId: "a" },
        { key: "b", kind: "task", title: "b", from: "2027-03-03", to: "2027-03-04", time: null, taskId: "b" },
        { key: "c", kind: "task", title: "c", from: "2027-03-01", to: "2027-03-03", time: null, taskId: "c" },
      ],
      week,
    );
    expect(lanes).toHaveLength(2);
    expect(lanes[0].map((segment) => segment.key)).toEqual(["a", "b"]);
    expect(lanes[1].map((segment) => segment.key)).toEqual(["c"]);
  });

  it("한 칸 안은 시간순이다 — 종일 띠가 먼저, 시간이 붙은 것은 이른 것부터 (K20)", () => {
    const week = weekGridDays("2027-03-03");
    const lanes = packLanes(
      [
        { key: "meeting:late", kind: "meeting", title: "오후", from: "2027-03-03", to: "2027-03-03", time: "14:00", taskId: null },
        { key: "meeting:early", kind: "meeting", title: "오전", from: "2027-03-03", to: "2027-03-03", time: "09:00", taskId: null },
        { key: "task:bar", kind: "task", title: "띠", from: "2027-03-03", to: "2027-03-03", time: null, taskId: "t1" },
      ],
      week,
    );
    expect(laneSeats(lanes, "2027-03-03").map((segment) => segment?.key)).toEqual(["task:bar", "meeting:early", "meeting:late"]);
  });

  it("종일 칸의 줄 순서는 그대로다 — 시간이 없는 것끼리는 예전 규칙이다 (회귀)", () => {
    const week = weekGridDays("2027-03-03");
    const lanes = packLanes(
      [
        { key: "short", kind: "task", title: "짧다", from: "2027-03-03", to: "2027-03-03", time: null, taskId: "s" },
        { key: "long", kind: "task", title: "길다", from: "2027-03-03", to: "2027-03-05", time: null, taskId: "l" },
      ],
      week,
    );
    // 같은 날 시작이면 «긴 것이 위». 시간이 없으니 K20 의 두 단계는 아무것도 바꾸지 않는다.
    expect(lanes.map((lane) => lane[0].key)).toEqual(["long", "short"]);
  });

  it("그 주에 닿지 않는 것은 줄을 차지하지 않는다", () => {
    const lanes = packLanes(
      [{ key: "z", kind: "task", title: "z", from: "2027-04-01", to: "2027-04-02", time: null, taskId: "z" }],
      weekGridDays("2027-03-03"),
    );
    expect(lanes).toEqual([]);
  });
});

describe("timedBlocks", () => {
  it("업무 배정은 벽시계 그대로, 회의는 서울로 옮긴 분으로 선다", () => {
    const withSchedule = task({ schedules: [{ schedule_id: "s1", on_date: "2027-03-03", starts_at: "10:00", ends_at: "11:30", version: 1 }] });
    const blocks = timedBlocks([withSchedule, meeting()], "all");
    const assigned = blocks.find((block) => block.scheduleId === "s1");
    expect(assigned).toMatchObject({ date: "2027-03-03", startMin: 600, endMin: 690, version: 1, taskId: "t1" });
    const booked = blocks.find((block) => block.kind === "meeting");
    expect(booked).toMatchObject({ date: "2027-03-04", startMin: 600, endMin: 660 });
  });
});

describe("blockingBlocks — 보이는 것과 시간을 막는 것은 같지 않다 (K25·K26·K27)", () => {
  const booked = task({
    schedules: [{ schedule_id: "s1", on_date: "2027-03-03", starts_at: "10:00", ends_at: "11:30", version: 1 }],
  });

  it("내 배정은 전부 막는다", () => {
    expect(blockingBlocks([booked]).map((block) => block.scheduleId)).toEqual(["s1"]);
  });

  it("참석하는 회의는 막는다 — 주최자도 서버에서 `attendee` 로 온다", () => {
    expect(blockingBlocks([meeting({ viewer_relation: "attendee" })]).map((block) => block.kind)).toEqual(["meeting"]);
  });

  /* K25 — 공유받은 회의는 「참고하라」고 공유된 것이지 내가 그 시간에 잡혀 있다는 뜻이 아니다.
     세면 **옆 팀 회의가 내 배정을 막는다.** */
  it("공유받기만 한 회의는 막지 않는다 (K25)", () => {
    expect(blockingBlocks([meeting({ viewer_relation: "shared" })])).toEqual([]);
  });

  /* K26 — 자동 취소가 기록 없이 지난 「예정」을 옮긴다. 세면 그 시간이 영구히 막힌다. */
  it("취소된 회의는 막지 않는다 (K26)", () => {
    expect(blockingBlocks([meeting({ status: "cancelled" })])).toEqual([]);
  });

  it("탭으로 거르지 않는다 — 「업무」 탭을 보고 있어도 회의는 내 시간을 막는다", () => {
    expect(blockingBlocks([booked, meeting()]).map((block) => block.kind).sort()).toEqual(["meeting", "task"]);
  });
});

describe("packBlocks", () => {
  const block = (key: string, startMin: number, endMin: number) => ({
    key,
    kind: "task" as const,
    title: key,
    date: "2027-03-03",
    startMin,
    endMin,
    startLabel: "",
    endLabel: "",
    scheduleId: null,
    version: null,
    taskId: null,
  });

  it("겹치면 나란히 앉는다 — 09:30–12:30 안에 든 10:00–11:00 이 밑에 깔리지 않는다 (K21)", () => {
    const packed = packBlocks([block("long", 570, 750), block("inside", 600, 660)]);
    expect(packed.map((row) => row.lanes)).toEqual([2, 2]);
    expect(new Set(packed.map((row) => row.lane)).size).toBe(2);
  });

  it("겹치지 않으면 칸을 통째로 쓴다 — 반열림이라 경계가 닿는 것은 겹침이 아니다", () => {
    const packed = packBlocks([block("a", 600, 660), block("b", 660, 720)]);
    expect(packed.map((row) => row.lanes)).toEqual([1, 1]);
    expect(packed.map((row) => row.lane)).toEqual([0, 0]);
  });

  it("사슬처럼 이어 겹치는 무리는 같은 줄 수를 나눠 쓴다 — 폭이 들쭉날쭉하지 않다", () => {
    const packed = packBlocks([block("a", 600, 700), block("b", 660, 760), block("c", 720, 820)]);
    expect(packed.map((row) => row.lanes)).toEqual([2, 2, 2]);
    expect(packed.find((row) => row.key === "c")!.lane).toBe(0);
  });

  it("그려지는 최소 높이로 판정한다 — 10분짜리 둘이 겹쳐 보이면 나란히 앉힌다", () => {
    const packed = packBlocks([block("a", 600, 610), block("b", 620, 650)]);
    expect(packed.map((row) => row.lanes)).toEqual([2, 2]);
  });

  it("이미 겹쳐 있는 것을 거르지 않는다 — 그리기이지 막는 것이 아니다 (K21)", () => {
    const packed = packBlocks([block("a", 600, 660), block("b", 600, 660), block("c", 600, 660)]);
    expect(packed).toHaveLength(3);
    expect(packed.map((row) => row.lane).sort()).toEqual([0, 1, 2]);
  });
});

describe("railCards", () => {
  const scope = { from: "2027-02-28", to: "2027-03-06", selected: null };

  it("기간 없는 업무도 카드로 선다 — 레일이 「날짜부터」를 하려면 필요하다", () => {
    const undated = task({ task_id: "t9", start_date: null, due_date: null, span_from: null, span_to: null });
    const cards = railCards([undated], "all", scope);
    expect(cards).toHaveLength(1);
    expect(cards[0].when).toBe("기한 없음");
  });

  it("업무의 시간 배정은 카드가 아니라 업무 카드의 meta 줄로 접힌다", () => {
    const withSchedule = task({ schedules: [{ schedule_id: "s1", on_date: "2027-03-03", starts_at: "10:00", ends_at: "11:30", version: 1 }] });
    const cards = railCards([withSchedule], "all", scope);
    expect(cards).toHaveLength(1);
    expect(cards[0].meta).toContain("3일 10:00");
  });

  it("회의 카드는 주최자 이름을 내고 member id 를 내지 않는다", () => {
    const cards = railCards([meeting({ location: "3층 회의실" })], "meeting", scope);
    expect(cards[0].meta).toContain("민아");
    expect(cards[0].meta.join(" ")).not.toContain("mina");
    expect(cards[0].when).toContain("10:00–11:00");
  });

  /* K19 가 K15 를 뒤집었다 — 앞 판은 여기서 `state` 가 «없음»을 지켰다. */
  it("카드가 상태와 승인을 둘 다 싣는다 — 배지 둘의 재료다 (K19)", () => {
    const submitted = task({ state: "done", approval: "awaiting_review" });
    expect(railCards([submitted], "all", scope)[0]).toMatchObject({ state: "done", approval: "awaiting_review" });
  });

  it("회의 카드는 상태도 승인도 없다 — 회의에는 그런 것이 없다", () => {
    expect(railCards([meeting()], "meeting", scope)[0]).toMatchObject({ state: null, approval: null });
  });

  it("날짜를 고르면 그 날에 걸치는 것만 남고 기한 없는 업무는 빠진다", () => {
    const undated = task({ task_id: "t9", start_date: null, due_date: null, span_from: null, span_to: null });
    const cards = railCards([task(), undated, meeting()], "all", { ...scope, selected: "2027-03-04" });
    expect(cards.map((card) => card.key)).toEqual(["task:t1", "meeting:m1"]);
  });

  it("탭이 레일도 가른다 — 회의 탭에는 업무가 없고 업무 탭에는 회의가 없다", () => {
    const entries: CalendarEntry[] = [task(), meeting()];
    expect(railCards(entries, "meeting", scope).map((card) => card.kind)).toEqual(["meeting"]);
    expect(railCards(entries, "task", scope).map((card) => card.kind)).toEqual(["task"]);
  });
});

describe("seoul 변환", () => {
  it("UTC 자정 근처의 회의가 서울 날짜로 하루 넘어간다", () => {
    expect(seoulDate("2027-03-04T16:00:00+00:00")).toBe("2027-03-05");
    expect(seoulClock("2027-03-04T16:00:00+00:00")).toBe("01:00");
  });
});
