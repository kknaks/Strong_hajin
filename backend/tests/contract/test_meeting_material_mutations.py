"""Meeting-owned materials change independently from the one versioned MeetingNote."""
from uuid import UUID

from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import ActivityEventRecord, AttachmentBindingRecord, AttachmentRecord, make_session_factory


JIHO = {"X-Demo-Persona": "jiho"}
MINA = {"X-Demo-Persona": "mina"}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    app = create_app(settings)
    return TestClient(app, raise_server_exceptions=False), app.state.workflow_application


def _meeting_with_note(client: TestClient) -> dict:
    meeting = client.post(
        "/api/meetings",
        headers=JIHO,
        json={
            "organization_id": "scax",
            "title": "첨부 변경 회의",
            "description": "회의 자료 이력 검증",
            "starts_at": "2026-10-10T05:00:00Z",
            "ends_at": "2026-10-10T06:00:00Z",
            "visibility": "private",
            "attendee_ids": ["mina"],
        },
    ).json()
    note = client.post(
        f"/api/meetings/{meeting['meeting_id']}/note",
        headers=JIHO,
        json={"body": "본문 v1"},
    )
    assert note.status_code == 201
    return client.get(f"/api/meetings/{meeting['meeting_id']}", headers=JIHO).json()


def test_meeting_material_add_detach_and_file_replace_keep_note_version(tmp_path) -> None:
    client, application = _stack(tmp_path)
    detail = _meeting_with_note(client)
    meeting_id = detail["meeting_id"]
    assert detail["version"] == 1 and detail["note"]["version"] == 1

    linked = client.post(
        f"/api/meetings/{meeting_id}/materials/links",
        headers=JIHO,
        json={"expected_version": 1, "url": "https://example.com/agenda", "label": "회의 안건"},
    )
    assert linked.status_code == 201, linked.text
    assert linked.json()["meeting_version"] == 2
    after_link = client.get(f"/api/meetings/{meeting_id}", headers=JIHO).json()
    assert after_link["note"]["version"] == 1

    detached = client.post(
        f"/api/meetings/{meeting_id}/material-bindings/{linked.json()['binding_id']}/detach",
        headers=JIHO,
        json={"expected_version": 2},
    )
    assert detached.status_code == 200, detached.text
    assert detached.json()["meeting_version"] == 3
    assert client.get(f"/api/meetings/{meeting_id}", headers=JIHO).json()["materials"] == []

    uploaded = client.post(
        f"/api/meetings/{meeting_id}/materials",
        headers=JIHO,
        data={"expected_version": "3"},
        files={"file": ("회의안-v1.txt", b"first", "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    old_material_id = uploaded.json()["material_id"]
    replaced = client.post(
        f"/api/meetings/{meeting_id}/material-bindings/{uploaded.json()['binding_id']}/replace",
        headers=JIHO,
        data={"expected_version": "4"},
        files={"file": ("회의안-v2.txt", b"second", "text/plain")},
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["meeting_version"] == 5
    assert replaced.json()["material_id"] != old_material_id
    final = client.get(f"/api/meetings/{meeting_id}", headers=JIHO).json()
    assert [(row["name"], row["material_id"]) for row in final["materials"]] == [
        ("회의안-v2.txt", replaced.json()["material_id"]),
    ]
    assert final["version"] == 5 and final["note"]["version"] == 1

    # The old file is not overwritten or deleted; only its Meeting binding is closed.
    with make_session_factory(application._settings.database_url)() as session:
        old = session.get(AttachmentRecord, UUID(old_material_id))
        assert old is not None and application._material_storage.get(old.source_ref) == b"first"
        events = list(session.query(ActivityEventRecord).filter_by(target_type="meeting", target_id=meeting_id))
        changed = [event for event in events if event.event_kind.startswith("meeting.material_")]
        assert [(event.actor_id, event.event_kind) for event in changed] == [
            ("jiho", "meeting.material_attached"),
            ("jiho", "meeting.material_detached"),
            ("jiho", "meeting.material_attached"),
            ("jiho", "meeting.material_replaced"),
        ]
        assert all(event.occurred_at is not None for event in changed)
        assert changed[-1].before_ref == f"attachment:{old_material_id}"
        assert changed[-1].after_ref == f"attachment:{replaced.json()['material_id']}"
        assert "회의안-v1.txt" in changed[-1].safe_summary and "회의안-v2.txt" in changed[-1].safe_summary

    # Retrying the old command cannot make a duplicate binding.
    replay = client.post(
        f"/api/meetings/{meeting_id}/material-bindings/{uploaded.json()['binding_id']}/replace",
        headers=JIHO,
        data={"expected_version": "4"},
        files={"file": ("회의안-v2.txt", b"second", "text/plain")},
    )
    assert replay.status_code == 422
    assert len(client.get(f"/api/meetings/{meeting_id}", headers=JIHO).json()["materials"]) == 1


def test_meeting_material_mutations_are_owner_only_and_rollback_binding_with_audit(tmp_path, monkeypatch) -> None:
    client, application = _stack(tmp_path)
    detail = _meeting_with_note(client)
    meeting_id = detail["meeting_id"]

    denied = client.post(
        f"/api/meetings/{meeting_id}/materials/links",
        headers=MINA,
        json={"expected_version": 1, "url": "https://example.com/private", "label": "소유자 전용"},
    )
    assert denied.status_code == 403

    from ax_workspace.platform.meetings import SqlAlchemyMeetingRepository

    original = SqlAlchemyMeetingRepository.append_audit

    def fail_audit(self, *args, **kwargs):
        if args[2] == "meeting.material_attached":
            raise RuntimeError("audit store unavailable")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SqlAlchemyMeetingRepository, "append_audit", fail_audit)
    failed = client.post(
        f"/api/meetings/{meeting_id}/materials/links",
        headers=JIHO,
        json={"expected_version": 1, "url": "https://example.com/rollback", "label": "롤백 자료"},
    )
    assert failed.status_code == 500

    with make_session_factory(application._settings.database_url)() as session:
        # The pre-existing immutable MeetingNote projection remains; only the failed link transaction rolls back.
        attachments = list(session.query(AttachmentRecord))
        assert len(attachments) == 1 and attachments[0].source_kind == "native_revision"
        assert session.query(AttachmentBindingRecord).filter_by(context_type="meeting", context_id=meeting_id).count() == 0
        assert session.query(AttachmentBindingRecord).filter_by(context_type="meeting_note").count() == 1
        assert session.query(ActivityEventRecord).filter_by(
            target_type="meeting", target_id=meeting_id, event_kind="meeting.material_attached"
        ).count() == 0
    final = client.get(f"/api/meetings/{meeting_id}", headers=JIHO).json()
    assert final["version"] == 1 and final["note"]["version"] == 1 and final["materials"] == []
