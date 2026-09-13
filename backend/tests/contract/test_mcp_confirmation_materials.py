"""MCP confirmations carry the same selected draft identities as browser confirmations."""
import asyncio

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from test_action_material_drafts import _stack, _proposal, JIHO


@pytest.mark.parametrize('selected', [True, False])
def test_confirmation_preserves_material_selection_and_replays_it(tmp_path, selected):
    client, application = _stack(tmp_path)
    proposal = _proposal(client, application)
    path = '/api/action-items/' + proposal['action_id']
    material = client.post(path + '/material-drafts/links', headers=JIHO, json={'url': 'https://example.com/brief', 'label': '선택할 기획서'}).json()
    item = client.get(path, headers=JIHO).json()
    server = _create_bound_persona_server(McpReportsFacade(application._settings, 'jiho'))
    tool = next(tool for tool in asyncio.run(server.list_tools()) if tool.name == 'action_item_get')
    assert {'rounds', 'discussion', 'allowed_commands'} <= tool.output_schema['properties'].keys()
    arguments = {'action_item_id': proposal['action_id'], 'command': 'confirm', 'expected_version': item['expected_version'], 'base_submission_version': item['submission_version'], 'attachment_draft_ids': [material['material_draft_id']] if selected else []}
    receipt = asyncio.run(server.call_tool('action_item_command', arguments)).structured_content
    replay = asyncio.run(server.call_tool('action_item_command', arguments)).structured_content
    assert receipt == replay
    detail = asyncio.run(server.call_tool('action_item_get', {'action_item_id': proposal['action_id']})).structured_content
    assert detail == client.get(path, headers=JIHO).json()
    materials = client.get('/api/tasks/' + receipt['derived_task_id'] + '/materials', headers=JIHO).json()
    assert [row['name'] for row in materials] == (['선택할 기획서'] if selected else [])
    changed = {**arguments, 'attachment_draft_ids': [] if selected else [material['material_draft_id']]}
    with pytest.raises(ToolError):
        asyncio.run(server.call_tool('action_item_command', changed))


def test_judgment_detail_keeps_discussion_attachments_and_revision_rounds(tmp_path):
    client, application = _stack(tmp_path)
    mina = {'X-Demo-Persona': 'mina'}
    request = client.post('/api/work-requests', headers=mina, json={'title': '확인할 요청', 'assignee_id': 'jiho'}).json()
    request_path = '/api/work-requests/' + request['request_id']
    comment = client.post(request_path + '/comments', headers=mina, json={'body': '판단 전에 읽을 근거'}).json()
    uploaded = client.post(request_path + '/comments/' + comment['comment_id'] + '/attachments', headers=mina, files={'file': ('검토.txt', b'review basis', 'text/plain')})
    assert uploaded.status_code == 201, uploaded.text
    [item] = client.get('/api/action-items', headers=JIHO).json()
    server = _create_bound_persona_server(McpReportsFacade(application._settings, 'jiho'))
    listed = asyncio.run(server.call_tool('action_item_list', {})).structured_content
    assert listed['result'] == [item]
    path = '/api/action-items/' + item['action_item_id']
    arguments = {'action_item_id': item['action_item_id']}
    before = asyncio.run(server.call_tool('action_item_get', arguments)).structured_content
    assert before == client.get(path, headers=JIHO).json()
    assert before['discussion'][0]['attachments'][0]['name'] == '검토.txt'
    receipt = asyncio.run(server.call_tool('action_item_command', {**arguments, 'command': 'adjust', 'expected_version': item['expected_version'], 'reason': '기한을 조정해 주세요', 'changes': {'due_date': '2026-09-30'}})).structured_content
    assert receipt['action_item_id'] == item['action_item_id'] and receipt['status'] == 'awaiting_revision'
    after = asyncio.run(server.call_tool('action_item_get', arguments)).structured_content
    assert after == client.get(path, headers=JIHO).json()
    assert after['rounds'][0]['decisions'][0]['suggested_changes'] == {'due_date': '2026-09-30'}
