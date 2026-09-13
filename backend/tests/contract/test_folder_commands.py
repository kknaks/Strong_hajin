"""Folder approval changes membership of materials, retaining original bytes."""
import asyncio
from uuid import UUID

import pytest

from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from ax_workspace.platform.persistence import AttachmentRecord, ConversationTurnRecord
from test_unified_commands import _stack, _approval_request


@pytest.mark.parametrize('delegated', [False, True, 'confirm'])
@pytest.mark.parametrize('tool', ['material_folder_create', 'material_folder_archive', 'folder_material_detach'])
def test_folder_commands_use_owner_effects_and_preserve_the_original(tmp_path, monkeypatch, tool, delegated):
    client, application = _stack(tmp_path)
    headers = {'X-Demo-Persona': 'mina'}
    folder = client.post('/api/material-folders', headers=headers, json={'kind': 'personal', 'title': '기존 자료함'}).json()
    folder_id = folder['folder_id']
    material = client.post(f'/api/material-folders/{folder_id}/materials', headers=headers, files={'file': ('source.txt', b'original content', 'text/plain')}).json()
    arguments = {
        'material_folder_create': {'request': {'kind': 'personal', 'title': '새 자료함'}},
        'material_folder_archive': {'folder_id': folder_id},
        'folder_material_detach': {'folder_id': folder_id, 'material_id': material['material_id']},
    }[tool]
    if delegated:
        conversation = client.post('/api/conversations', headers=headers, json={'title': '자료함 변경'}).json()
        accepted = client.post(f"/api/conversations/{conversation['conversation_id']}/messages", headers={**headers, 'Idempotency-Key': tool}, json={'body': '자료함을 정리해줘', 'context': []}).json()
        with application._session_factory() as session:
            execution_id = session.get(ConversationTurnRecord, UUID(accepted['turn_id'])).execution_id
        monkeypatch.setenv('AX_MCP_CAUSATION_ID', str(execution_id))
    server = _create_bound_persona_server(McpReportsFacade(application._settings, 'mina'))
    result = asyncio.run(server.call_tool(tool, arguments))
    assert not result.is_error
    if delegated:
        proposal = result.structured_content
        assert proposal['state'] == 'pending' and proposal['preview']
        assert len(client.get('/api/material-folders', headers=headers).json()) == 1
        assert client.get(material['origin'], headers=headers).content == b'original content'
        url, payload = _approval_request(client, headers, proposal, delegated)
        approved = client.post(url, headers=headers, json=payload)
        assert approved.status_code == 200, approved.text
        replay = client.post(url, headers=headers, json=payload)
        assert replay.status_code == 200, replay.text
        assert replay.json()['execution_result'] == approved.json()['execution_result']
    folders = client.get('/api/material-folders', headers=headers).json()
    if tool == 'material_folder_create':
        assert len([row for row in folders if row['title'] == '새 자료함']) == 1
    elif tool == 'material_folder_archive':
        assert folders == []
    else:
        assert client.get(f'/api/material-folders/{folder_id}/materials', headers=headers).json() == []
    with application._session_factory() as session:
        original = session.get(AttachmentRecord, UUID(material['material_id']))
        assert original.lifecycle != 'purged'
        assert application._material_storage.get(original.source_ref) == b'original content'


def test_a_proposal_whose_target_vanished_offers_no_editor(tmp_path, monkeypatch):
    """대상이 사라진 제안은 거절만 남는다 — 제출할 수 없는 편집기를 함께 내려보내지 않는다."""
    client, application = _stack(tmp_path)
    headers = {'X-Demo-Persona': 'mina'}
    folder = client.post(
        '/api/material-folders', headers=headers, json={'kind': 'personal', 'title': '곧 사라질 자료함'}
    ).json()
    conversation = client.post('/api/conversations', headers=headers, json={'title': '자료함 정리'}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**headers, 'Idempotency-Key': 'vanished-folder'},
        json={'body': '자료함을 정리해줘', 'context': []},
    ).json()
    with application._session_factory() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted['turn_id'])).execution_id
    monkeypatch.setenv('AX_MCP_CAUSATION_ID', str(execution_id))
    server = _create_bound_persona_server(McpReportsFacade(application._settings, 'mina'))
    proposed = asyncio.run(
        server.call_tool('material_folder_archive', {'folder_id': folder['folder_id']})
    )
    assert not proposed.is_error

    # 사람이 확인하기 전에 자료함이 화면에서 먼저 정리됐다.
    removed = client.post(f"/api/material-folders/{folder['folder_id']}/archive", headers=headers)
    assert removed.status_code in {200, 204}, removed.text

    item = client.get(
        f"/api/action-items/{proposed.structured_content['action_id']}", headers=headers
    ).json()
    assert [command['id'] for command in item['allowed_commands']] == ['reject']
    assert item.get('edit_contract') is None
    assert (item.get('extra') or {}).get('edit_contract') is None
