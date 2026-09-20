"""내 업무와 업무 요청이 **같은 공통 payload** 를 저장한다.

두 생성 표면은 오래 어긋나 있었다 — 요청에는 시작일·프로젝트가 없었고 내 업무에는 참조자가 없었다.
그래서 「같은 업무를 만드는 두 길」이 서로 다른 것을 기억했다. 여기서 닫는 것은 넷이다.

1. **아홉 칸이 두 표면에 똑같이 있다** — title · description · start_date · due_date · project_id ·
   parent_task_id · checklist · reference_task_ids · cc_member_ids.
2. **요청에 실은 값이 그 요청이 세운 업무로 그대로 간다** — 시작일과 프로젝트도 예외가 아니다.
3. **참조자는 읽기와 논의만 연다** — 내 업무에서도, 요청에서도. 수행도 판단도 옮기지 않는다.
4. **후속 개발인 것은 아직 없다** — 승인자(`approver_id`)와 선행 업무(`preceding_task_ids`)는 두
   표면 모두 모르는 필드로 거절한다.
"""
from uuid import UUID

import pytest
from sqlalchemy import select

from ax_workspace.platform.persistence import (
    ResourceRelationshipRecord,
    TaskRecord,
    WorkRequestRecord,
    make_session_factory,
)
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from fastapi.testclient import TestClient

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
YUNA = {"X-Demo-Persona": "yuna"}

#: 두 표면이 함께 갖는 칸. 하나라도 한쪽에만 있으면 「같은 업무를 다르게 저장하는 두 경로」가 다시 생긴다.
COMMON_PAYLOAD_FIELDS = {
    "title",
    "description",
    "start_date",
    "due_date",
    "project_id",
    "parent_task_id",
    "checklist",
    "reference_task_ids",
    "cc_member_ids",
    # WORK-003 Phase 2 가 더한 열 번째 칸 — 두 갈래가 **같은 배열**을 받는다 (SPEC-001 §5 표면 일치).
    "preceding_task_ids",
    # 열한 번째 — **결재자도 두 갈래가 함께 받는다.** 갈래가 정하는 것은 누가 담당인가 하나뿐이다.
    "approver_id",
}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    return TestClient(create_app(settings)), database_url


def _project_with(client, *member_ids: str) -> str:
    created = client.post("/api/projects", headers=JIHO, json={"name": "공통 payload 프로젝트"})
    assert created.status_code == 201, created.text
    project_id = created.json()["project_id"]
    for member_id in member_ids:
        joined = client.post(f"/api/projects/{project_id}/members", headers=JIHO, json={"member_id": member_id})
        assert joined.status_code == 201, joined.text
    return project_id


# ---- 1. 같은 아홉 칸 -----------------------------------------------------------


def test_both_creation_surfaces_declare_exactly_the_same_common_payload() -> None:
    """모델이 **한 곳에서** 이 아홉을 정의한다 — 두 입력이 같은 부모를 상속하는지를 여기서 잠근다."""
    from ax_workspace.modules.work.request_commands import WorkRequestCreateInput
    from ax_workspace.modules.work.task_creation import TaskCreateInput, WorkPayloadFields

    assert set(WorkPayloadFields.model_fields) == COMMON_PAYLOAD_FIELDS
    assert COMMON_PAYLOAD_FIELDS <= set(TaskCreateInput.model_fields)
    assert COMMON_PAYLOAD_FIELDS <= set(WorkRequestCreateInput.model_fields)
    # 요청 전용은 **둘뿐이다.** 담당은 공통이 아니라 경로의 차이다.
    assert set(WorkRequestCreateInput.model_fields) - COMMON_PAYLOAD_FIELDS == {
        "assignee_id",
        "supersedes_request_id",
    }
    # `업무` 갈래만 갖는 것은 **담당 하나**다. 결재자는 공통이라 여기 서지 않는다.
    assert set(TaskCreateInput.model_fields) - COMMON_PAYLOAD_FIELDS == {"assignee_id"}
    assert "approver_id" in WorkRequestCreateInput.model_fields


