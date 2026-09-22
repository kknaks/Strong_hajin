import { useEffect, useRef } from "react";

import { Empty } from "../../ds/Empty";
import { Icon } from "../../ds/icons/Icon";
import { dayDifference, personName, projectScreen } from "../../lib/labels";
import type { ProjectTaskRow } from "../../lib/viewModels";
import {
  GANTT,
  anchorIndex,
  barGeometry,
  barStatus,
  depLinks,
  depPath,
  ganttAxis,
  ganttRows,
  indentFor,
  taskPercent,
  withFoldedLinks,
  type DepLink,
  type GanttAxis,
  type GanttFlatRow,
} from "./projectModel";

/**
 * 본문 ② 진행 라인(간트) + ③ 의존선 — **이 판의 가장 어려운 자리** (WORK-005 FE-2).
 *
 * 기하는 시안 그대로다: 행 40px · 하루 34px · 라벨 칸 200px · 바 22/14/18px.
 * **우리가 바꾼 것은 들여쓰기 한 값뿐**이다 — 깊이당 12px · 5단에서 멈춘다 (D-18).
 * **멈추는 것은 들여쓰기이고 행이 아니다**: 6층 이하도 자기 행과 자기 바를 갖는다.
 *
 * ⚠ **시안의 `DepLines` 를 그대로 옮기면 안 된다.** 시안은 양 끝 좌표를 못 찾으면
 * `if (!a || !b) return` 으로 말없이 빠져나간다(`projects.v1.jsx:85-106`) — 목데이터가 1단계뿐이라
 * 생길 수 없던 상황이다. 우리 자료에는 깊이 제한이 없어서 **가지를 접는 순간 그 return 이 돈다.**
 * 그러면 화면이 「선행 없음」이라고 **거짓말한다.** 그래서 접힌 가지의 선은 `projectModel.anchorIndex()`
 * 가 **접힌 부모 바로 끌어붙이고**, 그래도 자리를 못 찾은 선(기간이 없는 업무에 걸린 선)은
 * **머리줄이 건수로 말한다.** 조용히 사라지는 선이 하나도 없다 (D-17).
 */
export function ProjectGantt({
  projectId,
  tasks,
  taskId,
  onTask,
  collapsed,
  onToggle,
  today,
}: {
  /** 지금 보고 있는 프로젝트. **첫 진입 스크롤을 «언제» 다시 맞출지**를 이 값이 정한다 (아래 `GanttCanvas`). */
  projectId: string;
  tasks: ProjectTaskRow[];
  taskId: string | null;
  onTask: (taskId: string) => void;
  /** 접힌 가지. **기본은 모두 펼침**이라 비어 있는 것이 기본값이다. */
  collapsed: ReadonlySet<string>;
  onToggle: (taskId: string) => void;
  /** 오늘 (ISO). **화면이 시계를 들지 않는다** — 부르는 쪽이 넘긴다. */
  today: string;
}) {
  const axis = ganttAxis(tasks);
  /* 자리 → 닻 → 선 → 접힌 셈. 이 순서가 규칙이다: 닻은 «지금 서 있는 행» 을 보고 정해진다. */
  const placedRows = ganttRows(tasks, collapsed);
  const anchors = anchorIndex(tasks, placedRows);
  const links = depLinks(tasks, anchors);
  const rows = withFoldedLinks(placedRows, links);
  const unplaced = links.filter((link) => link.unplaced).length;

  return (
    <section aria-label={projectScreen.ganttTitle} className="scax-pj-gantt">
      <header className="scax-pj-gantt__head">
        <h2 className="scax-pj-gantt__title">{projectScreen.ganttTitle}</h2>
        <span className="scax-pj-gantt__legend">
          {axis ? `${projectScreen.ganttRange(axis.from, axis.to)} · ` : ""}
          {projectScreen.ganttLegend}
        </span>
        {/* 자리를 못 찾은 선은 «없는 것» 이 아니라 «자리가 없는 것» 이다 — 그 사실을 여기서 말한다. */}
        {unplaced > 0 && (
          <span className="scax-pj-gantt__legend scax-pj-gantt__legend--warn" data-unplaced={unplaced}>
            {projectScreen.unplacedLinks(unplaced)}
          </span>
        )}
      </header>
      {axis === null || rows.length === 0 ? (
        <Empty description={projectScreen.ganttEmptyDescription} icon="calendar" title={projectScreen.ganttEmpty} />
      ) : (
        <GanttCanvas
          axis={axis}
          links={links}
          onTask={onTask}
          onToggle={onToggle}
          projectId={projectId}
          rows={rows}
          taskId={taskId}
          today={today}
        />
      )}
    </section>
  );
}

