import { useEffect, useRef } from "react";

import { inboxStreamUrl } from "../../lib/api";
import { openExternal } from "../../lib/shell";
import type { InboxStreamEvent } from "../../lib/viewModels";
import { safeHref } from "./inboxModel";

/**
 * 사용자 사건 채널 `/api/inbox/stream` 구독 (SPEC-008 §4.4 실시간 갱신 · AC-10b).
 *
 * 사건은 «무엇이 바뀌었는지»만 싣는다(본문 없음) — 받는 쪽이 API 로 다시 읽는다. 끊기면 물러서며 다시 붙는다
 * (2s → 4s → … 30s). 붙어 있는 동안 놓친 사건은 다시 붙을 때 `onReconnect` 로 한 번 다시 읽어 메운다.
 *
 * `WebSocket` 이 없는 환경(시험 일부)에서는 아무것도 하지 않는다.
 */
export function useInboxStream(onEvent: (event: InboxStreamEvent) => void, options: { enabled?: boolean; onReconnect?: () => void } = {}) {
  const handler = useRef(onEvent);
  handler.current = onEvent;
  const reconnect = useRef(options.onReconnect);
  reconnect.current = options.onReconnect;
  const enabled = options.enabled ?? true;

  useEffect(() => {
    if (!enabled || typeof WebSocket === "undefined") return;
    let socket: WebSocket | null = null;
    let timer: number | null = null;
    let attempt = 0;
    let closed = false;

    const connect = () => {
      try {
        socket = new WebSocket(inboxStreamUrl());
      } catch {
        schedule();
        return;
      }
      socket.onopen = () => {
        if (attempt > 0) reconnect.current?.();
        attempt = 0;
      };
      socket.onmessage = (message) => {
        try {
          const event = JSON.parse(String(message.data)) as InboxStreamEvent;
          if (event && typeof event.type === "string") handler.current(event);
        } catch {
          /* 모르는 글자는 버린다 — 채널 하나가 화면을 죽이지 않는다 */
        }
      };
      socket.onclose = () => {
        socket = null;
        if (!closed) schedule();
      };
    };
    const schedule = () => {
      attempt += 1;
      const delay = Math.min(30_000, 2_000 * 2 ** Math.min(attempt - 1, 4));
      timer = window.setTimeout(connect, delay);
    };
    connect();
    return () => {
      closed = true;
      if (timer !== null) window.clearTimeout(timer);
      if (socket) {
        socket.onclose = null;
        socket.close();
      }
    };
  }, [enabled]);
}

/** 사건을 여러 자식(대화방·메일·스레드)에 나눠 주는 작은 통 — 부모 하나가 WS 를 갖고 자식은 듣기만 한다. */
export type InboxEventHub = {
  subscribe: (listener: (event: InboxStreamEvent) => void) => () => void;
  emit: (event: InboxStreamEvent) => void;
};

export function createInboxEventHub(): InboxEventHub {
  const listeners = new Set<(event: InboxStreamEvent) => void>();
  return {
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    emit(event) {
      listeners.forEach((listener) => listener(event));
    },
  };
}

/**
 * 외부 링크 열기 — 데스크톱이면 `open_external` 로 OS 브라우저에, 브라우저면 새 탭(`noopener,noreferrer`)으로(§2.1 ⑦).
 * 셸이 있는데 실패하면 웹으로 폴백하지 않는다(U-4 — 앱 창 안에 두 번째 웹뷰가 앉는 사고).
 * **http · https · mailto 밖의 주소는 열지 않는다**(검수 F-1 — `javascript:` 가 우리 origin 에서 돌지 않게).
 * 돌려주는 값: 열었는가.
 */
export async function openLink(url: string): Promise<boolean> {
  const safe = safeHref(url);
  if (!safe) return false;
  const outcome = await openExternal(safe);
  if (outcome === "absent") window.open(safe, "_blank", "noopener,noreferrer");
  return outcome !== "failed";
}
