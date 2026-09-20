"""W1 — 한 번의 명시적 생성 명령 (SPEC-001 §6 W1 인수조건 · WORK-001 Phase 1~6).

여기서 닫는 것은 넷이다 — **경로별 권한**, **즉시 활성 담당**, **필수 멱등 키**, **수락 gate 부재**.
표면은 화면이 쓰는 REST 와 AX 가 쓰는 MCP 둘 다 본다. 둘이 다른 규칙을 가지면 한쪽에서만 gate 가 살아남는다.
"""
import asyncio
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ax_workspace.bootstrap.application import create_workflow_application
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from ax_workspace.modules.meetings.commands import MeetingTodoPromotionInput
from ax_workspace.entrypoints.reset_demo import reset_database
from legacy_acceptance import pending_request
from ax_workspace.modules.work.creation import (
    TaskCreationContext,
    decide_task_creation,
    require_idempotency_key,
)
from ax_workspace.modules.work.errors import TaskError, TaskIdempotencyKeyRequired
from ax_workspace.modules.organization_access.domain import (
    TASK_ASSIGN,
    TASK_SELF_MANAGE,
    WORK_REQUEST_CREATE,
)
from ax_workspace.platform.persistence import (
    DecisionItemRecord,
    WorkRequestRecord,
    ReviewAssignmentRecord,
    SubmissionRecord,
    TaskAssignmentRecord,
    TaskCreationAttemptRecord,
    TaskRecord,
    make_session_factory,
)

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
YUNA = {"X-Demo-Persona": "yuna"}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url)
    return TestClient(create_app(settings)), settings, database_url


def _create(client, headers, key, **body):
    return client.post("/api/tasks", headers={**headers, "Idempotency-Key": key}, json=body)


# ---- Phase 1 — 결정 함수 -------------------------------------------------------


def test_the_one_decision_names_the_route_and_the_checks_each_path_still_has() -> None:
    """세 경로가 같은 모양으로 들어오고 각자의 검사를 들고 나온다. `self` 도 역량 검사를 지난다."""
    own = decide_task_creation(TaskCreationContext(actor_id="mina", assignee_id=None))
    assert (own.route, own.assignee_id, own.required_capability, own.recipient_check) == (
        "self", "mina", TASK_SELF_MANAGE, "none",
    )
    # 본인을 수신자로 적은 것은 본인 업무다 — 같은 명령의 인자 차이일 뿐이다.
    assert decide_task_creation(TaskCreationContext(actor_id="mina", assignee_id="mina")).route == "self"
    assert decide_task_creation(TaskCreationContext(actor_id="mina", assignee_id="  ")).route == "self"

    horizontal = decide_task_creation(TaskCreationContext(actor_id="mina", assignee_id="jiho"))
    assert (horizontal.route, horizontal.required_capability, horizontal.recipient_check) == (
        "horizontal", WORK_REQUEST_CREATE, "work_request_candidate",
    )

    managed = decide_task_creation(TaskCreationContext(actor_id="jiho", assignee_id="mina", managed=True))
    assert (managed.route, managed.required_capability, managed.recipient_check) == (
        "managed", TASK_ASSIGN, "assignment_scope",
    )
    with pytest.raises(TaskError):
        decide_task_creation(TaskCreationContext(actor_id="jiho", assignee_id="jiho", managed=True))


def test_a_creation_key_is_required_and_never_invented_by_the_server() -> None:
    assert require_idempotency_key("  abc ") == "abc"
    for absent in (None, "", "   "):
        with pytest.raises(TaskIdempotencyKeyRequired):
            require_idempotency_key(absent)
    with pytest.raises(TaskIdempotencyKeyRequired):
        require_idempotency_key("x" * 201)


def test_the_creation_body_takes_the_recipient_and_still_refuses_what_it_never_took(tmp_path) -> None:
    """수신자는 본문으로 열리고, **키는 헤더**이며 모르는 필드는 그대로 거부된다.

    **승인자 한 줄만 뜻이 바뀌었다.** W1 은 열만 만들고 값을 받지 않았는데, SPEC-001 §4 가
    2026-09-19 부터 `업무` 갈래의 `approver_id` 를 **받아 저장하는 계약**으로 내렸다(WORK-003 Phase 3).
    그래서 이 줄은 「거부된다」가 아니라 **「받아서 저장한다」**로 선다 — 계약이 바뀐 자리이지
    검사를 놓은 자리가 아니다. 요청 갈래는 여전히 열지 않고, 그 gate 는
    `test_common_work_payload.py` 가 갖는다 (§7 OQ-M).
    """
    client, _, _ = _stack(tmp_path)
    assert _create(client, MINA, "body-1", title="본문 키는 거부", idempotency_key="k").status_code == 422
    approved = _create(client, MINA, "body-2", title="결재자가 있는 업무", approver_id="jiho")
    assert approved.status_code == 201, approved.text
    assert approved.json()["approver_id"] == "jiho"
    assert _create(client, MINA, "body-3", title="모르는 필드", nonsense=1).status_code == 422
    # 기존 규칙은 그대로 산다.
    assert _create(client, MINA, "body-4", title="x" * 301).status_code == 422
    assert _create(
        client, MINA, "body-5", title="일정", start_date="2026-09-30", due_date="2026-09-10"
    ).status_code == 422


# ---- Phase 2 — 멱등성 · 원자성 · 활성 담당 유일성 -------------------------------


