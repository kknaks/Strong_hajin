"""An AX file request waits for a person and keeps its original target through upload."""
import asyncio
import pytest

from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from legacy_acceptance import pending_request
from test_unified_commands import _stack

MINA = {'X-Demo-Persona': 'mina'}


def test_file_request_waits_and_uploads_once_to_its_bound_task(tmp_path):
    client, application = _stack(tmp_path)
    task = client.post('/api/tasks', headers=MINA, json={'title': '파일을 받을 업무'}).json()
    other = client.post('/api/tasks', headers=MINA, json={'title': '다른 업무'}).json()
    command = {'intent': 'task_material', 'target_id': task['task_id'], 'request_key': 'file-pick-1'}
    created = client.post('/api/browser-interactions/files', headers=MINA, json=command)
    assert created.status_code == 201, created.text
    waiting = created.json()
    assert waiting['status'] == 'waiting'
    assert waiting['result'] is None
    assert waiting['target']['id'] == task['task_id']
    assert waiting['open_url'] == application._settings.web_origin + '/?interaction=' + waiting['interaction_id']
    path = '/api/browser-interactions/' + waiting['interaction_id']
    assert client.post('/api/browser-interactions/files', headers=MINA, json=command).json() == waiting
    assert client.get('/api/tasks/' + task['task_id'] + '/materials', headers=MINA).json() == []
    changed = client.post('/api/browser-interactions/files', headers=MINA, json={**command, 'target_id': other['task_id']})
    assert changed.status_code == 409
    assert client.get(path, headers={'X-Demo-Persona': 'sora'}).status_code == 404

    for _ in range(2):
        uploaded = client.post(path + '/file', headers=MINA, files={'file': ('source.txt', b'same original', 'text/plain')})
        assert uploaded.status_code == 200, uploaded.text
        receipt = uploaded.json()
        assert receipt['status'] == 'completed'
        assert receipt['result']['task_id'] == task['task_id']
    assert len(client.get('/api/tasks/' + task['task_id'] + '/materials', headers=MINA).json()) == 1
    assert client.get('/api/tasks/' + other['task_id'] + '/materials', headers=MINA).json() == []
    different = client.post(path + '/file', headers=MINA, files={'file': ('new.txt', b'different original', 'text/plain')})
    assert different.status_code == 409
    assert client.get(path, headers=MINA).json() == receipt


def test_mcp_file_request_returns_the_same_waiting_browser_request(tmp_path):
    client, application = _stack(tmp_path)
    task = client.post('/api/tasks', headers=MINA, json={'title': 'AX가 요청할 파일'}).json()
    server = _create_bound_persona_server(McpReportsFacade(application._settings, 'mina'))
    result = asyncio.run(server.call_tool('file_attachment_request', {
        'request': {'intent': 'task_material', 'target_id': task['task_id'], 'request_key': 'mcp-file-pick'},
    })).structured_content
    assert result['status'] == 'waiting' and result['result'] is None
    stored = client.get('/api/browser-interactions/' + result['interaction_id'], headers=MINA)
    assert stored.status_code == 200 and stored.json() == result


def test_external_mcp_browser_link_uses_the_configured_web_origin(tmp_path):
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from ax_workspace.entrypoints.http import create_app
    client, application = _stack(tmp_path)
    task = client.post('/api/tasks', headers=MINA, json={'title': '외부 창에서 파일 선택'}).json()
    settings = replace(application._settings, web_origin='https://scax.example.test/')
    server = _create_bound_persona_server(McpReportsFacade(settings, 'mina'))
    result = asyncio.run(server.call_tool('file_attachment_request', {
        'request': {'intent': 'task_material', 'target_id': task['task_id'], 'request_key': 'external-file'},
    })).structured_content
    assert result['open_url'] == 'https://scax.example.test/?interaction=' + result['interaction_id']
    browser = TestClient(create_app(settings))
    assert browser.get('/api/browser-interactions/' + result['interaction_id'], headers=MINA).json() == result


