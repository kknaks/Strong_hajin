import { addDays, dayDifference, formatDate, personName, projectScreen } from "../../lib/labels";
import type { ProjectTaskRow, TaskState } from "../../lib/viewModels";

/**
 * 「프로젝트」 화면이 그리기 위해 하는 **계산 전부** — 부품이 아니라 값만 다룬다.
 * 선례는 `features/work/workRows.ts` · `features/calendar/calendarModel.ts` 다.
 *
 * 이 파일이 지키는 규율 셋 (SPEC-005 §2 · DEC-004):
 *
 * 1. **정규화를 다시 하지 않는다.** 간트 막대의 양 끝은 서버가 접어 준 `span_from`·`span_to` 뿐이다.
 *    한쪽 날짜만 있는 업무는 서버가 이미 그 날 하루로, 뒤집힌 기간은 `[min, max]` 로 냈다.
 *    원값 `start_date`·`due_date` 는 **말**(카드의 「…마감」)에만 닿는다.
 * 2. **「오늘」을 판정하지 않는다.** 지연은 서버가 `overdue_days` 를 낸 업무만 센다. 오늘 선(`now`)의
 *    날짜는 부르는 쪽이 넘긴다 — 이 파일에 시계가 없다.
 * 3. **깊이를 자르지 않는다** (D-16). 트리는 재귀이고 손자·증손자도 자기 행과 자기 바를 갖는다.
 *    자르는 쪽이 오히려 추가로 하는 일이다 — 서버가 평평하게 전부 싣기 때문이다.
 */

/** 시안 `projects.v1.jsx:5` 의 기하 상수. **들여쓰기 두 값만 우리 것**이다 (D-18). */
export const GANTT = {
  /** 하루 폭 */
  day: 34,
  /** 행 높이 */
  row: 40,
  /** 이름 칸 폭 */
  label: 200,
  /** 깊이당 들여쓰기 */
  indent: 12,
  /** 들여쓰기가 멈추는 깊이. **멈추는 것은 들여쓰기이고 행이 아니다** — 그 아래도 행은 계속 선다. */
  maxIndentDepth: 5,
  /**
   * 첫 진입에 **오늘 앞으로 남겨 두는 날 수** (D-36). 0 이면 오늘이 고정된 이름 칸에 딱 붙어
   * 「어제까지 무엇이 있었나」가 잘린다. 기하값이라 여기 둔다 — 좌표 계산은 이 값을 쓰지 않는다.
   */
  openLead: 2,
} as const;

/** 시안 간트 바의 status 어휘 (`projects.css:52-59`) + 우리가 더한 `cancelled` 한 종. */
export type GanttBarStatus = "progress" | "blocked" | "done" | "not-started" | "cancelled";

const BAR_STATUS: Record<TaskState, GanttBarStatus> = {
  in_progress: "progress",
  blocked: "blocked",
  done: "done",
  cancelled: "cancelled",
  open: "not-started",
};

/**
 * 상태 하나가 바 색을 고른다. **모르는 값이 와도 떨어뜨리지 않는다** — 계약 밖의 값이면
 * 「아직 시작 안 한 일」의 중립 규격으로 서고, 라벨은 `taskStateBadge()` 가 원문을 그대로 낸다.
 */
export function barStatus(state: TaskState): GanttBarStatus {
  return BAR_STATUS[state] ?? "not-started";
}

/** 깊이당 12px, 5단에서 멈춘다 (D-18). */
export function indentFor(depth: number): number {
  return Math.min(depth, GANTT.maxIndentDepth) * GANTT.indent;
}

/**
 * 바 안의 % — **체크리스트가 정본이다** (D-01~D-03).
 *
 * - 체크리스트가 있으면 완료 ÷ 전체.
 * - **없으면 `null`** — fill 도 % 텍스트도 그리지 않는다. **0% 가 아니다**: 0% 로 그리면
 *   「아무것도 안 한 일」이라는 허위가 생긴다.
 * - 외부 상태 `done` 은 체크리스트가 없어도 100%.
 * - `cancelled` 는 % 가 없다 — 끝나지 않은 채 멈춘 일에 완결도를 붙이지 않는다 (D-23).
 */
export function taskPercent(row: ProjectTaskRow): number | null {
  if (row.state === "cancelled") return null;
  const progress = row.checklist_progress;
  if (progress && progress.total > 0) return Math.round((progress.done / progress.total) * 100);
  if (row.state === "done") return 100;
  return null;
}

/* ---------------- 트리 ---------------- */

export type ProjectTaskNode = {
  row: ProjectTaskRow;
  depth: number;
  children: ProjectTaskNode[];
};

