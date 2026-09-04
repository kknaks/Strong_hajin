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


def _mina_proposal(client, application) -> dict:
    """A gated AX proposal owned by 민아, which 지호 has no relationship to at all."""
    from uuid import UUID

    from ax_workspace.platform.persistence import ConversationTurnRecord

    conversation = client.post("/api/conversations", headers=MINA, json={"title": "민아의 대화"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": "mina-proposal"},
        json={"body": "제안해줘", "context": []},
    )
    assert accepted.status_code == 202, accepted.text
    with make_session_factory(application._settings.database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"])).execution_id
    return application.propose_action(
        application.authenticated_principal("mina"), execution_id, "task.create_self", "업무 생성 확인", {"title": "민아의 비밀 업무"}
    )


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

    # The same call again is the same answer, not a second round. A revision is identified by the round it produced,
    # so an older version quoted alongside the same content is still that answer coming back.
    assert mina.run_action_command(waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], **revise) == receipt
    # A version this revision never consumed is a different request, even with the same content.
    with pytest.raises(Exception, match="not available"):
        mina.run_action_command(waiting["action_item_id"], "revise", expected_version=first["expected_version"], **revise)
    # Content this principal never sent is a different judgement, whatever version it quotes.
    with pytest.raises(Exception, match="not available"):
        mina.run_action_command(
            waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], changes={"title": "보내지 않은 수정"}
        )
    assert [row["submission_version"] for row in mina.action_item_detail(waiting["action_item_id"])["rounds"]] == [1, 2]

    # A decision is different: it is pinned to the version it actually consumed, so an older one never replays.
    [second] = jiho.pending_action_items()
    jiho.run_action_command(second["action_item_id"], "adjust", expected_version=second["expected_version"], **adjust)
    assert jiho.run_action_command(second["action_item_id"], "adjust", expected_version=second["expected_version"], **adjust)["status"] == "awaiting_revision"
    with pytest.raises(Exception, match="not available"):
        jiho.run_action_command(second["action_item_id"], "adjust", expected_version=first["expected_version"], **adjust)


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


def test_one_turn_holds_one_judgement_and_the_receipt_is_the_whole_payload(tmp_path, monkeypatch) -> None:
    """A receipt must prove the same judgement, not merely the same target and verb."""
    database_url, settings, client = _stack(tmp_path)
    application = client.app.state.workflow_application
    client.post("/api/work-requests", headers=MINA, json={"title": "한 턴 한 판단", "assignee_id": "jiho"})
    jiho = _facade(settings, "jiho")
    [item] = jiho.pending_action_items()
    _delegated_turn(client, application, JIHO, "jiho", monkeypatch)

    first = jiho.run_action_command(
        item["action_item_id"], "adjust", expected_version=item["expected_version"],
        reason="기한을 늦춰 주세요", changes={"due_date": "2026-12-01"},
    )
    assert first["state"] == "pending"

    # The identical call — including a reason that only differs by surrounding space — is the same receipt.
    same = jiho.run_action_command(
        item["action_item_id"], "adjust", expected_version=item["expected_version"],
        reason="  기한을 늦춰 주세요  ", changes={"due_date": "2026-12-01"},
    )
    assert same["action_id"] == first["action_id"] and same["payload_hash"] == first["payload_hash"]

    # Anything else about the judgement is a different judgement, and is refused rather than answered with this one.
    for label, kwargs in (
        ("다른 사유", {"reason": "역시 안 되겠습니다", "changes": {"due_date": "2026-12-01"}}),
        ("다른 제안", {"reason": "기한을 늦춰 주세요", "changes": {"due_date": "2026-12-24"}}),
        ("제안 없음", {"reason": "기한을 늦춰 주세요"}),
        ("다른 버전", {"reason": "기한을 늦춰 주세요", "changes": {"due_date": "2026-12-01"}, "expected_version": item["expected_version"] + 1}),
    ):
        payload = {"expected_version": item["expected_version"], **kwargs}
        with pytest.raises(Exception, match="다른 판단"):
            jiho.run_action_command(item["action_item_id"], "adjust", **payload)
    with pytest.raises(Exception, match="다른 판단"):
        jiho.run_action_command(item["action_item_id"], "reject", expected_version=item["expected_version"], reason="기한을 늦춰 주세요")

    # The stored confirmation never moved.
    unchanged = jiho.run_action_command(
        item["action_item_id"], "adjust", expected_version=item["expected_version"],
        reason="기한을 늦춰 주세요", changes={"due_date": "2026-12-01"},
    )
    assert unchanged["action_id"] == first["action_id"] and unchanged["payload_hash"] == first["payload_hash"]
    assert unchanged["version"] == first["version"] and unchanged["state"] == "pending"


