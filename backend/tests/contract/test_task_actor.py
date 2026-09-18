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



def _accept(client, request: dict, headers=JIHO) -> None:
    """받는 사람이 수락한다 — 여기서 담당이 확정되고 그 업무가 「내 업무」에 선다 (SPEC-003 §4 수락).

    W1 에서는 이 단계가 없었다(발송이 곧 배정). v2 가 되돌린 것은 **이 한 단계뿐**이다.
    """
    answered = client.post(
        f"/api/work-requests/{request['request_id']}/accept",
        headers=headers, json={"expected_version": request["version"]},
    )
    assert answered.status_code == 200, answered.text

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


def test_a_sent_request_keeps_the_sender_where_the_request_is(tmp_path) -> None:
    client, _, database_url = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "요청한 업무", "assignee_id": "jiho"}).json()
    # v2: 발송은 업무를 세우고 **담당은 수락이 세운다** (SPEC-003 §4).
    _accept(client, request)
    [task] = [row for row in client.get("/api/my-work", headers=JIHO).json() if row["title"] == "요청한 업무"]

    row = _task_row(database_url, task["task_id"])
    # 보낸 사람의 명령이 이 업무를 있게 했으므로 행위자 칼럼은 그 사람이다 — 받는 사람은 아무 행위도
    # 하지 않았다. 「보낸 사람이 업무에 복사된다」는 뜻이 아니다: 담당·요청자는 아래처럼 관계에서 푼다.
    assert row.created_by_actor_id == "mina"
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
    assigned = application.assign_task(jiho, "배정한 업무", "mina", idempotency_key="actor-assign-1")
    task_id = assigned["task"]["task_id"]

    row = _task_row(database_url, task_id)
    assert row.created_by_actor_id == "jiho"

    [assignment] = _assignments(database_url, task_id)
    assert assignment.assignee_id == "mina" and assignment.assigned_by == "jiho"
    assert assignment.assignment_kind == "direct" and assignment.status == "active"


def test_changing_the_holder_supersedes_the_one_before_it(tmp_path) -> None:
    """At most one active assignment, and the change is appended rather than written over."""
    client, application, database_url = _stack(tmp_path)
    jiho = application.authenticated_principal("jiho")
    task_id = application.assign_task(jiho, "옮겨 다닐 업무", "mina", idempotency_key="actor-assign-2")["task"]["task_id"]
    # 배정도 수락을 기다리지 않는다 — 명령이 성공하면 상대의 업무 목록에 이미 서 있다.
    current = client.get(f"/api/tasks/{task_id}", headers=MINA).json()

    moved = client.post(
        f"/api/tasks/{task_id}/reassign",
        headers=JIHO,
        json={"expected_version": current["version"], "assignee_id": "jiho", "reason": "제가 이어서 하겠습니다"},
    )
    assert moved.status_code == 200, moved.text

    rows = _assignments(database_url, task_id)
    # v2: **제안이 기존 담당을 닫지 않는다** (정책 V-18). 답이 올 때까지 기존 담당이 그대로 책임지고,
    # 교체는 수락 한 덩어리에서만 일어난다 — 그래서 이 순간 `active` 1 + `pending` 1 이 공존한다.
    assert [row.status for row in rows] == ["active", "pending"]
    assert [row.assignee_id for row in rows] == ["mina", "jiho"]
    assert rows[1].assigned_by == "jiho" and rows[1].supersedes_assignment_id == rows[0].id
    # **책임 공백이 없다는 것이 이 줄의 뜻이다** (정책 V-18): 제안 직후에도 활성 담당은 정확히 한 명이고,
    # 그 사람은 아직 미나다. 미나의 `superseded_at` 은 지호가 **수락한 뒤에야** 찍힌다.
    assert rows[0].superseded_at is None
    assert len([row for row in rows if row.status == "active"]) == 1

    # 답이 오기 전까지 미나가 계속 들고 있고, 원장이 누가 왜 옮기자고 했는지 말한다.
    assert [row["task_id"] for row in client.get("/api/my-work", headers=MINA).json()] == [task_id]
    from ax_workspace.platform.persistence import ActivityEventRecord

    with make_session_factory(database_url)() as session:
        # v2: 「제안」과 「실제 교체」가 각각 남는다 (정책 L-9). 여기서는 아직 제안만 있다.
        [event] = [
            row for row in session.scalars(select(ActivityEventRecord))
            if row.event_kind == "task.assignment.change_proposed"
        ]
    assert event.actor_id == "jiho" and event.reason == "제가 이어서 하겠습니다"
    assert event.safe_summary == "지호가 담당자를 민아에서 지호로 바꾸자고 제안함: 옮겨 다닐 업무"
    assert event.before_ref == f"task_assignment:{rows[0].id}" and event.after_ref == f"task_assignment:{rows[1].id}"


def test_only_someone_who_may_assign_can_change_the_holder(tmp_path) -> None:
    client, application, database_url = _stack(tmp_path)
    jiho = application.authenticated_principal("jiho")
    task_id = application.assign_task(jiho, "권한 확인 업무", "mina", idempotency_key="actor-assign-3")["task"]["task_id"]
    # 배정은 수락을 기다리지 않으므로 민아가 이미 들고 있다. 회차는 행에서 직접 읽는다.
    current = {"version": _task_row(database_url, task_id).version}

    # 담당자도 자기 일을 남에게 넘기지 못하고 무관한 사람은 손도 대지 못한다 — 둘 다 **배정 역량이
    # 없어서** 막히므로 403이다. 담당자 변경은 `task.assign` 을 요구하는 별도 명령이다.
    for headers in (MINA, {"X-Demo-Persona": "sora"}):
        refused = client.post(
            f"/api/tasks/{task_id}/reassign", headers=headers,
            json={"expected_version": current["version"], "assignee_id": "jiho"},
        )
        assert refused.status_code == 403 and "task.assign" in refused.text
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
    assert [row.status for row in rows] == ["active"] and rows[0].assignee_id == "mina"


def test_the_relationship_table_is_a_projection_that_can_be_rebuilt(tmp_path) -> None:
    """`ResourceRelationship` says who may reach a resource. It never decides who sent or holds the work."""
    client, application, database_url = _stack(tmp_path)
    from ax_workspace.platform.persistence import ResourceRelationshipRecord
    from ax_workspace.platform.work_tasks import SqlAlchemyWorkRequestRepository

    request = client.post(
        "/api/work-requests",
        headers=MINA,
        json={"title": "관계가 붙는 요청", "assignee_id": "jiho", "cc_member_ids": ["yuna"]},
    ).json()
    assigned = application.assign_task(application.authenticated_principal("jiho"), "배정한 업무", "mina", idempotency_key="actor-assign-4")

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
    assert ("yuna", "work_request", request["request_id"], "cc") in before

    # Throw away everything this table derives from the canonical rows, and rebuild it: it comes back the same.
    # `cc` is not derived — being copied in is itself an access relationship, and this table is where it lives.
    with make_session_factory(database_url)() as session:
        for row in session.scalars(select(ResourceRelationshipRecord)):
            if row.relationship_kind in {"requester", "assignee"}:
                session.delete(row)
        session.flush()
        assert relationships(session) == {("yuna", "work_request", request["request_id"], "cc")}
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
    assert client.get(f"/api/work-requests/{request['request_id']}/timeline", headers={"X-Demo-Persona": "yuna"}).status_code == 200
