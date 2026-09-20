"""Task relationship and delivery commands share the ordinary owner boundary."""
import asyncio
from uuid import UUID

import pytest

from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from ax_workspace.platform.persistence import ConversationTurnRecord
from test_unified_commands import _stack, _approval_request
from test_task_delivery import _requested_task


def test_assignment_capability_does_not_allow_taking_an_unreadable_task(tmp_path):
    client, _ = _stack(tmp_path)
    task = client.post('/api/tasks', headers={'X-Demo-Persona': 'hyeon'}, json={'title': '다른 범위의 비공개 업무'}).json()
    headers = {'X-Demo-Persona': 'jiho'}
    assert client.get(f"/api/tasks/{task['task_id']}", headers=headers).status_code == 404
    stolen = client.post(f"/api/tasks/{task['task_id']}/reassign", headers=headers, json={
        'expected_version': task['version'], 'assignee_id': 'jiho',
    })
    assert stolen.status_code == 404, stolen.text
    still_owned = client.get(f"/api/tasks/{task['task_id']}", headers={'X-Demo-Persona': 'hyeon'})
    assert still_owned.status_code == 200 and still_owned.json()['version'] == task['version']


@pytest.mark.parametrize('delegated', [False, True, 'confirm'])
def test_reassignment_still_requires_the_new_assignee_to_accept(tmp_path, monkeypatch, delegated):
    client, application = _stack(tmp_path)
    headers = {'X-Demo-Persona': 'jiho'}
    task = client.post('/api/tasks', headers=headers, json={'title': '인계할 업무'}).json()
    if delegated:
        conversation = client.post('/api/conversations', headers=headers, json={'title': '담당자 변경'}).json()
        accepted = client.post(f"/api/conversations/{conversation['conversation_id']}/messages", headers={**headers, 'Idempotency-Key': 'reassign'}, json={'body': '민아에게 인계해줘', 'context': []}).json()
        with application._session_factory() as session:
            execution_id = session.get(ConversationTurnRecord, UUID(accepted['turn_id'])).execution_id
        monkeypatch.setenv('AX_MCP_CAUSATION_ID', str(execution_id))
    server = _create_bound_persona_server(McpReportsFacade(application._settings, 'jiho'))
    result = asyncio.run(server.call_tool('task_reassign', {'task_id': task['task_id'], 'request': {'expected_version': task['version'], 'assignee_id': 'mina', 'reason': '담당 업무 인계'}}))
    assert not result.is_error
    if delegated:
        proposal = result.structured_content
        before = client.get(f"/api/tasks/{task['task_id']}", headers=headers).json()
        assert before['version'] == task['version']
        url, payload = _approval_request(client, headers, proposal, delegated)
        approved = client.post(url, headers=headers, json=payload)
        assert approved.status_code == 200, approved.text
    mina = {'X-Demo-Persona': 'mina'}
    assert task['task_id'] not in {row['task_id'] for row in client.get('/api/my-work', headers=mina).json()}
    pending = client.get('/api/action-items', headers=mina).json()
    [assignment] = [row for row in pending if row['subject'] == '인계할 업무']
    accepted = client.post(f"/api/action-items/{assignment['action_item_id']}/commands/accept", headers=mina, json={'expected_version': assignment['expected_version']})
    assert accepted.status_code == 200, accepted.text
    assert task['task_id'] in {row['task_id'] for row in client.get('/api/my-work', headers=mina).json()}


@pytest.mark.parametrize('delegated', [False, True, 'confirm'])
@pytest.mark.parametrize('tool', ['task_reference_add', 'task_reference_release', 'task_completion_submit'])
def test_task_reference_and_delivery_effects_wait_for_approval(tmp_path, monkeypatch, tool, delegated):
    client, application = _stack(tmp_path)
    actor = 'jiho' if tool == 'task_completion_submit' else 'mina'
    headers = {'X-Demo-Persona': actor}
    if tool == 'task_completion_submit':
        task_id = _requested_task(client)
        output = client.post(f'/api/tasks/{task_id}/materials', headers=headers, data={'kind': 'output'}, files={'file': ('결과.txt', b'delivered result', 'text/plain')})
        assert output.status_code == 201, output.text
        task = client.get(f'/api/tasks/{task_id}', headers=headers).json()
        arguments = {'task_id': task_id, 'request': {'expected_version': task['version'], 'summary': '완료한 결과', 'output_material_ids': [output.json()['material_id']]}}
    else:
        task_id = client.post('/api/tasks', headers=headers, json={'title': '현재 업무'}).json()['task_id']
        earlier = client.post('/api/tasks', headers=headers, json={'title': '이전 업무'}).json()['task_id']
        if tool == 'task_reference_add':
            arguments = {'task_id': task_id, 'referenced_task_id': earlier}
        else:
            ref = client.post(f'/api/tasks/{task_id}/references', headers=headers, json={'referenced_task_id': earlier}).json()
            arguments = {'task_id': task_id, 'reference_id': ref['reference_id']}
        task = client.get(f'/api/tasks/{task_id}', headers=headers).json()
    if delegated:
        conversation = client.post('/api/conversations', headers=headers, json={'title': '업무 변경'}).json()
        accepted = client.post(f"/api/conversations/{conversation['conversation_id']}/messages", headers={**headers, 'Idempotency-Key': tool}, json={'body': '업무 변경을 준비해줘', 'context': []}).json()
        with application._session_factory() as session:
            execution_id = session.get(ConversationTurnRecord, UUID(accepted['turn_id'])).execution_id
        monkeypatch.setenv('AX_MCP_CAUSATION_ID', str(execution_id))
    server = _create_bound_persona_server(McpReportsFacade(application._settings, actor))
    result = asyncio.run(server.call_tool(tool, arguments))
    assert not result.is_error
    if delegated:
        proposal = result.structured_content
        assert proposal['state'] == 'pending' and proposal['preview']
        if tool == 'task_completion_submit':
            assert any(row['id'] == 'output_materials' and row['value'] == '결과.txt' for row in proposal['preview'])
        before = client.get(f'/api/tasks/{task_id}', headers=headers).json()
        assert before['version'] == task['version'] and before['state'] == task['state']
        url, payload = _approval_request(client, headers, proposal, delegated)
        approved = client.post(url, headers=headers, json=payload)
        assert approved.status_code == 200, approved.text
        replay = client.post(url, headers=headers, json=payload)
        assert replay.status_code == 200 and replay.json()['execution_result'] == approved.json()['execution_result']
    after = client.get(f'/api/tasks/{task_id}', headers=headers).json()
    if tool == 'task_completion_submit':
        # 밖으로는 `done` 이고, 확인 전이라는 사실은 `derived.approval` 이 낸다 (SPEC-003 §4 State).
        assert after['state'] == 'done' and after['derived']['approval'] == 'awaiting_review'
        outstanding = client.get('/api/action-items', headers={'X-Demo-Persona': 'mina'}).json()
        assert len([row for row in outstanding if row['kind'] == 'task.delivery']) == 1
    elif tool == 'task_reference_add':
        assert [row['task']['task_id'] for row in after['references']] == [earlier]
    else:
        assert after['references'] == []
