import { describe, expect, it } from "vitest";

import type { ProjectTaskRow } from "../../lib/viewModels";
import {
  GANTT,
  anchorIndex,
  barGeometry,
  depLinks,
  ganttAxis,
  ganttRows,
  indentFor,
  successorIndex,
  summarize,
  taskPercent,
  withFoldedLinks,
} from "./projectModel";

function task(row: Partial<ProjectTaskRow> & { task_id: string }): ProjectTaskRow {
  return {
    title: row.task_id,
    state: "open",
    start_date: null,
    due_date: null,
    parent_task_id: null,
    preceding_task_ids: [],
    assignee: null,
    checklist_progress: { done: 0, total: 0 },
    span_from: null,
    span_to: null,
    overdue_days: null,
    ...row,
  };
}

/** 깊이 N 의 사슬 — 한 줄로 이어진 상위–하위. 전부 같은 날 하루짜리 바다. */
function chain(depth: number): ProjectTaskRow[] {
  return Array.from({ length: depth }, (_, index) =>
    task({
      task_id: `d${index}`,
      parent_task_id: index === 0 ? null : `d${index - 1}`,
      span_from: "2026-09-01",
      span_to: "2026-09-01",
    }),
  );
}

describe("projectModel", () => {
  it("깊이를 자르지 않는다 — 8층이면 행도 여덟이다", () => {
    const rows = ganttRows(chain(8), new Set());
    expect(rows.length).toBe(8);
    expect(rows.map((row) => row.depth)).toEqual([0, 1, 2, 3, 4, 5, 6, 7]);
  });

  it("멈추는 것은 들여쓰기이지 행이 아니다 — 5단에서 값이 고정된다", () => {
    expect([0, 1, 2, 3, 4, 5, 6, 9].map(indentFor)).toEqual([0, 12, 24, 36, 48, 60, 60, 60]);
    expect(GANTT.indent).toBe(12);
    expect(GANTT.maxIndentDepth).toBe(5);
  });

  it("twisty 는 자식이 있는 모든 깊이에 서고 기본은 모두 펼침이다", () => {
    const rows = ganttRows(chain(4), new Set());
    expect(rows.slice(0, 3).every((row) => row.hasChildren && row.expandable && row.open)).toBe(true);
    expect(rows[3].hasChildren).toBe(false);
    expect(rows[3].expandable).toBe(false);
  });

  it("하위가 «전부 기간 없는» 업무면 손잡이를 세우지 않는다 — 접어도 사라질 행이 없다", () => {
    const tasks = [
      task({ task_id: "top", span_from: "2026-09-01", span_to: "2026-09-01" }),
      task({ task_id: "kid1", parent_task_id: "top" }),
      task({ task_id: "kid2", parent_task_id: "top" }),
    ];
    const rows = ganttRows(tasks, new Set());
    expect(rows.map((row) => row.row.task_id)).toEqual(["top"]);
    /* 「하위가 있다」는 여전히 참이다 — 바는 상위 규격이고 「하위 2」도 난다. **손잡이만** 안 선다. */
    expect(rows[0].hasChildren).toBe(true);
    expect(rows[0].childCount).toBe(2);
    expect(rows[0].expandable).toBe(false);
    expect(rows[0].open).toBe(false);

    /* 기간 없는 하위 «아래» 에 기간이 있는 손자가 생기면 접을 것이 생긴다 — 그 손자가 이 행 아래로 선다. */
    const withGrandchild = [
      ...tasks,
      task({ task_id: "grand", parent_task_id: "kid1", span_from: "2026-09-02", span_to: "2026-09-02" }),
    ];
    const deeper = ganttRows(withGrandchild, new Set());
    expect(deeper.map((row) => row.row.task_id)).toEqual(["top", "grand"]);
    expect(deeper[0].expandable).toBe(true);
    expect(ganttRows(withGrandchild, new Set(["top"])).map((row) => row.row.task_id)).toEqual(["top"]);
  });

  it("접힌 가지의 선을 접힌 부모 바에 끌어붙인다 — 자리를 못 찾은 선은 셈으로 남는다", () => {
    const tasks = [
      ...chain(3),
      task({ task_id: "other", span_from: "2026-09-02", span_to: "2026-09-02", preceding_task_ids: ["d2"] }),
      /* 기간이 없어 간트에 못 서는 업무와 그것에 걸린 선 — 사라지는 대신 `unplaced` 로 남는다. */
      task({ task_id: "nodate", preceding_task_ids: ["other"] }),
    ];

    const open = ganttRows(tasks, new Set());
    const openLinks = depLinks(tasks, anchorIndex(tasks, open));
    expect(openLinks.length).toBe(2);
    expect(openLinks.filter((link) => link.unplaced).length).toBe(1);
    expect(openLinks.find((link) => link.fromTaskId === "d2")?.toRowId).toBe("other");

    /* d0 을 접으면 d2 의 자리가 사라진다 — 시안의 `if (!a || !b) return` 이 도는 자리다. */
    const folded = ganttRows(tasks, new Set(["d0"]));
    expect(folded.map((row) => row.row.task_id)).toEqual(["d0", "other"]);
    const foldedLinks = depLinks(tasks, anchorIndex(tasks, folded));
    /* 선의 개수가 줄지 않았고, d2 의 선은 접힌 부모 d0 에 붙었다. */
    expect(foldedLinks.length).toBe(2);
    expect(foldedLinks.find((link) => link.fromTaskId === "d2")?.fromRowId).toBe("d0");
  });

  it("가지 안쪽으로 접힌 선은 그 행이 건수로 들고 있는다", () => {
    const tasks = [
      ...chain(3),
      task({ task_id: "d1b", parent_task_id: "d0", span_from: "2026-09-02", span_to: "2026-09-02", preceding_task_ids: ["d2"] }),
    ];
    const folded = ganttRows(tasks, new Set(["d0"]));
    const links = depLinks(tasks, anchorIndex(tasks, folded));
    const rows = withFoldedLinks(folded, links);
    expect(links.length).toBe(1);
    expect(links[0].internal).toBe(true);
    expect(rows.find((row) => row.row.task_id === "d0")?.foldedLinks).toBe(1);
  });

  it("후행은 선행 배열을 뒤집어 만든다 — 서버에 묻지 않는다", () => {
    const tasks = [
      task({ task_id: "a" }),
      task({ task_id: "b", preceding_task_ids: ["a"] }),
      task({ task_id: "c", preceding_task_ids: ["a"] }),
    ];
    expect(successorIndex(tasks).get("a")).toEqual(["b", "c"]);
    expect(successorIndex(tasks).get("b")).toBeUndefined();
  });

  it("축과 막대는 서버가 접어 준 span 만 읽는다 — 화면이 정규화를 다시 하지 않는다", () => {
    const tasks = [
      /* 마감만 있는 업무: 서버가 이미 그 날 하루로 접었다. */
      task({ task_id: "due", start_date: null, due_date: "2026-09-03", span_from: "2026-09-03", span_to: "2026-09-03" }),
      /* 뒤집힌 기간: 원값은 그대로 오고 span 만 [min, max] 다. */
      task({ task_id: "flip", start_date: "2026-09-06", due_date: "2026-09-04", span_from: "2026-09-04", span_to: "2026-09-06" }),
    ];
    const axis = ganttAxis(tasks);
    expect(axis).not.toBeNull();
    expect(axis?.from).toBe("2026-09-03");
    expect(axis?.to).toBe("2026-09-06");
    expect(axis?.days.length).toBe(4);

    expect(barGeometry(tasks[0], axis!)).toEqual({ left: GANTT.label, width: GANTT.day });
    expect(barGeometry(tasks[1], axis!)).toEqual({ left: GANTT.label + GANTT.day, width: GANTT.day * 3 });
    /* 기간이 없으면 막대가 없다 — 간트에 안 선다. */
    expect(barGeometry(task({ task_id: "none" }), axis!)).toBeNull();
    expect(ganttAxis([task({ task_id: "none" })])).toBeNull();
  });

  it("바의 % — 체크리스트가 정본이고, 없으면 그리지 않는다", () => {
    expect(taskPercent(task({ task_id: "a", checklist_progress: { done: 2, total: 5 } }))).toBe(40);
    /* **0% 가 아니다** — 「아무것도 안 한 일」이라는 허위를 만들지 않는다. */
    expect(taskPercent(task({ task_id: "b", checklist_progress: { done: 0, total: 0 } }))).toBeNull();
    expect(taskPercent(task({ task_id: "c", checklist_progress: null }))).toBeNull();
    expect(taskPercent(task({ task_id: "d", state: "done" }))).toBe(100);
    expect(taskPercent(task({ task_id: "e", state: "cancelled", checklist_progress: { done: 4, total: 4 } }))).toBeNull();
  });

  it("요약의 모수는 하나다 — 취소 2건 · 완료 8건이면 전체 8 · 완료 8 · 100%", () => {
    const tasks = [
      ...Array.from({ length: 8 }, (_, index) => task({ task_id: `done-${index}`, state: "done" as const })),
      task({ task_id: "x1", state: "cancelled" }),
      task({ task_id: "x2", state: "cancelled" }),
    ];
    expect(summarize(tasks)).toEqual({ total: 8, inProgress: 0, overdue: 0, done: 8, percent: 100 });
  });

  it("「지연」은 서버가 기한 경과일을 낸 업무만 센다", () => {
    const tasks = [
      task({ task_id: "late", state: "in_progress", overdue_days: 2 }),
      /* 끝난 업무에는 서버가 `null` 을 낸다 — 기한을 넘겼어도 화면이 다시 판정하지 않는다. */
      task({ task_id: "closed", state: "done", due_date: "2020-01-01", overdue_days: null }),
    ];
    expect(summarize(tasks).overdue).toBe(1);
  });

  it("프로젝트 밖 상위를 가리키는 업무도 사라지지 않는다 — 뿌리로 선다", () => {
    const tasks = [task({ task_id: "orphan", parent_task_id: "somewhere-else", span_from: "2026-09-01", span_to: "2026-09-01" })];
    const rows = ganttRows(tasks, new Set());
    expect(rows.length).toBe(1);
    expect(rows[0].depth).toBe(0);
  });

  it("기간 없는 상위의 자식은 그 자리로 올라온다 — 깊이를 한 단 더 내리지 않는다", () => {
    const tasks = [
      task({ task_id: "ghost" }),
      task({ task_id: "kid", parent_task_id: "ghost", span_from: "2026-09-01", span_to: "2026-09-01" }),
    ];
    const rows = ganttRows(tasks, new Set());
    expect(rows.map((row) => [row.row.task_id, row.depth])).toEqual([["kid", 0]]);
  });
});
