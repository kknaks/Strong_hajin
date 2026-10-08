"""사건 × 관계 68행 — 행 id 가 시험 이름에 있다 (SPEC-011 §4.2 · AC-07 · WORK-013 WP2-BE 부록 2).

「알림」 행은 그 사람에게 한 줄이 서는 것을, 「없음」 행은 **그 사건으로 그 사람에게 아무 줄도 서지 않는 것**을 고정한다.
업무 · 회의 행은 실제 HTTP 입구를 부르고, 메시지 행은 연동 워커와 같은 저장 자리(`save_messages` · 카톡 업로드)를 지난다.
회의록 완료 · 실패(M09 · M10 · M11)는 `test_meeting_finalize.py` 가 합성 잡으로 고정한다.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
import pytest

from ax_workspace.platform.persistence import NotificationRecord, make_session_factory

from test_task_lifecycle_v2_support import (
    JIHO,
    MINA,
    YUNA,
    HYEON,
    accept_request,
    approve_delivery,
    delivery_item,
    report_completion,
    run_command,
    send_request,
    stack,
    start,
    version,
)


def _rows(database_url: str, member: str) -> list[NotificationRecord]:
    with make_session_factory(database_url)() as session:
        return list(session.query(NotificationRecord).filter_by(recipient_member_id=member).order_by(NotificationRecord.seq))


class Ledger:
    """사건 하나 앞뒤로 회원마다 새로 선 줄을 센다."""

    def __init__(self, database_url: str) -> None:
        self._url = database_url
        self._seen: dict[str, set[str]] = {}

    def mark(self, *members: str) -> None:
        for member in members:
            self._seen[member] = {str(row.id) for row in _rows(self._url, member)}

    def new(self, member: str) -> list[NotificationRecord]:
        before = self._seen.get(member, set())
        return [row for row in _rows(self._url, member) if str(row.id) not in before]


EVERYONE = ("mina", "jiho", "yuna", "hyeon", "minseok", "sora")


@pytest.fixture
def world(tmp_path):
    client, database_url = stack(tmp_path)
    ledger = Ledger(database_url)
    ledger.mark(*EVERYONE)
    return client, database_url, ledger


def _only(ledger: Ledger, member: str, kind: str, relation: str) -> NotificationRecord:
    rows = ledger.new(member)
    assert [(row.kind, row.relation) for row in rows] == [(kind, relation)], [(row.kind, row.relation) for row in rows]
    return rows[0]


def _none(ledger: Ledger, *members: str) -> None:
    for member in members:
        assert ledger.new(member) == [], (member, [(row.kind, row.relation) for row in ledger.new(member)])


def _request(client: TestClient, ledger: Ledger, title: str = "검토 요청", **body: Any) -> dict[str, Any]:
    made = send_request(client, title, "jiho", MINA, **body)
    ledger.mark(*EVERYONE)
    return made


def _request_version(client: TestClient, request_id: str, headers=MINA) -> int:
    return int(client.get(f"/api/work-requests/{request_id}", headers=headers).json()["version"])


# ── 업무 W01 ~ W38 ──────────────────────────────────────────────────────────────────────


def test_w01_w02_w03_w04_a_sent_request_reaches_only_the_assignee(world) -> None:
    client, _, ledger = world
    made = send_request(client, "배포 체크 요청", "jiho", MINA, cc_member_ids=["hyeon"], approver_id="yuna")
    row = _only(ledger, "jiho", "work.request_received", "assignee")  # W01
    assert row.theme == "work" and row.item == "request" and row.data == {"resubmitted": False}
    assert row.resource_title == "배포 체크 요청" and row.actor == {"member_id": "mina", "display_name": "민아 (구성원)"}
    assert row.target == {"surface": "work", "task_id": made["task_id"]}
    _none(ledger, "mina")  # W02 — 행위자
    _none(ledger, "hyeon")  # W03 — CC 는 댓글만
    _none(ledger, "yuna")  # W04 — 결재자


def test_w05_w06_accept_and_reject_go_to_the_requester(world) -> None:
    client, _, ledger = world
    made = _request(client, ledger)
    accept_request(client, made["request_id"], JIHO)
    accepted = _only(ledger, "mina", "work.request_answered", "requester")  # W05
    assert accepted.data == {"answer": "accepted"} and accepted.item == "answer"
    _none(ledger, "jiho")  # W06 — 행위자

    second = _request(client, ledger, "다른 요청")
    rejected = client.post(
        f"/api/work-requests/{second['request_id']}/reject", headers=JIHO,
        json={"expected_version": _request_version(client, second["request_id"], JIHO), "reason": "이번 주는 어렵습니다"},
    )
    assert rejected.status_code == 200, rejected.text
    assert _only(ledger, "mina", "work.request_answered", "requester").data == {"answer": "rejected"}
    _none(ledger, "jiho")


def test_w07_negotiation_goes_to_the_requester(world) -> None:
    client, _, ledger = world
    made = _request(client, ledger)
    negotiated = client.post(
        f"/api/work-requests/{made['request_id']}/negotiate", headers=JIHO,
        json={"expected_version": _request_version(client, made["request_id"], JIHO), "conditions": {"note": "다음 주면 가능"}},
    )
    assert negotiated.status_code == 200, negotiated.text
    assert _only(ledger, "mina", "work.request_answered", "requester").data == {"answer": "negotiated"}
    _none(ledger, "jiho")


def test_w08_amending_before_acceptance_goes_to_the_assignee(world) -> None:
    client, _, ledger = world
    made = _request(client, ledger)
    amended = client.post(
        f"/api/work-requests/{made['request_id']}/amend", headers=MINA,
        json={"expected_version": made["version"], "title": "고친 요청"},
    )
    assert amended.status_code == 200, amended.text
    assert _only(ledger, "jiho", "work.changed", "assignee").data == {"change": "amended"}
    _none(ledger, "mina")


def test_w09_resubmitting_goes_to_the_assignee_as_a_request_again(world) -> None:
    client, _, ledger = world
    made = _request(client, ledger)
    negotiated = client.post(
        f"/api/work-requests/{made['request_id']}/negotiate", headers=JIHO,
        json={"expected_version": _request_version(client, made["request_id"], JIHO), "conditions": {"note": "다음 주면 가능"}},
    ).json()
    ledger.mark(*EVERYONE)
    resubmitted = client.post(
        f"/api/work-requests/{made['request_id']}/resubmit", headers=MINA,
        json={"expected_version": negotiated["version"], "due_date": "2027-09-15"},
    )
    assert resubmitted.status_code == 200, resubmitted.text
    assert _only(ledger, "jiho", "work.request_received", "assignee").data == {"resubmitted": True}
    _none(ledger, "mina")


def test_w10_withdrawing_goes_to_the_assignee(world) -> None:
    client, _, ledger = world
    made = _request(client, ledger)
    withdrawn = client.post(
        f"/api/work-requests/{made['request_id']}/withdraw", headers=MINA,
        json={"expected_version": _request_version(client, made["request_id"])},
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert _only(ledger, "jiho", "work.changed", "assignee").data == {"change": "withdrawn"}
    _none(ledger, "mina")


def test_w11_reference_read_evidence_and_list_cleanup_make_nothing(world) -> None:
    client, _, ledger = world
    made = _request(client, ledger, cc_member_ids=["hyeon"])
    assert client.post(f"/api/work-requests/{made['request_id']}/read", headers=HYEON).status_code == 200
    _none(ledger, *EVERYONE)  # 참고 읽음
    adopted = client.post(
        f"/api/work-requests/{made['request_id']}/evidence", headers=JIHO, files={"file": ("근거.txt", b"one", "text/plain")}
    )
    assert adopted.status_code == 201, adopted.text
    _none(ledger, *EVERYONE)  # 자료 채택(검수 W-4)
    rejected = client.post(
        f"/api/work-requests/{made['request_id']}/reject", headers=JIHO,
        json={"expected_version": _request_version(client, made["request_id"], JIHO), "reason": "어렵습니다"},
    )
    assert rejected.status_code == 200, rejected.text
    ledger.mark(*EVERYONE)
    removed = client.delete(f"/api/work-requests/{made['request_id']}/list-entry", headers=MINA)
    assert removed.status_code in {200, 204}, removed.text
    _none(ledger, *EVERYONE)  # 목록 정리(검수 W-4)


def _assigned(client: TestClient, ledger: Ledger, *, by=YUNA, to: str = "mina", title: str = "배정 업무") -> dict[str, Any]:
    made = client.post("/api/tasks/assign", headers=by, json={"title": title, "assignee_id": to})
    assert made.status_code == 201, made.text
    ledger.mark(*EVERYONE)
    return made.json()["task"]


def test_w12_a_direct_assignment_goes_to_the_new_holder(world) -> None:
    client, _, ledger = world
    made = client.post("/api/tasks/assign", headers=JIHO, json={"title": "분기 보고", "assignee_id": "mina"})
    assert made.status_code == 201, made.text
    row = _only(ledger, "mina", "work.assigned", "assignee")
    assert row.data == {"mode": "assigned"} and row.item == "assign"
    _none(ledger, "jiho")


def test_w13_w14_w15_reassigning_reaches_both_holders_and_the_answer_reaches_the_assigner(world) -> None:
    client, _, ledger = world
    task = _assigned(client, ledger)
    proposed = client.post(
        f"/api/tasks/{task['task_id']}/reassign", headers=YUNA,
        json={"expected_version": version(client, task["task_id"], YUNA), "assignee_id": "jiho"},
    )
    assert proposed.status_code == 200, proposed.text
    assert _only(ledger, "jiho", "work.assigned", "assignee").data == {"mode": "change_proposed"}  # W13
    displaced = _only(ledger, "mina", "work.assigned", "assignee")  # W14
    # 화면 문장 「…담당이 {새 담당}님으로 바뀝니다」 의 이름은 서버가 만들 때 저장한다(검수 F-1).
    assert displaced.data == {"mode": "displaced", "new_assignee_id": "jiho", "new_assignee_name": "지호 (팀장)"}
    _none(ledger, "yuna")
    ledger.mark(*EVERYONE)
    accepted = client.post(f"/api/task-assignments/{proposed.json()['assignment_id']}/accept", headers=JIHO)
    assert accepted.status_code == 200, accepted.text
    assert _only(ledger, "yuna", "work.assignment_answered", "assigner").data == {"answer": "accepted"}  # W15
    _none(ledger, "jiho")


def test_w15_declining_a_reassignment_reaches_the_assigner(world) -> None:
    client, _, ledger = world
    task = _assigned(client, ledger)
    proposed = client.post(
        f"/api/tasks/{task['task_id']}/reassign", headers=YUNA,
        json={"expected_version": version(client, task["task_id"], YUNA), "assignee_id": "jiho"},
    ).json()
    ledger.mark(*EVERYONE)
    declined = client.post(f"/api/task-assignments/{proposed['assignment_id']}/decline", headers=JIHO, json={"reason": "이번 주 불가"})
    assert declined.status_code == 200, declined.text
    assert _only(ledger, "yuna", "work.assignment_answered", "assigner").data == {"answer": "rejected"}


def test_w13_handing_unheld_project_work_to_someone(world) -> None:
    client, _, ledger = world
    project = client.post("/api/projects", headers=JIHO, json={"name": "알림 프로젝트"}).json()["project_id"]
    assert client.post(f"/api/projects/{project}/members", headers=JIHO, json={"member_id": "mina"}).status_code == 201
    planned = client.post(f"/api/projects/{project}/tasks", headers=JIHO, json={"title": "계획만 선 일"})
    assert planned.status_code == 201, planned.text
    ledger.mark(*EVERYONE)
    task_id = planned.json()["task_id"]
    handed = client.post(
        f"/api/tasks/{task_id}/reassign", headers=JIHO,
        json={"expected_version": version(client, task_id, JIHO), "assignee_id": "mina"},
    )
    assert handed.status_code == 200, handed.text
    assert _only(ledger, "mina", "work.assigned", "assignee").data == {"mode": "handed_over"}
    _none(ledger, "jiho")


def test_w17_w18_w19_w20_only_the_holder_edits_so_date_changes_reach_nobody_today(world) -> None:
    """W17 — 기한 · 시작일 수정은 담당에게(행위자면 없음). **지금 코드에서 업무 수정은 활성 담당만 부를 수 있다**
    (`WT` `task()` — 활성 담당) — 그래서 행위자 = 담당이고 원칙 ① 로 늘 없다. 요청자(W18) · 결재자(W19) · 그 밖의 칸(W20)은 없다."""
    client, _, ledger = world
    made = _request(client, ledger, approver_id="yuna")
    accept_request(client, made["request_id"], JIHO)
    ledger.mark(*EVERYONE)
    for body in ({"due_date": "2027-10-01"}, {"start_date": "2027-09-01"}, {"title": "제목만"}, {"description": "설명만"}):
        edited = client.patch(
            f"/api/tasks/{made['task_id']}", headers=JIHO, json={"expected_version": version(client, made["task_id"], JIHO), **body}
        )
        assert edited.status_code == 200, edited.text
    _none(ledger, *EVERYONE)


def test_w21_w22_w23_moving_adding_children_and_state_changes_make_nothing(world) -> None:
    client, _, ledger = world
    parent = client.post("/api/tasks", headers=MINA, json={"title": "상위"}).json()
    child = client.post("/api/tasks", headers=MINA, json={"title": "하위", "parent_task_id": parent["task_id"]})
    assert child.status_code == 201, child.text
    started = client.post(f"/api/tasks/{parent['task_id']}/start", headers=MINA, json={"expected_version": version(client, parent["task_id"], MINA)})
    assert started.status_code == 200, started.text
    _none(ledger, *EVERYONE)


def test_w24_cancelling_by_the_holder_makes_nothing_because_the_holder_is_the_actor(world) -> None:
    """W24 — 취소는 담당에게. 직접 취소도 활성 담당만 부를 수 있어(`transition`) 행위자 = 담당 → 원칙 ①."""
    client, _, ledger = world
    task = _assigned(client, ledger, by=JIHO)
    cancelled = client.post(
        f"/api/tasks/{task['task_id']}/cancel", headers=MINA,
        json={"expected_version": version(client, task["task_id"], MINA), "reason": "필요 없어졌다"},
    )
    assert cancelled.status_code == 200, cancelled.text
    _none(ledger, *EVERYONE)


def _delivered(client: TestClient, ledger: Ledger, **body: Any) -> dict[str, Any]:
    made = _request(client, ledger, **body)
    accept_request(client, made["request_id"], JIHO)
    start(client, made["task_id"], JIHO)
    ledger.mark(*EVERYONE)
    reported = report_completion(client, made["task_id"], "끝냈습니다", JIHO)
    assert reported.status_code == 200, reported.text
    return made


def test_w25_w26_a_completion_report_reaches_the_requester_only(world) -> None:
    client, _, ledger = world
    made = _delivered(client, ledger, cc_member_ids=["hyeon"])
    row = _only(ledger, "mina", "work.completion_reported", "requester")  # W25
    assert row.item == "report" and row.target == {"surface": "work", "task_id": made["task_id"]}
    _none(ledger, "hyeon", "jiho")  # W26 — CC · 행위자


def test_w27_w28_rework_reaches_the_reporter_and_approval_makes_nothing(world) -> None:
    client, _, ledger = world
    made = _delivered(client, ledger)
    ledger.mark(*EVERYONE)
    asked = run_command(client, delivery_item(client, MINA, made["task_id"]), "request_changes", MINA, reason="여백을 줄여 주세요")
    assert asked.status_code == 200, asked.text
    row = _only(ledger, "jiho", "work.rework_requested", "assignee")  # W28
    assert row.data == {"comment": "여백을 줄여 주세요"}
    _none(ledger, "mina")
    reported = report_completion(client, made["task_id"], "고쳤습니다", JIHO)
    assert reported.status_code == 200, reported.text
    ledger.mark(*EVERYONE)
    assert approve_delivery(client, made["task_id"], MINA).status_code == 200
    _none(ledger, *EVERYONE)  # W27


def test_w29_w30_reopening_by_the_requester_reaches_the_holder(world) -> None:
    """W29 — 요청자가 열면 담당. W30(담당이 열면 요청자)은 **지금 권한으로 서지 않는다** — 요청 업무의 재개는 요청자만 할 수
    있고(`_require_may_reopen`), 본인 · 배정 업무에는 요청자가 없다."""
    client, _, ledger = world
    made = _delivered(client, ledger)
    assert approve_delivery(client, made["task_id"], MINA).status_code == 200
    ledger.mark(*EVERYONE)
    reopened = client.post(
        f"/api/tasks/{made['task_id']}/reopen", headers=MINA, json={"expected_version": version(client, made["task_id"], MINA)}
    )
    assert reopened.status_code == 200, reopened.text
    assert _only(ledger, "jiho", "work.changed", "assignee").data == {"change": "reopened"}
    _none(ledger, "mina")


def test_w29_reopening_by_the_requester_does_not_reach_the_meeting_promoter(world) -> None:
    """W29 의 받는 사람은 **담당뿐**이다 — 요청자 자리에 앉은 회의 승격자도 받지 않는다(검수 W-2)."""
    from uuid import UUID

    from ax_workspace.platform.persistence import WorkRequestRecord

    client, database_url, ledger = world
    made = _delivered(client, ledger)
    with make_session_factory(database_url)() as session:
        session.get(WorkRequestRecord, UUID(made["request_id"])).promoted_by_member_id = "hyeon"
        session.commit()
    assert approve_delivery(client, made["task_id"], MINA).status_code == 200
    ledger.mark(*EVERYONE)
    reopened = client.post(
        f"/api/tasks/{made['task_id']}/reopen", headers=MINA, json={"expected_version": version(client, made["task_id"], MINA)}
    )
    assert reopened.status_code == 200, reopened.text
    _only(ledger, "jiho", "work.changed", "assignee")
    _none(ledger, "hyeon", "mina")


def _accepted(client: TestClient, ledger: Ledger) -> dict[str, Any]:
    made = _request(client, ledger)
    accept_request(client, made["request_id"], JIHO)
    ledger.mark(*EVERYONE)
    return made


def _propose(client: TestClient, task_id: str, kind: str):
    body: dict[str, Any] = {"kind": kind, "expected_version": version(client, task_id, MINA), "reason": "사정이 바뀌었습니다"}
    if kind == "terms_change":
        body["payload"] = {"due_date": "2027-12-01"}
    proposed = client.post(f"/api/tasks/{task_id}/proposals", headers=MINA, json=body)
    assert proposed.status_code == 201, proposed.text
    return proposed.json()["proposal"]


def test_w31_w33_a_terms_proposal_reaches_the_holder_and_the_answer_reaches_the_proposer(world) -> None:
    client, _, ledger = world
    made = _accepted(client, ledger)
    proposal = _propose(client, made["task_id"], "terms_change")
    row = _only(ledger, "jiho", "work.changed", "assignee")  # W31
    assert row.data["change"] == "condition_proposed" and row.data["after"] == "2027-12-01"
    _none(ledger, "mina")
    ledger.mark(*EVERYONE)
    agreed = client.post(
        f"/api/tasks/{made['task_id']}/proposals/{proposal['proposal_id']}/respond", headers=JIHO,
        json={"expected_version": version(client, made["task_id"], JIHO), "agree": True},
    )
    assert agreed.status_code == 200, agreed.text
    assert _only(ledger, "mina", "work.proposal_answered", "requester").data == {"answer": "agreement_changed"}  # W33
    _none(ledger, "jiho")


def test_w32_w33_a_cancellation_proposal_and_its_refusal(world) -> None:
    client, _, ledger = world
    made = _accepted(client, ledger)
    proposal = _propose(client, made["task_id"], "cancellation")
    assert _only(ledger, "jiho", "work.changed", "assignee").data == {"change": "cancel_proposed"}  # W32
    ledger.mark(*EVERYONE)
    declined = client.post(
        f"/api/tasks/{made['task_id']}/proposals/{proposal['proposal_id']}/respond", headers=JIHO,
        json={"expected_version": version(client, made["task_id"], JIHO), "agree": False, "reason": "계속합니다"},
    )
    assert declined.status_code == 200, declined.text
    assert _only(ledger, "mina", "work.proposal_answered", "requester").data == {"answer": "declined"}


def test_w33_an_agreed_cancellation(world) -> None:
    client, _, ledger = world
    made = _accepted(client, ledger)
    proposal = _propose(client, made["task_id"], "cancellation")
    ledger.mark(*EVERYONE)
    agreed = client.post(
        f"/api/tasks/{made['task_id']}/proposals/{proposal['proposal_id']}/respond", headers=JIHO,
        json={"expected_version": version(client, made["task_id"], JIHO), "agree": True},
    )
    assert agreed.status_code == 200, agreed.text
    assert _only(ledger, "mina", "work.proposal_answered", "requester").data == {"answer": "agreement_cancelled"}


def test_w34_withdrawing_a_proposal_makes_nothing(world) -> None:
    client, _, ledger = world
    made = _accepted(client, ledger)
    proposal = _propose(client, made["task_id"], "cancellation")
    ledger.mark(*EVERYONE)
    withdrawn = client.post(
        f"/api/tasks/{made['task_id']}/proposals/{proposal['proposal_id']}/withdraw", headers=MINA,
        json={"expected_version": version(client, made["task_id"], MINA)},
    )
    assert withdrawn.status_code == 200, withdrawn.text
    _none(ledger, *EVERYONE)


def test_w36_a_request_comment_reaches_everyone_on_the_thread_but_its_author(world) -> None:
    client, _, ledger = world
    made = _request(client, ledger, cc_member_ids=["hyeon"])
    commented = client.post(f"/api/work-requests/{made['request_id']}/comments", headers=JIHO, json={"body": "내일까지 드릴게요"})
    assert commented.status_code == 201, commented.text
    # 업무 댓글은 **기본 꺼짐** 항목이다(§4.4) — 켠 사람에게만 선다. 기본값에서는 아무도 받지 않는다.
    _none(ledger, *EVERYONE)
    for headers in (MINA, HYEON):
        current = client.get("/api/me/notification-settings", headers=headers).json()
        current["themes"]["work"]["items"]["comment"] = True
        assert client.put("/api/me/notification-settings", headers=headers, json=current).status_code == 200
    ledger.mark(*EVERYONE)
    again = client.post(f"/api/work-requests/{made['request_id']}/comments", headers=JIHO, json={"body": "자료 붙였습니다"})
    assert again.status_code == 201, again.text
    assert _only(ledger, "mina", "work.commented", "requester").data == {"excerpt": "자료 붙였습니다"}
    assert _only(ledger, "hyeon", "work.commented", "cc").item == "comment"
    _none(ledger, "jiho")


def test_w37_checklists_make_nothing(world) -> None:
    client, _, ledger = world
    task = client.post("/api/tasks", headers=MINA, json={"title": "체크리스트", "checklist": ["하나"]})
    assert task.status_code == 201, task.text
    _none(ledger, *EVERYONE)


# ── 생성 규칙 (§4.3) ─────────────────────────────────────────────────────────────────────


def test_a_switched_off_item_makes_no_row_and_turning_it_back_on_does_not_backfill(world) -> None:
    client, database_url, ledger = world
    settings = client.get("/api/me/notification-settings", headers=JIHO).json()
    settings["themes"]["work"]["items"]["request"] = False
    assert client.put("/api/me/notification-settings", headers=JIHO, json=settings).status_code == 200
    send_request(client, "꺼진 동안", "jiho", MINA)
    _none(ledger, "jiho")
    settings = client.get("/api/me/notification-settings", headers=JIHO).json()
    settings["themes"]["work"]["on"] = False
    settings["themes"]["work"]["items"]["request"] = True
    assert client.put("/api/me/notification-settings", headers=JIHO, json=settings).status_code == 200
    send_request(client, "테마가 꺼진 동안", "jiho", MINA)
    _none(ledger, "jiho")
    settings["themes"]["work"]["on"] = True
    settings["enabled"] = False
    settings["version"] = client.get("/api/me/notification-settings", headers=JIHO).json()["version"]
    assert client.put("/api/me/notification-settings", headers=JIHO, json=settings).status_code == 200
    send_request(client, "전체가 꺼진 동안", "jiho", MINA)
    _none(ledger, "jiho")
    settings["enabled"] = True
    settings["version"] += 1
    assert client.put("/api/me/notification-settings", headers=JIHO, json=settings).status_code == 200
    _none(ledger, "jiho")  # 켜도 꺼진 동안의 사건은 생기지 않는다
    send_request(client, "다시 켠 뒤", "jiho", MINA)
    assert _only(ledger, "jiho", "work.request_received", "assignee").resource_title == "다시 켠 뒤"


def test_an_inactive_recipient_gets_nothing(world) -> None:
    client, database_url, ledger = world
    from ax_workspace.platform.persistence import MemberRecord

    with make_session_factory(database_url)() as session:
        session.get(MemberRecord, "mina").employment_state = "left"
        session.commit()
    assigned = client.post("/api/tasks/assign", headers=YUNA, json={"title": "퇴사자에게", "assignee_id": "mina"})
    if assigned.status_code == 201:
        _none(ledger, "mina")


def _ax_assign(client: TestClient, database_url: str, ledger: Ledger) -> dict[str, Any]:
    """판단함의 업무 배정 제안 — 위임 턴이 만드는 그대로(`test_project_membership_follows_work.py` 와 같은 길)."""
    from uuid import UUID

    from ax_workspace.platform.persistence import ConversationTurnRecord

    application = client.app.state.workflow_application
    conversation = client.post("/api/conversations", headers=YUNA, json={"title": "배정"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**YUNA, "Idempotency-Key": "notify-ax-assign"}, json={"body": "배정 제안해줘", "context": []},
    )
    assert accepted.status_code == 202, accepted.text
    with make_session_factory(database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"])).execution_id
    proposal = application.propose_action(
        application.authenticated_principal("yuna"), execution_id, "task.assign", "업무 배정 확인",
        {"title": "AX 가 고른 배정", "assignee_id": "hyeon", "due_date": "2027-09-30"},
    )
    ledger.mark(*EVERYONE)
    item = client.get(f"/api/action-items/{proposal['action_id']}", headers=YUNA).json()
    confirmed = client.post(
        f"/api/action-items/{proposal['action_id']}/commands/confirm", headers=YUNA,
        json={"expected_version": item["expected_version"], "base_submission_version": 1},
    )
    assert confirmed.status_code == 200, confirmed.text
    return proposal


def test_w38_confirming_an_ax_proposal_notifies_only_through_the_work_it_runs(world) -> None:
    """W38 — 판단 승인 자체는 행위자(소유자) 몫이라 없다. 그 실행이 배정이면 W12 를 탄다(받는 사람 = 새 담당)."""
    client, database_url, ledger = world
    _ax_assign(client, database_url, ledger)
    assert _only(ledger, "hyeon", "work.assigned", "assignee").data == {"mode": "assigned"}
    _none(ledger, "yuna")


def test_w16_withdrawing_a_pending_assignment_makes_nothing(world) -> None:
    from legacy_acceptance import make_assignment_look_pending

    client, database_url, ledger = world
    proposal = _ax_assign(client, database_url, ledger)
    [sent] = [row for row in client.get("/api/task-assignments/sent", headers=YUNA).json() if row["task"]["title"] == "AX 가 고른 배정"]
    make_assignment_look_pending(database_url, sent["assignment_id"])
    ledger.mark(*EVERYONE)
    waiting = client.get(f"/api/action-items/{proposal['action_id']}", headers=YUNA).json()
    cancelled = client.post(
        f"/api/action-items/{proposal['action_id']}/commands/cancel_assignment", headers=YUNA,
        json={"expected_version": waiting["expected_version"]},
    )
    assert cancelled.status_code == 200, cancelled.text
    _none(ledger, *EVERYONE)


# ── 메시지 X01 ~ X15 ────────────────────────────────────────────────────────────────────

SORA = {"X-Demo-Persona": "sora"}


@pytest.fixture
def inbox_world(tmp_path):
    from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
    from ax_workspace.entrypoints.http import create_app
    from ax_workspace.entrypoints.reset_demo import reset_database
    from ax_workspace.platform.external_tokens import generate_key
    from ax_workspace.platform.persistence import ExternalIntegrationRecord, ExternalRoomRecord

    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(
        RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"),
        external_token_encryption_key=generate_key(), external_channel_storage_dir=str(tmp_path / "external"),
    )
    client = TestClient(create_app(settings))
    sessions = make_session_factory(database_url)
    now = datetime(2027, 1, 5, tzinfo=UTC)
    with sessions() as session:
        mail = ExternalIntegrationRecord(
            member_id="mina", kind="mail", status="connected", account_key="mina@company.example", display_name="mina@company.example",
            account_meta={}, created_at=now, updated_at=now,
        )
        slack = ExternalIntegrationRecord(
            member_id="mina", kind="slack", status="connected", account_key="T1", display_name="회사", account_meta={"user_id": "U-ME"},
            created_at=now, updated_at=now,
        )
        session.add_all([mail, slack])
        session.flush()
        rooms = {
            room_type: ExternalRoomRecord(
                integration_id=slack.id, external_id=f"C-{room_type}", room_type=room_type, name=f"방 {room_type}", status="live",
                created_at=now, updated_at=now,
            )
            for room_type in ("channel", "dm", "group_dm", "private")
        }
        session.add_all(rooms.values())
        session.commit()
        ids = {"mail": str(mail.id), "slack": str(slack.id), **{f"room_{key}": str(row.id) for key, row in rooms.items()}}
    ledger = Ledger(database_url)
    ledger.mark(*EVERYONE)
    return client, database_url, ledger, ids


def _store(database_url: str):
    from ax_workspace.platform.external_channels_sync_store import SqlAlchemyExternalChannelsSyncStore

    return SqlAlchemyExternalChannelsSyncStore(make_session_factory(database_url))


_SEQUENCE = iter(range(1, 10_000))


def _mail(database_url: str, integration_id: str, *, sender: str, to: str, cc: str = "", subject: str = "견적", mode: str = "live") -> str:
    from ax_workspace.modules.external_channels.sync_messages import NormalizedMessage

    key = f"g{next(_SEQUENCE)}"
    headers = [{"name": "From", "value": sender}, {"name": "To", "value": to}, {"name": "Subject", "value": subject}]
    if cc:
        headers.append({"name": "Cc", "value": cc})
    message = NormalizedMessage(
        container_key="", external_key=key, thread_key=key, sent_at=datetime.now(UTC), subject=subject, author=sender,
        preview="안녕하세요", raw={"id": key, "payload": {"headers": headers}},
    )
    assert _store(database_url).save_messages(integration_id, "mail", None, [message], mode=mode) == 1
    return key


def _slack(database_url: str, ids: dict, room: str, *, user: str = "U-OTHER", text: str = "안녕", author: str | None = "동료", mode: str = "live") -> str:
    from ax_workspace.modules.external_channels.sync_messages import NormalizedMessage

    ts = f"1790000{next(_SEQUENCE):03d}.000100"
    message = NormalizedMessage(
        container_key=f"C-{room}", external_key=ts, thread_key=None, sent_at=datetime.now(UTC), subject=None, author=author,
        preview=text, raw={"ts": ts, "user": user, "text": text},
    )
    assert _store(database_url).save_messages(ids["slack"], "slack", ids[f"room_{room}"], [message], mode=mode) == 1
    return ts


def test_x01_x02_x03_mail_tags_follow_where_i_am_in_the_headers(inbox_world) -> None:
    _, database_url, ledger, ids = inbox_world
    _mail(database_url, ids["mail"], sender="Partner <partner@example.com>", to="Mina <MINA@company.example>")
    row = _only(ledger, "mina", "message.mail", "to")  # X01 — 대소문자 무시
    assert row.theme == "message" and row.item == "mail" and row.actor == {"external_name": "Partner"}
    assert row.data["subject"] == "견적" and row.data["account"] == "mina@company.example"
    assert row.target["surface"] == "inbox" and row.target["source"] == "mail"
    ledger.mark("mina")
    _mail(database_url, ids["mail"], sender="partner@example.com", to="team@company.example", cc="mina@company.example")
    _only(ledger, "mina", "message.mail", "mail-cc")  # X02
    ledger.mark("mina")
    _mail(database_url, ids["mail"], sender="list@example.com", to="all@company.example")
    _only(ledger, "mina", "message.mail", "mail-other")  # X03


def test_x07_mail_i_sent_is_from_me_makes_nothing_and_is_not_unread(inbox_world) -> None:
    client, database_url, ledger, ids = inbox_world
    _mail(database_url, ids["mail"], sender="Me <mina@company.example>", to="partner@example.com")
    _none(ledger, "mina")
    from ax_workspace.platform.persistence import ExternalMessageRecord

    with make_session_factory(database_url)() as session:
        assert [row.from_me for row in session.query(ExternalMessageRecord)] == [True]
    page = client.get("/api/inbox/messages", headers=MINA, params={"source": "mail"}).json()
    assert page["unread_counts"]["mail"] == 0 and page["items"][0]["unread"] is False
    unread_only = client.get("/api/inbox/messages", headers=MINA, params={"source": "mail", "unread": "true"}).json()
    assert unread_only["items"] == []


def test_backfilled_messages_make_nothing(inbox_world) -> None:
    """백필 · 메우기는 과거를 채우는 것이다 — 실시간 줄만 알림(OQ-1101 제안)."""
    _, database_url, ledger, ids = inbox_world
    _mail(database_url, ids["mail"], sender="p@example.com", to="mina@company.example", mode="backfill")
    _slack(database_url, ids, "dm", mode="backfill")
    _none(ledger, "mina")


def test_x04_dm_and_group_dm_are_one_row_each_never_merged(inbox_world) -> None:
    _, database_url, ledger, ids = inbox_world
    for room in ("dm", "dm", "group_dm"):
        _slack(database_url, ids, room)
    rows = ledger.new("mina")
    assert [(row.kind, row.relation) for row in rows] == [("message.slack", "dm")] * 3
    assert all(row.coalesce_key is None for row in rows)


def test_x05_a_mention_is_its_own_row_and_here_is_not_a_mention(inbox_world) -> None:
    _, database_url, ledger, ids = inbox_world
    _slack(database_url, ids, "channel", text="<@U-ME> 확인 부탁")
    mention = _only(ledger, "mina", "message.slack", "mention")
    assert mention.coalesce_key is None
    ledger.mark("mina")
    _slack(database_url, ids, "channel", text="<!here> 공지")
    assert _only(ledger, "mina", "message.slack", "channel").coalesce_key is not None


def test_x06_channel_messages_merge_into_one_unread_row_with_a_new_seq(inbox_world) -> None:
    client, database_url, ledger, ids = inbox_world
    _slack(database_url, ids, "channel", author="가")
    [first] = ledger.new("mina")
    first_seq = first.seq
    _slack(database_url, ids, "private", author="나")  # 다른 방은 따로
    _slack(database_url, ids, "channel", author="나")
    _slack(database_url, ids, "channel", author="가")
    rows = [row for row in ledger.new("mina") if row.resource_id == ids["room_channel"]]
    assert len(rows) == 1 and rows[0].id == first.id
    assert rows[0].data["count"] == 3 and rows[0].data["senders"] == ["가", "나"] and rows[0].seq > first_seq
    assert len([row for row in ledger.new("mina") if row.resource_id == ids["room_private"]]) == 1
    # 보낸 사람 수는 자른 목록(5명)과 따로 오른다 — 일곱 명이면 일곱(검수 W-1)
    for name in ("다", "라", "마", "바", "사"):
        _slack(database_url, ids, "channel", author=name)
    [merged] = [row for row in ledger.new("mina") if row.resource_id == ids["room_channel"]]
    assert merged.data["count"] == 8 and len(merged.data["senders"]) == 5 and merged.data["sender_count"] == 7
    # 읽고 나면 다음 메시지는 새 줄이다
    assert client.post(f"/api/notifications/{first.id}/read", headers=MINA).status_code == 200
    ledger.mark("mina")
    _slack(database_url, ids, "channel", author="다")
    assert _only(ledger, "mina", "message.slack", "channel").id != first.id


def test_x07_slack_lines_i_sent_make_nothing_and_are_not_unread(inbox_world) -> None:
    client, database_url, ledger, ids = inbox_world
    _slack(database_url, ids, "channel", user="U-ME", author="내 표시 이름")  # 이름으로 덮인 author 가 아니라 raw.user 로 판정
    _none(ledger, "mina")
    cards = client.get("/api/inbox/messages", headers=MINA, params={"source": "slack"}).json()["items"]
    assert all(card.get("unread_count", 0) == 0 for card in cards)


def test_x08_x09_x07_kakao_rows_follow_room_type_and_the_collector_flag(inbox_world) -> None:
    client, database_url, ledger, _ = inbox_world
    token = client.post("/api/device-tokens", headers=MINA, json={"device_name": "Mac"}).json()["token"]
    bearer = {"Authorization": f"Bearer {token}"}
    integration = client.get("/api/integrations/kakao/handshake", headers=bearer).json()["integration_id"]
    chosen = {"room_ids": ["1", "2"], "rooms": [{"room_id": "1", "type": "direct", "name": "1:1"}, {"room_id": "2", "type": "group", "name": "팀방"}]}
    assert client.post(f"/api/integrations/{integration}/rooms", headers=MINA, json=chosen).status_code == 202
    rooms = {row["external_id"]: row["room_id"] for row in client.get("/api/integrations/kakao/handshake", headers=bearer).json()["selected_rooms"]}

    def upload(room: str, log: int, *, backfill_done: bool = False, **extra: Any) -> None:
        body = {"room_id": rooms[room], "backfill_done": backfill_done,
                "messages": [{"logId": log, "author": "동료", "at": 1_790_000_000 + log, "type": 1, "text": f"카톡 {log}", **extra}]}
        assert client.post("/api/integrations/kakao/messages", headers=bearer, json=body).status_code == 202

    upload("1", 1, backfill_done=True)  # 백필의 끝 — 그 묶음은 과거다
    upload("2", 1, backfill_done=True)
    ledger.mark("mina")
    _none(ledger, "mina")
    upload("1", 2)
    _only(ledger, "mina", "message.kakao", "kakao-direct")  # X08
    ledger.mark("mina")
    upload("2", 2)
    _only(ledger, "mina", "message.kakao", "kakao-group")  # X09
    ledger.mark("mina")
    upload("2", 3, from_me=True)  # X07 — 수집기 표지
    _none(ledger, "mina")
    upload("2", 4)  # 표지 없음(옛 수집기) = null = 내 것 아님
    _only(ledger, "mina", "message.kakao", "kakao-group")
    from ax_workspace.platform.persistence import ExternalMessageRecord

    with make_session_factory(database_url)() as session:
        flags = {row.external_key: row.from_me for row in session.query(ExternalMessageRecord).filter_by(container_key="2")}
    assert flags == {"1": None, "2": None, "3": True, "4": None}


def test_x10_x11_x12_x13_losing_an_integration_or_a_room_is_a_red_row(inbox_world) -> None:
    _, database_url, ledger, ids = inbox_world
    store = _store(database_url)
    store.mark_disconnected(ids["mail"], "token_expired")
    lost = _only(ledger, "mina", "message.integration_lost", "integration")  # X10
    assert lost.item == "mail" and lost.failure is True and lost.data["reason"] == "disconnected"
    assert lost.target == {"surface": "settings", "tab": "mail"}
    ledger.mark("mina")
    store.set_room_access(ids["room_channel"], ok=False, reason="not_in_channel")
    room = _only(ledger, "mina", "message.integration_lost", "integration")  # X11
    assert room.item == "slack" and room.data["reason"] == "room_access_lost"
    ledger.mark("mina")
    store.set_room_access(ids["room_channel"], ok=True)  # X13 되살림 — 없음
    store.mark_disconnected(ids["mail"], "again")  # 이미 끊긴 연동 — 두 번째 줄 없음
    _none(ledger, "mina")


def test_x14_x15_reply_results_and_message_tasks_make_nothing(inbox_world) -> None:
    """X14 답장 결과 · X15 메시지로 만든 업무 확정은 행위자 몫이라 없다 — 그 자리(`INB:938` · `BA:1773-1800`)는 생성기를 부르지 않는다."""
    import inspect

    from ax_workspace.bootstrap import application as bootstrap
    from ax_workspace.modules.external_channels import inbox

    assert "notifier" not in inspect.getsource(inbox.InboxApplication._announce_result)
    assert "notif" not in inspect.getsource(bootstrap.WorkflowApplication._announce_message_updated).replace("NOTIFY", "")


def test_inbox_reads_also_read_the_message_rows_but_not_the_integration_row(inbox_world) -> None:
    """D-37 · §4.5-3 — 메일 한 통 · 방 `up_to_ts` · 「모두 읽음」 이 같은 트랜잭션에서 메시지 알림 줄을 읽는다. 반대 방향은 없다."""
    client, database_url, ledger, ids = inbox_world
    _mail(database_url, ids["mail"], sender="p@example.com", to="mina@company.example")
    first_ts = _slack(database_url, ids, "dm")
    second_ts = _slack(database_url, ids, "dm")
    _store(database_url).mark_disconnected(ids["slack"], "token_revoked")
    rows = {row.kind + (row.resource_id or ""): row for row in ledger.new("mina")}
    from ax_workspace.platform.persistence import ExternalMessageRecord

    with make_session_factory(database_url)() as session:
        mail_id = str(session.query(ExternalMessageRecord).filter_by(source_kind="mail").one().id)
    assert client.post(f"/api/inbox/mail/{mail_id}/read", headers=MINA).status_code in {200, 204}
    by_kind = lambda kind: [row for row in _rows(database_url, "mina") if row.kind == kind]
    assert all(row.read_at is not None for row in by_kind("message.mail"))
    assert client.post(f"/api/inbox/rooms/{ids['room_dm']}/read", headers=MINA, json={"up_to_ts": first_ts}).status_code in {200, 204}
    dm_rows = sorted(by_kind("message.slack"), key=lambda row: row.seq)
    assert [row.read_at is not None for row in dm_rows] == [True, False]
    assert client.post("/api/inbox/read-all", headers=MINA).status_code in {200, 204}
    assert all(row.read_at is not None for row in by_kind("message.slack"))
    [lost] = by_kind("message.integration_lost")
    assert lost.read_at is None  # 연동 끊김 줄은 메시지가 아니다
    assert rows and second_ts


# ── 회의 M01 ~ M15 (M09 · M10 · M11 은 test_meeting_finalize.py) ────────────────────────


def _meeting(client: TestClient, ledger: Ledger, attendees=("jiho", "hyeon"), external=("partner@example.com",)) -> dict[str, Any]:
    starts = datetime.now(UTC) + timedelta(days=3)
    made = client.post(
        "/api/meetings", headers=MINA,
        json={
            "title": "알림 회의", "starts_at": starts.isoformat(), "ends_at": (starts + timedelta(hours=1)).isoformat(),
            "attendee_ids": list(attendees), "external_attendees": list(external),
        },
    )
    assert made.status_code == 201, made.text
    return made.json()["meeting"]


def test_m01_m02_m03_creating_a_meeting_invites_member_attendees_only(world) -> None:
    client, _, ledger = world
    meeting = _meeting(client, ledger)
    for member in ("jiho", "hyeon"):
        row = _only(ledger, member, "meeting.invited", "attendee")  # M01
        assert row.item == "invite" and row.target == {"surface": "meetings", "meeting_id": meeting["meeting_id"]}
        assert row.data["starts_at"]
    _none(ledger, "mina")  # M02 — 소유자는 행위자 · M03 외부 참석자는 회원이 아니다


def test_m04_m05_m06_changing_time_and_attendees(world) -> None:
    client, _, ledger = world
    meeting = _meeting(client, ledger)
    ledger.mark(*EVERYONE)
    later = datetime.now(UTC) + timedelta(days=4)
    changed = client.patch(
        f"/api/meetings/{meeting['meeting_id']}", headers=MINA,
        json={"starts_at": later.isoformat(), "ends_at": (later + timedelta(hours=1)).isoformat(), "attendee_ids": ["jiho", "minseok"]},
    )
    assert changed.status_code == 200, changed.text
    updated = _only(ledger, "jiho", "meeting.changed", "attendee")  # M04 — 남은 참석자
    assert updated.data["change"] == "updated" and updated.data["before"]["starts_at"] != updated.data["after"]["starts_at"]
    assert _only(ledger, "minseok", "meeting.invited", "attendee").item == "invite"  # M05
    removed = _only(ledger, "hyeon", "meeting.changed", "attendee")  # M06 — 원칙 ③ 의 예외
    assert removed.data == {"change": "removed"} and removed.target == {"surface": "meetings", "meeting_id": meeting["meeting_id"]}
    _none(ledger, "mina")
    # 빠진 사람은 이제 그 회의를 못 연다 — 줄은 남고 `target` 만 null(§4.5-2-4)
    [item] = [row for row in client.get("/api/notifications", headers=HYEON).json()["items"] if row["kind"] == "meeting.changed"]
    assert item["target"] is None and item["subject"]["title"] == "알림 회의"


def test_m04_a_title_only_edit_makes_nothing(world) -> None:
    client, _, ledger = world
    meeting = _meeting(client, ledger)
    ledger.mark(*EVERYONE)
    assert client.patch(f"/api/meetings/{meeting['meeting_id']}", headers=MINA, json={"title": "제목만"}).status_code == 200
    _none(ledger, *EVERYONE)


def test_m07_cancelling_reaches_the_attendees(world) -> None:
    client, _, ledger = world
    meeting = _meeting(client, ledger)
    ledger.mark(*EVERYONE)
    assert client.delete(f"/api/meetings/{meeting['meeting_id']}", headers=MINA).status_code == 204
    for member in ("jiho", "hyeon"):
        assert _only(ledger, member, "meeting.changed", "attendee").data == {"change": "cancelled"}
    _none(ledger, "mina")


def test_m12_both_share_paths_notify_and_m13_revoking_does_not(world) -> None:
    client, _, ledger = world
    meeting = _meeting(client, ledger)
    ledger.mark(*EVERYONE)
    assert client.post(f"/api/meetings/{meeting['meeting_id']}/shares", headers=MINA, json={"member_ids": ["yuna"]}).status_code == 200
    assert _only(ledger, "yuna", "meeting.shared", "shared").item == "share"  # M12 — share_many
    application = client.app.state.workflow_application
    application.share_meeting(application.authenticated_principal("mina"), __import__("uuid").UUID(meeting["meeting_id"]), "minseok")
    assert _only(ledger, "minseok", "meeting.shared", "shared").item == "share"  # M12 — share
    ledger.mark(*EVERYONE)
    assert client.delete(f"/api/meetings/{meeting['meeting_id']}/shares/yuna", headers=MINA).status_code in {200, 204}
    _none(ledger, *EVERYONE)  # M13


def test_m15_making_a_meeting_public_notifies_nobody(world) -> None:
    from uuid import UUID

    client, database_url, ledger = world
    meeting = _meeting(client, ledger)
    ledger.mark(*EVERYONE)
    application = client.app.state.workflow_application
    with make_session_factory(database_url)() as session:
        application._meetings(session).apply_legacy_visibility(
            application.authenticated_principal("mina"), UUID(meeting["meeting_id"]), "public", ["yuna", "minseok"]
        )
        session.commit()
    _none(ledger, *EVERYONE)


def test_m08_m14_starting_a_meeting_makes_nothing(world) -> None:
    client, _, ledger = world
    meeting = _meeting(client, ledger)
    ledger.mark(*EVERYONE)
    client.post(f"/api/meetings/{meeting['meeting_id']}/start", headers=MINA)
    _none(ledger, *EVERYONE)


def test_the_inbox_badge_is_one_exists_with_the_rail_rules(inbox_world) -> None:
    """사이드바 메시지함 점(SPEC-011 §2.2 · 검수 W-3) — 안 읽은 것이 하나라도 있나. 레일과 같은 규칙: 내 줄은 빼고, 읽음 지점 뒤만."""
    client, database_url, _, ids = inbox_world

    def badge() -> bool:
        return client.get("/api/me/badges", headers=MINA).json()["inbox"]

    assert badge() is False
    _mail(database_url, ids["mail"], sender="Me <mina@company.example>", to="p@example.com")
    _slack(database_url, ids, "dm", user="U-ME")
    assert badge() is False  # 내가 보낸 줄만 있다
    _mail(database_url, ids["mail"], sender="p@example.com", to="mina@company.example")
    assert badge() is True
    from ax_workspace.platform.persistence import ExternalMessageRecord

    with make_session_factory(database_url)() as session:
        mail_ids = [str(row.id) for row in session.query(ExternalMessageRecord).filter_by(source_kind="mail", from_me=False)]
    for mail_id in mail_ids:
        assert client.post(f"/api/inbox/mail/{mail_id}/read", headers=MINA).status_code in {200, 204}
    assert badge() is False
    ts = _slack(database_url, ids, "channel")
    assert badge() is True
    rail = client.get("/api/inbox/messages", headers=MINA).json()["unread_counts"]["all"]
    assert rail > 0
    assert client.post(f"/api/inbox/rooms/{ids['room_channel']}/read", headers=MINA, json={"up_to_ts": ts}).status_code in {200, 204}
    assert badge() is False
    assert client.get("/api/inbox/messages", headers=MINA).json()["unread_counts"]["all"] == 0
