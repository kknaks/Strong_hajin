import { useEffect, useState } from 'react';
import { getBrowserInteraction, interruptBrowserInteraction, uploadBrowserFile, type BrowserInteraction } from './api';
import { BrowserRecordingPage } from './BrowserRecordingPage';

const statusLabel: Record<string, string> = {
  waiting: '파일 선택 대기', completed: '첨부 완료', cancelled: '선택 취소',
  uploading: '업로드 복구 대기',
  denied: '권한 또는 장치 사용 거절', failed: '첨부 실패', unsupported: '지원하지 않는 환경',
};

export function BrowserInteractionPage({ interactionId, onClose }: { interactionId: string; onClose: () => void }) {
  const [item, setItem] = useState<BrowserInteraction | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void getBrowserInteraction(interactionId).then((result) => {
      if (active) setItem(result);
    }).catch((failure: unknown) => {
      if (active) setError(failure instanceof Error ? failure.message : '요청을 불러오지 못했습니다.');
    });
    return () => { active = false; };
  }, [interactionId]);

  useEffect(() => {
    if (!busy) return;
    const guard = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, [busy]);

  async function cancel() {
    if (busy || !item || !['waiting', 'uploading'].includes(item.status)) return;
    setBusy(true);
    try {
      setItem(await interruptBrowserInteraction(interactionId, 'cancelled'));
      setFile(null);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '취소하지 못했습니다.');
    } finally { setBusy(false); }
  }

  async function upload() {
    if (busy || !file || !item || !['waiting', 'uploading'].includes(item.status)) return;
    setBusy(true);
    setError(null);
    try {
      setItem(await uploadBrowserFile(interactionId, file));
      setFile(null);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '파일을 첨부하지 못했습니다.');
      // A lost response can follow a committed upload. Read the saved receipt before offering another action.
      try { setItem(await getBrowserInteraction(interactionId)); } catch { setItem(null); }
    } finally { setBusy(false); }
  }

  if (item?.kind === 'recording') return <BrowserRecordingPage item={item} onChange={setItem} onClose={onClose} />;

  return <main className="login-shell">
    <section className="login-panel" aria-busy={busy}>
      <h1>파일 첨부</h1>
      {item && <>
        <h2>{item.target.title}</h2>
        <p role="status">{busy ? '처리 중…' : item.status === 'completed' && item.intent === 'action_material' ? '승인용 자료 준비 완료' : statusLabel[item.status] ?? item.status}</p>
        {item.intent === 'action_material' && <p>준비된 자료는 승인 화면에서 선택하고 최종 확인해야 연결됩니다.</p>}
        {['waiting', 'uploading'].includes(item.status) && <>
          <p>{item.status === 'uploading' ? '선택했던 같은 파일을 다시 전송하면 저장된 예약을 이어서 처리합니다.' : '선택한 파일을 이 대상에 첨부합니다. 아직 첨부된 파일은 없습니다.'}</p>
          <label>첨부할 파일<input type="file" disabled={busy} ref={(element) => {
            if (!element) return;
            const cancelled = () => { void cancel(); };
            element.addEventListener('cancel', cancelled);
            return () => element.removeEventListener('cancel', cancelled);
          }}
            onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label>
          <button type="button" disabled={busy || !file} onClick={() => { void upload(); }}>선택한 파일 첨부</button>
          <button type="button" disabled={busy} onClick={() => { void cancel(); }}>요청 취소</button>
        </>}
      </>}
      {!item && !error && <p>요청을 불러오는 중…</p>}
      {error && <p role="alert">{error}</p>}
      <button type="button" disabled={busy} onClick={onClose}>업무 화면으로</button>
    </section>
  </main>;
}
