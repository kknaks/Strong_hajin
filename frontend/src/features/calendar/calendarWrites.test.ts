import { describe, expect, it } from "vitest";

import type { CalendarTaskRow } from "../../lib/viewModels";
import {
  defaultSlot,
  denyMessage,
  dropGuard,
  moveTaskDates,
  previewResize,
  releaseNotice,
  resizeTaskDates,
  scheduleKey,
  slotGuard,
  snapClock,
  spanOf,
} from "./calendarWrites";

const task = (over: Partial<CalendarTaskRow> = {}): CalendarTaskRow => ({
  kind: "task",
  task_id: "t1",
  title: "업무",
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

/** 시작 9/6 · 마감 9/4 — 서버가 실제로 내려보내는 모양이다(②·③ 자리가 검증을 안 지난다). */
const flipped = task({ start_date: "2027-09-06", due_date: "2027-09-04", span_from: "2027-09-04", span_to: "2027-09-06" });

describe("R1 — 날짜 칸 드롭은 기간을 옮긴다", () => {
  it("둘 다 있으면 **span 길이**를 유지한 채 옮긴다 — raw 차이가 아니다 (WARN-A)", () => {
    expect(moveTaskDates(task(), "2027-04-10")).toEqual({ start_date: "2027-04-10", due_date: "2027-04-14" });
  });

  it("뒤집힌 업무도 span 길이(2일)로 옮겨지고, 옮긴 결과는 뒤집히지 않는다", () => {
    expect(moveTaskDates(flipped, "2027-04-10")).toEqual({ start_date: "2027-04-10", due_date: "2027-04-12" });
  });

  it("마감만 있으면 그 날 하루로 옮긴다 — 시작일을 지어내지 않는다", () => {
    const dueOnly = task({ start_date: null, span_from: "2027-03-05", span_to: "2027-03-05" });
    expect(moveTaskDates(dueOnly, "2027-04-10")).toEqual({ due_date: "2027-04-10" });
  });

  it("시작만 있으면 그 날로 옮긴다", () => {
    const startOnly = task({ due_date: null, span_from: "2027-03-01", span_to: "2027-03-01" });
    expect(moveTaskDates(startOnly, "2027-04-10")).toEqual({ start_date: "2027-04-10" });
  });

  it("둘 다 없으면 그 날 하루짜리 기간이 생긴다 (R2)", () => {
    const undated = task({ start_date: null, due_date: null, span_from: null, span_to: null });
    expect(moveTaskDates(undated, "2027-04-10")).toEqual({ start_date: "2027-04-10", due_date: "2027-04-10" });
  });
});

describe("R3·R4 — 손잡이의 정체는 «필드»다 (WARN-A)", () => {
  it("뒤집힌 업무에서도 start 손잡이는 start_date 를 바꾼다", () => {
    expect(resizeTaskDates(flipped, "start", "2027-09-03")).toEqual({ patch: { start_date: "2027-09-03" } });
  });

  it("뒤집힌 업무에서도 end 손잡이는 due_date 를 바꾼다", () => {
    expect(resizeTaskDates(flipped, "end", "2027-09-08")).toEqual({ patch: { due_date: "2027-09-08" } });
  });

  it("R4 — 마감만 있던 업무가 start 손잡이로 기간을 갖는다", () => {
    const dueOnly = task({ start_date: null, span_from: "2027-03-05", span_to: "2027-03-05" });
    expect(resizeTaskDates(dueOnly, "start", "2027-03-02")).toEqual({ patch: { start_date: "2027-03-02" } });
  });

  it("역전은 조용히 접지 않고 **말한다** — 시작이 마감을 넘으면 거절 문구가 난다", () => {
    expect(resizeTaskDates(task(), "start", "2027-03-09")).toEqual({ deny: "시작일은 마감일보다 뒤일 수 없습니다." });
    expect(resizeTaskDates(task(), "end", "2027-02-20")).toEqual({ deny: "시작일은 마감일보다 뒤일 수 없습니다." });
  });

  it("맞은편이 비어 있으면 넘을 것이 없다 — 거절하지 않는다", () => {
    const startOnly = task({ due_date: null, span_from: "2027-03-01", span_to: "2027-03-01" });
    expect(resizeTaskDates(startOnly, "start", "2027-12-31")).toEqual({ patch: { start_date: "2027-12-31" } });
  });
});

describe("R5 가드 — 조용한 거절이 없다", () => {
  it("기간 밖이면 **정규화 구간**을 문구에 넣는다 — 뒤집힌 업무면 [min, max] 다", () => {
    expect(slotGuard(flipped, "2027-09-08", true)).toBe(
      "이 업무의 기간(2027/09/04~2027/09/06) 안에만 시간을 배정할 수 있습니다.",
    );
  });

  it("기간 안이면 통과한다 — 뒤집힌 업무도 start_date 로 막지 않는다", () => {
    expect(slotGuard(flipped, "2027-09-05", true)).toBeNull();
  });

  it("기한이 없으면 「날짜부터」라고 말한다 (R2)", () => {
    const undated = task({ start_date: null, due_date: null, span_from: null, span_to: null });
    expect(slotGuard(undated, "2027-03-03", true)).toBe("먼저 업무 기간을 정해 주세요. 기간이 있어야 시간을 배정할 수 있습니다.");
  });

  it("내 업무를 다룰 수 없으면 그것도 말한다", () => {
    expect(slotGuard(task(), "2027-03-03", false)).toBe("내가 맡은 업무에만 시간을 배정할 수 있습니다.");
  });

  it("날짜 칸 드롭에는 기간 가드가 없다 — 기한 없는 업무도 떨어진다(R2 가 거기서 생긴다)", () => {
    const undated = task({ start_date: null, due_date: null, span_from: null, span_to: null });
    expect(dropGuard(undated, true)).toBeNull();
    expect(dropGuard(undated, false)).toBe("내가 맡은 업무에만 시간을 배정할 수 있습니다.");
  });
});

describe("시간 눈금", () => {
  it("30분 눈금으로 접고 하루를 넘지 않는다", () => {
    expect(snapClock(0)).toBe("00:00");
    expect(snapClock(614)).toBe("10:00");
    expect(snapClock(616)).toBe("10:30");
    expect(snapClock(99_999)).toBe("23:30");
  });

  it("기본 길이는 한 시간이다", () => {
    expect(defaultSlot(600)).toEqual({ starts_at: "10:00", ends_at: "11:00" });
  });

  /* 서버가 받는 것은 `datetime.time` 이라 `24:00` 이 없다 — 하루의 마지막 순간은 23:59 다.
     시안은 `wkClock(min + 60)` 로 「24:00」 을 만들지만, 그대로 보내면 422 다. */
  it("하루 끝에 떨어뜨려도 24:00 을 만들지 않는다 — 23:59 에 붙는다", () => {
    expect(defaultSlot(24 * 60)).toEqual({ starts_at: "23:30", ends_at: "23:59" });
    expect(defaultSlot(23 * 60 + 10)).toEqual({ starts_at: "23:00", ends_at: "23:59" });
  });
});

describe("멱등 키 — 연타가 409 가 아니라 영수증이 되게", () => {
  it("같은 내용의 재전송은 같은 키를 쓴다", () => {
    const keys = new Map<string, string>();
    const first = scheduleKey(keys, "t1", { on_date: "2027-03-03", starts_at: "10:00", ends_at: "11:00" });
    const again = scheduleKey(keys, "t1", { on_date: "2027-03-03", starts_at: "10:00", ends_at: "11:00" });
    expect(again).toBe(first);
  });

  it("다른 내용이면 다른 키다 — 다음 배정이 앞엣것의 영수증이 되면 안 된다", () => {
    const keys = new Map<string, string>();
    const ten = scheduleKey(keys, "t1", { on_date: "2027-03-03", starts_at: "10:00", ends_at: "11:00" });
    const two = scheduleKey(keys, "t1", { on_date: "2027-03-03", starts_at: "14:00", ends_at: "15:00" });
    expect(two).not.toBe(ten);
  });
});

describe("거절 문구 — 상태 코드 + 어떤 명령을 불렀는지로 가른다", () => {
  const span = { from: "2027-09-04", to: "2027-09-06" };

  it("403 은 어느 명령에서나 «내 업무만»이다", () => {
    expect(denyMessage("schedule_create", 403, span)).toBe("내가 맡은 업무에만 시간을 배정할 수 있습니다.");
    expect(denyMessage("task_dates", 403, span)).toBe("내가 맡은 업무에만 시간을 배정할 수 있습니다.");
  });

  it("422 는 명령마다 다르다 — 서버의 영문 문구를 그대로 내보내지 않는다", () => {
    expect(denyMessage("task_dates", 422, span)).toBe("시작일은 마감일보다 뒤일 수 없습니다.");
    expect(denyMessage("schedule_create", 422, span)).toBe(
      "이 업무의 기간(2027/09/04~2027/09/06) 안에만 시간을 배정할 수 있습니다.",
    );
    expect(denyMessage("schedule_create", 422, null)).toBe("먼저 업무 기간을 정해 주세요. 기간이 있어야 시간을 배정할 수 있습니다.");
    expect(denyMessage("schedule_update", 422, span)).toBe("종료 시각은 시작 시각보다 뒤여야 합니다.");
  });

  it("409 도 명령마다 다르다 — 생성만 «그 날이 방금 찼다»이고 나머지는 회차 충돌이다", () => {
    expect(denyMessage("schedule_create", 409, span)).toBe("이 날의 시간 배정이 방금 바뀌었습니다. 새로고침 후 다시 시도해 주세요.");
    expect(denyMessage("schedule_update", 409, span)).toBe("다른 곳에서 먼저 바뀌었습니다. 새로고침 후 다시 시도해 주세요.");
    expect(denyMessage("task_dates", 409, span)).toBe("다른 곳에서 먼저 바뀌었습니다. 새로고침 후 다시 시도해 주세요.");
  });

  it("404 는 없는 것과 못 읽는 것을 같은 말로 답한다", () => {
    expect(denyMessage("task_dates", 404, span)).toBe("그 업무를 더는 찾을 수 없습니다. 새로고침 후 다시 시도해 주세요.");
  });

  it("모르는 코드에는 명령별 기본 문구를 낸다 — 빈손으로 두지 않는다", () => {
    expect(denyMessage("task_dates", 500, span)).toBe("업무 기간을 바꾸지 못했습니다.");
    expect(denyMessage("schedule_update", 500, span)).toBe("시간 배정을 저장하지 못했습니다.");
  });
});

describe("K3 알림", () => {
  it("닫힌 건수가 있으면 말하고", () => {
    expect(releaseNotice({ released_count: 2, reason: "out_of_range" })).toBe("2건의 시간 배정이 기간 밖이라 해제되었습니다.");
  });

  it("0 건이면 **아무 말도 하지 않는다**", () => {
    expect(releaseNotice({ released_count: 0, reason: null })).toBeNull();
  });

  it("칸 자체가 없는 응답에서도 아무 말도 하지 않는다 — /block·/resume 은 이 칸을 싣지 않는다", () => {
    expect(releaseNotice(undefined)).toBeNull();
    expect(releaseNotice(null)).toBeNull();
  });
});

describe("spanOf", () => {
  it("정규화 구간을 그대로 낸다 — 화면이 다시 계산하지 않는다 (K14)", () => {
    expect(spanOf(flipped)).toEqual({ from: "2027-09-04", to: "2027-09-06" });
    expect(spanOf(task({ span_from: null, span_to: null }))).toBeNull();
  });
});

describe("previewResize — 끄는 동안은 그림만 바뀐다", () => {
  const bar = { key: "task:t1", from: "2027-03-01", to: "2027-03-05", time: null, taskId: "t1" };
  const chip = { key: "schedule:s1", from: "2027-03-03", to: "2027-03-03", time: "10:00", taskId: "t1" };

  it("잡은 끝만 포인터가 지나는 칸까지 당긴다", () => {
    expect(previewResize([bar], { taskId: "t1", edge: "end", date: "2027-03-08" })[0].to).toBe("2027-03-08");
    expect(previewResize([bar], { taskId: "t1", edge: "start", date: "2027-02-25" })[0].from).toBe("2027-02-25");
  });

  it("같은 업무의 **시간 배정 칩**은 건드리지 않는다 — 손잡이는 날짜 띠의 것이다", () => {
    expect(previewResize([chip], { taskId: "t1", edge: "end", date: "2027-03-08" })[0]).toBe(chip);
  });

  it("맞은편을 넘어서는 그리지 않는다 — 다만 그림이 접었다고 거절이 사라지지는 않는다", () => {
    expect(previewResize([bar], { taskId: "t1", edge: "start", date: "2027-03-09" })[0].from).toBe("2027-03-01");
  });

  it("아직 어느 칸도 지나지 않았으면 그대로다", () => {
    expect(previewResize([bar], { taskId: "t1", edge: "end", date: null })[0]).toBe(bar);
  });
});

describe("좌표가 없는 이벤트에서도 「NaN:NaN」 을 만들지 않는다", () => {
  it("유한하지 않은 분은 0 시로 읽는다 — 그대로 보내면 422 다", () => {
    expect(snapClock(Number.NaN)).toBe("00:00");
    expect(defaultSlot(Number.NaN)).toEqual({ starts_at: "00:00", ends_at: "01:00" });
  });
});
