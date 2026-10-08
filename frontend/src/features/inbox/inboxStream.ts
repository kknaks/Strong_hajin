import { useEventStream } from "../../lib/eventStreamContext";
import { openExternal } from "../../lib/shell";
import type { InboxStreamEvent } from "../../lib/viewModels";
import { safeHref } from "./inboxModel";

/**
 * 메시지함 사건 구독 (SPEC-008 §4.4 실시간 갱신 · AC-10b) — **앱 전역 사건 채널**(SSE `/api/events/stream`)을 듣는다.
 *
 * 연결은 이 훅이 만들지 않는다 — App 이 로그인한 동안 하나를 갖고(`lib/eventStreamContext.tsx` · SPEC-011 §4.1-6),
 * 화면이 떠나면 구독만 푼다. 사건은 «무엇이 바뀌었는지»만 싣는다(본문 없음) — 받는 쪽이 API 로 다시 읽는다.
 * 메시지함 사건 넷(`inbox.message_arrived` · `inbox.reply_result` · `integration.changed` · `inbox.message_updated`)만 `onEvent` 로 온다.
 *
 * `onReconnect` 는 채널의 **`resync`** 에 불린다 — 다시 붙은 연결에서 놓친 사건을 한 번 다시 읽어 메운다(한 연결에 한 번 · §4.1-3 ⑤).
 * Provider 밖(로그인 전 · 단독 시험)에서는 아무것도 하지 않는다.
 */
export function useInboxStream(onEvent: (event: InboxStreamEvent) => void, options: { enabled?: boolean; onReconnect?: () => void } = {}) {
  const enabled = options.enabled ?? true;
  useEventStream((signal) => {
    if (!enabled) return;
    if (signal.kind === "inbox") onEvent(signal.event);
    else if (signal.kind === "resync") options.onReconnect?.();
  });
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