@pytest.mark.no_auto_idempotency_key
def test_every_creation_surface_refuses_a_missing_or_blank_key(tmp_path) -> None:
    client, settings, database_url = _stack(tmp_path)
    for headers in ({**MINA}, {**MINA, "Idempotency-Key": "   "}):
        assert client.post("/api/tasks", headers=headers, json={"title": "키 없는 생성"}).status_code == 422
        assert client.post(
            "/api/work-requests", headers=headers, json={"title": "키 없는 요청", "assignee_id": "jiho"}
        ).status_code == 422
    assert client.post(
        "/api/tasks/assign", headers={**JIHO, "Idempotency-Key": ""}, json={"title": "키 없는 배정", "assignee_id": "mina"}
    ).status_code == 422

    facade = McpReportsFacade(settings, "mina")
    lead = McpReportsFacade(settings, "jiho")
    for absent in (None, "", "   "):
        with pytest.raises(TaskIdempotencyKeyRequired):
            facade.create_self_task("키 없는 도구 생성", absent)
        with pytest.raises(TaskIdempotencyKeyRequired):
            facade.create_work_request("키 없는 도구 요청", "jiho", absent)
        with pytest.raises(TaskIdempotencyKeyRequired):
            lead.assign_task("키 없는 도구 배정", "mina", absent)

    with make_session_factory(database_url)() as session:
        assert session.scalars(select(TaskRecord)).all() == []
        assert session.scalars(select(TaskCreationAttemptRecord)).all() == []


@pytest.mark.no_auto_idempotency_key
def test_the_manager_assignment_and_meeting_promotion_surfaces_also_refuse_a_missing_key(tmp_path) -> None:
    """키 요구는 **생성 표면 넷 전부**에 걸린다 — 헤더 누락도 공백도 같은 거절이다.

    회의 승격은 `Idempotency-Key` 를 받는 라우트가 하나 더 있는 자리라 따로 본다: 키가 없으면
    승격 자체가 서지 않아야 하고, 후보가 이미 업무로 갔다고 표시되어서도 안 된다.
    """
    from test_meeting_finalize import _finalized, _the_final
    from test_meeting_finalize import _stack as _meeting_stack

    client, application, agent = _meeting_stack(tmp_path)
    meeting_id, _ = _finalized(client, application, agent)
    [todo] = _the_final(client, meeting_id, MINA)["todos"]
    promote = f"/api/meetings/{meeting_id}/todos/{todo['todo_id']}/promote"

    for headers in ({**JIHO}, {**JIHO, "Idempotency-Key": ""}, {**JIHO, "Idempotency-Key": "   "}):
        refused = client.post(
            "/api/tasks/assign", headers=headers, json={"title": "키 없는 배정", "assignee_id": "mina"}
        )
        assert refused.status_code == 422, refused.text
    for headers in ({**MINA}, {**MINA, "Idempotency-Key": ""}, {**MINA, "Idempotency-Key": "   "}):
        refused = client.post(promote, headers=headers, json={"assignee_id": "jiho"})
        assert refused.status_code == 422, refused.text

    # 거절이 아무것도 남기지 않는다 — 업무·원장·요청 0건이고 후보는 아직 승격되지 않았다.
    with application._session_factory() as session:
        assert session.scalars(select(TaskRecord)).all() == []
        assert session.scalars(select(TaskCreationAttemptRecord)).all() == []
        assert session.scalars(select(WorkRequestRecord)).all() == []
    [still_there] = _the_final(client, meeting_id, MINA)["todos"]
    assert still_there["linked"] is None or still_there["linked"].get("work_request_id") is None

    # MCP 도구도 같은 거절이다 — 서버가 대신 키를 만들어 채우지 않는다.
    settings = application._settings
    with pytest.raises(TaskIdempotencyKeyRequired):
        McpReportsFacade(settings, "mina").promote_current_meeting_todo(
            meeting_id, todo["todo_id"], MeetingTodoPromotionInput(assignee_id="jiho"), ""
        )


def test_the_same_key_is_a_receipt_a_different_payload_is_a_conflict_and_a_new_key_is_a_new_task(tmp_path) -> None:
    client, _, database_url = _stack(tmp_path)
    first = _create(client, MINA, "intent-1", title="분기 마감 정리", due_date="2026-09-30")
    assert first.status_code == 201

    replay = _create(client, MINA, "intent-1", title="분기 마감 정리", due_date="2026-09-30")
    assert replay.status_code == 201
    # 같은 업무의 같은 투영이 그대로 돌아온다. SQLite 는 시각에 시간대를 저장하지 않아 방금 만든 값과
    # 다시 읽은 값의 문자열이 접미사만 다르다 — 전체 일치는 PostgreSQL 통합 테스트가 본다.
    stamps = {"created_at", "updated_at"}
    assert {k: v for k, v in replay.json().items() if k not in stamps} == {
        k: v for k, v in first.json().items() if k not in stamps
    }

    conflict = _create(client, MINA, "intent-1", title="다른 내용", due_date="2026-09-30")
    assert conflict.status_code == 409 and "다른 내용" in conflict.json()["detail"]

    # 다른 키에 같은 내용인 두 명령은 **둘 다** 선다 — 내용이 같다고 합치지 않는다.
    twin = _create(client, MINA, "intent-2", title="분기 마감 정리", due_date="2026-09-30")
    assert twin.status_code == 201 and twin.json()["task_id"] != first.json()["task_id"]

    with make_session_factory(database_url)() as session:
        assert len(session.scalars(select(TaskRecord)).all()) == 2
        attempts = session.scalars(select(TaskCreationAttemptRecord)).all()
        assert {row.request_key for row in attempts} == {"intent-1", "intent-2"}
        assert all(row.actor_id == "mina" and row.command_kind == "task.create" for row in attempts)


def test_one_key_belongs_to_one_actor_and_one_command(tmp_path) -> None:
    """전역 키가 아니다 — 남의 키로 남의 업무를 영수증으로 받아 가지 못한다."""
    client, _, _ = _stack(tmp_path)
    mine = _create(client, MINA, "shared-key", title="민아의 업무").json()
    theirs = _create(client, JIHO, "shared-key", title="지호의 업무").json()
    assert mine["task_id"] != theirs["task_id"] and theirs["title"] == "지호의 업무"
    # 같은 행위자라도 명령 종류가 다르면 섞이지 않는다.
    assigned = client.post(
        "/api/tasks/assign",
        headers={**JIHO, "Idempotency-Key": "shared-key"},
        json={"title": "배정한 업무", "assignee_id": "mina"},
    )
    assert assigned.status_code == 201 and assigned.json()["task"]["task_id"] != theirs["task_id"]


