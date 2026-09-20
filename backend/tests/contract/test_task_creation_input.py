"""Reject invalid persisted Task input consistently before direct writes or proposal storage."""
import pytest
from ax_workspace.entrypoints.mcp import McpReportsFacade
from ax_workspace.platform.persistence import ActionItemRecord, TaskRecord
from test_mcp_checklist import _delegated_turn
from test_unified_commands import _stack


@pytest.mark.parametrize('route', ['http', 'mcp', 'delegated'])
def test_task_creation_rejects_an_oversized_title_before_storing_any_effect(tmp_path, monkeypatch, route):
    client, application = _stack(tmp_path)
    headers = {'X-Demo-Persona': 'jiho'}
    if route == 'http':
        response = client.post('/api/tasks', headers=headers, json={'title': 'x' * 301})
        assert response.status_code == 422
    else:
        if route == 'delegated':
            _delegated_turn(client, application, headers, 'jiho', monkeypatch)
        facade = McpReportsFacade(application._settings, 'jiho')
        with pytest.raises(ValueError):
            facade.create_self_task('x' * 301, 'oversized-title')
    with application._session_factory() as session:
        assert session.query(TaskRecord).count() == 0
        assert session.query(ActionItemRecord).count() == 0


def test_task_creation_tool_uses_the_same_normalized_values_and_returns_a_real_task(tmp_path, monkeypatch):
    import asyncio
    from ax_workspace.entrypoints.mcp import _create_bound_persona_server
    client, application = _stack(tmp_path)
    monkeypatch.delenv('AX_MCP_CAUSATION_ID', raising=False)
    server = _create_bound_persona_server(McpReportsFacade(application._settings, 'jiho'))
    result = asyncio.run(server.call_tool('task_create_self', {'title': '  확인할 업무  ', 'idempotency_key': 'normalized-values', 'description': '   ', 'checklist': ['  자료   읽기 ', '', ' 검토하기 '], 'start_date': '2026-09-10', 'due_date': '2026-09-30'}))
    assert not result.is_error, result
    task = result.structured_content
    assert task['title'] == '확인할 업무' and task['description'] is None
    loaded = client.get(f"/api/tasks/{task['task_id']}", headers={'X-Demo-Persona': 'jiho'}).json()
    assert [step['text'] for step in loaded['checklist']] == ['자료 읽기', '검토하기']
    assert loaded['start_date'] == '2026-09-10' and loaded['due_date'] == '2026-09-30'
