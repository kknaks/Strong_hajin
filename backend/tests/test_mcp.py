import asyncio
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
import pytest

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.mcp import McpReportsFacade, create_mcp_server
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.ax_execution.ai import AiGeneration


class ContractTestAiProvider:
    def generate(self, request) -> AiGeneration:
        return AiGeneration(
            provider_run_ref="run_mcp_contract_test",
            provider_session_ref="thread_mcp_contract_test",
            body="MCP에서 생성한 보고 초안입니다.",
            requested_model="gpt-5.6-terra",
            observed_model="gpt-5.6-terra",
            requested_tier="fast",
            observed_tier="fast",
            latency_ms=1,
            usage={"input_tokens": 1, "output_tokens": 1},
        )


def test_mcp_facade_uses_direct_daily_report_operations(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    facade = McpReportsFacade(
        Settings(RuntimeProfile.TEST, database_url),
        "mina",
        ContractTestAiProvider(),
    )

    draft = facade.generate_daily_report_draft("2026-09-03")
    assert draft["workflow_state"] == "completed"
    assert draft["submission_status"] == "unsubmitted"
    edited = facade.edit_daily_report(
        draft["report_id"], draft["draft_id"], draft["draft_version"], "MCP에서 수정한 초안"
    )
    submitted = facade.submit_daily_report(
        draft["report_id"], edited["draft_id"], edited["draft_version"]
    )
    assert submitted["body"] == "MCP에서 수정한 초안"


def test_mcp_facade_uses_the_work_request_public_operations(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url)
    mina = McpReportsFacade(settings, "mina", ContractTestAiProvider())
    jiho = McpReportsFacade(settings, "jiho", ContractTestAiProvider())

    assert mina.work_request_assignee_candidates() == [{"id": "jiho", "display_name": "지호 (팀장)"}]
    created = mina.create_work_request("MCP 업무 요청", "jiho")
    assert mina.list_work_requests() == [created]
    assert jiho.get_work_request(created["request_id"]) == created

    accepted = jiho.accept_work_request(created["request_id"], created["version"])
    assert accepted["task_id"]
    assert jiho.list_work_requests()[0]["state"] == "accepted"


def test_unbound_mcp_server_fails_closed_instead_of_accepting_a_caller_persona(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AX_MCP_PERSONA", raising=False)

    with pytest.raises(RuntimeError, match="AX_MCP_PERSONA"):
        create_mcp_server(Settings(RuntimeProfile.TEST, "sqlite:///:memory:"))


def test_mcp_tool_exposure_is_bound_to_the_server_persona(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url)

    monkeypatch.setenv("AX_MCP_PERSONA", "mina")
    mina_tools = {tool.name for tool in asyncio.run(create_mcp_server(settings).list_tools())}
    monkeypatch.setenv("AX_MCP_PERSONA", "sora")
    sora_tools = {tool.name for tool in asyncio.run(create_mcp_server(settings).list_tools())}

    assert "daily_report_generate_draft" in mina_tools
    assert "daily_report_generate_draft" not in sora_tools
    assert "work_request_create" in mina_tools
    assert "work_request_accept" in mina_tools
    assert "work_request_create" not in sora_tools
    assert "start_daily_report" not in mina_tools
    assert all("persona" not in tool.name for tool in asyncio.run(create_mcp_server(settings).list_tools()))


def test_stdio_mcp_client_discovers_only_persona_bound_report_tools(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)

    async def scenario() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "ax_workspace.entrypoints.mcp"],
            cwd=os.getcwd(),
            env={
                **os.environ,
                "AX_PROFILE": "test",
                "DATABASE_URL": database_url,
                "AX_MCP_PERSONA": "mina",
            },
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                assert names == {
                    "daily_report_edit",
                    "daily_report_generate_draft",
                    "daily_report_history",
                    "daily_report_submit",
                    "work_request_accept",
                    "work_request_assignee_candidates",
                    "work_request_create",
                    "work_request_get",
                    "work_request_list",
                    "work_request_negotiate",
                    "work_request_reject",
                }

    asyncio.run(scenario())
