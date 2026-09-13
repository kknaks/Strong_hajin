"""Assignment creation shares typed values but still awaits the assignee's judgement."""
import asyncio
import pytest
from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from test_mcp_checklist import _delegated_turn
from test_unified_commands import _stack


@pytest.mark.parametrize('route', ['http', 'mcp', 'confirm'])
def test_assignment_creation_carries_parent_and_reference_values_through_the_same_owner(tmp_path, monkeypatch, route):
    client, application = _stack(tmp_path)
    jiho, mina = {'X-Demo-Persona': 'jiho'}, {'X-Demo-Persona': 'mina'}
    parent = client.post('/api/tasks', headers=jiho, json={'title': '상위 업무'}).json()
    reference = client.post('/api/tasks', headers=jiho, json={'title': '참고할 이전 업무'}).json()
    values = {'title': '  맡길 하위 업무  ', 'assignee_id': 'mina', 'parent_task_id': parent['task_id'], 'reference_task_ids': [reference['task_id']], 'checklist': ['  검토하기  '], 'description': '  검토 내용  '}
    if route == 'http':
        response = client.post('/api/tasks/assign', headers=jiho, json=values)
        assert response.status_code == 201, response.text
        assigned = response.json()
    else:
        if route == 'confirm':
            _delegated_turn(client, application, jiho, 'jiho', monkeypatch)
        server = _create_bound_persona_server(McpReportsFacade(application._settings, 'jiho'))
        result = asyncio.run(server.call_tool('task_assign', values))
        assert not result.is_error, result
        assigned = result.structured_content
        if route == 'confirm':
            path = f"/api/action-items/{assigned['action_id']}"
            item = client.get(path, headers=jiho).json()
            confirmed = client.post(path + '/commands/confirm', headers=jiho, json={'expected_version': item['expected_version'], 'base_submission_version': item['submission_version'], 'draft': item['edit_contract']['values']})
            assert confirmed.status_code == 200, confirmed.text
            assigned = confirmed.json()['execution_result']
    assert assigned['status'] == 'pending'
    assert client.get('/api/my-work', headers=mina).json() == []
    accepted = client.post(f"/api/task-assignments/{assigned['assignment_id']}/accept", headers=mina)
    assert accepted.status_code == 200, accepted.text
    task = client.get(f"/api/tasks/{assigned['task']['task_id']}", headers=mina).json()
    assert task['title'] == '맡길 하위 업무' and task['description'] == '검토 내용'
    assert task['parent']['task_id'] == parent['task_id']
    assert [step['text'] for step in task['checklist']] == ['검토하기']
    # The immutable accepted Task retains the reference identity even when the
    # assignee cannot open the referenced owner's private work.
    with application._session_factory() as session:
        from ax_workspace.platform.persistence import TaskReferenceRecord
        from uuid import UUID
        assert session.query(TaskReferenceRecord).filter_by(task_id=UUID(task['task_id']), referenced_task_id=UUID(reference['task_id'])).count() == 1
