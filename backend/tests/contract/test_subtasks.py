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
    # v2: 하위 진행이 넷으로 갈린다 — `blocking` 이 0이어야 상위를 끝낼 수 있다 (SPEC-003 §4 Data).
    assert parent_view["child_progress"] == {"done": 0, "blocking": 1, "cancelled": 0, "total": 1}
    assert parent_view["parent"] is None

    with make_session_factory(database_url)() as session:
        assert session.get(TaskRecord, UUID(child_id)).parent_task_id == UUID(parent)


def test_one_level_only_and_never_into_work_that_is_over(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    parent = _task(client, "부모 업무")
    child = _task(client, "자식 업무", parent_task_id=parent)

    # v2: **저장 깊이에는 제한이 없다** (정책 V-6). 막히는 것은 깊이가 아니라 「같은 담당자의 직접 작업
    # 아래 직접 작업」이다 — 손자는 그 경우라 `WORK_DIRECT_NESTING`(409) 이다. 다른 사람이 수락한
    # 요청 업무 아래에는 하위를 만들 수 있다(§ 중심 업무 판정).
    assert client.post("/api/tasks", headers=MINA, json={"title": "손자", "parent_task_id": child}).status_code == 409
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
    client.post(
        f"/api/tasks/{closed}/cancel", headers=MINA,
        json={"expected_version": current["version"], "reason": "필요 없어졌습니다"},
    )
    refused = client.post("/api/tasks", headers=MINA, json={"title": "뒤늦은 자식", "parent_task_id": closed})
    # v2: `WORK_PARENT_CLOSED` 는 409 다 — 입력이 틀린 것이 아니라 그 업무가 이미 끝났다.
    assert refused.status_code == 409


def test_a_parent_is_not_finished_while_its_work_is_not(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    parent = _task(client, "마감 업무")
    child = _task(client, "남은 하위 업무", parent_task_id=parent)
    current = client.get(f"/api/tasks/{parent}", headers=MINA).json()
    client.post(f"/api/tasks/{parent}/start", headers=MINA, json={"expected_version": current["version"]})
    running = client.get(f"/api/tasks/{parent}", headers=MINA).json()

    refused = client.post(f"/api/tasks/{parent}/complete", headers=MINA, json={"expected_version": running["version"]})
    # v2: `WORK_CHILDREN_UNFINISHED` 는 409 다 — 입력이 틀린 것이 아니라 지금 상태가 받지 않는다.
    assert refused.status_code == 409
    # It says which work is still open rather than just refusing.
    assert "남은 하위 업무" in refused.text

    # Finishing the child does not finish the parent; it only makes finishing it possible.
    child_view = client.get(f"/api/tasks/{child}", headers=MINA).json()
    client.post(f"/api/tasks/{child}/start", headers=MINA, json={"expected_version": child_view["version"]})
    child_view = client.get(f"/api/tasks/{child}", headers=MINA).json()
    client.post(f"/api/tasks/{child}/complete", headers=MINA, json={"expected_version": child_view["version"]})
    assert client.get(f"/api/tasks/{parent}", headers=MINA).json()["state"] == "in_progress"
    assert client.get(f"/api/tasks/{parent}", headers=MINA).json()["child_progress"] == {
        "done": 1, "blocking": 0, "cancelled": 0, "total": 1
    }

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
    assigned = application.assign_task(jiho, "지호가 만든 부모", "mina", idempotency_key="subtask-parent")
    # The manager assigns a child of their own parent to someone else.
    child = application.assign_task(jiho, "남에게 맡긴 하위 업무", "mina", idempotency_key="subtask-child", parent_task_id=UUID(assigned["task"]["task_id"]))
    # 배정도 수락을 기다리지 않는다 — 명령이 성공하면 상대의 업무 목록에 이미 서 있다.

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


def test_a_direct_cancellation_needs_a_reason_and_keeps_it(tmp_path) -> None:
    """**왜 접었는지가 남아야 한다** (SPEC-003 §4 API·Validation · SPEC-001 계승).

    사유 없이 사라진 업무는 남은 사람에게 「왜 없어졌는지」가 아무 데도 없는 일이 된다. 그래서 취소는
    사유를 요구하고, 그 사유가 **진행 기록에 그대로 실린다** — 입력에서만 통과하고 로그에서 사라지면
    요구한 적이 없는 것과 같다.
    """
    client, _, database_url = _stack(tmp_path)
    task_id = _task(client, "접을 업무")
    current = client.get(f"/api/tasks/{task_id}", headers=MINA).json()

    # 사유가 없거나 공백뿐이면 서지 않는다.
    for body in ({"expected_version": current["version"]}, {"expected_version": current["version"], "reason": "   "}):
        refused = client.post(f"/api/tasks/{task_id}/cancel", headers=MINA, json=body)
        assert refused.status_code == 422, refused.text

    cancelled = client.post(
        f"/api/tasks/{task_id}/cancel", headers=MINA,
        json={"expected_version": current["version"], "reason": "  분기 계획에서 빠졌습니다  "},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["state"] == "cancelled" and cancelled.json()["cancel_reason"] == "direct"

    # 그 사유가 이력에 있다 — 앞뒤 공백은 다듬어져 저장된다.
    from ax_workspace.platform.persistence import ActivityEventRecord, make_session_factory
    from sqlalchemy import select

    with make_session_factory(database_url)() as session:
        reasons = [
            row.reason
            for row in session.scalars(select(ActivityEventRecord))
            if row.event_kind == "task.state_changed" and row.target_id == task_id
        ]
    assert reasons == ["분기 계획에서 빠졌습니다"]
