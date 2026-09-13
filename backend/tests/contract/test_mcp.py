import asyncio
import os
import sys
from uuid import UUID, uuid4

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
import pytest
from sqlalchemy import delete, select

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.bootstrap.report_worker import DailyReportGenerationWorker
from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server, create_mcp_server
from ax_workspace.modules.organization_access.domain import Principal, TASK_READ, TASK_SELF_MANAGE, WORK_REQUEST_READ
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.ax_execution.ai import AiGeneration
from ax_workspace.modules.work.application import TaskAccessDenied
from ax_workspace.modules.work.requests import WorkRequestAccessDenied
from ax_workspace.modules.reports.application import DailyReportAccessDenied
from ax_workspace.platform.persistence import (
    ActionItemRecord,
    RoleCapabilityRecord,
    TaskActivityRecord,
    TaskRecord,
    WorkflowRunRecord,
    make_session_factory,
)
from ax_workspace.platform.persistence import ConversationTurnRecord
from ax_workspace.bootstrap.application import create_workflow_application


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


class CapabilityFacade:
    def __init__(self, principal: Principal) -> None:
        self._principal = principal

    @property
    def principal(self) -> Principal:
        return self._principal


def test_task_tool_discovery_separates_read_from_self_manage() -> None:
    read_only = CapabilityFacade(
        Principal("mina", "읽기", frozenset({"scax"}), frozenset({TASK_READ}))
    )
    manage_only = CapabilityFacade(
        Principal("mina", "관리", frozenset({"scax"}), frozenset({TASK_SELF_MANAGE}))
    )

    read_tools = {tool.name for tool in asyncio.run(_create_bound_persona_server(read_only).list_tools())}
    manage_tools = {tool.name for tool in asyncio.run(_create_bound_persona_server(manage_only).list_tools())}

    assert {"task_list", "task_get"} <= read_tools
    assert "task_create_self" not in read_tools
    assert {"task_create_self", "task_start", "task_cancel"} <= manage_tools
    assert "task_list" not in manage_tools


@pytest.mark.parametrize("capability", [TASK_READ, WORK_REQUEST_READ])
def test_graph_discovery_names_all_supported_nodes_and_only_canonical_arguments(capability) -> None:
    facade = CapabilityFacade(Principal("mina", "읽기", frozenset({"scax"}), frozenset({capability})))
    tools = {tool.name: tool for tool in asyncio.run(_create_bound_persona_server(facade).list_tools())}
    search = tools["graph_search"]
    neighbors = tools["graph_neighbors"]
    for kind in ("person", "team", "project", "task", "work_request", "meeting"):
        assert f"`{kind}`" in search.description
    for kind in ("person", "team", "project", "task", "work_request", "meeting", "material", "report"):
        assert f"`{kind}:<id>`" in neighbors.description
    assert set(search.input_schema["properties"]) == {"query", "limit"}
    assert search.input_schema["required"] == ["query"]
    assert set(neighbors.input_schema["properties"]) == {"node", "limit"}
    assert neighbors.input_schema["required"] == ["node"]
    assert "one hop" in neighbors.description and "never grants access" in neighbors.description
    assert "evidence" in search.description


def test_mcp_facade_uses_direct_daily_report_operations(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url)
    provider = ContractTestAiProvider()
    facade = McpReportsFacade(
        settings,
        "mina",
        provider,
    )

    accepted = facade.generate_daily_report_draft("2026-09-03")
    assert accepted["generation_status"] == "queued"
    worker = DailyReportGenerationWorker(
        settings,
        provider=provider,
        queue_factory=lambda _session: facade._application.memory_job_queue,
    )
    assert asyncio.run(worker.run_once())
    status = facade._application.daily_report_status(facade.principal, "2026-09-03")
    assert status["generation_status"] == "completed" and status["status"] == "draft"
    history = facade.daily_report_history(status["report_id"])
    [generated] = history["drafts"]
    edited = facade.edit_daily_report(
        history["report_id"], generated["draft_id"], generated["version"], "MCP에서 수정한 초안"
    )
    submitted = facade.submit_daily_report(
        history["report_id"], edited["draft_id"], edited["draft_version"]
    )
    assert submitted["body"] == "MCP에서 수정한 초안"


