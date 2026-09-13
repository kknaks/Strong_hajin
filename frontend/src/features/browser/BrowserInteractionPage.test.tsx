import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { BrowserInteractionPage } from './BrowserInteractionPage';
import * as api from '../../lib/api';
import { startBufferedAudioCapture } from './liveTranscription';

vi.mock('../../lib/api', () => ({
  getBrowserInteraction: vi.fn(), uploadBrowserFile: vi.fn(), interruptBrowserInteraction: vi.fn(),
  startBrowserRecording: vi.fn(), stopBrowserRecording: vi.fn(),
}));
vi.mock('./liveTranscription', () => ({ startBufferedAudioCapture: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

const waiting = {
  interaction_id: 'i1', kind: 'file', intent: 'task_material',
  target: { type: 'task', id: 't1', title: '원래 업무', version: 1 },
  status: 'waiting', result: null, open_url: '/?interaction=i1', created_at: '', updated_at: '',
};

it('waits for selection and guards leaving until the upload receipt arrives', async () => {
  vi.mocked(api.getBrowserInteraction).mockResolvedValue(waiting);
  let finish!: (value: typeof waiting) => void;
  vi.mocked(api.uploadBrowserFile).mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  const close = vi.fn();
  render(<BrowserInteractionPage interactionId="i1" onClose={close} />);
  expect(await screen.findByText('파일 선택 대기')).toBeTruthy();
  expect(api.uploadBrowserFile).not.toHaveBeenCalled();
  const file = new File(['original'], 'source.txt', { type: 'text/plain' });
  fireEvent.change(screen.getByLabelText('첨부할 파일'), { target: { files: [file] } });
  fireEvent.click(screen.getByRole('button', { name: '선택한 파일 첨부' }));
  await waitFor(() => expect(api.uploadBrowserFile).toHaveBeenCalledWith('i1', file));
  const leaving = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(leaving);
  expect(leaving.defaultPrevented).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '업무 화면으로' }));
  expect(close).not.toHaveBeenCalled();
  finish({ ...waiting, status: 'completed' });
  expect(await screen.findByText('첨부 완료')).toBeTruthy();
});

it('restores a completed request without submitting another file', async () => {
  vi.mocked(api.getBrowserInteraction).mockResolvedValue({ ...waiting, status: 'completed' });
  render(<BrowserInteractionPage interactionId="i1" onClose={() => {}} />);
  expect(await screen.findByText('첨부 완료')).toBeTruthy();
  expect(screen.queryByLabelText('첨부할 파일')).toBeNull();
  expect(api.uploadBrowserFile).not.toHaveBeenCalled();
});

it('keeps cancellation distinct from success', async () => {
  vi.mocked(api.getBrowserInteraction).mockResolvedValue(waiting);
  vi.mocked(api.interruptBrowserInteraction).mockResolvedValue({ ...waiting, status: 'cancelled' });
  render(<BrowserInteractionPage interactionId="i1" onClose={() => {}} />);
  await screen.findByText('파일 선택 대기');
  fireEvent.click(screen.getByRole('button', { name: '요청 취소' }));
  expect(await screen.findByText('선택 취소')).toBeTruthy();
  expect(api.uploadBrowserFile).not.toHaveBeenCalled();
});

it('records cancellation when the native file dialog is dismissed', async () => {
  vi.mocked(api.getBrowserInteraction).mockResolvedValue(waiting);
  vi.mocked(api.interruptBrowserInteraction).mockResolvedValue({ ...waiting, status: 'cancelled' });
  render(<BrowserInteractionPage interactionId="i1" onClose={() => {}} />);
  const picker = await screen.findByLabelText('첨부할 파일');
  fireEvent(picker, new Event('cancel'));
  expect(await screen.findByText('선택 취소')).toBeTruthy();
  expect(api.uploadBrowserFile).not.toHaveBeenCalled();
});

