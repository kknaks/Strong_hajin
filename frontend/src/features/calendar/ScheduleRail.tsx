import { Badge } from "../../ds/Badge";
import { Button } from "../../ds/Button";
import { Empty } from "../../ds/Empty";
import { Icon } from "../../ds/icons/Icon";
import { SegmentedControl } from "../../ds/SegmentedControl";
import { Skeleton } from "../../ds/Skeleton";
import { StatusNote } from "../../ds/StatusNote";
import { calendarScreen, calendarTabLabel, formatLongDate } from "../../lib/labels";
import type { CalendarTab, RailCard } from "./calendarModel";
/* 카드 본체는 옆자리 파일이 갖는다 — 레일은 «무엇을 세울지»만 안다. */
import { ScheduleCard } from "./ScheduleCard";

/**
 * 좌측 일정 레일 (G-CAL-01).
 *
 * ⚠ **이름이 `GutterList` 가 아니다.** 저장소 DS 에 같은 이름의 **다른 부품**이 이미 있고
 * (`ds/GutterList.tsx` — 왼쪽 고정 칸 + 2단 목록), 그 개명은 소비처를 안 세서 이번 범위 밖이다(§J).
 * 마크업 `.scax-gutter-list*` 는 시안과 같고 저장소에도 이미 있다 — `shell/InboxRail.tsx:103-114`
 * 가 인라인으로 쓰던 것을 부품으로 세웠다.
 *
 * **탭은 레일과 격자를 동시에 가른다**(SPEC §2.1). 다만 대칭이 아니다 — 레일은 「전체」에서
 * 회의만 카드로 세우고 업무의 시간 배정은 **업무 카드의 meta 줄로 접는다**. 그 계산은
 * `calendarModel.railCards()` 가 한다.
 */
export function ScheduleRail({
  cards,
  tab,
  onTab,
  selected,
  onClearDay,
  state,
  onRetry,
  onOpen,
  onDragStart,
}: {
  cards: RailCard[];
  tab: CalendarTab;
  onTab: (tab: CalendarTab) => void;
  /** 고른 날짜 (ISO). 있으면 레일이 그 날로 좁혀졌다는 것을 머리줄 아래에서 말한다. */
  selected: string | null;
  onClearDay: () => void;
  state: "loading" | "error" | "ready";
  onRetry: () => void;
  /** 업무 카드를 연다. 회의 카드에는 붙지 않는다. */
  onOpen?: (card: RailCard) => void;
  /** 업무 카드를 끌기 시작했다 (R1·R5). 회의 카드에는 붙지 않는다 — 캘린더는 회의를 옮기지 않는다(§F). */
  onDragStart?: (card: RailCard) => void;
}) {
  let body;
  if (state === "loading") {
    body = <Skeleton label={calendarScreen.loading} rows={3} />;
  } else if (state === "error") {
    body = (
      <StatusNote tone="danger">
        {calendarScreen.loadFailed}{" "}
        <Button onClick={onRetry} size="sm" type="button" variant="text">
          {calendarScreen.retry}
        </Button>
      </StatusNote>
    );
  } else if (cards.length === 0) {
    body = (
      <Empty
        description={calendarScreen.railEmptyDescription}
        icon="calendar"
        title={calendarScreen.railEmptyTitle}
      />
    );
  } else {
    /* 여는 것은 업무뿐이다 — 캘린더는 회의에 아무 명령도 내지 않는다(§2.4). 회의 카드는 읽는 카드다. */
    body = cards.map((card) => (
      <ScheduleCard
        card={card}
        key={card.key}
        onDragStart={card.kind === "task" ? onDragStart : undefined}
        onOpen={card.kind === "task" ? onOpen : undefined}
      />
    ));
  }
  return (
    <section aria-label={calendarScreen.railTitle} className="scax-gutter-list">
      <header className="scax-gutter-list__header">
        <h2 className="scax-gutter-list__title">
          <Icon name="calendar" size={24} />
          {calendarScreen.railTitle}
          {state === "ready" && cards.length > 0 && <Badge variant="count">{cards.length}</Badge>}
        </h2>
        <SegmentedControl
          ariaLabel={calendarScreen.tabAria}
          onChange={onTab}
          options={[
            { value: "all", label: calendarTabLabel.all },
            { value: "meeting", label: calendarTabLabel.meeting },
            { value: "task", label: calendarTabLabel.task },
          ]}
          value={tab}
        />
      </header>
      <div className="scax-gutter-list__body">
        {selected ? (
          <div className="scax-cal-rail__scope">
            <span>{formatLongDate(selected)}</span>
            <Button label={calendarScreen.railScopeAll} onClick={onClearDay} size="sm" tone="neutral" variant="text" />
          </div>
        ) : null}
        {body}
      </div>
    </section>
  );
}
