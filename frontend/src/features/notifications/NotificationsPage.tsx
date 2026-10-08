import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { Badge } from "../../ds/Badge";
import { Button } from "../../ds/Button";
import { Empty } from "../../ds/Empty";
import { Icon } from "../../ds/icons/Icon";
import { SegmentedControl } from "../../ds/SegmentedControl";
import { getNotifications, getNotificationSummary, markAllNotificationsRead } from "../../lib/api";
import { useEventStream } from "../../lib/eventStreamContext";
import {
  describeNotification,
  notificationDay,
  notificationRelationLabel,
  notificationScreen as copy,
  notificationThemeLabel,
  notificationTime,
  type NotificationPart,
} from "../../lib/labels";
import type { Notification, NotificationSummary, NotificationTheme } from "../../lib/viewModels";

/**
 * 「알림」 화면 (WORK-013 WP3-FE · SPEC-011 §2.1 — 확정 시안 `handoff/alerts/js/alerts.v1.jsx` 그대로).
 *
 * 머리 = 제목 「알림」 · 「안 읽음 N」 · [모두 읽음](전체 안 읽은 것이 없으면 비활성 — 필터와 무관 · D-40) /
 * 필터 `전체 · 업무 · 메시지 · 회의`(테마 탭에 안 읽은 수) / 레일 없는 한 단(880px) / 날짜 구분 넷(서울 기준).
 * 줄 = 테마 표식(실패는 붉게) · 문장 · 보조 줄 · 테마 → 꼬리표 → 시각 → 가는 곳 · 안 읽음 점.
 *
 * - **누르면** 화면은 그 줄을 읽음으로 그리고 `onOpen` 에 넘긴다 — 읽음 API · 대상 화면 열기 · 「열 수 없는 항목입니다」는
 *   App 의 `openNotification` 한 곳이 한다(OS 알림 클릭 — WP4 — 도 같은 자리다)
 * - **실시간**: `notification.upserted` 는 새 줄이면 맨 위에 끼우고 있는 줄이면 그 자리에서 고친다 · `notification.read` 반영 ·
 *   둘 다 요약(머리 수 · 탭 수)을 다시 읽는다 — 목록 전체는 다시 읽지 않는다. `resync` 면 목록과 요약을 다시 읽는다
 * - **이어 불러오기**(D-36): 스크롤 끝에 닿으면 다음 쪽을 자동으로 · 실패면 끝에 「더 불러오지 못했습니다 · 다시 시도」
 * - **상태 넷**: 기본 · 빈 · 로딩 · 오류 — 빈·로딩·오류에는 머리 「안 읽음」·「모두 읽음」·필터 수를 숨긴다
 */

type Filter = "all" | NotificationTheme;

const THEME_ICON = { work: "square-check", message: "inbox", meeting: "persons" } as const;

function Sentence({ parts }: { parts: NotificationPart[] }) {
  return (
    <span className="scax-alert__text">
      {parts.map((part, index) => {
        if (typeof part === "string") return <span key={index}>{part}</span>;
        if ("b" in part) return <strong className="scax-alert__who" key={index}>{part.b}</strong>;
        return <strong className="scax-alert__what" key={index}>‘{part.q}’</strong>;
      })}
    </span>
  );
}

