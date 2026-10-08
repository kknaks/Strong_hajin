/**
 * `EventSource` 시험 대역 — 사용자 사건 채널(SSE · SPEC-011 §4.1)을 쓰는 시험이 같이 쓴다.
 * `vi.stubGlobal("EventSource", FakeEventSource)` 로 끼우고, 만든 인스턴스는 `FakeEventSource.all` 에 쌓인다.
 */
type Listener = (message: Event) => void;

export class FakeEventSource {
  static all: FakeEventSource[] = [];
  static latest(): FakeEventSource {
    const last = FakeEventSource.all.at(-1);
    if (!last) throw new Error("EventSource 가 아직 없다");
    return last;
  }

  readyState = 0;
  closed = false;
  onerror: ((event: Event) => void) | null = null;
  private listeners = new Map<string, Set<Listener>>();

  constructor(public url: string) {
    FakeEventSource.all.push(this);
  }

  addEventListener(name: string, listener: Listener) {
    if (!this.listeners.has(name)) this.listeners.set(name, new Set());
    this.listeners.get(name)!.add(listener);
  }

  removeEventListener(name: string, listener: Listener) {
    this.listeners.get(name)?.delete(listener);
  }

  close() {
    this.readyState = 2;
    this.closed = true;
  }

  /** 서버가 `event: <name>` 을 보냈다. `data` 는 JSON 으로 싣고, `id` 가 있으면 `lastEventId` 가 된다. */
  emit(name: string, data: unknown = {}, id = "") {
    const message = { type: name, data: JSON.stringify(data), lastEventId: id } as unknown as Event;
    this.listeners.get(name)?.forEach((listener) => listener(message));
  }

  /** 연결이 섰다 — `open` 뒤 서버의 첫 `event: ready`. */
  ready() {
    this.readyState = 1;
    this.listeners.get("open")?.forEach((listener) => listener({ type: "open" } as Event));
    this.emit("ready", { v: 1, server_time: "2026-10-08T00:00:00Z" });
  }

  /** `error` — `0`(CONNECTING · 브라우저가 스스로 다시 붙는 중) 또는 `2`(CLOSED · 비-200 으로 포기). */
  fail(state: 0 | 2) {
    this.readyState = state;
    this.onerror?.({ type: "error" } as Event);
  }
}
