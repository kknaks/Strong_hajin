import { Fragment } from "react";

import { Badge } from "../../ds/Badge";
import { calendarScreen, derivedApprovalLabel, taskStateLabel, taskStateTone } from "../../lib/labels";
import type { RailCard } from "./calendarModel";
import { debugCalendarDnd } from "./calendarDndDebug";

/**
 * 좌측 일정 레일의 카드 한 장 (G-CAL-04).
 *
 * 마크업은 저장소에 이미 있는 `.scax-inbox-card` 계열 그대로다(`components.css:111-121` ·
 * 선례 `shell/InboxRail.tsx:59-71`) — 시안이 더하는 것은 `--draggable` 한 종뿐이고 **FE-2 가 그것을 켰다.**
 *
 * **회의 카드는 끌 수 없다** (§F) — 캘린더는 회의에 쓰기 명령을 내지 않는다.
 * 반면 **업무 카드는 «할 수 없을 때도» 끌 수 있다.** 끌 수 없게 막으면 왜 안 되는지 말할 자리가
 * 사라지기 때문이다 — 떨어뜨린 뒤에 화면이 말한다 (§I 조용한 거절 0개).
 *
 * **배지는 상태 배지 + 승인 배지 둘이다** (확정 — 증보 K19 가 K15 를 뒤집었다).
 *
 * 합본 조회의 `state` 는 내부 `completion_submitted` 를 `"done"` 으로 투영한다 —
 * **상태 배지 하나만 내면 「승인 대기」인 업무를 「완료」라고 말하게 된다.** K15 는 그래서 상태를
 * 감췄지만, **BE-4 가 `approval` 을 실어 주면서 말할 수 있게 됐다.**
 *
 * **새로 만든 것이 없다 — 업무 화면의 것을 그대로 쓴다.**
 * 상태는 `taskStateLabel`·`taskStateTone`, 승인은 `derivedApprovalLabel` 과
 * `WorkTables.tsx` 의 `WaitingBadge` 가 쓰는 **같은 톤**(확인 대기 `accent` · 보완 요청 `danger`)이다.
 * `approved`·`null` 에는 배지를 내지 않는 것도 그쪽과 같다 — 「기다리는 것」이 없으면 말하지 않는다.
 *
 * **격자는 여전히 상태를 말하지 않는다**(SPEC §2.6) — 배지는 **이 카드만**의 것이다.
 */
export function ScheduleCard({
  card,
  onOpen,
  onDragStart,
}: {
  card: RailCard;
  onOpen?: (card: RailCard) => void;
  /** 주면 끌 수 있다. 업무 카드에만 붙는다 — 회의는 캘린더에서 읽기 전용이다(§F). */
  onDragStart?: (card: RailCard) => void;
}) {
  const openable = Boolean(onOpen);
  const draggable = Boolean(onDragStart);
  return (
    <article
      className={[openable ? "scax-inbox-card openable" : "scax-inbox-card", draggable ? "scax-inbox-card--draggable" : ""]
        .filter(Boolean)
        .join(" ")}
      data-calendar-key={card.key}
      draggable={draggable}
      onClick={openable ? () => onOpen?.(card) : undefined}
      onDragStart={
        draggable
          ? (event) => {
              // WebKit normalizes the legacy `text` flavor differently from Chromium.
              // Publish both aliases; `text/plain` remains the canonical contract.
              event.dataTransfer.clearData();
              event.dataTransfer.setData("text/plain", card.id);
              event.dataTransfer.setData("text", card.id);
              event.dataTransfer.effectAllowed = "move";
              debugCalendarDnd("dragstart", event.dataTransfer);
              onDragStart?.(card);
            }
          : undefined
      }
      title={draggable ? calendarScreen.dragHint : undefined}
    >
      <div className="scax-inbox-card__content">
        <div className="scax-inbox-card__top">
          <Badge tone={card.kind === "meeting" ? "neutral" : "accent"}>
            {card.kind === "meeting" ? calendarScreen.meetingBadge : calendarScreen.taskBadge}
          </Badge>
          {card.state ? <Badge tone={taskStateTone[card.state]}>{taskStateLabel[card.state]}</Badge> : null}
          {card.approval === "awaiting_review" ? <Badge tone="accent">{derivedApprovalLabel.awaiting_review}</Badge> : null}
          {card.approval === "awaiting_revision" ? <Badge tone="danger">{derivedApprovalLabel.awaiting_revision}</Badge> : null}
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
