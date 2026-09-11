"""Canonical material search retains the meeting transcript's immutable segment and audio lineage."""
import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ax_workspace.bootstrap.material_worker import MaterialExtractionWorker
from ax_workspace.bootstrap.application import WorkflowApplication
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.meetings.transcription import FinalTranscriptSegment
from ax_workspace.modules.work.materials import MaterialError
from ax_workspace.platform.persistence import AttachmentBindingRecord, MeetingAttendeeRecord, MeetingNoteVersionRecord, MeetingRawTranscriptSegmentRecord, MembershipRecord, make_session_factory
from ax_workspace.platform.native_materials import NativeMaterialRepository
from test_material_search import MINA, JIHO
from test_meeting_recordings import RefinementProvider


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"), recordings_dir=str(tmp_path / "recordings"))
    app = create_app(settings, report_provider=RefinementProvider())
    application = app.state.workflow_application
    worker = MaterialExtractionWorker(settings, queue_factory=lambda session: application.memory_job_queue)
    return TestClient(app), application, worker, settings


def _recorded(client, *, live_text=None):
    meeting = client.post("/api/meetings", headers=MINA, json={"organization_id": "scax", "title": "전사 검색 회의",
                          "starts_at": "2026-09-10T01:00:00Z", "ends_at": "2026-09-10T02:00:00Z", "visibility": "private", "attendee_ids": ["jiho"]}).json()
    recording = client.post(f"/api/meetings/{meeting['meeting_id']}/recordings/start", headers=MINA, json={"purpose": "검증"}).json()
    if live_text:
        live = client.post(f"/api/meetings/{meeting['meeting_id']}/recordings/{recording['recording_id']}/realtime-segments", headers=MINA,
                          json={"segments": [{"source_segment_key": "live-a", "start_ms": 0, "end_ms": 1000, "text": live_text}]})
        assert live.status_code in {200, 201}, live.text
    stopped = client.post(f"/api/meetings/{meeting['meeting_id']}/recordings/{recording['recording_id']}/stop", headers=MINA,
                          data={"expected_version": str(recording["version"])}, files={"audio": ("original.webm", b"synthetic audio", "audio/webm")})
    assert stopped.status_code == 200
    return meeting, stopped.json()