/**
 * 평평한 `tasks[]` → **깊이 제한 없는** 재귀 트리 (D-16).
 *
 * 이 프로젝트 밖에 있는 상위를 가리키는 업무(읽을 수 없는 상위 · 다른 프로젝트의 상위)는 **뿌리로 선다** —
 * 감추면 그 업무가 화면에서 사라진다. 상위 사슬이 자기를 도로 가리키는 자료(고리)도 한 번만 세운다.
 */
export function buildTaskTree(tasks: readonly ProjectTaskRow[]): ProjectTaskNode[] {
  const byId = new Map(tasks.map((task) => [task.task_id, task]));
  const childrenOf = new Map<string | null, ProjectTaskRow[]>();
  for (const task of tasks) {
    const parent = task.parent_task_id && byId.has(task.parent_task_id) ? task.parent_task_id : null;
    const bucket = childrenOf.get(parent);
    if (bucket) bucket.push(task);
    else childrenOf.set(parent, [task]);
  }
  const seen = new Set<string>();
  const build = (row: ProjectTaskRow, depth: number): ProjectTaskNode => {
    seen.add(row.task_id);
    const kids = (childrenOf.get(row.task_id) ?? []).filter((kid) => !seen.has(kid.task_id));
    return { row, depth, children: kids.map((kid) => build(kid, depth + 1)) };
  };
  return (childrenOf.get(null) ?? []).map((root) => build(root, 0));
}

/** 상위 사슬 — 트리와 같은 규칙(프로젝트 밖 상위는 없는 것으로 친다). */
export function parentIndex(tasks: readonly ProjectTaskRow[]): Map<string, string> {
  const byId = new Set(tasks.map((task) => task.task_id));
  const index = new Map<string, string>();
  for (const task of tasks) {
    if (task.parent_task_id && byId.has(task.parent_task_id)) index.set(task.task_id, task.parent_task_id);
  }
  return index;
}

/** 직속 하위의 수 — **화면이 센다.** 서버는 이 값을 내지 않는다. */
export function childCounts(tasks: readonly ProjectTaskRow[]): Map<string, number> {
  const counts = new Map<string, number>();
  const parents = parentIndex(tasks);
  for (const [, parent] of parents) counts.set(parent, (counts.get(parent) ?? 0) + 1);
  return counts;
}

/**
 * **후행은 클라이언트가 역산한다** (D-07) — 그 프로젝트 업무 전부의 선행 배열을 뒤집는다.
 * 후행을 서버에 묻는 호출은 **0건**이다.
 */
export function successorIndex(tasks: readonly ProjectTaskRow[]): Map<string, string[]> {
  const index = new Map<string, string[]>();
  for (const task of tasks) {
    for (const predecessor of task.preceding_task_ids ?? []) {
      const bucket = index.get(predecessor);
      if (bucket) bucket.push(task.task_id);
      else index.set(predecessor, [task.task_id]);
    }
  }
  return index;
}

/* ---------------- 요약 스트립 ---------------- */

export type ProjectSummary = {
  /** 모수 — **취소를 뺀 업무**. 1번 칸과 5번 분모가 **같은 수**다 (D-04). */
  total: number;
  inProgress: number;
  /** **서버가 기한 경과일을 낸 업무의 수.** 화면이 「오늘」을 다시 판정하지 않는다 (I-4). */
  overdue: number;
  done: number;
  /** 완료 ÷ 전체. **업무별 % 의 평균이 아니다.** 셀 것이 없으면 `null` — 0% 라고 말하지 않는다. */
  percent: number | null;
};

export function summarize(tasks: readonly ProjectTaskRow[]): ProjectSummary {
  const counted = tasks.filter((task) => task.state !== "cancelled");
  const done = counted.filter((task) => task.state === "done").length;
  return {
    total: counted.length,
    inProgress: counted.filter((task) => task.state === "in_progress").length,
    overdue: counted.filter((task) => task.overdue_days !== null && task.overdue_days !== undefined).length,
    done,
    percent: counted.length === 0 ? null : Math.round((done / counted.length) * 100),
  };
}

/* ---------------- 좌 레일 카드 ---------------- */

export type ProjectRailCard = {
  id: string;
  title: string;
  state: TaskState;
  /** 카드 meta 의 첫 칸 — 기간. 원값이 말하는 자리다(「…마감」·「기한 없음」). */
  when: string;
  /** 뒤따르는 meta 칸들. **「분류」를 싣지 않는다** (D-08). 담당이 없으면 **빈 배열**이다. */
  meta: string[];
};

/** 기간의 말 — **원값이 말한다**(`span_*` 은 막대의 것이다). */
export function taskWhen(row: ProjectTaskRow): string {
  if (row.start_date && row.due_date) {
    return row.start_date === row.due_date
      ? formatDate(row.start_date)
      : `${formatDate(row.start_date)} ~ ${formatDate(row.due_date)}`;
  }
  if (row.due_date) return projectScreen.dueOnly(row.due_date);
  if (row.start_date) return projectScreen.startOnly(row.start_date);
  return projectScreen.undated;
}

