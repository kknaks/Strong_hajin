import assert from 'node:assert/strict';
import { chromium } from '@playwright/test';
import { loginAs } from './e2e-helpers.mjs';

const origin = process.env.SCAX_E2E_URL ?? 'http://127.0.0.1:15231';
const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  headless: true,
  args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'],
});

async function request(page, path, body) {
  return page.evaluate(async ({ path, body }) => {
    const response = await fetch(path, body === undefined ? undefined : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`);
    return response.json();
  }, { path, body });
}

try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await page.goto(origin);
  await loginAs(page, 'mina');
  const stamp = Date.now();
  const task = await request(page, '/api/tasks', { title: `화면 첨부 ${stamp}` });
  const fileRequest = await request(page, '/api/browser-interactions/files', {
    intent: 'task_material', target_id: task.task_id, request_key: `file-${stamp}`,
  });
  assert.equal(new URL(fileRequest.open_url).origin, origin);
  await page.goto(fileRequest.open_url);
  await page.getByText('파일 선택 대기', { exact: true }).waitFor();
  await page.getByLabel('첨부할 파일').setInputFiles({ name: 'source.txt', mimeType: 'text/plain', buffer: Buffer.from('browser original') });
  await page.getByRole('button', { name: '선택한 파일 첨부' }).click();
  await page.getByText('첨부 완료', { exact: true }).waitFor();
  await page.reload();
  await page.getByText('첨부 완료', { exact: true }).waitFor();
  assert.equal((await request(page, `/api/tasks/${task.task_id}/materials`)).length, 1);

  const starts = new Date(Date.now() + 60_000);
  const meeting = await request(page, '/api/meetings', {
    organization_id: 'scax', title: `화면 녹음 ${stamp}`, visibility: 'private',
    starts_at: starts.toISOString(), ends_at: new Date(starts.getTime() + 1800_000).toISOString(),
  });
  const recordingRequest = await request(page, '/api/browser-interactions/recordings', {
    target_id: meeting.meeting_id, purpose: '브라우저 캡처 검증', request_key: `record-${stamp}`,
  });
  // This journey verifies audio preservation when the optional live provider is unavailable.
  // Real Soniox transcript/refinement/summary acceptance is the separate provider journey.
  await page.route('**/realtime-credential', (route) => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '검증용 실시간 전사 장애' }) }));
  await page.goto(recordingRequest.open_url);
  await page.getByRole('button', { name: '마이크 허용하고 녹음 시작' }).click();
  await page.getByText('녹음 중', { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '업무 화면으로' }).isDisabled(), true);
  const second = await context.newPage();
  await second.goto(recordingRequest.open_url);
  await second.getByText('녹음을 시작한 창에서 종료해 주세요. 이 창에는 녹음 원본이 없습니다.').waitFor();
  assert.equal(await second.getByRole('button', { name: '마이크 허용하고 녹음 시작' }).count(), 0);
  await second.close();
  await page.waitForTimeout(1000); // MediaRecorder must produce actual encoded audio chunks.
  await page.getByRole('button', { name: '녹음 종료하고 업로드' }).click();
  await page.getByText('녹음 업로드 완료', { exact: true }).waitFor();
  const receipt = await request(page, `/api/browser-interactions/${recordingRequest.interaction_id}`);
  assert.equal(receipt.status, 'completed');
  assert.equal(receipt.result.state, 'uploaded');
  assert.ok(receipt.result.size_bytes > 0);
  await page.reload();
  await page.getByText('녹음 업로드 완료', { exact: true }).waitFor();
  const detail = await request(page, `/api/meetings/${meeting.meeting_id}`);
  assert.equal(detail.recordings.length, 1);
  assert.equal(detail.recordings[0].recording_id, receipt.result.recording_id);

  const deniedRequest = await request(page, '/api/browser-interactions/recordings', {
    target_id: meeting.meeting_id, purpose: '거절 검증', request_key: `denied-${stamp}`,
  });
  await page.addInitScript(() => {
    navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('마이크 접근 거절', 'NotAllowedError'); };
  });
  await page.goto(deniedRequest.open_url);
  await page.getByRole('button', { name: '마이크 허용하고 녹음 시작' }).click();
  await page.getByText('권한 또는 마이크 사용 거절', { exact: true }).waitFor();
  assert.equal((await request(page, `/api/meetings/${meeting.meeting_id}`)).recordings.length, 1);
  console.log(JSON.stringify({ file_attachment: 'completed_once', recording: 'uploaded_once', audio_bytes: receipt.result.size_bytes,
    second_window: 'no_capture', microphone_denial: 'no_recording', provider_pipeline: 'separate_acceptance' }));
} finally {
  await browser.close();
}