it('resumes a reserved action file and explains that final approval still selects the material', async () => {
  vi.mocked(api.getBrowserInteraction).mockResolvedValue({ ...waiting, intent: 'action_material', status: 'uploading' });
  vi.mocked(api.uploadBrowserFile).mockResolvedValue({ ...waiting, intent: 'action_material', status: 'completed' });
  render(<BrowserInteractionPage interactionId="i1" onClose={() => {}} />);
  expect(await screen.findByText('업로드 복구 대기')).toBeTruthy();
  expect(api.uploadBrowserFile).not.toHaveBeenCalled();
  const file = new File(['original'], 'source.txt', { type: 'text/plain' });
  fireEvent.change(screen.getByLabelText('첨부할 파일'), { target: { files: [file] } });
  fireEvent.click(screen.getByRole('button', { name: '선택한 파일 첨부' }));
  expect(await screen.findByText('승인용 자료 준비 완료')).toBeTruthy();
  expect(screen.getByText('준비된 자료는 승인 화면에서 선택하고 최종 확인해야 연결됩니다.')).toBeTruthy();
  expect(api.uploadBrowserFile).toHaveBeenCalledExactlyOnceWith('i1', file);
});

it('starts recording only after microphone permission and completes only after uploading audio', async () => {
  const recording = { ...waiting, kind: 'recording', intent: 'meeting_recording', target: { ...waiting.target, type: 'meeting', id: 'm1' } };
  vi.mocked(api.getBrowserInteraction).mockResolvedValue(recording);
  const audio = new Blob(['recorded original'], { type: 'audio/webm' });
  vi.mocked(startBufferedAudioCapture).mockResolvedValue({ stop: vi.fn().mockResolvedValue(audio) });
  const active = { ...recording, status: 'recording', result: { recording_id: 'r1', meeting_id: 'm1', version: 1, state: 'recording' } };
  vi.mocked(api.startBrowserRecording).mockResolvedValue(active);
  vi.mocked(api.stopBrowserRecording).mockResolvedValue({ ...active, status: 'completed', result: { ...active.result, state: 'uploaded' } });
  render(<BrowserInteractionPage interactionId="i1" onClose={() => {}} />);
  const start = await screen.findByRole('button', { name: '마이크 허용하고 녹음 시작' });
  expect(startBufferedAudioCapture).not.toHaveBeenCalled();
  fireEvent.click(start);
  expect(await screen.findByText('녹음 중')).toBeTruthy();
  expect(api.startBrowserRecording).toHaveBeenCalledWith('i1', expect.any(String));
  expect(vi.mocked(startBufferedAudioCapture).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(api.startBrowserRecording).mock.invocationCallOrder[0]);
  fireEvent.click(screen.getByRole('button', { name: '녹음 종료하고 업로드' }));
  expect(await screen.findByText('녹음 업로드 완료')).toBeTruthy();
  expect(api.stopBrowserRecording).toHaveBeenCalledWith('i1', expect.any(String), audio);
  expect(screen.getByText('전사와 요약 결과는 회의 화면에서 확인할 수 있습니다.')).toBeTruthy();
});

it('saves microphone denial without calling recording start', async () => {
  const recording = { ...waiting, kind: 'recording', intent: 'meeting_recording' };
  vi.mocked(api.getBrowserInteraction).mockResolvedValue(recording);
  vi.mocked(startBufferedAudioCapture).mockRejectedValue(new DOMException('마이크 접근 거절', 'NotAllowedError'));
  vi.mocked(api.interruptBrowserInteraction).mockResolvedValue({ ...recording, status: 'denied' });
  render(<BrowserInteractionPage interactionId="i1" onClose={() => {}} />);
  fireEvent.click(await screen.findByRole('button', { name: '마이크 허용하고 녹음 시작' }));
  expect(await screen.findByText('권한 또는 마이크 사용 거절')).toBeTruthy();
  expect(api.startBrowserRecording).not.toHaveBeenCalled();
});

it('restores an active recording from another window without opening a new microphone', async () => {
  vi.mocked(api.getBrowserInteraction).mockResolvedValue({ ...waiting, kind: 'recording', intent: 'meeting_recording', status: 'recording' });
  render(<BrowserInteractionPage interactionId="i1" onClose={() => {}} />);
  expect(await screen.findByText('녹음을 시작한 창에서 종료해 주세요. 이 창에는 녹음 원본이 없습니다.')).toBeTruthy();
  expect(startBufferedAudioCapture).not.toHaveBeenCalled();
  expect(api.startBrowserRecording).not.toHaveBeenCalled();
});
