import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { Button } from "../../ds/Button";
import { Empty } from "../../ds/Empty";
import {
  listInbox,
  listIntegrations,
  markInboxAllRead,
  markInboxMailRead,
  markInboxRoomRead,
  reconnectIntegration,
} from "../../lib/api";
import { inboxScreen as copy } from "../../lib/labels";
import type { InboxCard, InboxSource, InboxStreamEvent, Integration } from "../../lib/viewModels";
import { beginConsent } from "../settings/consent";
import { createInboxEventHub, useInboxStream } from "./inboxStream";
import { MailView } from "./MailView";
import { cardKey, MessageRail, type RailState } from "./MessageRail";
import { RoomView } from "./RoomView";

/**
 * 「메시지함」 화면 (WORK-011 FE-a · SPEC-008 §2.1·2.2·2.7·2.8) — 좌 레일(쌓인 메시지) + 본문(고른 것의 원문).
 *
 * 내비의 **독립 항목**이다 — 「업무 > 수신함」(`shell/InboxRail.tsx`)과 다른 화면이다(D-06).
 * 카드를 누르면 원문이 서고 그 카드가 읽음 · 머리 「모두 읽음으로」 · 네 상태(기본·빈·로딩·오류) · 출처 필터.
 * 새 메시지·답장 결과·연동 상태는 사용자 사건 채널(WS)로 밀려와 갱신된다(AC-10b). 수집이 끊긴 연동은 본문 머리
 * 배너로 알린다(D-50).
 */

type Props = {
  meName: string;
  onError: (message: string | null) => void;
  onRegisterRails?: (rails: { left?: ReactNode; right?: ReactNode }) => void;
  onRegisterHeaderActions?: (actions: ReactNode) => void;
  onRegisterRefresh?: (refresh: (() => Promise<void>) | null) => void;
};

const zeroCounts = { all: 0, mail: 0, slack: 0, kakao: 0 } as const;

