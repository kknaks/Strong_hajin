/**
 * WORK-006 Phase 1 — **두 녹음 경로의 포맷 판정**(M-2)과 장시간 녹음 계측.
 *
 * WORK §Phase 2 측정 규칙: **두 경로를 뭉치지 않는다.** 실물이 다르다 —
 * - **회의 라이브**: `features/meetings/microphone.ts` 의 **단일 MIME 하드코딩**(후보 없음).
 *   WS 스트림으로 서버에 간다. 이 경로가 안 열리면 **Phase 2 는 중단 판정**이다
 * - **브라우저 인터랙션**: `features/browser/liveTranscription.ts` 가 **이미 후보 셋**을 갖고
 *   멀티파트 업로드로 간다. 여기만 내려앉는 것은 중단 사유가 아니다
 *
 * 값을 제품에서 `import` 하지 않고 **베껴 둔 뒤 drift 테스트로 묶는다.**
 * 계측 페이지가 제품 모듈을 끌어오면 번들 경계가 흐려지고, 그렇다고 손으로 베끼기만 하면
 * 제품이 포맷을 바꿨을 때 **측정이 조용히 거짓이 된다.** 그래서 옆자리 테스트가 파일을 읽어 맞춘다.
 */

/** `features/meetings/microphone.ts` 의 `MIME_TYPE`. 후보가 없다 — 이 하나뿐이다. */
export const MEETING_LIVE_MIME = "audio/webm;codecs=opus";

/** `features/browser/liveTranscription.ts` 의 후보 셋. **순서가 곧 우선순위**다. */
export const BROWSER_RECORDING_CANDIDATES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"] as const;

/** `features/meetings/microphone.ts` 의 청크 간격. 장시간 녹음 계측을 제품과 같은 조건으로 둔다. */
export const CHUNK_MS = 250;

export type MimeSupport = { mime: string; supported: boolean };

export type MimeProbe = {
  /** 회의 라이브 — 단일 MIME 이 열리는가. **여기가 거짓이면 중단 판정 후보다.** */
  meetingLive: MimeSupport;
  /** 브라우저 인터랙션 — 후보별 지원 여부와, 실제로 **어느 후보로 떨어지는가**. */
  browserCandidates: MimeSupport[];
  browserChosen: string | null;
};

export type IsTypeSupported = (mime: string) => boolean;

/** 후보 목록에서 **첫 번째로 지원되는 것**을 고른다 — 제품의 `find` 와 같은 규칙이다. */
export function pickSupported(candidates: readonly string[], isSupported: IsTypeSupported): string | null {
  return candidates.find((mime) => isSupported(mime)) ?? null;
}

export function probeMimeSupport(isSupported: IsTypeSupported): MimeProbe {
  return {
    meetingLive: { mime: MEETING_LIVE_MIME, supported: isSupported(MEETING_LIVE_MIME) },
    browserCandidates: BROWSER_RECORDING_CANDIDATES.map((mime) => ({ mime, supported: isSupported(mime) })),
    browserChosen: pickSupported(BROWSER_RECORDING_CANDIDATES, isSupported),
  };
}

export type RecordingStats = {
  /** `MediaRecorder` 가 **실제로** 쓴 MIME. 요청한 것과 다를 수 있어 따로 잰다. */
  actualMime: string;
  chunks: number;
  bytes: number;
  startedAt: number;
  lastChunkAt: number | null;
  /** 청크 사이 최대 간격(ms). 장시간에 끊겼는지 보는 값이다. */
  maxGapMs: number;
};

export function emptyStats(actualMime: string, now: number): RecordingStats {
  return { actualMime, chunks: 0, bytes: 0, startedAt: now, lastChunkAt: null, maxGapMs: 0 };
}

/** 청크 하나를 장부에 더한다. **타이머가 아니라 사건으로만 갱신된다.** */
export function addChunk(stats: RecordingStats, size: number, at: number): RecordingStats {
  const previous = stats.lastChunkAt ?? stats.startedAt;
  return {
    ...stats,
    chunks: stats.chunks + 1,
    bytes: stats.bytes + size,
    lastChunkAt: at,
    maxGapMs: Math.max(stats.maxGapMs, at - previous),
  };
}