function AlertRow({ item, now, onOpen }: { item: Notification; now: Date; onOpen: (item: Notification) => void }) {
  const said = describeNotification(item);
  const unread = !item.read_at;
  return (
    <li>
      <button
        className={`scax-alert${unread ? " scax-alert--unread" : ""}`}
        data-notification-id={item.notification_id}
        onClick={() => onOpen(item)}
        type="button"
      >
        <span aria-hidden="true" className={`scax-alert__mark scax-alert__mark--${item.failure ? "fail" : item.theme}`}>
          <Icon name={item.failure ? "circle-exclamation" : THEME_ICON[item.theme] ?? "inbox"} size={20} />
        </span>
        <span className="scax-alert__body">
          <Sentence parts={said.parts} />
          {said.sub ? <span className="scax-alert__sub">{said.sub}</span> : null}
          <span className="scax-alert__meta">
            <span className="scax-alert__theme">{notificationThemeLabel[item.theme]}</span>
            {notificationRelationLabel[item.relation] ? <span className="scax-alert__rel">{notificationRelationLabel[item.relation]}</span> : null}
            <span className="scax-alert__sep" />
            <span className="scax-alert__at">{notificationTime(item.updated_at || item.created_at, now)}</span>
            <span className="scax-alert__sep" />
            <span className="scax-alert__to">
              {said.destination}
              <Icon name="chevron-right" size={16} />
            </span>
          </span>
        </span>
        <span className="scax-alert__end">{unread ? <span aria-label={copy.unreadDot} className="scax-alert__dot" role="img" /> : null}</span>
      </button>
    </li>
  );
}

function Loading() {
  return (
    <div aria-busy="true" className="scax-alerts__skel" role="status">
      <span className="sr-only">{copy.loading}</span>
      <span aria-hidden className="scax-skeleton scax-skeleton--title scax-skeleton--w-40" />
      {[0, 1, 2, 3, 4, 5].map((index) => (
        <div aria-hidden className="scax-alerts__skel-row" key={index}>
          <span className="scax-alerts__skel-mark" />
          <div className="scax-skeleton-stack">
            <span className={`scax-skeleton scax-skeleton--text scax-skeleton--w-${index % 2 ? "80" : "full"}`} />
            <span className="scax-skeleton scax-skeleton--text scax-skeleton--w-40" />
          </div>
        </div>
      ))}
    </div>
  );
}

const unreadCount = (summary: NotificationSummary | null, items: Notification[], filter: Filter) =>
  summary ? summary.unread[filter] : items.filter((item) => !item.read_at && (filter === "all" || item.theme === filter)).length;

