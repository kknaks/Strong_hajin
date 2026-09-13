"""Simultaneous browser redelivery commits one attachment and one extraction job."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.jobs.domain import JOB_KIND_MATERIAL_EXTRACTION
from ax_workspace.modules.jobs.domain import JOB_KIND_MEETING_FINALIZE
from ax_workspace.platform.persistence import ActionMaterialDraftRecord, BrowserInteractionRecord, ConversationTurnRecord, DurableJobRecord, MaterialExtractionRecord, make_session_factory
from ax_workspace.platform.persistence import MeetingRecordingFileRecord
from test_postgres_integration import _postgres_test_url


@pytest.mark.integration
@pytest.mark.parametrize('intent', ['task_material', 'action_material'])
def test_postgres_same_browser_request_and_upload_are_single_effects(tmp_path, intent):
    url = _postgres_test_url()
    reset_database(url)
    client = TestClient(create_app(Settings(RuntimeProfile.TEST, url, job_queue_backend='postgres', materials_dir=str(tmp_path / 'materials'))))
    headers = {'X-Demo-Persona': 'mina'}
    if intent == 'task_material':
        task = client.post('/api/tasks', headers=headers, json={'title': '두 창의 파일'}).json()
        target_id = task['task_id']
    else:
        application = client.app.state.workflow_application
        conversation = client.post('/api/conversations', headers=headers, json={'title': '두 창의 승인 자료'}).json()
        turn = client.post('/api/conversations/' + conversation['conversation_id'] + '/messages',
                           headers={**headers, 'Idempotency-Key': 'browser-file-proposal'}, json={'body': '업무 제안', 'context': []}).json()
        with make_session_factory(url)() as session:
            execution_id = session.get(ConversationTurnRecord, UUID(turn['turn_id'])).execution_id
        proposal = application.propose_action(application.authenticated_principal('mina'), execution_id,
                                              'task.create_self', '두 창의 승인 자료', {'title': '승인 후 생성', 'due_date': '2026-09-20'})
        target_id = proposal['action_id']
    command = {'intent': intent, 'target_id': target_id, 'request_key': 'two-windows'}
    barrier = Barrier(2)

    def open_request():
        barrier.wait(timeout=10)
        return client.post('/api/browser-interactions/files', headers=headers, json=command)

    with ThreadPoolExecutor(max_workers=2) as pool:
        opened = list(pool.map(lambda _: open_request(), range(2)))
    assert [row.status_code for row in opened] == [201, 201], [row.text for row in opened]
    assert opened[0].json() == opened[1].json()
    path = '/api/browser-interactions/' + opened[0].json()['interaction_id'] + '/file'
    barrier = Barrier(2)

    def upload():
        barrier.wait(timeout=10)
        return client.post(path, headers=headers, files={'file': ('two.txt', b'one immutable original', 'text/plain')})

    with ThreadPoolExecutor(max_workers=2) as pool:
        completed = list(pool.map(lambda _: upload(), range(2)))
    assert [row.status_code for row in completed] == [200, 200], [row.text for row in completed]
    assert completed[0].json() == completed[1].json()
    if intent == 'task_material':
        assert len(client.get('/api/tasks/' + target_id + '/materials', headers=headers).json()) == 1
    else:
        assert completed[0].json()['result']['state'] == 'staged'
        assert client.get('/api/my-work', headers=headers).json() == []
    with make_session_factory(url)() as session:
        assert session.scalar(select(func.count()).select_from(BrowserInteractionRecord)) == 1
        assert session.scalar(select(func.count()).select_from(ActionMaterialDraftRecord)) == (1 if intent == 'action_material' else 0)
        extractions = 1 if intent == 'task_material' else 0
        assert session.scalar(select(func.count()).select_from(MaterialExtractionRecord)) == extractions
        assert session.scalar(select(func.count()).select_from(DurableJobRecord).where(DurableJobRecord.kind == JOB_KIND_MATERIAL_EXTRACTION)) == extractions


@pytest.mark.integration
def test_postgres_browser_recording_start_and_stop_are_single_effects(tmp_path):
    url = _postgres_test_url()
    reset_database(url)
    client = TestClient(create_app(Settings(RuntimeProfile.TEST, url, job_queue_backend='postgres',
                                          recordings_dir=str(tmp_path / 'recordings'), materials_dir=str(tmp_path / 'materials'))))
    headers = {'X-Demo-Persona': 'mina'}
    meeting = client.post('/api/meetings', headers=headers, json={
        'title': '녹음 동시 재전송',
        'starts_at': '2026-09-13T01:00:00Z', 'ends_at': '2026-09-13T02:00:00Z',
    }).json()['meeting']
    pending = client.post('/api/browser-interactions/recordings', headers=headers, json={
        'target_id': meeting['meeting_id'], 'request_key': 'pg-record', 'purpose': '원본 하나',
    }).json()
    path = '/api/browser-interactions/' + pending['interaction_id']
    capture = {'capture_id': 'cd3e9499-9ba0-474f-9ee3-7d24cdfdceea'}
    barrier = Barrier(2)
    def start():
        barrier.wait(timeout=10)
        return client.post(path + '/recording/start', headers=headers, json=capture)
    with ThreadPoolExecutor(max_workers=2) as pool:
        started = list(pool.map(lambda _: start(), range(2)))
    assert [row.status_code for row in started] == [200, 200], [row.text for row in started]
    assert started[0].json() == started[1].json()
    other_capture = {'capture_id': 'ab9e83c3-ac63-4bf3-abf4-2d0aef157053'}
    assert client.post(path + '/recording/stop', headers=headers, data=other_capture,
                       files={'file': ('record.webm', b'original recording', 'audio/webm')}).status_code == 409
    barrier = Barrier(2)
    def stop():
        barrier.wait(timeout=10)
        return client.post(path + '/recording/stop', headers=headers, data=capture,
                           files={'file': ('record.webm', b'original recording', 'audio/webm')})
    with ThreadPoolExecutor(max_workers=2) as pool:
        stopped = list(pool.map(lambda _: stop(), range(2)))
    assert [row.status_code for row in stopped] == [200, 200], [row.text for row in stopped]
    assert stopped[0].json() == stopped[1].json()
    assert stopped[0].json()['status'] == 'completed'
    with make_session_factory(url)() as session:
        assert session.scalar(select(func.count()).select_from(MeetingRecordingFileRecord)) == 1
        assert session.scalar(select(func.count()).select_from(DurableJobRecord).where(DurableJobRecord.kind == JOB_KIND_MEETING_FINALIZE)) == 1