def test_a_delegated_server_only_advertises_a_command_it_could_actually_run(tmp_path, monkeypatch) -> None:
    """Discovery is a promise. In a turn the command needs both a judgement capability and the authority to gate it."""
    database_url, settings, client = _stack(tmp_path)
    monkeypatch.setenv("AX_MCP_CAUSATION_ID", "00000000-0000-4000-8000-000000000000")
    assert "action_item_command" in {tool.name for tool in _tools(settings, "jiho", monkeypatch)}

    with make_session_factory(database_url)() as session:
        session.execute(
            delete(RoleCapabilityRecord).where(
                RoleCapabilityRecord.role_id == "seed-role:jiho", RoleCapabilityRecord.capability_id == "action.decide"
            )
        )
        session.commit()

    # Without the authority to raise a confirmation, a delegated turn could never run the command it was offered.
    delegated = {tool.name for tool in _tools(settings, "jiho", monkeypatch)}
    assert "action_item_command" not in delegated
    assert {"action_item_list", "action_item_get"} <= delegated

    # Outside a turn the same persona still commands directly, so the tool stays.
    monkeypatch.delenv("AX_MCP_CAUSATION_ID", raising=False)
    assert "action_item_command" in {tool.name for tool in _tools(settings, "jiho", monkeypatch)}


def test_a_pending_confirmation_says_nothing_about_work_the_approver_may_no_longer_read(tmp_path, monkeypatch) -> None:
    database_url, settings, client = _stack(tmp_path)
    application = client.app.state.workflow_application
    client.post(
        "/api/work-requests", headers=MINA,
        json={"title": "기밀 예산 검토", "assignee_id": "jiho", "description": "내부 한도"},
    )
    jiho = _facade(settings, "jiho")
    [item] = jiho.pending_action_items()
    _delegated_turn(client, application, JIHO, "jiho", monkeypatch)
    wrapper = jiho.run_action_command(item["action_item_id"], "accept", expected_version=item["expected_version"])

    # While the approver can read the work, the card is the work's own presentation.
    card = [row for row in client.get("/api/actions", headers=JIHO).json() if row["action_id"] == wrapper["action_id"]][0]
    assert card["subject"] == "기밀 예산 검토"
    assert {row["id"] for row in card["preview"]} >= {"command"}
    # Nothing stored on the row itself carries the work's words or the wire ids.
    assert "기밀 예산 검토" not in card["title"] and "기밀 예산 검토" not in card["payload_summary"]
    assert item["action_item_id"] not in card["title"] and item["action_item_id"] not in card["payload_summary"]

    with make_session_factory(database_url)() as session:
        for capability in ("work_request.read", "work_request.decide", "work_request.create"):
            session.execute(
                delete(RoleCapabilityRecord).where(
                    RoleCapabilityRecord.role_id == "seed-role:jiho", RoleCapabilityRecord.capability_id == capability
                )
            )
        session.commit()

    # Once they may not read it, the card says nothing about it — not even the title it used to have.
    withheld = [row for row in client.get("/api/actions", headers=JIHO).json() if row["action_id"] == wrapper["action_id"]][0]
    assert "기밀 예산" not in str(withheld)
    assert withheld["preview"] == []


