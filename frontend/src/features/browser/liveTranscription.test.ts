import { afterEach, describe, expect, it, vi } from "vitest";

import { startBufferedAudioCapture } from "./liveTranscription";

/**
 * 녹음을 멈추는 길은 하나뿐이고, 그 길은 장치가 어떻게 끝나든 마이크를 놓아준다.
 * 장치를 뽑거나 OS 가 권한을 회수하면 recorder 는 이미 끝나 있을 수 있다 — 그때 stop() 이
 * 던지면 트랙 정리가 영영 돌지 않아 마이크가 잡힌 채 남고, 받아 둔 오디오도 회수할 수 없다.
 */
class FakeRecorder {
  static last: FakeRecorder | null = null;
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  state: "recording" | "inactive" = "recording";
  mimeType = "audio/webm";
  stopBehaviour: "normal" | "throws" = "normal";

  constructor() {
    FakeRecorder.last = this;
  }

  static isTypeSupported() {
    return true;
  }

  start() {}

  emit(chunk: Blob) {
    this.ondataavailable?.({ data: chunk });
  }

  stop() {
    if (this.stopBehaviour === "throws") throw new DOMException("already inactive", "InvalidStateError");
    this.state = "inactive";
    this.onstop?.();
  }
}

function stubDevice() {
  const tracks = [{ stop: vi.fn() }, { stop: vi.fn() }];
  vi.stubGlobal("MediaRecorder", FakeRecorder);
  Object.defineProperty(window.navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => tracks }) },
  });
  return tracks;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("버퍼링 오디오 캡처", () => {
  it("정상 종료는 받은 오디오를 돌려주고 마이크를 놓는다", async () => {
    const tracks = stubDevice();
    const capture = await startBufferedAudioCapture();
    FakeRecorder.last!.emit(new Blob(["a"]));

    const audio = await capture.stop();

    expect(audio.size).toBeGreaterThan(0);
    expect(tracks.every((track) => track.stop.mock.calls.length === 1)).toBe(true);
  });

  it("장치가 이미 끝났어도 마이크를 놓고 받아 둔 오디오를 돌려준다", async () => {
    const tracks = stubDevice();
    const capture = await startBufferedAudioCapture();
    FakeRecorder.last!.emit(new Blob(["a"]));
    // 장치 분리·권한 회수: recorder 는 우리가 부르기 전에 이미 끝났다.
    FakeRecorder.last!.state = "inactive";
    FakeRecorder.last!.stopBehaviour = "throws";

    const audio = await capture.stop();

    expect(audio.size).toBeGreaterThan(0);
    expect(tracks.every((track) => track.stop.mock.calls.length === 1)).toBe(true);
  });

  it("stop() 이 던져도 마이크는 잡힌 채 남지 않는다", async () => {
    const tracks = stubDevice();
    const capture = await startBufferedAudioCapture();
    FakeRecorder.last!.emit(new Blob(["a"]));
    FakeRecorder.last!.stopBehaviour = "throws";

    const audio = await capture.stop();

    expect(audio.size).toBeGreaterThan(0);
    expect(tracks.every((track) => track.stop.mock.calls.length === 1)).toBe(true);
  });
});