def test_delegated_daily_report_generation_reuses_one_draft_and_workflow_run(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    execution_id = uuid4()
    monkeypatch.setenv("AX_MCP_CAUSATION_ID", str(execution_id))
    settings = Settings(RuntimeProfile.TEST, database_url)
    facade = McpReportsFacade(
        settings,
        "mina",
        ContractTestAiProvider(),
    )

    first = facade.generate_daily_report_draft("2026-09-03")
    retried = facade.generate_daily_report_draft("2026-09-03")

    assert retried["report_id"] == first["report_id"]
    assert retried["generation_id"] == first["generation_id"]
    assert retried["generation_status"] == "queued"
    assert len(facade._application.memory_job_queue.snapshot()) == 1
    worker = DailyReportGenerationWorker(
        settings,
        provider=facade._application._report_provider,
        queue_factory=lambda _session: facade._application.memory_job_queue,
    )
    assert asyncio.run(worker.run_once())
    completed = facade._application.daily_report_status(facade.principal, "2026-09-03")
    assert completed["generation_status"] == "completed"
    assert len(facade.daily_report_history(completed["report_id"])["drafts"]) == 1
    with make_session_factory(database_url)() as session:
        assert session.query(WorkflowRunRecord).count() == 1


def test_mcp_facade_uses_the_work_request_public_operations(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url)
    mina = McpReportsFacade(settings, "mina", ContractTestAiProvider())
    jiho = McpReportsFacade(settings, "jiho", ContractTestAiProvider())

    assert mina.work_request_assignee_candidates() == [{"id": "jiho", "display_name": "지호 (팀장)"}, {"id": "yuna", "display_name": "유나 (대표)"}]
    created = mina.create_work_request("MCP 업무 요청", "jiho")
    assert mina.list_work_requests() == [created]
    # The detail read adds the earlier work pointed at and the meeting this request came out of;
    # everything else is the same row the list gave. `source_meeting_title` lives only here on purpose —
    # a list of requests must not walk to a meeting per row (D40).
    detail = jiho.get_work_request(created["request_id"])
    assert detail["references"] == []
    assert detail["source_meeting_title"] is None
    detail_only = {"references", "source_meeting_title"}
    assert {key: value for key, value in detail.items() if key not in detail_only} == created

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
    monkeypatch.setenv("AX_MCP_PERSONA", "jiho")
    jiho_tools = {tool.name for tool in asyncio.run(create_mcp_server(settings).list_tools())}

    assert "daily_report_generate_draft" in mina_tools
    assert "daily_report_generate_draft" not in sora_tools
    assert "task_create_self" in mina_tools
    assert "task_start" in mina_tools
    assert "work_request_create" in mina_tools
    # Judgement has one surface: the per-kind decision tools are not registered for anyone.
    assert "work_request_accept" not in mina_tools
    assert "action_item_command" in mina_tools
    assert "work_request_create" not in sora_tools
    assert "task_create_self" not in sora_tools
    assert "daily_report_submit" not in jiho_tools
    assert "work_request_accept" not in jiho_tools
    assert "action_item_command" in jiho_tools
    assert "start_daily_report" not in mina_tools
    assert all("persona" not in tool.name for tool in asyncio.run(create_mcp_server(settings).list_tools()))


def test_stdio_mcp_client_discovers_persona_bound_tools(tmp_path) -> None:
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
                assert names >= {
                    "action_item_command",
                    "action_item_get",
                    "action_item_list",
                    "conversation_search",
                    "daily_report_edit",
                    "daily_report_generate_draft",
                    "daily_report_history",
                    "daily_report_submit",
                    "work_request_amend",
                    "work_request_assignee_candidates",
                    "work_request_create",
                    "work_request_get",
                    "work_request_history",
                    "work_request_list",
                    "graph_neighbors",
                    "graph_overview",
                    "graph_search",
                    "meeting_get",
                    "meeting_list",
                    # 회의 배치 세션이 여는 바닥 넷 중 둘 (SCAX-SPEC-004 §7.2-2) — 전부 조회다.
                    "member_list",
                    "project_list",
                    "task_block",
                    "task_cancel",
                    "task_complete",
                    "task_create_self",
                    "task_checklist_add",
                    "task_checklist_archive",
                    "task_checklist_list",
                    "task_checklist_reorder",
                    "task_checklist_update",
                    "task_progress_batch",
                    "task_get",
                    "task_history",
                    "task_list",
                    "my_task_list",
                    "material_search",
                    "task_materials_list",
                    "task_resume",
                    "task_start",
                    "task_subtask_list",
                    "task_update",
                }

    asyncio.run(scenario())


def test_stdio_mcp_server_supports_2026_discovery_and_persona_filtered_tools(tmp_path) -> None:
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
                "AX_MCP_PERSONA": "jiho",
            },
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                discovery = await session.discover()
                assert "2026-07-28" in discovery.supported_versions
                assert discovery.capabilities.tools is not None
                names = {tool.name for tool in (await session.list_tools()).tools}
                assert "action_item_command" in names
                assert "work_request_accept" not in names
                assert "daily_report_submit" not in names

                invalid_result = await session.call_tool("task_start", {})
                assert invalid_result.is_error is True
                assert invalid_result.content
                assert "expected_version" in invalid_result.content[0].text

    asyncio.run(scenario())


