"""Task는 자기가 만들어진 사실만 소유한다.

Who sent the work, who holds it now, and who put them on it are three different relationships, and none of them is a
column on the Task. The Task keeps the actor who created it; everything else is resolved from the WorkRequest it came
from and from the assignment that is active right now. A task nobody handed over has no such relationship at all, and
inventing one would put a person in a role they never played.
"""
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import TaskAssignmentRecord, TaskRecord, make_session_factory

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    app = create_app(settings)
    return TestClient(app), app.state.workflow_application, database_url


def _assignments(database_url: str, task_id: str) -> list[TaskAssignmentRecord]:
    with make_session_factory(database_url)() as session:
        return list(
            session.scalars(
                select(TaskAssignmentRecord)
                .where(TaskAssignmentRecord.task_id == UUID(task_id))
                .order_by(TaskAssignmentRecord.created_at, TaskAssignmentRecord.id)
            )
        )


def _task_row(database_url: str, task_id: str) -> TaskRecord:
    with make_session_factory(database_url)() as session:
        return session.get(TaskRecord, UUID(task_id))


def test_a_task_i_made_records_who_made_it_and_nobody_else(tmp_path) -> None:
    client, _, database_url = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "내가 만든 업무"}).json()

    row = _task_row(database_url, task["task_id"])
    assert row.created_by_actor_id == "mina"
    assert not hasattr(row, "owner_id")

    # One active self assignment, and nobody put her on it.
    [assignment] = _assignments(database_url, task["task_id"])
    assert assignment.assignee_id == "mina" and assignment.assignment_kind == "self"
    assert assignment.status == "active" and assignment.assigned_by is None

    # Every surface reads the holder from that assignment.
    [listed] = [row for row in client.get("/api/my-work", headers=MINA).json() if row["task_id"] == task["task_id"]]
    assert listed["assignee"] == {"member_id": "mina", "display_name": "민아 (구성원)"}
    assert listed["origin"] is None


def test_an_accepted_request_keeps_the_sender_where_the_request_is(tmp_path) -> None:
    client, _, database_url = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "요청한 업무", "assignee_id": "jiho"}).json()
    [item] = client.get("/api/action-items", headers=JIHO).json()
    accepted = client.post(
        f"/api/action-items/{item['action_item_id']}/commands/accept",
        headers=JIHO,
        json={"expected_version": item["expected_version"]},
    )
    assert accepted.status_code == 200, accepted.text
    [task] = [row for row in client.get("/api/my-work", headers=JIHO).json() if row["title"] == "요청한 업무"]

    row = _task_row(database_url, task["task_id"])
    # The acceptance created the Task, and the sender is not copied onto it.
    assert row.created_by_actor_id == "jiho"
    assert str(row.source_work_request_id) == request["request_id"]

    [assignment] = _assignments(database_url, task["task_id"])
    assert assignment.assignee_id == "jiho" and assignment.assignment_kind == "request_effect"
    assert assignment.status == "active" and assignment.assigned_by is None

    # The sender is resolved from the request, with permission, not from a column on the Task.
    assert task["origin"]["kind"] == "work_request"
    assert task["origin"]["actor"] == {"member_id": "mina", "display_name": "민아 (구성원)"}
    assert task["assignee"] == {"member_id": "jiho", "display_name": "지호 (팀장)"}


def test_a_manager_putting_someone_on_work_is_recorded_on_the_assignment(tmp_path) -> None:
    client, application, database_url = _stack(tmp_path)
    jiho = application.authenticated_principal("jiho")
    assigned = application.assign_task(jiho, "배정한 업무", "mina")
    task_id = assigned["task"]["task_id"]

    row = _task_row(database_url, task_id)
    assert row.created_by_actor_id == "jiho"

    [assignment] = _assignments(database_url, task_id)
    assert assignment.assignee_id == "mina" and assignment.assigned_by == "jiho"
    assert assignment.assignment_kind == "direct" and assignment.status == "pending"


def test_changing_the_holder_supersedes_the_one_before_it(tmp_path) -> None:
    """At most one active assignment, and the change is appended rather than written over."""
    client, application, database_url = _stack(tmp_path)
    jiho = application.authenticated_principal("jiho")
    task_id = application.assign_task(jiho, "옮겨 다닐 업무", "mina")["task"]["task_id"]
    [item] = [row for row in client.get("/api/action-items", headers=MINA).json() if row["subject"] == "옮겨 다닐 업무"]
    client.post(
        f"/api/action-items/{item['action_item_id']}/commands/accept",
        headers=MINA,
        json={"expected_version": item["expected_version"]},
    )
    current = client.get(f"/api/tasks/{task_id}", headers=MINA).json()

    moved = client.post(
        f"/api/tasks/{task_id}/reassign",
        headers=JIHO,
        json={"expected_version": current["version"], "assignee_id": "jiho", "reason": "제가 이어서 하겠습니다"},
    )
    assert moved.status_code == 200, moved.text

    rows = _assignments(database_url, task_id)
    assert [row.status for row in rows] == ["superseded", "pending"]
    assert [row.assignee_id for row in rows] == ["mina", "jiho"]
    assert rows[1].assigned_by == "jiho" and rows[1].supersedes_assignment_id == rows[0].id
    assert rows[0].superseded_at is not None
    assert len([row for row in rows if row.status == "active"]) == 0

    # The one who was moved off no longer holds it, and the ledger says who moved it and why.
    assert client.get("/api/my-work", headers=MINA).json() == []
    from ax_workspace.platform.persistence import ActivityEventRecord

    with make_session_factory(database_url)() as session:
        [event] = [
            row for row in session.scalars(select(ActivityEventRecord)) if row.event_kind == "task.reassigned"
        ]
    assert event.actor_id == "jiho" and event.reason == "제가 이어서 하겠습니다"
    assert event.safe_summary == "지호가 담당자를 민아에서 지호로 바꿈: 옮겨 다닐 업무"
    assert event.before_ref == f"task_assignment:{rows[0].id}" and event.after_ref == f"task_assignment:{rows[1].id}"


