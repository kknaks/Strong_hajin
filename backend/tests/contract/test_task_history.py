"""업무가 어떻게 여기까지 왔는지.

A Task shows its current values, but the question people actually ask is what changed, when, by whom and why. Every
meaningful mutation freezes an immutable snapshot in the same transaction that made it, so two versions can be
compared long after the fact — including the things a current screen hides, like an archived checklist item or a
material that was detached.

Bytes are never copied into a snapshot. It refers to the Attachment identity and its integrity hash, so history stays
cheap and an artifact still has exactly one identity.
"""
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import TaskVersionRecord, make_session_factory

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
SORA = {"X-Demo-Persona": "sora"}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    app = create_app(settings)
    return TestClient(app), app.state.workflow_application, database_url


def _history(client, task_id: str, headers=MINA):
    return client.get(f"/api/tasks/{task_id}/history", headers=headers)


def test_every_meaningful_change_freezes_the_task_as_it_then_was(tmp_path) -> None:
    client, _, database_url = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "처음 제목", "description": "처음 설명"}).json()
    task_id = task["task_id"]

    # Creation is version 1 and is itself a snapshot.
    history = _history(client, task_id).json()
    assert [row["version"] for row in history["versions"]] == [1]
    assert history["versions"][0]["snapshot"]["title"] == "처음 제목"
    assert history["versions"][0]["change_kind"] == "task.created"
    assert history["versions"][0]["actor_id"] == "mina"

    current = client.get(f"/api/tasks/{task_id}", headers=MINA).json()
    client.patch(
        f"/api/tasks/{task_id}",
        headers=MINA,
        json={"expected_version": current["version"], "title": "고친 제목", "due_date": "2026-12-01"},
    )
    started = client.get(f"/api/tasks/{task_id}", headers=MINA).json()
    client.post(f"/api/tasks/{task_id}/start", headers=MINA, json={"expected_version": started["version"]})

    history = _history(client, task_id).json()
    assert [row["version"] for row in history["versions"]] == [1, 2, 3]
    assert [row["change_kind"] for row in history["versions"]] == ["task.created", "task.updated", "task.state_changed"]
    # Each snapshot is what the Task was at that moment, not what it is now.
    assert history["versions"][0]["snapshot"]["title"] == "처음 제목"
    assert history["versions"][1]["snapshot"]["title"] == "고친 제목"
    assert history["versions"][1]["snapshot"]["state"] == "open"
    assert history["versions"][2]["snapshot"]["state"] == "in_progress"
    assert history["versions"][2]["snapshot"]["due_date"] == "2026-12-01"

    with make_session_factory(database_url)() as session:
        rows = list(session.scalars(select(TaskVersionRecord).order_by(TaskVersionRecord.version)))
        assert [row.version for row in rows] == [1, 2, 3]
        assert all(row.task_id == UUID(task_id) for row in rows)


def test_a_snapshot_keeps_what_the_current_screen_hides(tmp_path) -> None:
    """An archived step and a detached material are gone from the Task, not from what it was."""
    client, _, _ = _stack(tmp_path)
    task_id = client.post("/api/tasks", headers=MINA, json={"title": "숨겨질 것들"}).json()["task_id"]
    step = client.post(f"/api/tasks/{task_id}/checklist", headers=MINA, json={"text": "자료 모으기"}).json()
    material = client.post(
        f"/api/tasks/{task_id}/materials/links", headers=MINA,
        json={"kind": "input", "url": "https://docs.example.com/spec", "label": "설계 문서"},
    ).json()

    with_both = _history(client, task_id).json()["versions"][-1]
    assert [row["text"] for row in with_both["snapshot"]["checklist"]] == ["자료 모으기"]
    assert [row["attachment_id"] for row in with_both["snapshot"]["materials"]] == [material["attachment_id"]]
    # The snapshot points at the artifact; it never copies its bytes.
    assert with_both["snapshot"]["materials"][0]["integrity_ref"] == material["integrity_ref"]
    assert "url" not in with_both["snapshot"]["materials"][0] or with_both["snapshot"]["materials"][0].get("data") is None

    client.delete(f"/api/tasks/{task_id}/checklist/{step['item_id']}", headers=MINA)
    client.post(f"/api/tasks/{task_id}/materials/{material['material_id']}/detach", headers=MINA)

    assert client.get(f"/api/tasks/{task_id}", headers=MINA).json()["checklist"] == []
    assert client.get(f"/api/tasks/{task_id}/materials", headers=MINA).json() == []

    versions = _history(client, task_id).json()["versions"]
    # The current version has neither; the versions that had them still do.
    assert versions[-1]["snapshot"]["checklist"] == [] and versions[-1]["snapshot"]["materials"] == []
    assert any(row["snapshot"]["checklist"] and row["snapshot"]["checklist"][0]["text"] == "자료 모으기" for row in versions)
    assert any(row["snapshot"]["materials"] for row in versions)


