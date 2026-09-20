"""One judgement ledger.

**W1 이후 수락 회차는 과거 행에만 있다** — 신규 요청·배정은 판단 없이 업무와 활성 담당을 세운다
(WORK-001 Phase 4). 봉투·명령·회차·영수증을 다루는 코드와 데이터는 그대로 남으므로, 예전 배포가
남긴 모양(`legacy_acceptance`)을 세워 계속 검증한다. 완료 승인과 AX 확인은 신규 경로에서도 그대로다.


Every path that needs a person's decision — a WorkRequest, a direct TaskAssignment, an AX gated proposal — is one
canonical ActionItem in one query, with one envelope shape. What differs between kinds is the policy the server
applies: which commands the current principal may run, and what the question in front of them says. The client reads
that projection; it never derives a command, a field, or a permission from the kind.
"""
from dataclasses import replace
from uuid import UUID

# SCAX-SPEC-004의 현재 회의 계약으로 채팅 제안과 확인 실행을 검증한다.


from fastapi.testclient import TestClient
from legacy_acceptance import make_assignment_look_pending, pending_assignment, pending_request
from sqlalchemy import delete

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.ax_execution.actions import action_payload_hash
from ax_workspace.platform.persistence import (
    ActionItemRecord,
    ConversationTurnRecord,
    DecisionItemRecord,
    MeetingLineRecord,
    MeetingRecord,
    ReviewAssignmentRecord,
    ReviewDecisionRecord,
    ResourceRelationshipRecord,
    RoleCapabilityRecord,
    SubjectRecord,
    SubjectVersionRecord,
    SubmissionRecord,
    TaskAssignmentRecord,
    make_session_factory,
)
MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
HYEON = {"X-Demo-Persona": "hyeon"}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    app = create_app(settings)
    client = TestClient(app)
    client.database_url = database_url
    return client, app.state.workflow_application


def _ax_proposal(
    client,
    application,
    headers,
    persona: str,
    action_type: str,
    title: str,
    payload: dict,
    *,
    body: str ="제안해줘",
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


def _make_pre_cutover(
    application,
    proposal: dict,
    *,
    action_type: str | None = None,
    payload: dict | None = None,
) -> None:
    """Leave the transitional Action row exactly as it could exist before the canonical ledger cutover."""
    with make_session_factory(application._settings.database_url)() as session:
        action = session.get(ActionItemRecord, UUID(proposal["action_id"]))
        if action_type is not None:
            action.action_type = action_type
        if payload is not None:
            action.payload = payload
            action.payload_hash = action_payload_hash(payload)
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
    _make_pre_cutover(application, proposal)

    [legacy] = [row for row in _pending(client, JIHO) if row["action_item_id"] == proposal["action_id"]]
    assert [command["id"] for command in legacy["allowed_commands"]] == ["approve", "reject"]
    approved = _command(
        client, JIHO, legacy["action_item_id"], "approve", expected_version=legacy["expected_version"]
    )
    assert approved.status_code == 200, approved.text
    assert [task["title"] for task in client.get("/api/my-work", headers=JIHO).json()] == ["기존 pending 업무"]


def test_a_retired_meeting_create_row_stays_readable_but_can_no_longer_run(tmp_path) -> None:
    """The old creation contract is withdrawn. A row left pending before the cutover still reads, and only clears."""
    client, application = _stack(tmp_path)
    current = _ax_proposal(
        client,
        application,
        MINA,
        "mina",
        "meeting.reservation.create",
        "회의 생성 확인",
        {
            "title": "임시 현재 회의",
            "starts_at": "2026-09-20T01:00:00Z",
            "ends_at": "2026-09-20T02:00:00Z",
        },
    )
    legacy_payload = {
        "organization_id": "product",
        "title": "병합 전 승인 대기 회의",
        "description": "기존 설명",
        "starts_at": "2026-09-20T01:00:00Z",
        "ends_at": "2026-09-20T02:00:00Z",
        "visibility": "private",
        "attendee_ids": [],
    }
    _make_pre_cutover(application, current, action_type="meeting.create", payload=legacy_payload)

    [retired] = [row for row in _pending(client, MINA) if row["action_item_id"] == current["action_id"]]
    assert retired["operation_label"] == "회의 생성"
    assert "회의 화면에서 새로 예약" in retired["current_question"]
    assert [command["id"] for command in retired["allowed_commands"]] == ["reject"]

    refused = _command(
        client, MINA, retired["action_item_id"], "approve", expected_version=retired["expected_version"]
    )
    assert refused.status_code == 422, refused.text

    cleared = _command(
        client, MINA, retired["action_item_id"], "reject", expected_version=retired["expected_version"]
    )
    assert cleared.status_code == 200, cleared.text
    with make_session_factory(application._settings.database_url)() as session:
        assert session.query(MeetingRecord).filter_by(title=legacy_payload["title"]).count() == 0


def test_a_pre_cutover_meeting_share_action_still_executes_on_the_current_model(tmp_path) -> None:
    client, application = _stack(tmp_path)
    made = client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "title": "공유할 회의",
            "starts_at": "2026-09-20T01:00:00Z",
            "ends_at": "2026-09-20T02:00:00Z",
        },
    ).json()["meeting"]
    meeting_id = UUID(made["meeting_id"])
    with make_session_factory(application._settings.database_url)() as session:
        meeting_version = session.get(MeetingRecord, meeting_id).version

    share = _ax_proposal(
        client,
        application,
        MINA,
        "mina",
        "meeting.share",
        "회의 공유 확인",
        {
            "meeting_id": str(meeting_id),
            "member_id": "hyeon",
            "expected_version": meeting_version,
        },
    )
    _make_pre_cutover(application, share)
    [share_item] = [row for row in _pending(client, MINA) if row["action_item_id"] == share["action_id"]]
    shared = _command(
        client,
        MINA,
        share_item["action_item_id"],
        "approve",
        expected_version=share_item["expected_version"],
    )
    assert shared.status_code == 200, shared.text
    assert "hyeon" in {
        row["member_id"]
        for row in application.meeting_viewers(
            application.authenticated_principal("mina"), meeting_id
        )
    }


