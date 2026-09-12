import type { AudioDeclaration } from "./stream";

/**
 * 내 마이크 하나만 캡처한다 — 시스템 오디오 캡처를 만들지 않는다 (SPEC §5.2-4).
 *
 * 형식은 **`MediaRecorder` 의 webm/opus** 를 쓴다. 이 선언이 첫 프레임의 `audio` 로 그대로 나가고
 * 서버가 provider 설정에 옮긴다 (§5.2-3) — 그래서 여기서 고른 것이 곧 계약이다.
 * `AudioWorklet` 로 16k PCM 을 뜨는 길도 있으나 worklet 모듈을 따로 실어야 하고, 데모에 필요한 것은
 * 「끊기지 않고 250ms 안쪽으로 올라간다」뿐이다. 형식이 바뀌면 이 상수 한 줄과 `mimeType` 만 바뀐다.
 */
export const AUDIO_DECLARATION: AudioDeclaration = { format: "webm/opus", sampleRate: 16000, channels: 1 };

/** 한 프레임 64KB 이하 · 250ms 이하 간격 (계약). */
export const MAX_CHUNK_BYTES = 64 * 1024;
export const CHUNK_MS = 250;

const MIME_TYPE = "audio/webm;codecs=opus";

export type MicrophoneHandle = { stop: () => void };

/** 64KB 를 넘는 청크는 잘라 보낸다 — 한 프레임 한도는 계약이다. */
function* slice(buffer: ArrayBuffer): Generator<ArrayBuffer> {
  for (let at = 0; at < buffer.byteLength; at += MAX_CHUNK_BYTES) {
    yield buffer.slice(at, Math.min(at + MAX_CHUNK_BYTES, buffer.byteLength));
  }
}

export async function startMicrophone(onChunk: (chunk: ArrayBuffer) => void): Promise<MicrophoneHandle> {
  const media = navigator.mediaDevices;
  if (!media?.getUserMedia) throw new Error("이 브라우저에서 마이크를 열 수 없습니다.");
  const stream = await media.getUserMedia({ audio: { channelCount: 1, sampleRate: AUDIO_DECLARATION.sampleRate } });
  const stop = () => {
    for (const track of stream.getTracks()) track.stop();
  };
  let recorder: MediaRecorder;
  try {
    recorder = new MediaRecorder(stream, { mimeType: MIME_TYPE });
  } catch (reason) {
    stop();
    throw reason;
  }
  recorder.ondataavailable = (event) => {
    if (!event.data || event.data.size === 0) return;
    void event.data.arrayBuffer().then((buffer) => {
      for (const chunk of slice(buffer)) onChunk(chunk);
    });
  };
  recorder.start(CHUNK_MS);
  return {
    stop: () => {
      if (recorder.state !== "inactive") recorder.stop();
      stop();
    },
  };
}
