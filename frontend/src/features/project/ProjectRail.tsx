import { Fragment, useEffect, useRef } from "react";

import { Badge } from "../../ds/Badge";
import { Button } from "../../ds/Button";
import { Empty } from "../../ds/Empty";
import { Icon } from "../../ds/icons/Icon";
import { Select } from "../../ds/Select";
import { Skeleton } from "../../ds/Skeleton";
import { StatusNote } from "../../ds/StatusNote";
import { emptyActionLabel, projectScreen, selectLabel, taskStateLabel, taskStateTone } from "../../lib/labels";
import type { Project } from "../../lib/viewModels";
import type { ProjectRailCard } from "./projectModel";

/**
 * 좌 레일 — 프로젝트 셀렉터 + 그 프로젝트의 업무 카드 (WORK-005 FE-1).
 *
 * **프로젝트 전환이 헤더에 있다** — 별도의 프로젝트 목록 화면이 없다(D-05). 셀렉터는 알약 트리거로
 * 갈아끼운 `ds/Select` 이고 선례는 `features/work/MyWorkPage.tsx:1410` 이다.
 *
 * 카드는 **기존 `scax-inbox-card` 를 그대로 재사용한다** — 조립 선례는
 * `features/calendar/ScheduleCard.tsx:42-99`. 이 화면이 더하는 것은 **선택 상태뿐**이고,
 * 그 CSS 는 `[role="button"]` 으로 좁혀 수신함·캘린더 카드에 닿지 않게 했다(§공유 자산).
 *
 * ⚠ **클릭의 뜻은 「선택」이고 「여는 것」이 아니다** (D-20). 드로어는 우 레일의 「업무 열기」가 연다.
 */
export function ProjectRail({
  projects,
  projectId,
  onProject,
  cards,
  taskId,
  onTask,
  state,
  onRetry,
}: {
  projects: Project[];
  projectId: string;
  onProject: (projectId: string) => void;
  cards: ProjectRailCard[];
  taskId: string | null;
  onTask: (taskId: string) => void;
  state: "loading" | "error" | "ready";
  onRetry: () => void;
}) {
  const list = useRef<HTMLDivElement | null>(null);
  /**
   * **선택을 좌 레일이 따라간다** (D-37 · L-46).
   *
   * 지금까지는 카드에 선택 표시만 서고 **뷰포트 밖에 남았다** — 아래쪽 업무를 간트에서 고르면
   * 사람은 아무 일도 안 일어난 것처럼 본다. `nearest` 라서 **이미 보이는 카드는 움직이지 않는다** —
   * 레일에서 직접 고른 경우가 그렇고, 불필요한 점프가 안 보이는 것보다 나쁘다.
   * jsdom 에는 `scrollIntoView` 가 없다 (선례 `ds/GutterList.tsx:36`).
   */
  useEffect(() => {
    if (taskId === null) return;
    const card = list.current?.querySelector(`.scax-inbox-card[data-task-id="${taskId}"]`);
    card?.scrollIntoView?.({ block: "nearest" });
  }, [taskId]);

  let body;
  if (state === "loading") {
    body = <Skeleton label={projectScreen.sideLoading} rows={3} />;
  } else if (state === "error") {
    body = (
      <StatusNote tone="danger">
        {projectScreen.loadFailed}{" "}
        <Button onClick={onRetry} size="sm" type="button" variant="text">
          {emptyActionLabel.error}
        </Button>
      </StatusNote>
    );
  } else if (cards.length === 0) {
    body = <Empty description={projectScreen.railEmptyDescription} icon="square-check" title={projectScreen.railEmptyTitle} />;
  } else {
    body = cards.map((card) => (
      <ProjectTaskCard card={card} key={card.id} onSelect={onTask} selected={card.id === taskId} />
    ));
  }
  return (
    <section aria-label={projectScreen.railTitle} className="scax-gutter-list">
      {/* **한 줄이다** (D-34, 사용자 결정): 프로젝트 아이콘 + 「프로젝트」 + 건수 배지 ─── 셀렉터.
          ~~윗줄 제목 · 아랫줄 셀렉터~~ 였던 두 줄(`--sub`)을 2026-09-22 에 놓았다 —
          `.scax-gutter-list__header` 의 기본(row · 32px)이 그대로 이 배치다.
          아이콘은 사이드바의 프로젝트 아이콘(`folder`)과 같은 것이고, 배지는 **수신함·캘린더 레일이
          쓰는 `Badge variant="count"` 그대로**다 — 새 부품이 아니다. */}
      <header className="scax-gutter-list__header">
        {/* 제목 쪽은 **안 줄어든다** — 길이가 고정(아이콘 + 「프로젝트」 + 두 자리 배지)이고,
            가변인 것은 프로젝트 «이름» 이다. 남는 폭은 이름이 가져간다 (`projects.css` 레일 머리 블록). */}
        <h2 className="scax-gutter-list__title scax-pj-rail__title">
          <Icon name="folder" size={24} />
          {projectScreen.railTitle}
          {state === "ready" && <Badge variant="count">{cards.length}</Badge>}
        </h2>
        <Select
          emptyActionLabel={emptyActionLabel.filter}
          label={projectScreen.projectSelect}
          labels={selectLabel}
          onChange={onProject}
          options={projects.map((project) => ({ value: project.project_id, label: project.name }))}
          trigger={({ label, props }) => (
            <button {...props} className="scax-select__trigger scax-pj-rail__project" type="button">
              <Icon name="folder" size={14} />
              {/* 이름은 **제 요소 안에** 있어야 한다 — 트리거가 flex 라 벌거벗은 글자는 익명 flex item 이 되고,
                  그 위의 `text-overflow` 가 닿지 않아 **말줄임표 없이 글자 중간에서 잘린다**. */}
              <span className="scax-pj-rail__project-name">{label}</span>
              <Icon name="chevron-down" size={12} />
            </button>
          )}
          value={projectId}
        />
      </header>
      <div className="scax-gutter-list__body" ref={list}>
        {body}
      </div>
    </section>
  );
}

