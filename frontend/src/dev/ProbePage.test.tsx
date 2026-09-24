import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RecordingPanel } from "./ProbePage";
import type { GuardClient, WakeState } from "./probeShell";

/**
 * 리뷰 W-2 의 회귀 시험.
 *
 * 계측 페이지의 존재 이유가 **M-2·M-4 를 «가르는» 것**인데, 마이크 실패(`E-13`)와
 * 커맨드 호출 실패(`E-14a`)를 한 `try` 로 묶으면 둘이 한 줄로 합쳐 기록된다.
 * 그러면 Phase 2 기록에 「마이크가 안 열렸다」가 남고 **중단 판정이 잘못 열린다.**
 */

type Track = { stop: ReturnType<typeof vi.fn> };

let tracks: Track[];
let recorderConstructed: number;
let recorderThrows: boolean;

class FakeRecorder {
  static isTypeSupported = () => true;
  state = "inactive";
  mimeType: string;
  ondataavailable: ((event: { data?: { size: number } }) => void) | null = null;

  constructor(_stream: unknown, options?: { mimeType?: string }) {
    recorderConstructed += 1;
    if (recorderThrows) throw new Error("이 웹뷰가 지원하지 않는 포맷");
    this.mimeType = options?.mimeType ?? "audio/webm";
  }
  start() {
    this.state = "recording";
  }
  stop() {
    this.state = "inactive";
  }
}

let getUserMedia: ReturnType<typeof vi.fn>;

beforeEach(() => {
  tracks = [{ stop: vi.fn() }];
  recorderConstructed = 0;
  recorderThrows = false;
  getUserMedia = vi.fn(async () => ({ getTracks: () => tracks }));
  vi.stubGlobal("MediaRecorder", FakeRecorder);
  Object.defineProperty(globalThis.navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia },
  });
});

afterEach(() => {
  // 이 저장소는 vitest `globals` 를 켜지 않아 자동 cleanup 이 돌지 않는다 —
  // 기존 테스트(`ds/Modal.test.tsx` 등)와 같게 직접 부른다.
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function guardWith(acquire: GuardClient["acquire"]): GuardClient {
  return { acquire, release: vi.fn(async () => ({ state: "off" as WakeState })) };
}

/** 로그를 모아 태그별로 되묻는다. */
function logSpy() {
  const lines: Array<{ tag: string; text: string }> = [];
  return {
    lines,
    onLog: (tag: string, text: string) => lines.push({ tag, text }),
    has: (needle: string) => lines.some((line) => line.text.includes(needle)),
  };
}

// 이 저장소의 기존 테스트가 쓰는 방식(`fireEvent`)을 그대로 따른다 —
// `user-event` 는 package.json 에 없는 전이 의존이라 기대지 않는다.
function startMeetingRecording(guard: GuardClient | null, onLog: (tag: string, text: string) => void) {
  render(<RecordingPanel guard={guard} onLog={onLog} />);
  fireEvent.click(screen.getByRole("button", { name: "회의 라이브 경로로 녹음 시작" }));
}

describe("W-2 · IPC 실패를 마이크 실패로 적지 않는다", () => {
  it("acquire 가 실패해도 E-14a 로 적고, E-13 으로 적지 않는다", async () => {
    const log = logSpy();
    const guard = guardWith(vi.fn(async () => {
      throw new Error("IPC 실패");
    }));

    startMeetingRecording(guard, log.onLog);

    await waitFor(() => expect(log.has("E-14a")).toBe(true));
    // 마이크는 실제로 열렸다 — 그 사실이 기록에서 뒤집히면 안 된다.
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(log.has("E-13")).toBe(false);
    expect(log.lines.some((line) => line.tag === "M-4")).toBe(false);
    // 녹음 시작 자체는 기록돼야 한다(M-2 의 값이 살아 있어야 한다).
    expect(log.has("녹음 시작")).toBe(true);
  });

  it("acquire 가 실패해도 화면은 마이크 실패가 아니라 점유 문제로 보인다", async () => {
    const log = logSpy();
    const guard = guardWith(vi.fn(async () => {
      throw new Error("IPC 실패");
    }));

    startMeetingRecording(guard, log.onLog);

    await waitFor(() => expect(screen.getByText(/점유 문제/)).toBeTruthy());
    expect(screen.queryByText(/마이크 실패\(E-13\)/)).toBeNull();
    // `E-14a` 는 **녹음을 계속한다** — 마이크를 되감지 않는다.
    expect(tracks[0].stop).not.toHaveBeenCalled();
  });

  it("degraded 는 실패가 아니라 상태로 보인다 — 녹음은 계속된다", async () => {
    const log = logSpy();
    const guard = guardWith(vi.fn(async () => ({ state: "degraded" as WakeState })));

    startMeetingRecording(guard, log.onLog);

    await waitFor(() => expect(screen.getByText(/degraded/)).toBeTruthy());
    expect(screen.queryByText(/마이크 실패\(E-13\)/)).toBeNull();
    expect(log.has("E-13")).toBe(false);
  });
});

describe("W-2 · 진짜 마이크 실패는 그대로 E-13 이다", () => {
  it("getUserMedia 가 거부되면 E-13 이고 점유를 걸지 않는다", async () => {
    const log = logSpy();
    const acquire = vi.fn(async () => ({ state: "on" as WakeState }));
    getUserMedia.mockRejectedValueOnce(new Error("NotAllowedError"));

    startMeetingRecording(guardWith(acquire), log.onLog);

    await waitFor(() => expect(log.has("E-13")).toBe(true));
    // L-03 — 녹음이 없는데 기기를 깨워 둘 이유가 없다.
    expect(acquire).not.toHaveBeenCalled();
    expect(log.has("E-14a")).toBe(false);
    expect(screen.getByText(/마이크 실패\(E-13\)/)).toBeTruthy();
  });

  it("포맷 미지원으로 MediaRecorder 가 던져도 E-13 이고 마이크를 놓아준다", async () => {
    const log = logSpy();
    const acquire = vi.fn(async () => ({ state: "on" as WakeState }));
    recorderThrows = true;

    startMeetingRecording(guardWith(acquire), log.onLog);

    await waitFor(() => expect(log.has("E-13")).toBe(true));
    expect(recorderConstructed).toBe(1);
    expect(acquire).not.toHaveBeenCalled();
    // 열었던 트랙을 놓아준다 — 안 놓으면 마이크가 켜진 채 남는다.
    expect(tracks[0].stop).toHaveBeenCalled();
  });
});

describe("W-2 · 셸이 없을 때(E-01)", () => {
  it("guard 가 없으면 녹음만 돌고 점유 관련 기록이 남지 않는다", async () => {
    const log = logSpy();
    startMeetingRecording(null, log.onLog);

    await waitFor(() => expect(log.has("녹음 시작")).toBe(true));
    expect(log.has("E-14a")).toBe(false);
    expect(log.has("E-13")).toBe(false);
    expect(screen.queryByText(/점유 문제/)).toBeNull();
  });
});
