"""One judgement ledger, reached over MCP.

A delegated persona answers questions through the same `ActionCenterApplication` that HTTP and the product UI use: the
same envelope, the same `allowed_commands`, the same payload-aware receipt. The MCP adapter is a transport — it adds no
domain transition of its own, derives no control from the kind, and takes no persona, member, org or capability from
the caller, because the persona is process configuration.
"""
import asyncio
import os
import sys

from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
import pytest
from sqlalchemy import delete

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.mcp import McpReportsFacade, create_mcp_server
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import RoleCapabilityRecord, TaskRecord, make_session_factory

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}

ACTION_ITEM_TOOLS = {"action_item_list", "action_item_get", "action_item_command"}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    return database_url, settings, TestClient(create_app(settings))


def _facade(settings, persona: str) -> McpReportsFacade:
    return McpReportsFacade(settings, persona)


def _tools(settings, persona: str, monkeypatch):
    monkeypatch.setenv("AX_MCP_PERSONA", persona)
    return asyncio.run(create_mcp_server(settings).list_tools())


def test_action_item_tools_read_exactly_what_the_product_reads(tmp_path) -> None:
    database_url, settings, client = _stack(tmp_path)
    created = client.post(
        "/api/work-requests",
        headers=MINA,
        json={"title": "MCP가 읽는 요청", "assignee_id": "jiho", "description": "설명"},
    ).json()
    jiho = _facade(settings, "jiho")

    # The projection is the product's own, field for field — not an MCP-shaped copy of it.
    assert jiho.pending_action_items() == client.get("/api/action-items", headers=JIHO).json()
    [item] = jiho.pending_action_items()
    assert item["kind"] == "work_request.acceptance" and item["subject"] == "MCP가 읽는 요청"
    assert [command["id"] for command in item["allowed_commands"]] == ["accept", "adjust", "reject"]
    assert jiho.action_item_detail(item["action_item_id"]) == client.get(
        f"/api/action-items/{item['action_item_id']}", headers=JIHO
    ).json()
    assert jiho.action_item_detail(item["action_item_id"])["resource"]["id"] == created["request_id"]


def test_a_whole_adjustment_round_trip_runs_over_mcp_and_makes_one_task(tmp_path) -> None:
    """The reviewer's structured ask, the requester's revision and the acceptance, all through the one command path."""
    database_url, settings, client = _stack(tmp_path)
    mina, jiho = _facade(settings, "mina"), _facade(settings, "jiho")
    client.post("/api/work-requests", headers=MINA, json={"title": "MCP 왕복 요청", "assignee_id": "jiho"})

    [first] = jiho.pending_action_items()
    adjusted = jiho.run_action_command(
        first["action_item_id"],
        "adjust",
        expected_version=first["expected_version"],
        reason="기한과 제목을 손봐 주세요",
        changes={"title": "MCP가 제안한 제목", "due_date": "2026-10-01"},
    )
    assert adjusted["status"] == "awaiting_revision"

    [waiting] = mina.pending_action_items()
    assert waiting["action_item_id"] == first["action_item_id"]
    assert waiting["suggested_changes"] == {"title": "MCP가 제안한 제목", "due_date": "2026-10-01"}
    assert [command["id"] for command in waiting["allowed_commands"]] == ["revise", "withdraw"]
    revised = mina.run_action_command(
        waiting["action_item_id"],
        "revise",
        expected_version=waiting["expected_version"],
        changes={"title": "MCP가 제안한 제목", "due_date": "2026-10-01"},
    )
    assert revised["status"] == "awaiting_review" and revised["submission_version"] == 2

    [second] = jiho.pending_action_items()
    accepted = jiho.run_action_command(second["action_item_id"], "accept", expected_version=second["expected_version"])
    assert accepted["status"] == "resolved"

    tasks = [task for task in jiho.list_tasks() if task["title"] == "MCP가 제안한 제목"]
    assert len(tasks) == 1
    assert tasks[0]["origin"]["kind"] == "work_request"
    with make_session_factory(database_url)() as session:
        assert session.query(TaskRecord).filter(TaskRecord.title == "MCP가 제안한 제목").count() == 1
    # Both ends of the ledger agree, read back through the same MCP surface.
    detail = jiho.action_item_detail(second["action_item_id"])
    assert [row["submission_version"] for row in detail["rounds"]] == [1, 2]
    assert detail["rounds"][0]["decisions"][0]["suggested_changes"] == {"title": "MCP가 제안한 제목", "due_date": "2026-10-01"}


