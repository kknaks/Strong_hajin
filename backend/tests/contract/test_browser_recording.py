"""AX asks for microphone interaction; only the browser starts the pinned meeting recording."""
import asyncio
import pytest
from dataclasses import replace

from fastapi.testclient import TestClient

from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from test_unified_commands import _stack

MINA = {'X-Demo-Persona': 'mina'}
CAPTURE = {'capture_id': '84ef680a-eb71-490c-bad9-de327ce48016'}


def _recording_stack(tmp_path):
    _, application = _stack(tmp_path)
    app = create_app(replace(application._settings, recordings_dir=str(tmp_path / 'recordings')))
    client = TestClient(app)
    meeting = client.post('/api/meetings', headers=MINA, json={
        'title': '녹음할 회의',
        'starts_at': '2026-09-13T01:00:00Z', 'ends_at': '2026-09-13T02:00:00Z',
    }).json()['meeting']
    return client, app.state.workflow_application, meeting


def test_recording_request_waits_for_the_browser_and_starts_once(tmp_path):
    client, application, meeting = _recording_stack(tmp_path)
    server = _create_bound_persona_server(McpReportsFacade(application._settings, 'mina'))
    request = {'intent': 'meeting_recording', 'target_id': meeting['meeting_id'], 'request_key': 'record-once', 'purpose': '주간 논의'}
    response = asyncio.run(server.call_tool('recording_request', {'request': request}))
    assert not response.is_error, response
    pending = response.structured_content
    assert pending['status'] == 'waiting' and pending['result'] is None
    assert client.get('/api/meetings/' + meeting['meeting_id'], headers=MINA).json()['meeting']['status'] == 'scheduled'
    path = '/api/browser-interactions/' + pending['interaction_id']
    assert client.get(path, headers={'X-Demo-Persona': 'sora'}).status_code == 404
    started = client.post(path + '/recording/start', headers=MINA, json=CAPTURE)
    assert started.status_code == 200, started.text
    result = started.json()
    assert result['status'] == 'recording'
    assert result['result']['meeting_id'] == meeting['meeting_id']
    assert result['result']['purpose'] == '주간 논의'
    assert result['result']['state'] == 'recording'
    assert client.post(path + '/recording/start', headers=MINA, json=CAPTURE).json() == result
    assert client.post(path + '/recording/start', headers=MINA, json={'capture_id': '7d4eb9c5-6722-4840-b98b-5085232c32b5'}).status_code == 409
    assert client.get(path, headers=MINA).json() == result
    assert client.get('/api/meetings/' + meeting['meeting_id'], headers=MINA).json()['meeting']['status'] == 'in_progress'
    restored = asyncio.run(server.call_tool('browser_interaction_get', {'interaction_id': pending['interaction_id']}))
    assert restored.structured_content == result
    assert 'temporary_key' not in str(result) and 'websocket_url' not in str(result)


def test_recording_stop_replays_one_upload_and_one_finalization_job(tmp_path):
    from sqlalchemy import func, select
    from ax_workspace.platform.persistence import MeetingRecordingFileRecord
    from ax_workspace.modules.jobs.domain import JOB_KIND_MEETING_FINALIZE
    client, application, meeting = _recording_stack(tmp_path)
    pending = client.post('/api/browser-interactions/recordings', headers=MINA, json={
        'target_id': meeting['meeting_id'], 'request_key': 'record-stop', 'purpose': '논의 원본',
    }).json()
    path = '/api/browser-interactions/' + pending['interaction_id']
    started = client.post(path + '/recording/start', headers=MINA, json=CAPTURE)
    assert started.status_code == 200, started.text
    audio = {'file': ('meeting.webm', b'captured original audio', 'audio/webm')}
    stopped = client.post(path + '/recording/stop', headers=MINA, data=CAPTURE, files=audio)
    assert stopped.status_code == 200, stopped.text
    result = stopped.json()
    assert result['status'] == 'completed' and result['result']['state'] == 'uploaded'
    assert result['result']['size_bytes'] == len(b'captured original audio')
    assert result['result']['recording_id'] == started.json()['result']['recording_id']
    assert client.post(path + '/recording/stop', headers=MINA, data=CAPTURE, files=audio).json() == result
    assert client.get(path, headers=MINA).json() == result
    assert client.post(path + '/recording/stop', headers=MINA, data=CAPTURE, files={'file': ('other.webm', b'changed', 'audio/webm')}).status_code == 409
    with application._session_factory() as session:
        assert session.scalar(select(func.count()).select_from(MeetingRecordingFileRecord)) == 1
    jobs = application.memory_job_queue.claim(JOB_KIND_MEETING_FINALIZE, limit=10, lease_seconds=30, worker_id='verify-recording')
    assert len(jobs) == 1