function GanttCanvas({
  axis,
  rows,
  links,
  taskId,
  onTask,
  onToggle,
  projectId,
  today,
}: {
  axis: GanttAxis;
  rows: GanttFlatRow[];
  links: DepLink[];
  taskId: string | null;
  onTask: (taskId: string) => void;
  onToggle: (taskId: string) => void;
  projectId: string;
  today: string;
}) {
  const width = GANTT.label + axis.days.length * GANTT.day;
  const height = rows.length * GANTT.row;
  /* 자리 표 — 행마다 막대의 양 끝과 세로 중심. 의존선은 «행의» 좌표를 쓴다. */
  const positions = new Map<string, { x1: number; x2: number; y: number }>();
  rows.forEach((entry, index) => {
    const bar = barGeometry(entry.row, axis);
    if (!bar) return;
    positions.set(entry.row.task_id, { x1: bar.left, x2: bar.left + bar.width, y: index * GANTT.row + GANTT.row / 2 });
  });
  const offset = dayDifference(axis.from, today);
  const nowLeft = offset >= 0 && offset < axis.days.length ? GANTT.label + offset * GANTT.day : null;

  const scroller = useRef<HTMLDivElement | null>(null);
  /**
   * 첫 진입은 **「오늘」이 보이는 자리**에서 연다 — 기간의 첫날이 아니다 (D-36).
   *
   * 고정된 이름 칸이 뷰포트 왼쪽 `GANTT.label` 을 늘 덮으므로, 「보인다」는 **오늘의 x 가 그 칸보다
   * 오른쪽에 온다**는 뜻이다: `scrollLeft ≤ offset × day`. 거기서 `openLead` 일만큼 더 물려
   * **앞 며칠도 함께** 보이게 둔다 — 오늘이 화면 맨 왼쪽에 붙으면 「어제까지 무엇이 있었나」가 잘린다.
   *
   * 오늘이 축 **밖**이면(프로젝트가 이미 끝났거나 아직 시작 전) 가장 가까운 끝으로 접는다 —
   * 없는 날로 스크롤하지 않는다. 선례는 캘린더 주 뷰(`features/calendar/WeekGrid.tsx:101-111`)다.
   */
  const openOffset = Math.min(Math.max(offset, 0), axis.days.length - 1);
  const openLeft = Math.max(0, (openOffset - GANTT.openLead) * GANTT.day);
  /* 최신 목표값을 ref 로 들고 effect 는 «프로젝트가 바뀔 때만» 깨운다 — 자료가 갱신될 때마다
     되감으면 **사람이 민 자리를 화면이 도로 빼앗는다** (선례 `ProjectPage.tsx:171-174`). */
  const latestOpen = useRef(openLeft);
  useEffect(() => {
    latestOpen.current = openLeft;
  });
  useEffect(() => {
    if (scroller.current) scroller.current.scrollLeft = latestOpen.current;
  }, [projectId]);

  /**
   * **반대 방향의 자동 스크롤** — 좌 레일에서 고른 업무의 행이 간트에서 보이게 한다 (D-37 · L-47).
   *
   * `nearest` 라서 **이미 보이는 행은 움직이지 않는다** — 간트에서 직접 고른 경우가 그렇다.
   * `inline:"nearest"` 도 같은 이유로 안전하다: 행은 `left:0;right:0` 이라 늘 스크롤 폭 전체를
   * 차지하므로 가로로는 계산이 붙지 않는다 — **첫 진입에 맞춰 둔 오늘 자리를 빼앗지 않는다.**
   * jsdom 에는 `scrollIntoView` 가 없다 (선례 `ds/GutterList.tsx:36`).
   */
  useEffect(() => {
    if (taskId === null) return;
    const row = scroller.current?.querySelector(`.scax-pj-gantt__row[data-task-id="${taskId}"]`);
    row?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [taskId]);

  return (
    <div className="scax-pj-gantt__scroll" ref={scroller}>
      <div className="scax-pj-gantt__canvas" style={{ width }}>
        <div className="scax-pj-gantt__axis">
          {/* 이름 칸 자리를 «비워» 두지 않고 요소로 덮는다 — 비운 자리는 아무것도 가리지 못해
              스크롤한 날짜 숫자가 고정된 이름 칸 위로 그대로 올라온다 (L-44). 폭은 이름 칸과 한 값이다. */}
          <span aria-hidden="true" className="scax-pj-gantt__axis-pad" style={{ width: GANTT.label }} />
          {axis.days.map((day) => (
            <span
              className={`scax-pj-gantt__day${day === today ? " scax-pj-gantt__day--today" : ""}`}
              key={day}
              style={{ width: GANTT.day }}
            >
              {Number(day.slice(8, 10))}
            </span>
          ))}
        </div>
        <div className="scax-pj-gantt__plot" style={{ height }}>
          <div className="scax-pj-gantt__grid" style={{ left: GANTT.label, backgroundSize: `${GANTT.day}px ${GANTT.row}px` }} />
          {nowLeft !== null && <span className="scax-pj-gantt__now" style={{ left: nowLeft, width: GANTT.day }} />}
          <DepLines height={height} links={links} positions={positions} taskId={taskId} width={width} />
          {rows.map((entry, index) => (
            <GanttRow
              entry={entry}
              key={entry.row.task_id}
              left={positions.get(entry.row.task_id)?.x1 ?? GANTT.label}
              onTask={onTask}
              onToggle={onToggle}
              selected={entry.row.task_id === taskId}
              top={index * GANTT.row}
              width={(positions.get(entry.row.task_id)?.x2 ?? GANTT.label) - (positions.get(entry.row.task_id)?.x1 ?? GANTT.label)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function GanttRow({
  entry,
  top,
  left,
  width,
  selected,
  onTask,
  onToggle,
}: {
  entry: GanttFlatRow;
  top: number;
  left: number;
  width: number;
  selected: boolean;
  onTask: (taskId: string) => void;
  onToggle: (taskId: string) => void;
}) {
  const { row, depth, hasChildren, expandable, open, childCount, foldedLinks } = entry;
  const percent = taskPercent(row);
  const owner = row.assignee ? personName(row.assignee.display_name) : "";
  const classes = [
    "scax-pj-gantt__row",
    depth > 0 ? "scax-pj-gantt__row--child" : "",
    row.state === "cancelled" ? "scax-pj-gantt__row--cancelled" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const barClasses = [
    "scax-pj-gantt__bar",
    `scax-pj-gantt__bar--${barStatus(row.state)}`,
    depth > 0 ? "scax-pj-gantt__bar--child" : "",
    hasChildren ? "scax-pj-gantt__bar--parent" : "",
    selected ? "scax-pj-gantt__bar--on" : "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <div className={classes} data-depth={depth} data-task-id={row.task_id} style={{ top, height: GANTT.row }}>
      <div className="scax-pj-gantt__name-cell" style={{ width: GANTT.label, paddingLeft: indentFor(depth) }}>
        {/* twisty 는 **자식이 있는 모든 깊이**에 선다 — 1단계에서 멈추지 않는다 (D-16). 다만
            하위가 «전부 기간 없는» 업무라 접어도 사라질 행이 없으면 세우지 않는다 — 죽은 손잡이다. */}
        {expandable ? (
          <button
            aria-expanded={open}
            aria-label={open ? projectScreen.collapse : projectScreen.expand}
            className="scax-pj-gantt__twisty"
            onClick={() => onToggle(row.task_id)}
            type="button"
          >
            <Icon name={open ? "chevron-down" : "chevron-right"} size={12} />
          </button>
        ) : (
          <span className="scax-pj-gantt__twisty scax-pj-gantt__twisty--blank" />
        )}
        <button
          className={`scax-pj-gantt__name${selected ? " scax-pj-gantt__name--on" : ""}`}
          onClick={() => onTask(row.task_id)}
          type="button"
        >
          <span className="scax-pj-gantt__name-text">{row.title}</span>
          <span className="scax-pj-gantt__name-owner">
            {owner}
            {hasChildren ? `${owner ? " · " : ""}${projectScreen.childCount(childCount)}` : ""}
          </span>
        </button>
        {/* 접어서 이 바가 삼킨 가지 «안쪽» 선 — 사라진 것이 아니라 여기 붙어 있다는 말이다. */}
        {foldedLinks > 0 && (
          <span className="scax-pj-gantt__folded" data-folded={foldedLinks} title={projectScreen.foldedLinks(foldedLinks)}>
            {foldedLinks}
          </span>
        )}
      </div>
      <button className={barClasses} onClick={() => onTask(row.task_id)} style={{ left, width }} type="button">
        {/* 체크리스트가 없으면 fill 도 % 도 그리지 않는다 — **0% 가 아니다** (D-02). */}
        {percent !== null && <span className="scax-pj-gantt__bar-fill" style={{ width: `${percent}%` }} />}
        {/* 상위 바는 % 를 내지 않는다 — 하위가 있는 **모든 깊이**에서. */}
        {percent !== null && !hasChildren && <span className="scax-pj-gantt__bar-pct">{percent}%</span>}
      </button>
    </div>
  );
}

/**
 * 의존선 오버레이 — 간트 위에 겹치는 SVG. 직각 3구간 + 화살표가 **후행**을 가리킨다.
 *
 * 그리는 것은 **양 끝이 서로 다른 행에 닿는 선**이다. 양 끝이 같은 행으로 접힌 선(가지 안쪽의 선)은
 * 자기를 삼킨 행이 건수로 들고 있고, 자리를 아예 못 찾은 선은 머리줄이 건수로 말한다 —
 * **어느 쪽도 조용히 사라지지 않는다.**
 */
function DepLines({
  links,
  positions,
  taskId,
  width,
  height,
}: {
  links: DepLink[];
  positions: Map<string, { x1: number; x2: number; y: number }>;
  taskId: string | null;
  width: number;
  height: number;
}) {
  const drawn = links
    .filter((link) => !link.internal && !link.unplaced)
    .map((link) => {
      const from = positions.get(link.fromRowId as string);
      const to = positions.get(link.toRowId as string);
      if (!from || !to) return null;
      return {
        key: link.key,
        /* 선택된 업무에 «닿는» 선만 강조한다 — 접혀서 부모 바에 붙은 선은 그 부모에도 닿는다. */
        on: taskId !== null && [link.fromTaskId, link.toTaskId, link.fromRowId, link.toRowId].includes(taskId),
        d: depPath(from, to),
      };
    })
    .filter((segment): segment is { key: string; on: boolean; d: string } => segment !== null);
  return (
    <svg aria-hidden="true" className="scax-pj-gantt__links" height={height} width={width}>
      {/* 화살촉은 **자기 marker 의 문맥**에서 색을 물려받는다 — 참조하는 선의 `currentColor` 가
          닿지 않는다. 그래서 강조용 촉을 아예 따로 둔다: 선만 accent 이고 촉이 회색인 어긋남이 없다. */}
      <defs>
        <marker id="pj-arrow" markerHeight="6" markerWidth="6" orient="auto" refX="5" refY="3">
          <path className="scax-pj-gantt__arrow" d="M0 0 L6 3 L0 6 z" />
        </marker>
        <marker id="pj-arrow-on" markerHeight="6" markerWidth="6" orient="auto" refX="5" refY="3">
          <path className="scax-pj-gantt__arrow scax-pj-gantt__arrow--on" d="M0 0 L6 3 L0 6 z" />
        </marker>
      </defs>
      {drawn.map((segment) => (
        <path
          className={`scax-pj-gantt__link${segment.on ? " scax-pj-gantt__link--on" : ""}`}
          d={segment.d}
          key={segment.key}
          markerEnd={segment.on ? "url(#pj-arrow-on)" : "url(#pj-arrow)"}
        />
      ))}
    </svg>
  );
}