def test_text_only_meeting_note_is_an_authorized_searchable_native_revision(tmp_path, monkeypatch):
    client, application, worker, settings = _stack(tmp_path)
    meeting = client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "organization_id": "scax",
            "title": "재논의 근거 회의",
            "starts_at": "2026-09-10T01:00:00Z",
            "ends_at": "2026-09-10T02:00:00Z",
            "visibility": "private",
            "attendee_ids": ["jiho"],
        },
    ).json()
    note = client.post(
        f"/api/meetings/{meeting['meeting_id']}/note",
        headers=MINA,
        json={"body": "결정: 공급사 일정이 바뀌어 출시 범위를 재논의했다."},
    ).json()
    principal = application.authenticated_principal("mina")

    pending = application.search_materials(principal, "재논의", resource_types=["meeting"])
    assert pending["results"] == []
    assert pending["unavailable_materials_count"] == 1
    assert pending["unavailable_materials"][0]["extraction"]["status"] == "queued"

    assert asyncio.run(worker.run_once())
    found = application.search_materials(principal, "재논의", resource_types=["meeting"])
    assert len(found["results"]) == 1
    hit = found["results"][0]
    assert hit["source_resource_type"] == "meeting"
    assert hit["source_resource_id"] == meeting["meeting_id"]
    assert hit["source_resource_title"] == "재논의 근거 회의"
    assert hit["source_locator"]["kind"] == "meeting_note"
    assert hit["source_locator"]["meeting_id"] == meeting["meeting_id"]
    assert hit["source_locator"]["note_id"] == note["note_id"]
    assert hit["source_locator"]["source_revision_id"] == note["versions"][0]["version_id"]
    assert hit["source_locator"]["note_version"] == 1
    context = hit["source_contexts"][0]
    assert context["source_layer"] == "meeting_note"
    assert context["note_lifecycle"] == "draft"
    assert context["is_current_revision"] is True
    opened = client.get(hit["origin"], headers=MINA)
    assert opened.status_code == 200
    assert opened.json()["body"] == "결정: 공급사 일정이 바뀌어 출시 범위를 재논의했다."
    filters = {"resource_type": "meeting", "resource_id": meeting["meeting_id"]}
    response = client.get("/api/materials/search", headers=MINA, params={"q": "재논의", **filters})
    server = _create_bound_persona_server(McpReportsFacade(settings, "mina"))
    delegated = asyncio.run(server.call_tool("material_search", {"query": "재논의", **filters})).structured_content
    assert response.status_code == 200 and delegated == response.json()

    from test_material_evidence_owners import _turn

    conversation_id, _, execution_id = _turn(client, settings)
    monkeypatch.setenv("AX_MCP_CAUSATION_ID", str(execution_id))
    McpReportsFacade(settings, "mina").search_materials("재논의", resource_types=["meeting"])
    resource = next(
        row for row in client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()["answer_resources"]
        if row["resource_type"] == "material"
    )
    assert resource["source_locator"]["meeting_id"] == meeting["meeting_id"]
    assert resource["source_locator"]["note_id"] == note["note_id"]
    assert resource["source_locator"]["note_version"] == 1

    assert application.search_materials(
        application.authenticated_principal("jiho"), "재논의", resource_types=["meeting"]
    )["results"]
    denied = application.search_materials(
        application.authenticated_principal("sora"), "재논의", resource_types=["meeting"]
    )
    assert denied["results"] == [] and denied["unavailable_materials_count"] == 0

    saved = client.patch(
        f"/api/meetings/{meeting['meeting_id']}/note",
        headers=MINA,
        json={"expected_version": 1, "body": "확정 결정: 공급사 납기 때문에 범위를 다시 검토한다."},
    ).json()
    pending_v2 = application.search_materials(principal, "다시 검토", resource_types=["meeting"])
    assert pending_v2["results"] == []
    assert pending_v2["unavailable_materials"][0]["source_contexts"][0]["source_revision"] == 2
    assert asyncio.run(worker.run_once())
    assert application.search_materials(principal, "재논의", resource_types=["meeting"])["results"] == []
    current = application.search_materials(principal, "다시 검토", resource_types=["meeting"])["results"][0]
    assert current["material_id"] != hit["material_id"]
    assert current["source_locator"]["source_revision_id"] == saved["versions"][-1]["version_id"]
    assert current["source_contexts"][0]["source_revision"] == 2

    historical = application.search_materials(
        principal,
        "재논의",
        resource_types=["meeting"],
        material_id=UUID(hit["material_id"]),
    )["results"][0]
    assert historical["source_contexts"][0]["is_current_revision"] is False
    assert historical["source_contexts"][0]["note_lifecycle"] == "draft"

    finalized = client.post(
        f"/api/meetings/{meeting['meeting_id']}/note/finalize",
        headers=MINA,
        json={"expected_version": 2},
    )
    assert finalized.status_code == 200
    after_finalize = application.search_materials(principal, "다시 검토", resource_types=["meeting"])["results"][0]
    assert after_finalize["material_id"] == current["material_id"]
    assert after_finalize["source_contexts"][0]["note_lifecycle"] == "finalized"

    old_attendee_principal = application.authenticated_principal("jiho")
    with make_session_factory(settings.database_url)() as session:
        attendee = session.scalar(
            select(MeetingAttendeeRecord).where(
                MeetingAttendeeRecord.meeting_id == UUID(meeting["meeting_id"]),
                MeetingAttendeeRecord.member_id == "jiho",
            )
        )
        attendee.removed_at = datetime.now(UTC)
        session.commit()
    revoked = application.search_materials(old_attendee_principal, "다시 검토", resource_types=["meeting"])
    assert revoked["results"] == [] and revoked["unavailable_materials_count"] == 0
    assert client.get(current["origin"], headers=JIHO).status_code == 404


