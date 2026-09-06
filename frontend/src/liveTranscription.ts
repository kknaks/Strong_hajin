import type { MeetingRealtimeCredential } from "./viewModels";

/**
 * Live transcription of a meeting, in the browser, while the recording is still open.
 *
 * Two readings of the same meeting are produced and they are not the same thing. This one streams the microphone to
 * the provider and writes down only what the provider has *settled* on, so a person can follow the room as it speaks.
 * The authoritative reading is made afterwards from the audio file itself and arrives as its own revision — so nothing
 * here has to be perfect, and nothing here is allowed to pretend it is final.
 *
 * The provider key never lives here: the server issues a short-lived key bound to this one recording, and that is the
 * only credential this module ever sees.
 */

export type SonioxToken = {
  text: string;
  start_ms: number;
  end_ms: number;
  is_final?: boolean;
  speaker?: string | null;
};

/** A stretch of settled speech, shaped the way the server's realtime endpoint accepts it. */
export type SettledSegment = {
  source_segment_key: string;
  start_ms: number;
  end_ms: number;
  text: string;
  speaker_label: string | null;
};

const SENTENCE_END = /[.!?…。！？]["'”’)\]]*$/;
/** A speaker who never stops still has to reach the page, so a segment closes on length as well as on punctuation. */
const MAX_SEGMENT_MS = 15_000;

function speakerOf(token: SonioxToken): string | null {
  const speaker = token.speaker?.trim();
  return speaker ? `Speaker ${speaker}` : null;
}

/**
 * Turns the provider's per-token stream into the segments the transcript is made of.
 *
 * Only settled tokens are gathered. A segment closes when the speaker changes, when the text finishes a sentence, or
 * when it has run long enough that holding it back would keep the room waiting.
 */
export class SettledSegmentBuffer {
  private tokens: SonioxToken[] = [];
  private closed = 0;

  accept(tokens: readonly SonioxToken[]): SettledSegment[] {
    const segments: SettledSegment[] = [];
    for (const token of tokens) {
      if (!token.is_final) continue;
      if (this.tokens.length > 0 && speakerOf(token) !== speakerOf(this.tokens[0])) segments.push(...this.take());
      this.tokens.push(token);
      if (SENTENCE_END.test(this.text()) || this.span() >= MAX_SEGMENT_MS) segments.push(...this.take());
    }
    return segments;
  }

  /** Whatever the stream settled on but had not yet closed — a meeting rarely ends on a full stop. */
  close(): SettledSegment[] {
    return this.take();
  }

  private text(): string {
    return this.tokens.map((token) => token.text).join("").trim();
  }

  private span(): number {
    if (this.tokens.length === 0) return 0;
    return this.tokens[this.tokens.length - 1].end_ms - this.tokens[0].start_ms;
  }

  private take(): SettledSegment[] {
    const text = this.text();
    const tokens = this.tokens;
    this.tokens = [];
    if (tokens.length === 0 || text.length === 0) return [];
    const start = Math.max(0, Math.min(...tokens.map((token) => token.start_ms)));
    const end = Math.max(...tokens.map((token) => token.end_ms));
    this.closed += 1;
    return [
      {
        source_segment_key: `live:${this.closed}:${start}`,
        start_ms: start,
        // The server refuses an empty span, and a single-token segment can report one.
        end_ms: end > start ? end : start + 1,
        text,
        speaker_label: speakerOf(tokens[0]),
      },
    ];
  }
}

/** The tail the provider is still changing its mind about. It is re-sent whole, so it replaces rather than appends. */
export function provisionalText(tokens: readonly SonioxToken[]): string {
  return tokens
    .filter((token) => !token.is_final)
    .map((token) => token.text)
    .join("")
    .trim();
}

export type LiveSocket = {
  send(data: string | ArrayBufferLike | Blob): void;
  close(): void;
};

export type LiveSocketHandlers = {
  onOpen: () => void;
  onMessage: (data: string) => void;
  onError: (message: string) => void;
};

export type LiveSocketFactory = (url: string, handlers: LiveSocketHandlers) => LiveSocket;

export type AudioCapture = { stop(): Promise<Blob> };

export type AudioCaptureFactory = (onChunk: (chunk: Blob) => void) => Promise<AudioCapture>;

export type LiveTranscriptionOptions = {
  credential: MeetingRealtimeCredential;
  languageHints?: string[];
  /** Write settled segments down. Rejecting stops the live reading; it never stops the recording. */
  append: (segments: SettledSegment[]) => Promise<unknown>;
  onSettled?: (segments: SettledSegment[]) => void;
  onProvisional?: (text: string) => void;
  onError?: (message: string) => void;
  openSocket?: LiveSocketFactory;
  startCapture?: AudioCaptureFactory;
};

export type LiveTranscriptionSession = {
  /** Stop capturing, let the provider settle its tail, write it down, and hand back the audio that was recorded. */
  stop(): Promise<Blob>;
};

const browserSocket: LiveSocketFactory = (url, handlers) => {
  const socket = new WebSocket(url);
  socket.binaryType = "arraybuffer";
  socket.onopen = () => handlers.onOpen();
  socket.onmessage = (event: MessageEvent) => handlers.onMessage(String(event.data));
  socket.onerror = () => handlers.onError("실시간 전사 연결이 끊어졌습니다.");
  return {
    send: (data) => socket.send(data as Parameters<WebSocket["send"]>[0]),
    close: () => socket.close(),
  };
};

const browserCapture: AudioCaptureFactory = async (onChunk) => {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const supported = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find(
    (type) => typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(type),
  );
  const recorder = new MediaRecorder(stream, supported ? { mimeType: supported } : undefined);
  const parts: Blob[] = [];
  recorder.ondataavailable = (event) => {
    if (event.data.size === 0) return;
    // The same bytes serve both readings: the provider hears them now, and the file is uploaded when recording stops.
    parts.push(event.data);
    onChunk(event.data);
  };
  recorder.start(250);
  return {
    stop: () =>
      new Promise<Blob>((resolve) => {
        recorder.onstop = () => {
          stream.getTracks().forEach((track) => track.stop());
          resolve(new Blob(parts, { type: recorder.mimeType || "audio/webm" }));
        };
        recorder.stop();
      }),
  };
};

export async function startLiveTranscription(options: LiveTranscriptionOptions): Promise<LiveTranscriptionSession> {
  const openSocket = options.openSocket ?? browserSocket;
  const startCapture = options.startCapture ?? browserCapture;
  const buffer = new SettledSegmentBuffer();
  const queue: SettledSegment[] = [];
  const pendingAudio: Blob[] = [];
  let open = false;
  let writing = true;
  let flushing: Promise<void> = Promise.resolve();
  let finished: (() => void) | null = null;

  function fail(message: string): void {
    if (!writing) return;
    // The live reading is a convenience; losing it costs nothing that the audio file will not recover.
    writing = false;
    options.onError?.(message);
  }

  async function drain(): Promise<void> {
    while (writing && queue.length > 0) {
      const batch = queue.splice(0, queue.length);
      try {
        await options.append(batch);
      } catch (error) {
        fail(error instanceof Error ? error.message : "실시간 전사를 저장하지 못했습니다.");
        return;
      }
      options.onSettled?.(batch);
    }
  }

  /** One writer at a time, so the transcript is written in the order it was spoken. */
  function flush(): Promise<void> {
    flushing = flushing.then(drain);
    return flushing;
  }

  const socket = openSocket(options.credential.websocket_url, {
    onOpen: () => {
      open = true;
      socket.send(
        JSON.stringify({
          api_key: options.credential.temporary_key,
          model: options.credential.model,
          audio_format: "auto",
          language_hints: options.languageHints ?? ["ko"],
          enable_speaker_diarization: options.credential.enable_speaker_diarization,
        }),
      );
      pendingAudio.splice(0, pendingAudio.length).forEach((chunk) => socket.send(chunk));
    },
    onMessage: (data) => {
      let message: { tokens?: SonioxToken[]; finished?: boolean; error_message?: string; error_type?: string };
      try {
        message = JSON.parse(data);
      } catch {
        fail("실시간 전사 응답을 이해하지 못했습니다.");
        return;
      }
      if (message.error_message || message.error_type) {
        fail(`실시간 전사가 중단되었습니다: ${message.error_type ?? message.error_message}`);
        return;
      }
      const tokens = message.tokens ?? [];
      queue.push(...buffer.accept(tokens));
      void flush();
      options.onProvisional?.(provisionalText(tokens));
      if (message.finished) finished?.();
    },
    onError: (message) => fail(message),
  });

  const capture = await startCapture((chunk) => {
    if (open) socket.send(chunk);
    else pendingAudio.push(chunk);
  });

  return {
    async stop(): Promise<Blob> {
      const audio = await capture.stop();
      const settled = new Promise<void>((resolve) => {
        finished = resolve;
        // The provider settles its tail after the stream ends; a silent provider must not hold up the upload.
        setTimeout(resolve, 5_000);
      });
      if (open) socket.send("");
      await settled;
      queue.push(...buffer.close());
      options.onProvisional?.("");
      await flush();
      socket.close();
      return audio;
    },
  };
}
