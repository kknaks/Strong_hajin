import { describe, expect, it, vi } from "vitest";

import {
  SettledSegmentBuffer,
  provisionalText,
  startLiveTranscription,
  type LiveSocketHandlers,
  type SettledSegment,
  type SonioxToken,
} from "./liveTranscription";
import type { MeetingRealtimeCredential } from "./viewModels";

const token = (text: string, start: number, end: number, is_final: boolean, speaker = "1"): SonioxToken => ({
  text,
  start_ms: start,
  end_ms: end,
  is_final,
  speaker,
});

describe("settled segments", () => {
  it("writes down only what the provider settled on, and keeps the tail out of the transcript", () => {
    const buffer = new SettledSegmentBuffer();
    const settled = buffer.accept([token("안녕하세요.", 0, 900, true), token("일정", 900, 1_200, false)]);
    expect(settled).toEqual([
      { source_segment_key: "live:1:0", start_ms: 0, end_ms: 900, text: "안녕하세요.", speaker_label: "Speaker 1" },
    ]);
    expect(provisionalText([token("안녕하세요.", 0, 900, true), token("일정", 900, 1_200, false)])).toBe("일정");
  });

  it("closes a segment when the speaker changes, so two people never share one line", () => {
    const buffer = new SettledSegmentBuffer();
    expect(buffer.accept([token("네", 0, 300, true, "1"), token("맞습니다", 400, 900, true, "1")])).toEqual([]);
    const [first] = buffer.accept([token("저는", 1_000, 1_400, true, "2")]);
    expect(first).toMatchObject({ text: "네맞습니다", speaker_label: "Speaker 1", start_ms: 0, end_ms: 900 });
    expect(buffer.close()).toEqual([
      { source_segment_key: "live:2:1000", start_ms: 1_000, end_ms: 1_400, text: "저는", speaker_label: "Speaker 2" },
    ]);
  });

  it("does not hold a long speaker back until they happen to finish a sentence", () => {
    const buffer = new SettledSegmentBuffer();
    const talking = Array.from({ length: 20 }, (_, index) => token("말", index * 1_000, index * 1_000 + 900, true));
    const segments = buffer.accept(talking);
    expect(segments).toHaveLength(1);
    expect(segments[0].end_ms - segments[0].start_ms).toBeGreaterThanOrEqual(15_000);
  });

  it("never emits an empty span the server would refuse", () => {
    const buffer = new SettledSegmentBuffer();
    buffer.accept([token(" ", 500, 500, true)]);
    const [only] = buffer.accept([token("음.", 500, 500, true)]);
    expect(only).toMatchObject({ start_ms: 500, end_ms: 501, text: "음." });
    expect(buffer.close()).toEqual([]);
  });
});

const credential: MeetingRealtimeCredential = {
  temporary_key: "temp-key",
  expires_at: "2026-09-10T02:00:00Z",
  client_reference_id: "meeting-recording:r1",
  websocket_url: "wss://stt-rt.example/transcribe-websocket",
  model: "stt-rt-v5",
  enable_speaker_diarization: true,
};

function fakeStack() {
  const sent: Array<string | Blob | ArrayBufferLike> = [];
  const appended: SettledSegment[][] = [];
  let handlers: LiveSocketHandlers | null = null;
  let emitChunk: ((chunk: Blob) => void) | null = null;
  return {
    sent,
    appended,
    get handlers() {
      return handlers!;
    },
    chunk(bytes: string) {
      emitChunk!(new Blob([bytes]));
    },
    options: {
      credential,
      append: async (segments: SettledSegment[]) => {
        appended.push(segments);
      },
      openSocket: (_url: string, socketHandlers: LiveSocketHandlers) => {
        handlers = socketHandlers;
        return {
          send: (data: string | Blob | ArrayBufferLike) => {
            sent.push(data);
            // Like the real provider: an empty frame ends the stream and is answered with a finished message.
            if (data === "") queueMicrotask(() => handlers?.onMessage(JSON.stringify({ tokens: [], finished: true })));
          },
          close: () => sent.push("<closed>"),
        };
      },
      startCapture: async (onChunk: (chunk: Blob) => void) => {
        emitChunk = onChunk;
        return { stop: async () => new Blob(["audio"], { type: "audio/webm" }) };
      },
    },
  };
}

describe("live transcription session", () => {
  it("opens with the restricted key, holds audio until the provider is listening, and writes settled speech down", async () => {
    const stack = fakeStack();
    const session = await startLiveTranscription(stack.options);

    stack.chunk("before-open");
    expect(stack.sent).toHaveLength(0);

    stack.handlers.onOpen();
    expect(JSON.parse(stack.sent[0] as string)).toEqual({
      api_key: "temp-key",
      model: "stt-rt-v5",
      audio_format: "auto",
      language_hints: ["ko"],
      enable_speaker_diarization: true,
    });
    expect(stack.sent[1]).toBeInstanceOf(Blob);

    stack.handlers.onMessage(JSON.stringify({ tokens: [token("안녕하세요.", 0, 900, true), token("다음", 900, 1_100, false)] }));
    await vi.waitFor(() => expect(stack.appended).toHaveLength(1));
    expect(stack.appended[0][0]).toMatchObject({ text: "안녕하세요.", start_ms: 0, end_ms: 900 });

    stack.handlers.onMessage(JSON.stringify({ tokens: [token("다음 주에 봅니다", 900, 2_000, true)] }));
    const audio = await session.stop();
    expect(stack.sent.at(-2)).toBe("");
    expect(stack.sent.at(-1)).toBe("<closed>");
    expect(stack.appended.flat().map((segment) => segment.text)).toEqual(["안녕하세요.", "다음 주에 봅니다"]);
    expect(audio.type).toBe("audio/webm");
  });

  it("keeps the recording when the live reading fails, and says so once", async () => {
    const stack = fakeStack();
    const errors: string[] = [];
    const session = await startLiveTranscription({
      ...stack.options,
      append: async () => {
        throw new Error("실시간 전사를 저장하지 못했습니다");
      },
      onError: (message) => errors.push(message),
    });
    stack.handlers.onOpen();
    stack.handlers.onMessage(JSON.stringify({ tokens: [token("첫 문장.", 0, 900, true)] }));
    await vi.waitFor(() => expect(errors).toHaveLength(1));
    stack.handlers.onMessage(JSON.stringify({ tokens: [token("두 번째 문장.", 900, 1_800, true)] }));

    const audio = await session.stop();
    // The live reading stopped; the audio the authoritative transcript is made from did not.
    expect(errors).toEqual(["실시간 전사를 저장하지 못했습니다"]);
    expect([audio.size, audio.type]).toEqual([5, "audio/webm"]);
  });

  it("reports a provider error instead of writing its message down as speech", async () => {
    const stack = fakeStack();
    const errors: string[] = [];
    await startLiveTranscription({ ...stack.options, onError: (message) => errors.push(message) });
    stack.handlers.onOpen();
    stack.handlers.onMessage(JSON.stringify({ tokens: [], error_code: 503, error_type: "service_unavailable", error_message: "no" }));
    expect(errors).toEqual(["실시간 전사가 중단되었습니다: service_unavailable"]);
    expect(stack.appended).toEqual([]);
  });
});
