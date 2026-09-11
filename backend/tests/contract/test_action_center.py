"""One judgement ledger.

Every path that needs a person's decision — a WorkRequest, a direct TaskAssignment, an AX gated proposal — is one
canonical ActionItem in one query, with one envelope shape. What differs between kinds is the policy the server
applies: which commands the current principal may run, and what the question in front of them says. The client reads
that projection; it never derives a command, a field, or a permission from the kind.
"""
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import delete

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import (
    ConversationTurnRecord,
    DecisionItemRecord,
    MeetingRecord,
    ReviewAssignmentRecord,
    ReviewDecisionRecord,
    RoleCapabilityRecord,
    SubjectRecord,
    SubjectVersionRecord,
    SubmissionRecord,
    TaskAssignmentRecord,
    make_session_factory,
)
from ax_workspace.platform.meetings import SqlAlchemyMeetingRepository
from ax_workspace.modules.meetings.domain import MeetingError

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
HYEON = {"X-Demo-Persona": "hyeon"}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    app = create_app(settings)
    return TestClient(app), app.state.workflow_application


def _ax_proposal(
    client,
    application,
    headers,
    persona: str,
    action_type: str,
    title: str,
    payload: dict,
    *,
    body: str = "제안해줘",
) -> dict:
    """A gated AX proposal, created the way a delegated turn creates one."""
    conversation = client.post("/api/conversations", headers=headers, json={"title": "판단 통합"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**headers, "Idempotency-Key": f"action-center-{action_type}-{persona}"},
        json={"body": body, "context": []},
    )
    assert accepted.status_code == 202, accepted.text
    with make_session_factory(application._settings.database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"])).execution_id
    return application.propose_action(application.authenticated_principal(persona), execution_id, action_type, title, payload)


def test_an_ax_proposal_opens_one_canonical_submission_on_its_conversation_turn(tmp_path) -> None:
    client, application = _stack(tmp_path)
    proposal = _ax_proposal(
        client,
        application,
        JIHO,
        "jiho",
        "task.create_self",
        "업무 생성 확인",
        {"title": "AX 원안", "description": "제안 설명"},
    )

    with make_session_factory(application._settings.database_url)() as session:
        decision = session.get(DecisionItemRecord, UUID(proposal["action_id"]))
        assert decision is not None
        subject = session.get(SubjectRecord, decision.subject_id)
        [submission] = session.query(SubmissionRecord).filter_by(decision_item_id=decision.id).all()
        version = session.get(SubjectVersionRecord, submission.subject_version_id)
        [assignment] = session.query(ReviewAssignmentRecord).filter_by(submission_id=submission.id).all()

        assert subject.owning_resource_type == "conversation_turn"
        assert subject.owning_resource_id == proposal["turn_id"]
        assert decision.context_type == "conversation" and decision.context_id == proposal["conversation_id"]
        assert decision.effect_identity.endswith(":task.create_self")
        assert submission.submission_version == 1 and submission.submitted_by == "ax"
        assert submission.payload_hash == version.content_hash == proposal["payload_hash"]
        assert version.snapshot == {"title": "AX 원안", "description": "제안 설명"}
        assert assignment.reviewer_member_id == "jiho" and assignment.status == "pending"

    detail = client.get(f"/api/action-items/{proposal['action_id']}", headers=JIHO)
    assert detail.status_code == 200, detail.text
    [round_one] = detail.json()["rounds"]
    assert round_one["submission_id"] != proposal["action_id"]
    assert round_one["snapshot"]["title"] == "AX 원안"


def test_a_pre_cutover_pending_ax_row_keeps_its_legacy_approval_path(tmp_path) -> None:
    client, application = _stack(tmp_path)
    proposal = _ax_proposal(
        client, application, JIHO, "jiho", "task.create_self", "업무 생성 확인", {"title": "기존 pending 업무"}
    )
    with make_session_factory(application._settings.database_url)() as session:
        decision = session.get(DecisionItemRecord, UUID(proposal["action_id"]))
        subject_id = decision.subject_id
        session.query(ReviewAssignmentRecord).filter(
            ReviewAssignmentRecord.submission_id.in_(
                session.query(SubmissionRecord.id).filter_by(decision_item_id=decision.id)
            )
        ).delete(synchronize_session=False)
        session.query(SubmissionRecord).filter_by(decision_item_id=decision.id).delete()
        session.delete(decision)
        session.query(SubjectVersionRecord).filter_by(subject_id=subject_id).delete()
        session.delete(session.get(SubjectRecord, subject_id))
        session.commit()

    [legacy] = [row for row in _pending(client, JIHO) if row["action_item_id"] == proposal["action_id"]]
    assert [command["id"] for command in legacy["allowed_commands"]] == ["approve", "reject"]
    approved = _command(
        client, JIHO, legacy["action_item_id"], "approve", expected_version=legacy["expected_version"]
    )
    assert approved.status_code == 200, approved.text
    assert [task["title"] for task in client.get("/api/my-work", headers=JIHO).json()] == ["기존 pending 업무"]


def test_confirming_an_unchanged_ax_draft_executes_submission_one_with_full_lineage(tmp_path) -> None:
    client, application = _stack(tmp_path)
    proposal = _ax_proposal(
        client,
        application,
        JIHO,
        "jiho",
        "task.create_self",
        "업무 생성 확인",
        {"title": "  원안 그대로  ", "description": " 설명 ", "due_date": "2026-09-30", "checklist": [" 첫 단계 "]},
    )
    [item] = [row for row in _pending(client, JIHO) if row["kind"] == "ax.task.create_self"]

    confirmed = _command(
        client,
        JIHO,
        item["action_item_id"],
        "confirm",
        expected_version=item["expected_version"],
        base_submission_version=item["submission_version"],
        draft={"title": "원안 그대로", "description": "설명", "due_date": "2026-09-30", "checklist": ["첫 단계"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "resolved"

    detail = client.get(f"/api/action-items/{item['action_item_id']}", headers=JIHO).json()
    assert [round_["submission_version"] for round_ in detail["rounds"]] == [1]
    [decision] = detail["rounds"][0]["decisions"]
    assert decision["decision"] == "confirm" and decision["actor_member_id"] == "jiho"

    [task] = [row for row in client.get("/api/my-work", headers=JIHO).json() if row["title"] == "원안 그대로"]
    task_detail = client.get(f"/api/tasks/{task['task_id']}", headers=JIHO).json()
    assert task_detail["version"] == 1
    assert task_detail["description"] == "설명"
    assert task_detail["lineage"]["source_action_item_id"] == item["action_item_id"]
    assert task_detail["lineage"]["source_decision_item_id"] == item["action_item_id"]
    assert task_detail["lineage"]["source_submission_id"] == detail["rounds"][0]["submission_id"]
    assert task_detail["lineage"]["source_review_decision_id"] == decision["review_decision_id"]


def test_ax_task_proposal_publishes_a_typed_server_authored_edit_contract(tmp_path) -> None:
    client, application = _stack(tmp_path)
    proposal = _ax_proposal(
        client,
        application,
        JIHO,
        "jiho",
        "task.create_self",
        "업무 생성 확인",
        {
            "title": "  AX 원안  ",
            "description": " 설명 ",
            "start_date": "2026-09-10",
            "due_date": "2026-09-20",
            "checklist": [" 준비 ", "검토"],
        },
    )
    item = client.get(f"/api/action-items/{proposal['action_id']}", headers=JIHO).json()

    contract = item["edit_contract"]
    assert contract["editor"] == "task"
    assert contract["base_submission_version"] == 1
    assert contract["values"] == {
        "title": "AX 원안",
        "description": "설명",
        "start_date": "2026-09-10",
        "due_date": "2026-09-20",
        "checklist": ["준비", "검토"],
        "reference_task_ids": [],
        "parent_task_id": None,
        "project_id": None,
    }
    fields = {field["id"]: field for field in contract["fields"]}
    assert set(fields) == {
        "title",
        "description",
        "assignee_id",
        "start_date",
        "due_date",
        "project_id",
        "checklist",
        "reference_task_ids",
    }
    assert fields["title"] == {
        "id": "title",
        "label": "업무 명",
        "type": "text",
        "required": True,
        "editable": True,
    }
    assert fields["assignee_id"]["editable"] is False
    assert fields["assignee_id"]["value"] == "jiho"
    assert fields["assignee_id"]["label_value"] == "지호 (팀장)"
    assert fields["due_date"]["required"] is True
    assert fields["project_id"]["options"] == []
    assert fields["reference_task_ids"]["options"] == []

    conversation = client.get(f"/api/conversations/{proposal['conversation_id']}", headers=JIHO).json()
    [chat_action] = [row for row in conversation["actions"] if row["action_id"] == proposal["action_id"]]
    assert chat_action["edit_contract"] == contract


def test_ax_work_request_keeps_editable_references_through_confirmation_and_acceptance(tmp_path) -> None:
    client, application = _stack(tmp_path)
    earlier = client.post(
        "/api/tasks",
        headers=MINA,
        json={"title": "지난 분기 보고", "due_date": "2026-09-20"},
    ).json()
    proposal = _ax_proposal(
        client,
        application,
        MINA,
        "mina",
        "work_request.create",
        "업무 요청 생성 확인",
        {
            "title": "이번 분기 보고",
            "assignee_id": "jiho",
            "description": "지난 보고를 참고해 주세요",
            "due_date": "2026-09-30",
            "cc_member_ids": ["mina", "jiho", "sora"],
            "checklist": ["초안 작성", "수치 검토"],
            "reference_task_ids": [earlier["task_id"]],
        },
    )

    item = client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()
    contract = item["edit_contract"]
    assert contract["editor"] == "task"
    assert contract["values"]["reference_task_ids"] == [earlier["task_id"]]
    assert contract["values"]["cc_member_ids"] == ["sora"]
    assert {
        option["value"] for option in next(field for field in contract["fields"] if field["id"] == "reference_task_ids")["options"]
    } == {earlier["task_id"]}
    conversation = client.get(f"/api/conversations/{proposal['conversation_id']}", headers=MINA).json()
    [chat_action] = [row for row in conversation["actions"] if row["action_id"] == proposal["action_id"]]
    assert chat_action["edit_contract"] == contract
    assert client.get("/api/work-requests", headers=MINA).json() == []

    confirmed = _command(
        client,
        MINA,
        item["action_item_id"],
        "confirm",
        expected_version=item["expected_version"],
        base_submission_version=item["submission_version"],
        draft={
            **contract["values"],
            "description": "지난 보고의 수치만 참고해 주세요",
            "checklist": ["수치 검토"],
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    replayed = _command(
        client,
        MINA,
        item["action_item_id"],
        "confirm",
        expected_version=item["expected_version"],
        base_submission_version=item["submission_version"],
        draft={
            **contract["values"],
            "description": "지난 보고의 수치만 참고해 주세요",
            "checklist": ["수치 검토"],
        },
    )
    assert replayed.status_code == 200
    assert replayed.json() == confirmed.json()
    [request] = client.get("/api/work-requests", headers=MINA).json()
    request_detail = client.get(f"/api/work-requests/{request['request_id']}", headers=MINA).json()
    assert request_detail["description"] == "지난 보고의 수치만 참고해 주세요"
    assert request_detail["cc_member_ids"] == ["sora"]
    assert [row["task"]["title"] for row in request_detail["references"]] == ["지난 분기 보고"]
    assert client.get("/api/my-work", headers=JIHO).json() == []

    [acceptance] = _pending(client, JIHO)
    accepted = _command(
        client,
        JIHO,
        acceptance["action_item_id"],
        "accept",
        expected_version=acceptance["expected_version"],
    )
    assert accepted.status_code == 200, accepted.text
    [task] = client.get("/api/my-work", headers=JIHO).json()
    detail = client.get(f"/api/tasks/{task['task_id']}", headers=JIHO).json()
    assert [row["text"] for row in detail["checklist"]] == ["수치 검토"]
    assert len(detail["references"]) == 1


def test_ax_work_request_confirmation_rechecks_reference_access_without_leaking_or_partial_writes(tmp_path) -> None:
    client, application = _stack(tmp_path)
    foreign = client.post(
        "/api/tasks",
        headers=HYEON,
        json={"title": "인사팀 비공개 계획", "description": "민감한 원문", "due_date": "2026-09-20"},
    )
    assert foreign.status_code == 201, foreign.text
    proposal = _ax_proposal(
        client,
        application,
        MINA,
        "mina",
        "work_request.create",
        "업무 요청 생성 확인",
        {
            "title": "분기 계획 검토",
            "assignee_id": "jiho",
            "reference_task_ids": [foreign.json()["task_id"]],
        },
    )
    item = client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()
    assert "인사팀 비공개 계획" not in str(item)
    assert "민감한 원문" not in str(item)
    assert any(row["id"] == "references" and row["value"] == "볼 수 없는 업무 1건" for row in item["preview"])

    refused = _command(
        client,
        MINA,
        item["action_item_id"],
        "confirm",
        expected_version=item["expected_version"],
        base_submission_version=item["submission_version"],
    )
    assert refused.status_code == 422, refused.text
    assert "참고 업무" in refused.text
    assert client.get("/api/work-requests", headers=MINA).json() == []
    detail = client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()
    assert detail["status"] == "awaiting_review"
    assert [round_["submission_version"] for round_ in detail["rounds"]] == [1]
    assert detail["rounds"][0]["decisions"] == []


def test_ax_work_request_confirmation_rechecks_current_creation_authority(tmp_path) -> None:
    client, application = _stack(tmp_path)
    proposal = _ax_proposal(
        client,
        application,
        MINA,
        "mina",
        "work_request.create",
        "업무 요청 생성 확인",
        {"title": "권한 재확인", "assignee_id": "jiho"},
    )
    item = client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()
    with make_session_factory(application._settings.database_url)() as session:
        session.execute(
            delete(RoleCapabilityRecord).where(
                RoleCapabilityRecord.role_id == "role:member",
                RoleCapabilityRecord.capability_id == "work_request.create",
            )
        )
        session.commit()

    refused = _command(
        client,
        MINA,
        item["action_item_id"],
        "confirm",
        expected_version=item["expected_version"],
        base_submission_version=item["submission_version"],
    )
    assert refused.status_code in {403, 422}, refused.text
    assert client.get("/api/work-requests", headers=MINA).json() == []
    detail = client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()
    assert detail["status"] == "awaiting_review"
    assert detail["rounds"][0]["decisions"] == []


def test_ax_task_confirmation_requires_the_due_date_shown_as_required(tmp_path) -> None:
    client, application = _stack(tmp_path)
    proposal = _ax_proposal(
        client, application, JIHO, "jiho", "task.create_self", "업무 생성 확인", {"title": "기한 없는 업무"}
    )
    [item] = [row for row in _pending(client, JIHO) if row["action_item_id"] == proposal["action_id"]]

    refused = _command(
        client,
        JIHO,
        item["action_item_id"],
        "confirm",
        expected_version=item["expected_version"],
        base_submission_version=item["submission_version"],
        draft={"title": "기한 없는 업무"},
    )

    assert refused.status_code == 422 and "기한" in refused.text
    assert [task for task in client.get("/api/my-work", headers=JIHO).json() if task["title"] == "기한 없는 업무"] == []


def test_ax_meeting_proposal_confirms_meeting_only_once_with_server_authored_contract_and_lineage(tmp_path) -> None:
    client, application = _stack(tmp_path)
    proposal = _ax_proposal(
        client,
        application,
        MINA,
        "mina",
        "meeting.create",
        "회의 생성 확인",
        {
            "organization_id": "scax",
            "title": "AX 회의 원안",
            "description": None,
            "starts_at": "2026-09-10T01:00:00Z",
            "ends_at": "2026-09-10T02:00:00Z",
            "visibility": "private",
            "attendee_ids": ["jiho"],
            "reference_task_ids": [],
        },
    )
    item = client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()

    assert [command["id"] for command in item["allowed_commands"]] == ["confirm", "reject"]
    assert item["allowed_commands"][0]["label"] == "이 내용으로 회의 생성"
    contract = item["edit_contract"]
    assert contract["editor"] == "meeting"
    assert contract["base_submission_version"] == 1
    assert contract["values"] == {
        "organization_id": "scax",
        "title": "AX 회의 원안",
        "description": None,
        "starts_at": "2026-09-10T01:00:00+00:00",
        "ends_at": "2026-09-10T02:00:00+00:00",
        "visibility": "private",
        "attendee_ids": ["jiho"],
        "reference_task_ids": [],
        "include_initial_note": False,
        "initial_note_body": None,
        "initial_note_source_status": "not_requested",
        "initial_note_source_evidence": [],
    }
    fields = {field["id"]: field for field in contract["fields"]}
    assert set(fields) == {
        "organization_id", "title", "description", "starts_at", "ends_at", "visibility", "host_id",
        "attendee_ids", "reference_task_ids", "include_initial_note", "initial_note_body",
    }
    assert fields["host_id"]["editable"] is False
    assert fields["host_id"]["value"] == "mina"
    assert {option["value"] for option in fields["organization_id"]["options"]} == {"product", "scax"}
    assert next(option for option in fields["organization_id"]["options"] if option["value"] == "scax")["label"] == "SCAX"
    assert {option["value"] for option in fields["attendee_ids"]["options"]} >= {"jiho", "mina"}
    attendee_options = {option["value"]: option for option in fields["attendee_ids"]["options"]}
    assert attendee_options["jiho"]["organization_ids"] == ["product", "scax"]
    assert "product" not in attendee_options["hyeon"]["organization_ids"]

    payload = {
        "expected_version": item["expected_version"],
        "base_submission_version": item["submission_version"],
    }
    first = _command(client, MINA, item["action_item_id"], "confirm", **payload)
    replay = _command(client, MINA, item["action_item_id"], "confirm", **payload)
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()

    meeting_id = first.json()["derived_meeting_id"]
    detail = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()
    assert detail["title"] == "AX 회의 원안"
    assert detail["visibility"] == "private"
    assert detail["note"] is None
    assert detail["version"] == 1
    assert detail["lineage"]["source_action_item_id"] == item["action_item_id"]
    assert detail["lineage"]["source_decision_item_id"] == item["action_item_id"]
    assert detail["lineage"]["confirmed_by"] == "mina"
    rounds = client.get(f"/api/action-items/{item['action_item_id']}", headers=MINA).json()["rounds"]
    assert len(rounds) == 1
    [decision] = rounds[0]["decisions"]
    assert detail["lineage"]["source_submission_id"] == rounds[0]["submission_id"]
    assert detail["lineage"]["source_review_decision_id"] == decision["review_decision_id"]


def test_ax_meeting_note_uses_frozen_current_turn_provenance_while_the_body_stays_editable(tmp_path) -> None:
    client, application = _stack(tmp_path)
    proposal = _ax_proposal(
        client,
        application,
        MINA,
        "mina",
        "meeting.create",
        "회의 생성 확인",
        {
            "organization_id": "scax",
            "title": "출시 점검 회의",
            "starts_at": "2026-09-10T03:00:00Z",
            "ends_at": "2026-09-10T04:00:00Z",
            "visibility": "public",
            "attendee_ids": ["jiho"],
            "include_initial_note": True,
            "initial_note_body": "출시 준비 상황과 막힌 점을 확인한다.",
        },
        body="출시 준비 상황과 막힌 점을 이야기할 회의를 잡아줘",
    )
    item = client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()
    values = item["edit_contract"]["values"]
    assert values["initial_note_source_status"] == "current_turn"
    assert values["initial_note_source_evidence"] == [
        {
            "source_type": "conversation_turn",
            "source_id": proposal["turn_id"],
            "label": "현재 대화",
            "excerpt": "출시 준비 상황과 막힌 점을 이야기할 회의를 잡아줘",
            "locator": {"conversation_id": proposal["conversation_id"], "turn_id": proposal["turn_id"]},
        }
    ]

    malicious = {
        **values,
        "title": "사람이 다듬은 출시 점검",
        "initial_note_body": "출시 범위와 담당자별 막힌 점을 확인한다.",
        "initial_note_source_status": "resolved",
        "initial_note_source_evidence": [{"source_type": "task", "source_id": "forged", "label": "가짜"}],
    }
    confirmed = _command(
        client,
        MINA,
        item["action_item_id"],
        "confirm",
        expected_version=item["expected_version"],
        base_submission_version=item["submission_version"],
        draft=malicious,
    )
    assert confirmed.status_code == 200, confirmed.text
    meeting = client.get(f"/api/meetings/{confirmed.json()['derived_meeting_id']}", headers=MINA).json()
    assert meeting["title"] == "사람이 다듬은 출시 점검"
    assert meeting["note"]["body"] == "출시 범위와 담당자별 막힌 점을 확인한다."
    assert meeting["note"]["source_status"] == "current_turn"
    assert meeting["note"]["versions"][0]["source_evidence"] == values["initial_note_source_evidence"]

    updated = client.patch(
        f"/api/meetings/{meeting['meeting_id']}/note",
        headers=MINA,
        json={"expected_version": 1, "body": "후속 편집은 근거를 덮어쓰지 않는다."},
    )
    assert updated.status_code == 200, updated.text
    meeting = client.get(f"/api/meetings/{meeting['meeting_id']}", headers=MINA).json()
    assert meeting["note"]["source_status"] == "current_turn"
    assert [version["source_status"] for version in meeting["note"]["versions"]] == ["current_turn", None]
    assert meeting["note"]["versions"][0]["source_evidence"] == values["initial_note_source_evidence"]
    rounds = client.get(f"/api/action-items/{item['action_item_id']}", headers=MINA).json()["rounds"]
    assert [round_["submission_version"] for round_ in rounds] == [1, 2]
    assert rounds[1]["snapshot"]["initial_note_source_evidence"] == values["initial_note_source_evidence"]


def test_ax_meeting_note_marks_missing_prior_discussion_and_can_be_disabled_before_confirm(tmp_path) -> None:
    client, application = _stack(tmp_path)
    proposal = _ax_proposal(
        client,
        application,
        MINA,
        "mina",
        "meeting.create",
        "회의 생성 확인",
        {
            "organization_id": "scax",
            "title": "지난 논의 후속 회의",
            "starts_at": "2026-09-11T01:00:00Z",
            "ends_at": "2026-09-11T02:00:00Z",
            "visibility": "private",
            "attendee_ids": [],
            "include_initial_note": True,
            "initial_note_body": "현재 요청에 나온 후속 논의만 정리한다.",
            "prior_discussion_requested": True,
            "source_turn_ids": ["11111111-1111-4111-8111-111111111111"],
        },
        body="저번에 논의한 내용으로 후속 회의를 잡아줘",
    )
    item = client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()
    values = item["edit_contract"]["values"]
    assert values["initial_note_source_status"] == "not_found"
    assert [source["source_type"] for source in values["initial_note_source_evidence"]] == ["conversation_turn"]

    disabled = _command(
        client,
        MINA,
        item["action_item_id"],
        "confirm",
        expected_version=item["expected_version"],
        base_submission_version=1,
        draft={**values, "include_initial_note": False},
    )
    assert disabled.status_code == 200, disabled.text
    meeting = client.get(f"/api/meetings/{disabled.json()['derived_meeting_id']}", headers=MINA).json()
    assert meeting["note"] is None
    rounds = client.get(f"/api/action-items/{item['action_item_id']}", headers=MINA).json()["rounds"]
    assert rounds[1]["snapshot"]["initial_note_body"] is None
    assert rounds[1]["snapshot"]["initial_note_source_status"] == "not_requested"
    assert rounds[1]["snapshot"]["initial_note_source_evidence"] == []


def test_ax_meeting_note_can_freeze_a_past_conversation_turn_observed_by_the_current_turn(
    tmp_path, monkeypatch
) -> None:
    from ax_workspace.entrypoints.mcp import McpReportsFacade

    client, application = _stack(tmp_path)
    past = client.post("/api/conversations", headers=MINA, json={"title": "지난 출시 논의"}).json()
    accepted = client.post(
        f"/api/conversations/{past['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": "past-launch-discussion"},
        json={"body": "출시 범위는 모바일 알림을 제외하기로 하자", "context": []},
    )
    assert accepted.status_code == 202, accepted.text
    past_turn_id = accepted.json()["turn_id"]

    current = client.post("/api/conversations", headers=MINA, json={"title": "현재 회의 요청"}).json()
    current_turn = client.post(
        f"/api/conversations/{current['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": "current-meeting-request"},
        json={"body": "지난 출시 논의를 바탕으로 후속 회의를 잡아줘", "context": []},
    )
    assert current_turn.status_code == 202, current_turn.text
    with make_session_factory(application._settings.database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(current_turn.json()["turn_id"])).execution_id

    monkeypatch.setenv("AX_MCP_CAUSATION_ID", str(execution_id))
    facade = McpReportsFacade(application._settings, "mina")
    found = facade.search_conversation_turns("모바일 알림")
    assert [turn["turn_id"] for turn in found["turns"]] == [past_turn_id]
    proposal = facade.create_meeting(
        organization_id="scax",
        title="출시 범위 후속 회의",
        starts_at="2026-09-12T01:00:00Z",
        ends_at="2026-09-12T02:00:00Z",
        visibility="private",
        attendee_ids=["jiho"],
        initial_note_body="모바일 알림 제외 결정을 기준으로 남은 범위를 확인한다.",
        prior_discussion_requested=True,
        source_turn_ids=[past_turn_id],
    )
    monkeypatch.delenv("AX_MCP_CAUSATION_ID")
    item = client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()
    values = item["edit_contract"]["values"]
    assert values["initial_note_source_status"] == "resolved"
    assert [source["source_id"] for source in values["initial_note_source_evidence"]] == [
        proposal["turn_id"],
        past_turn_id,
    ]
    assert values["initial_note_source_evidence"][1]["label"] == "과거 대화 · 지난 출시 논의"
    assert "모바일 알림을 제외" in values["initial_note_source_evidence"][1]["excerpt"]
    rounds = client.get(f"/api/action-items/{item['action_item_id']}", headers=MINA).json()["rounds"]
    assert "source_turn_ids" not in rounds[0]["snapshot"]


def test_ax_meeting_note_freezes_a_meeting_read_by_the_current_turn_without_inventing_parent_fields(
    tmp_path, monkeypatch
) -> None:
    from ax_workspace.entrypoints.mcp import McpReportsFacade

    client, application = _stack(tmp_path)
    past_meeting = client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "organization_id": "scax",
            "title": "지난 출시 점검",
            "starts_at": "2026-09-10T01:00:00Z",
            "ends_at": "2026-09-10T02:00:00Z",
            "visibility": "private",
            "attendee_ids": ["jiho"],
        },
    ).json()
    current = client.post("/api/conversations", headers=MINA, json={"title": "근거 회의 요청"}).json()
    accepted = client.post(
        f"/api/conversations/{current['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": "meeting-resource-source"},
        json={"body": "지난 출시 점검을 바탕으로 후속 회의를 잡아줘", "context": []},
    ).json()
    with make_session_factory(application._settings.database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id

    monkeypatch.setenv("AX_MCP_CAUSATION_ID", str(execution_id))
    facade = McpReportsFacade(application._settings, "mina")
    facade.get_meeting(past_meeting["meeting_id"])
    proposal = facade.create_meeting(
        organization_id="scax",
        title="출시 후속 점검",
        starts_at="2026-09-12T01:00:00Z",
        ends_at="2026-09-12T02:00:00Z",
        visibility="private",
        attendee_ids=["jiho"],
        initial_note_body="지난 회의 결정과 남은 업무를 확인한다.",
    )
    monkeypatch.delenv("AX_MCP_CAUSATION_ID", raising=False)

    item = client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()
    sources = item["edit_contract"]["values"]["initial_note_source_evidence"]
    assert item["edit_contract"]["values"]["initial_note_source_status"] == "resolved"
    meeting_source = next(source for source in sources if source["source_type"] == "meeting")
    assert meeting_source["source_id"] == past_meeting["meeting_id"]
    assert meeting_source["label"] == "지난 출시 점검"
    assert meeting_source["locator"] == {
        "resource_version": past_meeting["version"],
        "source_contexts": [],
        "source_locator": None,
    }

    meetings_before = client.get("/api/meetings", headers=MINA).json()
    assert [meeting["title"] for meeting in meetings_before if meeting["title"] == "출시 후속 점검"] == []

    confirmed = _command(
        client,
        MINA,
        item["action_item_id"],
        "confirm",
        expected_version=item["expected_version"],
        base_submission_version=item["submission_version"],
        draft=item["edit_contract"]["values"],
    )
    assert confirmed.status_code == 200, confirmed.text
    meetings_after = client.get("/api/meetings", headers=MINA).json()
    created = [meeting for meeting in meetings_after if meeting["title"] == "출시 후속 점검"]
    assert len(created) == 1
    meeting = client.get(
        f"/api/meetings/{confirmed.json()['derived_meeting_id']}", headers=MINA
    ).json()
    assert meeting["note"]["body"] == "지난 회의 결정과 남은 업무를 확인한다."
    assert meeting["note"]["versions"][0]["source_evidence"] == sources

    replay = _command(
        client,
        MINA,
        item["action_item_id"],
        "confirm",
        expected_version=item["expected_version"],
        base_submission_version=item["submission_version"],
        draft=item["edit_contract"]["values"],
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["derived_meeting_id"] == meeting["meeting_id"]
    meetings_after_replay = client.get("/api/meetings", headers=MINA).json()
    assert len([meeting for meeting in meetings_after_replay if meeting["title"] == "출시 후속 점검"]) == 1


def test_ax_meeting_note_rejects_an_unobserved_cross_conversation_turn_even_when_owned(tmp_path) -> None:
    client, application = _stack(tmp_path)
    past = client.post("/api/conversations", headers=MINA, json={"title": "소유했지만 읽지 않은 대화"}).json()
    accepted = client.post(
        f"/api/conversations/{past['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": "unobserved-past-turn"},
        json={"body": "공개되면 안 되는 별도 대화", "context": []},
    )
    assert accepted.status_code == 202, accepted.text

    proposal = _ax_proposal(
        client,
        application,
        MINA,
        "mina",
        "meeting.create",
        "회의 생성 확인",
        {
            "organization_id": "scax",
            "title": "관찰되지 않은 근거 차단",
            "starts_at": "2026-09-12T03:00:00Z",
            "ends_at": "2026-09-12T04:00:00Z",
            "visibility": "private",
            "attendee_ids": [],
            "include_initial_note": True,
            "initial_note_body": "현재 요청만 근거로 삼는다.",
            "prior_discussion_requested": True,
            "source_turn_ids": [accepted.json()["turn_id"]],
        },
        body="과거 대화를 찾지 못한 회의를 잡아줘",
    )
    values = client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()["edit_contract"]["values"]
    assert values["initial_note_source_status"] == "not_found"
    assert [source["source_id"] for source in values["initial_note_source_evidence"]] == [proposal["turn_id"]]


def test_ax_meeting_confirm_reauthorizes_attendees_and_rolls_back_the_whole_judgement(tmp_path) -> None:
    client, application = _stack(tmp_path)
    proposal = _ax_proposal(
        client,
        application,
        MINA,
        "mina",
        "meeting.create",
        "회의 생성 확인",
        {
            "organization_id": "scax",
            "title": "참석자 재확인",
            "starts_at": "2026-09-13T01:00:00Z",
            "ends_at": "2026-09-13T02:00:00Z",
            "visibility": "private",
            "attendee_ids": ["jiho"],
        },
    )
    item = client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()
    invalid = _command(
        client,
        MINA,
        item["action_item_id"],
        "confirm",
        expected_version=item["expected_version"],
        base_submission_version=1,
        draft={**item["edit_contract"]["values"], "attendee_ids": ["not-a-member"]},
    )
    assert invalid.status_code == 422, invalid.text
    detail = client.get(f"/api/action-items/{item['action_item_id']}", headers=MINA).json()
    assert detail["status"] == "awaiting_review"
    assert [round_["submission_version"] for round_ in detail["rounds"]] == [1]
    assert detail["rounds"][0]["decisions"] == []
    assert client.get("/api/meetings", headers=MINA).json() == []


def test_ax_meeting_confirm_rechecks_current_manage_authority_before_writing(tmp_path) -> None:
    client, application = _stack(tmp_path)
    proposal = _ax_proposal(
        client,
        application,
        MINA,
        "mina",
        "meeting.create",
        "회의 생성 확인",
        {
            "organization_id": "scax",
            "title": "권한이 유지될 때만 생성",
            "starts_at": "2026-09-14T01:00:00Z",
            "ends_at": "2026-09-14T02:00:00Z",
            "visibility": "private",
            "attendee_ids": [],
        },
    )
    item = client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()
    with make_session_factory(application._settings.database_url)() as session:
        session.execute(
            delete(RoleCapabilityRecord).where(RoleCapabilityRecord.capability_id == "meeting.manage")
        )
        session.commit()

    denied = _command(
        client,
        MINA,
        item["action_item_id"],
        "confirm",
        expected_version=item["expected_version"],
        base_submission_version=1,
    )
    assert denied.status_code in {403, 422}, denied.text
    with make_session_factory(application._settings.database_url)() as session:
        decision = session.get(DecisionItemRecord, UUID(proposal["action_id"]))
        assert decision.status == "open"
        assert session.query(SubmissionRecord).filter_by(decision_item_id=decision.id).count() == 1
        assert session.query(ReviewDecisionRecord).count() == 0
        assert session.query(MeetingRecord).count() == 0


def test_ax_meeting_and_initial_note_roll_back_together_when_note_creation_fails(tmp_path, monkeypatch) -> None:
    client, application = _stack(tmp_path)
    proposal = _ax_proposal(
        client,
        application,
        MINA,
        "mina",
        "meeting.create",
        "회의 생성 확인",
        {
            "organization_id": "scax",
            "title": "원자적인 회의와 회의록",
            "starts_at": "2026-09-14T03:00:00Z",
            "ends_at": "2026-09-14T04:00:00Z",
            "visibility": "private",
            "attendee_ids": [],
            "include_initial_note": True,
            "initial_note_body": "이 본문까지 함께 저장되어야 한다.",
        },
    )
    item = client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()

    def fail_note(*args, **kwargs):
        raise MeetingError("회의록 저장 실패")

    monkeypatch.setattr(SqlAlchemyMeetingRepository, "create_note", fail_note)
    failed = _command(
        client,
        MINA,
        item["action_item_id"],
        "confirm",
        expected_version=item["expected_version"],
        base_submission_version=1,
    )
    assert failed.status_code == 422, failed.text
    with make_session_factory(application._settings.database_url)() as session:
        decision = session.get(DecisionItemRecord, UUID(proposal["action_id"]))
        assert decision.status == "open"
        assert session.query(SubmissionRecord).filter_by(decision_item_id=decision.id).count() == 1
        assert session.query(ReviewDecisionRecord).count() == 0
        assert session.query(MeetingRecord).count() == 0


def test_ax_meeting_overlap_is_a_warning_and_not_a_confirmation_block(tmp_path) -> None:
    client, application = _stack(tmp_path)
    existing = client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "organization_id": "scax",
            "title": "이미 잡힌 회의",
            "starts_at": "2026-09-15T01:00:00Z",
            "ends_at": "2026-09-15T02:00:00Z",
            "visibility": "private",
            "attendee_ids": [],
        },
    )
    assert existing.status_code == 201, existing.text
    proposal = _ax_proposal(
        client,
        application,
        MINA,
        "mina",
        "meeting.create",
        "회의 생성 확인",
        {
            "organization_id": "scax",
            "title": "겹쳐도 확인 가능한 회의",
            "starts_at": "2026-09-15T01:30:00Z",
            "ends_at": "2026-09-15T02:30:00Z",
            "visibility": "private",
            "attendee_ids": [],
        },
    )
    item = client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()
    assert item["edit_contract"]["warnings"] == ["같은 조직에 시간이 겹치는 일정이 1건 있습니다."]
    confirmed = _command(
        client,
        MINA,
        item["action_item_id"],
        "confirm",
        expected_version=item["expected_version"],
        base_submission_version=1,
    )
    assert confirmed.status_code == 200, confirmed.text


