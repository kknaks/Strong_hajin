import { eventStreamUrl, getSession } from "./api";
import type { InboxStreamEvent, NotificationReadEvent, NotificationUpsertedEvent } from "./viewModels";

/**
 * 사용자 사건 채널 `/api/events/stream`(SSE)의 **연결 하나** — 주인은 앱 전역이다(SPEC-011 §4.1-6 · D-10).
 *
 * 화면이 바뀌어도 닫지 않고, 로그아웃·세션 상실 때만 닫는다. React 를 모른다 — `EventStreamProvider`
 * (`lib/eventStreamContext.tsx`)가 로그인한 동안 하나를 세우고, 화면은 그 통을 사건 이름으로 구독한다.
 *
 * ## 닫힘 처리 (§4.1-6 · F-1)
 * 화면은 비-200 의 상태 코드를 볼 수 없다. 그래서 `error` 에서 `readyState` 로만 가른다:
 * - `CONNECTING` — 네트워크 오류 뒤 브라우저가 스스로 다시 붙는 중이다(`Last-Event-ID` 머리가 실린다). 그대로 두되
 *   **30초** 넘게 이어지면 버리고 아래로 간다
 * - `CLOSED` — 비-200 을 받아 브라우저가 포기했다. 세션 확인(`GET /api/auth/me`)으로 가른다:
 *   `401` 이면 세션 상실(다시 붙지 않는다 — 로그인 화면으로 가는 것은 이때뿐) · 아니면 **백오프**(1→2→4…30초 · ±20%)
 *   뒤 새 `EventSource(…?last_event_id=)`
 * - **멈춤** — 세션 확인이 `200` 인데 스트림만 **연속 10회** 실패하면 빠른 재연결을 멈추고 5분마다 한 번 · 화면이
 *   다시 보이거나 창이 포커스를 받으면 바로 다시 시도한다. 세션 확인이 실패하는 동안(서버 장애)은 세지 않는다
 *
 * ## 다시 읽기 신호 (§4.1-2·3 ⑤ · R-F1 · R3-W2)
 * 구독자에게 `resync` 를 낸다 — 받은 화면은 지금까지의 「다시 붙으면 다시 읽기」(옛 `onReconnect`)를 한다.
 * **한 연결에서 한 번만**: 마지막 순번을 싣고 붙은 연결은 서버가 이어 받기 뒤 `resync` 를 보내므로 그때 한 번,
 * 순번 없이 다시 붙은 연결은 서버가 `resync` 를 못 내므로 **둘째 이후 `ready`** 에서 한 번. 같은 연결에 서버
 * `resync`(`replay_overflow` · `dropped`)가 또 오면 그때도 낸다.
 *
 * ## 이미 받은 알림 거르기 (§4.1-3 3-1)
 * 겹침 창으로 다시 온 `notification.upserted` 는 (알림 id · 순번) 쌍으로 걸러 구독자에게 다시 내지 않는다(이 연결 통의 수명 동안).
 */

export const INBOX_EVENT_NAMES = ["inbox.message_arrived", "inbox.reply_result", "integration.changed", "inbox.message_updated"] as const;

export type EventChannelSignal =
  /** 연결이 섰다. `first` = 이 앱 세션의 첫 `ready`(점 다시 읽기 자리 — WP3). */
  | { kind: "ready"; first: boolean }
  /** 다시 읽어라 — 서버 `resync` 이거나, 순번 없이 다시 붙은 연결의 둘째 이후 `ready`. */
  | { kind: "resync"; reason: string }
  | { kind: "inbox"; event: InboxStreamEvent }
  | { kind: "notification.upserted"; event: NotificationUpsertedEvent; seq: number }
  | { kind: "notification.read"; event: NotificationReadEvent };

export type SessionVerdict = "ok" | "lost" | "unknown";