def test_two_versions_can_be_compared_long_after_the_fact(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    task_id = client.post("/api/tasks", headers=MINA, json={"title": "비교할 업무", "description": "처음"}).json()["task_id"]
    current = client.get(f"/api/tasks/{task_id}", headers=MINA).json()
    client.patch(
        f"/api/tasks/{task_id}", headers=MINA,
        json={"expected_version": current["version"], "title": "바뀐 업무", "description": "나중"},
    )
    client.post(f"/api/tasks/{task_id}/checklist", headers=MINA, json={"text": "새 단계"})

    diff = client.get(f"/api/tasks/{task_id}/history/diff", headers=MINA, params={"from": 1, "to": 3}).json()
    assert diff["from"] == 1 and diff["to"] == 3
    assert diff["changes"]["title"] == {"before": "비교할 업무", "after": "바뀐 업무"}
    assert diff["changes"]["description"] == {"before": "처음", "after": "나중"}
    assert diff["changes"]["checklist"]["added"] == ["새 단계"] and diff["changes"]["checklist"]["removed"] == []
    # Nothing else is claimed to have moved.
    assert "state" not in diff["changes"]

    # A version that does not exist is refused rather than guessed at.
    assert client.get(f"/api/tasks/{task_id}/history/diff", headers=MINA, params={"from": 1, "to": 9}).status_code == 422


def test_history_is_read_by_the_same_people_who_may_read_the_task(tmp_path) -> None:
    client, application, _ = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "요청한 업무", "assignee_id": "jiho"}).json()
    [item] = client.get("/api/action-items", headers=JIHO).json()
    client.post(
        f"/api/action-items/{item['action_item_id']}/commands/accept",
        headers=JIHO,
        json={"expected_version": item["expected_version"]},
    )
    [task] = [row for row in client.get("/api/my-work", headers=JIHO).json() if row["title"] == "요청한 업무"]
    task_id = task["task_id"]

    # The holder reads it, and so does the person who sent the work.
    assert _history(client, task_id, JIHO).status_code == 200
    sender = _history(client, task_id, MINA)
    assert sender.status_code == 200 and [row["version"] for row in sender.json()["versions"]] == [1]

    # Someone with no relationship to it learns nothing, not even that it exists.
    stranger = _history(client, task_id, SORA)
    assert stranger.status_code in {403, 404}
    assert "요청한 업무" not in stranger.text


def test_history_reads_as_sentences_about_people_not_event_codes(tmp_path) -> None:
    """The list of versions answers "what changed"; the activity answers "what happened", in words."""
    client, _, _ = _stack(tmp_path)
    task_id = client.post("/api/tasks", headers=MINA, json={"title": "설명이 필요한 업무"}).json()["task_id"]
    client.post(f"/api/tasks/{task_id}/checklist", headers=MINA, json={"text": "자료 모으기"})
    started = client.get(f"/api/tasks/{task_id}", headers=MINA).json()
    client.post(f"/api/tasks/{task_id}/start", headers=MINA, json={"expected_version": started["version"]})
    running = client.get(f"/api/tasks/{task_id}", headers=MINA).json()
    blocked = client.post(
        f"/api/tasks/{task_id}/block",
        headers=MINA,
        json={"expected_version": running["version"], "reason": "자료를 기다립니다"},
    )
    assert blocked.status_code == 200, blocked.text

    history = _history(client, task_id).json()
    activity = history["activity"]
    # Newest first, because the question is almost always what just happened.
    assert [row["version"] for row in activity] == [4, 3, 2, 1]
    assert [row["event_kind"] for row in activity] == [
        "task.state_changed", "task.state_changed", "task.checklist.added", "task.created",
    ]
    # A person, not a member id, and the reason they gave.
    assert activity[0]["actor"] == {"member_id": "mina", "display_name": "민아 (구성원)"}
    assert activity[0]["reason"] == "자료를 기다립니다"
    assert "체크리스트 추가: 자료 모으기" in activity[2]["summary"]
    assert activity[3]["occurred_at"] <= activity[0]["occurred_at"]
    # Each line says which version it produced, so a reader can open exactly that snapshot.
    assert {row["version"] for row in activity} <= {row["version"] for row in history["versions"]}


def test_a_delegated_turn_reads_the_same_history_and_no_more(tmp_path) -> None:
    """`task_history` is the MCP window onto the same authorized projection — read-only, and no wider."""
    from ax_workspace.entrypoints.mcp import McpReportsFacade

    client, _, database_url = _stack(tmp_path)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    task_id = client.post("/api/tasks", headers=MINA, json={"title": "AX가 설명할 업무"}).json()["task_id"]
    client.post(f"/api/tasks/{task_id}/checklist", headers=MINA, json={"text": "자료 모으기"})

    mina = McpReportsFacade(settings, "mina")
    history = mina.task_history(task_id)
    assert [row["version"] for row in history["versions"]] == [1, 2]
    assert history["activity"][0]["event_kind"] == "task.checklist.added"

    sora = McpReportsFacade(settings, "sora")
    try:
        sora.task_history(task_id)
    except Exception as error:  # the same answer the REST surface gives a stranger
        assert "task" in str(error).lower() or "not" in str(error).lower()
    else:
        raise AssertionError("a stranger read a task history through MCP")