def test_meeting_note_integrity_drift_fails_before_publishing_search_chunks(tmp_path):
    client, application, worker, settings = _stack(tmp_path)
    meeting = client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "organization_id": "scax",
            "title": "회의록 무결성 검증",
            "starts_at": "2026-09-10T03:00:00Z",
            "ends_at": "2026-09-10T04:00:00Z",
            "visibility": "private",
            "attendee_ids": [],
        },
    ).json()
    note = client.post(
        f"/api/meetings/{meeting['meeting_id']}/note",
        headers=MINA,
        json={"body": "originalnotenativeintegritytoken"},
    ).json()
    principal = application.authenticated_principal("mina")
    pending = application.search_materials(principal, "originalnotenativeintegritytoken", resource_types=["meeting"])
    assert pending["unavailable_materials"][0]["extraction"]["status"] == "queued"

    with make_session_factory(settings.database_url)() as session:
        version = session.get(MeetingNoteVersionRecord, UUID(note["versions"][0]["version_id"]))
        version.body = "corruptednotenativeintegritytoken"
        session.commit()

    assert asyncio.run(worker.run_once())
    found = application.search_materials(principal, "corruptednotenativeintegritytoken", resource_types=["meeting"])
    assert found["results"] == []
    extraction = found["unavailable_materials"][0]["extraction"]
    assert extraction["status"] == "failed"
    assert extraction["failure_reason"] == "integrity_mismatch"
    assert extraction["chunk_count"] == 0


def test_legacy_meeting_note_is_lazily_projected_only_after_current_authorization(tmp_path, monkeypatch):
    client, application, worker, _ = _stack(tmp_path)
    meeting = client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "organization_id": "scax",
            "title": "기존 텍스트 회의록",
            "starts_at": "2026-09-09T01:00:00Z",
            "ends_at": "2026-09-09T02:00:00Z",
            "visibility": "private",
            "attendee_ids": [],
        },
    ).json()
    with monkeypatch.context() as previous_deployment:
        previous_deployment.setattr(WorkflowApplication, "_register_note_material_from_view", lambda *args: None)
        created = client.post(
            f"/api/meetings/{meeting['meeting_id']}/note",
            headers=MINA,
            json={"body": "legacymeetingnotetoken"},
        )
        assert created.status_code == 201

    denied = application.search_materials(
        application.authenticated_principal("sora"), "legacymeetingnotetoken", resource_types=["meeting"]
    )
    assert denied["results"] == [] and denied["unavailable_materials_count"] == 0
    pending = application.search_materials(
        application.authenticated_principal("mina"), "legacymeetingnotetoken", resource_types=["meeting"]
    )
    assert pending["results"] == []
    assert pending["unavailable_materials"][0]["extraction"]["status"] == "queued"
    assert asyncio.run(worker.run_once())
    assert application.search_materials(
        application.authenticated_principal("mina"), "legacymeetingnotetoken", resource_types=["meeting"]
    )["results"]


def test_final_raw_transcript_search_has_canonical_identity_and_exact_audio_segment_locator(tmp_path):
    client, application, worker, _ = _stack(tmp_path)
    meeting, recording = _recorded(client)
    raw = application.record_final_meeting_transcript(recording_id=UUID(recording["recording_id"]), provider="fixture",
                provider_reference="final:search", segments=[FinalTranscriptSegment("final-a", 1200, 3400, "meetingrawtailtoken", "speaker 1")])
    assert asyncio.run(worker.run_once())
    principal = application.authenticated_principal("mina")
    found = application.search_materials(principal, "meetingrawtailtoken", resource_types=["meeting"])
    assert len(found["results"]) == 1
    hit = found["results"][0]
    assert hit["source_resource_type"] == "meeting" and hit["source_resource_id"] == meeting["meeting_id"]
    assert hit["source_locator"]["start_ms"] == 1200 and hit["source_locator"]["end_ms"] == 3400
    assert hit["source_locator"]["segment_id"] == raw["segments"][0]["segment_id"]
    assert hit["source_locator"]["source_revision_id"] == raw["transcript_revision_id"]
    context = hit["source_contexts"][0]
    assert context["source_layer"] == "raw_transcript" and context["recording_id"] == recording["recording_id"]
    opened = client.get(hit["origin"], headers=MINA)
    assert opened.status_code == 200 and opened.json()["source_revision_id"] == raw["transcript_revision_id"]
    peer = application.search_materials(application.authenticated_principal("jiho"), "meetingrawtailtoken")
    assert peer["results"][0]["material_id"] == hit["material_id"]
    # Non-attending peers must never see even native projection metadata.
    denied = application.search_materials(application.authenticated_principal("sora"), "meetingrawtailtoken")
    assert denied["results"] == [] and denied["unavailable_materials_count"] == 0


