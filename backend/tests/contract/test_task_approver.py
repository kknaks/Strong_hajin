"""결재자 — WORK-001 이 열만 만들어 둔 자리에 값이 들어온다 (WORK-003 Phase 3).

SPEC-001 §4 Validation 의 넷이 그대로 걸린다: **0..1 · 재직 중 · 담당자 본인 불가 ·
`승인 대기` 뒤 변경 불가.** 화면 라벨은 「결재자」고 계약의 이름은 `approver_id` 다(§7 OQ-N).

**두 갈래가 함께 받는다.** 이 파일은 `업무` 갈래와 그 뒤의 수정·잠금을 본다 —
`요청` 갈래의 저장·수락 전파와 같은 규칙이 걸린다는 사실은 `test_common_work_payload.py` 가 갖는다.
"""
import pytest

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from fastapi.testclient import TestClient

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
YUNA = {"X-Demo-Persona": "yuna"}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    return TestClient(create_app(settings)), database_url


def _task(client, headers, key: str, **body) -> dict:
    created = client.post("/api/tasks", headers={**headers, "Idempotency-Key": key}, json=body)
    assert created.status_code == 201, created.text
    return created.json()


def _detail(client, headers, task_id: str) -> dict:
    response = client.get(f"/api/tasks/{task_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_an_approver_is_stored_at_creation_and_read_back_from_the_projection(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    task = _task(client, MINA, "ap-1", title="결재자가 있는 업무", approver_id="jiho")
    assert task["approver_id"] == "jiho"
    assert _detail(client, MINA, task["task_id"])["approver_id"] == "jiho"
    # 목록도 같은 값을 낸다 — 한 투영에서 나온다.
    rows = {row["task_id"]: row for row in client.get("/api/my-work", headers=MINA).json()}
    assert rows[task["task_id"]]["approver_id"] == "jiho"


def test_an_approver_is_optional_and_stays_empty_when_nobody_is_named(tmp_path) -> None:
    """**0..1 이다.** 비어 있는 것이 정상이고, 빈 문자열도 「없음」으로 읽는다."""
    client, _ = _stack(tmp_path)
    assert _task(client, MINA, "ap-2", title="결재자 없는 업무")["approver_id"] is None
    assert _task(client, MINA, "ap-3", title="빈 결재자", approver_id="")["approver_id"] is None


def test_an_approver_can_be_set_and_cleared_through_the_edit_command(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    task = _task(client, MINA, "ap-4", title="나중에 정하는 결재자")

    named = client.patch(
        f"/api/tasks/{task['task_id']}",
        headers=MINA,
        json={"expected_version": task["version"], "approver_id": "jiho"},
    )
    assert named.status_code == 200, named.text
    assert named.json()["approver_id"] == "jiho"

    # 비우는 길은 **명시적인 플래그**다 — 기한·프로젝트가 이미 그 모양이다(`clear_due_date`·
    # `clear_project`). `approver_id: null` 은 이 표면에서 「안 보냈다」와 구별되지 않으므로
    # 「비운다」로 읽지 않는다.
    cleared = client.patch(
        f"/api/tasks/{task['task_id']}",
        headers=MINA,
        json={"expected_version": named.json()["version"], "clear_approver": True},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["approver_id"] is None

    # **생략하면 건드리지 않는다** — 다른 칸만 고쳐도 결재자가 지워지지 않는다.
    again = client.patch(
        f"/api/tasks/{task['task_id']}",
        headers=MINA,
        json={"expected_version": cleared.json()["version"], "approver_id": "jiho"},
    ).json()
    renamed = client.patch(
        f"/api/tasks/{task['task_id']}",
        headers=MINA,
        json={"expected_version": again["version"], "title": "제목만 바꾼다"},
    )
    assert renamed.status_code == 200 and renamed.json()["approver_id"] == "jiho"


@pytest.mark.parametrize("surface", ["create", "edit"])
def test_the_holder_can_never_be_their_own_approver(tmp_path, surface) -> None:
    """**담당자 본인 불가** — 자기 일을 자기가 확인하는 자리를 만들지 않는다 (D-9)."""
    client, _ = _stack(tmp_path)
    if surface == "create":
        refused = client.post(
            "/api/tasks",
            headers={**MINA, "Idempotency-Key": "self-approve"},
            json={"title": "내가 내 결재자", "approver_id": "mina"},
        )
    else:
        task = _task(client, MINA, "self-approve-edit", title="나중에 나를 결재자로")
        refused = client.patch(
            f"/api/tasks/{task['task_id']}",
            headers=MINA,
            json={"expected_version": task["version"], "approver_id": "mina"},
        )
    assert refused.status_code == 422, refused.text
    assert "담당자 본인" in refused.json()["detail"]


def test_someone_who_is_not_an_active_member_cannot_be_the_approver(tmp_path) -> None:
    """**재직 중**이어야 한다 — 참조자와 **같은 명부**에 묻는다."""
    client, _ = _stack(tmp_path)
    refused = client.post(
        "/api/tasks",
        headers={**MINA, "Idempotency-Key": "ghost-approver"},
        json={"title": "없는 결재자", "approver_id": "nobody"},
    )
    assert refused.status_code == 422, refused.text
    assert "승인자를 다시 선택" in refused.json()["detail"]


def test_the_approver_is_locked_once_a_completion_is_waiting_for_review(tmp_path) -> None:
    """**`승인 대기` 뒤에는 아무도 못 바꾼다** (SPEC-001 §5 권한 · `WORK_APPROVER_LOCKED`, 409).

    이 저장소에서 `승인 대기` 가 서는 자리는 **요청 업무의 완료 보고**다 — 그 회차가 열리면
    요청자 앞에 판단이 놓인다. 그 뒤의 결재자 변경은 누가 무엇에 답했는지를 어긋나게 한다.
    """
    client, _ = _stack(tmp_path)
    sent = client.post(
        "/api/work-requests",
        headers={**MINA, "Idempotency-Key": "locked-approver"},
        json={"title": "확인이 필요한 요청", "assignee_id": "jiho"},
    )
    assert sent.status_code == 201, sent.text
    request = sent.json()
    accepted = client.post(
        f"/api/work-requests/{request['request_id']}/accept",
        headers=JIHO,
        json={"expected_version": request["version"]},
    )
    assert accepted.status_code == 200, accepted.text

    task_id = request["task_id"]
    held = _detail(client, JIHO, task_id)
    # 아직 승인 대기가 아니다 — 결재자를 바꿀 수 있다.
    named = client.patch(
        f"/api/tasks/{task_id}", headers=JIHO, json={"expected_version": held["version"], "approver_id": "yuna"}
    )
    assert named.status_code == 200 and named.json()["approver_id"] == "yuna"

    started = client.post(
        f"/api/tasks/{task_id}/start", headers=JIHO, json={"expected_version": named.json()["version"]}
    )
    assert started.status_code == 200, started.text
    reported = client.post(
        f"/api/tasks/{task_id}/completion-report",
        headers=JIHO,
        json={"expected_version": started.json()["version"], "summary": "끝냈습니다"},
    )
    assert reported.status_code == 200, reported.text
    assert _detail(client, JIHO, task_id)["derived"]["approval"] == "awaiting_review"

    locked = client.patch(
        f"/api/tasks/{task_id}",
        headers=JIHO,
        json={"expected_version": _detail(client, JIHO, task_id)["version"], "approver_id": "mina"},
    )
    assert locked.status_code == 409, locked.text
    assert "승인 대기" in locked.json()["detail"]
    assert _detail(client, JIHO, task_id)["approver_id"] == "yuna"


def test_a_request_approver_survives_every_step_from_send_to_completion_review(tmp_path) -> None:
    """요청에 실은 결재자가 **발송 → 수락 → 완료 보고**를 지나는 동안 한 값으로 남는다.

    생성·수락·상세가 같은 값을 내는 것은 `test_common_work_payload.py` 가 이미 본다. 여기서
    더 보는 것은 **그 뒤**다: 그 업무를 든 사람이 결재자를 고칠 수 있고, `승인 대기` 뒤에는
    두 갈래 모두 같은 자물쇠가 걸린다는 것.
    """
    client, _ = _stack(tmp_path)
    sent = client.post(
        "/api/work-requests",
        headers={**MINA, "Idempotency-Key": "request-approver-lifecycle"},
        json={"title": "결재자를 실어 보낸 요청", "assignee_id": "jiho", "approver_id": "yuna"},
    )
    assert sent.status_code == 201, sent.text
    request = sent.json()
    accepted = client.post(
        f"/api/work-requests/{request['request_id']}/accept",
        headers=JIHO,
        json={"expected_version": request["version"]},
    )
    assert accepted.status_code == 200, accepted.text

    task_id = request["task_id"]
    held = _detail(client, JIHO, task_id)
    assert held["approver_id"] == "yuna"

    # 든 사람이 고칠 수 있다 — 같은 규칙(담당자 본인 불가)이 그대로 걸린다.
    refused = client.patch(
        f"/api/tasks/{task_id}", headers=JIHO, json={"expected_version": held["version"], "approver_id": "jiho"}
    )
    assert refused.status_code == 422 and "담당자 본인" in refused.json()["detail"]
    changed = client.patch(
        f"/api/tasks/{task_id}", headers=JIHO, json={"expected_version": held["version"], "approver_id": "mina"}
    )
    assert changed.status_code == 200 and changed.json()["approver_id"] == "mina"

    # `승인 대기` 뒤에는 잠긴다 — 요청에서 온 업무도 예외가 아니다.
    started = client.post(
        f"/api/tasks/{task_id}/start", headers=JIHO, json={"expected_version": changed.json()["version"]}
    )
    assert started.status_code == 200, started.text
    reported = client.post(
        f"/api/tasks/{task_id}/completion-report",
        headers=JIHO,
        json={"expected_version": started.json()["version"], "summary": "끝냈습니다"},
    )
    assert reported.status_code == 200, reported.text
    locked = client.patch(
        f"/api/tasks/{task_id}",
        headers=JIHO,
        json={"expected_version": _detail(client, JIHO, task_id)["version"], "approver_id": "yuna"},
    )
    assert locked.status_code == 409 and "승인 대기" in locked.json()["detail"]
    assert _detail(client, JIHO, task_id)["approver_id"] == "mina"


# ---- Phase 3 회귀 — 현행이지만 계약으로 고정된 적이 없던 셋 ----------------------


def test_a_task_created_without_an_assignee_records_the_caller_as_the_holder(tmp_path) -> None:
    """`업무` 갈래에 **담당자 칸이 없고**, 보내지 않아도 서버가 현재 사용자를 담당자로 기록한다 (U-6-a).

    현행 동작으로 보였지만 **계약으로 고정된 적이 없다** — 여기서 못 박는다.
    """
    client, _ = _stack(tmp_path)
    task = _task(client, MINA, "holder-1", title="담당자를 적지 않은 업무")
    detail = _detail(client, MINA, task["task_id"])
    assert detail["assignee"] == {"member_id": "mina", "display_name": "민아 (구성원)"}
    assert detail["assignment"]["kind"] == "self" and detail["assignment"]["status"] == "active"
    assert [row["task_id"] for row in client.get("/api/my-work", headers=MINA).json()] == [task["task_id"]]


def test_cc_members_are_stored_on_both_branches(tmp_path) -> None:
    """**참조자가 두 갈래 모두 저장된다** — 「업무로 만들면 저장되지 않습니다」가 사실이 아니다."""
    client, _ = _stack(tmp_path)
    own = _task(client, MINA, "cc-both-1", title="내 업무에 참조자", cc_member_ids=["yuna"])
    assert own["cc_member_ids"] == ["yuna"]
    assert _detail(client, MINA, own["task_id"])["cc_member_ids"] == ["yuna"]

    sent = client.post(
        "/api/work-requests",
        headers={**MINA, "Idempotency-Key": "cc-both-2"},
        json={"title": "요청에 참조자", "assignee_id": "jiho", "cc_member_ids": ["yuna"]},
    )
    assert sent.status_code == 201, sent.text
    assert sent.json()["cc_member_ids"] == ["yuna"]
    assert _detail(client, JIHO, sent.json()["task_id"])["cc_member_ids"] == ["yuna"]


def test_a_request_takes_a_start_date_and_carries_it_into_the_task(tmp_path) -> None:
    """**요청 생성이 시작일을 받는다** — 화면이 접고 있을 뿐 이 입력에 이미 있다 (§4)."""
    client, _ = _stack(tmp_path)
    sent = client.post(
        "/api/work-requests",
        headers={**MINA, "Idempotency-Key": "request-start-date"},
        json={"title": "시작일이 있는 요청", "assignee_id": "jiho", "start_date": "2026-09-10"},
    )
    assert sent.status_code == 201, sent.text
    assert sent.json()["start_date"] == "2026-09-10"
    assert _detail(client, JIHO, sent.json()["task_id"])["start_date"] == "2026-09-10"