def test_a_receipt_is_withheld_from_a_caller_who_may_no_longer_read_it(tmp_path) -> None:
    """영수증을 돌려주기 **전에** 지금 읽을 수 있는지 다시 묻는다. 잃었으면 존재를 숨긴다."""
    client, _, database_url = _stack(tmp_path)
    sent = _create(client, MINA, "handover", title="넘긴 업무", assignee_id="jiho")
    assert sent.status_code == 201
    task_id = sent.json()["task_id"]

    # 요청자 관계를 걷으면 민아는 이 업무의 관계자가 아니다.
    from ax_workspace.platform.persistence import ResourceRelationshipRecord, WorkRequestRecord

    with make_session_factory(database_url)() as session:
        request = session.scalar(select(WorkRequestRecord))
        request.requester_id = "yuna"
        for row in session.scalars(
            select(ResourceRelationshipRecord).where(
                ResourceRelationshipRecord.resource_id == str(request.id),
                ResourceRelationshipRecord.member_id == "mina",
            )
        ):
            session.delete(row)
        session.commit()

    hidden = _create(client, MINA, "handover", title="넘긴 업무", assignee_id="jiho")
    assert hidden.status_code == 404
    assert client.get(f"/api/tasks/{task_id}", headers=MINA).status_code == 404


def test_the_task_and_its_active_assignment_stand_in_one_transaction(tmp_path) -> None:
    """생성이 실패하면 업무만 남거나 담당만 남지 않는다 — 원장 한 줄도 남지 않는다."""
    client, _, database_url = _stack(tmp_path)
    # 읽을 수 없는 상위 업무를 가리키면 생성은 거절된다.
    other = _create(client, JIHO, "other-1", title="지호의 업무").json()["task_id"]
    refused = _create(client, MINA, "atomic-1", title="하위로 붙이려는 업무", parent_task_id=other)
    assert refused.status_code == 404
    with make_session_factory(database_url)() as session:
        assert [row.title for row in session.scalars(select(TaskRecord)).all()] == ["지호의 업무"]
        assert [row.request_key for row in session.scalars(select(TaskCreationAttemptRecord)).all()] == ["other-1"]


def test_the_active_assignment_uniqueness_is_a_database_constraint() -> None:
    """application 검사로 낮추지 않는다 — 모델 metadata 가 부분 unique 를 들고 있다."""
    [index] = [
        row for row in TaskAssignmentRecord.__table__.indexes if row.name == "uq_task_assignments_active"
    ]
    assert index.unique is True
    assert [column.name for column in index.columns] == ["task_id"]
    assert "status = 'active'" in str(index.dialect_options["postgresql"]["where"])


# ---- Phase 3 — 권한 경계 -------------------------------------------------------


def test_a_member_with_neither_assign_nor_decide_may_send_work_to_a_peer(tmp_path) -> None:
    """#7 W1.5 — 배정 역량도 판단 역량도 없는 구성원이 한 번의 명령으로 동료에게 보낸다."""
    client, _, _ = _stack(tmp_path)
    application = client.app.state.workflow_application
    mina = application.authenticated_principal("mina")
    # 배정 역량은 없다. **판단 역량은 v2 에서 생겼다** — 자기에게 온 요청에 답하려면 필요하고,
    # 「남의 요청을 판단한다」가 아니다: 수신자 검사가 그 위에 따로 선다 (SPEC-003 §5 권한).
    assert "task.assign" not in mina.capabilities and "work_request.decide" in mina.capabilities

    candidates = client.get("/api/work-request-assignee-candidates", headers=MINA)
    assert candidates.status_code == 200
    # 판단 역량이 없는 동료도 후보에 뜬다 — 목록과 판정이 같은 함수를 지난다.
    assert "minseok" in {row["id"] for row in candidates.json()}
    assert "mina" not in {row["id"] for row in candidates.json()}

    sent = _create(client, MINA, "peer-1", title="동료에게 보낸 업무", assignee_id="minseok")
    assert sent.status_code == 201
    minseok = {"X-Demo-Persona": "minseok"}
    # v2: 받는 사람이 **수락해야** 자기 업무가 된다 — 자기에게 온 요청이므로 자기가 답한다.
    answered = client.post(
        f"/api/work-requests/{sent.json()['lineage']['source_work_request_id']}/accept",
        headers=minseok, json={"expected_version": 1},
    )
    assert answered.status_code == 200, answered.text
    [mine] = client.get("/api/my-work", headers=minseok).json()
    assert mine["task_id"] == sent.json()["task_id"] and mine["state"] == "open"
    # 수락이 회차를 하나 올렸으므로 시작은 지금 회차로 부른다.
    assert client.post(
        f"/api/tasks/{mine['task_id']}/start", headers=minseok, json={"expected_version": mine["version"]}
    ).status_code == 200
    # 배정 권한은 늘어나지 않았다.
    assert client.get("/api/task-assignment-candidates", headers=MINA).status_code == 403
    assert client.post(
        "/api/tasks/assign", headers={**MINA, "Idempotency-Key": "peer-2"},
        json={"title": "배정 시도", "assignee_id": "minseok"},
    ).status_code == 403