def test_an_action_item_no_one_may_read_answers_exactly_like_one_that_does_not_exist(tmp_path) -> None:
    database_url, settings, client = _stack(tmp_path)
    application = client.app.state.workflow_application
    # An AX proposal owned by 민아; 지호 is not part of it and must not learn that it exists.
    proposal = _mina_proposal(client, application)

    async def scenario() -> tuple:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "ax_workspace.entrypoints.mcp"],
            cwd=os.getcwd(),
            env={**os.environ, "AX_PROFILE": "test", "DATABASE_URL": database_url, "AX_MCP_PERSONA": "jiho"},
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                unknown = await session.call_tool("action_item_get", {"action_item_id": "11111111-1111-4111-8111-111111111111"})
                withheld = await session.call_tool("action_item_get", {"action_item_id": proposal["action_id"]})
                return unknown, withheld

    unknown, withheld = asyncio.run(scenario())
    assert unknown.is_error is True and withheld.is_error is True
    assert withheld.structured_content is None
    # The same answer, word for word: nothing distinguishes a withheld item from one that was never there.
    assert [block.text for block in withheld.content] == [block.text for block in unknown.content]
    body = " ".join(block.text for block in withheld.content)
    assert proposal["action_id"] not in body and "민아" not in body
    assert "task.create_self" not in body and "ax." not in body


def test_a_command_may_only_carry_the_fields_its_own_kind_owns_over_stdio(tmp_path) -> None:
    database_url, settings, client = _stack(tmp_path)
    client.post("/api/work-requests", headers=MINA, json={"title": "필드 계약", "assignee_id": "jiho"})
    [item] = _facade(settings, "jiho").pending_action_items()

    async def scenario() -> tuple:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "ax_workspace.entrypoints.mcp"],
            cwd=os.getcwd(),
            env={**os.environ, "AX_PROFILE": "test", "DATABASE_URL": database_url, "AX_MCP_PERSONA": "jiho"},
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                bad = await session.call_tool(
                    "action_item_command",
                    {
                        "action_item_id": item["action_item_id"],
                        "command": "adjust",
                        "expected_version": item["expected_version"],
                        "reason": "담당을 바꿔 주세요",
                        "changes": {"assignee_id": "sora"},
                    },
                )
                good = await session.call_tool(
                    "action_item_command",
                    {
                        "action_item_id": item["action_item_id"],
                        "command": "adjust",
                        "expected_version": item["expected_version"],
                        "reason": "기한을 늦춰 주세요",
                        "changes": {"due_date": "2026-12-01"},
                    },
                )
                return bad, good

    bad, good = asyncio.run(scenario())
    assert bad.is_error is True
    assert good.is_error is not True and good.structured_content["status"] == "awaiting_revision"
    detail = client.get(f"/api/action-items/{item['action_item_id']}", headers=MINA).json()
    assert [row["submission_version"] for row in detail["rounds"]] == [1]
    assert detail["rounds"][0]["decisions"][0]["suggested_changes"] == {"due_date": "2026-12-01"}


def test_a_pending_confirmation_survives_a_target_that_moved_or_authority_that_was_taken(tmp_path, monkeypatch) -> None:
    """An approval that can no longer be applied leaves the confirmation waiting, not half-done."""
    database_url, settings, client = _stack(tmp_path)
    application = client.app.state.workflow_application
    client.post("/api/work-requests", headers=MINA, json={"title": "움직인 대상", "assignee_id": "jiho"})
    client.post("/api/work-requests", headers=MINA, json={"title": "회수된 권한", "assignee_id": "jiho"})
    jiho = _facade(settings, "jiho")
    items = {row["subject"]: row for row in jiho.pending_action_items()}
    _delegated_turn(client, application, JIHO, "jiho", monkeypatch)

    moved = items["움직인 대상"]
    wrapper = jiho.run_action_command(moved["action_item_id"], "accept", expected_version=moved["expected_version"])
    monkeypatch.delenv("AX_MCP_CAUSATION_ID", raising=False)
    jiho.run_action_command(moved["action_item_id"], "adjust", expected_version=moved["expected_version"], reason="먼저 조정")

    refused = client.post(
        f"/api/actions/{wrapper['action_id']}/decide", headers=JIHO,
        json={"expected_version": wrapper["version"], "decision": "approve"},
    )
    assert refused.status_code == 422, refused.text
    still = [row for row in client.get("/api/actions", headers=JIHO).json() if row["action_id"] == wrapper["action_id"]][0]
    assert still["state"] == "pending" and still["version"] == wrapper["version"]
    assert client.get("/api/my-work", headers=JIHO).json() == []

    # The same holds when the authority to make the judgement is taken away between proposal and approval.
    revoked = items["회수된 권한"]
    _delegated_turn(client, application, JIHO, "jiho", monkeypatch)
    second = jiho.run_action_command(revoked["action_item_id"], "accept", expected_version=revoked["expected_version"])
    with make_session_factory(database_url)() as session:
        session.execute(
            delete(RoleCapabilityRecord).where(
                RoleCapabilityRecord.role_id == "seed-role:jiho", RoleCapabilityRecord.capability_id == "work_request.decide"
            )
        )
        session.commit()
    denied = client.post(
        f"/api/actions/{second['action_id']}/decide", headers=JIHO,
        json={"expected_version": second["version"], "decision": "approve"},
    )
    assert denied.status_code in {403, 422}, denied.text
    pending = [row for row in client.get("/api/actions", headers=JIHO).json() if row["action_id"] == second["action_id"]][0]
    assert pending["state"] == "pending"
    assert [row["state"] for row in client.get("/api/work-requests", headers=MINA).json()] == ["negotiating", "pending"]
    assert client.get("/api/my-work", headers=JIHO).json() == []