def test_pre_cutover_visibility_update_maps_public_access_without_discarding_explicit_shares(tmp_path) -> None:
    client, application = _stack(tmp_path)
    principal = application.authenticated_principal("mina")
    made = client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "title": "공개 범위 이행 대상",
            "starts_at": "2026-09-21T01:00:00Z",
            "ends_at": "2026-09-21T02:00:00Z",
        },
    ).json()["meeting"]
    meeting_id = made["meeting_id"]
    application.share_meeting_with(principal, UUID(meeting_id), ["hyeon"])

    def approve_visibility(visibility: str) -> None:
        with make_session_factory(application._settings.database_url)() as session:
            expected_version = session.get(MeetingRecord, UUID(meeting_id)).version
        proposal = _ax_proposal(
            client,
            application,
            MINA,
            "mina",
            "meeting.update",
            "기존 공개 범위 변경",
            {
                "meeting_id": meeting_id,
                "expected_version": expected_version,
                "visibility": visibility,
            },
        )
        _make_pre_cutover(application, proposal)
        [item] = [row for row in _pending(client, MINA) if row["action_item_id"] == proposal["action_id"]]
        response = _command(
            client,
            MINA,
            item["action_item_id"],
            "approve",
            expected_version=item["expected_version"],
        )
        assert response.status_code == 200, response.text

    approve_visibility("public")
    with make_session_factory(application._settings.database_url)() as session:
        assert session.query(ResourceRelationshipRecord).filter_by(
            resource_type="meeting",
            resource_id=meeting_id,
            relationship_kind="legacy_public_share",
        ).count() > 0
    assert client.get(f"/api/meetings/{meeting_id}", headers=JIHO).status_code == 200
    moved_principal = replace(
        application.authenticated_principal("jiho"),
        organization_scope=frozenset(),
    )
    moved_board = application.meeting_board(moved_principal)
    assert meeting_id in {
        row["meeting_id"]
        for row in [*moved_board["upcoming"], *moved_board["past"]["items"]]
    }

    approve_visibility("private")
    with make_session_factory(application._settings.database_url)() as session:
        assert session.query(ResourceRelationshipRecord).filter_by(
            resource_type="meeting",
            resource_id=meeting_id,
            relationship_kind="legacy_public_share",
            valid_until=None,
        ).count() == 0
    assert client.get(f"/api/meetings/{meeting_id}", headers=JIHO).status_code == 404
    moved_board = application.meeting_board(moved_principal)
    assert meeting_id not in {
        row["meeting_id"]
        for row in [*moved_board["upcoming"], *moved_board["past"]["items"]]
    }
    assert "hyeon" in {
        row["member_id"] for row in application.meeting_viewers(principal, UUID(meeting_id))
    }


