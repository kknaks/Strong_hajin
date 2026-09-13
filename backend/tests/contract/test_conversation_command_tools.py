"""Conversation control commands reuse the durable conversation application."""
import asyncio
from uuid import UUID

import pytest

from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from ax_workspace.platform.persistence import ConversationTurnRecord
from test_unified_commands import _stack, _approval_request


@pytest.mark.parametrize('delegated', [False, True, 'confirm'])
@pytest.mark.parametrize('tool', ['conversation_create', 'conversation_message_send', 'conversation_turn_cancel', 'conversation_turn_retry'])
def test_conversation_tools_only_accept_the_approved_effect(tmp_path, monkeypatch, tool, delegated):
    client, application = _stack(tmp_path)
    headers = {'X-Demo-Persona': 'mina'}
    target = client.post('/api/conversations', headers=headers, json={'title': '대상 대화'}).json()
    target_id = target['conversation_id']
    path = f'/api/conversations/{target_id}'
    arguments = {'conversation_id': target_id}
    if tool == 'conversation_create':
        arguments = {'request': {'title': '승인한 새 대화'}}
    elif tool == 'conversation_message_send':
        arguments['request'] = {'body': '이번 주 업무를 확인해줘', 'context': []}
        arguments['idempotency_key'] = 'approved-message'
    else:
        turn = client.post(path+'/messages', headers={**headers, 'Idempotency-Key': 'target-message'}, json={'body': '대기할 메시지', 'context': []}).json()
        target = client.get(path, headers=headers).json()
        if tool == 'conversation_turn_cancel':
            arguments['expected_version'] = target['version']
        else:
            cancelled = client.post(path+'/cancel', headers=headers, json={'expected_version': target['version']})
            assert cancelled.status_code == 200, cancelled.text
            arguments['turn_id'] = turn['turn_id']
    before = client.get(path, headers=headers).json()
    if delegated:
        control = client.post('/api/conversations', headers=headers, json={'title': '변경을 제안하는 대화'}).json()
        control_turn = client.post(f"/api/conversations/{control['conversation_id']}/messages", headers={**headers, 'Idempotency-Key': tool}, json={'body': '이 대화 변경을 준비해줘', 'context': []}).json()
        with application._session_factory() as session:
            execution_id = session.get(ConversationTurnRecord, UUID(control_turn['turn_id'])).execution_id
        monkeypatch.setenv('AX_MCP_CAUSATION_ID', str(execution_id))
    server = _create_bound_persona_server(McpReportsFacade(application._settings, 'mina'))
    result = asyncio.run(server.call_tool(tool, arguments))
    assert not result.is_error, result
    if delegated:
        proposal = result.structured_content
        assert client.get(path, headers=headers).json() == before
        assert not any(row['title'] == '승인한 새 대화' for row in client.get('/api/conversations', headers=headers).json())
        url, payload = _approval_request(client, headers, proposal, delegated)
        approved = client.post(url, headers=headers, json=payload)
        assert approved.status_code == 200, approved.text
        replay = client.post(url, headers=headers, json=payload)
        assert replay.status_code == 200 and replay.json()['execution_result'] == approved.json()['execution_result']
    if tool == 'conversation_create':
        assert len([row for row in client.get('/api/conversations', headers=headers).json() if row['title'] == '승인한 새 대화']) == 1
    elif tool == 'conversation_message_send':
        after = client.get(path, headers=headers).json()
        assert [row['body'] for row in after['messages'] if row['role'] == 'user'] == ['이번 주 업무를 확인해줘']
    else:
        with application._session_factory() as session:
            turns = session.query(ConversationTurnRecord).filter_by(conversation_id=UUID(target_id)).all()
            if tool == 'conversation_turn_cancel':
                assert len(turns) == 1 and turns[0].state == 'cancelled'
            else:
                assert len(turns) == 2
                assert len([row for row in turns if row.retry_of_turn_id == UUID(turn['turn_id'])]) == 1


def test_conversation_cancel_can_target_the_conversation_containing_its_approval(tmp_path):
    client, application = _stack(tmp_path)
    headers = {'X-Demo-Persona': 'mina'}
    conversation = client.post('/api/conversations', headers=headers, json={'title': '현재 대화 취소'}).json()
    path = f"/api/conversations/{conversation['conversation_id']}"
    accepted = client.post(path+'/messages', headers={**headers, 'Idempotency-Key': 'cancel-this'}, json={'body': '현재 응답을 취소할게', 'context': []}).json()
    current = client.get(path, headers=headers).json()
    with application._session_factory() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted['turn_id'])).execution_id
    proposal = application.propose_action(application.authenticated_principal('mina'), execution_id, 'conversation.turn.cancel', '현재 대화 취소 확인', {'conversation_id': conversation['conversation_id'], 'expected_version': current['version']})
    approved = client.post(f"/api/action-items/{proposal['action_id']}/commands/approve", headers=headers, json={'expected_version': proposal['version']})
    assert approved.status_code == 200, approved.text
    after = client.get(path, headers=headers)
    assert after.status_code == 200, after.text
    with application._session_factory() as session:
        assert session.get(ConversationTurnRecord, UUID(accepted['turn_id'])).state == 'cancelled'


def test_retrying_an_unknown_or_foreign_turn_has_the_same_not_found_response(tmp_path):
    from uuid import uuid4
    client, _ = _stack(tmp_path)
    headers = {'X-Demo-Persona': 'mina'}
    mine = client.post('/api/conversations', headers=headers, json={'title': '내 대화'}).json()
    foreign = client.post('/api/conversations', headers={'X-Demo-Persona': 'jiho'}, json={'title': '다른 대화'}).json()
    accepted = client.post(f"/api/conversations/{foreign['conversation_id']}/messages", headers={'X-Demo-Persona': 'jiho'}, json={'body': '비공개 메시지', 'context': []}).json()
    responses = [client.post(f"/api/conversations/{mine['conversation_id']}/turns/{identifier}/retry", headers=headers) for identifier in (accepted['turn_id'], str(uuid4()))]
    assert [response.status_code for response in responses] == [404, 404]
    assert responses[0].json() == responses[1].json()