export function railCards(tasks: readonly ProjectTaskRow[]): ProjectRailCard[] {
  return tasks.map((row) => ({
    id: row.task_id,
    title: row.title,
    state: row.state,
    when: taskWhen(row),
    /* 담당이 없으면 그 칸을 «비운다» — 「미정」을 지어내지 않는다 (I-1). */
    meta: row.assignee ? [personName(row.assignee.display_name)] : [],
  }));
}

/* ---------------- 간트 ---------------- */

/**
 * 간트에 서는 업무 — **정규화 결과가 있는 것만** (D-09).
 * 기간이 없으면 간트에 안 서고 좌 레일에만 선다.
 */
function hasSpan(row: ProjectTaskRow): boolean {
  return Boolean(row.span_from && row.span_to);
}

export type GanttFlatRow = {
  row: ProjectTaskRow;
  depth: number;
  /** 트리에 하위가 있다 — **바 규격**(상위는 14px · % 텍스트 없음)과 「하위 N」이 이것을 본다 (D-03). */
  hasChildren: boolean;
  /**
   * **접으면 실제로 사라질 행이 있다.** 하위가 «전부 기간 없는» 업무면 거짓이다 — 그런 가지는
   * 접어도 움직일 것이 없으므로 twisty 를 세우지 않는다. 「하위가 있다」와 다른 말이다.
   */
  expandable: boolean;
  open: boolean;
  childCount: number;
  /** 이 행이 접어 삼킨 **가지 안쪽 의존선**의 수. 0 이면 삼킨 것이 없다 (D-17). */
  foldedLinks: number;
};

/**
 * 축 — **데이터가 정한다.** 시안의 `{days: 30, today: 17}` 은 목데이터 하드코딩이고 우리에게는 없다.
 * 간트에 설 업무가 하나도 없으면 `null` 이고, 그때 화면은 축 대신 빈 상태를 낸다.
 */
export type GanttAxis = { from: string; to: string; days: string[] };

export function ganttAxis(tasks: readonly ProjectTaskRow[]): GanttAxis | null {
  const placed = tasks.filter(hasSpan);
  if (placed.length === 0) return null;
  let from = placed[0].span_from as string;
  let to = placed[0].span_to as string;
  for (const task of placed) {
    if ((task.span_from as string) < from) from = task.span_from as string;
    if ((task.span_to as string) > to) to = task.span_to as string;
  }
  const length = dayDifference(from, to) + 1;
  return { from, to, days: Array.from({ length }, (_, offset) => addDays(from, offset)) };
}

/** 막대의 왼쪽 x 와 폭. 축 밖으로 나가지 않는다 — 축이 데이터에서 나오므로 그럴 일도 없다. */
export function barGeometry(row: ProjectTaskRow, axis: GanttAxis): { left: number; width: number } | null {
  if (!hasSpan(row)) return null;
  const start = dayDifference(axis.from, row.span_from as string);
  const end = dayDifference(axis.from, row.span_to as string);
  return { left: GANTT.label + start * GANTT.day, width: (end - start + 1) * GANTT.day };
}

/**
 * 펼쳐진 것만 자리를 갖는다 — 상위 다음에 하위가 뒤따른다.
 *
 * **간트에 안 서는 업무(기간 없음)는 행이 되지 않지만, 그 자식은 사라지지 않는다** —
 * 가장 가까운 «간트에 선» 조상 아래로 붙어 깊이를 유지한다. 그래야 손자의 좌표가 남고
 * 그 손자에 걸린 의존선이 조용히 사라지지 않는다.
 */
/**
 * 이 마디 **아래에 간트에 설 업무가 하나라도 있는가** — 접었을 때 «사라질 행이 있는가» 와 같은 말이다.
 * 하위가 전부 기간 없는 업무면 거짓이다: 그 가지는 접어도 화면이 움직이지 않는다.
 */
function hasPlacedDescendant(node: ProjectTaskNode): boolean {
  return node.children.some((kid) => hasSpan(kid.row) || hasPlacedDescendant(kid));
}

export function ganttRows(tasks: readonly ProjectTaskRow[], collapsed: ReadonlySet<string>): GanttFlatRow[] {
  const counts = childCounts(tasks);
  const flat: GanttFlatRow[] = [];
  const walk = (nodes: ProjectTaskNode[], depth: number, hidden: boolean) => {
    for (const node of nodes) {
      const placed = hasSpan(node.row);
      const visible = placed && !hidden;
      if (visible) {
        const hasChildren = node.children.length > 0;
        /* 손잡이는 «움직일 것이 있을 때만» 선다 — 죽은 twisty 를 만들지 않는다. */
        const expandable = hasPlacedDescendant(node);
        flat.push({
          row: node.row,
          depth,
          hasChildren,
          expandable,
          open: expandable && !collapsed.has(node.row.task_id),
          childCount: counts.get(node.row.task_id) ?? 0,
          foldedLinks: 0,
        });
      }
      /* 자리를 못 얻은 업무의 자식은 «그 자리» 로 올라온다 — 깊이를 한 단 더 내리지 않는다. */
      const nextDepth = visible ? depth + 1 : depth;
      const nextHidden = hidden || (visible && collapsed.has(node.row.task_id));
      walk(node.children, nextDepth, nextHidden);
    }
  };
  walk(buildTaskTree(tasks), 0, false);
  return flat;
}

