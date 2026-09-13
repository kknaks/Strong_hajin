"""Task read contracts retain the workspace, provenance, and immutable history."""
import asyncio
from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from test_unified_commands import _stack


def test_task_queries_keep_the_complete_workspace_and_history(tmp_path):
    client, application = _stack(tmp_path)
    headers = {'X-Demo-Persona': 'mina'}
    task = client.post('/api/tasks', headers=headers, json={'title': '진행을 확인할 업무'}).json()
    task_id = task['task_id']
    path = '/api/tasks/' + task_id
    client.post(path + '/checklist', headers=headers, json={'text': '초안 확인'})
    server = _create_bound_persona_server(McpReportsFacade(application._settings, 'mina'))
    tool = next(row for row in asyncio.run(server.list_tools()) if row.name == 'task_get')
    assert {'origin', 'assignee', 'checklist', 'children', 'delivery'} <= tool.output_schema['properties'].keys()
    for name, arguments, route in [
        ('my_task_list', {}, '/api/my-work'),
        ('task_list', {}, '/api/tasks'),
        ('task_get', {'task_id': task_id}, path),
        ('task_history', {'task_id': task_id}, path + '/history'),
        ('task_history_diff', {'task_id': task_id, 'before': 1, 'after': 2}, path + '/history/diff?from=1&to=2'),
    ]:
        expected = client.get(route, headers=headers)
        assert expected.status_code == 200, expected.text
        result = asyncio.run(server.call_tool(name, arguments)).structured_content
        assert (result['result'] if isinstance(expected.json(), list) else result) == expected.json()

    detail = client.get(path, headers=headers).json()
    steps = asyncio.run(server.call_tool('task_checklist_list', {'task_id': task_id})).structured_content
    assert steps == {'task_id': task_id, 'checklist': detail['checklist'], 'progress': detail['checklist_progress']}
    parts = asyncio.run(server.call_tool('task_subtask_list', {'task_id': task_id})).structured_content
    assert parts == {'task_id': task_id, 'parent': detail['parent'], 'children': detail['children'], 'progress': detail['child_progress']}