def test_ax_meeting_does_not_reveal_overlap_counts_for_an_out_of_scope_organization(tmp_path) -> None:
    client, application = _stack(tmp_path)
    existing = client.post(
        "/api/meetings",
        headers=HYEON,
        json={
            "organization_id": "people",
            "title": "민아에게 비공개인 피플팀 회의",
            "starts_at": "2026-09-15T01:00:00Z",
            "ends_at": "2026-09-15T02:00:00Z",
            "visibility": "private",
            "attendee_ids": [],
        },
    )
    assert existing.status_code == 201, existing.text
    proposal = _ax_proposal(
        client,
        application,
        MINA,
        "mina",
        "meeting.create",
        "회의 생성 확인",
        {
            "organization_id": "people",
            "title": "범위 밖 조직을 넣은 제안",
            "starts_at": "2026-09-15T01:30:00Z",
            "ends_at": "2026-09-15T02:30:00Z",
            "visibility": "private",
            "attendee_ids": [],
        },
    )
    contract = client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()["edit_contract"]
    assert contract["warnings"] == []
    assert "people" not in {option["value"] for option in contract["fields"][0]["options"]}


def test_ax_task_assignment_uses_the_same_editor_but_keeps_assignee_acceptance_separate(tmp_path) -> None:
    client, application = _stack(tmp_path)
    proposal = _ax_proposal(
        client,
        application,
        JIHO,
        "jiho",
        "task.assign",
        "업무 배정 확인",
        {
            "title": "AX 요청 원안",
            "description": "상대가 판단할 업무",
            "assignee_id": "mina",
            "due_date": "2026-09-30",
            "checklist": ["검토"],
        },
    )
    item = client.get(f"/api/action-items/{proposal['action_id']}", headers=JIHO).json()

    assert [command["id"] for command in item["allowed_commands"]] == ["confirm", "reject"]
    assert item["allowed_commands"][0]["label"] == "이 내용으로 업무 요청"
    preview = {row["id"]: row for row in item["preview"]}
    assert preview["assignee"] == {"id": "assignee", "label": "담당자", "value": "민아 (구성원)", "kind": "person"}
    assert preview["requester"] == {"id": "requester", "label": "요청자", "value": "지호 (팀장)", "kind": "person"}
    contract = item["edit_contract"]
    assert contract["editor"] == "task"
    assignee = next(field for field in contract["fields"] if field["id"] == "assignee_id")
    assert assignee["editable"] is True
    assert {option["value"] for option in assignee["options"]} >= {"mina"}

    response = _command(
        client,
        JIHO,
        item["action_item_id"],
        "confirm",
        expected_version=item["expected_version"],
        base_submission_version=1,
        draft={**contract["values"], "title": "사람이 고친 요청"},
    )
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["status"] == "resolved"
    assert receipt["derived_task_id"]

    assignments = [row for row in _pending(client, MINA) if row["kind"] == "task.assignment"]
    assert len(assignments) == 1
    assert assignments[0]["subject"] == "사람이 고친 요청"
    assert assignments[0]["status"] == "awaiting_review"
    assert [row for row in client.get("/api/my-work", headers=MINA).json() if row["title"] == "사람이 고친 요청"] == []

    accepted = client.post(f"/api/task-assignments/{assignments[0]['action_item_id']}/accept", headers=MINA)
    assert accepted.status_code == 200, accepted.text
    settled = client.get(f"/api/action-items/{proposal['action_id']}", headers=JIHO).json()
    assert settled["allowed_commands"] == []
    too_late = _command(
        client,
        JIHO,
        proposal["action_id"],
        "cancel_assignment",
        expected_version=settled["expected_version"],
    )
    assert too_late.status_code == 422


