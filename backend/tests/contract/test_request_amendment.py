"""A requester improving their own request before anyone has judged it.

**W1 이후 이 회차는 과거 행에만 있다** — 신규 요청은 `assigned` 로 서고 판단 회차를 만들지 않는다
(WORK-001 Phase 4). 조정·재상신 코드와 데이터는 그대로 남으므로, 예전 배포가 남긴 모양을
`legacy_acceptance.pending_request` 로 세워 계속 검증한다.

This is not an answer to an adjustment: nobody asked for it, so it produces no ReviewDecision and never puts the
question back on the requester. It adds a round to the same WorkRequest — same ActionItem, same discussion — and
replaces what the assignee is looking at, so the person judging always judges the latest thing that was said.
"""
from uuid import UUID

from legacy_acceptance import pending_request

from fastapi.testclient import TestClient
from sqlalchemy import select

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import (
    ReviewAssignmentRecord,
    SubmissionRecord,
    WorkRequestAuditEventRecord,
    make_session_factory,
)

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    app = create_app(settings)
    return TestClient(app), database_url, settings


def _amend(client, headers, request_id: str, **body):
    return client.post(f"/api/work-requests/{request_id}/amend", headers=headers, json=body)


def _pending(client, headers) -> list[dict]:
    return client.get("/api/action-items", headers=headers).json()


def test_a_requester_improves_a_request_no_one_has_judged_yet(tmp_path) -> None:
    client, database_url, _ = _stack(tmp_path)
    created = pending_request(client, database_url, MINA, title="초안 요청", assignee_id="jiho", description="처음 설명", due_date="2026-10-01")
    rid = created["request_id"]
    [before] = _pending(client, JIHO)

    amended = _amend(
        client, MINA, rid,
        expected_version=created["version"], title="보강한 요청", description="더 자세한 설명",
    )
    assert amended.status_code == 200, amended.text
    assert amended.json()["state"] == "pending" and amended.json()["title"] == "보강한 요청"

    # The same question, one round later: not 조정 필요, and never the requester's turn.
    [after] = _pending(client, JIHO)
    assert after["action_item_id"] == before["action_item_id"]
    assert after["status"] == "awaiting_review" and after["submission_version"] == 2
    assert after["subject"] == "보강한 요청"
    assert [command["id"] for command in after["allowed_commands"]] ==["accept", "adjust", "reject"]
    assert _pending(client, MINA) ==[]

    # The earlier round is untouched, and the new one says what changed.
    detail = client.get(f"/api/action-items/{after['action_item_id']}", headers=MINA).json()
    assert [row["submission_version"] for row in detail["rounds"]] ==[1, 2]
    assert detail["rounds"][0]["snapshot"]["title"] == "초안 요청"
    assert detail["rounds"][0]["decisions"] ==[]
    assert detail["rounds"][1]["diff"]["title"] == {"before": "초안 요청", "after": "보강한 요청"}
    assert detail["rounds"][1]["snapshot"]["due_date"] == "2026-10-01"

    # Exactly one open assignment, on the latest round, for the same assignee; the old one is superseded.
    with make_session_factory(database_url)() as session:
        rounds = {
            row.submission_version: row.id
            for row in session.scalars(select(SubmissionRecord).order_by(SubmissionRecord.submission_version))
        }
        assignments = list(session.scalars(select(ReviewAssignmentRecord).order_by(ReviewAssignmentRecord.assigned_at)))
        assert [row.status for row in assignments] ==["superseded", "pending"]
        assert [row.reviewer_member_id for row in assignments] ==["jiho", "jiho"]
        assert assignments[0].submission_id == rounds[1] and assignments[1].submission_id == rounds[2]
        assert assignments[1].supersedes_assignment_id == assignments[0].id

    # And the assignee can still answer it, once.
    accepted = client.post(
        f"/api/action-items/{after['action_item_id']}/commands/accept",
        headers=JIHO,
        json={"expected_version": after["expected_version"]},
    )
    assert accepted.status_code == 200, accepted.text
    assert [task["title"] for task in client.get("/api/my-work", headers=JIHO).json()] ==["보강한 요청"]


def test_an_amendment_makes_an_open_judgement_answer_the_new_round(tmp_path) -> None:
    """Whatever the assignee was about to answer is no longer the question."""
    client, database_url, _ = _stack(tmp_path)
    created = pending_request(client, database_url, MINA, title="바뀔 요청", assignee_id="jiho")
    [opened] = _pending(client, JIHO)

    assert _amend(client, MINA, created["request_id"], expected_version=created["version"], title="바뀐 요청").status_code == 200

    stale = client.post(
        f"/api/action-items/{opened['action_item_id']}/commands/accept",
        headers=JIHO,
        json={"expected_version": opened["expected_version"]},
    )
    assert stale.status_code == 422, stale.text
    assert client.get("/api/my-work", headers=JIHO).json() ==[]
    [fresh] = _pending(client, JIHO)
    assert fresh["subject"] == "바뀐 요청" and fresh["expected_version"] != opened["expected_version"]