export function NotificationsPage({
  onOpen,
  onError,
  onReadChanged,
  onRegisterHeaderActions,
  onRegisterTitleEnd,
  now: fixedNow,
}: {
  /** 그 줄을 열어 달라 — 읽음 API · 대상 화면 · 「열 수 없는 항목입니다」는 App 이 한다. */
  onOpen: (item: Notification) => void;
  onError: (message: string | null) => void;
  /** 이 화면이 읽음 API 를 불렀다 — 사이드바 점을 다시 읽을 때(§2.2). */
  onReadChanged?: () => void;
  onRegisterHeaderActions?: (node: ReactNode) => void;
  onRegisterTitleEnd?: (node: ReactNode) => void;
  /** 시험용 — 날짜 구분 · 상대 시각의 「지금」. */
  now?: Date;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const [items, setItems] = useState<Notification[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [more, setMore] = useState<"idle" | "loading" | "error">("idle");
  const [summary, setSummary] = useState<NotificationSummary | null>(null);
  const filterRef = useRef<Filter>(filter);
  filterRef.current = filter;
  const now = fixedNow ?? new Date();

  const loadSummary = useCallback(async () => {
    try {
      const next = await getNotificationSummary();
      // 모양이 아니면(옛 서버 · 빈 응답) 지금 목록으로 센다
      setSummary(next && typeof next.unread === "object" && next.unread ? next : null);
    } catch {
      /* 머리 수는 보조 정보다 — 못 읽으면 지금 목록으로 센다 */
    }
  }, []);

  const loadList = useCallback(async (quiet = false) => {
    const wanted = filterRef.current;
    if (!quiet) setState("loading");
    setMore("idle");
    try {
      const page = await getNotifications({ theme: wanted === "all" ? null : wanted });
      if (filterRef.current !== wanted) return;
      setItems(Array.isArray(page?.items) ? page.items : []);
      setCursor(page?.next_cursor ?? null);
      setState("ready");
    } catch {
      if (filterRef.current === wanted && !quiet) setState("error");
    }
  }, []);

  useEffect(() => {
    void loadList();
  }, [filter, loadList]);

  useEffect(() => {
    void loadSummary();
  }, [loadSummary]);

  const loadingMore = useRef(false);
  const loadMore = useCallback(async () => {
    if (!cursor || loadingMore.current) return;
    const wanted = filterRef.current;
    loadingMore.current = true;
    setMore("loading");
    try {
      const page = await getNotifications({ theme: wanted === "all" ? null : wanted, cursor });
      if (filterRef.current !== wanted) return;
      setItems((current) => [...current, ...(page.items ?? []).filter((item) => !current.some((known) => known.notification_id === item.notification_id))]);
      setCursor(page.next_cursor ?? null);
      setMore("idle");
    } catch {
      if (filterRef.current === wanted) setMore("error");
    } finally {
      loadingMore.current = false;
    }
  }, [cursor]);

  /* 스크롤 끝 — 목록 끝의 표지가 보이면 다음 쪽(IntersectionObserver). 없는 환경은 스크롤 기둥의 끝 거리로 본다. */
  const scroller = useRef<HTMLDivElement | null>(null);
  const sentinel = useRef<HTMLDivElement | null>(null);
  const loadMoreRef = useRef(loadMore);
  loadMoreRef.current = loadMore;
  const canAutoLoad = state === "ready" && Boolean(cursor) && more === "idle";
  useEffect(() => {
    if (!canAutoLoad || typeof IntersectionObserver === "undefined" || !sentinel.current) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) void loadMoreRef.current();
    }, { root: scroller.current, rootMargin: "200px" });
    observer.observe(sentinel.current);
    return () => observer.disconnect();
  }, [canAutoLoad, items.length]);
  const onScroll = () => {
    if (!canAutoLoad || typeof IntersectionObserver !== "undefined") return;
    const box = scroller.current;
    if (box && box.scrollTop + box.clientHeight >= box.scrollHeight - 200) void loadMore();
  };

  /* 실시간 — 사건 채널(앱 전역 · SPEC-011 §4.1) */
  useEventStream((signal) => {
    if (signal.kind === "notification.upserted") {
      const incoming = signal.event.notification;
      const wanted = filterRef.current;
      if (wanted === "all" || incoming.theme === wanted) {
        setItems((current) => {
          const index = current.findIndex((item) => item.notification_id === incoming.notification_id);
          if (index < 0) return [incoming, ...current];
          const next = [...current];
          next[index] = incoming;
          return next;
        });
        setState((current) => (current === "loading" ? current : "ready"));
      }
      void loadSummary();
    } else if (signal.kind === "notification.read") {
      const readAt = new Date().toISOString();
      const ids = new Set(signal.event.notification_ids ?? []);
      const all = Boolean(signal.event.all);
      const theme = signal.event.theme ?? null;
      setItems((current) =>
        current.map((item) => (!item.read_at && (ids.has(item.notification_id) || (all && (!theme || item.theme === theme))) ? { ...item, read_at: readAt } : item)),
      );
      void loadSummary();
    } else if (signal.kind === "resync") {
      void loadList(true);
      void loadSummary();
    }
  });

  const open = (item: Notification) => {
    if (!item.read_at) {
      const readAt = new Date().toISOString();
      setItems((current) => current.map((row) => (row.notification_id === item.notification_id ? { ...row, read_at: readAt } : row)));
      setSummary((current) =>
        current
          ? { unread: { ...current.unread, all: Math.max(0, current.unread.all - 1), [item.theme]: Math.max(0, current.unread[item.theme] - 1) } }
          : current,
      );
    }
    onOpen(item);
  };

  /* ⚠ 부르는 쪽 콜백은 ref 로 든다 — 머리 액션 등록 effect 가 이 함수에 묶여 있어, 인라인 콜백이 매 렌더 새것이면
     등록 → App 상태 → 렌더 → 다시 등록 으로 무한히 돈다(`App.tsx` `canNavigate` 주석의 그 함정). */
  const callbacks = useRef({ onError, onReadChanged });
  callbacks.current = { onError, onReadChanged };
  const readAll = useCallback(async () => {
    try {
      await markAllNotificationsRead();
      const readAt = new Date().toISOString();
      setItems((current) => current.map((item) => (item.read_at ? item : { ...item, read_at: readAt })));
      setSummary({ unread: { all: 0, work: 0, message: 0, meeting: 0 } });
      callbacks.current.onReadChanged?.();
    } catch {
      callbacks.current.onError(copy.readAllError);
    }
  }, []);

  const empty = state === "ready" && items.length === 0;
  const headerShown = state === "ready" && !(empty && filter === "all");
  const totalUnread = unreadCount(summary, items, "all");

  useEffect(() => {
    if (!onRegisterHeaderActions) return;
    onRegisterHeaderActions(headerShown ? <Button disabled={totalUnread === 0} label={copy.readAll} onClick={() => void readAll()} size="sm" /> : null);
    return () => onRegisterHeaderActions(null);
  }, [headerShown, onRegisterHeaderActions, readAll, totalUnread]);

  useEffect(() => {
    if (!onRegisterTitleEnd) return;
    onRegisterTitleEnd(
      headerShown && totalUnread > 0 ? (
        <span className="scax-alerts__unread">
          {copy.unread} <Badge variant="count">{totalUnread}</Badge>
        </span>
      ) : null,
    );
    return () => onRegisterTitleEnd(null);
  }, [headerShown, onRegisterTitleEnd, totalUnread]);

  const options = copy.filters.map((option) => {
    const n = option.value === "all" || !headerShown ? 0 : unreadCount(summary, items, option.value);
    return { value: option.value as Filter, label: n ? `${option.label} ${n}` : option.label };
  });

  let body: ReactNode;
  if (state === "loading") body = <Loading />;
  else if (state === "error") {
    body = (
      <div className="scax-alerts__state">
        <Empty actionLabel={copy.retry} description={copy.retryDesc} onAction={() => void loadList()} title={copy.loadError} variant="error" />
      </div>
    );
  } else if (empty) {
    const byTheme = filter === "all" ? null : copy.emptyByTheme[filter];
    body = (
      <div className="scax-alerts__state">
        {byTheme ? <Empty description={byTheme.desc} icon="tune" title={byTheme.title} /> : <Empty description={copy.emptyDesc} icon="inbox" title={copy.emptyTitle} />}
      </div>
    );
  } else {
    body = (
      <>
        {copy.days.map((day) => {
          const list = items.filter((item) => notificationDay(item.updated_at || item.created_at, now) === day.key);
          if (!list.length) return null;
          return (
            <section aria-label={day.label} className="scax-alerts__day" key={day.key}>
              <h2 className="scax-alerts__day-title">{day.label}</h2>
              <ul className="scax-alerts__list">
                {list.map((item) => (
                  <AlertRow item={item} key={item.notification_id} now={now} onOpen={open} />
                ))}
              </ul>
            </section>
          );
        })}
        {more === "loading" ? <Loading /> : null}
        {more === "error" ? (
          <div className="scax-alerts__more">
            <Empty actionLabel={copy.retry} onAction={() => { setMore("idle"); void loadMore(); }} title={copy.moreError} variant="error" />
          </div>
        ) : null}
        {cursor ? <div aria-hidden className="scax-alerts__sentinel" data-testid="alerts-sentinel" ref={sentinel} /> : null}
      </>
    );
  }

  return (
    <div className="scax-alerts" onScroll={onScroll} ref={scroller}>
      <div className="scax-alerts__inner">
        <div className="scax-alerts__tools">
          <SegmentedControl ariaLabel={copy.filterAria} onChange={setFilter} options={options} value={filter} />
        </div>
        {body}
      </div>
    </div>
  );
}