def test_a_resent_mcp_command_is_the_same_receipt_and_a_stale_one_is_refused(tmp_path) -> None:
    database_url, settings, client = _stack(tmp_path)
    mina, jiho = _facade(settings, "mina"), _facade(settings, "jiho")
    client.post("/api/work-requests", headers=MINA, json={"title": "재전송 요청", "assignee_id": "jiho"})

    [first] = jiho.pending_action_items()
    adjust = {"reason": "다시 봐 주세요", "changes": {"title": "제안 제목"}}
    jiho.run_action_command(first["action_item_id"], "adjust", expected_version=first["expected_version"], **adjust)
    [waiting] = mina.pending_action_items()
    revise = {"changes": {"title": "1회차 수정"}}
    receipt = mina.run_action_command(waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], **revise)

    # The same call again is the same answer, not a second round.
    assert mina.run_action_command(waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], **revise) == receipt
    # An older version, or the right version with content this principal never sent, is a stale request.
    with pytest.raises(Exception, match="not available"):
        mina.run_action_command(waiting["action_item_id"], "revise", expected_version=first["expected_version"], **revise)
    with pytest.raises(Exception, match="not available"):
        mina.run_action_command(
            waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], changes={"title": "보내지 않은 수정"}
        )
    assert [row["submission_version"] for row in mina.action_item_detail(waiting["action_item_id"])["rounds"]] == [1, 2]


def test_an_action_item_is_invisible_to_a_persona_it_does_not_belong_to(tmp_path) -> None:
    """Not merely uncommandable: the existence, subject and kind of another team's question do not leak."""
    database_url, settings, client = _stack(tmp_path)
    client.post("/api/work-requests", headers=MINA, json={"title": "비밀 제목", "assignee_id": "jiho"})
    [item] = _facade(settings, "jiho").pending_action_items()

    outsider = _facade(settings, "sora")
    assert outsider.pending_action_items() == []
    with pytest.raises(Exception) as denied:
        outsider.action_item_detail(item["action_item_id"])
    message = str(denied.value)
    assert "비밀 제목" not in message and "work_request" not in message


def test_action_item_tool_discovery_follows_the_persona_capabilities(tmp_path, monkeypatch) -> None:
    _, settings, _ = _stack(tmp_path)

    jiho = {tool.name for tool in _tools(settings, "jiho", monkeypatch)}
    mina = {tool.name for tool in _tools(settings, "mina", monkeypatch)}
    sora = {tool.name for tool in _tools(settings, "sora", monkeypatch)}

    # Both judging personas reach the one ledger; a persona with no readable kind sees none of it.
    assert ACTION_ITEM_TOOLS <= jiho and ACTION_ITEM_TOOLS <= mina
    assert not (ACTION_ITEM_TOOLS & sora)
    # Nothing about the caller is an argument: the persona is process configuration.
    for tool in _tools(settings, "jiho", monkeypatch):
        if tool.name in ACTION_ITEM_TOOLS:
            properties = set(tool.input_schema.get("properties", {}))
            assert not (properties & {"persona", "persona_id", "member_id", "org_id", "capability", "capabilities"})


def test_action_item_tools_carry_the_annotations_a_client_needs_to_reason_about_them(tmp_path, monkeypatch) -> None:
    _, settings, _ = _stack(tmp_path)
    tools = {tool.name: tool for tool in _tools(settings, "jiho", monkeypatch)}

    for name in ("action_item_list", "action_item_get"):
        annotations = tools[name].annotations
        assert annotations is not None, name
        assert annotations.read_only_hint is True and annotations.destructive_hint is False
        assert annotations.idempotent_hint is True and annotations.open_world_hint is False
        assert tools[name].output_schema is not None

    command = tools["action_item_command"].annotations
    assert command is not None
    assert command.read_only_hint is False and command.open_world_hint is False
    # The canonical receipt makes a re-send safe; a command may still withdraw, reject or decline.
    assert command.idempotent_hint is True and command.destructive_hint is True
    assert tools["action_item_command"].output_schema is not None
    schema = tools["action_item_command"].input_schema["properties"]
    assert {"action_item_id", "command", "expected_version", "reason", "changes"} <= set(schema)


def test_the_tool_list_is_deterministic(tmp_path, monkeypatch) -> None:
    _, settings, _ = _stack(tmp_path)
    first = _tools(settings, "jiho", monkeypatch)
    second = _tools(settings, "jiho", monkeypatch)
    assert [tool.name for tool in first] == [tool.name for tool in second]
    assert [tool.input_schema for tool in first] == [tool.input_schema for tool in second]


