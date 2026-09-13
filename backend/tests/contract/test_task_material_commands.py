"""Material binding effects are shared by direct and approved commands."""
import asyncio
from uuid import UUID

import pytest

from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from ax_workspace.platform.persistence import AttachmentRecord, ConversationTurnRecord
from test_unified_commands import _stack, _approval_request


@pytest.mark.parametrize('delegated', [False, True, 'confirm'])
@pytest.mark.parametrize('tool', ['task_material_attach_link', 'task_material_attach_reference', 'task_material_detach'])
def test_material_binding_commands_share_owner_effects(tmp_path, monkeypatch, tool, delegated):
    client, application = _stack(tmp_path)
    headers = {'X-Demo-Persona': 'mina'}
    task = client.post('/api/tasks', headers=headers, json={'title': '자료를 연결할 업무'}).json()
    path = f"/api/tasks/{task['task_id']}/materials"
    arguments = {'task_id': task['task_id']}
    attachment_id = None
    if tool == 'task_material_attach_link':
        arguments['request'] = {'kind': 'input', 'url': 'https://example.com/spec', 'label': '공유 명세'}
    elif tool == 'task_material_attach_reference':
        meeting = client.post('/api/meetings', headers=headers, json={'title': '검토 회의', 'starts_at': '2026-09-11T10:00:00+09:00', 'ends_at': '2026-09-11T11:00:00+09:00'}).json()['meeting']
        arguments['request'] = {'kind': 'input', 'resource_type': 'meeting', 'resource_id': meeting['meeting_id']}
    else:
        material = client.post(path, headers=headers, data={'kind': 'input'}, files={'file': ('original.txt', b'preserved original', 'text/plain')}).json()
        arguments['binding_id'] = material['binding_id']
        attachment_id = material['material_id']
    before = client.get(path, headers=headers).json()
    if delegated:
        conversation = client.post('/api/conversations', headers=headers, json={'title': '자료 변경'}).json()
        turn = client.post(f"/api/conversations/{conversation['conversation_id']}/messages", headers={**headers, 'Idempotency-Key': tool}, json={'body': '이 업무의 자료 연결을 변경해줘', 'context': []}).json()
        with application._session_factory() as session:
            execution_id = session.get(ConversationTurnRecord, UUID(turn['turn_id'])).execution_id
        monkeypatch.setenv('AX_MCP_CAUSATION_ID', str(execution_id))
    server = _create_bound_persona_server(McpReportsFacade(application._settings, 'mina'))
    listed = asyncio.run(server.call_tool('task_materials_list', {'task_id': task['task_id']})).structured_content['result']
    assert listed == before
    result = asyncio.run(server.call_tool(tool, arguments))
    assert not result.is_error, result
    if delegated:
        proposal = result.structured_content
        assert client.get(path, headers=headers).json() == before
        url, body = _approval_request(client, headers, proposal, delegated)
        approved = client.post(url, headers=headers, json=body)
        assert approved.status_code == 200, approved.text
        replay = client.post(url, headers=headers, json=body)
        assert replay.status_code == 200 and replay.json()['execution_result'] == approved.json()['execution_result']
    after = client.get(path, headers=headers).json()
    if tool == 'task_material_detach':
        assert after == []
        with application._session_factory() as session:
            attachment = session.get(AttachmentRecord, UUID(attachment_id))
            assert application._materials(session)._storage.get(attachment.source_ref) == b'preserved original'
    else:
        assert len(after) == 1 and after[0]['name'] == ('공유 명세' if tool.endswith('link') else '검토 회의')
