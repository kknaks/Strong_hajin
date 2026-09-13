"""Project commands retain participation boundaries through MCP confirmation."""
import asyncio
from uuid import UUID

import pytest

from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from ax_workspace.platform.persistence import ConversationTurnRecord
from test_unified_commands import _stack, _approval_request
from test_action_center import _ax_proposal


@pytest.mark.parametrize('delegated', [False, True, 'confirm'])
@pytest.mark.parametrize('tool', ['project_create', 'project_assign_member', 'project_release_member', 'project_plan_work'])
def test_project_tools_apply_the_same_owner_commands_after_confirmation(tmp_path, monkeypatch, tool, delegated):
    client, application = _stack(tmp_path)
    headers = {'X-Demo-Persona': 'jiho'}
    project = client.post('/api/projects', headers=headers, json={'name': '기존 프로젝트'}).json()
    project_id = project['project_id']
    if tool == 'project_release_member':
        assigned = client.post(f'/api/projects/{project_id}/members', headers=headers, json={'member_id': 'hyeon'})
        assert assigned.status_code == 201
    arguments = {
        'project_create': {'request': {'name': '새 프로젝트'}},
        'project_assign_member': {'project_id': project_id, 'request': {'member_id': 'hyeon'}},
        'project_release_member': {'project_id': project_id, 'member_id': 'hyeon'},
        'project_plan_work': {'project_id': project_id, 'request': {'title': '담당 미정 계획'}},
    }[tool]
    if tool == 'project_release_member':
        arguments['assignment_id'] = assigned.json()['assignment_id']
    if delegated:
        conversation = client.post('/api/conversations', headers=headers, json={'title': '프로젝트 변경'}).json()
        accepted = client.post(f"/api/conversations/{conversation['conversation_id']}/messages", headers={**headers, 'Idempotency-Key': tool}, json={'body': '프로젝트를 변경해줘', 'context': []}).json()
        with application._session_factory() as session:
            execution_id = session.get(ConversationTurnRecord, UUID(accepted['turn_id'])).execution_id
        monkeypatch.setenv('AX_MCP_CAUSATION_ID', str(execution_id))
    server = _create_bound_persona_server(McpReportsFacade(application._settings, 'jiho'))
    result = asyncio.run(server.call_tool(tool, arguments))
    assert not result.is_error
    if delegated:
        proposal = result.structured_content
        assert proposal['state'] == 'pending' and proposal['preview']
        before = client.get(f'/api/projects/{project_id}', headers=headers).json()
        assert before['tasks'] == []
        if tool == 'project_assign_member':
            assert 'hyeon' not in {member['member_id'] for member in before['members']}
        if tool == 'project_release_member':
            assert 'hyeon' in {member['member_id'] for member in before['members']}
        url, payload = _approval_request(client, headers, proposal, delegated)
        approved = client.post(url, headers=headers, json=payload)
        assert approved.status_code == 200, approved.text
        replay = client.post(url, headers=headers, json=payload)
        assert replay.status_code == 200 and replay.json() == approved.json()
    after = client.get(f'/api/projects/{project_id}', headers=headers).json()
    if tool == 'project_create':
        rows = client.get('/api/projects', headers=headers).json()
        assert len([row for row in rows if row['name'] == '새 프로젝트']) == 1
    elif tool == 'project_assign_member':
        assert 'hyeon' in {member['member_id'] for member in after['members']}
        assert client.get(f'/api/projects/{project_id}', headers={'X-Demo-Persona': 'hyeon'}).status_code == 200
    elif tool == 'project_release_member':
        assert 'hyeon' not in {member['member_id'] for member in after['members']}
        assert client.get(f'/api/projects/{project_id}', headers={'X-Demo-Persona': 'hyeon'}).status_code == 404
    else:
        assert [task['title'] for task in after['tasks']] == ['담당 미정 계획']