/**
 * 업무 카드 한 장 — 상태 배지 → 제목 → meta 한 줄(기간 · 담당).
 *
 * **「분류」를 싣지 않는다** (D-08). **담당이 없으면 그 칸이 아예 없다** — 「미정」을 지어내지 않는다.
 * **취소된 업무도 남는다** — 요약 스트립에서 «세지 않는 것» 과 목록에서 «감추는 것» 은 다르다.
 */
function ProjectTaskCard({
  card,
  selected,
  onSelect,
}: {
  card: ProjectRailCard;
  selected: boolean;
  onSelect: (taskId: string) => void;
}) {
  return (
    <article
      className={`scax-inbox-card${selected ? " scax-inbox-card--selected" : ""}`}
      data-task-id={card.id}
      onClick={() => onSelect(card.id)}
      onKeyDown={(event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        onSelect(card.id);
      }}
      role="button"
      tabIndex={0}
    >
      <div className="scax-inbox-card__content">
        <div className="scax-inbox-card__top">
          <Badge tone={taskStateTone[card.state] ?? "neutral"}>{taskStateLabel[card.state] ?? card.state}</Badge>
        </div>
        <h3 className="scax-inbox-card__title">{card.title}</h3>
        <div className="scax-inbox-card__meta">
          <span className="scax-inbox-card__meta-who">{card.when}</span>
          {/* 구분선은 «칸 사이»에 서야 하므로 감싸는 요소를 두지 않는다 — 레일의 선례와 같다. */}
          {card.meta.map((line) => (
            <Fragment key={line}>
              <span className="scax-inbox-card__meta-sep" />
              <span>{line}</span>
            </Fragment>
          ))}
        </div>
      </div>
    </article>
  );
}
