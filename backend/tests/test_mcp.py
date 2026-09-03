import asyncio
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from ax.mcp_server import McpWorkflowFacade, create_mcp_server
from ax.reset_demo import reset_database
from ax.settings import RuntimeProfile, Settings


def test_mcp_facade_discovers_allowed_workflows_and_completes_a_golden_run(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    facade = McpWorkflowFacade(Settings(RuntimeProfile.TEST, database_url))

    catalog = facade.list_allowed_workflows("mina")
    assert "daily-report" in {item["workflow_id"] for item in catalog}
    assert "contract-review" not in {item["workflow_id"] for item in catalog}

    started = facade.start_workflow("mina", "daily-report", {})
    assert started["state"] == "waiting_for_decision"
    assert facade.list_decision_inbox("mina")[0]["node_id"] == "confirm"
    completed = facade.submit_decision("mina", started["run_id"], "confirm", "accept")
    assert completed["state"] == "completed"


def test_mcp_server_exposes_only_the_documented_workflow_tools() -> None:
    server = create_mcp_server(Settings(RuntimeProfile.TEST, "sqlite:///:memory:"))
    tools = asyncio.run(server.list_tools())
    assert {tool.name for tool in tools} == {
        "list_allowed_workflows",
        "start_workflow",
        "get_workflow_run",
        "list_decision_inbox",
        "submit_workflow_decision",
    }


def test_persona_bound_mcp_server_discovers_only_that_personas_start_tools() -> None:
    mina_server = create_mcp_server(Settings(RuntimeProfile.TEST, "sqlite:///:memory:"), persona="mina")
    admin_server = create_mcp_server(Settings(RuntimeProfile.TEST, "sqlite:///:memory:"), persona="demo-admin")

    mina_tools = {tool.name for tool in asyncio.run(mina_server.list_tools())}
    admin_tools = {tool.name for tool in asyncio.run(admin_server.list_tools())}

    assert "start_daily_report" in mina_tools
    assert "start_contract_review" not in mina_tools
    assert "start_contract_review" in admin_tools
    assert "submit_my_workflow_decision" in mina_tools


def test_stdio_mcp_client_discovers_and_starts_a_seeded_workflow(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)

    async def scenario() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "ax.mcp_server"],
            cwd=os.getcwd(),
            env={**os.environ, "AX_PROFILE": "test", "DATABASE_URL": database_url},
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert "start_workflow" in {tool.name for tool in tools.tools}
                catalog = await session.call_tool("list_allowed_workflows", {"persona": "mina"})
                assert catalog.structuredContent is not None
                started = await session.call_tool(
                    "start_workflow",
                    {"persona": "mina", "workflow_id": "daily-report", "input_data": {}},
                )
                assert started.structuredContent["state"] == "waiting_for_decision"

    asyncio.run(scenario())
