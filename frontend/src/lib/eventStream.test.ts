import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  BACKOFF_MAX_MS,
  CONNECTING_GIVE_UP_MS,
  PAUSE_AFTER_FAILURES,
  SLOW_RETRY_MS,
  createEventChannel,
  type EventChannelSignal,
  type SessionVerdict,
} from "./eventStream";
import { FakeEventSource } from "./fakeEventSource.test-utils";

/*
 * WORK-013 WP1-FE — 사용자 사건 채널(SSE)의 연결 수명(SPEC-011 §4.1-6 닫힘 처리 · §4.1-3 ⑤ 다시 읽기 한 번).
 * 난수는 0.5 로 고정해 흔들림을 0 으로 둔다 — 백오프가 1s → 2s → 4s … 30s 그대로 보인다.
 */

let signals: EventChannelSignal[] = [];
let verdict: SessionVerdict = "ok";
let checkSession: ReturnType<typeof vi.fn>;
let onSessionLost: ReturnType<typeof vi.fn>;

function channel() {
  const made = createEventChannel({
    emit: (signal) => signals.push(signal),
    onSessionLost,
    checkSession,
    random: () => 0.5,
  });
  made.start();
  return made;
}

const kinds = (kind: EventChannelSignal["kind"]) => signals.filter((signal) => signal.kind === kind);
const flush = () => vi.advanceTimersByTimeAsync(0);
const upserted = (id: string, seq: number, replayed = false) => ({
  v: 1,
  notification: { notification_id: id, seq, kind: "work_request.received", summary: "", actor_id: "a", resource: { type: "work_request", id: "w", version: 1, title: "" }, created_at: "", read_at: null },
  created: !replayed,
  replayed,
});