def test_ax_task_assignment_can_be_cancelled_by_the_requester_only_before_acceptance(tmp_path) -> None:
    client, application = _stack(tmp_path)
    proposal = _ax_proposal(
        client,
        application,
        JIHO,
        "jiho",
        "task.assign",
        "업무 배정 확인",
        {"title": "취소 가능한 요청", "assignee_id": "mina", "due_date": "2026-09-30"},
    )
    item = client.get(f"/api/action-items/{proposal['action_id']}", headers=JIHO).json()
    confirmed = _command(
        client,
        JIHO,
        item["action_item_id"],
        "confirm",
        expected_version=item["expected_version"],
        base_submission_version=1,
    )
    assert confirmed.status_code == 200, confirmed.text
    receipt = confirmed.json()
    assert [command["id"] for command in receipt["allowed_commands"]] == ["cancel_assignment"]
    assert receipt["allowed_commands"][0]["label"] == "취소"
    conversation = client.get(f"/api/conversations/{proposal['conversation_id']}", headers=JIHO).json()
    [chat_receipt] = [row for row in conversation["actions"] if row["action_id"] == proposal["action_id"]]
    assert chat_receipt["result"]["status"] == "pending"
    assert [command["id"] for command in chat_receipt["commands"]] == ["cancel_assignment"]

    # The recipient cannot use the requester's command even when they know the original AX Action id.
    denied = _command(
        client,
        MINA,
        proposal["action_id"],
        "cancel_assignment",
        expected_version=receipt["expected_version"],
    )
    assert denied.status_code in {403, 404, 422}

    cancelled = _command(
        client,
        JIHO,
        proposal["action_id"],
        "cancel_assignment",
        expected_version=receipt["expected_version"],
    )
    resent = _command(
        client,
        JIHO,
        proposal["action_id"],
        "cancel_assignment",
        expected_version=receipt["expected_version"],
    )
    assert cancelled.status_code == resent.status_code == 200
    assert cancelled.json() == resent.json()
    assert cancelled.json()["allowed_commands"] == []
    conversation = client.get(f"/api/conversations/{proposal['conversation_id']}", headers=JIHO).json()
    [chat_receipt] = [row for row in conversation["actions"] if row["action_id"] == proposal["action_id"]]
    assert chat_receipt["result"]["status"] == "cancelled" and chat_receipt["commands"] == []
    assert [row for row in _pending(client, MINA) if row["kind"] == "task.assignment"] == []
    [sent] = client.get("/api/task-assignments/sent", headers=JIHO).json()
    assert sent["status"] == "cancelled" and sent["task"]["state"] == "cancelled"
    with make_session_factory(application._settings.database_url)() as session:
        assignment = session.get(TaskAssignmentRecord, UUID(sent["assignment_id"]))
        assert assignment is not None and assignment.source_review_decision_id is None


