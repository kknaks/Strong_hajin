"""Preparation affects the named pending approval; final creation claims selected materials."""
import asyncio
from uuid import UUID

import pytest
from test_unified_commands import _approval_request

from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from ax_workspace.platform.persistence import ConversationTurnRecord
from test_action_material_drafts import _proposal, _stack, JIHO


@pytest.mark.parametrize('delegated', [False, True, 'confirm'])
@pytest.mark.parametrize('tool', ['action_material_link_stage', 'action_material_draft_discard'])
def test_action_material_commands_prepare_only_the_bound_creation(tmp_path, monkeypatch, tool, delegated):
    client, application = _stack(tmp_path)
    parent = _proposal(client, application)
    parent_id = parent['action_id']
    path = f'/api/action-items/{parent_id}'
    arguments = {'action_item_id': parent_id}
    if tool == 'action_material_link_stage':
        arguments['request'] = {'url': 'https://example.com/brief', 'label': '검토 문서'}
    else:
        staged = client.post(path+'/material-drafts/links', headers=JIHO, json={'url': 'https://example.com/brief', 'label': '검토 문서'}).json()
        arguments['material_draft_id'] = staged['material_draft_id']
    before = client.get(path, headers=JIHO).json()['material_drafts']
    if delegated:
        control = client.post('/api/conversations', headers=JIHO, json={'title': '자료 준비'}).json()
        turn = client.post(f"/api/conversations/{control['conversation_id']}/messages", headers={**JIHO, 'Idempotency-Key': tool}, json={'body': '승인 대기 업무의 자료를 준비해줘', 'context': []}).json()
        with application._session_factory() as session:
            execution_id = session.get(ConversationTurnRecord, UUID(turn['turn_id'])).execution_id
        monkeypatch.setenv('AX_MCP_CAUSATION_ID', str(execution_id))
    server = _create_bound_persona_server(McpReportsFacade(application._settings, 'jiho'))
    result = asyncio.run(server.call_tool(tool, arguments))
    assert not result.is_error, result
    if delegated:
        proposal = result.structured_content
        assert client.get(path, headers=JIHO).json()['material_drafts'] == before
        url, payload = _approval_request(client, JIHO, proposal, delegated)
        approved = client.post(url, headers=JIHO, json=payload)
        assert approved.status_code == 200, approved.text
        replay = client.post(url, headers=JIHO, json=payload)
        assert replay.status_code == 200 and replay.json()['execution_result'] == approved.json()['execution_result']
    item = client.get(path, headers=JIHO).json()
    assert client.get('/api/my-work', headers=JIHO).json() == []
    if tool == 'action_material_draft_discard':
        assert item['material_drafts'] == []
    else:
        assert len(item['material_drafts']) == 1 and item['material_drafts'][0]['name'] == '검토 문서'
        confirmed = client.post(path+'/commands/confirm', headers=JIHO, json={'expected_version': item['expected_version'], 'base_submission_version': item['submission_version'], 'attachment_draft_ids': [item['material_drafts'][0]['material_draft_id']]})
        assert confirmed.status_code == 200, confirmed.text
        material = client.get(f"/api/tasks/{confirmed.json()['derived_task_id']}/materials", headers=JIHO).json()
        assert len(material) == 1 and material[0]['url'] == 'https://example.com/brief'


def test_material_preparation_refreshes_the_creation_state_after_taking_its_lock(tmp_path):
    from ax_workspace.modules.work.action_materials import ActionMaterialNotFound
    from ax_workspace.platform.persistence import ActionItemRecord, ActionMaterialDraftRecord
    client, application = _stack(tmp_path)
    parent = _proposal(client, application)
    parent_id = UUID(parent['action_id'])
    principal = application.authenticated_principal('jiho')
    with application._session_factory() as session:
        cached = session.get(ActionItemRecord, parent_id)
        assert cached.state == 'pending'
        item = client.get(f'/api/action-items/{parent_id}', headers=JIHO).json()
        confirmed = client.post(f'/api/action-items/{parent_id}/commands/confirm', headers=JIHO, json={'expected_version': item['expected_version'], 'base_submission_version': item['submission_version']})
        assert confirmed.status_code == 200, confirmed.text
        with pytest.raises(ActionMaterialNotFound):
            application._action_materials(session).stage_link(principal, parent_id, url='https://example.com/late', label='늦게 도착한 자료')
        assert session.query(ActionMaterialDraftRecord).filter_by(action_id=parent_id).count() == 0