def test_the_server_tells_a_delegated_turn_to_judge_through_the_one_ledger(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AX_MCP_PERSONA", "jiho")
    _, settings, _ = _stack(tmp_path)
    server = create_mcp_server(settings)
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}

    assert "action_item" in (server.instructions or "")
    # The per-kind decision tools stay for compatibility, but they say where judgement actually belongs.
    for name in ("work_request_accept", "work_request_negotiate", "work_request_reject"):
        assert "Deprecated" in tools[name].description and "action_item_command" in tools[name].description
    for name in ("task_assignment_accept", "task_assignment_decline", "task_assignment_inbox"):
        assert "Deprecated" in tools[name].description and "action_item" in tools[name].description

    # The requester side lives on the persona that may create requests.
    monkeypatch.setenv("AX_MCP_PERSONA", "mina")
    requester = {tool.name: tool for tool in asyncio.run(create_mcp_server(settings).list_tools())}
    assert "Deprecated" in requester["work_request_resubmit"].description
    assert "action_item_command" in requester["work_request_resubmit"].description


def test_stdio_client_runs_the_ledger_and_keeps_it_bound_to_the_server_persona(tmp_path) -> None:
    database_url, settings, client = _stack(tmp_path)
    client.post("/api/work-requests", headers=MINA, json={"title": "stdio 판단 요청", "assignee_id": "jiho"})
    expected = client.get("/api/action-items", headers=JIHO).json()

    async def scenario() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "ax_workspace.entrypoints.mcp"],
            cwd=os.getcwd(),
            env={**os.environ, "AX_PROFILE": "test", "DATABASE_URL": database_url, "AX_MCP_PERSONA": "jiho"},
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                discovery = await session.discover()
                assert "2026-07-28" in discovery.supported_versions
                names = [tool.name for tool in (await session.list_tools()).tools]
                assert ACTION_ITEM_TOOLS <= set(names)

                listed = await session.call_tool("action_item_list", {})
                assert listed.is_error is not True
                # Structured output: a real client reads the projection, not a stringified copy of it.
                assert listed.structured_content == {"result": expected}

                item = expected[0]
                got = await session.call_tool("action_item_get", {"action_item_id": item["action_item_id"]})
                assert got.structured_content["rounds"][0]["submission_version"] == 1

                answered = await session.call_tool(
                    "action_item_command",
                    {
                        "action_item_id": item["action_item_id"],
                        "command": "adjust",
                        "expected_version": item["expected_version"],
                        "reason": "stdio에서 조정",
                        "changes": {"due_date": "2026-11-11"},
                    },
                )
                assert answered.is_error is not True
                assert answered.structured_content["status"] == "awaiting_revision"

    asyncio.run(scenario())
    assert client.get("/api/action-items", headers=MINA).json()[0]["suggested_changes"] == {"due_date": "2026-11-11"}


def test_a_capability_revoked_after_discovery_is_refused_at_call_time(tmp_path) -> None:
    database_url, settings, client = _stack(tmp_path)
    client.post("/api/work-requests", headers=MINA, json={"title": "권한 회수 요청", "assignee_id": "jiho"})
    [item] = _facade(settings, "jiho").pending_action_items()

    async def scenario() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "ax_workspace.entrypoints.mcp"],
            cwd=os.getcwd(),
            env={**os.environ, "AX_PROFILE": "test", "DATABASE_URL": database_url, "AX_MCP_PERSONA": "jiho"},
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                assert "action_item_command" in {tool.name for tool in (await session.list_tools()).tools}

                with make_session_factory(database_url)() as database_session:
                    database_session.execute(
                        delete(RoleCapabilityRecord).where(
                            RoleCapabilityRecord.role_id == "seed-role:jiho",
                            RoleCapabilityRecord.capability_id == "work_request.decide",
                        )
                    )
                    database_session.commit()

                # Discovery is a snapshot; the application re-checks authority when the command actually runs.
                result = await session.call_tool(
                    "action_item_command",
                    {"action_item_id": item["action_item_id"], "command": "accept", "expected_version": item["expected_version"]},
                )
                assert result.is_error is True

    asyncio.run(scenario())
    assert client.get("/api/work-requests", headers=MINA).json()[0]["state"] == "pending"
    with make_session_factory(database_url)() as session:
        assert session.query(TaskRecord).count() == 0