def test_refinement_is_a_separate_material_with_a_raw_span_and_current_owner_reauthorization(tmp_path):
    client, application, worker, settings = _stack(tmp_path)
    meeting, recording = _recorded(client)
    raw = application.record_final_meeting_transcript(recording_id=UUID(recording["recording_id"]), provider="fixture",
        provider_reference="final:layers", segments=[FinalTranscriptSegment("provider-segment-1", 0, 1500, "rawfirsttoken"),
                                                    FinalTranscriptSegment("provider-segment-2", 1500, 3000, "rawlasttoken")])
    refined = application.refine_meeting_transcript(UUID(raw["transcript_revision_id"]))
    assert asyncio.run(worker.run_once())
    principal = application.authenticated_principal("jiho")
    raw_hit = application.search_materials(principal, "rawlasttoken")["results"][0]
    refined_hit = application.search_materials(principal, "일정 논의")["results"][0]
    assert raw_hit["material_id"] != refined_hit["material_id"]
    assert refined_hit["source_contexts"][0]["source_layer"] == "refinement"
    assert refined_hit["source_locator"]["source_revision_id"] == refined["refinement_revision_id"]
    assert refined_hit["source_locator"]["raw_start_segment_id"] == raw["segments"][0]["segment_id"]
    assert refined_hit["source_locator"]["raw_end_segment_id"] == raw["segments"][1]["segment_id"]
    with make_session_factory(settings.database_url)() as session:
        attendee = session.scalar(select(MeetingAttendeeRecord).where(MeetingAttendeeRecord.meeting_id == UUID(meeting["meeting_id"]), MeetingAttendeeRecord.member_id == "jiho"))
        attendee.removed_at = datetime.now(UTC)
        session.commit()
    revoked = application.search_materials(principal, "rawlasttoken 일정")
    assert revoked["results"] == [] and revoked["unavailable_materials_count"] == 0
    assert client.get(raw_hit["origin"], headers=JIHO).status_code == 404
    assert client.get(refined_hit["origin"], headers=JIHO).status_code == 404
    # A file-purge operation must not claim to destroy text still owned by the canonical Meeting ledger.
    with pytest.raises(MaterialError, match="owning resource"):
        application.purge_attachment(application.authenticated_principal("yuna"), UUID(raw_hit["material_id"]), reason="test")


def test_a_native_revision_cannot_gain_read_permission_through_an_unrelated_file_binding(tmp_path):
    client, application, worker, settings = _stack(tmp_path)
    meeting, recording = _recorded(client)
    application.record_final_meeting_transcript(recording_id=UUID(recording["recording_id"]), provider="fixture",
        provider_reference="final:scope", segments=[FinalTranscriptSegment("one", 0, 1000, "nativeprivatetoken")])
    assert asyncio.run(worker.run_once())
    hit = application.search_materials(application.authenticated_principal("mina"), "nativeprivatetoken")["results"][0]
    task = client.post("/api/tasks", headers=JIHO, json={"title": "전사와 무관한 업무"}).json()
    # A malformed/imported link must not convert a native owner into a file sharing owner.
    with make_session_factory(settings.database_url)() as session:
        attendee = session.scalar(select(MeetingAttendeeRecord).where(MeetingAttendeeRecord.meeting_id == UUID(meeting["meeting_id"]), MeetingAttendeeRecord.member_id == "jiho"))
        attendee.removed_at = datetime.now(UTC)
        binding = AttachmentBindingRecord(attachment_id=UUID(hit["material_id"]), context_type="task", context_id=task["task_id"],
                                          role="input", bound_by="jiho", bound_at=datetime.now(UTC))
        session.add(binding)
        session.commit()
        binding_id = str(binding.id)
    denied = application.search_materials(application.authenticated_principal("jiho"), "nativeprivatetoken")
    assert denied["results"] == [] and denied["unavailable_materials_count"] == 0
    legacy = client.get('/api/materials/search', headers=JIHO, params={'q': 'nativeprivatetoken', 'resource_type': 'task', 'resource_id': task['task_id']}).json()
    assert legacy["results"] == [] and legacy["unavailable_materials"] == []
    assert client.get(f"/api/tasks/{task['task_id']}/materials/{binding_id}/content", headers=JIHO).status_code == 404