def test_the_same_amendment_sent_twice_is_one_round_and_one_receipt(tmp_path) -> None:
    client, database_url, _ = _stack(tmp_path)
    created = pending_request(client, database_url, MINA, title="한 번만", assignee_id="jiho")
    rid, version = created["request_id"], created["version"]
    command = {"expected_version": version, "title": "한 번만 고침", "description": "설명"}

    first = _amend(client, MINA, rid, **command)
    assert first.status_code == 200, first.text
    receipt = _amend(client, MINA, rid, **command)
    assert receipt.status_code == 200 and receipt.json() == first.json()

    # A different amendment quoting the consumed version is a different command, not this one coming back.
    assert _amend(client, MINA, rid, expected_version=version, title="다른 수정").status_code == 422
    with make_session_factory(database_url)() as session:
        assert [row.submission_version for row in session.scalars(select(SubmissionRecord))] ==[1, 2]
        amendments =[row for row in session.scalars(select(WorkRequestAuditEventRecord)) if row.event_type == "work_request.amended"]
        assert len(amendments) == 1 and amendments[0].actor_id == "mina"


def test_a_new_round_inherits_the_basis_without_touching_the_one_before_it(tmp_path) -> None:
    client, database_url, _ = _stack(tmp_path)
    created = pending_request(client, database_url, MINA, title="근거 있는 요청", assignee_id="jiho")
    rid = created["request_id"]
    adopted = client.post(
        f"/api/work-requests/{rid}/evidence", headers=MINA, files={"file": ("근거.txt", b"one", "text/plain")}
    ).json()
    before = client.get(f"/api/work-requests/{rid}/timeline", headers=MINA).json()["submissions"][0]

    assert _amend(client, MINA, rid, expected_version=adopted["request_version"], title="보강").status_code == 200

    timeline = client.get(f"/api/work-requests/{rid}/timeline", headers=MINA).json()
    rounds = {row["submission_version"]: row for row in timeline["submissions"]}
    assert rounds[1]["evidence_hash"] == before["evidence_hash"] == rounds[2]["evidence_hash"]
    assert [row["attachment_id"] for row in rounds[2]["evidence"]] ==[row["attachment_id"] for row in before["evidence"]]
    assert [row["event_kind"] for row in timeline["activity"]][-2:] ==["work_request.amended", "work_request.evidence_inherited"]
    # An amendment is not a judgement: no decision was recorded by anyone.
    assert timeline["review_decisions"] ==[]


def test_amending_the_date_moves_the_deadline_the_judgement_is_shown_with(tmp_path) -> None:
    """The question is judged against the latest round, so the date on it is that round's."""
    client, database_url, _ = _stack(tmp_path)
    from ax_workspace.platform.persistence import DecisionItemRecord

    created = pending_request(client, database_url, MINA, title="기한 있는 요청", assignee_id="jiho", due_date="2026-10-01")
    rid = created["request_id"]

    def deadlines() -> tuple:
        timeline = client.get(f"/api/work-requests/{rid}/timeline", headers=MINA).json()
        with make_session_factory(database_url)() as session:
            item = session.scalars(select(DecisionItemRecord)).one()
            pending_assignment =[row for row in session.scalars(select(ReviewAssignmentRecord)) if row.status == "pending"]
            assert len(pending_assignment) == 1
            return (
                timeline["decision_item"]["due_at"],
                item.due_at.date().isoformat() if item.due_at else None,
                pending_assignment[0].due_at.date().isoformat() if pending_assignment[0].due_at else None,
            )

    shown, item_due, assignment_due = deadlines()
    assert shown.startswith("2026-10-01") and item_due == assignment_due == "2026-10-01"

    moved = _amend(client, MINA, rid, expected_version=created["version"], due_date="2026-12-24")
    assert moved.status_code == 200 and moved.json()["due_date"] == "2026-12-24"
    shown, item_due, assignment_due = deadlines()
    assert shown.startswith("2026-12-24") and item_due == assignment_due == "2026-12-24"
    assert client.get("/api/action-items", headers=JIHO).json()[0]["preview"][-1]["value"] == "2026-12-24"

    # Removing it removes it everywhere the judgement is shown.
    cleared = _amend(client, MINA, rid, expected_version=moved.json()["version"], clear_due_date=True)
    assert cleared.status_code == 200 and cleared.json()["due_date"] is None
    shown, item_due, assignment_due = deadlines()
    assert shown is None and item_due is None and assignment_due is None
    assert {row["id"] for row in client.get("/api/action-items", headers=JIHO).json()[0]["preview"]} == {"requester", "assignee"}
    # The rounds still say what each of them was.
    rounds = client.get(f"/api/work-requests/{rid}/timeline", headers=MINA).json()["submissions"]
    assert [row["snapshot"]["due_date"] for row in rounds] ==["2026-10-01", "2026-12-24", None]