def test_legacy_assignment_approval_still_uses_the_canonical_confirm_operation(tmp_path) -> None:
    client, application = _stack(tmp_path)
    proposal = _ax_proposal(
        client,
        application,
        JIHO,
        "jiho",
        "task.assign",
        "업무 배정 확인",
        {"title": "호환 경로 요청", "assignee_id": "mina"},
    )

    approved = client.post(
        f"/api/actions/{proposal['action_id']}/decide",
        headers=JIHO,
        json={"expected_version": proposal["version"], "decision": "approve"},
    )

    assert approved.status_code == 200, approved.text
    assert approved.json()["state"] == "approved"
    [assignment] = [row for row in _pending(client, MINA) if row["kind"] == "task.assignment"]
    assert assignment["subject"] == "호환 경로 요청"
    detail = client.get(f"/api/action-items/{proposal['action_id']}", headers=JIHO).json()
    assert detail["rounds"][0]["decisions"][0]["decision"] == "confirm"


def test_confirming_a_changed_ax_draft_appends_submission_two_and_keeps_the_ai_original(tmp_path) -> None:
    client, application = _stack(tmp_path)
    proposal = _ax_proposal(
        client,
        application,
        JIHO,
        "jiho",
        "task.create_self",
        "업무 생성 확인",
        {"title": "AI 원안", "description": "처음 설명", "due_date": "2026-09-20"},
    )
    [item] = [row for row in _pending(client, JIHO) if row["kind"] == "ax.task.create_self"]
    confirmed = _command(
        client,
        JIHO,
        item["action_item_id"],
        "confirm",
        expected_version=item["expected_version"],
        base_submission_version=1,
        draft={
            "title": "사람이 고친 안",
            "description": "최종 설명",
            "start_date": "2026-09-10",
            "due_date": "2026-09-30",
            "checklist": ["준비", "검토"],
        },
    )
    assert confirmed.status_code == 200, confirmed.text

    detail = client.get(f"/api/action-items/{proposal['action_id']}", headers=JIHO).json()
    assert detail["subject"] == "사람이 고친 안"
    assert [round_["submission_version"] for round_ in detail["rounds"]] == [1, 2]
    original, final = detail["rounds"]
    assert original["snapshot"] == {"title": "AI 원안", "description": "처음 설명", "due_date": "2026-09-20"}
    assert original["decisions"] == []
    assert final["submitted_by"] == "jiho"
    assert final["snapshot"]["title"] == "사람이 고친 안"
    assert final["diff"]["title"] == {"before": "AI 원안", "after": "사람이 고친 안"}
    assert final["diff"]["due_date"] == {"before": "2026-09-20", "after": "2026-09-30"}

    [task] = [row for row in client.get("/api/my-work", headers=JIHO).json() if row["title"] == "사람이 고친 안"]
    task_detail = client.get(f"/api/tasks/{task['task_id']}", headers=JIHO).json()
    assert task_detail["version"] == 1 and task_detail["start_date"] == "2026-09-10"
    assert task_detail["due_date"] == "2026-09-30"
    assert task_detail["lineage"]["source_submission_id"] == final["submission_id"]


