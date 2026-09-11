"""위임된 turn도 체크리스트를 다룬다 — 사람이 승인할 때만.

The same application commands the screen uses are reachable from a delegated chat turn, through tools that are only
discovered when the persona actually holds the capability. Inside a turn nothing is written: each mutation becomes a
gated AX Action the person approves, and only that approval applies the effect exactly once.
"""
import asyncio
from uuid import uuid4

from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.mcp import McpReportsFacade, create_mcp_server
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import RoleCapabilityRecord, make_session_factory

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}

CHECKLIST_TOOLS = {
    "task_checklist_list",
    "task_checklist_add",
    "task_checklist_update",
    "task_checklist_archive",
    "task_checklist_reorder",
    "task_progress_batch",
}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    return database_url, settings, TestClient(create_app(settings))


def _tools(settings, persona: str, monkeypatch):
    monkeypatch.setenv("AX_MCP_PERSONA", persona)
    return asyncio.run(create_mcp_server(settings).list_tools())


def _task_with_steps(client, *texts: str, title: str = "AX가 도울 업무") -> tuple[str, list[dict]]:
    task_id = client.post("/api/tasks", headers=MINA, json={"title": title}).json()["task_id"]
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


def test_one_turn_batches_distinct_task_progress_and_applies_every_item_once(tmp_path, monkeypatch) -> None:
    _, settings, client = _stack(tmp_path)
    first_task, [first_step] = _task_with_steps(client, "취합 완료", title="A병원 CPA 데이터 취합")
    second_task, _ = _task_with_steps(client, title="플레이스 순위")
    third_task, _ = _task_with_steps(client, title="인스타 체험단")
    second_version = client.get(f"/api/tasks/{second_task}", headers=MINA).json()["version"]
    third_version = client.get(f"/api/tasks/{third_task}", headers=MINA).json()["version"]
    _delegated_turn(client, client.app.state.workflow_application, MINA, "mina", monkeypatch)

    facade = McpReportsFacade(settings, "mina")
    proposed = facade.update_task_progress_batch([
        {
            "kind": "checklist.update",
            "task_id": first_task,
            "item_id": first_step["item_id"],
            "expected_version": first_step["version"],
            "done": True,
        },
        {
            "kind": "progress.note",
            "task_id": second_task,
            "expected_version": second_version,
            "summary": "플레이스 순위 확인 중",
        },
        {
            "kind": "progress.note",
            "task_id": third_task,
            "expected_version": third_version,
            "summary": "인스타 체험단 5명 컨택",
        },
    ])
    assert proposed["state"] == "pending"
    assert proposed["action_type"] == "task.progress.batch"
    assert all(not row["done"] for row in client.get(f"/api/tasks/{first_task}", headers=MINA).json()["checklist"])
    assert not any(row["event_kind"] == "task.progress.noted" for row in client.get(f"/api/tasks/{second_task}/history", headers=MINA).json()["activity"])

    detail = client.get(f"/api/action-items/{proposed['action_id']}", headers=MINA).json()
    assert len(detail["rounds"][0]["snapshot"]["operations"]) == 3
    assert detail["edit_contract"]["editor"] == "task_progress_batch"
    edited = dict(detail["rounds"][0]["snapshot"])
    edited["operations"] = [dict(operation) for operation in edited["operations"]]
    edited["operations"][1]["summary"] = "플레이스 순위 검수 중"
    tampered = {"operations": [dict(operation) for operation in edited["operations"]]}
    tampered["operations"][1]["task_id"] = str(uuid4())
    refused = client.post(
        f"/api/action-items/{proposed['action_id']}/commands/confirm",
        headers=MINA,
        json={
            "expected_version": detail["expected_version"],
            "base_submission_version": detail["submission_version"],
            "draft": tampered,
        },
    )
    assert refused.status_code == 422
    confirmed = client.post(
        f"/api/action-items/{proposed['action_id']}/commands/confirm",
        headers=MINA,
        json={
            "expected_version": detail["expected_version"],
            "base_submission_version": detail["submission_version"],
            "draft": edited,
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["execution_result"]["batch_state"] == "completed"
    assert [row["status"] for row in confirmed.json()["execution_result"]["items"]] == ["applied"] * 3
    assert client.get(f"/api/tasks/{first_task}", headers=MINA).json()["checklist"][0]["done"] is True
    for task_id, summary in (
        (second_task, "진행 메모: 플레이스 순위 검수 중"),
        (third_task, "진행 메모: 인스타 체험단 5명 컨택"),
    ):
        [activity] = [
            row for row in client.get(f"/api/tasks/{task_id}/history", headers=MINA).json()["activity"]
            if row["event_kind"] == "task.progress.noted"
        ]
        assert activity["summary"] == summary
        assert activity["actor"]["member_id"] == "mina"
        assert activity["occurred_at"]
        assert activity["causation"] == {"kind": "action_item", "id": proposed["action_id"]}
    assert len(client.get("/api/my-work", headers=MINA).json()) == 3

    replay = client.post(
        f"/api/action-items/{proposed['action_id']}/commands/confirm",
        headers=MINA,
        json={
            "expected_version": detail["expected_version"],
            "base_submission_version": detail["submission_version"],
            "draft": edited,
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["execution_result"] == confirmed.json()["execution_result"]


def test_a_batch_reports_partial_outcomes_and_replay_does_not_repeat_the_applied_item(tmp_path, monkeypatch) -> None:
    _, settings, client = _stack(tmp_path)
    first_task, [first_step] = _task_with_steps(client, "완료할 단계", title="그대로 반영될 업무")
    stale_task, _ = _task_with_steps(client, title="먼저 바뀔 업무")
    stale_version = client.get(f"/api/tasks/{stale_task}", headers=MINA).json()["version"]
    _delegated_turn(client, client.app.state.workflow_application, MINA, "mina", monkeypatch)

    proposed = McpReportsFacade(settings, "mina").update_task_progress_batch([
        {
            "kind": "checklist.update",
            "task_id": first_task,
            "item_id": first_step["item_id"],
            "expected_version": first_step["version"],
            "done": True,
        },
        {
            "kind": "progress.note",
            "task_id": stale_task,
            "expected_version": stale_version,
            "summary": "반영되지 않아야 할 메모",
        },
    ])
    # Someone changes the second target after AX froze its proposal.
    client.post(f"/api/tasks/{stale_task}/checklist", headers=MINA, json={"text": "새 단계"})
    detail = client.get(f"/api/action-items/{proposed['action_id']}", headers=MINA).json()
    body = {
        "expected_version": detail["expected_version"],
        "base_submission_version": detail["submission_version"],
    }
    approved = client.post(
        f"/api/action-items/{proposed['action_id']}/commands/confirm", headers=MINA, json=body
    )
    assert approved.status_code == 200, approved.text
    result = approved.json()["execution_result"]
    assert result["batch_state"] == "partial" and result["applied_count"] == 1 and result["total_count"] == 2
    assert [row["status"] for row in result["items"]] == ["applied", "stale"]
    first_after = client.get(f"/api/tasks/{first_task}", headers=MINA).json()
    assert first_after["checklist"][0]["done"] is True
    assert not any(
        row["event_kind"] == "task.progress.noted"
        for row in client.get(f"/api/tasks/{stale_task}/history", headers=MINA).json()["activity"]
    )

    replay = client.post(
        f"/api/action-items/{proposed['action_id']}/commands/confirm", headers=MINA, json=body
    )
    assert replay.status_code == 200 and replay.json()["execution_result"] == result
    assert client.get(f"/api/tasks/{first_task}", headers=MINA).json()["version"] == first_after["version"]


def test_a_batch_reports_a_denied_item_without_leaking_its_task_title(tmp_path, monkeypatch) -> None:
    _, settings, client = _stack(tmp_path)
    own_task, _ = _task_with_steps(client, title="민아의 업무")
    hidden = client.post("/api/tasks", headers=JIHO, json={"title": "지호만 보는 업무"}).json()
    own_version = client.get(f"/api/tasks/{own_task}", headers=MINA).json()["version"]
    _delegated_turn(client, client.app.state.workflow_application, MINA, "mina", monkeypatch)
    proposed = McpReportsFacade(settings, "mina").update_task_progress_batch([
        {"kind": "progress.note", "task_id": own_task, "expected_version": own_version, "summary": "진행 중"},
        {"kind": "progress.note", "task_id": hidden["task_id"], "expected_version": hidden["version"], "summary": "볼 수 없음"},
    ])
    detail = client.get(f"/api/action-items/{proposed['action_id']}", headers=MINA).json()
    assert "지호만 보는 업무" not in str(detail)
    assert any(row["label"] == "볼 수 없는 업무" for row in detail["preview"])
    approved = client.post(
        f"/api/action-items/{proposed['action_id']}/commands/confirm",
        headers=MINA,
        json={
            "expected_version": detail["expected_version"],
            "base_submission_version": detail["submission_version"],
        },
    )
    assert approved.status_code == 200, approved.text
    assert [item["status"] for item in approved.json()["execution_result"]["items"]] == ["applied", "denied"]
    assert approved.json()["result_summary"] == "1/2건 반영됨 · 나머지 항목 확인 필요"


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
