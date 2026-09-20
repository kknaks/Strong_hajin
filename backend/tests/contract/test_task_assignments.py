"""ERD TASK_ASSIGNMENT: 본인 생성·수평 요청·관리자 배정 셋 다 **활성 담당 하나**를 그 자리에 세운다 (WORK-001 Phase 4)."""
from fastapi.testclient import TestClient
from sqlalchemy import select

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import DecisionItemRecord, ReviewDecisionRecord, TaskAssignmentRecord, make_session_factory

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
ADMIN = {"X-Demo-Persona": "yuna"}


def _client(tmp_path) -> tuple[TestClient, str]:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    return TestClient(create_app(Settings(RuntimeProfile.TEST, database_url))), database_url


def test_direct_task_and_sent_request_carry_an_active_assignment(tmp_path) -> None:
    client, database_url = _client(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "혼자 할 일"}).json()
    assert task["assignment"]["kind"] == "self" and task["assignment"]["status"] == "active"
    # 수평 요청은 업무를 세우고 **담당은 수락이 세운다**. 요청 행은 출처로 남는다 (SPEC-003 §4 발송).
    request = client.post("/api/work-requests", headers=MINA, json={"title": "검토", "assignee_id": "jiho"}).json()
    assert request["state"] == "pending" and request["task_id"]
    sent_task = client.get(f"/api/tasks/{request['task_id']}", headers=MINA).json()
    # 발송 직후의 담당 행은 **수락 대기**다 — 아무도 아직 들지 않았고 `accepted_at` 도 비어 있다.
    assert sent_task["assignment"] == {
        "assignment_id": sent_task["assignment"]["assignment_id"],
        "kind": "request_effect",
        "status": "pending",
        "assigned_by": None,
        "accepted_at": None,
    }
    assert sent_task["derived"]["assignment"] == "awaiting_acceptance"
    assert sent_task["lineage"]["source_work_request_id"] == request["request_id"]
    with make_session_factory(database_url)() as session:
        rows = session.scalars(select(TaskAssignmentRecord)).all()
        assert {row.assignment_kind for row in rows} == {"self", "request_effect"}
        # 본인 업무는 만든 순간 자기 것이고, 요청에서 난 업무는 **수락을 기다린다.**
        assert {row.assignment_kind: row.status for row in rows} == {"self": "active", "request_effect": "pending"}
        # 수락 회차는 **열려 있고**, 답은 아직 없다 — 사람이 하지 않은 수락을 기록하지 않는다.
        assert [row.kind for row in session.scalars(select(DecisionItemRecord))] == ["work_request.acceptance"]
        assert session.scalars(select(ReviewDecisionRecord)).all() == []


def test_manager_assignment_enters_my_work_at_once(tmp_path) -> None:
    client, database_url = _client(tmp_path)
    candidates = client.get("/api/task-assignment-candidates", headers=JIHO)
    assert candidates.status_code == 200
    # jiho leads 제품팀, so the people he may put work on are that team's — 대표도 다른 팀 사람도 여기 없다.
    assert [item["id"] for item in candidates.json()] == ["mina"]
    assert client.get("/api/task-assignment-candidates", headers=MINA).status_code == 403

    assigned = client.post(
        "/api/tasks/assign",
        headers=JIHO,
        json={"title": "분기 보고 정리", "assignee_id": "mina", "description": "지난 분기 수치", "due_date": "2026-09-30"},
    )
    assert assigned.status_code == 201, assigned.text
    assignment = assigned.json()
    assert assignment["status"] == "active" and assignment["assigned_by"] == "jiho"
    assert assignment["task"]["origin_kind"] == "assignment" and assignment["task"]["assignment"]["status"] == "active"
    task_id = assignment["task"]["task_id"]

    # 수락을 기다리지 않는다 — 명령이 성공하면 상대의 목록에 서 있고 바로 시작할 수 있다.
    my_work = {item["task_id"]: item for item in client.get("/api/my-work", headers=MINA).json()}
    assert my_work[task_id]["assignment"]["kind"] == "direct" and my_work[task_id]["assignment"]["assigned_by"] == "jiho"
    assert my_work[task_id]["due_date"] == "2026-09-30"
    assert client.get(f"/api/tasks/{task_id}", headers=MINA).status_code == 200
    started = client.post(f"/api/tasks/{task_id}/start", headers=MINA, json={"expected_version": 1})
    assert started.status_code == 200

    # 수락용 판단 항목이 아무에게도 생기지 않는다.
    assert [item for item in client.get("/api/action-items", headers=MINA).json() if item["kind"] == "task.assignment"] == []
    sent = client.get("/api/task-assignments/sent", headers=JIHO).json()
    assert sent[0]["status"] == "active" and sent[0]["task"]["state"] == "in_progress"
    with make_session_factory(database_url)() as session:
        assert session.scalar(select(DecisionItemRecord).where(DecisionItemRecord.kind == "task.assignment.acceptance")) is None
        assert session.scalars(select(ReviewDecisionRecord)).all() == []
    # 사람이 하지 않은 판단을 업무 이력이 가리키지 않는다. 배정한 사람·시각은 담당 행에 그대로 남는다.
    task = client.get(f"/api/tasks/{task_id}", headers=MINA).json()
    assert task["lineage"]["source_decision_item_id"] is None and task["lineage"]["source_review_decision_id"] is None
    assert task["assignment"]["assigned_by"] == "jiho" and task["assignment"]["accepted_at"]


