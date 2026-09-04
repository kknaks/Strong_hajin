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

    instructions = server.instructions or ""
    assert "action_item_list" in instructions and "action_item_command" in instructions
    # It says the two things a delegated turn must not get wrong: policy comes from the item, and it does not decide.
    assert "allowed_commands" in instructions
    assert "pending confirmation" in instructions


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


def _delegated_turn(client, application, headers, persona: str, monkeypatch) -> str:
    """Bind the facade to a real delegated turn, the way the conversation worker does."""
    from uuid import UUID

    from ax_workspace.platform.persistence import ConversationTurnRecord

    conversation = client.post("/api/conversations", headers=headers, json={"title": "위임 턴"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**headers, "Idempotency-Key": f"delegated-{persona}"},
        json={"body": "판단해줘", "context": []},
    )
    assert accepted.status_code == 202, accepted.text
    with make_session_factory(application._settings.database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"])).execution_id
    monkeypatch.setenv("AX_MCP_CAUSATION_ID", str(execution_id))
    return str(execution_id)


def _application(tmp_path_client):
    return tmp_path_client.app.state.workflow_application


def test_a_delegated_turn_proposes_a_judgement_for_a_person_instead_of_making_it(tmp_path, monkeypatch) -> None:
    """Inside a chat turn the model may prepare a judgement; only a person may actually make it."""
    database_url, settings, client = _stack(tmp_path)
    application = client.app.state.workflow_application
    client.post("/api/work-requests", headers=MINA, json={"title": "턴이 제안하는 판단", "assignee_id": "jiho"})
    jiho = _facade(settings, "jiho")
    [item] = jiho.pending_action_items()

    _delegated_turn(client, application, JIHO, "jiho", monkeypatch)
    gated = jiho.run_action_command(
        item["action_item_id"], "accept", expected_version=item["expected_version"]
    )
    # A wrapper Action to confirm, not the effect.
    assert gated["state"] == "pending" and gated["action_type"] == "action_item.command"
    assert client.get("/api/work-requests", headers=MINA).json()[0]["state"] == "pending"
    assert client.get("/api/my-work", headers=JIHO).json() == []
    assert jiho.pending_action_items()[0]["action_item_id"] == item["action_item_id"]

    # The card a person sees is the target's own permission-safe presentation, never the raw payload.
    assert gated["subject"] == "턴이 제안하는 판단"
    assert gated["operation_label"] == "업무 요청 판단"
    preview = {row["id"]: row["value"] for row in gated["preview"]}
    assert preview["command"] == "수락"
    # The card shows the work, not the wire: no raw ids or payload keys leak into it.
    assert item["action_item_id"] not in " ".join(f"{row['label']}{row['value']}" for row in gated["preview"])

    # Re-sending the identical call is the same wrapper, not a second one.
    again = jiho.run_action_command(item["action_item_id"], "accept", expected_version=item["expected_version"])
    assert again["action_id"] == gated["action_id"]

    # Only the person's approval applies the effect, exactly once.
    approved = client.post(
        f"/api/actions/{gated['action_id']}/decide",
        headers=JIHO,
        json={"expected_version": gated["version"], "decision": "approve"},
    )
    assert approved.status_code == 200, approved.text
    assert client.get("/api/work-requests", headers=MINA).json()[0]["state"] == "accepted"
    assert [task["title"] for task in client.get("/api/my-work", headers=JIHO).json()] == ["턴이 제안하는 판단"]
    replayed = client.post(
        f"/api/actions/{gated['action_id']}/decide",
        headers=JIHO,
        json={"expected_version": gated["version"], "decision": "approve"},
    )
    assert replayed.status_code == 200 and len(client.get("/api/my-work", headers=JIHO).json()) == 1


def test_a_delegated_turn_can_never_approve_an_ax_proposal_including_its_own(tmp_path, monkeypatch) -> None:
    """The gate exists to put a person between AX and the effect; AX may not step over it."""
    database_url, settings, client = _stack(tmp_path)
    application = client.app.state.workflow_application
    _delegated_turn(client, application, MINA, "mina", monkeypatch)
    mina = _facade(settings, "mina")

    gate = mina.create_work_request("AX가 스스로 승인하려는 요청", "jiho")
    assert gate["state"] == "pending"
    [proposal] = [item for item in mina.pending_action_items() if item["kind"].startswith("ax.")]

    with pytest.raises(Exception, match="사람"):
        mina.run_action_command(proposal["action_item_id"], "approve", expected_version=proposal["expected_version"])
    # Nothing happened: no WorkRequest, and the gate is still waiting for a person.
    assert client.get("/api/work-requests", headers=MINA).json() == []
    assert mina.pending_action_items()[0]["status"] == "awaiting_review"

    # A wrapper the turn proposes is itself an AX proposal, so the same rule blocks self-approving it.
    client.post("/api/tasks/assign", headers=JIHO, json={"title": "래퍼 확인", "assignee_id": "mina"})
    [incoming] = [row for row in mina.pending_action_items() if row["subject"] == "래퍼 확인"]
    wrapper = mina.run_action_command(incoming["action_item_id"], "accept", expected_version=incoming["expected_version"])
    assert wrapper["state"] == "pending"
    [wrapped] = [row for row in mina.pending_action_items() if row["action_item_id"] == wrapper["action_id"]]
    with pytest.raises(Exception, match="사람"):
        mina.run_action_command(wrapped["action_item_id"], "approve", expected_version=wrapped["expected_version"])
    assert client.get("/api/my-work", headers=MINA).json() == []


def test_a_direct_admin_or_test_caller_outside_a_chat_turn_still_commands_canonically(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AX_MCP_CAUSATION_ID", raising=False)
    database_url, settings, client = _stack(tmp_path)
    client.post("/api/work-requests", headers=MINA, json={"title": "직접 호출", "assignee_id": "jiho"})
    jiho = _facade(settings, "jiho")
    [item] = jiho.pending_action_items()
    assert jiho.run_action_command(item["action_item_id"], "accept", expected_version=item["expected_version"])["status"] == "resolved"
    assert [task["title"] for task in client.get("/api/my-work", headers=JIHO).json()] == ["직접 호출"]


def test_the_command_tool_requires_the_version_it_is_answering(tmp_path, monkeypatch) -> None:
    _, settings, client = _stack(tmp_path)
    client.post("/api/work-requests", headers=MINA, json={"title": "버전 필수", "assignee_id": "jiho"})
    tools = {tool.name: tool for tool in _tools(settings, "jiho", monkeypatch)}
    schema = tools["action_item_command"].input_schema
    assert set(schema["required"]) == {"action_item_id", "command", "expected_version"}
    assert schema["properties"]["expected_version"]["type"] == "integer"


def test_only_the_one_judgement_surface_is_registered(tmp_path, monkeypatch) -> None:
    """A second way to decide is a second policy; the per-kind decision tools are gone, not merely labelled."""
    _, settings, _ = _stack(tmp_path)
    for persona in ("jiho", "mina"):
        names = {tool.name for tool in _tools(settings, persona, monkeypatch)}
        assert ACTION_ITEM_TOOLS <= names
        assert not (names & {
            "work_request_accept",
            "work_request_negotiate",
            "work_request_reject",
            "work_request_resubmit",
            "task_assignment_inbox",
            "task_assignment_accept",
            "task_assignment_decline",
        })


def test_stdio_delegated_command_returns_a_pending_confirmation_and_changes_nothing(tmp_path) -> None:
    database_url, settings, client = _stack(tmp_path)
    application = client.app.state.workflow_application
    client.post("/api/work-requests", headers=MINA, json={"title": "stdio 위임 판단", "assignee_id": "jiho"})
    [item] = _facade(settings, "jiho").pending_action_items()

    from uuid import UUID

    from ax_workspace.platform.persistence import ConversationTurnRecord

    conversation = client.post("/api/conversations", headers=JIHO, json={"title": "stdio 턴"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**JIHO, "Idempotency-Key": "stdio-delegated"},
        json={"body": "판단해줘", "context": []},
    )
    with make_session_factory(database_url)() as session:
        execution_id = str(session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"])).execution_id)

    async def scenario() -> str:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "ax_workspace.entrypoints.mcp"],
            cwd=os.getcwd(),
            env={
                **os.environ,
                "AX_PROFILE": "test",
                "DATABASE_URL": database_url,
                "AX_MCP_PERSONA": "jiho",
                "AX_MCP_CAUSATION_ID": execution_id,
            },
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(
                    "action_item_command",
                    {
                        "action_item_id": item["action_item_id"],
                        "command": "accept",
                        "expected_version": item["expected_version"],
                    },
                )
                assert result.is_error is not True, result.content
                assert result.structured_content["state"] == "pending"
                assert result.structured_content["action_type"] == "action_item.command"

                # An unauthorized item and an unknown id are the same answer: no existence oracle on the wire.
                unknown = await session.call_tool("action_item_get", {"action_item_id": "11111111-1111-4111-8111-111111111111"})
                mina_only = await session.call_tool("action_item_get", {"action_item_id": item["action_item_id"]})
                assert unknown.is_error is True
                return result.structured_content["action_id"]

    action_id = asyncio.run(scenario())
    # The judgement did not happen: no Task, and the question is still Jiho's to answer.
    assert client.get("/api/my-work", headers=JIHO).json() == []
    assert client.get("/api/work-requests", headers=MINA).json()[0]["state"] == "pending"
    action = [row for row in client.get("/api/actions", headers=JIHO).json() if row["action_id"] == action_id][0]
    assert action["state"] == "pending" and action["subject"] == "stdio 위임 판단"

    approved = client.post(
        f"/api/actions/{action_id}/decide", headers=JIHO, json={"expected_version": action["version"], "decision": "approve"}
    )
    assert approved.status_code == 200, approved.text
    assert [task["title"] for task in client.get("/api/my-work", headers=JIHO).json()] == ["stdio 위임 판단"]
