import { useEffect, useRef, useState } from 'react';
import { getBrowserInteraction, interruptBrowserInteraction, startBrowserRecording, stopBrowserRecording, type BrowserInteraction } from '../../lib/api';
import { startBufferedAudioCapture, type LiveTranscriptionSession } from './liveTranscription';

const labels: Record<string, string> = {
  waiting: '마이크 사용 대기', recording: '녹음 중', upload_failed: '녹음 업로드 실패', completed: '녹음 업로드 완료',
  denied: '권한 또는 마이크 사용 거절', cancelled: '녹음 요청 취소', failed: '녹음 중단 또는 실패', unsupported: '녹음을 지원하지 않는 환경',
};

export function BrowserRecordingPage({ item, onChange, onClose }: {
  item: BrowserInteraction; onChange: (item: BrowserInteraction) => void; onClose: () => void;
}) {
  const [captureId] = useState(() => crypto.randomUUID());
  const [session, setSession] = useState<LiveTranscriptionSession | null>(null);
  const capture = useRef<LiveTranscriptionSession | null>(null);
  const original = useRef<Blob | null>(null);
  const [audioStopped, setAudioStopped] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const blocked = busy || session !== null;

  useEffect(() => {
    if (!blocked) return;
    const guard = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, [blocked]);
  useEffect(() => () => { void capture.current?.stop().catch(() => {}); }, []);

  useEffect(() => {
    if (!session || busy) return;
    let active = true;
    const poll = setInterval(() => {
      void getBrowserInteraction(item.interaction_id).then(async (current) => {
        if (!active) return;
        onChange(current);
        if (!['recording', 'upload_failed'].includes(current.status)) {
          await session.stop();
          capture.current = null;
          if (active) setSession(null);
        }
      }).catch(() => {});
    }, 2000);
    return () => { active = false; clearInterval(poll); };
  }, [item.interaction_id, session, busy, onChange]);

  async function start() {
    if (busy || item.status !== 'waiting') return;
    setBusy(true); setError(null);
    let microphone: Awaited<ReturnType<typeof startBufferedAudioCapture>> | null = null;
    try {
      microphone = await startBufferedAudioCapture();
      capture.current = microphone;
      const active = await startBrowserRecording(item.interaction_id, captureId);
      onChange(active);
      setSession(microphone);
    } catch (failure) {
      await microphone?.stop().catch(() => {});
      capture.current = null;
      setError(failure instanceof Error || failure instanceof DOMException ? failure.message : '녹음을 시작하지 못했습니다.');
      try {
        if (!microphone) {
          const status = failure instanceof DOMException && failure.name === 'NotAllowedError' ? 'denied'
            : !navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined' ? 'unsupported' : 'failed';
          onChange(await interruptBrowserInteraction(item.interaction_id, status));
        } else onChange(await getBrowserInteraction(item.interaction_id));
      } catch { /* Keep the request visible; never claim it started. */ }
    } finally { setBusy(false); }
  }

  async function stop() {
    if (!session || busy) return;
    setBusy(true); setError(null);
    try {
      original.current ??= await session.stop();
      setAudioStopped(true);
      const result = await stopBrowserRecording(item.interaction_id, captureId, original.current);
      onChange(result); setSession(null); capture.current = null;
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '녹음 업로드에 실패했습니다.');
      try {
        const current = await getBrowserInteraction(item.interaction_id);
        onChange(current);
        if (current.status === 'completed') { setSession(null); capture.current = null; }
      } catch { /* The retained audio can still be retried from this window. */ }
    } finally { setBusy(false); }
  }

  async function interrupt(status: 'cancelled' | 'failed') {
    if (busy) return;
    setBusy(true);
    try {
      await capture.current?.stop();
      if (capture.current) setAudioStopped(true);
      const result = await interruptBrowserInteraction(item.interaction_id, status);
      onChange(result); setSession(null); capture.current = null;
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '녹음 요청을 중단하지 못했습니다.');
    } finally { setBusy(false); }
  }

  return <main className="login-shell"><section className="login-panel" aria-busy={busy}>
    <h1>회의 녹음</h1><h2>{item.target.title}</h2>
    <p role="status">{busy ? '처리 중…' : audioStopped && session ? '녹음 종료 · 업로드 대기' : labels[item.status] ?? item.status}</p>
    {item.status === 'waiting' && <>
      <button disabled={busy} onClick={() => { void start(); }}>마이크 허용하고 녹음 시작</button>
      <button disabled={busy} onClick={() => { void interrupt('cancelled'); }}>요청 취소</button>
    </>}
    {session && <>
      <button disabled={busy} onClick={() => { void stop(); }}>{audioStopped ? '녹음 업로드 다시 시도' : '녹음 종료하고 업로드'}</button>
      <button disabled={busy} onClick={() => { void interrupt('cancelled'); }}>녹음 중단</button>
    </>}
    {!session && ['recording', 'upload_failed'].includes(item.status) && <>
      <p>녹음을 시작한 창에서 종료해 주세요. 이 창에는 녹음 원본이 없습니다.</p>
      <button disabled={busy} onClick={() => { void interrupt('failed'); }}>이 녹음 중단</button>
    </>}
    {item.status === 'completed' && <p>전사와 요약 결과는 회의 화면에서 확인할 수 있습니다.</p>}
    {error && <p role="alert">{error}</p>}
    <button disabled={blocked} onClick={onClose}>업무 화면으로</button>
  </section></main>;
}