def test_a_new_assignment_takes_no_acceptance_or_decline_command_and_nothing_changes(tmp_path) -> None:
    """신규 경로에 거절 명령을 **신설하지 않는다** (DEC-001 D-4). 과거 행을 위해 남은 endpoint 는 그대로 있다.

    그 endpoint 를 신규 배정에 걸면 정의된 도메인 거부이고, 업무·담당·이력에 side effect 가 없다.
    잘못 온 배정의 출구는 문의·요청과 담당자 변경이다.
    """
    client, database_url = _client(tmp_path)
    assignment = client.post("/api/tasks/assign", headers=ADMIN, json={"title": "감사 자료 준비", "assignee_id": "mina"}).json()
    task_id = assignment["task"]["task_id"]

    # 받는 사람에게는 「지금 답할 회차가 아니다」로 거절한다.
    declined = client.post(f"/api/task-assignments/{assignment['assignment_id']}/decline", headers=MINA, json={"reason": "휴가 중"})
    assert declined.status_code == 422 and "awaiting acceptance" in declined.text
    accepted = client.post(f"/api/task-assignments/{assignment['assignment_id']}/accept", headers=MINA)
    assert accepted.status_code == 422 and "awaiting acceptance" in accepted.text

    # 남의 답을 대신 낼 수는 없고, 그 경계는 **존재를 숨기는 404** 다 — 보낸 사람에게도 마찬가지다.
    for headers in (ADMIN, JIHO):
        hidden = client.post(f"/api/task-assignments/{assignment['assignment_id']}/accept", headers=headers)
        assert hidden.status_code == 404, hidden.text
        hidden = client.post(
            f"/api/task-assignments/{assignment['assignment_id']}/decline", headers=headers, json={"reason": "대신 거절"}
        )
        assert hidden.status_code == 404, hidden.text

    task = client.get(f"/api/tasks/{task_id}", headers=MINA).json()
    assert task["state"] == "open" and task["assignment"]["status"] == "active"
    assert task_id in {item["task_id"] for item in client.get("/api/my-work", headers=MINA).json()}
    sent = client.get("/api/task-assignments/sent", headers=ADMIN).json()
    assert sent[0]["status"] == "active" and sent[0]["decline_reason"] is None
    with make_session_factory(database_url)() as session:
        assert session.scalars(select(ReviewDecisionRecord)).all() == []


def test_assignment_scope_and_capability_are_enforced(tmp_path) -> None:
    client, _ = _client(tmp_path)
    assert client.post("/api/tasks/assign", headers=MINA, json={"title": "x", "assignee_id": "jiho"}).status_code == 403
    out_of_scope = client.post("/api/tasks/assign", headers=JIHO, json={"title": "x", "assignee_id": "sora"})
    assert out_of_scope.status_code == 422
    self_assign = client.post("/api/tasks/assign", headers=JIHO, json={"title": "x", "assignee_id": "jiho"})
    assert self_assign.status_code == 422