def test_ax_confirm_retry_is_one_receipt_and_a_failed_effect_rolls_back_the_new_round(tmp_path) -> None:
    client, application = _stack(tmp_path)
    proposal = _ax_proposal(
        client, application, JIHO, "jiho", "task.create_self", "업무 생성 확인", {"title": "원안", "due_date": "2026-09-30"}
    )
    [item] = [row for row in _pending(client, JIHO) if row["kind"] == "ax.task.create_self"]
    missing_reference = "00000000-0000-0000-0000-000000000099"
    failed_payload = {
        "expected_version": item["expected_version"],
        "base_submission_version": 1,
        "draft": {"title": "실패할 수정안", "due_date": "2026-09-30", "reference_task_ids": [missing_reference]},
    }
    failed = _command(client, JIHO, item["action_item_id"], "confirm", **failed_payload)
    assert failed.status_code in {404, 422}, failed.text

    # Submission 2 and its decision were flushed before the effect, but the application transaction rolled all of
    # them back together. The original question is still answerable.
    detail = client.get(f"/api/action-items/{proposal['action_id']}", headers=JIHO).json()
    assert [round_["submission_version"] for round_ in detail["rounds"]] == [1]
    assert detail["rounds"][0]["decisions"] == [] and detail["status"] == "awaiting_review"

    payload = {
        "expected_version": item["expected_version"],
        "base_submission_version": 1,
        "draft": {"title": "성공한 수정안", "description": "한 번만", "due_date": "2026-09-30"},
    }
    first = _command(client, JIHO, item["action_item_id"], "confirm", **payload)
    resent = _command(client, JIHO, item["action_item_id"], "confirm", **payload)
    assert first.status_code == resent.status_code == 200
    assert first.json() == resent.json()
    assert [row["title"] for row in client.get("/api/my-work", headers=JIHO).json()] == ["성공한 수정안"]
    detail = client.get(f"/api/action-items/{proposal['action_id']}", headers=JIHO).json()
    assert [round_["submission_version"] for round_ in detail["rounds"]] == [1, 2]
    assert sum(len(round_["decisions"]) for round_ in detail["rounds"]) == 1


def test_ax_confirm_rechecks_versions_and_current_authority_before_writing(tmp_path) -> None:
    client, application = _stack(tmp_path)
    proposal = _ax_proposal(
        client, application, JIHO, "jiho", "task.create_self", "업무 생성 확인", {"title": "권한 확인"}
    )
    [item] = [row for row in _pending(client, JIHO) if row["kind"] == "ax.task.create_self"]
    draft = {"title": "쓰이면 안 됨"}

    assert _command(
        client, JIHO, item["action_item_id"], "confirm",
        expected_version=item["expected_version"] + 1, base_submission_version=1, draft=draft,
    ).status_code == 422
    assert _command(
        client, JIHO, item["action_item_id"], "confirm",
        expected_version=item["expected_version"], base_submission_version=2, draft=draft,
    ).status_code == 422

    with make_session_factory(application._settings.database_url)() as session:
        session.execute(
            delete(RoleCapabilityRecord).where(
                RoleCapabilityRecord.role_id == "role:team-lead",
                RoleCapabilityRecord.capability_id == "action.decide",
            )
        )
        session.commit()
    denied = _command(
        client, JIHO, item["action_item_id"], "confirm",
        expected_version=item["expected_version"], base_submission_version=1, draft=draft,
    )
    assert denied.status_code in {403, 422}, denied.text
    with make_session_factory(application._settings.database_url)() as session:
        decision = session.get(DecisionItemRecord, UUID(proposal["action_id"]))
        assert decision.status == "open"
        assert session.query(SubmissionRecord).filter_by(decision_item_id=decision.id).count() == 1
        assert session.query(ReviewDecisionRecord).count() == 0


def test_ax_confirm_uses_the_direct_task_project_authorization_contract(tmp_path) -> None:
    client, application = _stack(tmp_path)
    project_response = client.post("/api/projects", headers=JIHO, json={"name": "AX 프로젝트"})
    assert project_response.status_code == 201, project_response.text
    project_id = project_response.json()["project_id"]
    proposal = _ax_proposal(
        client, application, JIHO, "jiho", "task.create_self", "업무 생성 확인", {"title": "프로젝트 원안", "due_date": "2026-09-30"}
    )
    [item] = [row for row in _pending(client, JIHO) if row["kind"] == "ax.task.create_self"]

    confirmed = _command(
        client,
        JIHO,
        item["action_item_id"],
        "confirm",
        expected_version=item["expected_version"],
        base_submission_version=1,
        draft={"title": "프로젝트 업무", "due_date": "2026-09-30", "project_id": project_id},
    )
    assert confirmed.status_code == 200, confirmed.text
    [task] = [row for row in client.get("/api/my-work", headers=JIHO).json() if row["title"] == "프로젝트 업무"]
    assert task["project_id"] == project_id


def test_one_query_returns_every_kind_of_pending_judgement_with_its_own_commands(tmp_path) -> None:
    client, application = _stack(tmp_path)

    # Two different origins, one ledger: a request Jiho must answer, and Jiho's own AX proposal awaiting approval.
    request = client.post(
        "/api/work-requests",
        headers=MINA,
        json={"title": "견적 재검토", "assignee_id": "jiho", "description": "9월 견적 재검토"},
    )
    assert request.status_code == 201, request.text
    request = request.json()
    proposal = _ax_proposal(client, application, JIHO, "jiho", "task.create_self", "업무 생성 확인", {"title": "AX가 만든 업무", "due_date": "2026-09-30"})

    pending = client.get("/api/action-items", headers=JIHO)
    assert pending.status_code == 200, pending.text
    items = {item["kind"]: item for item in pending.json()}
    assert set(items) == {"work_request.acceptance", "ax.task.create_self"}

    # One envelope shape for both, carrying the server's own presentation.
    for item in items.values():
        assert set(item) >= {
            "action_item_id",
            "kind",
            "status",
            "subject",
            "operation_label",
            "preview",
            "current_question",
            "allowed_commands",
            "submission_version",
            "waiting_on",
            "resource",
        }
        assert item["status"] == "awaiting_review"
        assert item["waiting_on"] == {"member_id": "jiho", "display_name": "지호 (팀장)"}
        assert isinstance(item["preview"], list)

    review = items["work_request.acceptance"]
    assert review["subject"] == "견적 재검토" and review["operation_label"] == "업무 요청"
    assert [command["id"] for command in review["allowed_commands"]] == ["accept", "adjust", "reject"]
    assert {row["label"] for row in review["preview"]} >= {"요청자", "설명"}
    assert review["resource"] == {"type": "work_request", "id": request["request_id"]}
    # A command that needs the actor to say why says so; the client does not decide that.
    assert [command["id"] for command in review["allowed_commands"] if command["requires_reason"]] == ["adjust", "reject"]

    proposal_item = items["ax.task.create_self"]
    assert proposal_item["subject"] == "AX가 만든 업무" and proposal_item["operation_label"] == "업무 생성"
    assert [command["id"] for command in proposal_item["allowed_commands"]] == ["confirm", "reject"]
    assert proposal_item["resource"] == {"type": "action", "id": proposal["action_id"]}

    # The same ledger is principal-scoped: Mina owes nothing here, she is waiting on Jiho.
    assert client.get("/api/action-items", headers=MINA).json() == []


def test_pending_holds_only_what_this_principal_must_answer_now(tmp_path) -> None:
    client, application = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "검토 요청", "assignee_id": "jiho"}).json()

    # Before anyone answers, the reviewer owes the decision and the requester owes nothing.
    [before] = client.get("/api/action-items", headers=JIHO).json()
    assert before["kind"] == "work_request.acceptance" and before["submission_version"] == 1
    assert client.get("/api/action-items", headers=MINA).json() == []

    # After the reviewer asks for a change, the same question moves to the requester; it never becomes a second item.
    adjusted = client.post(
        f"/api/work-requests/{request['request_id']}/negotiate",
        headers=JIHO,
        json={"expected_version": request["version"], "conditions": {"note": "기한을 늦춰 주세요"}},
    )
    assert adjusted.status_code == 200, adjusted.text
    assert client.get("/api/action-items", headers=JIHO).json() == []
    [waiting] = client.get("/api/action-items", headers=MINA).json()
    assert waiting["status"] == "awaiting_revision"
    assert waiting["waiting_on"] == {"member_id": "mina", "display_name": "민아 (구성원)"}
    assert waiting["current_question"] == "조정 요청에 답해 수정안을 다시 보낼지 결정하세요"
    assert [command["id"] for command in waiting["allowed_commands"]] == ["revise", "withdraw"]
    # Same judgement question, same identity across the round trip: an adjustment is never a second ActionItem.
    assert waiting["action_item_id"] == before["action_item_id"]
    assert waiting["submission_version"] == 1


def _pending(client, headers) -> list[dict]:
    return client.get("/api/action-items", headers=headers).json()


def _command(client, headers, action_item_id: str, command: str, **payload):
    return client.post(f"/api/action-items/{action_item_id}/commands/{command}", headers=headers, json=payload)


def test_commands_run_the_owning_module_operation_and_resolve_the_same_action_item(tmp_path) -> None:
    """One command endpoint, but the effect is always the owning module's own operation."""
    client, _ = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "승인될 요청", "assignee_id": "jiho"}).json()
    [item] = _pending(client, JIHO)

    accepted = _command(client, JIHO, item["action_item_id"], "accept", expected_version=item["expected_version"])
    assert accepted.status_code == 200, accepted.text
    # The WorkRequest module produced the effect: the assignee now holds a real Task.
    assert accepted.json()["status"] == "resolved"
    current = client.get("/api/work-requests", headers=MINA).json()
    [stored] = [row for row in current if row["request_id"] == request["request_id"]]
    assert stored["state"] == "accepted"
    assert [task["title"] for task in client.get("/api/my-work", headers=JIHO).json()] == ["승인될 요청"]
    # Answered questions leave the pending ledger for everyone.
    assert _pending(client, JIHO) == [] and _pending(client, MINA) == []