def _delegated_turn(client, application, headers, key: str) -> str:
    from ax_workspace.platform.persistence import ConversationTurnRecord

    conversation = client.post("/api/conversations", headers=headers, json={"title": "위임"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**headers, "Idempotency-Key": key},
        json={"body": "요청을 보강해줘", "context": []},
    )
    assert accepted.status_code == 202, accepted.text
    with make_session_factory(application._settings.database_url)() as session:
        return str(session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"])).execution_id)


def test_rest_mcp_and_a_delegated_turn_all_amend_through_the_one_command(tmp_path, monkeypatch) -> None:
    from ax_workspace.entrypoints.mcp import McpReportsFacade

    client, database_url, settings = _stack(tmp_path)
    application = client.app.state.workflow_application
    monkeypatch.delenv("AX_MCP_CAUSATION_ID", raising=False)
    mina = McpReportsFacade(settings, "mina")

    # 1) MCP outside a turn runs the same command REST runs.
    created = pending_request(client, database_url, MINA, title="도구로 보강", assignee_id="jiho")
    direct = mina.amend_work_request(created["request_id"], created["version"], title="도구가 보강함", description=None, due_date=None)
    assert direct["title"] == "도구가 보강함" and direct["version"] == created["version"] + 1
    assert client.get("/api/action-items", headers=JIHO).json()[0]["submission_version"] == 2

    # 2) Inside a delegated turn it is a confirmation for the requester, not an edit.
    monkeypatch.setenv("AX_MCP_CAUSATION_ID", _delegated_turn(client, application, MINA, "amend-turn"))
    gated = mina.amend_work_request(direct["request_id"], direct["version"], title="AX가 제안한 제목", description=None, due_date=None)
    assert gated["state"] == "pending" and gated["action_type"] == "work_request.amend"
    assert gated["operation_label"] == "요청 수정"
    assert any(row["value"] == "AX가 제안한 제목" for row in gated["preview"])
    assert client.get(f"/api/work-requests/{direct['request_id']}", headers=MINA).json()["title"] == "도구가 보강함"

    approved = client.post(
        f"/api/actions/{gated['action_id']}/decide", headers=MINA,
        json={"expected_version": gated["version"], "decision": "approve"},
    )
    assert approved.status_code == 200, approved.text
    current = client.get(f"/api/work-requests/{direct['request_id']}", headers=MINA).json()
    assert current["title"] == "AX가 제안한 제목" and current["version"] == direct["version"] + 1
    assert [row["submission_version"] for row in client.get(f"/api/work-requests/{direct['request_id']}/timeline", headers=MINA).json()["submissions"]] ==[1, 2, 3]


def test_a_delegated_turn_can_explain_the_rounds_it_did_not_see(tmp_path, monkeypatch) -> None:
    """A read-only history, scoped to what the bound persona may already read."""
    from ax_workspace.entrypoints.mcp import McpReportsFacade

    client, database_url, settings = _stack(tmp_path)
    monkeypatch.delenv("AX_MCP_CAUSATION_ID", raising=False)
    created = pending_request(client, database_url, MINA, title="이력 요청", assignee_id="jiho", description="처음")
    _amend(client, MINA, created["request_id"], expected_version=created["version"], title="보강한 이력 요청")
    [item] = _pending(client, JIHO)
    client.post(
        f"/api/action-items/{item['action_item_id']}/commands/adjust", headers=JIHO,
        json={"expected_version": item["expected_version"], "reason": "기한을 알려 주세요"},
    )

    history = McpReportsFacade(settings, "jiho").work_request_history(created["request_id"])
    assert [row["submission_version"] for row in history["submissions"]] ==[1, 2]
    assert history["submissions"][1]["diff"]["title"] == {"before": "이력 요청", "after": "보강한 이력 요청"}
    assert [row["decision"] for row in history["review_decisions"]] ==["negotiate"]
    assert history["review_decisions"][0]["reason"] == "기한을 알려 주세요"
    assert history["request"]["state"] == "negotiating"
    # It is the product's own permission-scoped read: a stranger learns nothing.
    outsider = McpReportsFacade(settings, "sora")
    try:
        outsider.work_request_history(created["request_id"])
        raise AssertionError("history was served to someone with no relationship to the request")
    except Exception as error:
        assert "이력 요청" not in str(error)