def test_other_pre_cutover_meeting_commands_are_consumed_by_current_owners(tmp_path) -> None:
    client, application = _stack(tmp_path)
    principal = application.authenticated_principal("mina")
    created = client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "title": "기존 명령 대상",
            "starts_at": "2026-09-22T01:00:00Z",
            "ends_at": "2026-09-22T02:00:00Z",
        },
    ).json()
    meeting_id = created["meeting"]["meeting_id"]

    def version() -> int:
        with make_session_factory(application._settings.database_url)() as session:
            return session.get(MeetingRecord, UUID(meeting_id)).version

    def approve(kind: str, payload: dict):
        proposal = _ax_proposal(client, application, MINA, "mina", kind, "기존 회의 명령", payload)
        _make_pre_cutover(application, proposal)
        [item] = [row for row in _pending(client, MINA) if row["action_item_id"] == proposal["action_id"]]
        response = _command(
            client,
            MINA,
            item["action_item_id"],
            "approve",
            expected_version=item["expected_version"],
        )
        assert response.status_code == 200, response.text
        return response.json()["execution_result"]

    updated = approve(
        "meeting.update",
        {
            "meeting_id": meeting_id,
            "expected_version": version(),
            "title": "옮겨진 기존 회의",
            "description": "옛 설명",
        },
    )
    assert updated["meeting"]["title"] == "옮겨진 기존 회의"
    assert updated["meeting"]["purpose"] == "옛 설명"

    application.share_meeting_with(principal, UUID(meeting_id), ["hyeon"])
    viewers = approve(
        "meeting.revoke_share",
        {"meeting_id": meeting_id, "member_id": "hyeon", "expected_version": version()},
    )
    assert "hyeon" not in {row["member_id"] for row in viewers["viewers"]}

    approve("meeting.note.create", {"meeting_id": meeting_id, "body": "첫 기존 회의록"})
    approve(
        "meeting.note.save",
        {"meeting_id": meeting_id, "expected_version": 1, "body": "고친 기존 회의록"},
    )
    approve("meeting.note.finalize", {"meeting_id": meeting_id, "expected_version": 2})
    with make_session_factory(application._settings.database_url)() as session:
        assert [row[0] for row in session.query(MeetingLineRecord.text).all()] == [
            "첫 기존 회의록",
            "고친 기존 회의록",
        ]


def test_pre_cutover_note_versions_are_mapped_independently_from_the_current_meeting_version(tmp_path) -> None:
    client, application = _stack(tmp_path)
    meeting_id = client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "title": "회의록 버전 이행 대상",
            "starts_at": "2026-09-23T01:00:00Z",
            "ends_at": "2026-09-23T02:00:00Z",
        },
    ).json()["meeting"]["meeting_id"]

    def command(kind: str, payload: dict):
        proposal = _ax_proposal(client, application, MINA, "mina", kind, "기존 회의록 명령", payload)
        _make_pre_cutover(application, proposal)
        [item] = [row for row in _pending(client, MINA) if row["action_item_id"] == proposal["action_id"]]
        return _command(
            client,
            MINA,
            item["action_item_id"],
            "approve",
            expected_version=item["expected_version"],
        )

    saved = command(
        "meeting.note.save",
        {"meeting_id": meeting_id, "expected_version": 2, "body": "이행된 세 번째 버전"},
    )
    assert saved.status_code == 200, saved.text
    stale = command(
        "meeting.note.save",
        {"meeting_id": meeting_id, "expected_version": 2, "body": "오래된 두 번째 버전"},
    )
    assert stale.status_code == 409, stale.text
    finalized = command(
        "meeting.note.finalize",
        {"meeting_id": meeting_id, "expected_version": 3},
    )
    assert finalized.status_code == 200, finalized.text
    with make_session_factory(application._settings.database_url)() as session:
        meeting = session.get(MeetingRecord, UUID(meeting_id))
        assert meeting.version == 1
        assert [row[0] for row in session.query(MeetingLineRecord.text).all()] == ["이행된 세 번째 버전"]