def test_an_adjustment_round_keeps_one_action_item_and_immutable_earlier_rounds(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    request = client.post(
        "/api/work-requests",
        headers=MINA,
        json={"title": "조정될 요청", "assignee_id": "jiho", "description": "처음 설명"},
    ).json()
    [first] = _pending(client, JIHO)

    # The reviewer must say why; an adjustment without a reason is refused.
    assert _command(client, JIHO, first["action_item_id"], "adjust", expected_version=first["expected_version"]).status_code == 422
    adjusted = _command(client, JIHO, first["action_item_id"], "adjust", expected_version=first["expected_version"], reason="기한을 늦춰 주세요")
    assert adjusted.status_code == 200, adjusted.text

    [waiting] = _pending(client, MINA)
    assert waiting["action_item_id"] == first["action_item_id"] and waiting["status"] == "awaiting_revision"

    # A revision that changes nothing is not a round.
    unchanged = _command(client, MINA, waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], changes={})
    assert unchanged.status_code == 422, unchanged.text

    revised = _command(
        client,
        MINA,
        waiting["action_item_id"],
        "revise",
        expected_version=waiting["expected_version"],
        changes={"title": "조정 반영한 요청", "description": "고친 설명"},
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["status"] == "awaiting_review" and revised.json()["submission_version"] == 2

    # The question came back to the reviewer as the same item, one round later.
    [second] = _pending(client, JIHO)
    assert second["action_item_id"] == first["action_item_id"] and second["submission_version"] == 2
    assert second["subject"] == "조정 반영한 요청"
    assert _pending(client, MINA) == []

    # Every earlier round survives with its own frozen content, decision and diff.
    detail = client.get(f"/api/action-items/{first['action_item_id']}", headers=MINA)
    assert detail.status_code == 200, detail.text
    rounds = detail.json()["rounds"]
    assert [row["submission_version"] for row in rounds] == [1, 2]
    assert rounds[0]["snapshot"]["title"] == "조정될 요청" and rounds[0]["snapshot"]["description"] == "처음 설명"
    assert rounds[0]["content_hash"] != rounds[1]["content_hash"]
    assert [decision["decision"] for decision in rounds[0]["decisions"]] == ["negotiate"]
    assert rounds[0]["decisions"][0]["reason"] == "기한을 늦춰 주세요"
    assert rounds[0]["decisions"][0]["actor_member_id"] == "jiho"
    assert rounds[1]["diff"]["title"] == {"before": "조정될 요청", "after": "조정 반영한 요청"}
    assert rounds[1]["decisions"] == []


def test_only_the_principal_the_item_waits_on_may_run_its_commands(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    client.post("/api/work-requests", headers=MINA, json={"title": "권한 확인 요청", "assignee_id": "jiho"}).json()
    [item] = _pending(client, JIHO)

    # The requester cannot answer their own request, and cannot revise before an adjustment was asked for.
    assert _command(client, MINA, item["action_item_id"], "accept", expected_version=item["expected_version"]).status_code in {403, 422}
    assert _command(client, MINA, item["action_item_id"], "revise", expected_version=item["expected_version"], changes={"title": "몰래 수정"}).status_code in {403, 422}
    # A command that is not offered on this item is refused rather than guessed at.
    assert _command(client, JIHO, item["action_item_id"], "withdraw", expected_version=item["expected_version"]).status_code == 422
    assert [row["title"] for row in client.get("/api/work-requests", headers=MINA).json()] == ["권한 확인 요청"]


def test_the_requester_can_withdraw_an_adjusted_request_and_it_leaves_every_ledger(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    client.post("/api/work-requests", headers=MINA, json={"title": "철회할 요청", "assignee_id": "jiho"}).json()
    [item] = _pending(client, JIHO)
    _command(client, JIHO, item["action_item_id"], "adjust", expected_version=item["expected_version"], reason="다시 생각해 주세요")
    [waiting] = _pending(client, MINA)

    withdrawn = _command(client, MINA, waiting["action_item_id"], "withdraw", expected_version=waiting["expected_version"])
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["status"] == "resolved"
    assert _pending(client, MINA) == [] and _pending(client, JIHO) == []
    [stored] = client.get("/api/work-requests", headers=MINA).json()
    assert stored["state"] == "withdrawn" and stored["task_id"] is None
    assert client.get("/api/my-work", headers=JIHO).json() == []


def test_an_ax_proposal_runs_its_effect_exactly_once_through_the_same_command_path(tmp_path) -> None:
    client, application = _stack(tmp_path)
    proposal = _ax_proposal(
        client,
        application,
        JIHO,
        "jiho",
        "task.create_self",
        "업무 생성 확인",
        {"title": "AX가 만든 업무", "due_date": "2026-09-30"},
    )
    [item] = [row for row in _pending(client, JIHO) if row["kind"] == "ax.task.create_self"]

    approved = _command(
        client, JIHO, item["action_item_id"], "confirm",
        expected_version=item["expected_version"], base_submission_version=item["submission_version"],
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "resolved"
    assert [task["title"] for task in client.get("/api/my-work", headers=JIHO).json()] == ["AX가 만든 업무"]

    # A retried command is an idempotent receipt, never a second Task.
    again = _command(
        client, JIHO, item["action_item_id"], "confirm",
        expected_version=item["expected_version"], base_submission_version=item["submission_version"],
    )
    assert again.status_code == 200, again.text
    assert [task["title"] for task in client.get("/api/my-work", headers=JIHO).json()] == ["AX가 만든 업무"]
    assert client.get("/api/actions", headers=JIHO).json()[0]["state"] == "approved"
    assert proposal["action_id"] == item["action_item_id"]


def test_a_direct_assignment_is_the_same_kind_of_question_in_the_same_ledger(tmp_path) -> None:
    client, application = _stack(tmp_path)
    jiho = application.authenticated_principal("jiho")
    assigned = application.assign_task(jiho, "배정된 업무", "mina", description="맡아 주세요")

    [item] = [row for row in _pending(client, MINA) if row["kind"] == "task.assignment"]
    assert item["subject"] == "배정된 업무" and item["operation_label"] == "업무 배정"
    assert item["current_question"] == "이 업무 배정을 수락할지 결정하세요"
    assert [command["id"] for command in item["allowed_commands"]] == ["accept", "decline"]
    assert [command["requires_reason"] for command in item["allowed_commands"]] == [False, True]
    assert {row["label"] for row in item["preview"]} >= {"설명", "배정자"}
    assert item["waiting_on"] == {"member_id": "mina", "display_name": "민아 (구성원)"}
    # The assigner is not the one who owes an answer.
    assert [row for row in _pending(client, JIHO) if row["kind"] == "task.assignment"] == []

    accepted = _command(client, MINA, item["action_item_id"], "accept", expected_version=item["expected_version"])
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "resolved"
    assert [task["title"] for task in client.get("/api/my-work", headers=MINA).json()] == ["배정된 업무"]
    assert [row for row in _pending(client, MINA) if row["kind"] == "task.assignment"] == []
    assert assigned["task"]["title"] == "배정된 업무"

    # A re-sent accept is a receipt, and a declined-after-accepted command is refused rather than guessed at.
    assert _command(client, MINA, item["action_item_id"], "accept", expected_version=item["expected_version"]).status_code == 200
    assert _command(client, MINA, item["action_item_id"], "decline", expected_version=item["expected_version"], reason="역시 어렵습니다").status_code == 422
    assert len(client.get("/api/my-work", headers=MINA).json()) == 1


def test_an_adjustment_carries_an_optional_structured_change_proposal_the_requester_answers(tmp_path) -> None:
    """조정 요청은 필수 사유 위에 '무엇을 이렇게 바꿔 달라'는 구조화 제안을 남길 수 있다."""
    client, _ = _stack(tmp_path)
    client.post(
        "/api/work-requests",
        headers=MINA,
        json={"title": "제안이 붙는 요청", "assignee_id": "jiho", "description": "처음 설명", "due_date": "2026-09-10"},
    )
    [first] = _pending(client, JIHO)

    adjusted = _command(
        client,
        JIHO,
        first["action_item_id"],
        "adjust",
        expected_version=first["expected_version"],
        reason="기한과 제목을 함께 손봐 주세요",
        changes={"title": "제안대로 고친 요청", "due_date": "2026-09-30"},
    )
    assert adjusted.status_code == 200, adjusted.text

    # The requester meets the proposal as a field-level ask, not as prose buried in the reason.
    [waiting] = _pending(client, MINA)
    assert waiting["suggested_changes"] == {"title": "제안대로 고친 요청", "due_date": "2026-09-30"}
    detail = client.get(f"/api/action-items/{waiting['action_item_id']}", headers=MINA).json()
    assert detail["suggested_changes"] == {"title": "제안대로 고친 요청", "due_date": "2026-09-30"}
    # It is a proposal, not an edit: the current round still holds what the requester actually submitted.
    assert detail["rounds"][0]["snapshot"]["title"] == "제안이 붙는 요청"
    assert detail["rounds"][0]["decisions"][0]["suggested_changes"] == {"title": "제안대로 고친 요청", "due_date": "2026-09-30"}
    assert detail["rounds"][0]["decisions"][0]["reason"] == "기한과 제목을 함께 손봐 주세요"

    # Applying it is the requester's own revision, and it clears the ask.
    revised = _command(
        client,
        MINA,
        waiting["action_item_id"],
        "revise",
        expected_version=waiting["expected_version"],
        changes={"title": "제안대로 고친 요청", "due_date": "2026-09-30"},
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["suggested_changes"] == {}
    [second] = _pending(client, JIHO)
    assert second["subject"] == "제안대로 고친 요청" and second["suggested_changes"] == {}

    # A reason-only adjustment stays legal and simply carries no proposal.
    plain = _command(client, JIHO, second["action_item_id"], "adjust", expected_version=second["expected_version"], reason="설명만 더 주세요")
    assert plain.status_code == 200, plain.text
    assert _pending(client, MINA)[0]["suggested_changes"] == {}


def test_a_resent_command_returns_the_same_receipt_instead_of_a_second_effect(tmp_path) -> None:
    """A lost response must not force the caller to choose between a duplicate Task and a stale error."""
    client, _ = _stack(tmp_path)
    client.post("/api/work-requests", headers=MINA, json={"title": "한 번만 수락될 요청", "assignee_id": "jiho"})
    [item] = _pending(client, JIHO)

    first = _command(client, JIHO, item["action_item_id"], "accept", expected_version=item["expected_version"])
    assert first.status_code == 200, first.text
    resent = _command(client, JIHO, item["action_item_id"], "accept", expected_version=item["expected_version"])
    assert resent.status_code == 200, resent.text
    assert resent.json()["status"] == "resolved" and resent.json()["action_item_id"] == item["action_item_id"]
    assert [task["title"] for task in client.get("/api/my-work", headers=JIHO).json()] == ["한 번만 수락될 요청"]

    # Only the decision that was actually made replays; a different answer to a settled question is still refused.
    assert _command(client, JIHO, item["action_item_id"], "reject", expected_version=item["expected_version"], reason="역시 아니오").status_code == 422
    detail = client.get(f"/api/action-items/{item['action_item_id']}", headers=JIHO).json()
    assert [decision["decision"] for decision in detail["rounds"][0]["decisions"]] == ["accept"]


def test_the_discussion_stays_on_one_action_item_across_rounds_and_never_moves_it(tmp_path) -> None:
    """댓글과 전체 논의는 ActionItem에 유지되고 상태를 직접 바꾸지 않는다."""
    client, _ = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "논의가 붙는 요청", "assignee_id": "jiho"}).json()
    asked = client.post(f"/api/work-requests/{request['request_id']}/comments", headers=JIHO, json={"body": "예산 근거가 있나요?"})
    assert asked.status_code == 201, asked.text
    [item] = _pending(client, JIHO)

    # Commenting is not a judgement: the question is still open and still Jiho's.
    assert _pending(client, JIHO)[0]["status"] == "awaiting_review"
    discussion = client.get(f"/api/action-items/{item['action_item_id']}", headers=JIHO).json()["discussion"]
    assert [row["body"] for row in discussion] == ["예산 근거가 있나요?"]

    _command(client, JIHO, item["action_item_id"], "adjust", expected_version=item["expected_version"], reason="근거를 붙여 주세요")
    [waiting] = _pending(client, MINA)
    answered = client.post(f"/api/work-requests/{request['request_id']}/comments", headers=MINA, json={"body": "9월 견적서를 붙였습니다"})
    assert answered.status_code == 201, answered.text
    _command(client, MINA, waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], changes={"description": "근거 추가"})

    # The same thread, with the same comment identities, survives the round it was written before.
    after = client.get(f"/api/action-items/{item['action_item_id']}", headers=JIHO).json()
    assert [row["comment_id"] for row in after["discussion"]] == [asked.json()["comment_id"], answered.json()["comment_id"]]
    assert [row["author_member_id"] for row in after["discussion"]] == ["jiho", "mina"]
    assert len(after["rounds"]) == 2


def test_the_detail_offers_commands_only_to_the_principal_the_item_waits_on(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    client.post("/api/work-requests", headers=MINA, json={"title": "차례가 있는 요청", "assignee_id": "jiho"})
    [item] = _pending(client, JIHO)

    # Both participants read the same question; only the one whose turn it is is offered a way to answer it.
    mine = client.get(f"/api/action-items/{item['action_item_id']}", headers=JIHO).json()
    theirs = client.get(f"/api/action-items/{item['action_item_id']}", headers=MINA).json()
    assert [command["id"] for command in mine["allowed_commands"]] == ["accept", "adjust", "reject"]
    assert theirs["allowed_commands"] == [] and theirs["waiting_on"]["member_id"] == "jiho"
    assert theirs["subject"] == mine["subject"] and theirs["status"] == mine["status"]


def _rounds(client, headers, action_item_id: str) -> list[dict]:
    return client.get(f"/api/action-items/{action_item_id}", headers=headers).json()["rounds"]


def test_only_the_answer_just_given_replays_and_a_stale_resend_is_refused(tmp_path) -> None:
    """A receipt must prove it is *this* answer coming back, not merely an answer of the same shape."""
    client, _ = _stack(tmp_path)
    client.post("/api/work-requests", headers=MINA, json={"title": "두 번 조정될 요청", "assignee_id": "jiho", "description": "처음 설명"})
    [round_one] = _pending(client, JIHO)
    item_id = round_one["action_item_id"]

    first_adjust = {"expected_version": round_one["expected_version"], "reason": "1회차 사유", "changes": {"title": "1회차 제안"}}
    assert _command(client, JIHO, item_id, "adjust", **first_adjust).status_code == 200
    [awaiting] = _pending(client, MINA)
    first_revise = {"expected_version": awaiting["expected_version"], "changes": {"title": "1회차 수정"}}
    assert _command(client, MINA, item_id, "revise", **first_revise).status_code == 200

    [round_two] = _pending(client, JIHO)
    second_adjust = {"expected_version": round_two["expected_version"], "reason": "2회차 사유", "changes": {"title": "2회차 제안"}}
    assert _command(client, JIHO, item_id, "adjust", **second_adjust).status_code == 200
    [awaiting_again] = _pending(client, MINA)
    second_revise = {"expected_version": awaiting_again["expected_version"], "changes": {"title": "2회차 수정"}}
    assert _command(client, MINA, item_id, "revise", **second_revise).status_code == 200

    before = _rounds(client, MINA, item_id)
    assert [row["submission_version"] for row in before] == [1, 2, 3]

    # A round-one command arriving late carries a version and a payload that no longer describe anything current.
    assert _command(client, MINA, item_id, "revise", **first_revise).status_code == 422
    assert _command(client, JIHO, item_id, "adjust", **first_adjust).status_code == 422
    # Even the right version with the wrong content is not this principal's answer.
    assert _command(client, MINA, item_id, "revise", expected_version=awaiting_again["expected_version"], changes={"title": "보내지 않은 수정"}).status_code == 422
    assert _command(client, JIHO, item_id, "adjust", expected_version=round_two["expected_version"], reason="보내지 않은 사유").status_code == 422

    # Only the answer that was actually just given comes back as a receipt.
    replayed = _command(client, MINA, item_id, "revise", **second_revise)
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["action_item_id"] == item_id and replayed.json()["submission_version"] == 3

    after = _rounds(client, MINA, item_id)
    assert [row["submission_version"] for row in after] == [1, 2, 3]
    assert [len(row["decisions"]) for row in after] == [len(row["decisions"]) for row in before]
    assert [row["content_hash"] for row in after] == [row["content_hash"] for row in before]

    # Accepting settles it, and that acceptance replays without producing a second Task.
    [final] = _pending(client, JIHO)
    accept = {"expected_version": final["expected_version"]}
    assert _command(client, JIHO, item_id, "accept", **accept).status_code == 200
    assert _command(client, JIHO, item_id, "accept", **accept).status_code == 200
    assert len(client.get("/api/my-work", headers=JIHO).json()) == 1
    # A stale acceptance, against the version the request had before it was accepted, is not a receipt either.
    assert _command(client, JIHO, item_id, "accept", expected_version=final["expected_version"] - 1).status_code == 422


def test_a_revision_that_changes_nothing_is_refused_however_it_is_written(tmp_path) -> None:
    """The UI disables the button; the server is what makes an empty round impossible."""
    client, _ = _stack(tmp_path)
    client.post(
        "/api/work-requests",
        headers=MINA,
        json={"title": "그대로인 요청", "assignee_id": "jiho", "description": "처음 설명", "due_date": "2026-09-10"},
    )
    [item] = _pending(client, JIHO)
    _command(client, JIHO, item["action_item_id"], "adjust", expected_version=item["expected_version"], reason="다시 봐 주세요")
    [waiting] = _pending(client, MINA)

    # Every field repeated at its current value is as empty a revision as sending no fields at all.
    for changes in ({}, {"title": "그대로인 요청"}, {"title": "그대로인 요청", "description": "처음 설명", "due_date": "2026-09-10"}):
        refused = _command(client, MINA, waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], changes=changes)
        assert refused.status_code == 422, f"{changes} was accepted: {refused.text}"

    # The same question is still waiting on the same round, untouched.
    [still] = _pending(client, MINA)
    assert still["action_item_id"] == waiting["action_item_id"] and still["expected_version"] == waiting["expected_version"]
    assert [row["submission_version"] for row in _rounds(client, MINA, waiting["action_item_id"])] == [1]

    # The REST route the request module owns refuses it on the same grounds, not just the command path.
    request_id = still["resource"]["id"]
    direct = client.post(
        f"/api/work-requests/{request_id}/resubmit",
        headers=MINA,
        json={"expected_version": still["expected_version"], "title": "그대로인 요청"},
    )
    assert direct.status_code == 422, direct.text
    assert [row["submission_version"] for row in _rounds(client, MINA, waiting["action_item_id"])] == [1]

    # A real change still goes through and is the only thing that makes a new round.
    accepted = _command(client, MINA, waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], changes={"title": "정말 바뀐 요청"})
    assert accepted.status_code == 200, accepted.text
    assert [row["submission_version"] for row in _rounds(client, MINA, waiting["action_item_id"])] == [1, 2]


def test_every_command_must_name_the_version_it_is_answering(tmp_path) -> None:
    """Optimistic concurrency is the contract, not an option: a command without a version is refused."""
    client, _ = _stack(tmp_path)
    client.post("/api/work-requests", headers=MINA, json={"title": "버전 계약", "assignee_id": "jiho"})
    [item] = _pending(client, JIHO)

    missing = client.post(f"/api/action-items/{item['action_item_id']}/commands/accept", headers=JIHO, json={})
    assert missing.status_code == 422, missing.text
    assert _command(client, JIHO, item["action_item_id"], "accept", expected_version=item["expected_version"] - 1).status_code == 422
    assert _command(client, JIHO, item["action_item_id"], "accept", expected_version=item["expected_version"] + 1).status_code == 422
    assert client.get("/api/my-work", headers=JIHO).json() == []
    assert _command(client, JIHO, item["action_item_id"], "accept", expected_version=item["expected_version"]).status_code == 200


def test_a_revision_may_only_change_the_fields_a_revision_owns(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    client.post("/api/work-requests", headers=MINA, json={"title": "허용 필드", "assignee_id": "jiho"})
    [item] = _pending(client, JIHO)
    _command(client, JIHO, item["action_item_id"], "adjust", expected_version=item["expected_version"], reason="고쳐 주세요")
    [waiting] = _pending(client, MINA)

    # A field a revision does not own is refused rather than quietly dropped from a successful-looking answer.
    refused = _command(
        client, MINA, waiting["action_item_id"], "revise",
        expected_version=waiting["expected_version"], changes={"title": "고친 제목", "assignee_id": "sora"},
    )
    assert refused.status_code == 422, refused.text
    assert "assignee_id" in refused.text
    assert [row["submission_version"] for row in _rounds(client, MINA, waiting["action_item_id"])] == [1]
    # The fields it does own still work, clear_due_date included.
    accepted = _command(
        client, MINA, waiting["action_item_id"], "revise",
        expected_version=waiting["expected_version"], changes={"title": "고친 제목", "clear_due_date": True},
    )
    assert accepted.status_code == 200, accepted.text


def test_a_direct_assignment_answers_on_the_task_version_it_was_shown(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    client.post("/api/tasks/assign", headers=JIHO, json={"title": "배정 A", "assignee_id": "mina"})
    client.post("/api/tasks/assign", headers=JIHO, json={"title": "배정 B", "assignee_id": "mina"})
    items = {row["subject"]: row for row in _pending(client, MINA)}
    accept_target, decline_target = items["배정 A"], items["배정 B"]

    assert _command(client, MINA, accept_target["action_item_id"], "accept").status_code == 422
    assert _command(client, MINA, accept_target["action_item_id"], "accept", expected_version=accept_target["expected_version"] + 1).status_code == 422
    assert _command(client, MINA, accept_target["action_item_id"], "accept", expected_version=accept_target["expected_version"]).status_code == 200
    # Acceptance leaves the Task version alone, so the same call replays as a receipt.
    replay = _command(client, MINA, accept_target["action_item_id"], "accept", expected_version=accept_target["expected_version"])
    assert replay.status_code == 200 and replay.json()["status"] == "resolved"

    # A decline cancels the Task, which moves it one version on; only that exact relation replays.
    assert _command(client, MINA, decline_target["action_item_id"], "decline", expected_version=decline_target["expected_version"], reason="여력 없음").status_code == 200
    receipt = _command(client, MINA, decline_target["action_item_id"], "decline", expected_version=decline_target["expected_version"], reason="여력 없음")
    assert receipt.status_code == 200 and receipt.json()["status"] == "resolved"
    assert _command(client, MINA, decline_target["action_item_id"], "decline", expected_version=decline_target["expected_version"], reason="다른 사유").status_code == 422
    assert _command(client, MINA, decline_target["action_item_id"], "decline", expected_version=decline_target["expected_version"] + 1, reason="여력 없음").status_code == 422
    assert [task["title"] for task in client.get("/api/my-work", headers=MINA).json()] == ["배정 A"]


def test_an_ax_proposal_answers_on_the_version_it_was_shown(tmp_path) -> None:
    client, application = _stack(tmp_path)
    proposal = _ax_proposal(client, application, MINA, "mina", "task.create_self", "업무 생성 확인", {"title": "AX 업무", "due_date": "2026-09-30"})
    [item] = _pending(client, MINA)
    assert item["action_item_id"] == proposal["action_id"]

    assert _command(client, MINA, item["action_item_id"], "confirm").status_code == 422
    assert _command(
        client, MINA, item["action_item_id"], "confirm",
        expected_version=item["expected_version"] + 1, base_submission_version=item["submission_version"],
    ).status_code == 422
    assert client.get("/api/my-work", headers=MINA).json() == []
    assert _command(
        client, MINA, item["action_item_id"], "confirm",
        expected_version=item["expected_version"], base_submission_version=item["submission_version"],
    ).status_code == 200
    replay = _command(
        client, MINA, item["action_item_id"], "confirm",
        expected_version=item["expected_version"], base_submission_version=item["submission_version"],
    )
    assert replay.status_code == 200 and replay.json()["status"] == "resolved"
    # The version the effect actually consumed is the only one that replays.
    assert _command(
        client, MINA, item["action_item_id"], "confirm",
        expected_version=item["expected_version"] + 1, base_submission_version=item["submission_version"],
    ).status_code == 422
    assert [task["title"] for task in client.get("/api/my-work", headers=MINA).json()] == ["AX 업무"]


def test_a_legacy_negotiate_cannot_leave_the_ledger_unreadable(tmp_path) -> None:
    """The compatibility endpoint writes what the canonical reader can read, or it does not write at all."""
    client, _ = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "원장 보호", "assignee_id": "jiho"}).json()

    poisoned = client.post(
        f"/api/work-requests/{request['request_id']}/negotiate",
        headers=JIHO,
        json={"expected_version": request["version"], "conditions": {"note": "조정", "changes": {"assignee_id": "sora", "priority": "high"}}},
    )
    assert poisoned.status_code == 422, poisoned.text

    # Both sides can still read their judgement ledger, and the request was not moved.
    assert client.get("/api/action-items", headers=MINA).status_code == 200
    assert client.get("/api/action-items", headers=JIHO).status_code == 200
    assert client.get("/api/work-requests", headers=MINA).json()[0]["state"] == "pending"

    # A proposal the canonical path accepts goes through the legacy endpoint too, and stays readable.
    fine = client.post(
        f"/api/work-requests/{request['request_id']}/negotiate",
        headers=JIHO,
        json={"expected_version": request["version"], "conditions": {"note": "조정", "changes": {"due_date": "2026-12-01"}}},
    )
    assert fine.status_code == 200, fine.text
    [waiting] = _pending(client, MINA)
    assert waiting["suggested_changes"] == {"due_date": "2026-12-01"}


def test_an_adjustment_may_only_propose_the_fields_an_adjustment_owns(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    client.post("/api/work-requests", headers=MINA, json={"title": "제안 필드", "assignee_id": "jiho"})
    [item] = _pending(client, JIHO)

    refused = _command(
        client, JIHO, item["action_item_id"], "adjust",
        expected_version=item["expected_version"], reason="담당을 바꿔 주세요", changes={"assignee_id": "sora"},
    )
    assert refused.status_code == 422 and "assignee_id" in refused.text
    # Nothing moved: the question is still the reviewer's, on the same round, with no decision recorded.
    [again] = _pending(client, JIHO)
    assert again["expected_version"] == item["expected_version"]
    assert _rounds(client, JIHO, item["action_item_id"])[0]["decisions"] == []
    assert client.get("/api/action-items", headers=MINA).status_code == 200


def test_an_ax_approval_replays_only_on_the_version_it_consumed(tmp_path) -> None:
    """The Action's own approval door holds the same exact-version contract as the judgement it gates."""
    client, application = _stack(tmp_path)
    proposal = _ax_proposal(client, application, MINA, "mina", "task.create_self", "업무 생성 확인", {"title": "AX 업무", "due_date": "2026-09-30"})
    decided = client.post(
        f"/api/actions/{proposal['action_id']}/decide", headers=MINA,
        json={"expected_version": proposal["version"], "decision": "approve"},
    )
    assert decided.status_code == 200, decided.text

    receipt = client.post(
        f"/api/actions/{proposal['action_id']}/decide", headers=MINA,
        json={"expected_version": proposal["version"], "decision": "approve"},
    )
    assert receipt.status_code == 200 and receipt.json()["state"] == "approved"
    # A version this decision never consumed is stale, even though the outcome happens to match.
    for version in (proposal["version"] + 1, proposal["version"] + 2, proposal["version"] - 1):
        stale = client.post(
            f"/api/actions/{proposal['action_id']}/decide", headers=MINA,
            json={"expected_version": version, "decision": "approve"},
        )
        assert stale.status_code == 422, f"{version}: {stale.text}"
    [task] = client.get("/api/my-work", headers=MINA).json()
    task_detail = client.get(f"/api/tasks/{task['task_id']}", headers=MINA).json()
    action_detail = client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()
    assert task_detail["lineage"]["source_submission_id"] == action_detail["rounds"][0]["submission_id"]
    assert task_detail["lineage"]["source_review_decision_id"] == action_detail["rounds"][0]["decisions"][0]["review_decision_id"]


def test_a_revision_can_clear_the_description_by_saying_it_is_empty(tmp_path) -> None:
    """An explicitly empty description is a change — the requester removing it — not an absent field."""
    client, _ = _stack(tmp_path)
    client.post(
        "/api/work-requests",
        headers=MINA,
        json={"title": "설명 지우기", "assignee_id": "jiho", "description": "지워질 설명", "due_date": "2026-09-30"},
    )
    [item] = _pending(client, JIHO)
    _command(client, JIHO, item["action_item_id"], "adjust", expected_version=item["expected_version"], reason="설명을 빼 주세요")
    [waiting] = _pending(client, MINA)

    cleared = _command(
        client, MINA, waiting["action_item_id"], "revise",
        expected_version=waiting["expected_version"], changes={"description": ""},
    )
    assert cleared.status_code == 200, cleared.text
    rounds = _rounds(client, MINA, waiting["action_item_id"])
    assert [row["submission_version"] for row in rounds] == [1, 2]
    assert rounds[1]["snapshot"]["description"] is None
    assert rounds[1]["snapshot"]["title"] == "설명 지우기" and rounds[1]["snapshot"]["due_date"] == "2026-09-30"
    assert rounds[1]["diff"]["description"] == {"before": "지워질 설명", "after": None}


def test_an_empty_title_is_refused_and_a_date_is_only_removed_by_asking_to_remove_it(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    client.post(
        "/api/work-requests",
        headers=MINA,
        json={"title": "필드 계약", "assignee_id": "jiho", "description": "설명", "due_date": "2026-09-30"},
    )
    [item] = _pending(client, JIHO)
    _command(client, JIHO, item["action_item_id"], "adjust", expected_version=item["expected_version"], reason="고쳐 주세요")
    [waiting] = _pending(client, MINA)
    revise = lambda changes: _command(
        client, MINA, waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], changes=changes
    )

    # A request must keep a title, and an empty date is not how a date is removed.
    empty_title = revise({"title": ""})
    assert empty_title.status_code == 422 and "제목" in empty_title.text
    assert revise({"title": "   "}).status_code == 422
    empty_date = revise({"due_date": ""})
    assert empty_date.status_code == 422 and "기한" in empty_date.text
    # Naming the date and leaving it empty stays an error even when the revision does change something else, so it is
    # never quietly dropped from an answer that otherwise succeeded.
    beside_a_real_change = revise({"title": "정말 바뀐 제목", "due_date": ""})
    assert beside_a_real_change.status_code == 422 and "기한" in beside_a_real_change.text
    assert revise({"title": "정말 바뀐 제목", "due_date": None}).status_code == 422
    assert [row["submission_version"] for row in _rounds(client, MINA, waiting["action_item_id"])] == [1]

    removed = revise({"clear_due_date": True})
    assert removed.status_code == 200, removed.text
    rounds = _rounds(client, MINA, waiting["action_item_id"])
    assert rounds[1]["snapshot"]["due_date"] is None and rounds[1]["snapshot"]["description"] == "설명"


def test_a_receipt_survives_the_basis_moving_underneath_it(tmp_path) -> None:
    """A revision's receipt is pinned by the round it produced, not by a request version anyone may move."""
    client, _ = _stack(tmp_path)
    request = client.post(
        "/api/work-requests", headers=MINA, json={"title": "영수증 유지", "assignee_id": "jiho", "description": "처음"}
    ).json()
    [item] = _pending(client, JIHO)
    _command(client, JIHO, item["action_item_id"], "adjust", expected_version=item["expected_version"], reason="고쳐 주세요")
    [waiting] = _pending(client, MINA)

    revision = {"expected_version": waiting["expected_version"], "changes": {"title": "고친 요청"}}
    assert _command(client, MINA, waiting["action_item_id"], "revise", **revision).status_code == 200
    assert _command(client, MINA, waiting["action_item_id"], "revise", **revision).status_code == 200

    # Someone adopts evidence on the new round, moving the request on.
    adopted = client.post(
        f"/api/work-requests/{request['request_id']}/evidence", headers=MINA, files={"file": ("근거.txt", b"one", "text/plain")}
    )
    assert adopted.status_code == 201 and adopted.json()["request_version"] > waiting["expected_version"]

    # The lost-response retry is still the same answer, not a stale error.
    replayed = _command(client, MINA, waiting["action_item_id"], "revise", **revision)
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["submission_version"] == 2
    assert [row["submission_version"] for row in _rounds(client, MINA, waiting["action_item_id"])] == [1, 2]
    # A different revision on that settled round is still refused.
    assert _command(
        client, MINA, waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], changes={"title": "보내지 않은 수정"}
    ).status_code == 422


def test_a_decision_keeps_the_server_facts_apart_from_the_conditions_a_person_wrote(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "조건 분리", "assignee_id": "jiho"}).json()
    client.post(f"/api/work-requests/{request['request_id']}/evidence", headers=MINA, files={"file": ("근거.txt", b"one", "text/plain")})
    # Read the item after the basis moved: the version it hands out is the one the answer will consume.
    [item] = _pending(client, JIHO)
    adjusted = _command(
        client, JIHO, item["action_item_id"], "adjust",
        expected_version=item["expected_version"], reason="기한을 늦춰 주세요", changes={"due_date": "2026-12-01"},
    )
    assert adjusted.status_code == 200, adjusted.text

    timeline = client.get(f"/api/work-requests/{request['request_id']}/timeline", headers=JIHO).json()
    [decision] = timeline["review_decisions"]
    conditions = decision["conditions"]
    # What the reviewer said stays at the top, where a client that renders conditions has always found it.
    assert set(conditions) == {"note", "changes", "_decision"}
    assert conditions["changes"] == {"due_date": "2026-12-01"} and conditions["note"] == "기한을 늦춰 주세요"
    # What the server froze lives under one reserved key of its own.
    facts = conditions["_decision"]
    assert set(facts) == {"expected_version", "evidence_hash", "evidence_manifest"}
    assert facts["expected_version"] == item["expected_version"]
    assert decision["evidence_hash"] == facts["evidence_hash"]
    assert [row["attachment_id"] for row in facts["evidence_manifest"]] == [row["attachment_id"] for row in timeline["submissions"][0]["evidence"]]
    # The requester still reads the proposal the way they always did.
    assert _pending(client, MINA)[0]["suggested_changes"] == {"due_date": "2026-12-01"}