export type EventChannelOptions = {
  /** 구독자에게 신호를 낸다. */
  emit: (signal: EventChannelSignal) => void;
  /** 세션 확인이 `401` 이었다 — App 의 세션 상실 처리(회의 스트림 4401 과 같은 길). */
  onSessionLost: () => void;
  /** 기본은 `GET /api/auth/me`: 200 → ok · 401 → lost · 그 밖(네트워크 · 5xx) → unknown. */
  checkSession?: () => Promise<SessionVerdict>;
  /** 백오프 흔들림의 난수(시험이 고정한다). */
  random?: () => number;
};

export type EventChannel = { start: () => void; stop: () => void };

export const BACKOFF_BASE_MS = 1_000;
export const BACKOFF_MAX_MS = 30_000;
export const BACKOFF_JITTER = 0.2;
export const CONNECTING_GIVE_UP_MS = 30_000;
export const PAUSE_AFTER_FAILURES = 10;
export const SLOW_RETRY_MS = 5 * 60_000;

/* `EventSource.CONNECTING` · `CLOSED` — 시험 대역이 정적 상수를 갖지 않아도 되게 숫자로 본다. */
const CONNECTING = 0;
const CLOSED = 2;

async function defaultCheckSession(): Promise<SessionVerdict> {
  try {
    return (await getSession()) ? "ok" : "lost";
  } catch {
    return "unknown";
  }
}

function parse(message: Event): unknown {
  try {
    return JSON.parse(String((message as MessageEvent).data));
  } catch {
    return null;
  }
}

