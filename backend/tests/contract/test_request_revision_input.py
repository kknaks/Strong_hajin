"""The approved revision is the new request round, not acceptance of that request.

**W1 이후 조정·재상신 회차는 과거 행에만 있다** — 신규 요청은 `assigned` 로 서고 판단 회차를 만들지 않는다
(WORK-001 Phase 4). 회차를 다루는 명령과 그 입력 정규화는 그대로 남으므로, 예전 배포가 남긴 모양에서 본다.
"""
import asyncio
import pytest
from legacy_acceptance import pending_request
from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from test_mcp_checklist import _delegated_turn
from test_unified_commands import _stack


@pytest.mark.parametrize('route', ['http', 'mcp', 'confirm'])
def test_request_amendment_preserves_the_original_and_applies_final_edits(tmp_path, monkeypatch, route):
    client, application = _stack(tmp_path)
    mina = {'X-Demo-Persona': 'mina'}
    created = pending_request(client, application._settings.database_url, mina, title='원안', description='원래 설명', due_date='2026-09-30', assignee_id='jiho')
    values = {'expected_version': created['version'], 'title': '  보강한 요청  ', 'description': '', 'clear_due_date': True}
    if route == 'http':
        response = client.post(f"/api/work-requests/{created['request_id']}/amend", headers=mina, json=values)
        assert response.status_code == 200, response.text
    else:
        if route == 'confirm':
            _delegated_turn(client, application, mina, 'mina', monkeypatch)
        server = _create_bound_persona_server(McpReportsFacade(application._settings, 'mina'))
        response = asyncio.run(server.call_tool('work_request_amend', {**values, 'request_id': created['request_id']}))
        assert not response.is_error
        if route == 'confirm':
            path = f"/api/action-items/{response.structured_content['action_id']}"
            item = client.get(path, headers=mina).json()
            contract = item['edit_contract']
            assert contract['editor'] == 'command'
            fields = {field['id']: field for field in contract['fields']}
            assert fields['description']['empty_policy'] == 'empty_string'
            draft = {**contract['values'], 'title': '사람이 보강한 요청'}
            payload = {'expected_version': item['expected_version'], 'base_submission_version': item['submission_version'], 'draft': draft}
            response = client.post(path + '/commands/confirm', headers=mina, json=payload)
            assert response.status_code == 200, response.text
            replay = client.post(path + '/commands/confirm', headers=mina, json=payload)
            assert replay.status_code == 200 and replay.json()['execution_result'] == response.json()['execution_result']
    current = client.get(f"/api/work-requests/{created['request_id']}", headers=mina).json()
    assert current['title'] == ('사람이 보강한 요청' if route == 'confirm' else '보강한 요청')
    assert current['description'] is None and current['due_date'] is None
    assert current['state'] == 'pending' and current['task_id'] is None
    history = client.get(f"/api/work-requests/{created['request_id']}/timeline", headers=mina).json()
    assert [row['snapshot']['title'] for row in history['submissions']] == ['원안', current['title']]
    assert history['submissions'][0]['snapshot']['description'] == '원래 설명'
    assert history['review_decisions'] == []


@pytest.mark.parametrize('operation', ['amend', 'resubmit'])
def test_request_revisions_reject_an_oversized_title_without_a_new_round(tmp_path, operation):
    client, application = _stack(tmp_path)
    mina, jiho = {'X-Demo-Persona': 'mina'}, {'X-Demo-Persona': 'jiho'}
    created = pending_request(client, application._settings.database_url, mina, title='원안', assignee_id='jiho')
    if operation == 'resubmit':
        response = client.post(f"/api/work-requests/{created['request_id']}/negotiate", headers=jiho, json={'expected_version': created['version'], 'conditions': {'note': '보강해 주세요'}})
        assert response.status_code == 200, response.text
        created = response.json()
    response = client.post(f"/api/work-requests/{created['request_id']}/{operation}", headers=mina, json={'expected_version': created['version'], 'title': '가' * 301})
    assert response.status_code == 422, response.text
    history = client.get(f"/api/work-requests/{created['request_id']}/timeline", headers=mina).json()
    assert len(history['submissions']) == 1