@pytest.mark.parametrize('state', ['cancelled', 'denied', 'failed', 'unsupported'])
def test_browser_interruption_stays_distinct_from_completed_upload(tmp_path, state):
    client, _ = _stack(tmp_path)
    task = client.post('/api/tasks', headers=MINA, json={'title': '중단된 파일 선택'}).json()
    opened = client.post('/api/browser-interactions/files', headers=MINA, json={
        'intent': 'task_material', 'target_id': task['task_id'], 'request_key': state,
    }).json()
    path = '/api/browser-interactions/' + opened['interaction_id']
    changed = client.patch(path, headers=MINA, json={'status': state})
    assert changed.status_code == 200, changed.text
    assert changed.json()['status'] == state and changed.json()['result'] is None
    assert client.get(path, headers=MINA).json() == changed.json()
    refused = client.post(path + '/file', headers=MINA, files={'file': ('late.txt', b'too late', 'text/plain')})
    assert refused.status_code == 409
    assert client.get('/api/tasks/' + task['task_id'] + '/materials', headers=MINA).json() == []


def test_an_upload_failure_is_saved_without_an_attachment(tmp_path):
    client, _ = _stack(tmp_path)
    task = client.post('/api/tasks', headers=MINA, json={'title': '실패한 파일'}).json()
    opened = client.post('/api/browser-interactions/files', headers=MINA, json={
        'intent': 'task_material', 'target_id': task['task_id'], 'request_key': 'empty-file',
    }).json()
    path = '/api/browser-interactions/' + opened['interaction_id']
    failed = client.post(path + '/file', headers=MINA, files={'file': ('empty.txt', b'', 'text/plain')})
    assert failed.status_code == 422
    assert client.get(path, headers=MINA).json()['status'] == 'failed'
    assert client.get('/api/tasks/' + task['task_id'] + '/materials', headers=MINA).json() == []


def test_permission_revocation_before_upload_is_a_denied_attempt(tmp_path):
    from uuid import UUID
    from sqlalchemy import delete
    from ax_workspace.platform.persistence import BrowserInteractionRecord, RoleCapabilityRecord

    client, application = _stack(tmp_path)
    task = client.post('/api/tasks', headers=MINA, json={'title': '권한 재확인'}).json()
    opened = client.post('/api/browser-interactions/files', headers=MINA, json={
        'intent': 'task_material', 'target_id': task['task_id'], 'request_key': 'revoked-upload',
    }).json()
    path = '/api/browser-interactions/' + opened['interaction_id']
    with application._session_factory() as session:
        session.execute(delete(RoleCapabilityRecord).where(RoleCapabilityRecord.capability_id == 'task.self_manage'))
        session.commit()
    denied = client.post(path + '/file', headers=MINA, files={'file': ('denied.txt', b'no effect', 'text/plain')})
    assert denied.status_code == 403
    with application._session_factory() as session:
        saved = session.get(BrowserInteractionRecord, UUID(opened['interaction_id']))
        assert saved.status == 'denied' and saved.result is None
    assert client.get('/api/tasks/' + task['task_id'] + '/materials', headers=MINA).json() == []