/**
 * 모든 업무 → **자리를 가진 행**의 매핑 (D-17).
 *
 * 접혀서 자리가 없거나(가지가 접혔다) 간트에 서지 않는(기간이 없다) 업무는 **가장 가까운
 * 자리 있는 조상**으로 접힌다. 자리를 끝내 못 찾으면 `null` 이고, 그때 그 선은 **말없이 사라지는 대신
 * 「자리를 못 찾은 선」으로 셈에 남는다** — 시안의 `if (!a || !b) return` 이 바로 그 거짓의 자리였다.
 */
export function anchorIndex(tasks: readonly ProjectTaskRow[], rows: readonly GanttFlatRow[]): Map<string, string | null> {
  const placed = new Set(rows.map((entry) => entry.row.task_id));
  const parents = parentIndex(tasks);
  const anchors = new Map<string, string | null>();
  const resolve = (taskId: string, guard: Set<string>): string | null => {
    const known = anchors.get(taskId);
    if (known !== undefined) return known;
    if (placed.has(taskId)) return taskId;
    const parent = parents.get(taskId);
    if (!parent || guard.has(parent)) return null;
    guard.add(parent);
    return resolve(parent, guard);
  };
  for (const task of tasks) anchors.set(task.task_id, resolve(task.task_id, new Set([task.task_id])));
  return anchors;
}

export type DepLink = {
  key: string;
  /** 원래의 선행·후행. 접힘으로 자리가 바뀌어도 **어느 업무의 선이었는지는 남는다**. */
  fromTaskId: string;
  toTaskId: string;
  /** 실제로 선이 닿는 행. 접힌 가지의 선은 **접힌 부모 바**에 붙는다. */
  fromRowId: string | null;
  toRowId: string | null;
  /** 양 끝이 같은 행으로 접혔다 — 가지 «안쪽» 의 선이다. 그 행이 셈으로 들고 있는다. */
  internal: boolean;
  /** 양 끝 중 하나가 자리를 못 찾았다 — 기간이 없는 업무에 걸린 선이다. */
  unplaced: boolean;
};

/**
 * 의존선 전부 — **원천은 `preceding_task_ids` 하나**다.
 *
 * **하나도 떨어뜨리지 않는다.** 접히면 부모 바로, 자리를 못 찾으면 `unplaced` 로 남는다.
 * 그 셈을 화면이 말하므로 「선행 없음」이라는 거짓이 생기지 않는다.
 */
export function depLinks(tasks: readonly ProjectTaskRow[], anchors: Map<string, string | null>): DepLink[] {
  const links: DepLink[] = [];
  for (const task of tasks) {
    for (const predecessor of task.preceding_task_ids ?? []) {
      const fromRowId = anchors.get(predecessor) ?? null;
      const toRowId = anchors.get(task.task_id) ?? null;
      links.push({
        key: `${predecessor}->${task.task_id}`,
        fromTaskId: predecessor,
        toTaskId: task.task_id,
        fromRowId,
        toRowId,
        internal: fromRowId !== null && fromRowId === toRowId,
        unplaced: fromRowId === null || toRowId === null,
      });
    }
  }
  return links;
}

/** 가지 안쪽으로 접힌 선의 수를 그 행에 싣는다 — 접어도 **선이 사라지지 않았다**는 증거다. */
export function withFoldedLinks(rows: readonly GanttFlatRow[], links: readonly DepLink[]): GanttFlatRow[] {
  const folded = new Map<string, number>();
  for (const link of links) {
    if (!link.internal || link.fromRowId === null) continue;
    folded.set(link.fromRowId, (folded.get(link.fromRowId) ?? 0) + 1);
  }
  return rows.map((entry) => ({ ...entry, foldedLinks: folded.get(entry.row.task_id) ?? 0 }));
}

/** 직각 3구간 — `M 선행끝 H 중간 V 후행행 H 후행시작-5` (시안 `projects.v1.jsx:92`). */
export function depPath(a: { x2: number; y: number }, b: { x1: number; y: number }): string {
  const middle = Math.max(a.x2 + 10, b.x1 - 10);
  return `M ${a.x2} ${a.y} H ${middle} V ${b.y} H ${b.x1 - 5}`;
}