def test_stdio_mcp_tool_call_rechecks_a_revoked_capability(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url)
    facade = McpReportsFacade(settings, "mina", ContractTestAiProvider())

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
                discovered = {tool.name for tool in (await session.list_tools()).tools}
                assert "task_create_self" in discovered

                with make_session_factory(database_url)() as database_session:
                    database_session.execute(
                        delete(RoleCapabilityRecord).where(
                            RoleCapabilityRecord.role_id == "role:member",
                            RoleCapabilityRecord.capability_id == "task.self_manage",
                        )
                    )
                    database_session.commit()

                assert "task_create_self" not in {tool.name for tool in (await session.list_tools()).tools}
                result = await session.call_tool("task_create_self", {"title": "권한 회수 뒤 생성"})
                assert result.is_error is True
                assert result.content[0].text == "Unknown tool"
                with make_session_factory(database_url)() as database_session:
                    database_session.add(RoleCapabilityRecord(role_id="role:member", capability_id="task.self_manage"))
                    database_session.commit()
                assert "task_create_self" in {tool.name for tool in (await session.list_tools()).tools}
                # Restore the revoked state for the direct adapter denial assertion below.
                with make_session_factory(database_url)() as database_session:
                    database_session.execute(delete(RoleCapabilityRecord).where(
                        RoleCapabilityRecord.role_id == "role:member",
                        RoleCapabilityRecord.capability_id == "task.self_manage"))
                    database_session.commit()

    asyncio.run(scenario())
    with pytest.raises(TaskAccessDenied, match="task.self_manage"):
        facade.create_self_task("권한 회수 뒤 생성")
    with make_session_factory(database_url)() as session:
        assert session.query(TaskRecord).count() == 0


def test_delegated_stdio_mcp_tool_rechecks_capability_before_proposing_an_action(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url)
    application = create_workflow_application(settings, ContractTestAiProvider())
    mina = application.authenticated_principal("mina")
    conversation = application.create_conversation(mina, "delegated capability recheck")
    accepted = application.accept_conversation_message(
        mina,
        "업무를 생성해줘",
        UUID(conversation["conversation_id"]),
        [],
        "delegated-capability-recheck",
    )
    with make_session_factory(database_url)() as session:
        turn = session.get(ConversationTurnRecord, UUID(accepted["turn_id"]))
        assert turn is not None
        execution_id = str(turn.execution_id)

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
                "AX_MCP_CAUSATION_ID": execution_id,
            },
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                discovered = {tool.name for tool in (await session.list_tools()).tools}
                assert "task_create_self" in discovered

                with make_session_factory(database_url)() as database_session:
                    database_session.execute(
                        delete(RoleCapabilityRecord).where(
                            RoleCapabilityRecord.role_id == "role:member",
                            RoleCapabilityRecord.capability_id == "task.self_manage",
                        )
                    )
                    database_session.commit()

                result = await session.call_tool("task_create_self", {"title": "승인 제안도 금지"})
                assert result.is_error is True
                assert result.content[0].text == "Unknown tool"

    asyncio.run(scenario())
    with make_session_factory(database_url)() as session:
        assert session.query(TaskRecord).count() == 0
        assert session.query(ActionItemRecord).count() == 0


