"""Pre-create Action material drafts are owned, expiring inputs claimed by one confirmation."""
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import (
    ActionMaterialDraftRecord,
    AttachmentBindingRecord,
    AttachmentRecord,
    ConversationTurnRecord,
    SubmissionRecord,
    TaskVersionRecord,
    make_session_factory,
)

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    app = create_app(settings)
    return TestClient(app), app.state.workflow_application


def _proposal(client, application, *, title="첨부할 AX 업무"):
    conversation = client.post("/api/conversations", headers=JIHO, json={"title": "첨부 계약"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**JIHO, "Idempotency-Key": f"action-material-{abs(hash(title))}"},
        json={"body": "첨부할 업무를 제안해줘", "context": []},
    ).json()
    with make_session_factory(application._settings.database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id
    return application.propose_action(
        application.authenticated_principal("jiho"),
        execution_id,
        "task.create_self",
        "업무 생성 확인",
        {"title": title, "due_date": "2026-09-30"},
    )


# 옛 `meeting.create` 확인 경로를 딛던 헬퍼(`_meeting_proposal`)와 시험 둘
# (`test_meeting_without_note_claims_materials_into_the_meeting_owner` ·
#  `test_excluding_the_initial_note_keeps_staged_meeting_materials`)을 걷었다.
# 그 경로는 **옛 회의 모델**(description·visibility·판 있는 회의록)에 서 있었고 SCAX-SPEC-004 의
# `meeting.reservation.create` 가 그것을 대체했다 — 이제 회의 생성의 공개 계약은 그 하나뿐이고
# 옛 타입은 실행되지 않는다(`RETIRED_ACTION_TYPES`). 자료 초안 계약 자체는 아래 업무 제안 네 시험이
# 그대로 지키며, 회의 쪽 첨부 청구는 `test_action_center.py` 의 예약 생성 승인이 검증한다.


def test_link_and_file_drafts_are_projected_and_claimed_by_confirm(tmp_path) -> None:
    client, application = _stack(tmp_path)
    proposal = _proposal(client, application)
    action_id = proposal["action_id"]

    link = client.post(
        f"/api/action-items/{action_id}/material-drafts/links",
        headers=JIHO,
        json={"url": " https://example.com/brief ", "label": " 기획 문서 "},
    )
    assert link.status_code == 201, link.text
    uploaded = client.post(
        f"/api/action-items/{action_id}/material-drafts/files",
        headers=JIHO,
        files={"file": ("검토안.txt", b"draft material", "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    assert link.json()["source_kind"] == "external_link" and link.json()["url"] == "https://example.com/brief"
    assert uploaded.json()["source_kind"] == "file" and uploaded.json()["state"] == "staged"

    item = client.get(f"/api/action-items/{action_id}", headers=JIHO).json()
    assert [row["material_draft_id"] for row in item["material_drafts"]] == [
        link.json()["material_draft_id"], uploaded.json()["material_draft_id"]
    ]
    selected = [row["material_draft_id"] for row in item["material_drafts"]]
    command_body = {
        "expected_version": item["expected_version"],
        "base_submission_version": item["submission_version"],
        "attachment_draft_ids": selected,
    }
    confirmed = client.post(
        f"/api/action-items/{action_id}/commands/confirm",
        headers=JIHO,
        json=command_body,
    )
    assert confirmed.status_code == 200, confirmed.text
    receipt = confirmed.json()
    assert [row["source_kind"] for row in receipt["material_drafts"]] == ["external_link", "file"]
    assert {row["state"] for row in receipt["material_drafts"]} == {"claimed"}
    assert [row["source_kind"] for row in receipt["material_results"]] == ["external_link", "file"]

    task_id = receipt["derived_task_id"]
    materials = client.get(f"/api/tasks/{task_id}/materials", headers=JIHO).json()
    assert [row["source_kind"] for row in materials] == ["external_link", "file"]
    assert all(row["kind"] == "input" for row in materials)
    replayed = client.post(f"/api/action-items/{action_id}/commands/confirm", headers=JIHO, json=command_body)
    assert replayed.status_code == 200 and replayed.json()["derived_task_id"] == task_id
    wrong_replay = client.post(
        f"/api/action-items/{action_id}/commands/confirm",
        headers=JIHO,
        json={"expected_version": item["expected_version"], "base_submission_version": 1},
    )
    assert wrong_replay.status_code == 422
    detail = client.get(f"/api/action-items/{action_id}", headers=JIHO).json()
    assert [round_["submission_version"] for round_ in detail["rounds"]] == [1, 2]
    assert detail["rounds"][1]["snapshot"]["attachment_draft_ids"] == selected
    with make_session_factory(application._settings.database_url)() as session:
        creation = session.query(TaskVersionRecord).filter_by(task_id=UUID(task_id), version=1).one()
        assert [row["source_kind"] for row in creation.snapshot["materials"]] == ["external_link", "file"]


def test_stage_storage_failure_and_cross_principal_access_leave_no_draft(tmp_path) -> None:
    client, application = _stack(tmp_path)
    proposal = _proposal(client, application, title="실패 격리")
    action_id = proposal["action_id"]

    original_put = application._material_storage.put
    application._material_storage.put = lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("store down"))
    try:
        failed = client.post(
            f"/api/action-items/{action_id}/material-drafts/files",
            headers=JIHO,
            files={"file": ("실패.txt", b"must not persist", "text/plain")},
        )
    finally:
        application._material_storage.put = original_put
    assert failed.status_code == 422
    assert client.get(f"/api/action-items/{action_id}", headers=JIHO).json()["material_drafts"] == []
    with make_session_factory(application._settings.database_url)() as session:
        [failed_draft] = session.query(ActionMaterialDraftRecord).all()
        assert failed_draft.state == "discarded"
    assert application.reconcile_action_material_drafts() == 1

    staged = client.post(
        f"/api/action-items/{action_id}/material-drafts/links",
        headers=JIHO,
        json={"url": "https://example.com/private", "label": "비공개 초안"},
    ).json()
    denied = client.post(
        f"/api/action-items/{action_id}/material-drafts/{staged['material_draft_id']}/discard", headers=MINA
    )
    assert denied.status_code == 404
    assert client.get(f"/api/action-items/{action_id}", headers=MINA).status_code in {403, 404}


def test_expired_draft_is_not_claimed_and_cleanup_is_idempotent(tmp_path) -> None:
    client, application = _stack(tmp_path)
    proposal = _proposal(client, application, title="만료 정리")
    action_id = proposal["action_id"]
    staged = client.post(
        f"/api/action-items/{action_id}/material-drafts/files",
        headers=JIHO,
        files={"file": ("만료.txt", b"expired bytes", "text/plain")},
    ).json()
    stored_files = [path for path in (tmp_path / "materials").rglob("*") if path.is_file()]
    assert len(stored_files) == 1
    with make_session_factory(application._settings.database_url)() as session:
        record = session.get(ActionMaterialDraftRecord, UUID(staged["material_draft_id"]))
        record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    item = client.get(f"/api/action-items/{action_id}", headers=JIHO).json()
    refused = client.post(
        f"/api/action-items/{action_id}/commands/confirm",
        headers=JIHO,
        json={
            "expected_version": item["expected_version"],
            "base_submission_version": 1,
            "attachment_draft_ids": [staged["material_draft_id"]],
        },
    )
    assert refused.status_code == 422
    assert client.get("/api/my-work", headers=JIHO).json() == []
    assert application.reconcile_action_material_drafts() == 1
    assert application.reconcile_action_material_drafts() == 0
    assert not stored_files[0].exists()
    with make_session_factory(application._settings.database_url)() as session:
        assert session.get(ActionMaterialDraftRecord, UUID(staged["material_draft_id"])).state == "purged"


def test_confirm_rollback_keeps_the_staged_file_unclaimed(tmp_path) -> None:
    client, application = _stack(tmp_path)
    proposal = _proposal(client, application, title="원자성")
    action_id = proposal["action_id"]
    staged = client.post(
        f"/api/action-items/{action_id}/material-drafts/files",
        headers=JIHO,
        files={"file": ("원자성.txt", b"still staged", "text/plain")},
    ).json()
    item = client.get(f"/api/action-items/{action_id}", headers=JIHO).json()
    failed = client.post(
        f"/api/action-items/{action_id}/commands/confirm",
        headers=JIHO,
        json={
            "expected_version": item["expected_version"],
            "base_submission_version": 1,
            "draft": {"title": "실패할 업무", "reference_task_ids": ["00000000-0000-0000-0000-000000000099"]},
            "attachment_draft_ids": [staged["material_draft_id"]],
        },
    )
    assert failed.status_code in {404, 422}
    with make_session_factory(application._settings.database_url)() as session:
        record = session.get(ActionMaterialDraftRecord, UUID(staged["material_draft_id"]))
        assert record.state == "staged" and record.claimed_task_id is None
        assert session.query(AttachmentRecord).count() == 0
        assert session.query(AttachmentBindingRecord).count() == 0
        assert session.query(SubmissionRecord).count() == 1
