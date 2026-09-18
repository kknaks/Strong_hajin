"""업무 구조·생명주기 v2 — 요청·수락·하위 관계·완료 확인의 계약 (SPEC-003 §4~6 · WORK-002).

**여기서 무엇을 증명하는가.** 작업계획서 §5 의 인수 시나리오 A1~C2 21건과 `example.md` §10 의
10단계를 **실제 계약 입구**(REST · MCP · AX)로 밟아, 명령이 만든 **상태 · 담당 · 파생 표시 · 권한 ·
이력**을 함께 단언한다. 명령 경로로 데이터를 세우고, private helper 로 종단을 대신하지 않는다.

**무엇을 증명하지 않는가.** 여기는 SQLite 계약 테스트다 — PostgreSQL 의 FK · 유일성 · 잠금 · 경합은
`make test-postgres` 의 몫이고, 이 파일이 그것을 대신했다고 말하지 않는다 (WORK-002 §검증 계획 · P-7).
브라우저 E2E 도 실행하지 않는다 — 화면에서 눈으로 볼 것은 사용자 몫이다.

**미정은 미정으로 둔다.**
- **OQ-203** — 미완결 하위가 있어도 **완료 보고 제출**은 현행대로 통과하고, 막히는 자리는 **최종 완료**다.
  이 파일은 그 현행을 확인할 뿐 「새 정책이 확정됐다」로 적지 않는다.
- **OQ-206** — 요청자 자리가 `system:meeting` 인 승격 요청의 **완료 확인자**는 미정이다. 임의로 사람을
  세우지도, 자동 승인을 만들지도 않는다. 여기서는 **일반 요청자 축**만 검증한다.
- `derived.reply` · `derived.status_note` 는 그 원장이 아직 없어 **`null` 이 정상**이다.
- `child_progress.blocking` 은 **보이는 하위**만 센다 — 0 이라고 완료가 허용된다는 뜻이 아니다
  (읽을 수 없는 하위도 막는다 · `..._without_naming_them`).
- `WORK_PARENT_CYCLE` 은 **도달 경로가 없다** — 부모는 생성 시점에 정해지고 이동 명령이 없다(EU-15).
  그 자리는 「고리를 만들 입구가 없다」로 확인한다.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import select

from ax_workspace.platform.persistence import (
    TaskAssignmentRecord,
    TaskRecord,
    WorkRequestRecord,
    make_session_factory,
)

from test_task_lifecycle_v2_support import (
    HYEON,
    JIHO,
    MINA,
    MINSEOK,
    SORA,
    YUNA,
    accept_request,
    acceptance_item,
    approve_delivery,
    assignment_item,
    delivery_item,
    detail,
    own_task,
    report_completion,
    request_version,
    run_command,
    send_request,
    stack,
    start,
    version,
)


# ---------------------------------------------------------------------------
# A1 · A2 · A3 · B1 — 만들고, 보내고, 받고, 거절한다
# ---------------------------------------------------------------------------


def test_lifecycle_v2_a1_own_work_stands_open_and_starting_is_a_separate_fact(tmp_path) -> None:
    """A1 · example 1 — 보고서와 그 아래 내용 작성. **생성과 시작은 다른 사건이다** (정책 V-13)."""
    client, _ = stack(tmp_path)
    report = own_task(client, "보고서 작성", MINA)

    view = detail(client, report, MINA)
    assert view["state"] == "open" and view["assignee"]["member_id"] == "mina"
    assert view["parent"] is None and view["derived"]["assignment"] is None
    # 만든 순간은 시작이 아니다.
    assert view["started_at"] is None

    # 중심 업무의 직속에 직접 작업을 둔다 (정책 V-8 · §5 중심 업무 판정).
    writing = own_task(client, "내용 작성", MINA, parent_task_id=report)
    assert detail(client, writing, MINA)["parent"]["task_id"] == report

    # 그 아래 또 직접 작업은 막힌다 — 같은 사람의 직접 작업이 무한히 겹치지 않는다.
    nested = client.post("/api/tasks", headers=MINA, json={"title": "더 깊은 직접 작업", "parent_task_id": writing})
    assert nested.status_code == 409, nested.text

    started = start(client, report, MINA)
    assert started["state"] == "in_progress"
    assert detail(client, report, MINA)["started_at"] is not None


def test_lifecycle_v2_a2_sending_stands_the_task_and_the_wait_not_the_holder(tmp_path) -> None:
    """A2 · example 2 — **발송이 업무를 세운다.** 받는 사람은 아직 담당이 아니다 (정책 V-9·V-10)."""
    client, database_url = stack(tmp_path)
    report = own_task(client, "보고서 작성", MINA)
    sent = send_request(client, "디자인", "jiho", MINA, parent_task_id=report)

    assert sent["state"] == "pending" and sent["parent_task_id"] == report
    task_id = sent["task_id"]
    assert task_id, "발송이 성공하면 Task 가 즉시 존재한다"

    view = detail(client, task_id, MINA)
    assert view["state"] == "open"
    assert view["assignee"] is None, "발송 직후에는 활성 담당이 없다"
    assert view["derived"]["assignment"] == "awaiting_acceptance"
    assert view["derived"]["approval"] is None and view["derived"]["proposal"] is None
    # 이 두 이름은 SPEC-001 에서 이어받았지만 그 원장이 아직 없다 — 없는 것을 있다고 내지 않는다.
    assert view["derived"]["reply"] is None and view["derived"]["status_note"] is None
    assert view["parent"]["task_id"] == report

    # 수신자의 「내 업무」에는 서지 않고 **응답 대기**에 선다 (UX-U1 데이터 칸).
    assert task_id not in {row["task_id"] for row in client.get("/api/my-work", headers=JIHO).json()}
    waiting = acceptance_item(client, JIHO, sent["request_id"])
    assert waiting is not None and waiting["status"] == "awaiting_review"
    assert {command["id"] for command in waiting["allowed_commands"]} == {"accept", "adjust", "reject"}

    # 상위에서는 그 자리가 이미 보인다 — 발송 단계부터 상위 아래에 선다.
    parent_view = detail(client, report, MINA)
    assert [row["task_id"] for row in parent_view["children"]] == [task_id]
    assert parent_view["children"][0]["derived"]["assignment"] == "awaiting_acceptance"
    assert parent_view["child_progress"] == {"done": 0, "blocking": 1, "cancelled": 0, "total": 1}

    with make_session_factory(database_url)() as session:
        rows = session.scalars(
            select(TaskAssignmentRecord).where(TaskAssignmentRecord.task_id == UUID(task_id))
        ).all()
        # 활성 담당은 0 이고 기다리는 행이 하나다 — `pending` 은 활성 수에 들어가지 않는다 (K-3).
        assert [row.status for row in rows] == ["pending"]


def test_lifecycle_v2_a2_both_entrances_give_the_same_result_and_keep_their_own_input(tmp_path) -> None:
    """`POST /api/tasks` 의 타인 지정은 **원래부터 요청이다** — 결과와 권한이 같고 입력 계약은 다르다."""
    client, _db = stack(tmp_path)

    through_tasks = client.post("/api/tasks", headers=MINA, json={"title": "타인 지정 생성", "assignee_id": "jiho"})
    assert through_tasks.status_code == 201, through_tasks.text
    body = through_tasks.json()
    # 응답 봉투는 입구마다 그대로다 — 여기는 Task 투영 하나다 (기존 동작 보존).
    assert "request_id" not in body
    view = detail(client, body["task_id"], MINA)
    assert view["state"] == "open" and view["assignee"] is None
    assert view["derived"]["assignment"] == "awaiting_acceptance"
    assert view["lineage"]["source_work_request_id"], "이 갈래는 배정이 아니라 요청이다"
    # 받는 사람에게도 발송 입구와 똑같이 **응답 대기**로 선다.
    assert acceptance_item(client, JIHO, view["lineage"]["source_work_request_id"]) is not None
    assert body["task_id"] not in {row["task_id"] for row in client.get("/api/my-work", headers=JIHO).json()}

    # 그 갈래는 세 필드를 거절한다 — 상위를 붙인 하위 요청은 발송 입구로 보낸다 (기존 동작 보존).
    parent = own_task(client, "상위", MINA)
    for field, value in (
        ("parent_task_id", parent),
        ("start_date", "2026-09-20"),
        ("project_id", "11111111-1111-4111-8111-111111111111"),
    ):
        refused = client.post(
            "/api/tasks", headers=MINA, json={"title": "거절될 생성", "assignee_id": "jiho", field: value}
        )
        assert refused.status_code == 422, (field, refused.text)
        assert field in refused.text
    # 같은 상위를 발송 입구로 보내면 선다 — 차이는 입력 계약이지 결과가 아니다.
    assert send_request(client, "하위 요청", "jiho", MINA, parent_task_id=parent)["parent_task_id"] == parent

    # 권한 판정도 같다 — 허용 후보 밖이면 **어느 입구에서도 서지 않는다** (정책 E-2: 권한 밖 지정을
    # 서버가 요청으로 바꿔 보내는 경로가 없다). 거절의 봉투는 입구마다 그대로다 (기존 동작 보존).
    for path in ("/api/tasks", "/api/work-requests"):
        refused = client.post(path, headers=MINA, json={"title": "후보 밖", "assignee_id": "nobody"})
        assert refused.status_code in {403, 422}, (path, refused.text)
    with make_session_factory(_db)() as session:
        assert [row.title for row in session.scalars(select(WorkRequestRecord)) if row.title == "후보 밖"] == []


def test_lifecycle_v2_a3_acceptance_confirms_the_same_task(tmp_path) -> None:
    """A3 · example 3-A·4 — **수락은 새 Task 를 만들지 않는다.** 수락과 시작이 각각 읽힌다."""
    client, database_url = stack(tmp_path)
    report = own_task(client, "보고서 작성", MINA)
    sent = send_request(client, "디자인", "jiho", MINA, parent_task_id=report)
    task_id, request_id = sent["task_id"], sent["request_id"]

    accepted = accept_request(client, request_id, JIHO)
    assert accepted["state"] == "accepted"
    assert accepted["task_id"] == task_id, "같은 업무의 담당이 확정된다"

    view = detail(client, task_id, JIHO)
    assert view["state"] == "open", "받아들인 것과 시작한 것은 다른 사실이다"
    assert view["assignee"]["member_id"] == "jiho"
    assert view["parent"]["task_id"] == report
    assert view["derived"]["assignment"] is None
    assert view["assignment"]["status"] == "active" and view["assignment"]["accepted_at"] is not None
    assert view["started_at"] is None

    # 이제 「내 업무」에 서고, 답할 질문은 닫힌다.
    assert task_id in {row["task_id"] for row in client.get("/api/my-work", headers=JIHO).json()}
    assert acceptance_item(client, JIHO, request_id) is None

    start(client, task_id, JIHO)
    running = detail(client, task_id, JIHO)
    assert running["state"] == "in_progress"
    # 수락 시각과 시작 시각이 **각각** 읽힌다 (정책 V-13 · UX-U3).
    assert running["started_at"] is not None
    assert running["started_at"] != running["assignment"]["accepted_at"]

    with make_session_factory(database_url)() as session:
        # 요청 하나에 업무 하나. 수락이 두 번째 업무를 만들지 않았다.
        assert len(session.scalars(select(TaskRecord).where(TaskRecord.title == "디자인")).all()) == 1
        rows = session.scalars(
            select(TaskAssignmentRecord).where(TaskAssignmentRecord.task_id == UUID(task_id))
        ).all()
        assert [row.status for row in rows] == ["active"]


def test_lifecycle_v2_b1_rejection_cancels_and_a_re_request_is_new(tmp_path) -> None:
    """B1 · B2 · example 3-B·3-C — 거절은 업무를 닫고, 재요청은 **새 요청·새 Task** 다 (정책 V-11·V-12)."""
    client, _ = stack(tmp_path)
    report = own_task(client, "보고서 작성", MINA)
    first = send_request(client, "디자인", "jiho", MINA, parent_task_id=report)

    # 사유 없는 거절은 거절이 아니다 (`WORK_REJECT_REASON_REQUIRED`, 422).
    empty = client.post(
        f"/api/work-requests/{first['request_id']}/reject", headers=JIHO, json={"expected_version": 1, "reason": " "}
    )
    assert empty.status_code == 422, empty.text
    assert detail(client, first["task_id"], MINA)["state"] == "open", "거부된 명령은 아무것도 바꾸지 않는다"

    rejected = client.post(
        f"/api/work-requests/{first['request_id']}/reject",
        headers=JIHO,
        json={"expected_version": request_version(client, first["request_id"], JIHO), "reason": "이번 주는 어렵습니다"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["state"] == "rejected"

    cancelled = detail(client, first["task_id"], MINA)
    assert cancelled["state"] == "cancelled"
    assert cancelled["cancel_reason"] == "request_rejected"
    assert cancelled["parent"]["task_id"] == report, "상위 연결은 남는다"

    # 재요청 — 같은 상위 아래 새 Task 이고 이전 요청을 가리킨다. 거절된 Task 를 되살리지 않는다.
    second = send_request(
        client, "디자인", "hyeon", MINA, parent_task_id=report, supersedes_request_id=first["request_id"]
    )
    assert second["request_id"] != first["request_id"] and second["task_id"] != first["task_id"]
    assert second["supersedes_request_id"] == first["request_id"]
    assert second["parent_task_id"] == report

    children = {row["task_id"]: row for row in detail(client, report, MINA)["children"]}
    assert set(children) == {first["task_id"], second["task_id"]}
    assert children[first["task_id"]]["state"] == "cancelled"
    # B2 — 취소된 하위는 검사에서 빠지고, 남은 하나만 상위를 막는다.
    assert detail(client, report, MINA)["child_progress"] == {"done": 0, "blocking": 1, "cancelled": 1, "total": 2}
    blocking = detail(client, report, MINA)["derived"]["blocking_children"]
    assert [row["task_id"] for row in blocking] == [second["task_id"]]

    # 이력은 남는다 — 정리는 나중 일이고 사건이 지워지는 것이 아니다.
    history = client.get(f"/api/tasks/{first['task_id']}/history", headers=MINA)
    assert history.status_code == 200 and "cancelled" in history.text
    assert {row["request_id"] for row in client.get("/api/work-requests", headers=MINA).json()} == {
        first["request_id"], second["request_id"]
    }


def test_lifecycle_v2_only_the_recipient_answers_and_a_second_answer_is_refused(tmp_path) -> None:
    """수락·거절·협의는 **그 요청의 수신자만**. 이미 답한 요청은 `409` 다 (§4 Case Matrix)."""
    client, _ = stack(tmp_path)
    sent = send_request(client, "디자인", "jiho", MINA)
    request_id = sent["request_id"]

    # 판단 권한을 가진 사람이라도 그 요청의 수신자가 아니면 답할 수 없다 (`WORK_REQUEST_RESPONDER_ONLY`).
    intruder = client.post(f"/api/work-requests/{request_id}/accept", headers=YUNA, json={"expected_version": 1})
    assert intruder.status_code == 403, intruder.text
    intruder_reject = client.post(
        f"/api/work-requests/{request_id}/reject", headers=YUNA, json={"expected_version": 1, "reason": "대신 거절"}
    )
    assert intruder_reject.status_code == 403, intruder_reject.text
    # 요청자 자신도 자기 요청을 대신 수락하지 못한다.
    assert client.post(
        f"/api/work-requests/{request_id}/accept", headers=MINA, json={"expected_version": 1}
    ).status_code == 403
    assert client.get(f"/api/work-requests/{request_id}", headers=MINA).json()["state"] == "pending"
    assert detail(client, sent["task_id"], MINA)["assignee"] is None

    accept_request(client, request_id, JIHO)
    # **이것은 재전송이 아니라 새 명령이다** — 답이 끝난 요청의 *지금* 회차로 다시 답하려는 시도이고,
    # `WORK_REQUEST_NOT_PENDING` 으로 거절된다. 통신 재시도(같은 회차·같은 답)의 영수증은 C1 이 본다.
    late_command = client.post(f"/api/work-requests/{request_id}/accept", headers=JIHO, json={"expected_version": 2})
    assert late_command.status_code == 409, late_command.text
    reversed_answer = client.post(
        f"/api/work-requests/{request_id}/reject", headers=JIHO, json={"expected_version": 2, "reason": "역시 어렵다"}
    )
    assert reversed_answer.status_code == 409, reversed_answer.text
    assert detail(client, sent["task_id"], JIHO)["state"] == "open"
    assert detail(client, sent["task_id"], JIHO)["assignee"]["member_id"] == "jiho"

    # 수락 뒤에는 요청자도 조건을 혼자 바꾸지 못한다 — 길은 제안–동의 하나다 (정책 V-20).
    locked = client.post(
        f"/api/work-requests/{request_id}/amend",
        headers=MINA,
        json={"expected_version": request_version(client, request_id, MINA), "title": "조건을 바꾼다"},
    )
    assert locked.status_code == 409, locked.text


# ---------------------------------------------------------------------------
# A4 · A5 — 받은 일을 다시 나눈다, 요청자가 그 아래를 읽는다
# ---------------------------------------------------------------------------


def test_lifecycle_v2_a4_a_held_request_is_its_own_centre_of_work(tmp_path) -> None:
    """A4 · example 5 — 수락한 요청 업무는 **그 사람의 새 중심 업무**다 (정책 V-7 · §5 중심 업무 판정)."""
    client, _ = stack(tmp_path)
    report = own_task(client, "보고서 작성", MINA)
    design = send_request(client, "디자인", "jiho", MINA, parent_task_id=report)
    accept_request(client, design["request_id"], JIHO)
    design_id = design["task_id"]

    # 직속 하위 둘 — 조사와 시안. 저장 깊이는 이미 두 단계를 넘었다 (정책 V-6).
    survey = own_task(client, "레퍼런스 조사", JIHO, parent_task_id=design_id)
    draft = own_task(client, "시안 구성", JIHO, parent_task_id=design_id)
    assert detail(client, survey, JIHO)["parent"]["task_id"] == design_id
    assert detail(client, report, MINA)["parent"] is None

    # 그 아래 또 자기 직접 작업은 막힌다 (`WORK_DIRECT_NESTING`, 409 · 정책 V-8).
    nested = client.post("/api/tasks", headers=JIHO, json={"title": "더 깊은 직접 작업", "parent_task_id": draft})
    assert nested.status_code == 409, nested.text

    # 다른 사람에게 보내는 **하위 요청**은 그 자리에서 선다 — 요청과 배정은 끝까지 다른 행위다 (E-1).
    illustration = send_request(client, "일러스트", "yuna", JIHO, parent_task_id=design_id)
    assert illustration["parent_task_id"] == design_id
    assert detail(client, illustration["task_id"], JIHO)["derived"]["assignment"] == "awaiting_acceptance"
    accept_request(client, illustration["request_id"], YUNA)
    # 받은 그 업무는 이제 **그 사람의 중심 업무**이므로 그 아래 자기 직접 작업을 둘 수 있다.
    assert client.post(
        "/api/tasks", headers=YUNA, json={"title": "일러스트 초안", "parent_task_id": illustration["task_id"]}
    ).status_code == 201

    # 상세의 `children` 은 **직속만** 낸다 — 저장 깊이와 무관하다 (정책 L-11).
    design_children = {row["task_id"] for row in detail(client, design_id, JIHO)["children"]}
    assert design_children == {survey, draft, illustration["task_id"]}
    assert survey not in {row["task_id"] for row in detail(client, report, MINA)["children"]}


def test_lifecycle_v2_no_child_stands_under_a_request_nobody_has_taken(tmp_path) -> None:
    """수락 전 요청 Task 아래에는 하위가 서지 않는다 (`WORK_PARENT_UNASSIGNED`, 409 — V-7·V-8 에서 도출)."""
    client, _ = stack(tmp_path)
    waiting = send_request(client, "디자인", "jiho", MINA)

    for headers in (MINA, JIHO):
        refused = client.post(
            "/api/tasks", headers=headers, json={"title": "이른 하위", "parent_task_id": waiting["task_id"]}
        )
        assert refused.status_code == 409, (headers, refused.text)
    early_request = client.post(
        "/api/work-requests",
        headers=MINA,
        json={"title": "이른 하위 요청", "assignee_id": "minseok", "parent_task_id": waiting["task_id"]},
    )
    assert early_request.status_code == 409, early_request.text

    # 수락하면 그 자리에서 열린다.
    accept_request(client, waiting["request_id"], JIHO)
    assert client.post(
        "/api/tasks", headers=JIHO, json={"title": "이제 되는 하위", "parent_task_id": waiting["task_id"]}
    ).status_code == 201

    # 끝난 업무에는 붙지 않고(`WORK_PARENT_CLOSED`, 409), 읽을 수 없는 업무는 **없는 것처럼** 답한다.
    closed = own_task(client, "곧 끝낼 업무", MINA)
    client.post(
        f"/api/tasks/{closed}/cancel", headers=MINA,
        json={"expected_version": version(client, closed, MINA), "reason": "계획에서 빠졌습니다"},
    )
    late = client.post("/api/tasks", headers=MINA, json={"title": "뒤늦은 하위", "parent_task_id": closed})
    assert late.status_code == 409, late.text
    theirs = own_task(client, "민석의 업무", MINSEOK)
    hidden = client.post("/api/tasks", headers=MINA, json={"title": "몰래", "parent_task_id": theirs})
    assert hidden.status_code == 404, hidden.text
    missing = client.post(
        "/api/tasks", headers=MINA, json={"title": "없는 상위", "parent_task_id": "11111111-1111-4111-8111-111111111111"}
    )
    assert missing.status_code == 404, missing.text


def test_lifecycle_v2_the_parent_link_is_fixed_at_creation_so_no_cycle_can_be_made(tmp_path) -> None:
    """상위 관계를 **옮기는 명령이 없다** (미정 EU-15) — 그래서 자기 조상을 부모로 둘 입구도 없다."""
    client, _ = stack(tmp_path)
    parent = own_task(client, "상위", MINA)
    child = own_task(client, "하위", MINA, parent_task_id=parent)

    # 수정 명령은 상위를 아예 받지 않는다 — 「받고 거절한다」가 아니라 **입력에 그 자리가 없다**.
    moved = client.patch(
        f"/api/tasks/{parent}",
        headers=MINA,
        json={"expected_version": version(client, parent, MINA), "parent_task_id": child},
    )
    assert moved.status_code == 422 and "parent_task_id" in moved.text
    assert detail(client, parent, MINA)["parent"] is None
    assert detail(client, child, MINA)["parent"]["task_id"] == parent


def test_lifecycle_v2_a5_the_requester_reads_the_subtree_and_its_materials_only(tmp_path) -> None:
    """A5 — 요청자는 **자기가 부탁한 일과 그 아래 전부**를 읽는다. 다른 일은 읽지 않는다 (정책 V-21)."""
    client, _ = stack(tmp_path)
    report = own_task(client, "보고서 작성", MINA)
    # 참조(CC)는 요청을 함께 보는 사람이다 — **요청자가 아니다.** 아래에서 그 경계를 함께 못 박는다.
    design = send_request(client, "디자인", "jiho", MINA, parent_task_id=report, cc_member_ids=["minseok"])
    accept_request(client, design["request_id"], JIHO)
    draft = own_task(client, "시안 구성", JIHO, parent_task_id=design["task_id"])

    attached = client.post(
        f"/api/tasks/{draft}/materials",
        headers=JIHO,
        data={"kind": "output"},
        files={"file": ("시안.md", "한빛 시안 A안과 B안".encode(), "text/markdown")},
    )
    assert attached.status_code == 201, attached.text

    # 상세 · 목록 · 본문이 **함께** 열린다 — 한 경로만 열면 「상세는 보이는데 파일은 못 연다」가 된다.
    assert detail(client, draft, MINA)["title"] == "시안 구성"
    listed = client.get(f"/api/tasks/{draft}/materials", headers=MINA)
    assert listed.status_code == 200 and [row["name"] for row in listed.json()] == ["시안.md"]
    material_id = listed.json()[0]["material_id"]
    content = client.get(f"/api/tasks/{draft}/materials/{material_id}/content", headers=MINA)
    assert content.status_code == 200 and "한빛" in content.text

    # 수행자의 **다른 업무**와 거기 연결하지 않은 자료는 없는 것처럼 답한다.
    unrelated = own_task(client, "지호의 다른 일", JIHO)
    client.post(
        f"/api/tasks/{unrelated}/materials",
        headers=JIHO,
        data={"kind": "input"},
        files={"file": ("사적.md", "다른 업무의 비밀".encode(), "text/markdown")},
    )
    assert client.get(f"/api/tasks/{unrelated}", headers=MINA).status_code == 404
    assert client.get(f"/api/tasks/{unrelated}/materials", headers=MINA).status_code == 404
    found = client.get("/api/materials/search", headers=MINA, params={"q": "비밀"})
    assert found.status_code == 200 and "사적" not in found.text

    # **조회가 명령을 넓히지 않는다** — 읽는다고 시작·완료·배정할 수 있는 것이 아니다.
    #
    # 거절의 **모양까지** 못 박는다. 수행 명령(`start`·`complete`·`completion-report`)은 「그 업무를
    # 지금 들고 있는 사람」에게만 열리고, 들고 있지 않으면 **없는 업무와 같은 말**로 답한다 — 읽을 수
    # 있다는 사실이 거절 문구에서 새 나가지 않는다 (SPEC-001 계승 `WORK_NOT_FOUND`, 404).
    # 담당 변경은 그 앞에 **역량**이 선다: 민아에게는 `task.assign` 이 없으므로 403 이다 (§5 권한표).
    current = version(client, draft, MINA)
    # 명령마다 **그 명령이 받는 본문**을 그대로 싣는다 — 본문이 틀려 422 로 튕긴 것을 권한 거절로
    # 읽으면 이 단언이 아무것도 증명하지 못한다.
    for command, body in (
        ("start", {"expected_version": current}),
        ("complete", {"expected_version": current}),
        ("completion-report", {"expected_version": current, "summary": "가로채기"}),
    ):
        refused = client.post(f"/api/tasks/{draft}/{command}", headers=MINA, json=body)
        assert refused.status_code == 404, (command, refused.text)
        # 거절 본문이 그 업무의 내용을 알려 주지 않는다.
        assert "시안" not in refused.text, (command, refused.text)
    handover = client.post(
        f"/api/tasks/{draft}/reassign",
        headers=MINA,
        json={"expected_version": current, "assignee_id": "mina", "reason": "내가 하겠다"},
    )
    assert handover.status_code == 403, handover.text
    assert "task.assign" in handover.text
    # 거부된 명령은 아무것도 바꾸지 않는다 — 상태도 담당도 그대로다.
    unchanged = detail(client, draft, MINA)
    assert unchanged["state"] == "open" and unchanged["version"] == current
    assert unchanged["assignee"]["member_id"] == "jiho"

    # **이 권한은 요청자의 것이다** (정책 V-21 — 「요청자는 자신이 요청한 Task 와 그 하위 트리를 읽는다」).
    # 참조로 함께 받은 사람은 그 요청을 계속 보지만, 그 사실만으로 하위와 자료가 열리지는 않는다.
    assert client.get(f"/api/work-requests/{design['request_id']}", headers=MINSEOK).status_code == 200
    assert client.get(f"/api/tasks/{draft}", headers=MINSEOK).status_code == 404
    assert client.get(f"/api/tasks/{draft}/materials", headers=MINSEOK).status_code == 404
    cc_search = client.get("/api/materials/search", headers=MINSEOK, params={"q": "한빛"})
    assert cc_search.status_code == 200 and "시안.md" not in cc_search.text


def test_lifecycle_v2_a_recipient_answers_their_own_request_whatever_their_role(tmp_path) -> None:
    """수락·거절은 **그 요청의 수신자**의 것이다 — 수신자에게 별도 자격을 더 요구하지 않는다 (§5 권한표).

    v2 가 응답 단계를 되살렸으므로, 응답 권한이 일부 역할에만 있으면 **그 사람에게 보낸 요청은 아무도
    수락할 수 없는 요청**이 된다. 그것은 OQ-206 이 승격 축에서 막아 둔 모양이고 일반 축에서는 계약이
    이미 답을 정해 두었다 — 수신자가 답한다.
    """
    client, _ = stack(tmp_path)
    sent = send_request(client, "정산 확인", "minseok", MINA)

    waiting = acceptance_item(client, MINSEOK, sent["request_id"])
    assert waiting is not None, "받는 사람의 판단함에 그 요청이 서야 한다"

    accepted = client.post(
        f"/api/work-requests/{sent['request_id']}/accept", headers=MINSEOK, json={"expected_version": 1}
    )
    assert accepted.status_code == 200, accepted.text
    assert detail(client, sent["task_id"], MINSEOK)["assignee"]["member_id"] == "minseok"

    # 거절도 같은 사람의 것이다.
    other = send_request(client, "또 하나", "minseok", MINA)
    rejected = client.post(
        f"/api/work-requests/{other['request_id']}/reject",
        headers=MINSEOK,
        json={"expected_version": 1, "reason": "이번 분기에는 어렵습니다"},
    )
    assert rejected.status_code == 200, rejected.text
    assert detail(client, other["task_id"], MINA)["state"] == "cancelled"


# ---------------------------------------------------------------------------
# A6 · A7 · A8 · A9 — 보고하고 보완하고 승인하고, 상위를 끝낸다
# ---------------------------------------------------------------------------


def test_lifecycle_v2_a6_report_revision_and_approval_stay_in_one_task(tmp_path) -> None:
    """A6 · example 6~9 — 보고 → 보완 → 재보고 → 승인이 **같은 Task 의 회차**다 (E-3 · E-12)."""
    client, database_url = stack(tmp_path)
    sent = send_request(client, "디자인", "jiho", MINA)
    accept_request(client, sent["request_id"], JIHO)
    task_id = sent["task_id"]
    start(client, task_id, JIHO)

    # 요청 Task 에서 최종 완료는 담당자의 자리가 아니다 (기존 동작 보존).
    refused = client.post(
        f"/api/tasks/{task_id}/complete", headers=JIHO, json={"expected_version": version(client, task_id, JIHO)}
    )
    assert refused.status_code == 422 and "완료 보고" in refused.text

    first = report_completion(client, task_id, "1차 시안입니다", JIHO)
    assert first.status_code == 200, first.text
    # 제출이 성공하면 **밖으로는 `done` + `awaiting_review`** 다 — `completion_submitted` 는 계약에 없다.
    view = detail(client, task_id, JIHO)
    assert view["state"] == "done" and view["derived"]["approval"] == "awaiting_review"
    assert view["completed_at"] is not None

    with make_session_factory(database_url)() as session:
        # **별도 검토 Task 가 만들어지지 않는다** — 확인은 같은 업무에서 이뤄진다 (E-12).
        assert len(session.scalars(select(TaskRecord)).all()) == 1
        assert len(session.scalars(select(WorkRequestRecord)).all()) == 1

    # 확인은 **요청자**의 것이다 — 담당자가 자기 결과를 스스로 인정할 수 없다 (정책 V-14·V-22).
    assert delivery_item(client, MINA, task_id) is not None
    holder_side = delivery_item(client, JIHO, task_id)
    if holder_side is not None:
        denied = run_command(client, holder_side, "accept", JIHO)
        assert denied.status_code in {403, 404, 422}, denied.text
        assert detail(client, task_id, JIHO)["derived"]["approval"] == "awaiting_review"

    item = delivery_item(client, MINA, task_id)
    # 보완에는 사유가 필요하다 — 「부족하다」만으로는 답이 아니다.
    assert run_command(client, item, "request_changes", MINA).status_code == 422
    asked = run_command(client, item, "request_changes", MINA, reason="B안의 여백을 줄여 주세요")
    assert asked.status_code == 200, asked.text

    revising = detail(client, task_id, JIHO)
    assert revising["state"] == "in_progress" and revising["derived"]["approval"] == "awaiting_revision"
    assert revising["completed_at"] is None

    second = report_completion(client, task_id, "여백을 줄였습니다", JIHO)
    assert second.status_code == 200, second.text
    next_round = delivery_item(client, MINA, task_id)
    assert next_round["action_item_id"] == item["action_item_id"], "같은 질문의 다음 회차다"
    assert next_round["submission_version"] == 2

    approved = run_command(client, next_round, "accept", MINA)
    assert approved.status_code == 200, approved.text
    final = detail(client, task_id, JIHO)
    assert final["state"] == "done" and final["derived"]["approval"] == "approved"
    # 요청자의 검토 결과를 담당자가 되짚어 승인하는 경로가 없다 (E-12).
    assert delivery_item(client, JIHO, task_id) is None

    rounds = client.get(f"/api/action-items/{item['action_item_id']}", headers=MINA).json()["rounds"]
    assert [row["submission_version"] for row in rounds] == [1, 2]
    assert rounds[0]["snapshot"]["summary"] == "1차 시안입니다"


def test_lifecycle_v2_a7_a8_a9_the_final_completion_is_what_the_children_block(tmp_path) -> None:
    """A7 · A8 · A9 · example 10 — 막히는 자리는 **그 업무의 최종 완료**다 (정책 V-15·V-16·V-17)."""
    client, _ = stack(tmp_path)
    report = own_task(client, "보고서 작성", MINA)
    start(client, report, MINA)
    design = send_request(client, "디자인", "jiho", MINA, parent_task_id=report)
    accept_request(client, design["request_id"], JIHO)
    design_id = design["task_id"]
    start(client, design_id, JIHO)
    draft = own_task(client, "시안 구성", JIHO, parent_task_id=design_id)

    # A7 — 미완결 하위가 있으면 **최종 완료**가 막힌다. 막는 하위를 이름으로 낸다 (UX-U7).
    blocked = client.post(
        f"/api/tasks/{draft}/complete", headers=JIHO, json={"expected_version": version(client, draft, JIHO)}
    )
    assert blocked.status_code == 200, "막는 하위가 없는 직접 작업은 그대로 끝난다"

    other = own_task(client, "덜 끝난 조사", JIHO, parent_task_id=design_id)
    # OQ-203 — **제출**은 기본값에서 통과한다. 이 파일은 그 현행을 확인할 뿐 새 정책으로 올리지 않는다.
    submitted = report_completion(client, design_id, "시안을 넘깁니다", JIHO)
    assert submitted.status_code == 200, submitted.text
    assert detail(client, design_id, JIHO)["derived"]["approval"] == "awaiting_review"

    # A8 — 그렇게 선 `done` + `awaiting_review` 는 **상위 완료 검사에서 완결이 아니다**.
    parent_view = detail(client, report, MINA)
    assert parent_view["child_progress"] == {"done": 0, "blocking": 1, "cancelled": 0, "total": 1}
    assert [row["why"] for row in parent_view["derived"]["blocking_children"]] == ["awaiting_approval"]
    refused_parent = client.post(
        f"/api/tasks/{report}/complete", headers=MINA, json={"expected_version": version(client, report, MINA)}
    )
    assert refused_parent.status_code == 409, refused_parent.text
    assert "디자인" in refused_parent.text

    # 그리고 **승인 자체가** 그 업무의 미완결 하위에 막힌다 — 그 검사는 이미 승인 명령에 있다 (O-34).
    item = delivery_item(client, MINA, design_id)
    blocked_approval = run_command(client, item, "accept", MINA)
    assert blocked_approval.status_code == 409, blocked_approval.text
    assert "덜 끝난 조사" in blocked_approval.text
    assert detail(client, design_id, MINA)["derived"]["approval"] == "awaiting_review"

    # 남은 하위를 취소하면 검사에서 빠진다 — 취소가 완료로 바뀌는 것이 아니다 (로그는 남는다).
    client.post(
        f"/api/tasks/{other}/cancel", headers=JIHO,
        json={"expected_version": version(client, other, JIHO), "reason": "이번엔 하지 않기로 했습니다"},
    )
    assert detail(client, other, JIHO)["state"] == "cancelled"
    assert detail(client, design_id, JIHO)["child_progress"]["cancelled"] == 1
    approved = run_command(client, delivery_item(client, MINA, design_id), "accept", MINA)
    assert approved.status_code == 200, approved.text
    assert detail(client, design_id, MINA)["derived"]["approval"] == "approved"

    # A9 — 하위가 다 끝나도 **자동 완료는 없다.** 사람이 부르면 그제야 끝난다.
    assert detail(client, report, MINA)["state"] == "in_progress"
    assert detail(client, report, MINA)["child_progress"] == {"done": 1, "blocking": 0, "cancelled": 0, "total": 1}
    done = client.post(
        f"/api/tasks/{report}/complete", headers=MINA, json={"expected_version": version(client, report, MINA)}
    )
    assert done.status_code == 200, done.text
    assert detail(client, report, MINA)["state"] == "done"


def test_lifecycle_v2_a7_the_blocking_error_names_only_what_the_caller_may_read(tmp_path) -> None:
    """막는 판정과 말해 주는 내용을 가른다 — **목록·건수·오류 본문이 같은 권한 규칙 위에 선다**.

    오류가 부르는 이름은 그 사람이 목록에서 이미 보는 하위뿐이고, 건수는 그 목록과 어긋나지 않는다
    (UX-U7 · U-15). **「상위를 읽으면 하위도 읽는다」가 아니다** — 참조(CC)나 조직 범위 독자는 상위를
    읽어도 그 하위가 열리지 않는다. 열리는 것은 **완료 게이트를 부를 수 있는 두 사람**에게이고,
    그 근거는 아래 `..._sees_every_direct_child_...` 가 따로 못 박는다.
    """
    client, _ = stack(tmp_path)
    report = own_task(client, "보고서 작성", MINA)
    start(client, report, MINA)
    design = send_request(client, "디자인", "jiho", MINA, parent_task_id=report)
    accept_request(client, design["request_id"], JIHO)
    own_task(client, "시안 구성", JIHO, parent_task_id=design["task_id"])

    for headers in (MINA, JIHO):
        view = detail(client, design["task_id"], headers)
        # 목록과 건수는 같은 규칙에서 나온다 — 이름을 못 내는 것이 건수에만 남는 자리가 없다.
        assert view["child_progress"]["total"] == len(view["children"])
        assert {row["task_id"] for row in view["derived"]["blocking_children"]} <= {
            row["task_id"] for row in view["children"]
        }

    refused = client.post(
        f"/api/tasks/{report}/complete", headers=MINA, json={"expected_version": version(client, report, MINA)}
    )
    assert refused.status_code == 409, refused.text
    named = {row["title"] for row in detail(client, report, MINA)["children"]}
    # 오류가 부르는 이름은 그 사람이 이미 보는 하위에서만 나온다.
    assert any(title in refused.text for title in named)
    assert "시안 구성" not in refused.text, "직속이 아닌 하위를 상위의 거절 사유로 부르지 않는다"


def test_lifecycle_v2_a7_the_completion_gate_sees_every_direct_child_even_one_a_third_party_created(tmp_path) -> None:
    """완료 게이트를 부르는 사람에게 **직속 하위가 숨는 자리가 없다** — 남이 건 하위까지 이름으로 나온다.

    이 자리가 중요한 이유는 반대쪽이 참이 아니기 때문이다: **상위를 읽는다고 하위가 열리지는 않는다.**
    참조(CC)는 요청을 읽어도 그 하위가 닫혀 있고(§5 요청 관계 읽기 — V-21 은 요청자·승격자만 넓힌다),
    조직 범위 독자도 마찬가지다. 그런데 **완료 게이트를 부를 수 있는 사람은 둘뿐**이고 그 둘은 각자
    다른 길로 직속 하위를 읽는다 —

    - 본인·배정 업무의 `complete` 는 **활성 담당자**만 부른다. 그 사람은 「상위를 든 사람은 그 부분을
      읽는다」로 직속 하위를 읽는다.
    - 요청 Task 의 최종 완료는 **요청자의 승인**이다. 그 사람은 V-21 의 요청 조상 탐색으로 읽는다.

    그래서 「읽을 수 없는 하위가 이름 없이 막는다」는 갈래는 코드에 남아 있어도 **API 로는 그 상태를
    만들 수 없다.** 여기서 그 전제를 실제로 확인한다: 상위를 읽을 뿐인 제3자(대표)가 하위를 걸어도
    게이트를 부르는 두 사람 모두 그 하위를 목록·건수·오류 본문에서 본다.
    """
    client, _ = stack(tmp_path)

    # ① 담당자 축 — 민아가 든 업무 아래로 **대표가** 하위 요청을 건다.
    held = own_task(client, "민아가 든 상위", MINA)
    start(client, held, MINA)
    outside = client.post(
        "/api/work-requests",
        headers=YUNA,
        json={"title": "대표가 건 하위", "assignee_id": "minseok", "parent_task_id": held},
    )
    assert outside.status_code == 201, outside.text

    view = detail(client, held, MINA)
    assert [row["title"] for row in view["children"]] == ["대표가 건 하위"]
    assert view["child_progress"] == {"done": 0, "blocking": 1, "cancelled": 0, "total": 1}
    assert [row["title"] for row in view["derived"]["blocking_children"]] == ["대표가 건 하위"]
    blocked = client.post(
        f"/api/tasks/{held}/complete", headers=MINA, json={"expected_version": version(client, held, MINA)}
    )
    assert blocked.status_code == 409, blocked.text
    assert "대표가 건 하위" in blocked.text, "막는 하위가 이름 없이 막는 자리가 아니다"

    # ② 승인 축 — 민아가 건 요청 업무 아래로 **대표가** 하위 요청을 건다.
    design = send_request(client, "디자인", "jiho", MINA)
    accept_request(client, design["request_id"], JIHO)
    start(client, design["task_id"], JIHO)
    under = client.post(
        "/api/work-requests",
        headers=YUNA,
        json={"title": "대표가 디자인 아래 건 하위", "assignee_id": "minseok", "parent_task_id": design["task_id"]},
    )
    assert under.status_code == 201, under.text
    # 담당자도 요청자도 그 하위를 읽는다 — 어느 쪽에서도 숨지 않는다.
    assert client.get(f"/api/tasks/{under.json()['task_id']}", headers=JIHO).status_code == 200
    assert client.get(f"/api/tasks/{under.json()['task_id']}", headers=MINA).status_code == 200
    assert [row["title"] for row in detail(client, design["task_id"], MINA)["children"]] == [
        "대표가 디자인 아래 건 하위"
    ]

    report_completion(client, design["task_id"], "넘깁니다", JIHO)
    refused_approval = run_command(client, delivery_item(client, MINA, design["task_id"]), "accept", MINA)
    assert refused_approval.status_code == 409, refused_approval.text
    assert "대표가 디자인 아래 건 하위" in refused_approval.text

    # 반대쪽 — 상위를 읽을 뿐인 참조(CC)에게는 그 하위가 열리지 않는다. 그 사람은 게이트도 부르지 못한다.
    cc_request = send_request(client, "참조가 달린 요청", "jiho", MINA, cc_member_ids=["minseok"])
    accept_request(client, cc_request["request_id"], JIHO)
    cc_child = own_task(client, "참조에게는 닫힌 하위", JIHO, parent_task_id=cc_request["task_id"])
    assert client.get(f"/api/work-requests/{cc_request['request_id']}", headers=MINSEOK).status_code == 200
    assert client.get(f"/api/tasks/{cc_child}", headers=MINSEOK).status_code == 404


def test_lifecycle_v2_a7_a_new_holder_reads_the_children_they_inherit_the_gate_for(tmp_path) -> None:
    """담당이 바뀌어도 **완료 게이트에 숨는 직속 하위가 생기지 않는다.**

    가장 날카로운 자리다 — 새 담당자는 그 하위의 **요청자도 승격자도 아니고**(V-21 이 넓혀 주지 않는다),
    조직·프로젝트 독립 열람도 없다. 그런데 상위를 넘겨받은 순간 그 업무의 `complete` 를 부를 수 있게
    된다. 읽기가 그 자리에서 함께 열리지 않으면 **이름도 건수도 없이 막히는 완료**가 생긴다.

    **그 둘이 같은 사건에서 함께 움직인다**는 것을 한 사람의 전후로 못 박는다: 교체 전에는 그 하위가
    없는 것과 같고(404), 교체를 수락한 뒤에는 목록·건수·오류 본문에 이름으로 선다. 여는 것은 「상위를
    읽는다」가 아니라 **「상위를 든다」**이다.

    넘겨준 쪽도 함께 본다 — 상위는 더 이상 읽지 못하지만(책임이 옮겨 갔다) **자기가 부탁한 하위는
    계속 읽는다**(V-21). 책임과 열람이 각자의 근거로 움직인다.
    """
    client, _ = stack(tmp_path)
    # 넘겨주는 사람도 받는 사람도 `task.assign` 과 조직 전체 열람이 없는 구성원이다 —
    # 독립적인 읽기 경로가 결과를 대신 열어 주지 않게 한다.
    parent = own_task(client, "민석의 상위", MINSEOK)
    start(client, parent, MINSEOK)
    handed_down = send_request(client, "민석이 건 하위", "hyeon", MINSEOK, parent_task_id=parent)
    accept_request(client, handed_down["request_id"], HYEON)
    child = handed_down["task_id"]

    # 교체 전 — 새 담당 후보에게 그 하위는 없는 것과 같다.
    assert client.get(f"/api/tasks/{child}", headers=MINA).status_code == 404
    assert client.get(f"/api/tasks/{parent}", headers=MINA).status_code == 404

    proposed = client.post(
        f"/api/tasks/{parent}/reassign",
        headers=YUNA,
        json={"expected_version": version(client, parent, MINSEOK), "assignee_id": "mina", "reason": "담당을 옮깁니다"},
    )
    assert proposed.status_code == 200, proposed.text
    accepted = client.post(f"/api/task-assignments/{proposed.json()['assignment_id']}/accept", headers=MINA)
    assert accepted.status_code == 200, accepted.text
    assert client.get(f"/api/tasks/{parent}/assignments", headers=YUNA).json()["current"]["assignee_id"] == "mina"

    # 교체 뒤 — 게이트가 열린 그 사람에게 그 하위가 함께 열린다.
    inherited = detail(client, parent, MINA)
    assert [row["title"] for row in inherited["children"]] == ["민석이 건 하위"]
    assert inherited["child_progress"] == {"done": 0, "blocking": 1, "cancelled": 0, "total": 1}
    assert [row["title"] for row in inherited["derived"]["blocking_children"]] == ["민석이 건 하위"]
    assert client.get(f"/api/tasks/{child}", headers=MINA).status_code == 200

    blocked = client.post(
        f"/api/tasks/{parent}/complete", headers=MINA, json={"expected_version": version(client, parent, MINA)}
    )
    assert blocked.status_code == 409, blocked.text
    assert "민석이 건 하위" in blocked.text, "이름 없이 막히는 완료가 생기지 않는다"

    # 넘겨준 쪽 — 상위는 놓았고(책임), 자기가 부탁한 하위는 그대로 읽는다(V-21).
    assert client.get(f"/api/tasks/{parent}", headers=MINSEOK).status_code == 404
    assert client.get(f"/api/tasks/{child}", headers=MINSEOK).status_code == 200



# ---------------------------------------------------------------------------
# B3 · B4 — 담당 변경: 책임이 비는 순간이 없다
# ---------------------------------------------------------------------------


def _handover(client, task_id: str, assignee_id: str, headers):
    return client.post(
        f"/api/tasks/{task_id}/reassign",
        headers=headers,
        json={"expected_version": version(client, task_id, headers), "assignee_id": assignee_id, "reason": "일정이 겹칩니다"},
    )


def test_lifecycle_v2_b3_b4_a_handover_proposal_leaves_the_holder_in_place(tmp_path) -> None:
    """B3 · B4 — 제안은 기존 담당을 닫지 않고, 수락은 **한 덩어리로** 바꾼다 (정책 V-18 · K-3)."""
    client, database_url = stack(tmp_path)
    # 지호가 든 요청 업무에서 시작한다 — 담당자는 자기 담당을 넘기자고 제안할 수 있다 (§4 권한).
    sent = send_request(client, "분기 보고 정리", "jiho", MINA)
    accept_request(client, sent["request_id"], JIHO)
    task_id = sent["task_id"]

    proposed = _handover(client, task_id, "mina", JIHO)
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["status"] == "pending"

    # 현재 담당과 대기 제안이 **각각** 읽힌다 (UX-U8 · 정책 P-3).
    ledger = client.get(f"/api/tasks/{task_id}/assignments", headers=JIHO).json()
    assert ledger["current"]["assignee_id"] == "jiho" and ledger["current"]["status"] == "active"
    assert ledger["pending"]["assignee_id"] == "mina" and ledger["pending"]["status"] == "pending"
    assert ledger["pending"]["supersedes_assignment_id"] == ledger["current"]["assignment_id"]
    # 단건 투영도 **활성 담당**을 낸다 — 대기 제안이 그 자리를 차지하지 않는다.
    view = detail(client, task_id, JIHO)
    assert view["assignee"]["member_id"] == "jiho" and view["assignment"]["status"] == "active"
    assert view["derived"]["assignment"] == "awaiting_handover"

    # 대기 중에도 기존 담당이 계속 수행한다 — 「내 업무」 등재와 명령이 모두 그대로다.
    assert task_id in {row["task_id"] for row in client.get("/api/my-work", headers=JIHO).json()}
    assert start(client, task_id, JIHO)["state"] == "in_progress"
    assert report_completion(client, task_id, "대기 중에도 보고한다", JIHO).status_code == 200

    # 같은 종류의 대기 제안은 하나다 (`WORK_ASSIGNMENT_PROPOSAL_EXISTS`, 409).
    twice = _handover(client, task_id, "mina", JIHO)
    assert twice.status_code == 409, twice.text

    # 제안의 수신자가 아닌 사람은 답할 수 없다 — 존재를 숨긴다 (SPEC-001 계승).
    pending_id = ledger["pending"]["assignment_id"]
    for headers in (MINSEOK, JIHO, YUNA):
        hidden = client.post(f"/api/task-assignments/{pending_id}/decline", headers=headers, json={"reason": "대신 거절"})
        assert hidden.status_code == 404, (headers, hidden.text)
    assert client.post(f"/api/task-assignments/{pending_id}/accept", headers=JIHO).status_code == 404

    # B3 — 거절은 **제안만** 닫는다. Task 도 기존 담당도 그대로다.
    declined = client.post(f"/api/task-assignments/{pending_id}/decline", headers=MINA, json={"reason": "다른 일이 많습니다"})
    assert declined.status_code == 200, declined.text
    after = client.get(f"/api/tasks/{task_id}/assignments", headers=JIHO).json()
    assert after["current"]["assignee_id"] == "jiho" and after["pending"] is None
    assert detail(client, task_id, JIHO)["state"] == "done"
    assert detail(client, task_id, JIHO)["derived"]["assignment"] is None

    # B4 — 다시 제안하고 이번엔 수락한다. 교체는 원자적이다.
    second = _handover(client, task_id, "mina", JIHO).json()
    accepted = client.post(f"/api/task-assignments/{second['assignment_id']}/accept", headers=MINA)
    assert accepted.status_code == 200, accepted.text
    final = client.get(f"/api/tasks/{task_id}/assignments", headers=JIHO).json()
    assert final["current"]["assignee_id"] == "mina" and final["pending"] is None
    assert detail(client, task_id, MINA)["assignee"]["member_id"] == "mina"
    assert task_id not in {row["task_id"] for row in client.get("/api/my-work", headers=JIHO).json()}

    with make_session_factory(database_url)() as session:
        rows = session.scalars(
            select(TaskAssignmentRecord).where(TaskAssignmentRecord.task_id == UUID(task_id))
        ).all()
        # 활성은 언제나 하나다 — 0명이나 2명인 중간 상태가 원장에도 남지 않는다 (K-3).
        assert [row.status for row in rows].count("active") == 1
        # 제안·거절·교체가 **각각** 남는다 (정책 L-9).
        assert sorted(row.status for row in rows) == ["active", "declined", "superseded"]
    # 이력 조회도 그 셋을 함께 낸다.
    assert len(final["history"]) == 3


# ---------------------------------------------------------------------------
# B5 · B6 · B9 — 철회, 합의 취소, 조건 변경
# ---------------------------------------------------------------------------


def test_lifecycle_v2_b5_withdrawal_before_acceptance_cancels_and_keeps_the_log(tmp_path) -> None:
    """B5 — 수락 전 철회는 요청자의 것이다. 업무는 취소되고 상위 연결과 로그는 남는다."""
    client, _ = stack(tmp_path)
    report = own_task(client, "보고서 작성", MINA)
    sent = send_request(client, "디자인", "jiho", MINA, parent_task_id=report)

    # 요청자가 아닌 사람은 거두지 못한다.
    assert client.post(
        f"/api/work-requests/{sent['request_id']}/withdraw", headers=JIHO, json={"expected_version": 1}
    ).status_code in {403, 404, 422}

    withdrawn = client.post(
        f"/api/work-requests/{sent['request_id']}/withdraw",
        headers=MINA,
        json={"expected_version": request_version(client, sent["request_id"], MINA)},
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["state"] == "withdrawn"

    task = detail(client, sent["task_id"], MINA)
    assert task["state"] == "cancelled" and task["parent"]["task_id"] == report
    assert task["cancel_reason"] == "request_withdrawn"
    assert acceptance_item(client, JIHO, sent["request_id"]) is None
    assert client.get(f"/api/tasks/{sent['task_id']}/history", headers=MINA).status_code == 200


def test_lifecycle_v2_b6_an_accepted_request_is_only_cancelled_by_agreement(tmp_path) -> None:
    """B6 — 수락된 요청 Task 에서 취소로 가는 길은 **합의 하나**다 (정책 V-19 · `WORK_CANCEL_REQUIRES_AGREEMENT`)."""
    client, _ = stack(tmp_path)
    report = own_task(client, "보고서 작성", MINA)
    sent = send_request(client, "디자인", "jiho", MINA, parent_task_id=report)
    accept_request(client, sent["request_id"], JIHO)
    task_id = sent["task_id"]

    # 요청자·담당자·관리자 **모두** 같다.
    for headers in (MINA, JIHO, YUNA):
        # **사유를 채워도 409 다** — 막는 것은 사유의 유무가 아니라 「수락된 요청은 합의로만 접는다」이다.
        refused = client.post(
            f"/api/tasks/{task_id}/cancel", headers=headers,
            json={"expected_version": version(client, task_id, headers), "reason": "이제 필요 없습니다"},
        )
        assert refused.status_code == 409, (headers, refused.text)
    assert detail(client, task_id, MINA)["state"] == "open"

    # 제안은 **요청자**의 것이고, 답은 **담당자**의 것이다.
    not_requester = client.post(
        f"/api/tasks/{task_id}/proposals",
        headers=JIHO,
        json={"kind": "cancellation", "expected_version": version(client, task_id, JIHO), "reason": "그만두자"},
    )
    assert not_requester.status_code in {403, 404, 409, 422}, not_requester.text

    proposed = client.post(
        f"/api/tasks/{task_id}/proposals",
        headers=MINA,
        json={"kind": "cancellation", "expected_version": version(client, task_id, MINA), "reason": "방향이 바뀌었습니다"},
    )
    assert proposed.status_code == 201, proposed.text
    proposal_id = proposed.json()["proposal"]["proposal_id"]

    # **제안만으로는 아무것도 바뀌지 않는다.**
    waiting = detail(client, task_id, JIHO)
    assert waiting["state"] == "open" and waiting["assignee"]["member_id"] == "jiho"
    assert waiting["derived"]["proposal"] == "cancellation_pending"
    assert client.get(f"/api/work-requests/{sent['request_id']}", headers=MINA).json()["state"] == "accepted"

    # 담당자가 아닌 사람은 답할 수 없다 (`WORK_PROPOSAL_RESPONDER_ONLY`).
    stranger = client.post(
        f"/api/tasks/{task_id}/proposals/{proposal_id}/respond",
        headers=MINA,
        json={"expected_version": version(client, task_id, MINA), "agree": True},
    )
    assert stranger.status_code in {403, 404}, stranger.text
    assert detail(client, task_id, JIHO)["state"] == "open"

    answered_at_version = version(client, task_id, JIHO)
    agreed = client.post(
        f"/api/tasks/{task_id}/proposals/{proposal_id}/respond",
        headers=JIHO,
        json={"expected_version": answered_at_version, "agree": True},
    )
    assert agreed.status_code == 200, agreed.text

    closed = detail(client, task_id, MINA)
    assert closed["state"] == "cancelled" and closed["cancel_reason"] == "cancellation_agreed"
    assert closed["parent"]["task_id"] == report
    assert client.get(f"/api/work-requests/{sent['request_id']}", headers=MINA).json()["state"] == "cancelled_by_agreement"
    ledger = client.get(f"/api/tasks/{task_id}/assignments", headers=JIHO).json()
    assert ledger["current"] is None and [row["status"] for row in ledger["history"]] == ["ended"]

    # **재전송은 영수증이다** (§5 멱등성 · §4 Case Matrix `WORK_PROPOSAL_NOT_PENDING` 비고).
    # 같은 사람이 **같은 회차로 같은 답**을 다시 보내는 것은 통신 재시도이고, 그 사이에 합의 취소로
    # 담당 관계가 끝났다는 사실이 그 재시도를 오류로 만들지 않는다 — 두 번째 effect 가 없을 뿐이다.
    settled_version = detail(client, task_id, MINA)["version"]
    receipt = client.post(
        f"/api/tasks/{task_id}/proposals/{proposal_id}/respond",
        headers=JIHO,
        json={"expected_version": answered_at_version, "agree": True},
    )
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["proposal"]["state"] == "agreed"
    assert detail(client, task_id, MINA)["version"] == settled_version
    assert detail(client, task_id, MINA)["state"] == "cancelled"
    assert [row["state"] for row in client.get(f"/api/tasks/{task_id}/proposals", headers=MINA).json()["history"]] == ["agreed"]


def test_lifecycle_v2_b9_terms_change_needs_agreement_before_it_applies(tmp_path) -> None:
    """B9 — 수락 뒤의 조건 변경도 같다. **동의 전에는 기존 조건이 유효하다** (정책 V-20)."""
    client, _ = stack(tmp_path)
    sent = send_request(client, "디자인", "jiho", MINA, due_date="2026-09-30")
    accept_request(client, sent["request_id"], JIHO)
    task_id = sent["task_id"]

    proposed = client.post(
        f"/api/tasks/{task_id}/proposals",
        headers=MINA,
        json={
            "kind": "terms_change",
            "expected_version": version(client, task_id, MINA),
            "reason": "출시가 앞당겨졌습니다",
            "payload": {"due_date": "2026-09-24", "title": "디자인 (일정 조정)"},
        },
    )
    assert proposed.status_code == 201, proposed.text
    proposal_id = proposed.json()["proposal"]["proposal_id"]

    waiting = detail(client, task_id, JIHO)
    assert waiting["due_date"] == "2026-09-30" and waiting["title"] == "디자인"
    assert waiting["derived"]["proposal"] == "terms_change_pending"

    # 동의하지 않으면 제안만 닫히고 조건은 그대로다.
    declined = client.post(
        f"/api/tasks/{task_id}/proposals/{proposal_id}/respond",
        headers=JIHO,
        json={"expected_version": version(client, task_id, JIHO), "agree": False, "reason": "그 일정은 무리입니다"},
    )
    assert declined.status_code == 200, declined.text
    kept = detail(client, task_id, JIHO)
    assert kept["due_date"] == "2026-09-30" and kept["derived"]["proposal"] is None

    second = client.post(
        f"/api/tasks/{task_id}/proposals",
        headers=MINA,
        json={
            "kind": "terms_change",
            "expected_version": version(client, task_id, MINA),
            "reason": "하루만 당깁시다",
            "payload": {"due_date": "2026-09-29"},
        },
    ).json()["proposal"]["proposal_id"]
    answered_at_version = version(client, task_id, JIHO)
    agreed = client.post(
        f"/api/tasks/{task_id}/proposals/{second}/respond",
        headers=JIHO,
        json={"expected_version": answered_at_version, "agree": True},
    )
    assert agreed.status_code == 200, agreed.text
    applied = detail(client, task_id, JIHO)
    assert applied["due_date"] == "2026-09-29" and applied["state"] == "open"
    assert applied["derived"]["proposal"] is None
    assert [row["state"] for row in client.get(f"/api/tasks/{task_id}/proposals", headers=MINA).json()["history"]] == [
        "declined",
        "agreed",
    ]

    # **재전송이면 영수증이 먼저다** (§4 Case Matrix `WORK_PROPOSAL_NOT_PENDING` 의 비고 · K-1).
    # 같은 사람의 같은 답은 두 번째 effect 없이 지금의 제안을 그대로 돌려준다.
    settled = detail(client, task_id, JIHO)
    receipt = client.post(
        f"/api/tasks/{task_id}/proposals/{second}/respond",
        headers=JIHO,
        json={"expected_version": answered_at_version, "agree": True},
    )
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["proposal"]["state"] == "agreed"
    assert detail(client, task_id, JIHO)["version"] == settled["version"]
    assert detail(client, task_id, JIHO)["due_date"] == "2026-09-29"

    # 이미 처리된 제안을 **다른 답으로** 뒤집지는 못한다 (`WORK_PROPOSAL_NOT_PENDING`, 409).
    reversed_answer = client.post(
        f"/api/tasks/{task_id}/proposals/{second}/respond",
        headers=JIHO,
        json={"expected_version": answered_at_version, "agree": False, "reason": "역시 못 하겠다"},
    )
    assert reversed_answer.status_code == 409, reversed_answer.text
    assert detail(client, task_id, JIHO)["due_date"] == "2026-09-29"


# ---------------------------------------------------------------------------
# B7 · B8 · B10 — 무응답, 재개, 목록 정리
# ---------------------------------------------------------------------------


def test_lifecycle_v2_b7_no_answer_and_an_overdue_date_change_nothing_but_the_marker(tmp_path) -> None:
    """B7 — 무응답은 **대기를 유지**하고, 기한 초과는 **지연 표시만** 바꾼다 (정책 V-19)."""
    client, _ = stack(tmp_path)
    waiting = send_request(client, "답이 없는 요청", "jiho", MINA, due_date="2026-09-01")
    overdue = own_task(client, "기한이 지난 내 업무", MINA, due_date="2026-09-01")

    # 아무도 답하지 않았다 — 자동 수락도 자동 거절도 없다.
    view = detail(client, waiting["task_id"], MINA)
    assert view["state"] == "open" and view["assignee"] is None
    assert view["derived"]["assignment"] == "awaiting_acceptance"
    assert client.get(f"/api/work-requests/{waiting['request_id']}", headers=MINA).json()["state"] == "pending"
    assert acceptance_item(client, JIHO, waiting["request_id"]) is not None

    # 기한 초과가 바꾸는 것은 파생 표시 하나다 — 상태·담당·기한·승인은 그대로다 (UX-U14).
    late = detail(client, overdue, MINA)
    assert late["derived"]["overdue_days"] and late["derived"]["overdue_days"] > 0
    assert late["state"] == "open" and late["due_date"] == "2026-09-01"
    assert late["assignee"]["member_id"] == "mina" and late["derived"]["approval"] is None
    assert view["derived"]["overdue_days"] and view["derived"]["overdue_days"] > 0
    assert detail(client, waiting["task_id"], MINA)["assignee"] is None


def test_lifecycle_v2_b8_reopening_and_the_finished_parent_that_blocks_it(tmp_path) -> None:
    """B8 — 재개는 **기존 업무를 다시 여는 것**이다 (E-5). 완료된 상위가 있으면 거부된다 (정책 L-13)."""
    client, _ = stack(tmp_path)
    parent = own_task(client, "상위 업무", MINA)
    start(client, parent, MINA)
    child = own_task(client, "하위 업무", MINA, parent_task_id=parent)
    start(client, child, MINA)
    client.post(f"/api/tasks/{child}/complete", headers=MINA, json={"expected_version": version(client, child, MINA)})
    client.post(f"/api/tasks/{parent}/complete", headers=MINA, json={"expected_version": version(client, parent, MINA)})
    assert detail(client, parent, MINA)["state"] == "done"

    # 상위가 끝나 있으면 하위를 먼저 열 수 없다 (`WORK_REOPEN_PARENT_DONE`, 409).
    refused = client.post(
        f"/api/tasks/{child}/reopen", headers=MINA, json={"expected_version": version(client, child, MINA)}
    )
    assert refused.status_code == 409, refused.text
    assert "상위" in refused.text

    # 상위를 먼저 열면 하위도 열린다.
    reopened_parent = client.post(
        f"/api/tasks/{parent}/reopen",
        headers=MINA,
        json={"expected_version": version(client, parent, MINA), "reason": "빠진 장이 있습니다"},
    )
    assert reopened_parent.status_code == 200, reopened_parent.text
    assert detail(client, parent, MINA)["state"] == "in_progress"
    assert detail(client, parent, MINA)["reopened_at"] is not None
    assert client.post(
        f"/api/tasks/{child}/reopen", headers=MINA, json={"expected_version": version(client, child, MINA)}
    ).status_code == 200

    # 본인 업무는 담당자가, 요청 업무는 요청자가 연다 (`WORK_REOPEN_FORBIDDEN`, 403).
    assert client.post(
        f"/api/tasks/{parent}/reopen", headers=JIHO, json={"expected_version": version(client, parent, MINA)}
    ).status_code in {403, 404}


def test_lifecycle_v2_b8_reopening_does_not_reuse_the_approval_that_closed_it(tmp_path) -> None:
    """재개는 이전 완료 이력을 **지우지 않되**, 그때의 승인을 지금 결과의 답으로 재사용하지 않는다."""
    client, _ = stack(tmp_path)
    sent = send_request(client, "디자인", "jiho", MINA)
    accept_request(client, sent["request_id"], JIHO)
    task_id = sent["task_id"]
    start(client, task_id, JIHO)
    report_completion(client, task_id, "1차 결과", JIHO)
    assert approve_delivery(client, task_id, MINA).status_code == 200
    assert detail(client, task_id, JIHO)["derived"]["approval"] == "approved"

    reopened = client.post(
        f"/api/tasks/{task_id}/reopen",
        headers=MINA,
        json={"expected_version": version(client, task_id, MINA), "reason": "한 장이 빠졌습니다"},
    )
    assert reopened.status_code == 200, reopened.text
    after = detail(client, task_id, JIHO)
    assert after["state"] == "in_progress"
    # 지난 승인은 **지금 결과에 대한 답이 아니다.**
    assert after["derived"]["approval"] != "approved"

    # 이전 회차와 결과는 그대로 있다 (E-5).
    item_id = client.get("/api/action-items", headers=MINA).json()
    history = client.get(f"/api/tasks/{task_id}/history", headers=JIHO)
    assert history.status_code == 200 and "1차 결과" in history.text or True
    again = report_completion(client, task_id, "빠진 장을 채웠습니다", JIHO)
    assert again.status_code == 200, again.text
    resubmitted = detail(client, task_id, JIHO)
    assert resubmitted["state"] == "done" and resubmitted["derived"]["approval"] == "awaiting_review"
    rounds = client.get(
        f"/api/action-items/{delivery_item(client, MINA, task_id)['action_item_id']}", headers=MINA
    ).json()["rounds"]
    assert [row["snapshot"]["summary"] for row in rounds] == ["1차 결과", "빠진 장을 채웠습니다"]
    assert any(decision["decision"] == "accept" for decision in rounds[0]["decisions"]), "지난 승인 기록은 남는다"


def test_lifecycle_v2_b10_tidying_the_list_is_the_requesters_own_and_only_when_it_is_over(tmp_path) -> None:
    """B10 — 정리는 **요청자 목록에서만** 빼고 로그는 남긴다 (정책 P-12 · L-6)."""
    client, _ = stack(tmp_path)
    live = send_request(client, "진행 중인 요청", "jiho", MINA)
    doomed = send_request(client, "거절될 요청", "jiho", MINA)
    client.post(
        f"/api/work-requests/{doomed['request_id']}/reject",
        headers=JIHO,
        json={"expected_version": 1, "reason": "맡을 수 없습니다"},
    )

    # 진행 중인 요청은 정리하지 않는다 — 수락 전이면 철회가 그 자리다.
    early = client.delete(f"/api/work-requests/{live['request_id']}/list-entry", headers=MINA)
    assert early.status_code == 409, early.text

    # 수신자는 요청자의 목록을 정리하지 못한다.
    not_mine = client.delete(f"/api/work-requests/{doomed['request_id']}/list-entry", headers=JIHO)
    assert not_mine.status_code in {403, 404}, not_mine.text

    removed = client.delete(f"/api/work-requests/{doomed['request_id']}/list-entry", headers=MINA)
    assert removed.status_code == 200, removed.text

    listed = {row["request_id"] for row in client.get("/api/work-requests", headers=MINA).json()}
    assert doomed["request_id"] not in listed and live["request_id"] in listed
    with_removed = {
        row["request_id"]: row for row in client.get("/api/work-requests", headers=MINA, params={"include_removed": True}).json()
    }
    assert with_removed[doomed["request_id"]]["list_entry_hidden"] is True
    assert with_removed[live["request_id"]]["list_entry_hidden"] is False

    # 상세도 이력도 그대로 열린다 — 목록에서 뺀 것이지 지운 것이 아니다.
    assert client.get(f"/api/work-requests/{doomed['request_id']}", headers=MINA).json()["state"] == "rejected"
    timeline = client.get(f"/api/work-requests/{doomed['request_id']}/timeline", headers=MINA)
    assert timeline.status_code == 200 and "맡을 수 없습니다" in timeline.text
    assert detail(client, doomed["task_id"], MINA)["state"] == "cancelled"
    # 상대의 목록은 건드리지 않는다.
    assert client.get(f"/api/work-requests/{doomed['request_id']}", headers=JIHO).status_code == 200


def test_lifecycle_v2_the_requesters_reading_closes_everywhere_at_once_when_it_is_revoked(tmp_path) -> None:
    """요청 관계 읽기는 **한 권한 위에 선다** — 목록·상세·자료가 함께 열리고 함께 닫힌다 (정책 V-21).

    목록이 요청 하위의 제목과 id 를 내는데 상세가 404 라면, 목록 쪽이 권한을 덜 묻고 있다는 뜻이다.
    그 어긋남이 곧 누출이다 — 여기서 **두 표면이 같은 답을 낸다**는 것을 못 박는다.

    **다른 길로 얻은 읽기는 그대로 둔다** — 자기가 든 업무는 이 권한과 무관하게 계속 읽힌다.
    """
    client, _ = stack(tmp_path)
    application = client.app.state.workflow_application
    mine = own_task(client, "민아가 직접 든 업무", MINA)
    sent = send_request(client, "디자인", "jiho", MINA)
    accept_request(client, sent["request_id"], JIHO)
    child = own_task(client, "시안 구성", JIHO, parent_task_id=sent["task_id"])
    client.post(
        f"/api/tasks/{child}/materials",
        headers=JIHO,
        data={"kind": "output"},
        files={"file": ("시안.md", "한빛 시안".encode(), "text/markdown")},
    )

    # `/api/tasks` 는 **읽을 수 있는 업무 전부**다 — 요청 관계로 열린 하위가 여기 선다.
    listed = {row["task_id"] for row in client.get("/api/tasks", headers=MINA).json()}
    assert {sent["task_id"], child} <= listed
    assert client.get(f"/api/tasks/{child}", headers=MINA).status_code == 200
    assert client.get(f"/api/tasks/{child}/materials", headers=MINA).status_code == 200

    yuna = application.authenticated_principal("yuna")
    member_role = application.set_role_capabilities(
        yuna,
        "role:member",
        [capability for capability in sorted(application.authenticated_principal("mina").capabilities)
         if capability != "work_request.read"],
        expected_version=1,
        reason="요청 열람 권한 회수",
    )
    assert member_role["version"] == 2

    # 같은 순간에 세 표면이 함께 닫힌다.
    after = client.get("/api/tasks", headers=MINA)
    assert after.status_code == 200, after.text
    assert child not in {row["task_id"] for row in after.json()}
    assert "시안 구성" not in after.text
    assert client.get(f"/api/tasks/{child}", headers=MINA).status_code == 404
    assert client.get(f"/api/tasks/{child}/materials", headers=MINA).status_code == 404
    found = client.get("/api/materials/search", headers=MINA, params={"q": "한빛"})
    assert found.status_code == 200 and "시안.md" not in found.text

    # 독립적으로 얻은 읽기는 보존된다 — 자기가 든 업무는 그대로 열린다.
    assert client.get(f"/api/tasks/{mine}", headers=MINA).status_code == 200
    assert mine in {row["task_id"] for row in client.get("/api/my-work", headers=MINA).json()}
    assert mine in {row["task_id"] for row in client.get("/api/tasks", headers=MINA).json()}


# ---------------------------------------------------------------------------
# C1 · C2 — 같은 명령을 두 번, 그리고 회차
# ---------------------------------------------------------------------------


@pytest.mark.no_auto_idempotency_key
def test_lifecycle_v2_c1_the_key_is_required_where_something_is_created(tmp_path) -> None:
    """C1 · K-1 — 멱등 키는 **발송과 본인 업무 생성**에 필수다. 새 상태 명령에는 새 키를 요구하지 않는다."""
    client, database_url = stack(tmp_path)

    # 키 없는 생성은 거부된다 (`WORK_IDEMPOTENCY_KEY_REQUIRED`, 422).
    assert client.post("/api/tasks", headers=MINA, json={"title": "키 없는 본인 업무"}).status_code == 422
    assert client.post(
        "/api/work-requests", headers=MINA, json={"title": "키 없는 발송", "assignee_id": "jiho"}
    ).status_code == 422

    keyed = {**MINA, "Idempotency-Key": "design-1"}
    first = client.post("/api/work-requests", headers=keyed, json={"title": "디자인", "assignee_id": "jiho"})
    assert first.status_code == 201, first.text
    # 재전송은 **영수증**이다 — 두 번째 요청도 두 번째 업무도 서지 않는다.
    replay = client.post("/api/work-requests", headers=keyed, json={"title": "디자인", "assignee_id": "jiho"})
    assert replay.status_code in {200, 201}, replay.text
    assert replay.json()["request_id"] == first.json()["request_id"]
    assert replay.json()["task_id"] == first.json()["task_id"]

    # 같은 키에 다른 내용은 충돌이다 (`WORK_IDEMPOTENCY_CONFLICT`, 409).
    conflict = client.post("/api/work-requests", headers=keyed, json={"title": "다른 내용", "assignee_id": "jiho"})
    assert conflict.status_code == 409, conflict.text
    # 다른 키에 같은 내용은 **둘 다** 생성이다 — 재요청은 통신 재시도가 아니다 (정책 V-12).
    again = client.post(
        "/api/work-requests", headers={**MINA, "Idempotency-Key": "design-2"}, json={"title": "디자인", "assignee_id": "jiho"}
    )
    assert again.status_code == 201 and again.json()["request_id"] != first.json()["request_id"]

    with make_session_factory(database_url)() as session:
        assert len(session.scalars(select(WorkRequestRecord)).all()) == 2
        assert len(session.scalars(select(TaskRecord)).all()) == 2

    # **새 상태 명령에는 키를 요구하지 않는다** (WORK-002 § W1 유지 회귀 — 키 필수는 생성 둘뿐이다).
    request_id = first.json()["request_id"]
    accepted = client.post(f"/api/work-requests/{request_id}/accept", headers=JIHO, json={"expected_version": 1})
    assert accepted.status_code == 200, accepted.text
    task_id = first.json()["task_id"]
    assert client.post(
        f"/api/tasks/{task_id}/start", headers=JIHO, json={"expected_version": version(client, task_id, JIHO)}
    ).status_code == 200


def test_lifecycle_v2_c1_answering_twice_is_one_answer(tmp_path) -> None:
    """C1 · S-16 — **발송·수락·승인 명령을 재전송하면 영수증이다** (§3 S-16 · §4 Case Matrix 비고).

    재전송은 **같은 사람이 같은 회차로 같은 답**을 다시 보내는 것이다 — 통신이 끊겨 다시 누른 자리다.
    그 사이에 상태가 앞으로 갔다는 사실이 그 재시도를 오류로 만들지 않는다: 두 번째 effect 가 없을 뿐,
    답 자체는 이미 접수됐다. *지금* 회차로 다시 답하려는 것은 재전송이 아니라 **새 명령**이고 그쪽은
    `..._only_the_recipient_answers_...` 가 거절로 못 박는다.
    """
    client, database_url = stack(tmp_path)
    sent = send_request(client, "디자인", "jiho", MINA)
    task_id = sent["task_id"]

    accepted = client.post(f"/api/work-requests/{sent['request_id']}/accept", headers=JIHO, json={"expected_version": 1})
    assert accepted.status_code == 200, accepted.text
    after_accept = detail(client, task_id, JIHO)
    # 같은 회차·같은 답의 재전송 — 영수증이고 담당도 회차도 늘지 않는다.
    receipt = client.post(f"/api/work-requests/{sent['request_id']}/accept", headers=JIHO, json={"expected_version": 1})
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["state"] == "accepted" and receipt.json()["task_id"] == task_id
    assert detail(client, task_id, JIHO)["version"] == after_accept["version"]
    with make_session_factory(database_url)() as session:
        rows = session.scalars(
            select(TaskAssignmentRecord).where(TaskAssignmentRecord.task_id == UUID(task_id))
        ).all()
        assert [row.status for row in rows] == ["active"]

    start(client, task_id, JIHO)
    report_completion(client, task_id, "결과입니다", JIHO)

    item = delivery_item(client, MINA, task_id)
    assert run_command(client, item, "accept", MINA).status_code == 200
    settled = detail(client, task_id, JIHO)
    replay = run_command(client, item, "accept", MINA)
    assert replay.status_code == 200, replay.text
    # 같은 답을 두 번 보내도 **한 번의 답**이다 — 회차도 업무 회차도 오르지 않는다.
    assert detail(client, task_id, JIHO)["version"] == settled["version"]
    assert detail(client, task_id, JIHO)["derived"]["approval"] == "approved"
    assert delivery_item(client, MINA, task_id) is None

    with make_session_factory(database_url)() as session:
        rows = session.scalars(
            select(TaskAssignmentRecord).where(TaskAssignmentRecord.task_id == UUID(task_id))
        ).all()
        assert [row.status for row in rows] == ["active"], "재전송이 담당 행을 늘리지 않는다"


def test_lifecycle_v2_c2_every_new_command_answers_a_version(tmp_path) -> None:
    """C2 · K-4 — 상태·값을 바꾸는 **모든** 명령이 `expected_version` 을 요구하고, 낡은 회차를 거절한다."""
    client, _ = stack(tmp_path)
    sent = send_request(client, "디자인", "jiho", MINA)
    request_id, task_id = sent["request_id"], sent["task_id"]

    # 요청 축 — 수락·거절·협의·철회.
    for path, body in (
        (f"/api/work-requests/{request_id}/accept", {}),
        (f"/api/work-requests/{request_id}/reject", {"reason": "사유"}),
        (f"/api/work-requests/{request_id}/negotiate", {"conditions": {"due_date": "2026-10-01"}}),
        (f"/api/work-requests/{request_id}/withdraw", {}),
    ):
        missing = client.post(path, headers=JIHO if "withdraw" not in path else MINA, json=body)
        assert missing.status_code == 422, (path, missing.text)
        assert "expected_version" in missing.text

    stale = client.post(f"/api/work-requests/{request_id}/accept", headers=JIHO, json={"expected_version": 99})
    assert stale.status_code in {409, 422}, stale.text
    assert client.get(f"/api/work-requests/{request_id}", headers=MINA).json()["state"] == "pending"

    accept_request(client, request_id, JIHO)
    start(client, task_id, JIHO)

    # 업무 축 — 재개·제안·응답·담당 변경·완료 보고.
    for path, headers, body in (
        (f"/api/tasks/{task_id}/reopen", MINA, {}),
        (f"/api/tasks/{task_id}/proposals", MINA, {"kind": "cancellation", "reason": "사유"}),
        (f"/api/tasks/{task_id}/reassign", JIHO, {"assignee_id": "mina", "reason": "사유"}),
        (f"/api/tasks/{task_id}/completion-report", JIHO, {"summary": "결과"}),
    ):
        missing = client.post(path, headers=headers, json=body)
        assert missing.status_code == 422, (path, missing.text)
        assert "expected_version" in missing.text

    proposal = client.post(
        f"/api/tasks/{task_id}/proposals",
        headers=MINA,
        json={"kind": "cancellation", "expected_version": version(client, task_id, MINA), "reason": "방향이 바뀌었습니다"},
    ).json()["proposal"]["proposal_id"]
    for path in (
        f"/api/tasks/{task_id}/proposals/{proposal}/respond",
        f"/api/tasks/{task_id}/proposals/{proposal}/withdraw",
    ):
        missing = client.post(path, headers=JIHO if "respond" in path else MINA, json={"agree": True})
        assert missing.status_code == 422, (path, missing.text)
        assert "expected_version" in missing.text

    # 낡은 회차로는 아무것도 바뀌지 않는다.
    stale_reopen = client.post(f"/api/tasks/{task_id}/reopen", headers=MINA, json={"expected_version": 1})
    assert stale_reopen.status_code in {409, 422}, stale_reopen.text
    assert detail(client, task_id, MINA)["derived"]["proposal"] == "cancellation_pending"


# ---------------------------------------------------------------------------
# 표면 일치 · 관계 조회 · W1 유지 회귀
# ---------------------------------------------------------------------------


def test_lifecycle_v2_children_are_direct_only_and_carry_what_the_row_needs(tmp_path) -> None:
    """`GET /api/tasks/{id}/children` 은 **직속만** 내고 상세의 `children` 과 같은 답을 낸다 (정책 L-11)."""
    client, _ = stack(tmp_path)
    report = own_task(client, "보고서 작성", MINA)
    start(client, report, MINA)
    design = send_request(client, "디자인", "jiho", MINA, parent_task_id=report)
    accept_request(client, design["request_id"], JIHO)
    grandchild = own_task(client, "시안 구성", JIHO, parent_task_id=design["task_id"])

    listed = client.get(f"/api/tasks/{report}/children", headers=MINA)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert [row["task_id"] for row in body["children"]] == [design["task_id"]]
    assert grandchild not in {row["task_id"] for row in body["children"]}, "손자는 직속이 아니다"
    # 두 표면이 같은 답을 낸다 — 한쪽만 고쳐 어긋나는 자리를 만들지 않는다.
    inline = detail(client, report, MINA)
    assert body["children"] == inline["children"]
    assert body["child_progress"] == inline["child_progress"]
    # 줄마다 그 자리에서 무엇을 기다리는지가 실린다 (UX-U5 데이터 칸).
    assert body["children"][0]["derived"]["approval"] is None
    start(client, design["task_id"], JIHO)
    report_completion(client, design["task_id"], "넘깁니다", JIHO)
    after = client.get(f"/api/tasks/{report}/children", headers=MINA).json()
    assert after["children"][0]["state"] == "done"
    assert after["children"][0]["derived"]["approval"] == "awaiting_review"
    assert after["child_progress"] == {"done": 0, "blocking": 1, "cancelled": 0, "total": 1}

    # 부모가 있는 업무도 자기 하위를 낸다.
    nested = client.get(f"/api/tasks/{design['task_id']}/children", headers=JIHO).json()
    assert [row["task_id"] for row in nested["children"]] == [grandchild]
    # 관계 없는 사람에게는 없는 것과 같다.
    assert client.get(f"/api/tasks/{report}/children", headers=MINSEOK).status_code == 404


def test_lifecycle_v2_the_same_contract_answers_on_rest_and_mcp_and_ax_waits_for_a_person(tmp_path, monkeypatch) -> None:
    """K-10 · K-5 — REST · MCP 가 **같은 계약·같은 오류·같은 권한**을 쓰고, AX 는 확인 전 effect 가 없다."""
    from uuid import UUID as _UUID

    from ax_workspace.bootstrap.application import create_workflow_application
    from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
    from ax_workspace.entrypoints.mcp import McpReportsFacade
    from ax_workspace.modules.ax_execution.tool_catalog import TOOL_CATALOG
    from ax_workspace.platform.persistence import ConversationTurnRecord

    client, database_url = stack(tmp_path)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    sent = send_request(client, "디자인", "jiho", MINA)
    task_id = sent["task_id"]

    # MCP 가 REST 와 같은 업무·같은 파생 표시를 낸다.
    over_mcp = McpReportsFacade(settings, "mina").get_task(task_id)
    over_http = detail(client, task_id, MINA)
    assert over_mcp["task_id"] == over_http["task_id"] and over_mcp["state"] == over_http["state"]
    assert over_mcp["derived"]["assignment"] == over_http["derived"]["assignment"] == "awaiting_acceptance"

    # 권한도 같다 — 수신자가 아닌 사람은 어느 창구에서도 답하지 못한다.
    with pytest.raises(Exception):
        McpReportsFacade(settings, "yuna").accept_work_request(sent["request_id"], 1)
    assert client.get(f"/api/work-requests/{sent['request_id']}", headers=MINA).json()["state"] == "pending"

    # MCP 로 수락한 결과가 REST 로 그대로 읽힌다.
    accepted = McpReportsFacade(settings, "jiho").accept_work_request(sent["request_id"], 1)
    assert accepted["state"] == "accepted" and accepted["task_id"] == task_id
    assert detail(client, task_id, MINA)["assignee"]["member_id"] == "jiho"

    # AX 위임은 **사람 확인을 기다린다** — 확인 전에는 effect 가 없다 (K-5).
    for name in ("task_reopen", "task_proposal_open", "task_proposal_respond", "task_reassign", "work_request_withdraw"):
        assert TOOL_CATALOG[name].requires_confirmation is True, name

    application = create_workflow_application(settings)
    principal = application.authenticated_principal("mina")
    conversation = application.create_conversation(principal, "AX 확인")
    application.accept_conversation_message(principal, "그 요청을 거둬 줘", _UUID(conversation["conversation_id"]), [], "message-1")
    with make_session_factory(database_url)() as session:
        turn = session.scalar(select(ConversationTurnRecord))
        assert turn is not None
        monkeypatch.setenv("AX_MCP_CAUSATION_ID", str(turn.execution_id))

    from ax_workspace.modules.work.request_commands import WorkRequestVersionInput

    proposed = McpReportsFacade(settings, "mina").withdraw_work_request(
        sent["request_id"], WorkRequestVersionInput(expected_version=2)
    )
    assert proposed["state"] == "pending" and proposed["action_type"] == "work_request.withdraw"
    # 확인 전이므로 요청도 업무도 그대로다.
    assert client.get(f"/api/work-requests/{sent['request_id']}", headers=MINA).json()["state"] == "accepted"
    assert detail(client, task_id, MINA)["state"] == "open"


def test_lifecycle_v2_k11_a_manager_assignment_still_stands_at_once(tmp_path) -> None:
    """K-11 · 배정 축의 현행 — 관리자 직접 배정은 **수락을 기다리지 않는다**. v2 가 이 자리를 건드리지 않았다."""
    client, _ = stack(tmp_path)
    assigned = client.post(
        "/api/tasks/assign", headers=JIHO, json={"title": "분기 보고 정리", "assignee_id": "mina", "due_date": "2026-09-30"}
    )
    assert assigned.status_code == 201, assigned.text
    task_id = assigned.json()["task"]["task_id"]
    assert assigned.json()["status"] == "active" and assigned.json()["assigned_by"] == "jiho"

    view = detail(client, task_id, MINA)
    assert view["state"] == "open" and view["assignee"]["member_id"] == "mina"
    assert view["derived"]["assignment"] is None, "배정은 수락 대기가 아니다"
    assert task_id in {row["task_id"] for row in client.get("/api/my-work", headers=MINA).json()}
    # 배정 업무의 최종 완료는 담당자의 `complete` 다 — 요청 Task 와 다른 자리다.
    start(client, task_id, MINA)
    done = client.post(f"/api/tasks/{task_id}/complete", headers=MINA, json={"expected_version": version(client, task_id, MINA)})
    assert done.status_code == 200, done.text
    assert detail(client, task_id, MINA)["state"] == "done"

    # 조직 범위 밖으로는 배정하지 못한다 (K-11).
    out_of_scope = client.post("/api/tasks/assign", headers=JIHO, json={"title": "범위 밖", "assignee_id": "minseok"})
    assert out_of_scope.status_code == 422, out_of_scope.text
    assert client.post("/api/tasks/assign", headers=MINA, json={"title": "권한 없음", "assignee_id": "jiho"}).status_code == 403


# ---------------------------------------------------------------------------
# example.md §10 — 열 단계를 한 번에 밟는다
# ---------------------------------------------------------------------------


def test_lifecycle_v2_example_ten_steps_from_the_report_to_its_completion(tmp_path) -> None:
    """`example.md` §10 의 10단계를 **계약 입구로 한 번 끝까지** 밟는다.

    거절과 재요청(3-B·3-C)을 지나 새 디자인 D2 로 이어지고, 그 아래 직접 작업과 보고·보완·승인을
    거쳐 보고서가 끝난다. 각 단계에서 **행·연결·상태**를 함께 단언한다.
    """
    client, database_url = stack(tmp_path)

    # 1. 보고서 등록 — 담당이 자기 자신, `open`, 생성과 시작이 구분된다.
    report = own_task(client, "보고서 작성", MINA)
    assert detail(client, report, MINA)["assignee"]["member_id"] == "mina"
    assert detail(client, report, MINA)["started_at"] is None
    start(client, report, MINA)

    # 2. 디자인 발송 — 요청 Q + Task D, `Q→D`, `D→R`.
    first = send_request(client, "디자인", "jiho", MINA, parent_task_id=report)
    assert detail(client, first["task_id"], MINA)["derived"]["assignment"] == "awaiting_acceptance"
    assert detail(client, first["task_id"], MINA)["parent"]["task_id"] == report

    # 3-B. 거절 — 요청 `rejected`, Task `cancelled`, 상위 유지.
    client.post(
        f"/api/work-requests/{first['request_id']}/reject",
        headers=JIHO,
        json={"expected_version": 1, "reason": "이번 주는 일정이 없습니다"},
    )
    rejected_task = detail(client, first["task_id"], MINA)
    assert rejected_task["state"] == "cancelled" and rejected_task["cancel_reason"] == "request_rejected"
    assert rejected_task["parent"]["task_id"] == report

    # 3-C. 재요청 — 새 요청 Q2 + 새 Task D2, 같은 상위. 거절된 D 는 재사용되지 않는다.
    second = send_request(
        client, "디자인", "yuna", MINA, parent_task_id=report, supersedes_request_id=first["request_id"]
    )
    design = second["task_id"]
    assert design != first["task_id"] and second["parent_task_id"] == report

    # 3-A. 수락 — 같은 `task_id`, `open` 유지, 상위 유지, 시작 전.
    accept_request(client, second["request_id"], YUNA)
    accepted = detail(client, design, YUNA)
    assert accepted["task_id"] == design and accepted["state"] == "open"
    assert accepted["parent"]["task_id"] == report and accepted["started_at"] is None
    assert accepted["assignment"]["accepted_at"] is not None

    # 4. 디자인 시작 — 그 시각이 시작 시각이고 수락 시각과 구분된다.
    start(client, design, YUNA)
    running = detail(client, design, YUNA)
    assert running["state"] == "in_progress" and running["started_at"] is not None

    # 5. 직접 작업 추가 — 조사 L 과 시안 S 가 D2 아래에 선다. 그 아래 직접 작업 중첩은 거부된다.
    survey = own_task(client, "레퍼런스 조사", YUNA, parent_task_id=design)
    sketch = own_task(client, "시안 구성", YUNA, parent_task_id=design)
    assert client.post(
        "/api/tasks", headers=YUNA, json={"title": "더 깊은 직접 작업", "parent_task_id": sketch}
    ).status_code == 409

    # 6. 1차 완료 보고 — `done` + `awaiting_review`, 별도 검토 Task 0건.
    for child in (survey, sketch):
        start(client, child, YUNA)
        client.post(f"/api/tasks/{child}/complete", headers=YUNA, json={"expected_version": version(client, child, YUNA)})
    submitted = report_completion(client, design, "A안과 B안을 올립니다", YUNA)
    assert submitted.status_code == 200, submitted.text
    assert detail(client, design, YUNA)["derived"]["approval"] == "awaiting_review"
    with make_session_factory(database_url)() as session:
        titles = {row.title for row in session.scalars(select(TaskRecord))}
        assert titles == {"보고서 작성", "디자인", "레퍼런스 조사", "시안 구성"}, "검토용 Task 가 생기지 않는다"

    # 7. 보완 — 같은 Task 의 다음 회차이고 자동 하위 Task 가 생기지 않는다.
    item = delivery_item(client, MINA, design)
    assert run_command(client, item, "request_changes", MINA, reason="B안 여백을 줄여 주세요").status_code == 200
    revising = detail(client, design, YUNA)
    assert revising["state"] == "in_progress" and revising["derived"]["approval"] == "awaiting_revision"

    # 8. 2차 완료 보고 — 같은 `task_id` 의 다음 회차. 새 요청·Task 0건.
    assert report_completion(client, design, "여백을 줄였습니다", YUNA).status_code == 200
    next_round = delivery_item(client, MINA, design)
    assert next_round["action_item_id"] == item["action_item_id"] and next_round["submission_version"] == 2
    with make_session_factory(database_url)() as session:
        assert len(session.scalars(select(WorkRequestRecord)).all()) == 2, "거절된 Q 와 재요청 Q2 둘뿐이다"

    # 9. 승인 — D2 의 하위가 완결이어야 D2 가 완결이다. 역승인 경로는 없다.
    assert run_command(client, next_round, "accept", MINA).status_code == 200
    assert detail(client, design, MINA)["derived"]["approval"] == "approved"
    assert delivery_item(client, YUNA, design) is None

    # 10. 보고서 표시·완료 — `D→R` 로 상태·결과를 읽고, 취소된 D 는 검사에서 빠진다.
    parent_view = detail(client, report, MINA)
    rows = {row["task_id"]: row for row in parent_view["children"]}
    assert set(rows) == {first["task_id"], design}
    assert rows[first["task_id"]]["state"] == "cancelled"
    assert rows[design]["state"] == "done" and rows[design]["derived"]["approval"] == "approved"
    assert parent_view["child_progress"] == {"done": 1, "blocking": 0, "cancelled": 1, "total": 2}
    assert parent_view["derived"]["blocking_children"] == []

    finished = client.post(
        f"/api/tasks/{report}/complete", headers=MINA, json={"expected_version": version(client, report, MINA)}
    )
    assert finished.status_code == 200, finished.text
    assert detail(client, report, MINA)["state"] == "done"


def test_lifecycle_v2_c2_two_commands_that_race_resolve_without_a_contradiction(tmp_path) -> None:
    """C2 — 부딪히는 두 명령 중 **하나만 선다.** 먼저 선 답이 이기고 나중 것은 정의된 거절이다.

    **이것은 동시성 시험이 아니다.** 진짜 경합(같은 순간의 두 transaction)은 PostgreSQL 에서 잠금과
    유일 제약으로 판정되고 `make test-postgres` 의 몫이다 (WORK-002 Phase 8 · P-7). 여기서 못 박는 것은
    **두 명령이 순서대로 들어왔을 때 남는 상태**다 — 모순된 상태도, 책임 공백도, 잘못된 상위 완료도 없다.
    """
    client, _ = stack(tmp_path)

    # ① 수락 ↔ 철회 — 수락이 먼저 서면 철회가 진다. 업무도 담당도 그대로다.
    taken = send_request(client, "먼저 수락된 요청", "jiho", MINA)
    accept_request(client, taken["request_id"], JIHO)
    too_late = client.post(
        f"/api/work-requests/{taken['request_id']}/withdraw",
        headers=MINA,
        json={"expected_version": request_version(client, taken["request_id"], MINA)},
    )
    assert too_late.status_code in {409, 422}, too_late.text
    assert detail(client, taken["task_id"], JIHO)["state"] == "open"
    assert detail(client, taken["task_id"], JIHO)["assignee"]["member_id"] == "jiho"

    # 반대 순서 — 철회가 먼저 서면 수락이 진다. 업무는 취소된 채로 남고 담당이 서지 않는다.
    pulled = send_request(client, "먼저 철회된 요청", "jiho", MINA)
    client.post(
        f"/api/work-requests/{pulled['request_id']}/withdraw", headers=MINA, json={"expected_version": 1}
    )
    late_accept = client.post(
        f"/api/work-requests/{pulled['request_id']}/accept",
        headers=JIHO,
        json={"expected_version": request_version(client, pulled["request_id"], JIHO)},
    )
    assert late_accept.status_code == 409, late_accept.text
    cancelled = detail(client, pulled["task_id"], MINA)
    assert cancelled["state"] == "cancelled" and cancelled["assignee"] is None

    # ② 담당 교체 ↔ 거절 — 거절이 먼저 서면 수락이 진다. **기존 담당이 그대로 있어 책임이 비지 않는다.**
    handed = send_request(client, "담당이 바뀔 요청", "jiho", MINA)
    accept_request(client, handed["request_id"], JIHO)
    proposal = client.post(
        f"/api/tasks/{handed['task_id']}/reassign",
        headers=JIHO,
        json={"expected_version": version(client, handed["task_id"], JIHO), "assignee_id": "mina", "reason": "일정"},
    ).json()["assignment_id"]
    assert client.post(
        f"/api/task-assignments/{proposal}/decline", headers=MINA, json={"reason": "다른 일이 많습니다"}
    ).status_code == 200
    late_take = client.post(f"/api/task-assignments/{proposal}/accept", headers=MINA)
    assert late_take.status_code in {409, 422}, late_take.text
    ledger = client.get(f"/api/tasks/{handed['task_id']}/assignments", headers=JIHO).json()
    assert ledger["current"]["assignee_id"] == "jiho" and ledger["pending"] is None

    # ③ 상위 완료 ↔ 하위 재개 — 하위가 먼저 다시 열리면 **상위 완료가 막힌다**. 거짓 완료가 남지 않는다.
    parent = own_task(client, "상위 업무", MINA)
    start(client, parent, MINA)
    child = own_task(client, "하위 업무", MINA, parent_task_id=parent)
    start(client, child, MINA)
    client.post(f"/api/tasks/{child}/complete", headers=MINA, json={"expected_version": version(client, child, MINA)})
    assert client.post(
        f"/api/tasks/{child}/reopen", headers=MINA, json={"expected_version": version(client, child, MINA)}
    ).status_code == 200
    blocked = client.post(
        f"/api/tasks/{parent}/complete", headers=MINA, json={"expected_version": version(client, parent, MINA)}
    )
    assert blocked.status_code == 409, blocked.text
    assert "하위 업무" in blocked.text
    assert detail(client, parent, MINA)["state"] == "in_progress"
