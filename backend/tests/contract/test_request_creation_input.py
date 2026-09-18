"""Request creation normalizes the same reviewed input on every surface, and **stands at once**.

W1 이후 요청은 수락을 기다리지 않는다 — 같은 정규화를 지난 값이 그대로 업무와 활성 담당이 된다
(WORK-001 Phase 4 · Phase 6). 정규화의 주제는 그대로이고, 뒤에 붙던 수락 단계만 사라졌다.
"""
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
    keyed = {**values, 'idempotency_key': f'request-creation-{route}'}
    if route == 'http':
        response = client.post('/api/work-requests', headers=mina, json=values)
        assert response.status_code == 201, response.text
        request = response.json()
    else:
        if route == 'confirm':
            _delegated_turn(client, application, mina, 'mina', monkeypatch)
        server = _create_bound_persona_server(McpReportsFacade(application._settings, 'mina'))
        response = asyncio.run(server.call_tool('work_request_create', keyed))
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
    # 발송은 업무를 세우고 **담당은 수락이 세운다** — 답하기 전에는 그 사람의 목록에 서지 않는다.
    assert request['state'] == 'pending' and request['task_id']
    assert client.get('/api/my-work', headers=jiho).json() == []
    answered = client.post(
        f"/api/work-requests/{request['request_id']}/accept",
        headers=jiho, json={'expected_version': request['version']},
    )
    assert answered.status_code == 200, answered.text
    assert [row['task_id'] for row in client.get('/api/my-work', headers=jiho).json()] == [request['task_id']]
    task = client.get(f"/api/tasks/{request['task_id']}", headers=jiho).json()
    assert [step['text'] for step in task['checklist']] == ['검토 하기']
    # 요청에 실린 단계는 요청자가 쓴 것이고, 업무는 그 요청을 출처로 계속 가리킨다.
    assert task['lineage']['source_work_request_id'] == request['request_id']