@pytest.mark.parametrize('intent', ['meeting_material', 'folder_material', 'request_evidence', 'request_comment_attachment'])
def test_file_request_uses_each_existing_owner_and_restores_its_receipt(tmp_path, intent):
    client, application = _stack(tmp_path)
    command = {'intent': intent, 'request_key': intent}
    if intent.startswith('meeting_'):
        meeting = client.post('/api/meetings', headers=MINA, json={
            'title': '파일을 받을 회의',
            'starts_at': '2026-09-13T01:00:00Z', 'ends_at': '2026-09-13T02:00:00Z',
        }).json()
        command['target_id'] = meeting['meeting']['meeting_id']
    elif intent == 'folder_material':
        folder = client.post('/api/material-folders', headers=MINA, json={'kind': 'personal', 'title': '파일 자료함'}).json()
        command['target_id'] = folder['folder_id']
    else:
        # 요청 근거는 판단 회차에 붙는다 — 신규 요청에는 그 회차가 없으므로 과거 모양 행에서 본다.
        request = pending_request(client, application._settings.database_url, MINA, title='파일 근거 요청', assignee_id='jiho')
        command['target_id'] = request['request_id']
        if intent == 'request_comment_attachment':
            comment = client.post('/api/work-requests/' + request['request_id'] + '/comments', headers=MINA, json={'body': '첨부할 논의'}).json()
            command['comment_id'] = comment['comment_id']
    opened = client.post('/api/browser-interactions/files', headers=MINA, json=command)
    assert opened.status_code == 201, opened.text
    path = '/api/browser-interactions/' + opened.json()['interaction_id']
    filename, content_type = ('source.md', 'text/markdown') if intent == 'meeting_material' else ('source.txt', 'text/plain')
    result = client.post(path + '/file', headers=MINA, files={'file': (filename, b'one source', content_type)})
    assert result.status_code == 200, result.text
    receipt = result.json()
    assert receipt['status'] == 'completed'
    assert receipt['target']['id'] == command['target_id']
    if intent == 'request_evidence':
        request = client.get('/api/work-requests/' + command['target_id'], headers=MINA).json()
        accepted = client.post('/api/work-requests/' + command['target_id'] + '/accept', headers={'X-Demo-Persona': 'jiho'}, json={'expected_version': request['version']})
        assert accepted.status_code == 200, accepted.text
    assert client.get(path, headers=MINA).json() == receipt
    server = _create_bound_persona_server(McpReportsFacade(application._settings, 'mina'))
    restored = asyncio.run(server.call_tool('browser_interaction_get', {'interaction_id': receipt['interaction_id']})).structured_content
    assert restored == receipt
    replay = client.post(path + '/file', headers=MINA, files={'file': (filename, b'one source', content_type)})
    assert replay.status_code == 200 and replay.json() == receipt


def test_action_file_is_staged_until_the_person_selects_it_at_confirmation(tmp_path):
    from test_action_center import _ax_proposal

    client, application = _stack(tmp_path)
    proposal = _ax_proposal(client, application, MINA, 'mina', 'task.create_self', '첨부할 생성 제안',
                            {'title': '승인 뒤 생길 업무', 'due_date': '2026-09-20'})
    opened = client.post('/api/browser-interactions/files', headers=MINA, json={
        'intent': 'action_material', 'target_id': proposal['action_id'], 'request_key': 'proposal-file',
    })
    assert opened.status_code == 201, opened.text
    path = '/api/browser-interactions/' + opened.json()['interaction_id']
    uploaded = client.post(path + '/file', headers=MINA, files={'file': ('basis.txt', b'approved basis', 'text/plain')})
    assert uploaded.status_code == 200, uploaded.text
    result = uploaded.json()
    assert result['status'] == 'completed' and result['result']['state'] == 'staged'
    assert client.get('/api/my-work', headers=MINA).json() == []
    item_path = '/api/action-items/' + proposal['action_id']
    item = client.get(item_path, headers=MINA).json()
    approved = client.post(item_path + '/commands/confirm', headers=MINA, json={
        'expected_version': item['expected_version'], 'base_submission_version': item['submission_version'],
        'draft': item['edit_contract']['values'], 'attachment_draft_ids': [result['result']['material_draft_id']],
    })
    assert approved.status_code == 200, approved.text
    assert client.get(path, headers=MINA).json() == result
    task_id = approved.json()['derived_task_id']
    assert len(client.get('/api/tasks/' + task_id + '/materials', headers=MINA).json()) == 1