def test_both_branches_store_the_approver_and_the_request_hands_it_to_its_task(tmp_path) -> None:
    """**결재자는 두 갈래 모두 저장된다**, 그리고 요청의 결재자가 그 요청이 세우는 업무로 이어진다.

    예전에는 `요청` 갈래가 이 필드를 열지 않았다(OQ-M). 확정된 계약은 둘 다 받는 것이고,
    갈래가 갈리는 자리는 **누가 담당인가** 하나뿐이다 — 내 업무는 만드는 사람, 요청은 받는 사람.
    그래서 같은 값이 두 표면에서 같은 이름으로 나가고 같은 규칙을 지난다.
    """
    client, database_url = _stack(tmp_path)

    own = client.post(
        "/api/tasks",
        headers={**MINA, "Idempotency-Key": "task-approver"},
        json={"title": "업무에 결재자", "approver_id": "jiho"},
    )
    assert own.status_code == 201, own.text
    assert own.json()["approver_id"] == "jiho"

    sent = client.post(
        "/api/work-requests",
        headers={**MINA, "Idempotency-Key": "request-approver"},
        json={"title": "요청에 결재자", "assignee_id": "jiho", "approver_id": "yuna"},
    )
    assert sent.status_code == 201, sent.text
    request = sent.json()
    assert request["approver_id"] == "yuna"

    # **요청 행이 직접 들고 있다** — 호출 인자로만 흘려보내면 다시 읽을 때 사라진다.
    with make_session_factory(database_url)() as session:
        [stored] = session.scalars(select(WorkRequestRecord)).all()
        assert stored.approver_id == "yuna"

    # 발송이 세운 업무가 같은 값을 들고 선다 — 생성·상세·업무 셋이 한 값이다.
    assert client.get(f"/api/tasks/{request['task_id']}", headers=JIHO).json()["approver_id"] == "yuna"
    assert client.get(f"/api/work-requests/{request['request_id']}", headers=MINA).json()["approver_id"] == "yuna"

    # 수락해도 그 값은 그대로다 — 수락은 담당을 확정할 뿐 결재자를 다시 정하지 않는다.
    accepted = client.post(
        f"/api/work-requests/{request['request_id']}/accept",
        headers=JIHO,
        json={"expected_version": request["version"]},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["approver_id"] == "yuna"
    assert client.get(f"/api/tasks/{request['task_id']}", headers=JIHO).json()["approver_id"] == "yuna"


def test_the_request_branch_runs_the_same_approver_rules_as_my_own_work(tmp_path) -> None:
    """**규칙을 복제하지 않는다** — 재직 중 · 담당자 본인 불가가 두 갈래에 똑같이 걸린다.

    요청 갈래의 담당은 **받는 사람**이다: 자기에게 온 일을 자기가 확인하는 자리를 만들지 않는다.
    """
    client, database_url = _stack(tmp_path)

    same = client.post(
        "/api/work-requests",
        headers={**MINA, "Idempotency-Key": "request-self-approve"},
        json={"title": "받는 사람이 결재자", "assignee_id": "jiho", "approver_id": "jiho"},
    )
    assert same.status_code == 422, same.text
    assert "담당자 본인" in same.json()["detail"]

    ghost = client.post(
        "/api/work-requests",
        headers={**MINA, "Idempotency-Key": "request-ghost-approve"},
        json={"title": "없는 결재자", "assignee_id": "jiho", "approver_id": "nobody"},
    )
    assert ghost.status_code == 422, ghost.text
    assert "승인자를 다시 선택" in ghost.json()["detail"]

    # 거절이 **아무것도 남기지 않는다** — 요청 행도 그 요청이 세울 업무도 서지 않았다.
    with make_session_factory(database_url)() as session:
        assert session.scalars(select(WorkRequestRecord)).all() == []
        assert session.scalars(select(TaskRecord)).all() == []

    # 비우는 것은 정상이다 — 0..1 이다.
    blank = client.post(
        "/api/work-requests",
        headers={**MINA, "Idempotency-Key": "request-no-approve"},
        json={"title": "결재자 없는 요청", "assignee_id": "jiho", "approver_id": ""},
    )
    assert blank.status_code == 201 and blank.json()["approver_id"] is None


@pytest.mark.parametrize("field", ["material_ids", "attachment_draft_ids", "attachments"])
def test_the_request_payload_never_grew_a_materials_field(tmp_path, field) -> None:
    """**자료는 요청 생성 payload 에 없다.** 자료는 두 단계로 붙고(생성 뒤 첨부) 그 계약이 그대로다.

    받아서 무시하는 칸을 만들지 않는다 — `extra='forbid'` 가 알 수 없는 필드로 422 를 낸다.
    """
    client, database_url = _stack(tmp_path)
    refused = client.post(
        "/api/work-requests",
        headers={**MINA, "Idempotency-Key": f"request-{field}"},
        json={"title": "자료를 실은 요청", "assignee_id": "jiho", field: []},
    )
    assert refused.status_code == 422, refused.text
    with make_session_factory(database_url)() as session:
        assert session.scalars(select(WorkRequestRecord)).all() == []


@pytest.mark.parametrize("surface", ["tasks", "work-requests"])
def test_neither_surface_takes_a_field_that_belongs_to_later_work(tmp_path, surface) -> None:
    """선행 업무의 **후속**인 것은 여전히 열지 않는다 — `preceding_task_ids` 는 이제 계약이다.

    WORK-003 Phase 2 가 선행 배열을 두 갈래 모두에 열었으므로, 「아직 없는 칸」으로 남은 것은
    간트 화면(OQ-K)처럼 **데이터 계약조차 서지 않은 것**뿐이다. 그 자리를 지키는 시험이다.
    """
    client, database_url = _stack(tmp_path)
    body = {"title": "아직 없는 칸", "gantt_lane_id": "lane-1"}
    if surface == "work-requests":
        body["assignee_id"] = "jiho"
    refused = client.post(f"/api/{surface}", headers={**MINA, "Idempotency-Key": f"later-{surface}"}, json=body)
    assert refused.status_code == 422, refused.text
    with make_session_factory(database_url)() as session:
        assert session.scalars(select(TaskRecord)).all() == []
        assert session.scalars(select(WorkRequestRecord)).all() == []


# ---- 2. 요청이 실은 값이 업무로 간다 -------------------------------------------


def test_a_request_carries_the_whole_common_payload_into_the_task_it_stands_up(tmp_path) -> None:
    """발송이 세우는 업무가 **요청에 적힌 아홉 칸 그대로** 선다 — 시작일도 프로젝트도 사라지지 않는다."""
    client, database_url = _stack(tmp_path)
    project_id = _project_with(client, "mina", "jiho")
    earlier = client.post(
        "/api/tasks", headers={**MINA, "Idempotency-Key": "earlier-work"}, json={"title": "참고가 될 지난 업무"}
    ).json()

    sent = client.post(
        "/api/work-requests",
        headers={**MINA, "Idempotency-Key": "full-payload-request"},
        json={
            "title": "공통 payload 요청",
            "assignee_id": "jiho",
            "description": "내용",
            "start_date": "2026-09-10",
            "due_date": "2026-09-30",
            "project_id": project_id,
            "checklist": ["첫 단계"],
            "reference_task_ids": [earlier["task_id"]],
            "cc_member_ids": ["yuna"],
        },
    )
    assert sent.status_code == 201, sent.text
    request = sent.json()
    # 요청의 응답이 **내 업무와 같은 이름으로** 같은 값을 낸다.
    assert request["start_date"] == "2026-09-10"
    assert request["due_date"] == "2026-09-30"
    assert request["project_id"] == project_id
    assert request["cc_member_ids"] == ["yuna"]

    # 요청 행 자신이 그 값을 들고 있다 — 호출 인자로만 흘려보내면 다시 읽을 때 사라진다.
    with make_session_factory(database_url)() as session:
        [stored] = session.scalars(select(WorkRequestRecord)).all()
        assert stored.start_date.isoformat() == "2026-09-10"
        assert str(stored.project_id) == project_id

    task = client.get(f"/api/tasks/{request['task_id']}", headers=JIHO).json()
    assert task["start_date"] == "2026-09-10"
    assert task["due_date"] == "2026-09-30"
    assert task["project_id"] == project_id
    assert task["cc_member_ids"] == ["yuna"]

    # 수락하면 받는 사람의 작업 공간이 열리고, 요청에 실렸던 단계와 참고 업무가 그 안에 있다.
    answered = client.post(
        f"/api/work-requests/{request['request_id']}/accept",
        headers=JIHO,
        json={"expected_version": request["version"]},
    )
    assert answered.status_code == 200, answered.text
    held = client.get(f"/api/tasks/{request['task_id']}", headers=JIHO).json()
    assert [step["text"] for step in held["checklist"]] == ["첫 단계"]
    assert held["start_date"] == "2026-09-10" and held["project_id"] == project_id
    # 참고 업무의 **포인터는 따라오고 그것을 열 권한은 따라오지 않는다** — 읽을 수 없으면 `task` 가 비는 것이 사실이다.
    assert len(held["references"]) == 1 and held["references"][0]["task"] is None


def test_a_self_task_stores_and_returns_the_same_nine_values(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    project_id = _project_with(client, "mina")
    parent = client.post(
        "/api/tasks", headers={**MINA, "Idempotency-Key": "parent-work"}, json={"title": "중심 업무"}
    ).json()

    created = client.post(
        "/api/tasks",
        headers={**MINA, "Idempotency-Key": "full-payload-task"},
        json={
            "title": "공통 payload 내 업무",
            "description": "내용",
            "start_date": "2026-09-10",
            "due_date": "2026-09-30",
            "project_id": project_id,
            "parent_task_id": parent["task_id"],
            "checklist": ["첫 단계"],
            "reference_task_ids": [parent["task_id"]],
            "cc_member_ids": ["yuna"],
        },
    )
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["start_date"] == "2026-09-10" and task["due_date"] == "2026-09-30"
    # 하위 업무는 프로젝트를 묻지 않고 **상위를 따른다** — 상위에 프로젝트가 없으면 이 업무도 없다.
    assert task["project_id"] is None
    assert task["cc_member_ids"] == ["yuna"]
    assert client.get(f"/api/tasks/{task['task_id']}", headers=MINA).json()["cc_member_ids"] == ["yuna"]


# ---- 3. 참조자는 읽기와 논의만 연다 --------------------------------------------


def test_a_cc_member_of_my_own_work_may_read_it_and_may_not_touch_it(tmp_path) -> None:
    """내 업무의 참조자도 **읽기**에 들어온다 — 목록과 상세가 같은 문에서 열린다.

    목록에 넣지 않고 상세만 열면 「상세는 보이는데 그 업무의 자료는 못 연다」가 된다: 자료 쪽이 묻는
    권한 판정이 그 목록으로 답하기 때문이다. 그리고 **수행은 그대로 막힌다** — 참조는 담당이 아니다.
    """
    client, _ = _stack(tmp_path)
    task = client.post(
        "/api/tasks",
        headers={**MINA, "Idempotency-Key": "cc-on-my-work"},
        json={"title": "참조자가 있는 내 업무", "cc_member_ids": ["yuna"]},
    ).json()

    read = client.get(f"/api/tasks/{task['task_id']}", headers=YUNA)
    assert read.status_code == 200, read.text
    assert read.json()["access"] == "read_only"
    assert read.json()["cc_member_ids"] == ["yuna"]

    # 참조로 받은 업무는 **내 업무가 아니다.** 드는 것과 읽는 것을 한 목록에 섞지 않는다.
    assert [row["task_id"] for row in client.get("/api/my-work", headers=YUNA).json()] == []
    assert task["task_id"] in {row["task_id"] for row in client.get("/api/tasks", headers=YUNA).json()}

    # 수행도 수정도 열리지 않는다.
    started = client.post(
        f"/api/tasks/{task['task_id']}/start", headers=YUNA, json={"expected_version": task["version"]}
    )
    assert started.status_code >= 400, started.text
    edited = client.patch(
        f"/api/tasks/{task['task_id']}",
        headers=YUNA,
        json={"expected_version": task["version"], "title": "참조자가 고친 제목"},
    )
    assert edited.status_code >= 400, edited.text
    assert client.get(f"/api/tasks/{task['task_id']}", headers=MINA).json()["title"] == "참조자가 있는 내 업무"


def test_a_stranger_still_cannot_find_work_they_were_not_copied_into(tmp_path) -> None:
    """참조자를 여는 것이 **다른 누구에게도** 문을 열지 않는다."""
    client, _ = _stack(tmp_path)
    task = client.post(
        "/api/tasks",
        headers={**JIHO, "Idempotency-Key": "no-cc-at-all"},
        json={"title": "아무도 참조하지 않은 업무"},
    ).json()
    hidden = client.get(f"/api/tasks/{task['task_id']}", headers=MINA)
    assert hidden.status_code == 404, hidden.text


def test_the_cc_of_a_request_reads_both_the_request_and_the_task_it_produced(tmp_path) -> None:
    """요청의 참조자는 **그 요청이 세운 업무도** 읽는다 — 한쪽만 열면 경계가 어긋난다."""
    client, database_url = _stack(tmp_path)
    request = client.post(
        "/api/work-requests",
        headers={**MINA, "Idempotency-Key": "cc-flows-to-task"},
        json={"title": "참조자가 있는 요청", "assignee_id": "jiho", "cc_member_ids": ["yuna"]},
    ).json()

    assert client.get(f"/api/work-requests/{request['request_id']}", headers=YUNA).status_code == 200
    task = client.get(f"/api/tasks/{request['task_id']}", headers=YUNA)
    assert task.status_code == 200, task.text
    assert task.json()["cc_member_ids"] == ["yuna"]
    # 판단은 **받는 사람뿐이다** — 참조자가 수락할 수 없다.
    answered = client.post(
        f"/api/work-requests/{request['request_id']}/accept",
        headers=YUNA,
        json={"expected_version": request["version"]},
    )
    assert answered.status_code >= 400, answered.text

    with make_session_factory(database_url)() as session:
        kinds = {
            (row.resource_type, row.relationship_kind)
            for row in session.scalars(select(ResourceRelationshipRecord))
            if row.member_id == "yuna" and row.valid_until is None
        }
        assert kinds == {("work_request", "cc"), ("task", "cc")}


def test_a_recipient_creation_still_refuses_its_three_fields_but_carries_the_cc(tmp_path) -> None:
    """`POST /api/tasks` 에 담당을 적는 갈래의 계약은 **그대로다** — 다만 참조자는 원래 요청의 칸이라 간다."""
    client, _ = _stack(tmp_path)
    refused = client.post(
        "/api/tasks",
        headers={**MINA, "Idempotency-Key": "recipient-start-date"},
        json={"title": "남에게 보내는 업무", "assignee_id": "jiho", "start_date": "2026-09-10"},
    )
    assert refused.status_code == 422 and "쓸 수 없는 항목" in refused.json()["detail"]

    sent = client.post(
        "/api/tasks",
        headers={**MINA, "Idempotency-Key": "recipient-with-cc"},
        json={"title": "참조자와 함께 보낸 업무", "assignee_id": "jiho", "cc_member_ids": ["yuna"]},
    )
    assert sent.status_code == 201, sent.text
    assert sent.json()["cc_member_ids"] == ["yuna"]
    assert client.get(f"/api/tasks/{sent.json()['task_id']}", headers=YUNA).status_code == 200


def test_a_cc_member_who_is_not_an_active_member_is_refused_not_quietly_dropped(tmp_path) -> None:
    """없는 이름을 조용히 버리면 보낸 사람은 참조자가 사라진 것을 끝내 모른다."""
    client, database_url = _stack(tmp_path)
    refused = client.post(
        "/api/tasks",
        headers={**MINA, "Idempotency-Key": "unknown-cc"},
        json={"title": "없는 참조자", "cc_member_ids": ["nobody"]},
    )
    assert refused.status_code == 422, refused.text
    assert "nobody" in refused.json()["detail"]
    with make_session_factory(database_url)() as session:
        assert session.scalars(select(TaskRecord)).all() == []


def test_the_creator_is_never_left_standing_in_their_own_cc_list(tmp_path) -> None:
    """만든 사람은 이미 담당 자리에 있다 — 같은 사람을 두 자리에 세우지 않는다 (요청 쪽과 같은 규칙)."""
    client, _ = _stack(tmp_path)
    task = client.post(
        "/api/tasks",
        headers={**MINA, "Idempotency-Key": "self-in-cc"},
        json={"title": "나를 참조자로 적은 업무", "cc_member_ids": [" mina ", "yuna", "mina"]},
    ).json()
    assert task["cc_member_ids"] == ["yuna"]
