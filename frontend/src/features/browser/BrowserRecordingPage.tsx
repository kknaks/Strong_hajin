import { useEffect, useRef, useState } from 'react';
import { getBrowserInteraction, interruptBrowserInteraction, startBrowserRecording, stopBrowserRecording, type BrowserInteraction } from '../../lib/api';
import { startBufferedAudioCapture, type LiveTranscriptionSession } from './liveTranscription';
import { acquireWakeGuard, newWakeSession, releaseWakeGuard } from '../../lib/shell';
import { meetingScreen } from '../../lib/labels';

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
  /* 절전 방지 점유의 **마지막 확인값**. 한 번 뜨고 마는 알림이 아니다.
     null 이면 아무것도 보이지 않는다 — 셸이 없을 때(`E-01`)와 잘 걸렸을 때가 여기 속한다. */
  const [wakeGuard, setWakeGuard] = useState<null | 'degraded' | 'cleanupFailed'>(null);
  /* 이 회차의 **점유 키**. `captureId` 와 일부러 갈라 둔다(리뷰 W-1).
     `captureId` 는 서버가 녹음 회차를 짚는 값이라 마운트당 하나여야 하는데, `start()` 가
     `startBrowserRecording` 에서 던지면 상태가 `waiting` 으로 남아 **같은 창에서 다시
     시작**할 수 있다. 그때 점유 키까지 같으면 1회차의 늦은 해제가 2회차를 푸는
     길이 열린다(`E-08` 보호가 얇아진다). 그래서 점유 키는 **마이크가 열릴 때마다 새로** 뽑는다. */
  const wakeSession = useRef<string | null>(null);
  /* 언마운트 뒤 늦게 도착한 응답이 상태를 오염시키지 않게 한다(리뷰 W-4).
     회의 경로(`stream.ts`)의 `stopped` 가드와 규칙을 맞춘다.

     **본문에서 `true` 로 되돌리는 것이 핵심이다**(리뷰 R-1). React 18 개발 StrictMode 는 같은
     인스턴스에서 effect 를 mount→cleanup→mount 로 두 번 돌린다. 정리에서 `false` 로만 내리면
     모의 언마운트 뒤 **영구히 `false`** 가 되어, 개발 빌드에서 U-3·정리 실패 한 줄이 아예
     뜨지 않는다. 회의 경로는 `stopped` 가 effect 지역 변수라 이 문제가 없다. */
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const blocked = busy || session !== null;

  /** **마이크가 «실제로» 열린 뒤에만** 부른다(L-07). 회차마다 **새 키**를 뽑는다(W-1). */
  const holdWake = async () => {
    const session = newWakeSession();
    wakeSession.current = session;
    const outcome = await acquireWakeGuard(session);
    // 늦게 온 응답이 언마운트·다음 회차를 덮지 않는다(W-4).
    if (!mounted.current || wakeSession.current !== session) return;
    // 셸 부재(`E-01`)는 조용하다. `degraded`·호출 실패(`E-14a`)는 **녹음을 막지 않고** 한 줄만 낸다.
    setWakeGuard(outcome.kind === 'degraded' ? 'degraded' : null);
  };

  /** **녹음이 «실제로» 끝난 자리에서만** 부른다(L-08). 업로드 재시도 중에는 유지한다. */
  const dropWake = async () => {
    const session = wakeSession.current;
    if (!session) return; // 마이크가 열린 적이 없다 — 걸지 않았으니 풀 것도 없다.
    // 키를 **먼저** 비운다 — 두 번 도는 정리에서도 같은 키를 두 번 풀지 않는다(멱등 · `E-08`).
    wakeSession.current = null;
    const outcome = await releaseWakeGuard(session);
    /* `E-14b` — 이미 끝난 녹음을 **다시 켜지 않는다.** 사실은 `shell.ts` 가 이미 기록했고
       (언마운트 뒤에도 남는다 — W-3), 화면이 살아 있을 때만 한 줄을 띄운다(W-4). */
    if (outcome.kind === 'failed' && mounted.current) setWakeGuard('cleanupFailed');
  };

  useEffect(() => {
    if (!blocked) return;
    const guard = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, [blocked]);
  // 창을 떠나며 마이크를 닫는 자리 — 녹음이 «실제로» 끝나므로 점유도 여기서 푼다(L-08).
  // 참조를 고정해 두 번 도는 정리에서도 같은 키로만 푼다(멱등 — `E-08`).
  const dropWakeRef = useRef(dropWake);
  dropWakeRef.current = dropWake;
  useEffect(() => () => {
    void capture.current?.stop().catch(() => {});
    void dropWakeRef.current();
  }, []);

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
          // 서버가 «끝났다»고 말했고 마이크도 닫았다 — 그 시점이 해제 시점이다(L-08).
          await dropWakeRef.current();
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
      /* **L-07 — 마이크가 «실제로» 열린 바로 이 시점**에 건다.
         위 `await` 가 던지면 여기까지 오지 않는다 → 권한 거부·미지원에서 **걸지 않는다**. */
      void holdWake();
      const active = await startBrowserRecording(item.interaction_id, captureId);
      onChange(active);
      setSession(microphone);
    } catch (failure) {
      await microphone?.stop().catch(() => {});
      capture.current = null;
      /* 마이크는 열렸는데 «녹음 시작»이 실패했다 — 방금 그 마이크를 닫았으므로 점유도 푼다.
         획득이 아직 날아가는 중이어도 `shell.ts` 가 같은 키의 호출을 한 줄로 세워
         **해제가 획득 뒤에 가고 최종 잔존이 0** 이 된다. */
      await dropWake();
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
      // 업로드가 끝났다 — 여기가 해제 시점이다(L-08).
      await dropWake();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '녹음 업로드에 실패했습니다.');
      try {
        const current = await getBrowserInteraction(item.interaction_id);
        onChange(current);
        // 업로드가 실패해도 오디오가 남아 **재시도할 수 있으면 점유를 유지한다**(L-08).
        // 서버가 이미 완료로 보고 있다면 그 녹음은 끝난 것이다.
        if (current.status === 'completed') { setSession(null); capture.current = null; await dropWake(); }
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
      // 취소·실패로 끝났다 — 더 이상 재시도하지 않으므로 해제한다(L-08).
      await dropWake();
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
    {/* SPEC-006 U-3 — 이미 상태 문구를 내는 자리에 **같은 한 줄**을 낸다.
        녹음을 막지 않는 안내이고, 점유가 잘 걸렸거나 셸이 없으면 보이지 않는다. */}
    {wakeGuard === 'degraded' && <p role="status">{meetingScreen.wakeGuardDegraded}</p>}
    {wakeGuard === 'cleanupFailed' && <p role="status">{meetingScreen.wakeGuardCleanupFailed}</p>}
    {error && <p role="alert">{error}</p>}
    <button disabled={blocked} onClick={onClose}>업무 화면으로</button>
  </section></main>;
}
