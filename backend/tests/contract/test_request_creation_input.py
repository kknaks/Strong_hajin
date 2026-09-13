"""Request creation normalizes the same reviewed input before its separate acceptance."""
import asyncio
import pytest
from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from test_mcp_checklist import _delegated_turn
from test_unified_commands import _stack


@pytest.mark.parametrize('route', ['http', 'mcp', 'confirm'])
def test_request_creation_shares_normalized_cc_and_initial_steps_without_accepting_it(tmp_path, monkeypatch, route):
    client, application = _stack(tmp_path)
    mina, jiho = {'X-Demo-Persona': 'mina'}, {'X-Demo-Persona': 'jiho'}
    values = {'title': '  검토 요청  ', 'assignee_id': 'jiho', 'description': '   ', 'due_date': '2026-09-30', 'cc_member_ids': [' mina ', 'jiho', 'mina'], 'checklist': ['  검토   하기 ', '']}
    if route == 'http':
        response = client.post('/api/work-requests', headers=mina, json=values)
        assert response.status_code == 201, response.text
        request = response.json()
    else:
        if route == 'confirm':
            _delegated_turn(client, application, mina, 'mina', monkeypatch)
        server = _create_bound_persona_server(McpReportsFacade(application._settings, 'mina'))
        response = asyncio.run(server.call_tool('work_request_create', values))
        assert not response.is_error, response
        request = response.structured_content
        if route == 'confirm':
            path = f"/api/action-items/{request['action_id']}"
            item = client.get(path, headers=mina).json()
            response = client.post(path + '/commands/confirm', headers=mina, json={'expected_version': item['expected_version'], 'base_submission_version': item['submission_version'], 'draft': item['edit_contract']['values']})
            assert response.status_code == 200, response.text
            request = response.json()['execution_result']
    assert request['title'] == '검토 요청' and request['description'] is None
    assert request['cc_member_ids'] == [] and request['checklist'] == ['검토 하기']
    assert request['task_id'] is None and client.get('/api/my-work', headers=jiho).json() == []
    accepted = client.post(f"/api/work-requests/{request['request_id']}/accept", headers=jiho, json={'expected_version': request['version']})
    assert accepted.status_code == 200, accepted.text
    task = client.get(f"/api/tasks/{accepted.json()['task_id']}", headers=jiho).json()
    assert [step['text'] for step in task['checklist']] == ['검토 하기']
