import asyncio
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
import pytest

from ax.auth import seeded_principal
from ax.mcp_server import McpWorkflowFacade, create_mcp_server
from ax.reset_demo import reset_database
from ax.settings import RuntimeProfile, Settings


def test_mcp_facade_discovers_allowed_workflows_and_completes_a_golden_run(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    facade = McpWorkflowFacade(Settings(RuntimeProfile.TEST, database_url), seeded_principal("mina"))

    catalog = facade.list_allowed_workflows()
    assert "daily-report" in {item["workflow_id"] for item in catalog}
    assert "contract-review" not in {item["workflow_id"] for item in catalog}

    started = facade.start_workflow("daily-report", {})
    assert started["state"] == "waiting_for_decision"
    assert facade.list_decision_inbox()[0]["node_id"] == "confirm"
    completed = facade.submit_decision(started["run_id"], "confirm", "accept")
    assert completed["state"] == "completed"


def test_unbound_mcp_server_fails_closed_instead_of_accepting_a_caller_persona(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AX_MCP_PERSONA", raising=False)

    with pytest.raises(RuntimeError, match="AX_MCP_PERSONA"):
        create_mcp_server(Settings(RuntimeProfile.TEST, "sqlite:///:memory:"))


def test_persona_bound_mcp_server_discovers_only_that_personas_start_tools() -> None:
    mina_server = create_mcp_server(Settings(RuntimeProfile.TEST, "sqlite:///:memory:"), persona="mina")
    admin_server = create_mcp_server(Settings(RuntimeProfile.TEST, "sqlite:///:memory:"), persona="demo-admin")

    mina_tools = {tool.name for tool in asyncio.run(mina_server.list_tools())}
    admin_tools = {tool.name for tool in asyncio.run(admin_server.list_tools())}

    assert "start_daily_report" in mina_tools
    assert "start_contract_review" not in mina_tools
    assert "start_contract_review" in admin_tools
    assert "submit_my_workflow_decision" in mina_tools
    assert "start_workflow" not in mina_tools
    mina_schemas = {tool.name: tool.inputSchema for tool in asyncio.run(mina_server.list_tools())}
    assert all("persona" not in schema.get("properties", {}) for schema in mina_schemas.values())


def test_stdio_mcp_client_discovers_and_starts_a_seeded_workflow(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)

    async def scenario() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "ax.mcp_server"],
            cwd=os.getcwd(),
            env={**os.environ, "AX_PROFILE": "test", "DATABASE_URL": database_url, "AX_MCP_PERSONA": "mina"},
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                assert "start_daily_report" in names
                assert "start_contract_review" not in names
                assert "start_workflow" not in names
                started = await session.call_tool(
                    "start_daily_report",
                    {"input_data": {}},
                )
                assert started.structuredContent["state"] == "waiting_for_decision"

    asyncio.run(scenario())
