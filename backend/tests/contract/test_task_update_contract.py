"""Field edits use the same typed meaning for screen, tool and final approval."""
import asyncio
from uuid import UUID

import pytest

from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from ax_workspace.platform.persistence import ConversationTurnRecord
from test_unified_commands import _stack


@pytest.mark.parametrize('route', ['http', 'mcp', 'confirm'])
def test_task_update_shares_clear_and_project_changes_and_executes_the_edited_final_values(tmp_path, monkeypatch, route):
    client, application = _stack(tmp_path)
    headers = {'X-Demo-Persona': 'jiho'}
    project = client.post('/api/projects', headers=headers, json={'name': '옮길 프로젝트'}).json()
    task = client.post('/api/tasks', headers=headers, json={'title': '원래 업무', 'description': '지울 설명', 'start_date': '2026-09-10', 'due_date': '2026-09-30'}).json()
    request = {'expected_version': task['version'], 'title': '최종 업무', 'description': '', 'clear_start_date': True, 'clear_due_date': True, 'project_id': project['project_id']}
    if route == 'http':
        response = client.patch(f"/api/tasks/{task['task_id']}", headers=headers, json=request)
        assert response.status_code == 200, response.text
    else:
        if route == 'confirm':
            conversation = client.post('/api/conversations', headers=headers, json={'title': '업무 수정'}).json()
            turn = client.post(f"/api/conversations/{conversation['conversation_id']}/messages", headers={**headers, 'Idempotency-Key': 'edit-task'}, json={'body': '내용과 프로젝트를 변경해줘', 'context': []}).json()
            with application._session_factory() as session:
                execution_id = session.get(ConversationTurnRecord, UUID(turn['turn_id'])).execution_id
            monkeypatch.setenv('AX_MCP_CAUSATION_ID', str(execution_id))
        server = _create_bound_persona_server(McpReportsFacade(application._settings, 'jiho'))
        result = asyncio.run(server.call_tool('task_update', {'task_id': task['task_id'], **request, 'title': '수정 전 제안' if route == 'confirm' else '최종 업무'}))
        assert not result.is_error, result
        if route == 'confirm':
            proposal = result.structured_content
            assert client.get(f"/api/tasks/{task['task_id']}", headers=headers).json()['version'] == task['version']
            item = client.get(f"/api/action-items/{proposal['action_id']}", headers=headers).json()
            assert item['edit_contract']['editor'] == 'command'
            body = {'expected_version': item['expected_version'], 'base_submission_version': item['submission_version'], 'draft': {**item['edit_contract']['values'], 'title': '최종 업무'}}
            url = f"/api/action-items/{proposal['action_id']}/commands/confirm"
            approved = client.post(url, headers=headers, json=body)
            assert approved.status_code == 200, approved.text
            assert client.post(url, headers=headers, json=body).json()['execution_result'] == approved.json()['execution_result']
    updated = client.get(f"/api/tasks/{task['task_id']}", headers=headers).json()
    assert updated['title'] == '최종 업무' and updated['description'] is None
    assert updated['start_date'] is updated['due_date'] is None
    assert updated['project_id'] == project['project_id'] and updated['version'] == task['version'] + 1


def test_legacy_nested_task_edit_remains_confirmable_without_rewriting_its_submission(tmp_path):
    from test_extended_confirmation import _propose
    client, application = _stack(tmp_path)
    headers = {'X-Demo-Persona': 'jiho'}
    task = client.post('/api/tasks', headers=headers, json={'title': '보존할 제목', 'description': '원래 설명'}).json()
    item = _propose(client, application, 'task.update', {'task_id': task['task_id'], 'expected_version': task['version'], 'changes': {'description': '과거 제안'}})
    original = item['rounds'][0]
    assert original['snapshot']['changes'] == {'description': '과거 제안'}
    draft = {**item['edit_contract']['values'], 'description': None}
    response = client.post(f"/api/action-items/{item['action_item_id']}/commands/confirm", headers=headers, json={'expected_version': item['expected_version'], 'base_submission_version': item['submission_version'], 'draft': draft})
    assert response.status_code == 200, response.text
    updated = client.get(f"/api/tasks/{task['task_id']}", headers=headers).json()
    assert updated['title'] == '보존할 제목' and updated['description'] is None
    assert client.get(f"/api/action-items/{item['action_item_id']}", headers=headers).json()['rounds'][0]['snapshot'] == original['snapshot']


@pytest.mark.parametrize('payload', [{}, {'title': ''}, {'due_date': 'invalid-date'}])
def test_invalid_or_empty_task_edits_are_input_errors_without_a_version_change(tmp_path, payload):
    client, _ = _stack(tmp_path)
    headers = {'X-Demo-Persona': 'jiho'}
    task = client.post('/api/tasks', headers=headers, json={'title': '변경하지 않을 업무'}).json()
    response = client.patch(f"/api/tasks/{task['task_id']}", headers=headers, json={'expected_version': task['version'], **payload})
    assert response.status_code == 422, response.text
    assert client.get(f"/api/tasks/{task['task_id']}", headers=headers).json()['version'] == task['version']


def test_task_title_confirmation_does_not_require_project_discovery_permission(tmp_path):
    from sqlalchemy import delete
    from ax_workspace.platform.persistence import RoleCapabilityRecord
    from test_extended_confirmation import _propose
    client, application = _stack(tmp_path)
    headers = {'X-Demo-Persona': 'jiho'}
    task = client.post('/api/tasks', headers=headers, json={'title': '프로젝트 없는 업무'}).json()
    with application._session_factory() as session:
        session.execute(delete(RoleCapabilityRecord).where(RoleCapabilityRecord.capability_id == 'project.read'))
        session.commit()
    item = _propose(client, application, 'task.update', {'task_id': task['task_id'], 'expected_version': task['version'], 'title': '수정할 제목'})
    project = next(field for field in item['edit_contract']['fields'] if field['id'] == 'project_id')
    assert project['options'] == []
    approved = client.post(f"/api/action-items/{item['action_item_id']}/commands/confirm", headers=headers, json={'expected_version': item['expected_version'], 'base_submission_version': item['submission_version'], 'draft': item['edit_contract']['values']})
    assert approved.status_code == 200, approved.text
    assert approved.json()['execution_result']['title'] == '수정할 제목'