beforeEach(() => {
  vi.useFakeTimers();
  signals = [];
  verdict = "ok";
  checkSession = vi.fn(async () => verdict);
  onSessionLost = vi.fn();
  FakeEventSource.all = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("사건 채널 — 연결과 사건", () => {
  it("연결 하나를 같은 origin `/api/events/stream` 으로 열고 · 메시지함 넷을 이름 그대로 내고 · 모르는 사건은 버린다", () => {
    const made = channel();
    expect(FakeEventSource.all).toHaveLength(1);
    const source = FakeEventSource.latest();
    expect(source.url).toBe("/api/events/stream");
    source.ready();
    expect(signals).toEqual([{ kind: "ready", first: true }]);

    source.emit("inbox.message_arrived", { v: 1, type: "inbox.message_arrived", member_id: "me", room_id: "r1" });
    source.emit("integration.changed", { v: 1, member_id: "me", integration_id: "i1" });
    source.emit("someday.new_kind", { v: 1 });
    expect(kinds("inbox").map((signal) => (signal.kind === "inbox" ? signal.event.type : ""))).toEqual(["inbox.message_arrived", "integration.changed"]);
    expect(kinds("resync")).toHaveLength(0);

    made.stop();
    expect(source.closed).toBe(true);
  });

  it("겹침 창으로 다시 온 (알림 id · 순번) 은 거른다 — 같은 id 라도 새 순번이면 낸다", () => {
    channel();
    const source = FakeEventSource.latest();
    source.ready();
    source.emit("notification.upserted", upserted("n1", 5), "5");
    source.emit("notification.upserted", upserted("n1", 5, true), "5");
    source.emit("notification.upserted", upserted("n1", 9), "9");
    expect(kinds("notification.upserted").map((signal) => (signal.kind === "notification.upserted" ? signal.seq : 0))).toEqual([5, 9]);
  });
});

describe("닫힘 처리 — 비-200 뒤 회복 (SPEC-011 §4.1-6)", () => {
  it("① CLOSED + 세션 확인 200 → 백오프 뒤 새 인스턴스 · 주소에 받은 마지막 순번 · 로그인 화면으로 가지 않는다", async () => {
    channel();
    const first = FakeEventSource.latest();
    first.ready();
    first.emit("notification.upserted", upserted("n1", 7), "7");
    first.fail(2);
    await flush();
    expect(checkSession).toHaveBeenCalledTimes(1);
    expect(FakeEventSource.all).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(999);
    expect(FakeEventSource.all).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(FakeEventSource.all).toHaveLength(2);
    expect(FakeEventSource.latest().url).toBe("/api/events/stream?last_event_id=7");
    expect(onSessionLost).not.toHaveBeenCalled();

    // 다음 실패는 2초 — 백오프가 늘고, 성공(`ready`)하면 0 으로
    FakeEventSource.latest().fail(2);
    await flush();
    await vi.advanceTimersByTimeAsync(1999);
    expect(FakeEventSource.all).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(FakeEventSource.all).toHaveLength(3);
    FakeEventSource.latest().ready();
    FakeEventSource.latest().fail(2);
    await flush();
    await vi.advanceTimersByTimeAsync(1000);
    expect(FakeEventSource.all).toHaveLength(4);
  });

  it("백오프는 30초에서 멈춘다 · 흔들림은 ±20%", async () => {
    const random = vi.fn(() => 1);
    createEventChannel({ emit: () => undefined, onSessionLost, checkSession: async () => "unknown", random }).start();
    for (let index = 0; index < 8; index += 1) {
      FakeEventSource.latest().fail(2);
      await flush();
      await vi.advanceTimersByTimeAsync(BACKOFF_MAX_MS * 1.2);
    }
    const before = FakeEventSource.all.length;
    FakeEventSource.latest().fail(2);
    await flush();
    await vi.advanceTimersByTimeAsync(BACKOFF_MAX_MS * 1.2 - 1);
    expect(FakeEventSource.all).toHaveLength(before);
    await vi.advanceTimersByTimeAsync(1);
    expect(FakeEventSource.all).toHaveLength(before + 1);
  });

  it("② CLOSED + 세션 확인 401 → 세션 상실 · 다시 붙지 않는다", async () => {
    verdict = "lost";
    channel();
    FakeEventSource.latest().ready();
    FakeEventSource.latest().fail(2);
    await flush();
    expect(onSessionLost).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(10 * 60_000);
    expect(FakeEventSource.all).toHaveLength(1);
  });

  it("③ CONNECTING 오류는 브라우저 재연결에 맡긴다 — 새 인스턴스 없음 · 30초 넘게 못 붙으면 버리고 세션을 확인한다", async () => {
    channel();
    const source = FakeEventSource.latest();
    source.ready();
    source.fail(0);
    await vi.advanceTimersByTimeAsync(CONNECTING_GIVE_UP_MS - 1);
    expect(FakeEventSource.all).toHaveLength(1);
    expect(checkSession).not.toHaveBeenCalled();
    expect(source.closed).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(source.closed).toBe(true);
    expect(checkSession).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1000);
    expect(FakeEventSource.all).toHaveLength(2);
  });

  it("③′ 브라우저가 30초 안에 다시 붙으면 버리지 않는다", async () => {
    channel();
    const source = FakeEventSource.latest();
    source.ready();
    source.fail(0);
    await vi.advanceTimersByTimeAsync(5_000);
    source.ready();
    await vi.advanceTimersByTimeAsync(CONNECTING_GIVE_UP_MS);
    expect(source.closed).toBe(false);
    expect(checkSession).not.toHaveBeenCalled();
  });
});

describe("다시 읽기는 한 연결에 한 번 (§4.1-3 ⑤ · R-F1 · R3-W2)", () => {
  it("④ 알림을 한 건도 받지 않은 채 끊겼다 붙으면 — 쿼리 없이 붙고 둘째 `ready` 에서 한 번 다시 읽는다", async () => {
    channel();
    FakeEventSource.latest().ready();
    expect(kinds("resync")).toHaveLength(0);
    FakeEventSource.latest().fail(2);
    await flush();
    await vi.advanceTimersByTimeAsync(1000);
    expect(FakeEventSource.latest().url).toBe("/api/events/stream");
    FakeEventSource.latest().ready();
    expect(kinds("resync")).toEqual([{ kind: "resync", reason: "reconnected" }]);
    expect(kinds("ready")).toEqual([
      { kind: "ready", first: true },
      { kind: "ready", first: false },
    ]);
  });

  it("④′ 브라우저가 스스로 다시 붙어도(같은 인스턴스 · 순번 없음) 둘째 `ready` 에서 한 번", () => {
    channel();
    const source = FakeEventSource.latest();
    source.ready();
    source.fail(0);
    source.ready();
    expect(kinds("resync")).toHaveLength(1);
  });

  it("⑥ 순번을 실은 재연결은 `ready` 가 아니라 서버 `resync` 에서만 다시 읽는다 · 같은 연결에 또 오면 따른다", async () => {
    channel();
    FakeEventSource.latest().ready();
    FakeEventSource.latest().emit("notification.upserted", upserted("n1", 3), "3");
    FakeEventSource.latest().fail(2);
    await flush();
    await vi.advanceTimersByTimeAsync(1000);
    const second = FakeEventSource.latest();
    expect(second.url).toBe("/api/events/stream?last_event_id=3");
    second.ready();
    expect(kinds("resync")).toHaveLength(0);
    second.emit("notification.upserted", upserted("n1", 3, true), "3");
    second.emit("resync", { v: 1, reason: "reconnected" });
    expect(kinds("resync")).toEqual([{ kind: "resync", reason: "reconnected" }]);
    expect(kinds("notification.upserted")).toHaveLength(1);
    second.emit("resync", { v: 1, reason: "dropped" });
    expect(kinds("resync")).toHaveLength(2);
  });

  it("⑥′ `id:` 를 받은 인스턴스가 브라우저 재연결로 붙으면(머리 `Last-Event-ID`) `ready` 에서 다시 읽지 않는다", () => {
    channel();
    const source = FakeEventSource.latest();
    source.ready();
    source.emit("notification.upserted", upserted("n2", 11), "11");
    source.fail(0);
    source.ready();
    expect(kinds("resync")).toHaveLength(0);
    source.emit("resync", { v: 1, reason: "reconnected" });
    expect(kinds("resync")).toHaveLength(1);
  });
});

describe("멈춤 (§4.1-6 4 · R-W6 · R3-W1)", () => {
  async function failOnce() {
    FakeEventSource.latest().fail(2);
    await flush();
  }

  it("⑤ 세션 확인 200 + 스트림 10회 실패 → 빠른 재연결을 멈추고 5분마다 · 화면 활성 · 포커스 때 바로 다시", async () => {
    channel();
    FakeEventSource.latest().ready();
    for (let index = 1; index < PAUSE_AFTER_FAILURES; index += 1) {
      await failOnce();
      await vi.advanceTimersByTimeAsync(BACKOFF_MAX_MS);
    }
    expect(FakeEventSource.all).toHaveLength(PAUSE_AFTER_FAILURES);
    await failOnce(); // 10번째
    await vi.advanceTimersByTimeAsync(SLOW_RETRY_MS - 1);
    expect(FakeEventSource.all).toHaveLength(PAUSE_AFTER_FAILURES);
    await vi.advanceTimersByTimeAsync(1);
    expect(FakeEventSource.all).toHaveLength(PAUSE_AFTER_FAILURES + 1);

    // 멈춘 채 또 실패 → 다시 5분 · 그 사이 화면이 보이면 바로
    await failOnce();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(FakeEventSource.all).toHaveLength(PAUSE_AFTER_FAILURES + 1);
    document.dispatchEvent(new Event("visibilitychange"));
    expect(FakeEventSource.all).toHaveLength(PAUSE_AFTER_FAILURES + 2);

    await failOnce();
    window.dispatchEvent(new Event("focus"));
    expect(FakeEventSource.all).toHaveLength(PAUSE_AFTER_FAILURES + 3);

    // 붙으면 세던 수가 0 — 다음 실패는 다시 빠른 백오프(1초)
    FakeEventSource.latest().ready();
    await failOnce();
    await vi.advanceTimersByTimeAsync(1000);
    expect(FakeEventSource.all).toHaveLength(PAUSE_AFTER_FAILURES + 4);
  });

  it("⑤′ 세션 확인이 실패하는 동안(서버 장애)은 세지 않는다 — 몇 번이든 멈추지 않고 백오프를 이어 간다", async () => {
    verdict = "unknown";
    channel();
    for (let index = 0; index < PAUSE_AFTER_FAILURES + 5; index += 1) {
      await failOnce();
      await vi.advanceTimersByTimeAsync(BACKOFF_MAX_MS);
    }
    expect(FakeEventSource.all).toHaveLength(PAUSE_AFTER_FAILURES + 6);
    expect(onSessionLost).not.toHaveBeenCalled();
  });

  it("멈추지 않은 동안 화면 활성 · 포커스는 연결을 더 만들지 않는다", () => {
    channel();
    FakeEventSource.latest().ready();
    document.dispatchEvent(new Event("visibilitychange"));
    window.dispatchEvent(new Event("focus"));
    expect(FakeEventSource.all).toHaveLength(1);
  });
});

describe("닫기", () => {
  it("stop 뒤에는 닫힌 연결의 오류로도 다시 붙지 않는다", async () => {
    const made = channel();
    const source = FakeEventSource.latest();
    source.ready();
    made.stop();
    source.fail(2);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(FakeEventSource.all).toHaveLength(1);
    expect(checkSession).not.toHaveBeenCalled();
  });

  it("EventSource 가 없는 환경에서는 아무것도 하지 않는다", () => {
    vi.stubGlobal("EventSource", undefined);
    const made = channel();
    expect(FakeEventSource.all).toHaveLength(0);
    made.stop();
  });
});
