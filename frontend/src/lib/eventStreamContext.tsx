import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";

import { createEventChannel, createEventHub, type EventChannelSignal, type EventHub } from "./eventStream";

/**
 * 사건 채널의 주인 — App 이 **로그인한 동안** 이것으로 앱을 감싼다(SPEC-011 §4.1-6 · D-10).
 *
 * 연결은 이 Provider 가 사는 동안 하나다: 화면이 바뀌어도 그대로이고, 로그아웃·세션 상실로 App 이 로그인 화면을
 * 그리면 Provider 가 내려가며 닫힌다. 화면은 `useEventStream`(또는 메시지함 모양의 `useInboxStream`)으로 구독만 한다.
 */
const EventStreamContext = createContext<EventHub | null>(null);

export function EventStreamProvider({ onSessionLost, children }: { onSessionLost: () => void; children: ReactNode }) {
  const [hub] = useState(createEventHub);
  const lost = useRef(onSessionLost);
  lost.current = onSessionLost;
  useEffect(() => {
    const channel = createEventChannel({ emit: hub.emit, onSessionLost: () => lost.current() });
    channel.start();
    return () => channel.stop();
  }, [hub]);
  return <EventStreamContext.Provider value={hub}>{children}</EventStreamContext.Provider>;
}

/** 전역 연결의 신호를 듣는다. Provider 밖(로그인 전 · 단독 시험)에서는 아무것도 하지 않는다. */
export function useEventStream(listener: (signal: EventChannelSignal) => void) {
  const hub = useContext(EventStreamContext);
  const current = useRef(listener);
  current.current = listener;
  useEffect(() => hub?.subscribe((signal) => current.current(signal)), [hub]);
}