def test_mcp_hidden_tools_and_direct_facade_calls_share_capability_denial(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    facade = McpReportsFacade(
        Settings(RuntimeProfile.TEST, database_url),
        "sora",
        ContractTestAiProvider(),
    )

    with pytest.raises(TaskAccessDenied, match="task.read"):
        facade.list_tasks()
    with pytest.raises(WorkRequestAccessDenied, match="work_request.read"):
        facade.list_work_requests()
    with pytest.raises(DailyReportAccessDenied, match="daily_report.generate"):
        facade.generate_daily_report_draft("2026-09-03")


def test_mcp_create_mutations_are_idempotent_within_a_server_bound_turn(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url)
    application = create_workflow_application(settings, ContractTestAiProvider())
    principal = application.authenticated_principal("mina")
    conversation = application.create_conversation(principal, "idempotent action")
    accepted = application.accept_conversation_message(
        principal,
        "업무를 만들어줘",
        UUID(conversation["conversation_id"]),
        [],
        "idempotent-action-message",
    )
    monkeypatch.setenv("AX_MCP_CAUSATION_ID", str(accepted["turn_id"]))
    with make_session_factory(database_url)() as session:
        turn = session.get(ConversationTurnRecord, UUID(accepted["turn_id"]))
        assert turn is not None
        monkeypatch.setenv("AX_MCP_CAUSATION_ID", str(turn.execution_id))
    facade = McpReportsFacade(
        settings,
        "mina",
        ContractTestAiProvider(),
    )

    fields = {
        "description": "직접 생성과 같은 설명",
        "start_date": "2026-09-10",
        "due_date": "2026-09-30",
        "checklist": ["자료 확인"],
    }
    first_action = facade.create_self_task("재시도해도 하나인 업무", **fields)
    repeated_action = facade.create_self_task("재시도해도 하나인 업무", **fields)

    assert first_action["state"] == "pending"
    assert repeated_action["action_id"] == first_action["action_id"]
    assert first_action["edit_contract"]["values"] == {
        "title": "재시도해도 하나인 업무",
        "description": "직접 생성과 같은 설명",
        "start_date": "2026-09-10",
        "due_date": "2026-09-30",
        "checklist": ["자료 확인"],
        "reference_task_ids": [],
        "parent_task_id": None,
        "project_id": None,
    }
    with make_session_factory(database_url)() as session:
        assert session.query(TaskActivityRecord).count() == 0


def test_delegated_chat_work_request_is_an_action_until_the_owner_approves(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url)
    application = create_workflow_application(settings, ContractTestAiProvider())
    principal = application.authenticated_principal("mina")
    conversation = application.create_conversation(principal, "AX 확인")
    application.accept_conversation_message(
        principal,
        "지호에게 업무 요청을 만들어줘",
        UUID(conversation["conversation_id"]),
        [],
        "message-1",
    )
    with make_session_factory(database_url)() as session:
        turn = session.scalar(select(ConversationTurnRecord))
        assert turn is not None
        execution_id = str(turn.execution_id)

    monkeypatch.setenv("AX_MCP_CAUSATION_ID", execution_id)
    action = McpReportsFacade(settings, "mina", ContractTestAiProvider()).create_work_request(
        "승인이 필요한 업무 요청",
        "jiho",
    )
    repeated_action = McpReportsFacade(settings, "mina", ContractTestAiProvider()).create_work_request(
        "승인이 필요한 업무 요청",
        "jiho",
    )
    assert action["state"] == "pending"
    assert action["action_type"] == "work_request.create"
    assert repeated_action["action_id"] == action["action_id"]
    assert application.list_work_requests(principal) == []

    approved = application.decide_action(
        principal,
        UUID(action["action_id"]),
        action["version"],
        "approve",
    )
    assert approved["state"] == "approved"
    assert approved["result"]["request_id"]
    retried_approval = application.decide_action(
        principal,
        UUID(action["action_id"]),
        action["version"],
        "approve",
    )
    assert retried_approval == approved
    assert len(application.list_work_requests(principal)) == 1
    timeline = application.conversation(principal, UUID(conversation["conversation_id"]))
    assert timeline["actions"][0]["action_id"] == action["action_id"]


def test_delegated_action_rejects_unknown_or_cross_owner_execution_ids(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    application = create_workflow_application(
        Settings(RuntimeProfile.TEST, database_url),
        ContractTestAiProvider(),
    )
    mina = application.authenticated_principal("mina")
    jiho = application.authenticated_principal("jiho")
    conversation = application.create_conversation(mina, "민아의 AX 대화")
    application.accept_conversation_message(
        mina,
        "확인할 변경",
        UUID(conversation["conversation_id"]),
        [],
        "mina-message",
    )
    with make_session_factory(database_url)() as session:
        execution_id = session.scalar(select(ConversationTurnRecord.execution_id))
        assert execution_id is not None

    with pytest.raises(ValueError, match="execution was not found"):
        application.propose_action(
            jiho,
            execution_id,
            "task.create_self",
            "권한 없는 제안",
            {"title": "권한 없는 업무"},
        )
    with pytest.raises(ValueError, match="execution was not found"):
        application.propose_action(
            mina,
            uuid4(),
            "task.create_self",
            "없는 실행 제안",
            {"title": "없는 실행 업무"},
        )
    with make_session_factory(database_url)() as session:
        assert session.query(ActionItemRecord).count() == 0


def test_delegated_discovery_requires_permission_to_prepare_confirmation(monkeypatch):
    monkeypatch.setenv('AX_MCP_CAUSATION_ID', str(uuid4()))
    facade = CapabilityFacade(Principal('mina', '내 업무 관리', frozenset({'scax'}), frozenset({TASK_SELF_MANAGE})))
    server = _create_bound_persona_server(facade)
    assert 'task_create_self' not in {tool.name for tool in asyncio.run(server.list_tools())}
    facade._principal = Principal('mina', '승인 가능', frozenset({'scax'}), frozenset({TASK_SELF_MANAGE, 'action.decide'}))
    assert 'task_create_self' in {tool.name for tool in asyncio.run(server.list_tools())}
