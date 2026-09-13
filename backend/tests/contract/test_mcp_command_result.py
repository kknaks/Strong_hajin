"""Typed command results must retain the existing object shape on the real MCP SDK."""
import asyncio
from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from test_mcp_checklist import _delegated_turn
from test_unified_commands import _stack


def test_command_schema_and_both_results_keep_their_top_level_identities(tmp_path, monkeypatch):
    client, application = _stack(tmp_path)
    headers = {'X-Demo-Persona': 'mina'}
    server = _create_bound_persona_server(McpReportsFacade(application._settings, 'mina'))
    tool = next(tool for tool in asyncio.run(server.list_tools()) if tool.name == 'task_create_self')
    assert tool.output_schema['type'] == 'object'
    assert len(tool.output_schema['anyOf']) == 2
    direct = asyncio.run(server.call_tool('task_create_self', {'title': '직접 생성'})).structured_content
    assert 'task_id' in direct and 'result' not in direct
    _delegated_turn(client, application, headers, 'mina', monkeypatch)
    prepared = asyncio.run(server.call_tool('task_create_self', {'title': '준비한 원안'})).structured_content
    assert 'action_id' in prepared and prepared['state'] == 'pending'
    assert prepared['edit_contract']['values']['title'] == '준비한 원안'
    assert prepared['material_drafts'] == [] and prepared['material_results'] == []
    assert prepared['commands'][0]['id'] == 'confirm'
    stored = next(row for row in client.get('/api/actions', headers=headers).json() if row['action_id'] == prepared['action_id'])
    assert prepared == stored
