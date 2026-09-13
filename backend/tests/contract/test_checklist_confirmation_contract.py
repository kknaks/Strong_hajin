"""Checklist confirmation answers a fixed task/step and executes edited content/order."""
import pytest

from ax_workspace.entrypoints.mcp import McpReportsFacade
from test_mcp_checklist import _stack, _task_with_steps, _delegated_turn, MINA


@pytest.mark.parametrize('operation', ['add', 'update', 'archive', 'reorder'])
def test_checklist_commands_confirm_final_typed_values_and_replay(tmp_path, monkeypatch, operation):
    _, settings, client = _stack(tmp_path)
    task_id, [first, second] = _task_with_steps(client, '첫 단계', '둘째 단계')
    _delegated_turn(client, client.app.state.workflow_application, MINA, 'mina', monkeypatch)
    facade = McpReportsFacade(settings, 'mina')
    if operation == 'add':
        proposal = facade.add_checklist_item(task_id, '제안 단계')
        edit = {'text': '확정한 새 단계'}
    elif operation == 'update':
        proposal = facade.update_checklist_item(task_id, first['item_id'], first['version'], done=True)
        edit = {'text': '확인한 단계 이름', 'done': True}
    elif operation == 'archive':
        proposal = facade.archive_checklist_item(task_id, first['item_id'], first['version'])
        edit = {}
    else:
        proposal = facade.reorder_checklist(task_id, [first['item_id'], second['item_id']])
        edit = {'item_ids': [second['item_id'], first['item_id']]}
    path = f"/api/action-items/{proposal['action_id']}"
    item = client.get(path, headers=MINA).json()
    assert item['edit_contract']['editor'] == 'command'
    if operation == 'reorder':
        field = next(field for field in item['edit_contract']['fields'] if field['id'] == 'item_ids')
        assert field['type'] == 'ordered_select'
        assert [option['label'] for option in field['options']] == ['첫 단계', '둘째 단계']
    body = {'expected_version': item['expected_version'], 'base_submission_version': item['submission_version'], 'draft': {**item['edit_contract']['values'], **edit}}
    confirmed = client.post(path + '/commands/confirm', headers=MINA, json=body)
    assert confirmed.status_code == 200, confirmed.text
    assert client.post(path + '/commands/confirm', headers=MINA, json=body).json()['execution_result'] == confirmed.json()['execution_result']
    steps = client.get(f'/api/tasks/{task_id}', headers=MINA).json()['checklist']
    if operation == 'add':
        assert steps[-1]['text'] == '확정한 새 단계'
    elif operation == 'update':
        assert steps[0]['text'] == '확인한 단계 이름' and steps[0]['done'] is True
    elif operation == 'archive':
        assert [step['item_id'] for step in steps] == [second['item_id']]
    else:
        assert [step['item_id'] for step in steps] == edit['item_ids']


@pytest.mark.parametrize('operation', ['add', 'update', 'archive', 'reorder'])
def test_checklist_tools_preserve_the_optional_task_version_guard(tmp_path, monkeypatch, operation):
    import asyncio
    from mcp.server.mcpserver.exceptions import ToolError
    from ax_workspace.modules.work.application import TaskError
    from ax_workspace.entrypoints.mcp import _create_bound_persona_server
    _, settings, client = _stack(tmp_path)
    task_id, [step] = _task_with_steps(client, '보존할 단계')
    before = client.get(f'/api/tasks/{task_id}', headers=MINA).json()
    monkeypatch.delenv('AX_MCP_CAUSATION_ID', raising=False)
    arguments = {'task_id': task_id, 'expected_task_version': before['version'] - 1}
    if operation == 'add':
        arguments['text'] = '추가하지 않을 단계'
    elif operation == 'reorder':
        arguments['item_ids'] = [step['item_id']]
    else:
        arguments.update(item_id=step['item_id'], expected_version=step['version'])
        if operation == 'update':
            arguments['done'] = True
    server = _create_bound_persona_server(McpReportsFacade(settings, 'mina'))
    with pytest.raises(ToolError) as raised:
        asyncio.run(server.call_tool('task_checklist_' + operation, arguments))
    assert isinstance(raised.value.__cause__, TaskError) and 'stale' in str(raised.value.__cause__)
    assert client.get(f'/api/tasks/{task_id}', headers=MINA).json() == before
