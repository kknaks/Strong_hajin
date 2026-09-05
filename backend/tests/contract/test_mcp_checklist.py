"""위임된 turn도 체크리스트를 다룬다 — 사람이 승인할 때만.

The same application commands the screen uses are reachable from a delegated chat turn, through tools that are only
discovered when the persona actually holds the capability. Inside a turn nothing is written: each mutation becomes a
gated AX Action the person approves, and only that approval applies the effect exactly once.
"""
import asyncio

from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.mcp import McpReportsFacade, create_mcp_server
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import RoleCapabilityRecord, make_session_factory

MINA = {"X-Demo-Persona": "mina"}

CHECKLIST_TOOLS = {
    "task_checklist_list",
    "task_checklist_add",
    "task_checklist_update",
    "task_checklist_archive",
    "task_checklist_reorder",
}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    return database_url, settings, TestClient(create_app(settings))


def _tools(settings, persona: str, monkeypatch):
    monkeypatch.setenv("AX_MCP_PERSONA", persona)
    return asyncio.run(create_mcp_server(settings).list_tools())


def _task_with_steps(client, *texts: str) -> tuple[str, list[dict]]:
    task_id = client.post("/api/tasks", headers=MINA, json={"title": "AX가 도울 업무"}).json()["task_id"]
    steps = [client.post(f"/api/tasks/{task_id}/checklist", headers=MINA, json={"text": text}).json() for text in texts]
    return task_id, steps


def _delegated_turn(client, application, headers, persona: str, monkeypatch) -> str:
    """Bind the facade to a real delegated turn, the way the conversation worker does."""
    from uuid import UUID

    from ax_workspace.platform.persistence import ConversationTurnRecord

    conversation = client.post("/api/conversations", headers=headers, json={"title": "위임 턴"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**headers, "Idempotency-Key": f"delegated-{persona}"},
        json={"body": "단계 하나 추가해 줘", "context": []},
    )
    assert accepted.status_code == 202, accepted.text
    with make_session_factory(application._settings.database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"])).execution_id
    monkeypatch.setenv("AX_MCP_CAUSATION_ID", str(execution_id))
    return str(execution_id)


def test_a_turn_reads_the_steps_and_proposes_every_change(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AX_MCP_CAUSATION_ID", raising=False)
    _, settings, client = _stack(tmp_path)
    task_id, [first, second] = _task_with_steps(client, "자료 모으기", "초안 쓰기")
    facade = McpReportsFacade(settings, "mina")

    listed = facade.task_checklist(task_id)
    assert [row["text"] for row in listed["checklist"]] == ["자료 모으기", "초안 쓰기"]
    assert listed["progress"] == {"done": 0, "total": 2}

    # Outside a delegated turn the tool is the command itself.
    added = facade.add_checklist_item(task_id, "검토 요청")
    assert added["text"] == "검토 요청" and added["version"] == 1
    assert [row["text"] for row in facade.task_checklist(task_id)["checklist"]] == ["자료 모으기", "초안 쓰기", "검토 요청"]

    checked = facade.update_checklist_item(task_id, first["item_id"], expected_version=1, done=True)
    assert checked["done"] is True
    archived = facade.archive_checklist_item(task_id, second["item_id"], expected_version=1)
    assert archived["state"] == "archived"
    reordered = facade.reorder_checklist(task_id, [added["item_id"], first["item_id"]])
    assert [row["text"] for row in reordered["checklist"]] == ["검토 요청", "자료 모으기"]


def test_inside_a_turn_a_step_is_proposed_and_only_an_approval_writes_it(tmp_path, monkeypatch) -> None:
    database_url, settings, client = _stack(tmp_path)
    task_id, [existing] = _task_with_steps(client, "자료 모으기")
    _delegated_turn(client, client.app.state.workflow_application, MINA, "mina", monkeypatch)

    facade = McpReportsFacade(settings, "mina")
    proposed = facade.add_checklist_item(task_id, "검토 요청")
    assert proposed["state"] == "pending" and proposed["action_type"] == "task.checklist.add"
    # Nothing was written: the list is still what the person left it as.
    assert [row["text"] for row in client.get(f"/api/tasks/{task_id}", headers=MINA).json()["checklist"]] == ["자료 모으기"]

    # The card says what would happen, in the words of the work rather than a wire payload.
    [detail] = [row for row in client.get("/api/actions", headers=MINA).json() if row["action_id"] == proposed["action_id"]]
    assert detail["subject"] == "AX가 도울 업무"
    assert detail["operation_label"] == "체크리스트 단계 추가"
    assert any(row["value"] == "검토 요청" for row in detail["preview"])

    approved = client.post(
        f"/api/actions/{proposed['action_id']}/decide",
        headers=MINA,
        json={"decision": "approve", "expected_version": detail["version"]},
    )
    assert approved.status_code == 200, approved.text
    assert [row["text"] for row in client.get(f"/api/tasks/{task_id}", headers=MINA).json()["checklist"]] == [
        "자료 모으기",
        "검토 요청",
    ]
    assert existing["item_id"] != proposed["action_id"]


def test_the_tools_appear_only_for_a_persona_who_may_use_them(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AX_MCP_CAUSATION_ID", raising=False)
    database_url, settings, _ = _stack(tmp_path)
    names = {tool.name for tool in _tools(settings, "mina", monkeypatch)}
    assert CHECKLIST_TOOLS <= names
    read_only = {tool.name for tool in _tools(settings, "mina", monkeypatch) if tool.annotations and tool.annotations.read_only_hint}
    assert "task_checklist_list" in read_only
    assert not (CHECKLIST_TOOLS - {"task_checklist_list"}) & read_only

    # Take the ability to manage one's own work away, and the mutations are not on the menu at all.
    with make_session_factory(database_url)() as session:
        session.execute(
            RoleCapabilityRecord.__table__.delete().where(RoleCapabilityRecord.__table__.c.capability_id == "task.self_manage")
        )
        session.commit()
    limited = {tool.name for tool in _tools(settings, "mina", monkeypatch)}
    assert "task_checklist_add" not in limited and "task_checklist_reorder" not in limited