def test_an_emptied_field_is_part_of_the_judgement_a_turn_prepares(tmp_path, monkeypatch) -> None:
    """Clearing a field is a decision. It must survive normalization and must change what the receipt is for."""
    database_url, settings, client = _stack(tmp_path)
    application = client.app.state.workflow_application
    client.post(
        "/api/work-requests",
        headers=MINA,
        json={"title": "설명 있는 요청", "assignee_id": "jiho", "description": "지워질 설명"},
    )
    jiho, mina = _facade(settings, "jiho"), _facade(settings, "mina")
    [item] = jiho.pending_action_items()
    jiho.run_action_command(item["action_item_id"], "adjust", expected_version=item["expected_version"], reason="설명을 빼 주세요")
    [waiting] = mina.pending_action_items()

    # The canonical form keeps the field the caller named, empty value and all.
    canonical = application.normalize_action_command(
        application.authenticated_principal("mina"), waiting["action_item_id"], "revise",
        {"expected_version": waiting["expected_version"], "changes": {"description": ""}},
    )
    assert canonical["changes"] == {"description": ""}

    _delegated_turn(client, application, MINA, "mina", monkeypatch)
    wrapper = mina.run_action_command(
        waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], changes={"description": ""}
    )
    assert wrapper["state"] == "pending"
    # Clearing the description and leaving it alone are different judgements, so they are different receipts.
    with pytest.raises(Exception, match="다른 판단"):
        mina.run_action_command(
            waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], changes={"title": "다른 수정"}
        )
    same = mina.run_action_command(
        waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], changes={"description": "  "}
    )
    assert same["action_id"] == wrapper["action_id"] and same["payload_hash"] == wrapper["payload_hash"]

    approved = client.post(
        f"/api/actions/{wrapper['action_id']}/decide", headers=MINA,
        json={"expected_version": wrapper["version"], "decision": "approve"},
    )
    assert approved.status_code == 200, approved.text
    rounds = client.get(f"/api/action-items/{waiting['action_item_id']}", headers=MINA).json()["rounds"]
    assert rounds[1]["snapshot"]["description"] is None and rounds[1]["snapshot"]["title"] == "설명 있는 요청"