def test_recording_upload_failure_is_visible_and_retries_the_same_recording(tmp_path, monkeypatch):
    client, application, meeting = _recording_stack(tmp_path)
    pending = client.post('/api/browser-interactions/recordings', headers=MINA, json={
        'target_id': meeting['meeting_id'], 'request_key': 'record-retry', 'purpose': '원본 보존',
    }).json()
    path = '/api/browser-interactions/' + pending['interaction_id']
    started = client.post(path + '/recording/start', headers=MINA, json=CAPTURE).json()
    replace_recording = application._recording_storage.replace
    def unavailable(*args, **kwargs):
        raise OSError('storage offline')
    monkeypatch.setattr(application._recording_storage, 'replace', unavailable)
    audio = {'file': ('retry.webm', b'original audio', 'audio/webm')}
    failed = client.post(path + '/recording/stop', headers=MINA, data=CAPTURE, files=audio)
    assert failed.status_code == 422, failed.text
    assert client.get(path, headers=MINA).json()['status'] == 'upload_failed'
    monkeypatch.setattr(application._recording_storage, 'replace', replace_recording)
    retried = client.post(path + '/recording/stop', headers=MINA, data=CAPTURE, files=audio)
    assert retried.status_code == 200, retried.text
    assert retried.json()['result']['recording_id'] == started['result']['recording_id']
    assert retried.json()['status'] == 'completed'


@pytest.mark.parametrize('status', ['cancelled', 'denied', 'failed', 'unsupported'])
def test_recording_device_interruption_before_start_never_creates_a_recording(tmp_path, status):
    client, _, meeting = _recording_stack(tmp_path)
    pending = client.post('/api/browser-interactions/recordings', headers=MINA, json={
        'target_id': meeting['meeting_id'], 'request_key': status, 'purpose': '장치 대기',
    }).json()
    path = '/api/browser-interactions/' + pending['interaction_id']
    interrupted = client.patch(path, headers=MINA, json={'status': status})
    assert interrupted.status_code == 200, interrupted.text
    assert interrupted.json()['status'] == status and interrupted.json()['result'] is None
    assert client.post(path + '/recording/start', headers=MINA, json=CAPTURE).status_code == 409
    assert client.get('/api/meetings/' + meeting['meeting_id'], headers=MINA).json()['meeting']['status'] == 'scheduled'


def test_recording_cannot_be_replaced_by_the_file_endpoint(tmp_path):
    client, _, meeting = _recording_stack(tmp_path)
    pending = client.post('/api/browser-interactions/recordings', headers=MINA, json={
        'target_id': meeting['meeting_id'], 'request_key': 'wrong-endpoint', 'purpose': '녹음 보호',
    }).json()
    path = '/api/browser-interactions/' + pending['interaction_id']
    started = client.post(path + '/recording/start', headers=MINA, json=CAPTURE).json()
    wrong = client.post(path + '/file', headers=MINA, files={'file': ('file.txt', b'not audio', 'text/plain')})
    assert wrong.status_code == 409, wrong.text
    assert client.get(path, headers=MINA).json() == started


def test_revoked_recording_permission_is_saved_as_denied_before_start(tmp_path):
    from sqlalchemy import delete
    from uuid import UUID
    from ax_workspace.platform.persistence import BrowserInteractionRecord, RoleCapabilityRecord
    client, application, meeting = _recording_stack(tmp_path)
    pending = client.post('/api/browser-interactions/recordings', headers=MINA, json={
        'target_id': meeting['meeting_id'], 'request_key': 'denied-start', 'purpose': '권한 검사',
    }).json()
    path = '/api/browser-interactions/' + pending['interaction_id']
    with application._session_factory() as session:
        session.execute(delete(RoleCapabilityRecord).where(RoleCapabilityRecord.capability_id == 'meeting.record'))
        session.commit()
    assert client.post(path + '/recording/start', headers=MINA, json=CAPTURE).status_code == 404
    with application._session_factory() as session:
        saved = session.get(BrowserInteractionRecord, UUID(pending['interaction_id']))
        assert saved.status == 'denied'
    assert client.get('/api/meetings/' + meeting['meeting_id'], headers=MINA).json()['meeting']['status'] == 'scheduled'


def test_lost_browser_audio_closes_the_started_meeting_for_finalization(tmp_path):
    from ax_workspace.modules.jobs.domain import JOB_KIND_MEETING_FINALIZE
    client, application, meeting = _recording_stack(tmp_path)
    pending = client.post('/api/browser-interactions/recordings', headers=MINA, json={
        'target_id': meeting['meeting_id'], 'request_key': 'lost-capture', 'purpose': '중단된 장치',
    }).json()
    path = '/api/browser-interactions/' + pending['interaction_id']
    started = client.post(path + '/recording/start', headers=MINA, json=CAPTURE).json()
    closed = client.patch(path, headers=MINA, json={'status': 'failed'})
    assert closed.status_code == 200, closed.text
    assert closed.json()['status'] == 'failed'
    assert closed.json()['result']['state'] == 'failed'
    assert closed.json()['result']['recording_id'] == started['result']['recording_id']
    assert client.patch(path, headers=MINA, json={'status': 'failed'}).json() == closed.json()
    jobs = application.memory_job_queue.claim(
        JOB_KIND_MEETING_FINALIZE, limit=10, lease_seconds=30, worker_id='verify-lost'
    )
    assert len(jobs) == 1
