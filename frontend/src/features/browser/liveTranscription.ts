export type AudioCapture = { stop(): Promise<Blob> };

export type AudioCaptureFactory = (onChunk: (chunk: Blob) => void) => Promise<AudioCapture>;

export type LiveTranscriptionSession = AudioCapture;

const browserCapture: AudioCaptureFactory = async (onChunk) => {
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
    throw new Error('이 환경에서는 마이크 녹음을 지원하지 않습니다.');
  }
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  try {
    const supported = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'].find(
      (type) => MediaRecorder.isTypeSupported(type),
    );
    const recorder = new MediaRecorder(stream, supported ? { mimeType: supported } : undefined);
    const parts: Blob[] = [];
    recorder.ondataavailable = (event) => {
      if (event.data.size === 0) return;
      parts.push(event.data);
      onChunk(event.data);
    };
    // recorder 가 어떻게 끝나든 한 번은 도는 자리. 장치를 뽑거나 OS 가 권한을 회수하면
    // recorder 는 우리가 부르기 전에 이미 끝나 있을 수 있고, 그때 `stop()` 은 던진다.
    const ended = new Promise<void>((resolve) => {
      recorder.onstop = () => resolve();
    });
    recorder.start(250);
    let stopped: Promise<Blob> | null = null;
    return {
      stop: () => stopped ??= (async () => {
        try {
          if (recorder.state !== 'inactive') {
            recorder.stop();
            await ended;
          }
        } catch {
          // 이미 끝난 장치다. 받아 둔 오디오는 그대로 쓴다 — 여기서 던지면 녹음이 통째로 사라진다.
        } finally {
          // 마이크는 어느 길로 와도 놓아준다.
          stream.getTracks().forEach((track) => track.stop());
        }
        return new Blob(parts, { type: recorder.mimeType || 'audio/webm' });
      })(),
    };
  } catch (error) {
    stream.getTracks().forEach((track) => track.stop());
    throw error;
  }
};

/** Acquire and buffer microphone audio before the server-side recording starts. */
export async function startBufferedAudioCapture(
  factory: AudioCaptureFactory = browserCapture,
): Promise<LiveTranscriptionSession> {
  return factory(() => {});
}