def test_only_someone_who_may_assign_can_change_the_holder(tmp_path) -> None:
    client, application, database_url = _stack(tmp_path)
    jiho = application.authenticated_principal("jiho")
    task_id = application.assign_task(jiho, "권한 확인 업무", "mina")["task"]["task_id"]
    # The assignment is still pending, so nobody holds it yet; the version comes from the row itself.
    current = {"version": _task_row(database_url, task_id).version}

    # The holder cannot hand their own work to someone else, and a stranger cannot touch it at all.
    assert client.post(
        f"/api/tasks/{task_id}/reassign", headers=MINA,
        json={"expected_version": current["version"], "assignee_id": "jiho"},
    ).status_code in {403, 404, 422}
    assert client.post(
        f"/api/tasks/{task_id}/reassign", headers={"X-Demo-Persona": "sora"},
        json={"expected_version": current["version"], "assignee_id": "jiho"},
    ).status_code in {403, 404, 422}
    # A stale version and a holder who is already on it are both refused.
    assert client.post(
        f"/api/tasks/{task_id}/reassign", headers=JIHO,
        json={"expected_version": current["version"] + 5, "assignee_id": "jiho"},
    ).status_code == 422
    assert client.post(
        f"/api/tasks/{task_id}/reassign", headers=JIHO,
        json={"expected_version": current["version"], "assignee_id": "mina"},
    ).status_code == 422

    rows = _assignments(database_url, task_id)
    assert [row.status for row in rows] == ["pending"] and rows[0].assignee_id == "mina"


def test_the_relationship_table_is_a_projection_that_can_be_rebuilt(tmp_path) -> None:
    """`ResourceRelationship` says who may reach a resource. It never decides who sent or holds the work."""
    client, application, database_url = _stack(tmp_path)
    from ax_workspace.platform.persistence import ResourceRelationshipRecord
    from ax_workspace.platform.work_tasks import SqlAlchemyWorkRequestRepository

    request = client.post(
        "/api/work-requests",
        headers=MINA,
        json={"title": "관계가 붙는 요청", "assignee_id": "jiho", "cc_member_ids": ["demo-admin"]},
    ).json()
    assigned = application.assign_task(application.authenticated_principal("jiho"), "배정한 업무", "mina")

    def relationships(session) -> set[tuple[str, str, str, str]]:
        return {
            (row.member_id, row.resource_type, row.resource_id, row.relationship_kind)
            for row in session.scalars(select(ResourceRelationshipRecord))
            if row.valid_until is None
        }

    with make_session_factory(database_url)() as session:
        before = relationships(session)
    assert ("mina", "work_request", request["request_id"], "requester") in before
    assert ("jiho", "work_request", request["request_id"], "assignee") in before
    assert ("demo-admin", "work_request", request["request_id"], "cc") in before

    # Throw away everything this table derives from the canonical rows, and rebuild it: it comes back the same.
    # `cc` is not derived — being copied in is itself an access relationship, and this table is where it lives.
    with make_session_factory(database_url)() as session:
        for row in session.scalars(select(ResourceRelationshipRecord)):
            if row.relationship_kind in {"requester", "assignee"}:
                session.delete(row)
        session.flush()
        assert relationships(session) == {("demo-admin", "work_request", request["request_id"], "cc")}
        assert SqlAlchemyWorkRequestRepository(session).rebuild_relationships() == 2
        assert relationships(session) == before
        # Rebuilding again changes nothing: it reconstructs, it does not accumulate.
        assert SqlAlchemyWorkRequestRepository(session).rebuild_relationships() == 0
        assert relationships(session) == before
        session.commit()

    # Who put someone on it and who holds it never came from this table, so wiping it changed neither answer.
    task = client.get(f"/api/tasks/{assigned['task']['task_id']}", headers=JIHO).json()
    assert task["origin"]["actor"] == {"member_id": "jiho", "display_name": "지호 (팀장)"}
    assert task["assignee"] == {"member_id": "mina", "display_name": "민아 (구성원)"}
    assert client.get(f"/api/work-requests/{request['request_id']}/timeline", headers={"X-Demo-Persona": "demo-admin"}).status_code == 200