def test_a_confirmation_whose_target_moved_says_so_instead_of_failing_when_approved(tmp_path, monkeypatch) -> None:
    """A pending confirmation is an answer to a moment. When that moment passes, the card says so before it is used."""
    database_url, settings, client = _stack(tmp_path)
    application = client.app.state.workflow_application
    request = client.post("/api/work-requests", headers=MINA, json={"title": "지나간 확인", "assignee_id": "jiho"}).json()
    jiho = _facade(settings, "jiho")
    [item] = jiho.pending_action_items()
    _delegated_turn(client, application, JIHO, "jiho", monkeypatch)
    wrapper = jiho.run_action_command(item["action_item_id"], "accept", expected_version=item["expected_version"])

    fresh = [row for row in client.get("/api/actions", headers=JIHO).json() if row["action_id"] == wrapper["action_id"]][0]
    assert fresh["obsolete"] is False
    assert [command["id"] for command in fresh["commands"]] == ["approve", "reject"]

    # The basis moves: the request is no longer the one this confirmation answers.
    client.post(f"/api/work-requests/{request['request_id']}/evidence", headers=MINA, files={"file": ("근거.txt", b"one", "text/plain")})

    stale = [row for row in client.get("/api/actions", headers=JIHO).json() if row["action_id"] == wrapper["action_id"]][0]
    assert stale["obsolete"] is True
    # Only the way out is offered; approving is not something the person can be led into.
    assert [command["id"] for command in stale["commands"]] == ["reject"]
    assert any("바뀌" in row["value"] for row in stale["preview"]), stale["preview"]

    # The same is true in the one judgement ledger.
    [envelope] = [row for row in jiho.pending_action_items() if row["action_item_id"] == wrapper["action_id"]]
    assert [command["id"] for command in envelope["allowed_commands"]] == ["reject"]

    # A client that approves anyway is told what happened, not handed a raw version error.
    refused = client.post(
        f"/api/actions/{wrapper['action_id']}/decide", headers=JIHO,
        json={"expected_version": wrapper["version"], "decision": "approve"},
    )
    assert refused.status_code == 422
    assert "바뀌" in refused.text and "stale" not in refused.text
    assert client.get("/api/my-work", headers=JIHO).json() == []
    assert [row["state"] for row in client.get("/api/actions", headers=JIHO).json() if row["action_id"] == wrapper["action_id"]] == ["pending"]

    # Clearing it away works, and the judgement itself is still there to be made afresh.
    cleared = client.post(
        f"/api/actions/{wrapper['action_id']}/decide", headers=JIHO,
        json={"expected_version": wrapper["version"], "decision": "reject"},
    )
    assert cleared.status_code == 200 and cleared.json()["state"] == "rejected"
    [again] = [row for row in jiho.pending_action_items() if row["kind"] == "work_request.acceptance"]
    assert [command["id"] for command in again["allowed_commands"]] == ["accept", "adjust", "reject"]


def test_a_confirmation_cannot_be_approved_when_its_target_can_no_longer_be_read(tmp_path, monkeypatch) -> None:
    """You cannot approve what you cannot see: an unverifiable confirmation offers only the way out."""
    database_url, settings, client = _stack(tmp_path)
    application = client.app.state.workflow_application
    client.post("/api/work-requests", headers=MINA, json={"title": "볼 수 없는 대상", "assignee_id": "jiho"})
    jiho = _facade(settings, "jiho")
    [item] = jiho.pending_action_items()
    _delegated_turn(client, application, JIHO, "jiho", monkeypatch)
    wrapper = jiho.run_action_command(item["action_item_id"], "accept", expected_version=item["expected_version"])

    with make_session_factory(database_url)() as session:
        for capability in ("work_request.read", "work_request.decide", "work_request.create"):
            session.execute(
                delete(RoleCapabilityRecord).where(
                    RoleCapabilityRecord.role_id == "seed-role:jiho", RoleCapabilityRecord.capability_id == capability
                )
            )
        session.commit()

    withheld = [row for row in client.get("/api/actions", headers=JIHO).json() if row["action_id"] == wrapper["action_id"]][0]
    assert withheld["obsolete"] is True
    assert [command["id"] for command in withheld["commands"]] == ["reject"]
    assert "볼 수 없는 대상" not in str(withheld)

    refused = client.post(
        f"/api/actions/{wrapper['action_id']}/decide", headers=JIHO,
        json={"expected_version": wrapper["version"], "decision": "approve"},
    )
    assert refused.status_code in {403, 422}
    assert client.get("/api/my-work", headers=JIHO).json() == []
    assert [row["state"] for row in client.get("/api/actions", headers=JIHO).json() if row["action_id"] == wrapper["action_id"]] == ["pending"]
