import { Fragment } from "react";

import { Badge } from "../../ds/Badge";
import { calendarScreen } from "../../lib/labels";
import type { RailCard } from "./calendarModel";

/**
 * 좌측 일정 레일의 카드 한 장 (G-CAL-04).
 *
 * 마크업은 저장소에 이미 있는 `.scax-inbox-card` 계열 그대로다(`components.css:111-121` ·
 * 선례 `shell/InboxRail.tsx:59-71`) — 시안이 더하는 것은 `--draggable` 한 종뿐이고 그것은 **FE-2** 다.
 *
 * **배지는 유형 하나뿐이다 — 상태를 내지 않는다** (K15). 합본 조회의 `state` 는 내부
 * `completion_submitted` 를 `"done"` 으로 투영하고 행에 `derived` 가 없어서, 여기서 상태를 쓰면
 * 「승인 대기」인 업무를 **「완료」라고 말하게 된다**. 상태는 카드를 열어 상세에서 읽는다.
 */
export function ScheduleCard({ card, onOpen }: { card: RailCard; onOpen?: (card: RailCard) => void }) {
  const openable = Boolean(onOpen);
  return (
    <article
      className={openable ? "scax-inbox-card openable" : "scax-inbox-card"}
      data-calendar-key={card.key}
      onClick={openable ? () => onOpen?.(card) : undefined}
    >
      <div className="scax-inbox-card__content">
        <div className="scax-inbox-card__top">
          <Badge tone={card.kind === "meeting" ? "neutral" : "accent"}>
            {card.kind === "meeting" ? calendarScreen.meetingBadge : calendarScreen.taskBadge}
          </Badge>
        </div>
        <h3 className="scax-inbox-card__title">
          {openable ? (
            <button
              className="scax-inbox-card__open"
              onClick={(event) => {
                event.stopPropagation();
                onOpen?.(card);
              }}
              type="button"
            >
              {card.title}
            </button>
          ) : (
            card.title
          )}
        </h3>
        <div className="scax-inbox-card__meta">
          <span className="scax-inbox-card__meta-who">{card.when}</span>
          {/* 구분선은 «칸 사이»에 서야 하므로 감싸는 요소를 두지 않는다 — 레일의 선례와 같다
              (`shell/InboxRail.tsx:71`): `.scax-inbox-card__meta` 가 flex 라 한 겹이 끼면 간격이 어긋난다. */}
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