export function InboxPage({ meName, onError, onRegisterRails, onRegisterHeaderActions, onRegisterRefresh }: Props) {
  const [source, setSource] = useState<InboxSource>("all");
  const [cards, setCards] = useState<InboxCard[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [counts, setCounts] = useState<Partial<Record<InboxSource, number>> | null>(null);
  const [state, setState] = useState<RailState>("loading");
  const [loadingMore, setLoadingMore] = useState(false);
  const [selected, setSelected] = useState<InboxCard | null>(null);
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const hub = useMemo(createInboxEventHub, []);
  const sourceRef = useRef(source);
  sourceRef.current = source;
  /** 지금 레일의 카드 — 읽음 처리가 «읽기 전» 값을 보고 숫자를 고친다. */
  const held = useRef(cards);
  held.current = cards;

  const loadList = useCallback(async (mode: "first" | "quiet" = "first") => {
    const wanted = sourceRef.current;
    if (mode === "first") setState("loading");
    try {
      const page = await listInbox({ source: wanted });
      if (sourceRef.current !== wanted) return;
      setCards(page.items ?? []);
      setCursor(page.next_cursor ?? null);
      setCounts(page.unread_counts ?? null);
      setState("ready");
    } catch {
      if (mode === "first") setState("error");
    }
  }, []);

  const loadIntegrations = useCallback(async () => {
    try {
      setIntegrations(await listIntegrations());
    } catch {
      /* 배너는 보조 정보다 — 못 읽으면 배너만 안 선다 */
    }
  }, []);

  useEffect(() => {
    void loadList("first");
  }, [loadList, source]);

  useEffect(() => {
    void loadIntegrations();
  }, [loadIntegrations]);

  useEffect(() => {
    if (!onRegisterRefresh) return;
    onRegisterRefresh(() => loadList("quiet"));
    return () => onRegisterRefresh(null);
  }, [loadList, onRegisterRefresh]);

  /* 실시간 — 새 메시지는 목록을 다시 읽고(짧게 모아서), 사건은 열린 본문에도 나눠 준다 */
  const refreshTimer = useRef<number | null>(null);
  const onEvent = useCallback(
    (event: InboxStreamEvent) => {
      if (event.type === "inbox.message_arrived") {
        if (refreshTimer.current !== null) window.clearTimeout(refreshTimer.current);
        refreshTimer.current = window.setTimeout(() => void loadList("quiet"), 400);
      }
      if (event.type === "integration.changed") void loadIntegrations();
      hub.emit(event);
    },
    [hub, loadIntegrations, loadList],
  );
  useInboxStream(onEvent, { onReconnect: () => void loadList("quiet") });
  useEffect(() => () => {
    if (refreshTimer.current !== null) window.clearTimeout(refreshTimer.current);
  }, []);

  const loadMore = useCallback(async () => {
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const page = await listInbox({ source: sourceRef.current, cursor });
      setCards((current) => [...current, ...(page.items ?? []).filter((item) => !current.some((known) => cardKey(known) === cardKey(item)))]);
      setCursor(page.next_cursor);
    } catch {
      onError(copy.listError);
    } finally {
      setLoadingMore(false);
    }
  }, [cursor, onError]);

  /* 읽음 — 화면을 먼저 고치고 서버에 알린다. 실패하면 알리고 다시 읽는다(카드 숫자를 서버 값으로 되돌린다). */
  const readMail = useCallback(
    (messageId: string) => {
      const target = held.current.find((card) => card.kind === "mail" && card.message_id === messageId);
      setCards((current) => current.map((card) => (card.kind === "mail" && card.message_id === messageId ? { ...card, unread: false } : card)));
      if (target && target.kind === "mail" && target.unread) {
        setCounts((current) => (current ? { ...current, mail: Math.max(0, (current.mail ?? 0) - 1), all: Math.max(0, (current.all ?? 0) - 1) } : current));
      }
      markInboxMailRead(messageId).catch(() => {
        onError(copy.readFailed);
        void loadList("quiet");
      });
    },
    [loadList, onError],
  );
  const readRoom = useCallback(
    (roomId: string, upTo: string) => {
      const target = held.current.find((card) => card.kind !== "mail" && card.room_id === roomId);
      setCards((current) => current.map((card) => (card.kind !== "mail" && card.room_id === roomId ? { ...card, unread_count: 0 } : card)));
      if (target && target.kind !== "mail" && target.unread_count > 0) {
        const was = target.kind;
        setCounts((current) => (current ? { ...current, [was]: Math.max(0, (current[was] ?? 0) - 1), all: Math.max(0, (current.all ?? 0) - 1) } : current));
      }
      markInboxRoomRead(roomId, upTo).catch(() => {
        onError(copy.readFailed);
        void loadList("quiet");
      });
    },
    [loadList, onError],
  );
  const readAll = useCallback(async () => {
    const scope = sourceRef.current;
    setCards((current) => current.map((card) => (card.kind === "mail" ? { ...card, unread: false } : { ...card, unread_count: 0 })));
    setCounts((current) => (scope === "all" ? { ...zeroCounts } : current ? { ...current, [scope]: 0, all: Math.max(0, (current.all ?? 0) - (current[scope] ?? 0)) } : current));
    try {
      await markInboxAllRead(scope);
    } catch {
      onError(copy.readFailed);
      void loadList("quiet");
    }
  }, [loadList, onError]);

  const selectedKey = selected ? cardKey(selected) : null;
  /* 레일 숫자 = 미읽음 «카드» 수(§2.1). 서버가 세어 주면 그것을, 아니면 지금 목록에서 센다. */
  const unread = counts?.[source] ?? cards.filter((card) => (card.kind === "mail" ? card.unread : card.unread_count > 0)).length;

  useEffect(() => {
    if (!onRegisterRails) return;
    onRegisterRails({
      left: (
        <MessageRail
          cards={cards}
          hasMore={Boolean(cursor)}
          loadingMore={loadingMore}
          onMore={() => void loadMore()}
          onRetry={() => void loadList("first")}
          onSelect={setSelected}
          onSource={setSource}
          selectedKey={selectedKey}
          source={source}
          state={state}
          unread={unread}
        />
      ),
    });
    return () => onRegisterRails({});
  }, [cards, cursor, loadList, loadMore, loadingMore, onRegisterRails, selectedKey, source, state, unread]);

  useEffect(() => {
    if (!onRegisterHeaderActions) return;
    onRegisterHeaderActions(state === "ready" ? <Button label={copy.readAll} onClick={() => void readAll()} size="sm" /> : null);
    return () => onRegisterHeaderActions(null);
  }, [onRegisterHeaderActions, readAll, state]);

  /* 수집 실패 배너(D-50) — 끊긴 메일·슬랙 연동마다 한 줄 + 「다시 연결」 */
  const broken = integrations.filter((item) => item.status === "disconnected" && item.kind !== "kakao");
  const reconnect = async (integration: Integration) => {
    try {
      const outcome = await beginConsent(await reconnectIntegration(integration.id));
      if (outcome === "failed") onError(copy.reconnectFailed);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : copy.reconnectFailed);
    }
  };

  let body: ReactNode;
  if (state === "loading" && !selected) {
    body = (
      <div className="scax-inbox-main">
        <div aria-busy="true" className="scax-inbox-skel" role="status">
          <span className="sr-only">{copy.loadingList}</span>
          <span className="scax-skeleton scax-skeleton--title scax-skeleton--w-60" />
          {[0, 1, 2, 3, 4].map((index) => (
            <span className={`scax-skeleton scax-skeleton--text scax-skeleton--w-${index % 2 ? "80" : "full"}`} key={index} />
          ))}
        </div>
      </div>
    );
  } else if (state === "error" && !selected) {
    body = (
      <div className="scax-inbox-main scax-inbox-main--empty">
        <Empty actionLabel={copy.retry} description={copy.retryDesc} onAction={() => void loadList("first")} title={copy.bodyError} variant="error" />
      </div>
    );
  } else if (!selected) {
    body = (
      <div className="scax-inbox-main scax-inbox-main--empty">
        <Empty description={copy.noneDesc} title={copy.noneTitle} />
      </div>
    );
  } else if (selected.kind === "mail") {
    body = <MailView card={selected} hub={hub} key={selectedKey} onRead={readMail} />;
  } else {
    body = <RoomView card={selected} hub={hub} key={selectedKey} meName={meName} onRead={readRoom} />;
  }

  return (
    <div className="scax-inbox-body">
      {broken.length ? (
        <div className="scax-inbox-banners">
          {broken.map((item) => (
            <div className="scax-inbox-notice scax-inbox-notice--danger" key={item.id} role="alert">
              <span className="scax-inbox-notice__text">{copy.brokenBanner(item.kind === "slack" ? copy.slackKind : copy.mailAccount(item.display_name))}</span>
              <Button label={copy.reconnect} onClick={() => void reconnect(item)} size="sm" tone="neutral" variant="outlined" />
            </div>
          ))}
        </div>
      ) : null}
      {body}
    </div>
  );
}
