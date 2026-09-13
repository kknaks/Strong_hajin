"""Request results keep the frozen decision basis and discussion distinct."""
import asyncio
from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from test_unified_commands import _stack


def test_request_queries_retain_references_evidence_comments_and_decisions(tmp_path):
    client, application = _stack(tmp_path)
    mina = {'X-Demo-Persona': 'mina'}
    jiho = {'X-Demo-Persona': 'jiho'}
    task = client.post('/api/tasks', headers=mina, json={'title': '참고 업무'}).json()
    request = client.post('/api/work-requests', headers=mina, json={'title': '검토 요청', 'assignee_id': 'jiho', 'reference_task_ids': [task['task_id']]}).json()
    path = '/api/work-requests/' + request['request_id']
    client.post(path + '/comments', headers=mina, json={'body': '검토 맥락'})
    evidence = client.post(path + '/evidence', headers=mina, files={'file': ('요청.txt', b'basis', 'text/plain')})
    assert evidence.status_code == 201, evidence.text
    request = client.get(path, headers=mina).json()
    rejected = client.post(path + '/reject', headers=jiho, json={'expected_version': request['version'], 'reason': '범위 조정 필요'})
    assert rejected.status_code == 200, rejected.text
    server = _create_bound_persona_server(McpReportsFacade(application._settings, 'mina'))
    tool = next(row for row in asyncio.run(server.list_tools()) if row.name == 'work_request_history')
    assert {'submissions', 'review_decisions', 'evidence', 'comments'} <= tool.output_schema['properties'].keys()
    for name, arguments, route in [
        ('work_request_list', {}, '/api/work-requests'),
        ('work_request_get', {'request_id': request['request_id']}, path),
        ('work_request_history', {'request_id': request['request_id']}, path + '/timeline'),
    ]:
        expected = client.get(route, headers=mina)
        assert expected.status_code == 200, expected.text
        result = asyncio.run(server.call_tool(name, arguments)).structured_content
        assert (result['result'] if isinstance(expected.json(), list) else result) == expected.json()