def test_latest_search_does_not_mix_old_transcripts_but_an_explicit_revision_remains_openable(tmp_path):
    client, application, worker, _ = _stack(tmp_path)
    _, recording = _recorded(client)
    application.record_final_meeting_transcript(recording_id=UUID(recording["recording_id"]), provider="fixture",
        provider_reference="final:old", segments=[FinalTranscriptSegment("old", 0, 1000, "historicalrevisiontoken")])
    assert asyncio.run(worker.run_once())
    principal = application.authenticated_principal("mina")
    old_hit = application.search_materials(principal, "historicalrevisiontoken")["results"][0]
    application.record_final_meeting_transcript(recording_id=UUID(recording["recording_id"]), provider="fixture",
        provider_reference="final:new", segments=[FinalTranscriptSegment("new", 0, 1000, "currentrevisiontoken")])
    assert asyncio.run(worker.run_once())
    assert application.search_materials(principal, "historicalrevisiontoken")["results"] == []
    explicit = application.search_materials(principal, "historicalrevisiontoken", material_id=UUID(old_hit["material_id"]))
    assert explicit["results"][0]["material_id"] == old_hit["material_id"]
    assert client.get(old_hit["origin"], headers=MINA).status_code == 200
    assert explicit["results"][0]["source_contexts"][0]["is_current_revision"] is False


def test_legacy_native_projection_reads_source_content_only_after_meeting_authorization(tmp_path, monkeypatch):
    client, application, worker, _ = _stack(tmp_path)
    _, recording = _recorded(client)
    with monkeypatch.context() as previous:
        previous.setattr(WorkflowApplication, "_register_native_material", lambda *args: None)
        application.record_final_meeting_transcript(recording_id=UUID(recording["recording_id"]), provider="fixture",
            provider_reference="final:legacy", segments=[FinalTranscriptSegment("old", 0, 1000, "legacymeetingtoken")])
    original = NativeMaterialRepository.payload
    observed = []
    def capture(self, kind, identifier):
        observed.append(str(identifier))
        return original(self, kind, identifier)
    monkeypatch.setattr(NativeMaterialRepository, "payload", capture)
    denied = application.search_materials(application.authenticated_principal("sora"), "legacymeetingtoken")
    assert denied["unavailable_materials_count"] == 0 and observed == []
    pending = application.search_materials(application.authenticated_principal("mina"), "legacymeetingtoken")
    assert len(observed) == 1 and next(row for row in pending["unavailable_materials"] if row["reason"] == "extraction")["extraction"]["status"] == "queued"
    assert asyncio.run(worker.run_once())
    assert application.search_materials(application.authenticated_principal("mina"), "legacymeetingtoken")["results"]


def test_native_source_hash_drift_fails_before_publishing_chunks(tmp_path):
    client, application, worker, settings = _stack(tmp_path)
    _, recording = _recorded(client)
    raw = application.record_final_meeting_transcript(recording_id=UUID(recording["recording_id"]), provider="fixture",
        provider_reference="final:hash", segments=[FinalTranscriptSegment("one", 0, 1000, "originalmeetingtoken")])
    with make_session_factory(settings.database_url)() as session:
        segment = session.get(MeetingRawTranscriptSegmentRecord, UUID(raw["segments"][0]["segment_id"]))
        segment.text = "corruptedmeetingtoken"
        session.commit()
    assert asyncio.run(worker.run_once())
    found = application.search_materials(application.authenticated_principal("mina"), "corruptedmeetingtoken")
    assert found["results"] == []
    extraction = next(row for row in found["unavailable_materials"] if row["reason"] == "extraction")["extraction"]
    assert extraction["failure_reason"] == "integrity_mismatch" and extraction["chunk_count"] == 0
    assert extraction["coverage"]["complete"] is False