def test_action_file_resumes_the_durable_reservation_after_process_loss(tmp_path):
    from uuid import UUID
    from ax_workspace.platform.persistence import ActionMaterialDraftRecord
    from test_action_center import _ax_proposal

    client, application = _stack(tmp_path)
    proposal = _ax_proposal(client, application, MINA, 'mina', 'task.create_self', '중단된 자료 준비',
                            {'title': '원본 예약 보존', 'due_date': '2026-09-20'})
    opened = client.post('/api/browser-interactions/files', headers=MINA, json={
        'intent': 'action_material', 'target_id': proposal['action_id'], 'request_key': 'reserved-file',
    }).json()
    identifier = UUID(opened['interaction_id'])
    fields = {'name': 'resume.txt', 'content_type': 'text/plain', 'data': b'reserved original'}
    with application._session_factory() as session:
        reservation = application._browser_interactions(session).prepare_action_upload(application.authenticated_principal('mina'), identifier, **fields)
        session.commit()
    assert reservation is not None
    path = '/api/browser-interactions/' + str(identifier)
    assert client.get(path, headers=MINA).json()['status'] == 'uploading'
    from ax_workspace.entrypoints.http import create_app
    from fastapi.testclient import TestClient
    restarted = TestClient(create_app(application._settings))
    resumed = restarted.post(path + '/file', headers=MINA, files={'file': ('resume.txt', fields['data'], fields['content_type'])})
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()['result']['material_draft_id'] == str(reservation)
    assert '_storage_key' not in str(resumed.json())
    with application._session_factory() as session:
        assert session.query(ActionMaterialDraftRecord).count() == 1


def test_action_file_storage_failure_discards_the_reserved_draft(tmp_path, monkeypatch):
    from ax_workspace.platform.persistence import ActionMaterialDraftRecord
    from test_action_center import _ax_proposal
    client, application = _stack(tmp_path)
    proposal = _ax_proposal(client, application, MINA, 'mina', 'task.create_self', '저장 실패',
                            {'title': '저장 실패 원안', 'due_date': '2026-09-20'})
    opened = client.post('/api/browser-interactions/files', headers=MINA, json={
        'intent': 'action_material', 'target_id': proposal['action_id'], 'request_key': 'storage-failure',
    }).json()
    def unavailable(*args, **kwargs):
        raise OSError('storage unavailable')
    monkeypatch.setattr(application._material_storage, 'put', unavailable)
    path = '/api/browser-interactions/' + opened['interaction_id']
    failed = client.post(path + '/file', headers=MINA, files={'file': ('fail.txt', b'not saved', 'text/plain')})
    assert failed.status_code == 422, failed.text
    assert client.get(path, headers=MINA).json()['status'] == 'failed'
    with application._session_factory() as session:
        assert session.query(ActionMaterialDraftRecord).one().state == 'discarded'


def test_action_file_rechecks_permission_after_reservation_commit(tmp_path, monkeypatch):
    from uuid import UUID
    from sqlalchemy import delete
    from ax_workspace.bootstrap.browser_interactions import SessionBrowserFileTargets
    from ax_workspace.platform.persistence import ActionMaterialDraftRecord, BrowserInteractionRecord, RoleCapabilityRecord
    from test_action_center import _ax_proposal

    client, application = _stack(tmp_path)
    proposal = _ax_proposal(client, application, MINA, 'mina', 'task.create_self', '저장 직전 권한 철회',
                            {'title': '철회 후 생성 금지', 'due_date': '2026-09-20'})
    opened = client.post('/api/browser-interactions/files', headers=MINA, json={
        'intent': 'action_material', 'target_id': proposal['action_id'], 'request_key': 'revoke-between-phases',
    }).json()
    reserve = SessionBrowserFileTargets.reserve_action_file

    def revoke_after_reserve(self, *args, **kwargs):
        result = reserve(self, *args, **kwargs)
        self._session.execute(delete(RoleCapabilityRecord).where(RoleCapabilityRecord.capability_id == 'action.decide'))
        return result

    monkeypatch.setattr(SessionBrowserFileTargets, 'reserve_action_file', revoke_after_reserve)
    result = client.post('/api/browser-interactions/' + opened['interaction_id'] + '/file', headers=MINA,
                         files={'file': ('revoked.txt', b'no stored effect', 'text/plain')})
    assert result.status_code == 404, result.text
    with application._session_factory() as session:
        assert session.get(BrowserInteractionRecord, UUID(opened['interaction_id'])).status == 'denied'
        draft = session.query(ActionMaterialDraftRecord).one()
        assert draft.state == 'discarded'
        with pytest.raises(FileNotFoundError):
            application._material_storage.get(draft.source_ref)