def test_confirming_an_unchanged_ax_draft_executes_submission_one_with_full_lineage(tmp_path) -> None:
    client, application = _stack(tmp_path)
    _ax_proposal(
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
        # 참조자 — 내 업무와 업무 요청이 함께 쓰는 공통 payload 의 칸이다.
        "cc_member_ids": [],
        # 선행업무 — 같은 공통 payload 의 칸. 생성 표면 전부가 같은 배열을 받는다 (SPEC-001 §5).
        "preceding_task_ids": [],
        # 결재자 — `업무` 갈래만 여는 칸 (SPEC-001 §7 OQ-M).
        "approver_id": None,
        # 담당 — W1 이 생성 입력에 연 필드. 초안 계약이 같은 집합을 쓴다 (WORK-001 Phase 6).
        "assignee_id": None,
    }
    fields = {field["id"]: field for field in contract["fields"]}
    assert set(fields) == {
        "title",
        "description",
        "assignee_id",
        "start_date",
        "due_date",
        "project_id",
        # 참조자도 확인 화면에서 고칠 수 있다 — 읽기와 논의만 여는 자리라 담당을 옮기지 않는다.
        "cc_member_ids",
        # 선행·결재자도 `업무` 초안의 칸이다 — 생성 표면이 같은 배열·같은 값을 받는다.
        "preceding_task_ids",
        "approver_id",
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
    # 참조자 후보에 **자기 자신은 없다** — 초안을 만든 사람이 이미 담당 자리에 서 있다.
    assert "jiho" not in {option["value"] for option in fields["cc_member_ids"]["options"]}

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
    # 확인이 끝나면 요청이 서고 업무도 함께 선다. **담당은 수락이 세운다** — 받는 사람이 답하기 전에는
    # 그 사람의 「내 업무」에 서지 않고 응답 대기에 선다 (SPEC-003 §4 발송 · 정책 V-10).
    assert request_detail["state"] == "pending"
    assert client.get("/api/my-work", headers=JIHO).json() == []
    accepted = client.post(
        f"/api/work-requests/{request['request_id']}/accept",
        headers=JIHO, json={"expected_version": request_detail["version"]},
    )
    assert accepted.status_code == 200, accepted.text
    [assigned] = client.get("/api/my-work", headers=JIHO).json()
    assert assigned["title"] == request_detail["title"]

    # 수락 판단은 생기지 않는다 — 요청이 선 자리에서 업무가 이미 서 있다 (WORK-001 Phase 4).
    assert _pending(client, JIHO) == []
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


def test_ax_meeting_confirm_rechecks_current_manage_authority_before_writing(tmp_path) -> None:
    client, application = _stack(tmp_path)
    proposal = _ax_proposal(
        client,
        application,
        MINA,
        "mina",
        "meeting.reservation.create",
        "회의 생성 확인",
        {
            "title": "권한이 유지될 때만 생성",
            "starts_at": "2026-09-14T01:00:00Z",
            "ends_at": "2026-09-14T02:00:00Z",
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
    assert denied.status_code in {403, 404, 422}, denied.text
    with make_session_factory(application._settings.database_url)() as session:
        decision = session.get(DecisionItemRecord, UUID(proposal["action_id"]))
        assert decision.status == "open"
        assert session.query(SubmissionRecord).filter_by(decision_item_id=decision.id).count() == 1
        assert session.query(ReviewDecisionRecord).count() == 0
        assert session.query(MeetingRecord).count() == 0


def test_ax_task_assignment_uses_the_same_editor_and_stands_at_once(tmp_path) -> None:
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

    # 사람이 확인한 것이 곧 배정이다 — 받는 사람의 판단이 따로 열리지 않는다 (WORK-001 Phase 4).
    assert [row for row in _pending(client, MINA) if row["kind"] == "task.assignment"] == []
    [mine] = [row for row in client.get("/api/my-work", headers=MINA).json() if row["title"] == "사람이 고친 요청"]
    assert mine["assignment"]["kind"] == "direct" and mine["assignment"]["status"] == "active"

    # 상대가 이미 들고 있으므로 보낸 쪽의 되돌리기(취소)도 남지 않는다. 출구는 문의·요청과 담당자 변경이다.
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


def test_a_confirmed_ax_assignment_offers_no_withdrawal_because_it_already_stands(tmp_path) -> None:
    """취소는 **수락을 기다리는 동안**의 명령이었다. 기다림이 없어지면 그 명령도 봉투에 서지 않는다.

    신규 경로에 거절·취소를 신설하지 않는다 (DEC-001 D-4). 과거 행을 위해 남은 endpoint 는 그대로 있고,
    신규 배정에 걸면 정의된 도메인 거부이며 업무·담당에 side effect 가 없다.
    """
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
    assert receipt["allowed_commands"] == []
    conversation = client.get(f"/api/conversations/{proposal['conversation_id']}", headers=JIHO).json()
    [chat_receipt] = [row for row in conversation["actions"] if row["action_id"] == proposal["action_id"]]
    assert chat_receipt["commands"] == []

    # 봉투가 내지 않는 명령을 id 를 알고 직접 불러도 막힌다. 두 거절은 뜻이 다르다 —
    # 받는 쪽에게는 **그 판단 항목의 존재 자체를 숨기고**(404), 보낸 쪽에게는 지금 걸 수 있는 명령이
    # 아니라고 답한다(422). 「정의된 4xx」를 집합으로 받지 않고 각자 하나로 고정한다.
    hidden = _command(
        client, MINA, proposal["action_id"], "cancel_assignment",
        expected_version=receipt["expected_version"],
    )
    assert hidden.status_code == 404, hidden.text
    refused = _command(
        client, JIHO, proposal["action_id"], "cancel_assignment",
        expected_version=receipt["expected_version"],
    )
    assert refused.status_code == 422 and "cancel_assignment" in refused.text

    # 업무도 담당도 그대로 서 있다 — 거부에 side effect 가 없다.
    assert [row for row in _pending(client, MINA) if row["kind"] == "task.assignment"] == []
    [sent] = client.get("/api/task-assignments/sent", headers=JIHO).json()
    assert sent["status"] == "active" and sent["task"]["state"] == "open"
    [mine] = client.get("/api/my-work", headers=MINA).json()
    assert mine["title"] == "취소 가능한 요청"
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
    # 호환 경로도 같은 생성 계약을 지난다 — 배정이 그 자리에서 선다.
    assert [row for row in _pending(client, MINA) if row["kind"] == "task.assignment"] == []
    [mine] = client.get("/api/my-work", headers=MINA).json()
    assert mine["title"] == "호환 경로 요청"
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
    missing_reference ="00000000-0000-0000-0000-000000000099"
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


def test_ax_confirm_rechecks_current_authority_before_writing(tmp_path) -> None:
    client, application = _stack(tmp_path)
    proposal = _ax_proposal(
        client, application, JIHO, "jiho", "task.create_self", "업무 생성 확인", {"title": "권한 확인"}
    )
    [item] = [row for row in _pending(client, JIHO) if row["kind"] == "ax.task.create_self"]
    draft = {"title": "쓰이면 안 됨"}

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
    _ax_proposal(
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
    request = pending_request(client, client.database_url, MINA, title="견적 재검토", assignee_id="jiho", description="9월 견적 재검토")
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
    request = pending_request(client, client.database_url, MINA, title="검토 요청", assignee_id="jiho")

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
    request = pending_request(client, client.database_url, MINA, title="승인될 요청", assignee_id="jiho")
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
    pending_request(client, client.database_url, MINA, title="조정될 요청", assignee_id="jiho", description="처음 설명")
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
    pending_request(client, client.database_url, MINA, title="권한 확인 요청", assignee_id="jiho")
    [item] = _pending(client, JIHO)

    # The requester cannot answer their own request, and cannot revise before an adjustment was asked for.
    assert _command(client, MINA, item["action_item_id"], "accept", expected_version=item["expected_version"]).status_code in {403, 422}
    assert _command(client, MINA, item["action_item_id"], "revise", expected_version=item["expected_version"], changes={"title": "몰래 수정"}).status_code in {403, 422}
    # A command that is not offered on this item is refused rather than guessed at.
    assert _command(client, JIHO, item["action_item_id"], "withdraw", expected_version=item["expected_version"]).status_code == 422
    assert [row["title"] for row in client.get("/api/work-requests", headers=MINA).json()] == ["권한 확인 요청"]


def test_the_requester_can_withdraw_an_adjusted_request_and_it_leaves_every_ledger(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    pending_request(client, client.database_url, MINA, title="철회할 요청", assignee_id="jiho")
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
    assigned = application.assign_task(jiho, "배정된 업무", "mina", idempotency_key="center-assign", description="맡아 주세요")
    # 과거 모양의 수락 대기 배정 — 신규 배정은 즉시 활성 담당으로 서므로 이 회차를 만들지 않는다.
    make_assignment_look_pending(client.database_url, assigned["assignment_id"])

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
    pending_request(client, client.database_url, MINA, title="제안이 붙는 요청", assignee_id="jiho", description="처음 설명", due_date="2026-09-10")
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
    pending_request(client, client.database_url, MINA, title="한 번만 수락될 요청", assignee_id="jiho")
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
    request = pending_request(client, client.database_url, MINA, title="논의가 붙는 요청", assignee_id="jiho")
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
    pending_request(client, client.database_url, MINA, title="차례가 있는 요청", assignee_id="jiho")
    [item] = _pending(client, JIHO)

    # Both participants read the same question; only the one whose turn it is is offered a way to answer it.
    mine = client.get(f"/api/action-items/{item['action_item_id']}", headers=JIHO).json()
    theirs = client.get(f"/api/action-items/{item['action_item_id']}", headers=MINA).json()
    assert [command["id"] for command in mine["allowed_commands"]] == ["accept", "adjust", "reject"]
    assert theirs["allowed_commands"] == [] and theirs["waiting_on"]["member_id"] == "jiho"
    assert theirs["subject"] == mine["subject"] and theirs["status"] == mine["status"]


def _rounds(client, headers, action_item_id: str) -> list[dict]:
    return client.get(f"/api/action-items/{action_item_id}", headers=headers).json()["rounds"]


def test_every_command_must_name_the_version_it_is_answering(tmp_path) -> None:
    """Optimistic concurrency is the contract, not an option: a command without a version is refused."""
    client, _ = _stack(tmp_path)
    pending_request(client, client.database_url, MINA, title="버전 계약", assignee_id="jiho")
    [item] = _pending(client, JIHO)

    missing = client.post(f"/api/action-items/{item['action_item_id']}/commands/accept", headers=JIHO, json={})
    assert missing.status_code == 422, missing.text
    assert _command(client, JIHO, item["action_item_id"], "accept", expected_version=item["expected_version"] - 1).status_code == 422
    assert _command(client, JIHO, item["action_item_id"], "accept", expected_version=item["expected_version"] + 1).status_code == 422
    assert client.get("/api/my-work", headers=JIHO).json() == []
    assert _command(client, JIHO, item["action_item_id"], "accept", expected_version=item["expected_version"]).status_code == 200


def test_a_revision_may_only_change_the_fields_a_revision_owns(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    pending_request(client, client.database_url, MINA, title="허용 필드", assignee_id="jiho")
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


def test_a_legacy_negotiate_cannot_leave_the_ledger_unreadable(tmp_path) -> None:
    """The compatibility endpoint writes what the canonical reader can read, or it does not write at all."""
    client, _ = _stack(tmp_path)
    request = pending_request(client, client.database_url, MINA, title="원장 보호", assignee_id="jiho")

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


def test_a_receipt_survives_the_basis_moving_underneath_it(tmp_path) -> None:
    """A revision's receipt is pinned by the round it produced, not by a request version anyone may move."""
    client, _ = _stack(tmp_path)
    request = pending_request(client, client.database_url, MINA, title="영수증 유지", assignee_id="jiho", description="처음")
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
    request = pending_request(client, client.database_url, MINA, title="조건 분리", assignee_id="jiho")
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
