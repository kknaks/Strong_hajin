import { describe, expect, it } from "vitest";
// 제품 소스를 **문자열로만** 읽는다(`?raw`). 모듈로 import 하면 계측 번들이 제품 코드를 끌어오고,
// node:fs 로 읽으면 이 저장소의 tsconfig 에 없는 node 타입이 필요해진다.
import microphoneSource from "../features/meetings/microphone.ts?raw";
import liveTranscriptionSource from "../features/browser/liveTranscription.ts?raw";
import {
  addChunk,
  BROWSER_RECORDING_CANDIDATES,
  CHUNK_MS,
  emptyStats,
  MEETING_LIVE_MIME,
  pickSupported,
  probeMimeSupport,
} from "./probeRecording";

describe("제품 포맷과의 drift (M-2 의 기준이 거짓이 되지 않게)", () => {
  // 계측 페이지는 제품 모듈을 import 하지 않는다(번들 경계). 대신 값이 어긋나면 여기서 붉어진다 —
  // 제품이 포맷을 바꿨는데 계측이 옛 값을 재면 **측정 기록 자체가 거짓**이 된다.
  it("회의 라이브의 단일 MIME 이 microphone.ts 와 같다", () => {
    expect(microphoneSource).toContain(`const MIME_TYPE = "${MEETING_LIVE_MIME}"`);
  });

  it("회의 라이브에 후보 목록이 생기지 않았다 — 생겼다면 중단 판정의 전제가 바뀐다", () => {
    expect(microphoneSource).not.toContain("isTypeSupported");
  });

  it("브라우저 인터랙션의 후보 셋과 순서가 liveTranscription.ts 와 같다", () => {
    const listed = /\[\s*(?:'audio\/[^']+'\s*,?\s*)+\]/.exec(liveTranscriptionSource);
    expect(listed).not.toBeNull();
    const found = Array.from(listed![0].matchAll(/'([^']+)'/g), (match) => match[1]);
    expect(found).toEqual([...BROWSER_RECORDING_CANDIDATES]);
  });

  it("청크 간격이 제품과 같다", () => {
    expect(microphoneSource).toContain(`export const CHUNK_MS = ${CHUNK_MS}`);
  });
});

describe("포맷 판정 (M-2)", () => {
  it("후보 중 첫 번째로 지원되는 것을 고른다", () => {
    expect(pickSupported(BROWSER_RECORDING_CANDIDATES, (mime) => mime === "audio/mp4")).toBe("audio/mp4");
    expect(pickSupported(BROWSER_RECORDING_CANDIDATES, () => true)).toBe("audio/webm;codecs=opus");
  });

  it("하나도 지원되지 않으면 null 이다 — 「아마 된다」로 메우지 않는다", () => {
    expect(pickSupported(BROWSER_RECORDING_CANDIDATES, () => false)).toBeNull();
  });

  it("두 경로를 따로 찍는다 — 브라우저가 열려도 회의가 닫혀 있을 수 있다", () => {
    // 실제로 갈릴 수 있는 조합이다: mp4 만 되는 웹뷰.
    const probe = probeMimeSupport((mime) => mime === "audio/mp4");
    expect(probe.meetingLive).toEqual({ mime: MEETING_LIVE_MIME, supported: false });
    expect(probe.browserChosen).toBe("audio/mp4");
    expect(probe.browserCandidates.map((row) => row.supported)).toEqual([false, false, true]);
  });
});

describe("장시간 녹음 계측", () => {
  it("청크 수·바이트·최대 간격을 사건으로만 쌓는다", () => {
    let stats = emptyStats("audio/webm;codecs=opus", 1_000);
    stats = addChunk(stats, 512, 1_250);
    stats = addChunk(stats, 512, 1_500);
    stats = addChunk(stats, 256, 3_000);
    expect(stats.chunks).toBe(3);
    expect(stats.bytes).toBe(1_280);
    // 250 · 250 · 1500 중 최대 — 장시간에 끊겼는지를 이 값으로 본다.
    expect(stats.maxGapMs).toBe(1_500);
  });

  it("첫 청크 간격은 시작 시각부터 잰다", () => {
    const stats = addChunk(emptyStats("audio/mp4", 1_000), 10, 1_400);
    expect(stats.maxGapMs).toBe(400);
  });
});
