"""Graph transport retains the same authorized relations and grouped counts."""
import asyncio

from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from test_relation_graph import JIHO, _journey, _stack


def test_graph_queries_preserve_nodes_edges_and_grouping(tmp_path):
    client, application = _stack(tmp_path)
    made = _journey(client)
    server = _create_bound_persona_server(McpReportsFacade(application._settings, 'jiho'))
    cases = [
        ('graph_search', {'query': '팀장'}, '/api/graph/search', {'q': '팀장'}),
        ('graph_neighbors', {'node': 'task:' + made['task']['task_id']}, '/api/graph/neighbors', {'node': 'task:' + made['task']['task_id']}),
        *[('graph_overview', {'view': view}, '/api/graph/overview', {'view': view}) for view in ('member', 'team', 'project')],
    ]
    for tool, arguments, route, params in cases:
        expected = client.get(route, headers=JIHO, params=params)
        assert expected.status_code == 200, expected.text
        actual = asyncio.run(server.call_tool(tool, arguments)).structured_content
        assert actual == expected.json()