def test_the_candidate_list_and_the_command_answer_the_same_question(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    allowed = {row["id"] for row in client.get("/api/work-request-assignee-candidates", headers=MINA).json()}
    for index, member_id in enumerate(sorted(allowed)):
        assert _create(client, MINA, f"listed-{index}", title=f"{member_id}에게", assignee_id=member_id).status_code == 201
    refused = _create(client, MINA, "unlisted", title="명부에 없는 사람", assignee_id="nobody-by-that-name")
    assert refused.status_code == 403 and "보낼 수 없습니다" in refused.json()["detail"]


def test_sending_work_never_widens_what_a_person_may_change(tmp_path) -> None:
    """신규 요청 권한이 타인의 **기존 업무**를 수정·재배정하는 권한이 되지 않는다."""
    client, _, _ = _stack(tmp_path)
    theirs = _create(client, JIHO, "theirs", title="지호가 들고 있는 일").json()["task_id"]
    assert client.patch(
        f"/api/tasks/{theirs}", headers=MINA, json={"expected_version": 1, "title": "가로챈 제목"}
    ).status_code == 404
    assert client.post(
        f"/api/tasks/{theirs}/reassign", headers=MINA, json={"expected_version": 1, "assignee_id": "mina"}
    ).status_code == 403


def test_manager_assignment_keeps_its_own_capability_and_organization_scope(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    # 배정 역량이 없으면 403, 있어도 조직 범위 밖이면 422 — 두 거절이 서로 다르다.
    assert client.post(
        "/api/tasks/assign", headers={**MINA, "Idempotency-Key": "scope-1"},
        json={"title": "x", "assignee_id": "jiho"},
    ).status_code == 403
    out_of_scope = client.post(
        "/api/tasks/assign", headers={**JIHO, "Idempotency-Key": "scope-2"},
        json={"title": "x", "assignee_id": "sora"},
    )
    assert out_of_scope.status_code == 422 and "assignment scope" in out_of_scope.text


def test_past_pending_rows_still_need_the_judgement_capability(tmp_path) -> None:
    """후보 판정을 고쳐도 판단 명령의 권한은 그대로다 — 수락·거절·조정은 여전히 `work_request.decide` 다."""
    client, _, database_url = _stack(tmp_path)
    request = client.post(
        "/api/work-requests", headers={**MINA, "Idempotency-Key": "past-1"},
        json={"title": "과거 모양으로 되돌린 요청", "assignee_id": "jiho"},
    ).json()
    from ax_workspace.platform.persistence import WorkRequestRecord

    with make_session_factory(database_url)() as session:
        row = session.scalar(select(WorkRequestRecord))
        row.state = "pending"
        session.commit()
    # **보낸 사람은 자기 요청을 스스로 수락할 수 없다.** 역량이 아니라 「그 요청의 수신자만 답한다」가
    # 막는 것이고, 그래서 거절 사유가 역량 이름이 아니라 그 규칙을 말한다 (SPEC-003 §5 권한).
    denied = client.post(
        f"/api/work-requests/{request['request_id']}/accept", headers=MINA, json={"expected_version": 1}
    )
    assert denied.status_code == 403 and "받는 사람" in denied.text


# ---- Phase 4 — 수락 gate 부재와 출처 보존 --------------------------------------


def test_no_creation_path_opens_an_acceptance_round(tmp_path) -> None:
    client, _, database_url = _stack(tmp_path)
    _create(client, MINA, "gate-1", title="본인 생성")
    _create(client, MINA, "gate-2", title="구성원 타인 생성", assignee_id="jiho")
    client.post(
        "/api/tasks/assign", headers={**YUNA, "Idempotency-Key": "gate-3"},
        json={"title": "관리자 배정", "assignee_id": "mina"},
    )
    # **배정 축에는 회차가 없다** — 관리자 배정도 본인 생성도 사람이 답할 것이 없다.
    # 수평 요청은 다르다: 받는 사람이 답해야 하므로 그 요청 하나에 질문 하나가 선다 (SPEC-003 §4 발송).
    for headers in (MINA, JIHO, YUNA):
        kinds = {row["kind"] for row in client.get("/api/action-items", headers=headers).json()}
        assert "task.assignment" not in kinds
    assert {row["kind"] for row in client.get("/api/action-items", headers=JIHO).json()} == {"work_request.acceptance"}
    with make_session_factory(database_url)() as session:
        # 있는 회차는 **그 수평 요청 하나**뿐이다.
        assert [row.kind for row in session.scalars(select(DecisionItemRecord))] == ["work_request.acceptance"]
        assert len(session.scalars(select(SubmissionRecord)).all()) == 1
        assert [row.reviewer_member_id for row in session.scalars(select(ReviewAssignmentRecord))] == ["jiho"]
        # 배정·본인 생성은 즉시 `active`, 수평 요청은 `pending` — 셋이 한 DB 에 함께 산다.
        assert {row.status for row in session.scalars(select(TaskAssignmentRecord)).all()} == {"active", "pending"}


def test_a_new_request_stands_as_assigned_and_takes_no_round_command(tmp_path) -> None:
    """`assigned` 는 판단 없이 업무와 활성 담당이 섰다는 사실만 말한다 (DEC-001 D-3c).

    그 상태에는 수락·거절·조정·재상신·거두기·근거가 걸리지 않고, 걸어도 side effect 가 없다.
    """
    client, _, database_url = _stack(tmp_path)
    request = client.post(
        "/api/work-requests", headers={**MINA, "Idempotency-Key": "assigned-1"},
        json={"title": "즉시 배정된 요청", "assignee_id": "jiho"},
    ).json()
    # v2: 발송은 업무를 세우고 담당은 수락이 세운다 — 요청은 `pending`, 담당 관계도 `pending` 이다.
    assert request["state"] == "pending" and request["assignment_state"] == "pending"
    rid = request["request_id"]

    # v2: 받는 사람은 **답할 수 있다.** 협의 전에는 재상신할 것이 없으므로 그것만 서지 않는다.
    refused = client.post(
        f"/api/work-requests/{rid}/resubmit", headers=MINA, json={"expected_version": 1, "title": "다시 올린 제목"}
    )
    assert refused.status_code in {403, 409, 422}, refused.text

    after = client.get(f"/api/work-requests/{rid}", headers=MINA).json()
    assert after["state"] == "pending" and after["version"] == 1
    with make_session_factory(database_url)() as session:
        from ax_workspace.platform.persistence import AttachmentRecord, WorkRequestAuditEventRecord

        assert session.scalars(select(AttachmentRecord)).all() == []
        assert {row.event_type for row in session.scalars(select(WorkRequestAuditEventRecord)).all()} == {
            "work_request.created"
        }
    # **받는 사람의 수신함에 선다** — 답을 기다리는 요청이 바로 그것이다 (SPEC-003 §4 발송).
    # W1 에서는 답할 것이 없어 수신함이 비어 있었다.
    application = client.app.state.workflow_application
    inbox = application.work_request_inbox(application.authenticated_principal("jiho"))
    assert [row["request_id"] for row in inbox] == [rid]
    # 같은 답이 REST 로도 나온다 — `/inbox` 가 `/{request_id}` 보다 먼저 맞춰져야 UUID 파싱에 안 걸린다.
    received = client.get("/api/work-requests/inbox", headers=JIHO)
    assert received.status_code == 200, received.text
    assert [row["request_id"] for row in received.json()] == [rid]
    # 그 줄이 자기가 세운 업무를 가리키므로 답하기 전에 무엇에 대한 요청인지 열어 볼 수 있다.
    assert received.json()[0]["task_id"] and received.json()[0]["state"] == "pending"
    # **보낸 사람의 수신함이 아니다** — 내가 보낸 요청은 `GET /api/work-requests` 쪽이다.
    assert client.get("/api/work-requests/inbox", headers=MINA).json() == []
    # 답하고 나면 수신함에서 빠진다.
    answered = client.post(
        f"/api/work-requests/{rid}/accept", headers=JIHO, json={"expected_version": 1}
    )
    assert answered.status_code == 200, answered.text
    assert client.get("/api/work-requests/inbox", headers=JIHO).json() == []


def test_the_request_origin_and_its_completion_reviewer_survive(tmp_path) -> None:
    """수락 gate 제거가 이력을 지우지 않는다 — 출처·행위자·시각이 남고 완료 승인이 요청자에게 간다."""
    client, _, _ = _stack(tmp_path)
    sent = _create(client, MINA, "origin-1", title="요청에서 난 업무", assignee_id="jiho").json()
    task_id = sent["task_id"]
    # 수락해야 담당이 서고, 그 뒤 시작·완료 보고가 열린다.
    accepted = client.post(
        f"/api/work-requests/{sent['lineage']['source_work_request_id']}/accept",
        headers=JIHO, json={"expected_version": 1},
    )
    assert accepted.status_code == 200, accepted.text
    detail = client.get(f"/api/tasks/{task_id}", headers=JIHO).json()
    assert detail["origin"]["kind"] == "work_request"
    assert detail["origin"]["actor"]["member_id"] == "mina" and detail["origin"]["actor_role"] == "요청자"
    assert detail["lineage"]["source_work_request_id"]
    # 생성 시점의 회차가 요청 출처와 담당을 함께 들고 있고, 요청 쪽 진행 기록이 행위자와 시각을 남긴다.
    [first_version, _accepted_version] = client.get(f"/api/tasks/{task_id}/history", headers=JIHO).json()["versions"]
    assert first_version["change_kind"] == "task.created"
    assert first_version["snapshot"]["source_work_request_id"] == detail["lineage"]["source_work_request_id"]
    assert first_version["snapshot"]["assignment"]["assignee_id"] == "jiho"
    timeline = client.get(
        f"/api/work-requests/{detail['lineage']['source_work_request_id']}/timeline", headers=MINA
    ).json()
    [created] = [row for row in timeline["activity"] if row["event_kind"] == "work_request.created"]
    assert created["actor_id"] == "mina" and "업무를 보냄" in created["safe_summary"] and created["occurred_at"]
    # 회차 하나에 **사람이 실제로 낸 답 하나**가 붙어 있다 — 위에서 지호가 수락했다.
    # 보장은 그대로다: 사람이 하지 않은 수락은 이력에 없다.
    assert timeline["decision_item"] is not None and len(timeline["submissions"]) == 1
    assert [row["reviewer_member_id"] for row in timeline["review_assignments"]] == ["jiho"]
    assert [(row["actor_member_id"], row["decision"]) for row in timeline["review_decisions"]] == [("jiho", "accept")]

    # 완료 보고가 열리고 확인자는 그 요청의 요청자다 — 본인 생성에는 그 회차가 없다.
    # 수락이 회차를 올렸으므로 지금 회차로 시작하고, 시작이 또 올린 회차로 보고한다.
    current = client.get(f"/api/tasks/{task_id}", headers=JIHO).json()
    started = client.post(f"/api/tasks/{task_id}/start", headers=JIHO, json={"expected_version": current["version"]})
    assert started.status_code == 200, started.text
    reported = client.post(
        f"/api/tasks/{task_id}/completion-report", headers=JIHO,
        json={"expected_version": started.json()["version"], "summary": "끝냈습니다"},
    )
    assert reported.status_code == 200, reported.text
    [review] = [row for row in client.get("/api/action-items", headers=MINA).json() if row["kind"] == "task.delivery"]
    assert review["subject"] == "요청에서 난 업무"

    own = _create(client, MINA, "origin-2", title="본인 업무").json()
    assert client.get(f"/api/tasks/{own['task_id']}", headers=MINA).json()["lineage"]["source_work_request_id"] is None


# ---- Phase 6 — 표면 정렬 -------------------------------------------------------


def test_rest_and_mcp_create_the_same_thing_under_the_same_key_rules(tmp_path) -> None:
    client, settings, database_url = _stack(tmp_path)
    facade = McpReportsFacade(settings, "mina")
    through_tool = facade.create_self_task("도구로 만든 업무", "tool-1", due_date="2026-09-30")
    replay = facade.create_self_task("도구로 만든 업무", "tool-1", due_date="2026-09-30")
    assert replay["task_id"] == through_tool["task_id"]
    # 같은 대화 turn·같은 도구라도 **다른 명시 키**는 두 건이다.
    twin = facade.create_self_task("도구로 만든 업무", "tool-2", due_date="2026-09-30")
    assert twin["task_id"] != through_tool["task_id"]

    through_rest = _create(client, MINA, "rest-1", title="REST 로 만든 업무", due_date="2026-09-30").json()
    assert {key: through_rest[key] for key in ("title", "state", "due_date")} == {
        "title": "REST 로 만든 업무", "state": "open", "due_date": "2026-09-30",
    }
    with make_session_factory(database_url)() as session:
        assert len(session.scalars(select(TaskRecord)).all()) == 3


def test_every_w1_tool_takes_the_key_as_an_explicit_argument(tmp_path) -> None:
    """서버가 호출 시점에 임의 키를 채우는 길이 없다 — 스키마가 키를 요구한다."""
    _, settings, _ = _stack(tmp_path)
    server = _create_bound_persona_server(McpReportsFacade(settings, "jiho"))
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    for name in ("task_create_self", "task_assign", "work_request_create", "meeting_todo_promote"):
        schema = tools[name].input_schema
        assert "idempotency_key" in schema["required"], name
        assert "같은 키" in schema["properties"]["idempotency_key"]["description"], name


def test_a_non_w1_mutation_keeps_its_own_idempotency_meaning(tmp_path, monkeypatch) -> None:
    """공용 `_mutation_key` 를 일괄로 바꾸지 않았다 — 일일보고 초안·회의 예약이 그대로 쓴다."""
    import inspect

    from ax_workspace.entrypoints import mcp as mcp_module

    source = inspect.getsource(mcp_module.McpReportsFacade)
    assert "_mutation_key('daily_report.generate_draft'" in source or '_mutation_key("daily_report.generate_draft"' in source
    assert '_mutation_key("meeting.reservation.create"' in source
    # W1 생성 도구는 그 helper 를 더는 쓰지 않는다.
    for operation in ("task.create_self", "work_request.create"):
        assert f"_mutation_key('{operation}'" not in source and f'_mutation_key("{operation}"' not in source


def test_seed_data_carries_no_pending_assignment_or_acceptance_round(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    application = create_workflow_application(Settings(RuntimeProfile.TEST, database_url))
    mina = application.authenticated_principal("mina")
    application.create_task(mina, "seed 확인용", idempotency_key="seed-1", assignee_id="jiho")
    with make_session_factory(database_url)() as session:
        # v2: 수평 요청은 **답을 기다리는 회차 하나**와 **대기 담당 하나**를 세운다.
        assert [row.reviewer_member_id for row in session.scalars(select(ReviewAssignmentRecord))] == ["jiho"]
        assert [row.status for row in session.scalars(select(TaskAssignmentRecord)).all()] == ["pending"]
        # 회차는 **요청 수락 하나**다 — 배정 수락 회차를 신규 생성이 만들지 않는다는 W1 보장은 그대로다.
        assert [row.kind for row in session.scalars(select(DecisionItemRecord))] == ["work_request.acceptance"]


def test_an_ax_proposal_creates_nothing_until_a_person_confirms_it(tmp_path, monkeypatch) -> None:
    """읽기·제안만으로는 업무가 생기지 않는다. 확인된 action 하나가 생성 의도 하나다."""
    from ax_workspace.platform.persistence import ConversationTurnRecord

    client, settings, database_url = _stack(tmp_path)
    application = client.app.state.workflow_application
    principal = application.authenticated_principal("mina")
    conversation = application.create_conversation(principal, "AX 확인")
    application.accept_conversation_message(
        principal, "업무 하나 만들어줘", UUID(conversation["conversation_id"]), [], "ax-message-1"
    )
    with make_session_factory(database_url)() as session:
        execution_id = str(session.scalar(select(ConversationTurnRecord)).execution_id)
    monkeypatch.setenv("AX_MCP_CAUSATION_ID", execution_id)
    proposed = McpReportsFacade(settings, "mina").create_self_task(
        "AX가 제안한 업무", "ax-intent-1", due_date="2026-09-30"
    )
    monkeypatch.delenv("AX_MCP_CAUSATION_ID")

    assert proposed["state"] == "pending"
    with make_session_factory(database_url)() as session:
        assert session.scalars(select(TaskRecord)).all() == []
        assert session.scalars(select(TaskCreationAttemptRecord)).all() == []

    [card] = [row for row in client.get("/api/actions", headers=MINA).json() if row["action_id"] == proposed["action_id"]]
    approved = client.post(
        f"/api/actions/{proposed['action_id']}/decide", headers=MINA,
        json={"decision": "approve", "expected_version": card["version"]},
    )
    assert approved.status_code == 200, approved.text
    with make_session_factory(database_url)() as session:
        [task] = session.scalars(select(TaskRecord)).all()
        assert task.title == "AX가 제안한 업무"
        # 확정된 action 의 id 가 새 키 자리에 그대로 실린다 — 재실행이 두 건이 되지 않는다.
        [attempt] = session.scalars(select(TaskCreationAttemptRecord)).all()
        assert attempt.request_key == proposed["action_id"] and attempt.task_id == task.id


# ---- F-1 · W-1 — 담당을 지정한 생성이 무엇을 거절하고 무엇을 데리고 가는가 ------


@pytest.mark.parametrize(
    "field, value",
    [("start_date", "2026-09-10"), ("parent_task_id", None), ("project_id", None)],
)
def test_a_recipient_creation_refuses_each_field_it_cannot_carry_and_leaves_nothing_behind(
    tmp_path, field, value
) -> None:
    """남에게 보내는 생성이 받지 못하는 값은 **조용히 버리지 않고 하나씩 거절한다** (WORK-001 Phase 1).

    `work_requests` 에 시작일·상위·프로젝트 열이 없어 요청에서 업무로 옮길 자리가 없다. 받아 두고 버리면
    화면이 보낸 값이 사라진 것을 아무도 모르므로, 각 필드가 **자기 이름으로** 422 를 받는다.
    거절이 업무도 원장도 출처 행도 남기지 않는다는 것이 이 시험의 두 번째 절반이다.
    """
    client, _, database_url = _stack(tmp_path)
    if field == "parent_task_id":
        value = _create(client, MINA, "parent-of", title="상위가 될 내 업무").json()["task_id"]
    elif field == "project_id":
        # 민아를 붙여 **읽을 수 있는** 프로젝트로 만든다 — 거절 사유가 「읽을 수 없는 프로젝트」가 아니라
        # **담당을 지정한 갈래가 받지 않는 값**임을 분리하기 위해서다.
        created = client.post("/api/projects", headers=JIHO, json={"name": "보낼 수 없는 프로젝트"})
        assert created.status_code == 201, created.text
        value = created.json()["project_id"]
        joined = client.post(f"/api/projects/{value}/members", headers=JIHO, json={"member_id": "mina"})
        assert joined.status_code == 201, joined.text

    refused = _create(client, MINA, f"refuse-{field}", title="남에게 보내는 업무", assignee_id="jiho", **{field: value})
    assert refused.status_code == 422, refused.text
    assert field in refused.json()["detail"] and "쓸 수 없는 항목" in refused.json()["detail"]

    # 아무것도 서지 않았다 — 업무도, 멱등 원장도, 출처가 될 요청 행도.
    with make_session_factory(database_url)() as session:
        titles = {row.title for row in session.scalars(select(TaskRecord)).all()}
        assert "남에게 보내는 업무" not in titles
        keys = {row.request_key for row in session.scalars(select(TaskCreationAttemptRecord)).all()}
        assert f"refuse-{field}" not in keys
        assert session.scalars(select(WorkRequestRecord)).all() == []
    assert client.get("/api/my-work", headers=JIHO).json() == []
    # 같은 값을 본인 업무로 만드는 길은 그대로다 — 거절은 담당을 지정한 갈래의 계약이다.
    assert _create(client, MINA, f"self-{field}", title="내 업무로는 된다", **{field: value}).status_code == 201


def test_an_ax_confirmation_that_names_a_recipient_keeps_the_action_it_came_from(tmp_path, monkeypatch) -> None:
    """사람이 초안에서 담당을 골라도 그 업무는 **자기를 만든 AX action 을 계속 가리킨다** (WORK-001 invariant 9).

    담당 지정은 초안의 편집 가능 필드로 열려 있다(`TaskCreateInput.assignee_id`). 그 갈래로 갔다는 이유로
    계보를 버리면, 확정된 action 하나가 만든 결과를 되짚을 수 없게 된다 — 거절로 축소하지도 않는다.
    """
    from ax_workspace.platform.persistence import ConversationTurnRecord

    client, settings, database_url = _stack(tmp_path)
    application = client.app.state.workflow_application
    principal = application.authenticated_principal("mina")
    conversation = application.create_conversation(principal, "AX 담당 지정")
    application.accept_conversation_message(
        principal, "지호에게 업무를 만들어줘", UUID(conversation["conversation_id"]), [], "ax-assignee-1"
    )
    with make_session_factory(database_url)() as session:
        execution_id = str(session.scalar(select(ConversationTurnRecord)).execution_id)
    monkeypatch.setenv("AX_MCP_CAUSATION_ID", execution_id)
    proposed = McpReportsFacade(settings, "mina").create_self_task(
        "AX가 지호에게 보낼 업무", "ax-assignee-intent", due_date="2026-09-30", assignee_id="jiho"
    )
    monkeypatch.delenv("AX_MCP_CAUSATION_ID")

    # 확인 전에는 effect 가 없다 — 업무도 요청도 원장도.
    assert proposed["state"] == "pending"
    with make_session_factory(database_url)() as session:
        assert session.scalars(select(TaskRecord)).all() == []
        assert session.scalars(select(WorkRequestRecord)).all() == []
        assert session.scalars(select(TaskCreationAttemptRecord)).all() == []

    [card] = [row for row in client.get("/api/actions", headers=MINA).json() if row["action_id"] == proposed["action_id"]]
    approved = client.post(
        f"/api/actions/{proposed['action_id']}/decide", headers=MINA,
        json={"decision": "approve", "expected_version": card["version"]},
    )
    assert approved.status_code == 200, approved.text

    # 담당은 지호이고, 업무는 자기를 만든 action 과 그 확인 회차를 가리킨다.
    # AX 확인이 만든 것도 **수평 요청**이므로 받는 사람이 수락해야 그 사람의 업무가 된다.
    [waiting] = [row for row in client.get("/api/action-items", headers=JIHO).json() if row["kind"] == "work_request.acceptance"]
    answered = client.post(
        f"/api/action-items/{waiting['action_item_id']}/commands/accept",
        headers=JIHO, json={"expected_version": waiting["expected_version"]},
    )
    assert answered.status_code == 200, answered.text
    [mine] = client.get("/api/my-work", headers=JIHO).json()
    detail = client.get(f"/api/tasks/{mine['task_id']}", headers=JIHO).json()
    assert detail["title"] == "AX가 지호에게 보낼 업무"
    assert detail["lineage"]["source_action_item_id"] == proposed["action_id"]
    assert detail["lineage"]["source_work_request_id"]
    assert detail["lineage"]["source_decision_item_id"] and detail["lineage"]["source_submission_id"]

    # 재실행은 영수증이다 — 확정된 action 하나가 의도 하나이므로 두 번째 업무가 서지 않는다.
    replay = client.post(
        f"/api/actions/{proposed['action_id']}/decide", headers=MINA,
        json={"decision": "approve", "expected_version": card["version"]},
    )
    assert replay.status_code == 200, replay.text
    with make_session_factory(database_url)() as session:
        assert len(session.scalars(select(TaskRecord)).all()) == 1
        assert len(session.scalars(select(WorkRequestRecord)).all()) == 1
        [attempt] = session.scalars(select(TaskCreationAttemptRecord)).all()
        assert attempt.request_key == proposed["action_id"]


def test_the_creating_actor_is_the_one_who_ran_the_command_on_every_path(tmp_path) -> None:
    """`created_by_actor_id` 와 최초 회차의 actor 는 **그 명령을 실제로 부른 사람**이다.

    신규 요청에서 받는 사람은 아무 행위도 하지 않았으므로 거기 설 수 없고, 과거 pending 행을 수락해
    업무를 세운 담당자는 그대로 남아야 한다 — 두 뜻을 한 값으로 뭉개지 않는다.
    """
    client, _, database_url = _stack(tmp_path)
    sent = _create(client, MINA, "actor-1", title="민아가 보낸 업무", assignee_id="jiho").json()

    with make_session_factory(database_url)() as session:
        row = session.get(TaskRecord, UUID(sent["task_id"]))
        assert row.created_by_actor_id == "mina"
    [first] = client.get(f"/api/tasks/{sent['task_id']}/history", headers=JIHO).json()["versions"]
    assert first["change_kind"] == "task.created" and first["actor_id"] == "mina"

    # 과거 모양의 수락은 **수락한 담당자**가 업무를 세운 것이 맞다 — 그 뜻이 바뀌지 않았다.
    legacy = pending_request(client, database_url, MINA, title="예전에 수락될 요청", assignee_id="jiho")
    accepted = client.post(
        f"/api/work-requests/{legacy['request_id']}/accept", headers=JIHO, json={"expected_version": legacy["version"]}
    )
    assert accepted.status_code == 200, accepted.text
    with make_session_factory(database_url)() as session:
        row = session.get(TaskRecord, UUID(accepted.json()["task_id"]))
        assert row.created_by_actor_id == "jiho"

    # 읽기 경계는 담당 관계가 정한다 — 행위자 칼럼이 바뀌어도 보낸 사람이 남의 목록을 얻지 않고,
    # 관계 없는 구성원에게는 업무 읽기 역량이 있어도 존재가 숨는다.
    assert client.get("/api/my-work", headers=MINA).json() == []
    assert client.get(f"/api/tasks/{sent['task_id']}", headers={"X-Demo-Persona": "minseok"}).status_code == 404
    # 보낸 사람은 출처로 계속 읽는다 — 그 길이 끊기지 않았다.
    assert client.get(f"/api/tasks/{sent['task_id']}", headers=MINA).status_code == 200


def test_the_read_boundary_holds_in_the_moment_no_one_holds_the_work(tmp_path) -> None:
    """담당자 변경 **제안 중**에도 활성 담당은 한 명이다 — 그것이 v2 가 없앤 공백이다 (정책 V-18).

    예전에는 이 자리에 활성 담당이 **없었다**: 제안이 기존 담당을 닫았기 때문이다. v2 는 닫지 않으므로
    기존 담당이 계속 책임진다. 읽기 경계는 그대로 고정해 둔다 — 행위자 칼럼이 받는 사람에서 부른
    사람으로 바뀐 뒤(`created_by_actor_id`) 누가 읽고 누가 못 읽는지, 그리고 **읽을 수 있다는 것이
    몰 수 있다는 뜻이 아닌 것**을 함께 본다.
    """
    client, _, database_url = _stack(tmp_path)
    sent = _create(client, MINA, "gap-1", title="담당이 잠시 비는 업무", assignee_id="jiho").json()
    task_id = sent["task_id"]
    # 먼저 지호가 수락해야 옮길 담당이 있다.
    accepted = client.post(
        f"/api/work-requests/{sent['lineage']['source_work_request_id']}/accept",
        headers=JIHO, json={"expected_version": 1},
    )
    assert accepted.status_code == 200, accepted.text
    current = client.get(f"/api/tasks/{task_id}", headers=JIHO).json()
    handed = client.post(
        f"/api/tasks/{task_id}/reassign", headers=JIHO,
        json={"expected_version": current["version"], "assignee_id": "mina", "reason": "넘깁니다"},
    )
    assert handed.status_code == 200, handed.text

    with make_session_factory(database_url)() as session:
        rows = list(
            session.scalars(select(TaskAssignmentRecord).where(TaskAssignmentRecord.task_id == UUID(task_id)))
        )
        # v2: 제안 직후에도 **활성 담당은 한 명**이다 — 기존 담당이 답이 올 때까지 계속 책임진다.
        assert sorted(row.status for row in rows) == ["active", "pending"]
        assert len([row for row in rows if row.status == "active"]) == 1
        # 이 자리에서 fallback 이 읽는 값이다 — 업무를 있게 한 명령을 부른 사람.
        assert session.get(TaskRecord, UUID(task_id)).created_by_actor_id == "mina"

    # 요청자(=부른 사람)는 출처로 읽는다. 지호는 **아직 담당자다** — 제안이 담당을 닫지 않으므로
    # 그 업무는 여전히 자기 작업 공간으로 열린다 (정책 V-18).
    assert client.get(f"/api/tasks/{task_id}", headers=MINA).json()["access"] == "read_only"
    assert client.get(f"/api/tasks/{task_id}", headers=JIHO).json()["access"] == "owner"
    # 조직 전체를 읽는 자격은 담당이 비어도 닿는다 — 이 데모 조직은 회사가 하나라 행위자 칼럼이
    # 누구든 같은 답이 나온다. 차이를 가르는 것은 `work.read.all` 의 범위이지 이 칼럼이 아니다.
    assert client.get(f"/api/tasks/{task_id}", headers=YUNA).json()["access"] == "read_only"
    # 관계도 조직 자격도 없는 구성원에게는 존재가 숨는다 — 담당이 비었다는 이유로 열리지 않는다.
    for stranger in ({"X-Demo-Persona": "minseok"}, {"X-Demo-Persona": "hyeon"}):
        assert client.get(f"/api/tasks/{task_id}", headers=stranger).status_code == 404

    # 읽을 수 있다는 것이 몰 수 있다는 뜻은 아니다 — **드는 사람만 시작한다.**
    # 지호는 아직 담당자라 시작할 수 있고, 요청자와 관리자는 읽을 뿐이라 존재가 숨는다.
    current = client.get(f"/api/tasks/{task_id}", headers=JIHO).json()
    for headers in (MINA, YUNA):
        refused = client.post(
            f"/api/tasks/{task_id}/start", headers=headers, json={"expected_version": current["version"]}
        )
        assert refused.status_code == 404, refused.text
