import { describe, expect, it } from "vitest";

import { calendarDeny, calendarScreen, formatDate, projectScreen, proposalFieldLabel, taskDateLabel, taskDetail, workChipLabel } from "./labels";

/**
 * 업무 날짜 표기 — 이름 「마감일」 · 형식 `2026/10/06` (SPEC-001 U-17 · WORK-009 2b-2).
 */
describe("formatDate — 업무 날짜 형식은 하나다", () => {
  it("날짜는 `/` 로 잇는다", () => {
    expect(formatDate("2026-10-06")).toBe("2026/10/06");
  });

  it("비어 있으면 「—」", () => {
    expect(formatDate(null)).toBe("—");
    expect(formatDate("")).toBe("—");
  });

  it("시각은 서울 날짜로 옮긴다 — UTC 15:00 이후는 다음 날이다", () => {
    expect(formatDate("2026-10-05T15:00:00Z")).toBe("2026/10/06");
    expect(formatDate("2026-10-05T14:59:59Z")).toBe("2026/10/05");
    expect(formatDate("2026-10-05T23:30:00+09:00")).toBe("2026/10/05");
  });

  it("날짜가 아닌 글자는 그대로 둔다", () => {
    expect(formatDate("다음 주")).toBe("다음 주");
  });
});

describe("「마감일」 — `due_date` 의 화면 이름", () => {
  it("상세·제안·칩·배지·빈 값이 같은 이름을 쓴다", () => {
    expect(taskDetail.metaDue).toBe("마감일");
    expect(proposalFieldLabel.due_date).toBe("마감일");
    expect(workChipLabel.overdue).toBe("마감일 지남");
    expect(taskDateLabel.overdue).toBe("마감일 초과");
    expect(calendarScreen.undated).toBe("마감일 없음");
    expect(projectScreen.undated).toBe("마감일 없음");
  });

  it("마감일만 있는 카드는 「마감일 2026/10/06」이다 — 「… 마감」이 아니다", () => {
    expect(calendarScreen.dueOnly("2026-10-06")).toBe("마감일 2026/10/06");
    expect(projectScreen.dueOnly("2026-10-06")).toBe("마감일 2026/10/06");
  });

  it("상세의 날짜 넷 이름과 순서", () => {
    expect([taskDetail.metaPlannedStart, taskDetail.metaActualStart, taskDetail.metaActualEnd, taskDetail.metaDue]).toEqual([
      "시작 예정일",
      "실제 시작일",
      "실제 종료일",
      "마감일",
    ]);
  });

  it("라벨이 붙는 업무 날짜는 「라벨 값」 어순 하나다 — 「시작 2026/10/01」 · 「마감일 2026/10/06」", () => {
    expect(projectScreen.startOnly("2026-10-01")).toBe("시작 2026/10/01");
    expect(projectScreen.dueOnly("2026-10-06")).toBe("마감일 2026/10/06");
  });

  it("만들기 창의 시작일 이름도 같은 상수다", () => {
    expect(taskDateLabel.start).toBe("시작일");
  });

  it("캘린더 거절 문구의 날짜도 같은 형식이다", () => {
    expect(calendarDeny.outOfRange("2026-09-02", "2026-09-04")).toContain("2026/09/02~2026/09/04");
  });
});