def test_a_revision_receipt_holds_to_the_version_it_actually_consumed(tmp_path) -> None:
    """Evidence may move the request afterwards; that must not widen which requests count as this answer."""
    client, _ = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "정확한 재전송", "assignee_id": "jiho"}).json()
    [first] = _pending(client, JIHO)
    _command(client, JIHO, first["action_item_id"], "adjust", expected_version=first["expected_version"], reason="고쳐 주세요")
    [waiting] = _pending(client, MINA)
    changes = {"title": "고친 요청"}

    assert _command(client, MINA, waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], changes=changes).status_code == 200
    client.post(f"/api/work-requests/{request['request_id']}/evidence", headers=MINA, files={"file": ("근거.txt", b"one", "text/plain")})

    # The original call, re-sent: still the same answer even though the request has moved on since.
    assert _command(client, MINA, waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], changes=changes).status_code == 200
    # Any other version is a different request, not this answer coming back.
    for version in (first["expected_version"], waiting["expected_version"] - 1, waiting["expected_version"] + 1, waiting["expected_version"] + 2):
        stale = _command(client, MINA, waiting["action_item_id"], "revise", expected_version=version, changes=changes)
        assert stale.status_code == 422, f"{version}: {stale.text}"
    assert [row["submission_version"] for row in _rounds(client, MINA, waiting["action_item_id"])] == [1, 2]