def test_long_final_transcript_keeps_the_last_segment_and_live_transcript_is_not_an_immutable_material(tmp_path):
    client, application, worker, _ = _stack(tmp_path)
    _, recording = _recorded(client, live_text="realtimemeetingtoken")
    principal = application.authenticated_principal("mina")
    assert application.search_materials(principal, "realtimemeetingtoken")["searched_materials"] == 0
    segments = [FinalTranscriptSegment(f"final-{i}", i * 1000, (i + 1) * 1000,
                                      "lastmeetingsegmenttoken" if i == 999 else f"일반 발화 {i}") for i in range(1000)]
    raw = application.record_final_meeting_transcript(recording_id=UUID(recording["recording_id"]), provider="fixture", provider_reference="final:long", segments=segments)
    assert asyncio.run(worker.run_once())
    hit = application.search_materials(principal, "lastmeetingsegmenttoken")["results"][0]
    assert hit["extraction"]["coverage"]["total_units"] == 1000
    assert hit["extraction"]["chunk_count"] == 1000
    assert hit["source_locator"]["segment_id"] == raw["segments"][-1]["segment_id"]
    assert hit["source_locator"]["start_ms"] == 999000


def test_native_search_refreshes_ended_organization_membership_even_for_an_old_principal(tmp_path):
    client, application, worker, settings = _stack(tmp_path)
    _, recording = _recorded(client)
    application.record_final_meeting_transcript(recording_id=UUID(recording["recording_id"]), provider="fixture",
        provider_reference="final:membership", segments=[FinalTranscriptSegment("one", 0, 1000, "membershipmeetingtoken")])
    assert asyncio.run(worker.run_once())
    old_principal = application.authenticated_principal("jiho")
    assert application.search_materials(old_principal, "membershipmeetingtoken")["results"]
    with make_session_factory(settings.database_url)() as session:
        for member in session.scalars(select(MembershipRecord).where(MembershipRecord.member_id == "jiho", MembershipRecord.organization_id == "scax")):
            member.valid_until = datetime.now(UTC)
        session.commit()
    denied = application.search_materials(old_principal, "membershipmeetingtoken")
    assert denied["results"] == [] and denied["unavailable_materials_count"] == 0


@pytest.mark.parametrize("pending_layer,state", [("raw", "processing"), ("refinement", "pending"), ("refinement", "failed")])
def test_unfinished_new_revision_keeps_latest_completed_material_searchable(tmp_path, pending_layer, state):
    from ax_workspace.platform.persistence import MeetingRawTranscriptRevisionRecord, MeetingTranscriptRefinementRevisionRecord

    client, application, worker, settings = _stack(tmp_path)
    _, recording = _recorded(client)
    raw = application.record_final_meeting_transcript(recording_id=UUID(recording["recording_id"]), provider="fixture",
        provider_reference="final:confirmed", segments=[FinalTranscriptSegment("provider-segment-1", 0, 1500, "confirmedmeetingtoken"),
                                                      FinalTranscriptSegment("provider-segment-2", 1500, 3000, "confirmedtailtoken")])
    application.refine_meeting_transcript(UUID(raw["transcript_revision_id"]))
    assert asyncio.run(worker.run_once())
    principal = application.authenticated_principal("mina")
    query = "confirmedmeetingtoken" if pending_layer == "raw" else "일정 논의"
    before = application.search_materials(principal, query)["results"][0]
    with make_session_factory(settings.database_url)() as session:
        if pending_layer == "raw":
            session.add(MeetingRawTranscriptRevisionRecord(recording_id=UUID(recording["recording_id"]), revision=2,
                state=state, source_kind="async_final", provider="fixture", provider_reference="final:pending", created_at=datetime.now(UTC)))
        else:
            session.add(MeetingTranscriptRefinementRevisionRecord(raw_transcript_revision_id=UUID(raw["transcript_revision_id"]),
                revision=2, state=state, created_at=datetime.now(UTC)))
        session.commit()
    after = application.search_materials(principal, query)["results"]
    assert len(after) == 1
    assert after[0]["material_id"] == before["material_id"]
    assert after[0]["source_contexts"][0]["is_current_revision"] is True