export function createEventChannel(options: EventChannelOptions): EventChannel {
  const checkSession = options.checkSession ?? defaultCheckSession;
  const random = options.random ?? Math.random;
  const emit = (signal: EventChannelSignal) => {
    try {
      options.emit(signal);
    } catch {
      /* 구독자 하나가 채널을 죽이지 않는다 */
    }
  };

  let stopped = true;
  let source: EventSource | null = null;
  /** 받은 마지막 순번 — 화면이 새 연결을 만들 때 `?last_event_id=` 로 싣는다. */
  let lastSeq: number | null = null;
  const seen = new Set<string>();
  let readyCount = 0;
  /** 지금 연결이 마지막 순번을 싣고 붙었나 — 쿼리로 실었거나, 이 인스턴스가 `id:` 를 받아 브라우저가 머리로 싣는다. */
  let urlCarriedSeq = false;
  let instanceGotId = false;
  let attempt = 0;
  let streamFailures = 0;
  let paused = false;
  let retryTimer: number | null = null;
  let connectingTimer: number | null = null;

  const clearRetry = () => {
    if (retryTimer !== null) window.clearTimeout(retryTimer);
    retryTimer = null;
  };
  const clearConnecting = () => {
    if (connectingTimer !== null) window.clearTimeout(connectingTimer);
    connectingTimer = null;
  };

  const abandon = (es: EventSource) => {
    es.close();
    if (source === es) source = null;
    clearConnecting();
  };

  const backoff = () => {
    const base = Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** attempt);
    attempt += 1;
    return Math.round(base * (1 + (random() * 2 - 1) * BACKOFF_JITTER));
  };

  const schedule = (delay: number) => {
    clearRetry();
    retryTimer = window.setTimeout(() => {
      retryTimer = null;
      open();
    }, delay);
  };

  const handleClosed = async () => {
    const verdict = await checkSession();
    if (stopped || source) return;
    if (verdict === "lost") {
      stop();
      options.onSessionLost();
      return;
    }
    if (verdict === "ok") {
      streamFailures += 1;
      if (streamFailures >= PAUSE_AFTER_FAILURES) {
        paused = true;
        schedule(SLOW_RETRY_MS);
        return;
      }
    }
    schedule(backoff());
  };

  const own = (es: EventSource, handler: (message: Event) => void) => (message: Event) => {
    if (source === es && !stopped) handler(message);
  };

  function open() {
    clearRetry();
    if (stopped || source || typeof EventSource === "undefined") return;
    urlCarriedSeq = lastSeq !== null;
    instanceGotId = false;
    let es: EventSource;
    try {
      es = new EventSource(eventStreamUrl(lastSeq));
    } catch {
      schedule(backoff());
      return;
    }
    source = es;
    es.addEventListener("open", own(es, clearConnecting));
    es.addEventListener(
      "ready",
      own(es, () => {
        clearConnecting();
        attempt = 0;
        streamFailures = 0;
        paused = false;
        readyCount += 1;
        emit({ kind: "ready", first: readyCount === 1 });
        if (readyCount > 1 && !(urlCarriedSeq || instanceGotId)) emit({ kind: "resync", reason: "reconnected" });
      }),
    );
    es.addEventListener(
      "resync",
      own(es, (message) => {
        const data = parse(message) as { reason?: unknown } | null;
        emit({ kind: "resync", reason: typeof data?.reason === "string" ? data.reason : "reconnected" });
      }),
    );
    es.addEventListener(
      "notification.upserted",
      own(es, (message) => {
        const data = parse(message) as NotificationUpsertedEvent | null;
        const id = (message as MessageEvent).lastEventId;
        if (id) instanceGotId = true;
        const seq = Number(id || data?.notification?.seq);
        if (!data?.notification || !Number.isFinite(seq)) return;
        lastSeq = lastSeq === null ? seq : Math.max(lastSeq, seq);
        const key = `${data.notification.notification_id}:${seq}`;
        if (seen.has(key)) return;
        seen.add(key);
        emit({ kind: "notification.upserted", event: data, seq });
      }),
    );
    es.addEventListener(
      "notification.read",
      own(es, (message) => {
        const data = parse(message) as NotificationReadEvent | null;
        if (data) emit({ kind: "notification.read", event: data });
      }),
    );
    for (const name of INBOX_EVENT_NAMES) {
      es.addEventListener(
        name,
        own(es, (message) => {
          const data = parse(message) as Partial<InboxStreamEvent> | null;
          if (!data || typeof data !== "object") return;
          emit({ kind: "inbox", event: { ...data, type: typeof data.type === "string" ? data.type : name } as InboxStreamEvent });
        }),
      );
    }
    es.onerror = () => {
      if (source !== es || stopped) return;
      if (es.readyState === CONNECTING) {
        if (connectingTimer === null) {
          connectingTimer = window.setTimeout(() => {
            connectingTimer = null;
            if (source !== es || stopped) return;
            abandon(es);
            void handleClosed();
          }, CONNECTING_GIVE_UP_MS);
        }
        return;
      }
      if (es.readyState === CLOSED) {
        abandon(es);
        void handleClosed();
      }
    };
  }

  /* 멈춘 동안 화면이 다시 보이거나 창이 포커스를 받으면 바로 다시 시도한다(§4.1-6 4). */
  const wake = () => {
    if (stopped || !paused || source) return;
    if (document.visibilityState === "hidden") return;
    open();
  };

  function start() {
    if (!stopped) return;
    stopped = false;
    document.addEventListener("visibilitychange", wake);
    window.addEventListener("focus", wake);
    open();
  }

  function stop() {
    stopped = true;
    clearRetry();
    clearConnecting();
    document.removeEventListener("visibilitychange", wake);
    window.removeEventListener("focus", wake);
    if (source) {
      source.close();
      source = null;
    }
  }

  return { start, stop };
}

/** 구독 통 — 연결이 서기 전에도 구독할 수 있게 연결과 따로 산다. */
export type EventHub = {
  subscribe: (listener: (signal: EventChannelSignal) => void) => () => void;
  emit: (signal: EventChannelSignal) => void;
};

export function createEventHub(): EventHub {
  const listeners = new Set<(signal: EventChannelSignal) => void>();
  return {
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    emit(signal) {
      [...listeners].forEach((listener) => listener(signal));
    },
  };
}
