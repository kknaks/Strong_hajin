"""ERD TASK_ASSIGNMENT: self assignment on direct tasks, manager assignment that waits for acceptance."""
from fastapi.testclient import TestClient
from sqlalchemy import select

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import DecisionItemRecord, ReviewDecisionRecord, TaskAssignmentRecord, make_session_factory

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
ADMIN = {"X-Demo-Persona": "demo-admin"}


def _client(tmp_path) -> tuple[TestClient, str]:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    return TestClient(create_app(Settings(RuntimeProfile.TEST, database_url))), database_url


def test_direct_task_and_accepted_request_carry_an_active_assignment(tmp_path) -> None:
    client, database_url = _client(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "혼자 할 일"}).json()
    assert task["assignment"]["kind"] == "self" and task["assignment"]["status"] == "active"
    request = client.post("/api/work-requests", headers=MINA, json={"title": "검토", "assignee_id": "jiho"}).json()
    accepted = client.post(f"/api/work-requests/{request['request_id']}/accept", headers=JIHO, json={"expected_version": 1}).json()
    accepted_task = client.get(f"/api/tasks/{accepted['task_id']}", headers=JIHO).json()
    assert accepted_task["assignment"] == {
        "assignment_id": accepted_task["assignment"]["assignment_id"],
        "kind": "request_effect",
        "status": "active",
        "assigned_by": "mina",
        "accepted_at": accepted_task["assignment"]["accepted_at"],
    }
    with make_session_factory(database_url)() as session:
        rows = session.scalars(select(TaskAssignmentRecord)).all()
        assert {row.assignment_kind for row in rows} == {"self", "request_effect"}
        assert all(row.status == "active" for row in rows)


def test_manager_assignment_enters_my_work_only_after_the_assignee_accepts(tmp_path) -> None:
    client, database_url = _client(tmp_path)
    candidates = client.get("/api/task-assignment-candidates", headers=JIHO)
    assert candidates.status_code == 200
    # jiho leads 제품팀; demo-admin also holds a 제품팀 membership, sora (법무팀) does not appear.
    assert [item["id"] for item in candidates.json()] == ["demo-admin", "mina"]
    assert client.get("/api/task-assignment-candidates", headers=MINA).status_code == 403

    assigned = client.post(
        "/api/tasks/assign",
        headers=JIHO,
        json={"title": "분기 보고 정리", "assignee_id": "mina", "description": "지난 분기 수치", "due_date": "2026-09-30"},
    )
    assert assigned.status_code == 201, assigned.text
    assignment = assigned.json()
    assert assignment["status"] == "pending" and assignment["assigned_by"] == "jiho"
    assert assignment["task"]["origin_kind"] == "assignment" and assignment["task"]["assignment"]["status"] == "pending"
    task_id = assignment["task"]["task_id"]

    # Not in My Work, not readable, not drivable before acceptance.
    assert task_id not in {item["task_id"] for item in client.get("/api/my-work", headers=MINA).json()}
    assert client.get(f"/api/tasks/{task_id}", headers=MINA).status_code == 404
    assert client.post(f"/api/tasks/{task_id}/start", headers=MINA, json={"expected_version": 1}).status_code == 404

    inbox = client.get("/api/task-assignments/inbox", headers=MINA).json()
    assert [item["assignment_id"] for item in inbox] == [assignment["assignment_id"]]
    assert inbox[0]["task"]["title"] == "분기 보고 정리"
    # Only the assignee may answer.
    assert client.post(f"/api/task-assignments/{assignment['assignment_id']}/accept", headers=JIHO).status_code == 404

    accepted = client.post(f"/api/task-assignments/{assignment['assignment_id']}/accept", headers=MINA)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "active"
    my_work = {item["task_id"]: item for item in client.get("/api/my-work", headers=MINA).json()}
    assert my_work[task_id]["assignment"]["kind"] == "direct" and my_work[task_id]["assignment"]["assigned_by"] == "jiho"
    assert my_work[task_id]["due_date"] == "2026-09-30"
    started = client.post(f"/api/tasks/{task_id}/start", headers=MINA, json={"expected_version": 1})
    assert started.status_code == 200
    assert client.get("/api/task-assignments/inbox", headers=MINA).json() == []
    sent = client.get("/api/task-assignments/sent", headers=JIHO).json()
    assert sent[0]["status"] == "active" and sent[0]["task"]["state"] == "in_progress"
    # Acceptance is a recorded review decision on the assignment's ActionItem.
    with make_session_factory(database_url)() as session:
        item = session.scalar(select(DecisionItemRecord).where(DecisionItemRecord.kind == "task.assignment.acceptance"))
        assert item is not None and item.status == "resolved"
        decision = session.scalar(select(ReviewDecisionRecord))
        assert decision is not None and decision.decision == "accept" and decision.actor_member_id == "mina"
    # The task now carries the acceptance lineage.
    task = client.get(f"/api/tasks/{task_id}", headers=MINA).json()
    assert task["lineage"]["source_decision_item_id"] and task["lineage"]["source_review_decision_id"]


def test_declined_assignment_never_enters_my_work_and_records_the_reason(tmp_path) -> None:
    client, _ = _client(tmp_path)
    assignment = client.post("/api/tasks/assign", headers=ADMIN, json={"title": "감사 자료 준비", "assignee_id": "mina"}).json()
    missing = client.post(f"/api/task-assignments/{assignment['assignment_id']}/decline", headers=MINA, json={"reason": " "})
    assert missing.status_code == 422
    declined = client.post(f"/api/task-assignments/{assignment['assignment_id']}/decline", headers=MINA, json={"reason": "휴가 중"})
    assert declined.status_code == 200, declined.text
    assert declined.json()["status"] == "declined" and declined.json()["decline_reason"] == "휴가 중"
    assert declined.json()["task"]["state"] == "cancelled"
    assert assignment["task"]["task_id"] not in {item["task_id"] for item in client.get("/api/my-work", headers=MINA).json()}
    sent = client.get("/api/task-assignments/sent", headers=ADMIN).json()
    assert sent[0]["status"] == "declined" and sent[0]["decline_reason"] == "휴가 중"
    # A decided assignment cannot be answered twice.
    again = client.post(f"/api/task-assignments/{assignment['assignment_id']}/accept", headers=MINA)
    assert again.status_code == 422


def test_assignment_scope_and_capability_are_enforced(tmp_path) -> None:
    client, _ = _client(tmp_path)
    assert client.post("/api/tasks/assign", headers=MINA, json={"title": "x", "assignee_id": "jiho"}).status_code == 403
    out_of_scope = client.post("/api/tasks/assign", headers=JIHO, json={"title": "x", "assignee_id": "sora"})
    assert out_of_scope.status_code == 422
    self_assign = client.post("/api/tasks/assign", headers=JIHO, json={"title": "x", "assignee_id": "jiho"})
    assert self_assign.status_code == 422
