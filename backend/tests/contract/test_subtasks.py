"""하위 업무는 체크리스트가 아니라 업무다.

Some steps need their own person, their own deadline and their own acceptance — those are Tasks, not checklist
lines. A parent is context and a place to see progress; it is never the truth about a child's state, and finishing
a child never finishes the parent. One level only, for now, and nothing crosses an organization.
"""
from uuid import UUID

from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import TaskRecord, make_session_factory

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
SORA = {"X-Demo-Persona": "sora"}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    app = create_app(settings)
    return TestClient(app), app.state.workflow_application, database_url


def _task(client, title: str, headers=MINA, **body) -> str:
    created = client.post("/api/tasks", headers=headers, json={"title": title, **body})
    assert created.status_code == 201, created.text
    return created.json()["task_id"]


def test_a_piece_of_work_can_be_broken_into_work(tmp_path) -> None:
    client, _, database_url = _stack(tmp_path)
    parent = _task(client, "분기 마감")

    child = client.post("/api/tasks", headers=MINA, json={"title": "매출 집계", "parent_task_id": parent, "due_date": "2026-10-10"})
    assert child.status_code == 201, child.text
    child_id = child.json()["task_id"]

    # A child is a Task in every way: its own dates, state, version and lifecycle commands.
    view = client.get(f"/api/tasks/{child_id}", headers=MINA).json()
    assert view["due_date"] == "2026-10-10" and view["state"] == "open" and view["version"] == 1
    assert view["parent"] == {"task_id": parent, "title": "분기 마감", "state": "open"}
    started = client.post(f"/api/tasks/{child_id}/start", headers=MINA, json={"expected_version": view["version"]})
    assert started.status_code == 200, started.text

    # The parent is where you see them together, with how far along they are.
    parent_view = client.get(f"/api/tasks/{parent}", headers=MINA).json()
    assert [row["title"] for row in parent_view["children"]] == ["매출 집계"]
    assert parent_view["children"][0]["state"] == "in_progress"
    assert parent_view["child_progress"] == {"done": 0, "total": 1}
    assert parent_view["parent"] is None

    with make_session_factory(database_url)() as session:
        assert session.get(TaskRecord, UUID(child_id)).parent_task_id == UUID(parent)


def test_one_level_only_and_never_into_work_that_is_over(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    parent = _task(client, "부모 업무")
    child = _task(client, "자식 업무", parent_task_id=parent)

    # No grandchildren yet, and nothing is its own parent.
    assert client.post("/api/tasks", headers=MINA, json={"title": "손자", "parent_task_id": child}).status_code == 422
    assert client.patch(
        f"/api/tasks/{parent}",
        headers=MINA,
        json={"expected_version": client.get(f"/api/tasks/{parent}", headers=MINA).json()["version"], "title": "그대로"},
    ).status_code == 200

    # Work you cannot read is not a parent you can pick, and neither is one that no longer exists.
    theirs = _task(client, "지호의 업무", JIHO)
    assert client.post("/api/tasks", headers=MINA, json={"title": "몰래", "parent_task_id": theirs}).status_code in {403, 404, 422}
    assert client.post(
        "/api/tasks", headers=MINA, json={"title": "없는 부모", "parent_task_id": "11111111-1111-4111-8111-111111111111"}
    ).status_code in {404, 422}

    # A parent that is finished or cancelled takes no new children.
    closed = _task(client, "이미 끝난 업무")
    current = client.get(f"/api/tasks/{closed}", headers=MINA).json()
    client.post(f"/api/tasks/{closed}/cancel", headers=MINA, json={"expected_version": current["version"]})
    refused = client.post("/api/tasks", headers=MINA, json={"title": "뒤늦은 자식", "parent_task_id": closed})
    assert refused.status_code == 422


def test_a_parent_is_not_finished_while_its_work_is_not(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    parent = _task(client, "마감 업무")
    child = _task(client, "남은 하위 업무", parent_task_id=parent)
    current = client.get(f"/api/tasks/{parent}", headers=MINA).json()
    client.post(f"/api/tasks/{parent}/start", headers=MINA, json={"expected_version": current["version"]})
    running = client.get(f"/api/tasks/{parent}", headers=MINA).json()

    refused = client.post(f"/api/tasks/{parent}/complete", headers=MINA, json={"expected_version": running["version"]})
    assert refused.status_code == 422
    # It says which work is still open rather than just refusing.
    assert "남은 하위 업무" in refused.text

    # Finishing the child does not finish the parent; it only makes finishing it possible.
    child_view = client.get(f"/api/tasks/{child}", headers=MINA).json()
    client.post(f"/api/tasks/{child}/start", headers=MINA, json={"expected_version": child_view["version"]})
    child_view = client.get(f"/api/tasks/{child}", headers=MINA).json()
    client.post(f"/api/tasks/{child}/complete", headers=MINA, json={"expected_version": child_view["version"]})
    assert client.get(f"/api/tasks/{parent}", headers=MINA).json()["state"] == "in_progress"
    assert client.get(f"/api/tasks/{parent}", headers=MINA).json()["child_progress"] == {"done": 1, "total": 1}

    done = client.post(
        f"/api/tasks/{parent}/complete",
        headers=MINA,
        json={"expected_version": client.get(f"/api/tasks/{parent}", headers=MINA).json()["version"]},
    )
    assert done.status_code == 200, done.text
    # A cancelled child is over too: it does not hold the parent open.
    assert client.get(f"/api/tasks/{parent}", headers=MINA).json()["state"] == "done"


def test_someone_else_holding_a_child_sees_the_parent_but_not_through_it(tmp_path) -> None:
    client, application, _ = _stack(tmp_path)
    parent = _task(client, "부모가 되는 업무")
    _task(client, "내가 하는 하위 업무", parent_task_id=parent)

    jiho = application.authenticated_principal("jiho")
    assigned = application.assign_task(jiho, "지호가 만든 부모", "mina")
    # The manager assigns a child of their own parent to someone else.
    child = application.assign_task(jiho, "남에게 맡긴 하위 업무", "mina", parent_task_id=UUID(assigned["task"]["task_id"]))
    [item] = [row for row in client.get("/api/action-items", headers=MINA).json() if row["subject"] == "남에게 맡긴 하위 업무"]
    client.post(
        f"/api/action-items/{item['action_item_id']}/commands/accept",
        headers=MINA,
        json={"expected_version": item["expected_version"]},
    )

    # The holder of the child sees enough of the parent to know what it belongs to, and no more.
    child_view = client.get(f"/api/tasks/{child['task']['task_id']}", headers=MINA).json()
    assert child_view["parent"] == {"task_id": assigned["task"]["task_id"], "title": "지호가 만든 부모", "state": "open"}
    assert "children" not in child_view or child_view["children"] == []

    # Someone with no relationship to the parent learns nothing about what hangs under it.
    stranger = client.get(f"/api/tasks/{parent}", headers=SORA)
    assert stranger.status_code in {403, 404}
    assert "내가 하는 하위 업무" not in stranger.text


def test_breaking_work_down_is_recorded_on_both_sides(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    parent = _task(client, "기록이 남는 부모")
    child = _task(client, "기록이 남는 자식", parent_task_id=parent)

    parent_history = client.get(f"/api/tasks/{parent}/history", headers=MINA).json()
    assert any(row["event_kind"] == "task.subtask_added" for row in parent_history["activity"])
    assert any("기록이 남는 자식" in row["summary"] for row in parent_history["activity"])
    # The parent moved on, so the version that has this child is frozen with it.
    assert parent_history["versions"][-1]["snapshot"]["children"] == [child]
