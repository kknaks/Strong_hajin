"""A report edit chooses final content and sources; submission answers that exact draft."""
import asyncio
from datetime import UTC, datetime
import pytest
from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from test_mcp_checklist import _delegated_turn
from test_unified_commands import _stack
from ax_workspace.platform.work_tasks import business_date


@pytest.mark.parametrize('route', ['http', 'mcp', 'confirm'])
def test_report_edit_and_submission_preserve_their_separate_approved_versions(tmp_path, monkeypatch, route):
    client, application = _stack(tmp_path)
    headers = {'X-Demo-Persona': 'mina'}
    for title in ['검토한 업무', '다음 보고에 넣을 업무']:
        client.post('/api/tasks', headers=headers, json={'title': title})
    draft = application.generate_daily_report_draft(application.authenticated_principal('mina'), business_date(datetime.now(UTC)))
    assert len(draft['source_refs']) >= 2
    values = {'draft_id': draft['draft_id'], 'expected_version': draft['draft_version'], 'body': '  수정한 본문  ', 'exclude_source_refs': [draft['source_refs'][0]]}
    if route == 'http':
        response = client.post(f"/api/daily-reports/{draft['report_id']}/edit", headers=headers, json=values)
        assert response.status_code == 200, response.text
        edited = response.json()
    else:
        if route == 'confirm':
            _delegated_turn(client, application, headers, 'mina', monkeypatch)
        server = _create_bound_persona_server(McpReportsFacade(application._settings, 'mina'))
        response = asyncio.run(server.call_tool('daily_report_edit', {**values, 'report_id': draft['report_id']}))
        edited = response.structured_content
        if route == 'confirm':
            path = f"/api/action-items/{edited['action_id']}"
            item = client.get(path, headers=headers).json()
            contract = item['edit_contract']
            assert next(field for field in contract['fields'] if field['id'] == 'exclude_source_refs')['type'] == 'source_select'
            final = {**contract['values'], 'body': '최종 확인 본문', 'exclude_source_refs': [draft['source_refs'][1]]}
            payload = {'expected_version': item['expected_version'], 'base_submission_version': item['submission_version'], 'draft': final}
            response = client.post(path + '/commands/confirm', headers=headers, json=payload)
            assert response.status_code == 200, response.text
            edited = response.json()['execution_result']
            replay = client.post(path + '/commands/confirm', headers=headers, json=payload)
            assert replay.status_code == 200 and replay.json()['execution_result'] == edited
    assert edited['body'] == ('최종 확인 본문' if route == 'confirm' else '수정한 본문')
    assert edited['source_refs'] == [draft['source_refs'][0 if route == 'confirm' else 1]]
    history = application.daily_report_history(application.authenticated_principal('mina'), draft['report_id'])
    assert len(history['drafts']) == 2 and history['submissions'] == []
    assert history['drafts'][0]['body'] == draft['body']
    values = {'report_id': draft['report_id'], 'draft_id': edited['draft_id'], 'expected_version': edited['draft_version'], 'reason': None}
    if route == 'confirm':
        proposal = asyncio.run(server.call_tool('daily_report_submit', values)).structured_content
        path = f"/api/action-items/{proposal['action_id']}"
        item = client.get(path, headers=headers).json()
        assert next(field for field in item['preview'] if field['id'] == 'body')['value'] == edited['body']
        response = client.post(path + '/commands/confirm', headers=headers, json={'expected_version': item['expected_version'], 'base_submission_version': item['submission_version'], 'draft': {**item['edit_contract']['values'], 'reason': '확인 후 제출'}})
        assert response.status_code == 200, response.text
        submission = response.json()['execution_result']
    elif route == 'mcp':
        submission = asyncio.run(server.call_tool('daily_report_submit', values)).structured_content
    else:
        submission = application.submit_daily_report(application.authenticated_principal('mina'), **values)
    assert submission['body'] == edited['body'] and submission['source_refs'] == edited['source_refs']

    if route == 'mcp':
        principal = application.authenticated_principal('mina')
        history = asyncio.run(server.call_tool('daily_report_history', {'report_id': draft['report_id']})).structured_content
        assert history == application.daily_report_history(principal, draft['report_id'])
        recent = asyncio.run(server.call_tool('daily_report_recent', {})).structured_content['result']
        assert recent == application.daily_report_recent(principal)
        report_date = business_date(datetime.now(UTC))
        status = asyncio.run(server.call_tool('daily_report_status', {'report_date': report_date})).structured_content
        assert status == application.daily_report_status(principal, report_date)
