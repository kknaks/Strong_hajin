import type { KeyboardEvent } from "react";

import { Badge } from "../../ds/Badge";
import { Button } from "../../ds/Button";
import { Empty } from "../../ds/Empty";
import { Icon } from "../../ds/icons/Icon";
import { SegmentedControl } from "../../ds/SegmentedControl";
import { inboxScreen as copy } from "../../lib/labels";
import type { InboxCard, InboxMailCard, InboxRoomCard, InboxSource } from "../../lib/viewModels";
import { clockOf, dayLabelOf, parseAddress, shortWhen } from "./inboxModel";

/**
 * 메시지함 좌 레일 — 출처 전환 + 카드 목록 (SPEC-008 §2.1 · DC-1·6).
 *
 * 카드 모양은 업무 탭의 `.scax-inbox-card` 그대로이고 **행동은 읽음뿐**이다(D-07 — 업무/참고 배지 · `+` · 내 업무로 ·
 * 확인완료 없음). 메일은 한 통 = 카드 하나, 슬랙·카톡은 방 하나 = 카드 하나(마지막 3줄 미리보기 · D-08).
 */

export type RailState = "loading" | "error" | "ready";

export const cardKey = (card: InboxCard) => (card.kind === "mail" ? `mail:${card.message_id}` : `room:${card.room_id}`);

function cardProps(card: InboxCard, unread: boolean, selected: boolean, onSelect: (card: InboxCard) => void) {
  return {
    "aria-pressed": selected,
    className: `scax-inbox-card scax-inbox-card--${unread ? "unread" : "read"}${selected ? " scax-inbox-card--selected" : ""}`,
    "data-card": cardKey(card),
    onClick: () => onSelect(card),
    onKeyDown: (event: KeyboardEvent) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        onSelect(card);
      }
    },
    role: "button",
    tabIndex: 0,
  };
}

/** 메일 = 단건. 제목 · 본문 두 줄 · 보낸 사람 · 시각 · 첨부 수 */
function MailCard({ card, selected, onSelect }: { card: InboxMailCard; selected: boolean; onSelect: (card: InboxCard) => void }) {
  const sender = parseAddress(card.sender ?? "");
  return (
    <article {...cardProps(card, card.unread, selected, onSelect)}>
      <div className="scax-inbox-card__content">
        <div className="scax-inbox-card__top">
          <Badge tone="neutral">{copy.sourceBadge.mail}</Badge>
          {card.unread ? <span aria-label={copy.unreadDot} className="scax-inbox-card__dot" role="img" /> : null}
        </div>
        <h3 className="scax-inbox-card__title">{card.subject ?? ""}</h3>
        {card.snippet ? <p className="scax-inbox-card__excerpt">{card.snippet}</p> : null}
        <div className="scax-inbox-card__meta">
          <span className="scax-inbox-card__meta-who">{sender.name}</span>
          <span className="scax-inbox-card__meta-sep" />
          <span>{shortWhen(card.at)}</span>
          {card.attach_count ? (
            <>
              <span className="scax-inbox-card__meta-sep" />
              <span className="scax-inbox-card__source">
                <Icon name="paperclip" size={16} />
                {card.attach_count}
              </span>
            </>
          ) : null}
        </div>
      </div>
    </article>
  );
}

/** 슬랙·카톡 = 대화방 한 장. 마지막 3줄 미리보기 */
function RoomCard({ card, selected, onSelect }: { card: InboxRoomCard; selected: boolean; onSelect: (card: InboxCard) => void }) {
  const shown = (card.preview ?? []).slice(-3);
  const rest = card.unread_count > shown.length ? card.unread_count - shown.length : 0;
  return (
    <article {...cardProps(card, card.unread_count > 0, selected, onSelect)}>
      <div className="scax-inbox-card__content">
        <div className="scax-inbox-card__top">
          <Badge tone="neutral">{copy.sourceBadge[card.kind]}</Badge>
          {card.unread_count ? <Badge variant="count">{card.unread_count}</Badge> : null}
        </div>
        <h3 className="scax-inbox-card__title">{card.title}</h3>
        {shown.length ? (
          <ul className="scax-room-preview">
            {shown.map((line, index) => (
              <li className="scax-room-preview__line" key={`${line.at}-${index}`}>
                <span className="scax-room-preview__who">{line.author ?? ""}</span>
                <span className="scax-room-preview__text">{line.text ?? ""}</span>
                <span className="scax-room-preview__at">{clockOf(line.at)}</span>
              </li>
            ))}
            {rest ? <li className="scax-room-preview__more">{copy.more(rest)}</li> : null}
          </ul>
        ) : null}
        <div className="scax-inbox-card__meta">
          {card.member_count != null ? <span className="scax-inbox-card__meta-who">{copy.members(card.member_count)}</span> : null}
          {card.member_count != null ? <span className="scax-inbox-card__meta-sep" /> : null}
          <span>{copy.last(`${dayLabelOf(card.last_at)} ${clockOf(card.last_at)}`)}</span>
        </div>
      </div>
    </article>
  );
}

export function MessageRail({
  state,
  source,
  onSource,
  cards,
  unread,
  selectedKey,
  onSelect,
  onRetry,
  hasMore,
  loadingMore,
  onMore,
}: {
  state: RailState;
  source: InboxSource;
  onSource: (source: InboxSource) => void;
  cards: InboxCard[];
  unread: number | undefined;
  selectedKey: string | null;
  onSelect: (card: InboxCard) => void;
  onRetry: () => void;
  hasMore: boolean;
  loadingMore: boolean;
  onMore: () => void;
}) {
  let body;
  if (state === "loading") {
    body = (
      <div aria-busy="true" className="scax-skeleton-stack" role="status">
        <span className="sr-only">{copy.loadingList}</span>
        {[0, 1, 2, 3, 4].map((index) => (
          <span aria-hidden className="scax-skeleton scax-skeleton--card" key={index} />
        ))}
      </div>
    );
  } else if (state === "error") {
    body = <Empty actionLabel={copy.retry} description={copy.retryDesc} onAction={onRetry} title={copy.listError} variant="error" />;
  } else if (!cards.length && source === "all") {
    body = <Empty description={copy.emptyDesc} title={copy.emptyTitle} />;
  } else if (!cards.length) {
    body = <Empty description={copy.filterEmptyDesc} title={copy.filterEmptyTitle} variant="filter" />;
  } else {
    body = (
      <div className="scax-inbox-list">
        {cards.map((card) =>
          card.kind === "mail" ? (
            <MailCard card={card} key={cardKey(card)} onSelect={onSelect} selected={cardKey(card) === selectedKey} />
          ) : (
            <RoomCard card={card} key={cardKey(card)} onSelect={onSelect} selected={cardKey(card) === selectedKey} />
          ),
        )}
        {hasMore ? (
          <div className="scax-inbox-list__more">
            <Button disabled={loadingMore} label={copy.loadMore} onClick={onMore} size="sm" variant="text" />
          </div>
        ) : null}
      </div>
    );
  }
  return (
    <section aria-label={copy.title} className="scax-gutter-list">
      <header className="scax-gutter-list__header">
        <h2 className="scax-gutter-list__title">
          <Icon name="inbox" size={24} />
          {copy.title}
          {state === "ready" && typeof unread === "number" ? <Badge variant="count">{unread}</Badge> : null}
        </h2>
        <SegmentedControl ariaLabel={copy.sourceAria} onChange={onSource} options={copy.sources} value={source} />
      </header>
      <div className="scax-gutter-list__body">{body}</div>
    </section>
  );
}