def test_transcript_links_to_canonical_audio_without_claiming_audio_text_extraction(tmp_path):
    import hashlib
    from ax_workspace.platform.persistence import AttachmentRecord, MaterialExtractionRecord

    client, application, worker, settings = _stack(tmp_path)
    meeting, recording = _recorded(client)
    application.record_final_meeting_transcript(recording_id=UUID(recording["recording_id"]), provider="fixture",
        provider_reference="final:audio", segments=[FinalTranscriptSegment("one", 700, 1400, "audiolinktoken")])
    assert asyncio.run(worker.run_once())
    principal = application.authenticated_principal("jiho")
    hit = application.search_materials(principal, "audiolinktoken")["results"][0]
    audio = hit["source_contexts"][0]["recording_material"]
    assert audio["material_id"] != hit["material_id"]
    assert audio["integrity_ref"] == f"sha256:{hashlib.sha256(b'synthetic audio').hexdigest()}"
    opened = client.get(audio["origin"], headers=JIHO)
    assert opened.status_code == 200 and opened.content == b"synthetic audio"
    assert opened.headers["content-type"] == "audio/webm"
    selected = application.search_materials(principal, "audiolinktoken", material_id=UUID(audio["material_id"]))
    assert selected["results"] == [] and selected["searched_materials"] == 0
    assert selected["unavailable_materials"][0]["reason"] == "native_recording"
    assert selected["selected_material"]["integrity_ref"] == audio["integrity_ref"]
    with make_session_factory(settings.database_url)() as session:
        artifact = session.get(AttachmentRecord, UUID(audio["material_id"]))
        assert artifact.size_bytes == len(b"synthetic audio")
        assert not list(session.scalars(select(MaterialExtractionRecord).where(MaterialExtractionRecord.attachment_id == artifact.id)))


def test_audio_original_rechecks_membership_before_storage_and_rejects_hash_drift(tmp_path, monkeypatch):
    from ax_workspace.modules.work.materials import MaterialNotFound
    from ax_workspace.platform.persistence import MeetingRecordingRecord

    client, application, _, settings = _stack(tmp_path)
    meeting, recording = _recorded(client)
    principal = application.authenticated_principal("jiho")
    found = application.search_materials(principal, "녹음")
    audio = next(row for row in found["unavailable_materials"] if row["reason"] == "native_recording")
    identifier = UUID(audio["material_id"])
    origin = audio["source_contexts"][0]["origin"]
    assert client.get(origin, headers=JIHO).content == b"synthetic audio"
    observed = []
    original = application._recording_storage.get
    def capture(key):
        observed.append(key)
        return original(key)
    monkeypatch.setattr(application._recording_storage, "get", capture)
    with make_session_factory(settings.database_url)() as session:
        for member in session.scalars(select(MembershipRecord).where(MembershipRecord.member_id == "jiho", MembershipRecord.organization_id == "scax")):
            member.valid_until = datetime.now(UTC)
        row = session.get(MeetingRecordingRecord, UUID(recording["recording_id"]))
        row.state = "failed"  # Transcription failure does not erase the uploaded original.
        storage_key = row.storage_key
        session.commit()
    with pytest.raises(MaterialNotFound):
        application.open_meeting_material(principal, UUID(meeting["meeting_id"]), identifier)
    assert client.get(origin, headers=JIHO).status_code == 404 and observed == []
    assert client.get(origin, headers=MINA).content == b"synthetic audio"
    from pathlib import Path
    (Path(settings.recordings_dir) / storage_key).write_bytes(b"tampered audio")
    with pytest.raises(MaterialError, match="integrity"):
        application.open_meeting_material(application.authenticated_principal("mina"), UUID(meeting["meeting_id"]), identifier)
    with pytest.raises(MaterialError, match="owning resource"):
        application.purge_attachment(application.authenticated_principal("yuna"), identifier, reason="test")


