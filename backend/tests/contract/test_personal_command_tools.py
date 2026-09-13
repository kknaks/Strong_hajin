"""Preference, discussion and read-state commands retain their distinct effects."""
import asyncio
from uuid import UUID

import pytest

from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from ax_workspace.platform.persistence import ConversationTurnRecord
from test_unified_commands import _stack, _approval_request


@pytest.mark.parametrize('delegated', [False, True, 'confirm'])
@pytest.mark.parametrize('tool', ['assistant_character_set', 'notification_mark_read', 'work_request_comment_add'])
def test_personal_command_effect_is_applied_once_after_confirmation(tmp_path, monkeypatch, tool, delegated):
    client, application = _stack(tmp_path)
    headers = {'X-Demo-Persona': 'jiho'}
    request = client.post('/api/work-requests', headers={'X-Demo-Persona': 'mina'}, json={'title': '논의할 업무', 'assignee_id': 'jiho'}).json()
    if tool == 'assistant_character_set':
        arguments = {'request': {'character_key': 'rabbit', 'expected_version': 0}}
        def state(): return application.my_organization_profile(application.authenticated_principal('jiho'))['assistant_character']
    elif tool == 'notification_mark_read':
        [notification] = client.get('/api/notifications', headers=headers).json()
        arguments = {'notification_id': notification['notification_id']}
        def state(): return client.get('/api/notifications', headers=headers).json()
    else:
        arguments = {'request_id': request['request_id'], 'body': '일정은 금요일로 확인했습니다.'}
        def state(): return client.get(f"/api/work-requests/{request['request_id']}/timeline", headers=headers).json()
    before = state()
    if delegated:
        conversation = client.post('/api/conversations', headers=headers, json={'title': '개인 변경'}).json()
        turn = client.post(f"/api/conversations/{conversation['conversation_id']}/messages", headers={**headers, 'Idempotency-Key': tool}, json={'body': '이 변경을 준비해줘', 'context': []}).json()
        with application._session_factory() as session:
            execution_id = session.get(ConversationTurnRecord, UUID(turn['turn_id'])).execution_id
        monkeypatch.setenv('AX_MCP_CAUSATION_ID', str(execution_id))
    server = _create_bound_persona_server(McpReportsFacade(application._settings, 'jiho'))
    result = asyncio.run(server.call_tool(tool, arguments))
    assert not result.is_error, result
    if delegated:
        proposal = result.structured_content
        assert state() == before
        url, payload = _approval_request(client, headers, proposal, delegated)
        approved = client.post(url, headers=headers, json=payload)
        assert approved.status_code == 200, approved.text
        replay = client.post(url, headers=headers, json=payload)
        assert replay.status_code == 200 and replay.json()['execution_result'] == approved.json()['execution_result']
    after = state()
    if tool == 'assistant_character_set':
        assert after == {'character_key': 'rabbit', 'version': 1}
    elif tool == 'notification_mark_read':
        assert after[0]['read_at'] is not None
    else:
        assert [row['body'] for row in after['comments']] == ['일정은 금요일로 확인했습니다.']
        unchanged = client.get(f"/api/work-requests/{request['request_id']}", headers=headers).json()
        assert unchanged['state'] == request['state'] and unchanged['version'] == request['version']


def test_notification_source_denial_is_hidden_but_owner_failure_is_not_empty_success(tmp_path, monkeypatch):
    from ax_workspace.modules.notifications import NotificationNotFound
    from ax_workspace.modules.work.requests import WorkRequestAccessDenied, WorkRequestApplication, WorkRequestError
    client, application = _stack(tmp_path)
    created = client.post('/api/work-requests', headers={'X-Demo-Persona': 'mina'}, json={'title': '원본 권한 알림', 'assignee_id': 'jiho'})
    assert created.status_code == 201, created.text
    principal = application.authenticated_principal('jiho')
    [notification] = application.list_notifications(principal)

    def denied(*args, **kwargs):
        raise WorkRequestAccessDenied('current read permission removed')

    with monkeypatch.context() as revoked:
        revoked.setattr(WorkRequestApplication, 'get', denied)
        assert application.list_notifications(principal) == []
        with pytest.raises(NotificationNotFound):
            application.mark_notification_read(principal, UUID(notification['notification_id']))
    assert application.list_notifications(principal)[0]['read_at'] is None

    def failed(*args, **kwargs):
        raise WorkRequestError('owner projection unavailable')

    with monkeypatch.context() as unavailable:
        unavailable.setattr(WorkRequestApplication, 'get', failed)
        with pytest.raises(WorkRequestError, match='unavailable'):
            application.list_notifications(principal)