def test_project_self_release_keeps_the_approval_receipt_after_access_is_gone(tmp_path):
    client, application = _stack(tmp_path)
    headers = {'X-Demo-Persona': 'jiho'}
    project = client.post('/api/projects', headers=headers, json={'name': '참여 해제'}).json()
    detail = client.get(f"/api/projects/{project['project_id']}", headers=headers).json()
    assignment_id = next(member['assignment_id'] for member in detail['members'] if member['member_id'] == 'jiho')
    proposal = _ax_proposal(client, application, headers, 'jiho', 'project.release_member', '프로젝트 참여 해제 확인', {
        'project_id': project['project_id'], 'member_id': 'jiho', 'assignment_id': assignment_id,
    })
    url = f"/api/action-items/{proposal['action_id']}/commands/approve"
    payload = {'expected_version': proposal['version']}
    approved = client.post(url, headers=headers, json=payload)
    assert approved.status_code == 200, approved.text
    assert client.get(f"/api/projects/{project['project_id']}", headers=headers).status_code == 404
    replay = client.post(url, headers=headers, json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()['execution_result'] == approved.json()['execution_result']


def test_old_round_approval_cannot_end_a_new_participation_and_history_is_authorized(tmp_path):
    client, application = _stack(tmp_path)
    headers = {'X-Demo-Persona': 'jiho'}
    project = client.post('/api/projects', headers=headers, json={'name': '회차 경계'}).json()
    project_id = project['project_id']
    first = client.post(f'/api/projects/{project_id}/members', headers=headers, json={'member_id': 'hyeon'}).json()
    proposal = _ax_proposal(client, application, headers, 'jiho', 'project.release_member', '프로젝트 참여 해제 확인', {
        'project_id': project_id, 'member_id': 'hyeon', 'assignment_id': first['assignment_id'], 'reason': '예정된 종료',
    })
    ended = client.request('DELETE', f'/api/projects/{project_id}/members/hyeon', headers=headers, json={'assignment_id': first['assignment_id'], 'reason': '화면에서 종료'})
    assert ended.status_code == 204, ended.text
    second = client.post(f'/api/projects/{project_id}/members', headers=headers, json={'member_id': 'hyeon'}).json()
    assert second['assignment_id'] != first['assignment_id']
    approved = client.post(f"/api/action-items/{proposal['action_id']}/commands/approve", headers=headers, json={'expected_version': proposal['version']})
    assert approved.status_code == 200, approved.text
    assert approved.json()['execution_result']['assignment_id'] == first['assignment_id']
    current = client.get(f'/api/projects/{project_id}', headers=headers).json()
    assert next(member['assignment_id'] for member in current['members'] if member['member_id'] == 'hyeon') == second['assignment_id']
    server = _create_bound_persona_server(McpReportsFacade(application._settings, 'jiho'))
    result = asyncio.run(server.call_tool('project_participation_history', {'project_id': project_id}))
    history = result.structured_content['result']
    assert history == client.get(f'/api/projects/{project_id}/participation-history', headers=headers).json()
    assert asyncio.run(server.call_tool('get_project', {'project_id': project_id})).structured_content == current
    assert asyncio.run(server.call_tool('list_projects', {})).structured_content['result'] == client.get('/api/projects', headers=headers).json()
    old = next(row for row in history if row['assignment_id'] == first['assignment_id'])
    assert old['ended_at'] is not None and old['end_reason'] == '화면에서 종료'
    assert next(row for row in history if row['assignment_id'] == second['assignment_id'])['ended_at'] is None
    from mcp.server.mcpserver.exceptions import ToolError
    from ax_workspace.modules.errors import RESOURCE_NOT_FOUND_MESSAGE
    denied = _create_bound_persona_server(McpReportsFacade(application._settings, 'mina'))
    with pytest.raises(ToolError, match=RESOURCE_NOT_FOUND_MESSAGE):
        asyncio.run(denied.call_tool('project_participation_history', {'project_id': project_id}))