@pytest.mark.parametrize("lifecycle", ["unbound", "purged"])
def test_inactive_audio_is_neither_openable_nor_advertised_by_transcript(tmp_path, lifecycle):
    from ax_workspace.modules.work.materials import MaterialNotFound
    from ax_workspace.platform.persistence import AttachmentRecord

    client, application, worker, settings = _stack(tmp_path)
    meeting, recording = _recorded(client)
    application.record_final_meeting_transcript(recording_id=UUID(recording["recording_id"]), provider="fixture",
        provider_reference="final:audio-lifecycle", segments=[FinalTranscriptSegment("one", 0, 1000, "audiolifecycletoken")])
    assert asyncio.run(worker.run_once())
    principal = application.authenticated_principal("mina")
    audio = application.search_materials(principal, "audiolifecycletoken")["results"][0]["source_contexts"][0]["recording_material"]
    with make_session_factory(settings.database_url)() as session:
        if lifecycle == "purged":
            session.get(AttachmentRecord, UUID(audio["material_id"])).lifecycle = "purged"
        else:
            binding = session.scalar(select(AttachmentBindingRecord).where(AttachmentBindingRecord.attachment_id == UUID(audio["material_id"])))
            binding.unbound_at = datetime.now(UTC)
        session.commit()
    hit = application.search_materials(principal, "audiolifecycletoken")["results"][0]
    assert "recording_material" not in hit["source_contexts"][0]
    assert client.get(audio["origin"], headers=MINA).status_code == 404
    with pytest.raises(MaterialNotFound):
        application.search_materials(principal, "audio", material_id=UUID(audio["material_id"]))


def test_audio_cannot_be_shared_by_file_binding_after_meeting_access_is_removed(tmp_path):
    from ax_workspace.platform.work_tasks import SqlAlchemyAttachmentRepository

    client, application, _, settings = _stack(tmp_path)
    meeting, _ = _recorded(client)
    audio = application.search_materials(application.authenticated_principal("jiho"), "audio")["unavailable_materials"][0]
    task = client.post("/api/tasks", headers=JIHO, json={"title": "녹음과 무관한 업무"}).json()
    with make_session_factory(settings.database_url)() as session:
        with pytest.raises(ValueError, match="not shareable"):
            SqlAlchemyAttachmentRepository(session).bind(attachment_id=UUID(audio["material_id"]), context_type="task", context_id=task["task_id"], role="input", bound_by="jiho")
        attendee = session.scalar(select(MeetingAttendeeRecord).where(MeetingAttendeeRecord.meeting_id == UUID(meeting["meeting_id"]), MeetingAttendeeRecord.member_id == "jiho"))
        attendee.removed_at = datetime.now(UTC)
        binding = AttachmentBindingRecord(attachment_id=UUID(audio["material_id"]), context_type="task", context_id=task["task_id"],
            role="input", bound_by="jiho", bound_at=datetime.now(UTC))
        session.add(binding)
        session.commit()
        binding_id = str(binding.id)
    found = application.search_materials(application.authenticated_principal("jiho"), "audio")
    assert found["results"] == [] and found["unavailable_materials_count"] == 0
    assert client.get(audio["source_contexts"][0]["origin"], headers=JIHO).status_code == 404
    assert client.get(f"/api/tasks/{task['task_id']}/materials/{binding_id}/content", headers=JIHO).status_code == 404


def test_audio_manual_reextraction_is_rejected_without_creating_a_projection_or_job(tmp_path):
    from ax_workspace.modules.work.material_extraction import PARSER_VERSION
    from ax_workspace.platform.persistence import MaterialExtractionRecord

    client, application, worker, settings = _stack(tmp_path)
    _recorded(client)
    audio = application.search_materials(application.authenticated_principal("mina"), "audio")["unavailable_materials"][0]
    with pytest.raises(ValueError, match="audio"):
        application.reextract_material(attachment_id=UUID(audio["material_id"]), parser_version=PARSER_VERSION)
    with make_session_factory(settings.database_url)() as session:
        assert not list(session.scalars(select(MaterialExtractionRecord)))
    assert not asyncio.run(worker.run_once())
