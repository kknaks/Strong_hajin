"""Recording intake is a Meeting command, not an unauthorised blob upload."""
from __future__ import annotations

import hashlib
import asyncio
import threading
import time
from types import SimpleNamespace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.ax_execution.ai import AiConversationResult, AiGeneration, ProviderUnavailable
from ax_workspace.bootstrap.meeting_worker import MeetingFinalizationWorker
from ax_workspace.modules.meetings.jobs import JOB_KIND_MEETING_FINALIZE, MeetingFinalizationJob, MeetingFinalizationQueue
from ax_workspace.platform.durable_jobs import MemoryDurableJobQueue
from ax_workspace.modules.meetings.application import MeetingApplication, MeetingError, MeetingVersionConflict
from ax_workspace.platform.persistence import (
    ActivityEventRecord,
    MeetingRawTranscriptRevisionRecord,
    MeetingRecordingRecord,
    make_session_factory,
)
from ax_workspace.modules.meetings.summary import SummaryStatement
from ax_workspace.modules.meetings.transcription import (
    FinalTranscriptSegment,
    FinalTranscriptionResult,
    TranscriptionFailure,
)


class RefinementProvider:
    """Test injection only; production composition always uses CodexCliProviderAdapter."""

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request) -> AiGeneration:
        self.calls += 1
        if "statements" in request.output_schema["properties"]:
            assert "Do not invent facts, names, numbers, dates" in request.prompt
            body = (
                '{"body":"## 논의 요약\\n일정을 논의했습니다.","statements":['
                '{"kind":"summary","text":"일정을 논의했습니다.",'
                '"refinement_start_sequence":1,"refinement_end_sequence":1}]}'
            )
        else:
            assert "Never invent or normalize names, numbers, dates" in request.prompt
            body = (
                '{"segments":[{"raw_start_source_key":"provider-segment-1",'
                '"raw_end_source_key":"provider-segment-2","start_ms":0,"end_ms":3000,'
                '"text":"안녕하세요. 일정을 논의합니다.","speaker_label":null,'
                '"correction_kind":"merge","confidence":0.92}]}'
            )
        return AiGeneration(
            provider_run_ref="codex-run-refinement-test",
            provider_session_ref=None,
            body=body,
            requested_model="test",
            observed_model="test",
            requested_tier="test",
            observed_tier=None,
            latency_ms=1,
            usage=None,
        )

    def converse(self, request, *, sink=None, cancel=None) -> AiConversationResult:  # pragma: no cover - test never chats
        raise AssertionError("refinement must use structured generation, not chat")


class FailingMeetingProvider:
    def generate(self, request) -> AiGeneration:
        raise ProviderUnavailable("Codex CLI is unavailable")

    def converse(self, request, *, sink=None, cancel=None) -> AiConversationResult:  # pragma: no cover - test never chats
        raise AssertionError("meeting finalization must use structured generation")


class CompleteTranscriber:
    def transcribe(self, *, data, original_name, content_type, client_reference_id) -> FinalTranscriptionResult:
        del data, original_name, content_type, client_reference_id
        return FinalTranscriptionResult(
            provider_reference="soniox:complete",
            provider_file_ref="soniox:file",
            segments=(
                FinalTranscriptSegment("provider-segment-1", 0, 1_500, "원본 전사"),
                FinalTranscriptSegment("provider-segment-2", 1_500, 3_000, "일정 논의"),
            ),
        )


class CleanupFailingTranscriber:
    def transcribe(self, *, data, original_name, content_type, client_reference_id) -> FinalTranscriptionResult:
        del data, original_name, content_type, client_reference_id
        raise TranscriptionFailure(
            "provider_request_failed",
            retryable=False,
            cleanup_warnings=("cleanup:transcriptions:provider_request_failed",),
        )


class SlowCompleteTranscriber(CompleteTranscriber):
    def __init__(self) -> None:
        self.started = threading.Event()

    def transcribe(self, **kwargs) -> FinalTranscriptionResult:
        self.started.set()
        time.sleep(3.2)
        return super().transcribe(**kwargs)


class LeaseRejectingQueue(MemoryDurableJobQueue):
    def extend_lease(self, job_id, lease_token, lease_seconds) -> bool:
        del job_id, lease_token, lease_seconds
        return False


def _client(tmp_path, *, provider=None) -> TestClient:
    database_url = f"sqlite:///{tmp_path / 'ax_demo.db'}"
    reset_database(database_url)
    return TestClient(
        create_app(
            Settings(
                RuntimeProfile.TEST,
                database_url,
                recordings_dir=str(tmp_path / "recordings"),
            ),
            report_provider=provider,
        )
    )


def _meeting(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/meetings",
        headers={"X-Demo-Persona": "mina"},
        json={
            "organization_id": "scax",
            "title": "녹음할 주간 회의",
            "starts_at": "2026-09-10T01:00:00Z",
            "ends_at": "2026-09-10T02:00:00Z",
            "visibility": "private",
            "attendee_ids": ["jiho"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_recording_start_stop_persists_only_metadata_and_a_content_hash(tmp_path) -> None:
    client = _client(tmp_path)
    meeting = _meeting(client)
    headers = {"X-Demo-Persona": "mina"}

    started = client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/start",
        headers=headers,
        json={"purpose": "회의록 작성"},
    )
    assert started.status_code == 201, started.text
    recording = started.json()
    assert recording["state"] == "recording"
    assert recording["recording_id"]
    assert recording["storage_key"] is None

    audio = b"webm bytes from local media recorder"
    stopped = client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/{recording['recording_id']}/stop",
        headers=headers,
        data={"expected_version": str(recording["version"])},
        files={"audio": ("weekly.webm", audio, "audio/webm")},
    )
    assert stopped.status_code == 200, stopped.text
    final = stopped.json()
    assert final["state"] == "uploaded"
    assert final["content_type"] == "audio/webm"
    assert final["size_bytes"] == len(audio)
    assert final["sha256"] == hashlib.sha256(audio).hexdigest()
    assert final["storage_key"] is None  # object paths are infrastructure provenance, never a browser capability

    detail = client.get(f"/api/meetings/{meeting['meeting_id']}", headers=headers)
    assert detail.status_code == 200
    assert [{key: item[key] for key in ("recording_id", "state", "sha256")} for item in detail.json()["recordings"]] == [
        {"recording_id": recording["recording_id"], "state": "uploaded", "sha256": hashlib.sha256(audio).hexdigest()}
    ]


def test_recording_start_rechecks_the_current_recording_capability_and_meeting_relationship(tmp_path) -> None:
    client = _client(tmp_path)
    meeting = _meeting(client)

    # Sora is neither attendee nor owner; a private detail and a recording command both fail closed.
    assert client.get(f"/api/meetings/{meeting['meeting_id']}", headers={"X-Demo-Persona": "sora"}).status_code == 404
    denied = client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/start",
        headers={"X-Demo-Persona": "sora"},
        json={"purpose": "권한 없는 녹음"},
    )
    assert denied.status_code in {403, 404}


def test_realtime_credential_rechecks_recording_access_and_never_accepts_a_client_reference(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("SONIOX_API_KEY", raising=False)
    client = _client(tmp_path)
    meeting = _meeting(client)
    started = client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/start",
        headers={"X-Demo-Persona": "mina"},
        json={"purpose": "실시간 전사"},
    ).json()
    unavailable = client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/{started['recording_id']}/realtime-credential",
        headers={"X-Demo-Persona": "mina"},
        json={"max_session_duration_seconds": 900, "client_reference_id": "client-must-not-control-this"},
    )
    assert unavailable.status_code == 422  # unknown client_reference_id is rejected before any provider call
    missing_key = client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/{started['recording_id']}/realtime-credential",
        headers={"X-Demo-Persona": "mina"},
        json={"max_session_duration_seconds": 900},
    )
    assert missing_key.status_code == 503
    assert missing_key.json()["detail"] == {"code": "provider_unavailable", "retryable": False}
    denied = client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/{started['recording_id']}/realtime-credential",
        headers={"X-Demo-Persona": "jiho"},
        json={"max_session_duration_seconds": 900},
    )
    assert denied.status_code == 403


def test_final_raw_transcript_is_immutable_and_idempotent_by_provider_reference(tmp_path) -> None:
    client = _client(tmp_path)
    meeting = _meeting(client)
    headers = {"X-Demo-Persona": "mina"}
    started = client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/start",
        headers=headers,
        json={"purpose": "원본 전사"},
    ).json()
    stopped = client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/{started['recording_id']}/stop",
        headers=headers,
        data={"expected_version": str(started["version"])},
        files={"audio": ("raw.webm", b"audio", "audio/webm")},
    )
    assert stopped.status_code == 200

    application = client.app.state.workflow_application
    first = application.record_final_meeting_transcript(
        recording_id=UUID(started["recording_id"]),
        provider="soniox",
        provider_reference="async:transcription-123",
        segments=[
            FinalTranscriptSegment("provider-segment-1", 0, 1_500, " 안녕하세요 ", "Speaker 1"),
            FinalTranscriptSegment("provider-segment-2", 1_500, 3_000, "일정을 논의합니다.", "Speaker 2"),
        ],
    )
    replay = application.record_final_meeting_transcript(
        recording_id=UUID(started["recording_id"]),
        provider="soniox",
        provider_reference="async:transcription-123",
        segments=[FinalTranscriptSegment("different", 0, 2_000, "재전송 데이터")],
    )

    assert replay == first
    assert first["state"] == "completed"
    assert first["source_kind"] == "async_final"
    assert first["segments"] == [
        {
            "segment_id": first["segments"][0]["segment_id"],
            "source_segment_key": "provider-segment-1",
            "start_ms": 0,
            "end_ms": 1_500,
            "text": "안녕하세요",
            "speaker_label": "Speaker 1",
            "confirmed_member_id": None,
        },
        {
            "segment_id": first["segments"][1]["segment_id"],
            "source_segment_key": "provider-segment-2",
            "start_ms": 1_500,
            "end_ms": 3_000,
            "text": "일정을 논의합니다.",
            "speaker_label": "Speaker 2",
            "confirmed_member_id": None,
        },
    ]
    detail = client.get(f"/api/meetings/{meeting['meeting_id']}", headers=headers).json()
    # Raw STT is immutable provenance, not pipeline completion. Refinement and
    # final summary still have to be produced by the durable worker.
    assert detail["recordings"][0]["state"] == "uploaded"


def test_refinement_is_a_versioned_provenance_layer_and_never_overwrites_raw_stt(tmp_path) -> None:
    provider = RefinementProvider()
    client = _client(tmp_path, provider=provider)
    meeting = _meeting(client)
    headers = {"X-Demo-Persona": "mina"}
    started = client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/start",
        headers=headers,
        json={"purpose": "정제 테스트"},
    ).json()
    stopped = client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/{started['recording_id']}/stop",
        headers=headers,
        data={"expected_version": str(started["version"])},
        files={"audio": ("raw.webm", b"audio", "audio/webm")},
    )
    assert stopped.status_code == 200
    application = client.app.state.workflow_application
    raw = application.record_final_meeting_transcript(
        recording_id=UUID(started["recording_id"]),
        provider="soniox",
        provider_reference="async:refinement-source",
        segments=[
            FinalTranscriptSegment("provider-segment-1", 0, 1_500, "안녕 하세요", "Speaker 1"),
            FinalTranscriptSegment("provider-segment-2", 1_500, 3_000, "일정을 논의 합니다", "Speaker 1"),
        ],
    )

    refined = application.refine_meeting_transcript(UUID(raw["transcript_revision_id"]))
    replay = application.refine_meeting_transcript(UUID(raw["transcript_revision_id"]))

    assert provider.calls == 1
    assert replay == refined
    assert refined["state"] == "completed"
    assert refined["provider_call_ref"] == "codex-run-refinement-test"
    assert refined["segments"] == [
        {
            "segment_id": refined["segments"][0]["segment_id"],
            "raw_start_segment_id": raw["segments"][0]["segment_id"],
            "raw_end_segment_id": raw["segments"][1]["segment_id"],
            "start_ms": 0,
            "end_ms": 3_000,
            "text": "안녕하세요. 일정을 논의합니다.",
            "speaker_label": None,
            "confirmed_member_id": None,
            "correction_kind": "merge",
            "confidence": 0.92,
        }
    ]
    # The raw source remains an immutable provider record rather than being replaced by the readable projection.
    assert raw["segments"][0]["text"] == "안녕 하세요"
    assert raw["segments"][1]["text"] == "일정을 논의 합니다"


def test_final_summary_is_pinned_to_refinement_with_raw_evidence(tmp_path) -> None:
    provider = RefinementProvider()
    client = _client(tmp_path, provider=provider)
    meeting = _meeting(client)
    headers = {"X-Demo-Persona": "mina"}
    started = client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/start",
        headers=headers,
        json={"purpose": "요약 테스트"},
    ).json()
    stopped = client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/{started['recording_id']}/stop",
        headers=headers,
        data={"expected_version": str(started["version"])},
        files={"audio": ("raw.webm", b"audio", "audio/webm")},
    )
    assert stopped.status_code == 200
    application = client.app.state.workflow_application
    raw = application.record_final_meeting_transcript(
        recording_id=UUID(started["recording_id"]),
        provider="soniox",
        provider_reference="async:summary-source",
        segments=[
            FinalTranscriptSegment("provider-segment-1", 0, 1_500, "안녕 하세요", "Speaker 1"),
            FinalTranscriptSegment("provider-segment-2", 1_500, 3_000, "일정을 논의 합니다", "Speaker 1"),
        ],
    )
    refined = application.refine_meeting_transcript(UUID(raw["transcript_revision_id"]))

    summary = application.summarize_meeting_transcript(UUID(refined["refinement_revision_id"]))
    replay = application.summarize_meeting_transcript(UUID(refined["refinement_revision_id"]))

    assert provider.calls == 2
    assert replay == summary
    assert summary["kind"] == "final"
    assert summary["state"] == "completed"
    assert summary["raw_transcript_revision_id"] == raw["transcript_revision_id"]
    assert summary["refinement_revision_id"] == refined["refinement_revision_id"]
    assert summary["evidence"] == [
        {
            "statement_index": 1,
            "kind": "summary",
            "text": "일정을 논의했습니다.",
            "refinement_start_segment_id": refined["segments"][0]["segment_id"],
            "refinement_end_segment_id": refined["segments"][0]["segment_id"],
            "raw_start_segment_id": raw["segments"][0]["segment_id"],
            "raw_end_segment_id": raw["segments"][1]["segment_id"],
            "raw_start_ms": 0,
            "raw_end_ms": 3_000,
        }
    ]
    adopted = client.post(
        f"/api/meetings/{meeting['meeting_id']}/summaries/{summary['summary_id']}/adopt",
        headers=headers,
        json={"expected_version": summary["version"]},
    )
    assert adopted.status_code == 200, adopted.text
    adopted_body = adopted.json()
    assert adopted_body["summary"]["state"] == "adopted"
    assert adopted_body["summary"]["version"] == summary["version"] + 1
    assert adopted_body["note"]["version"] == 1
    assert adopted_body["note"]["body"] == summary["body"]
    assert adopted_body["note"]["versions"][0]["source_evidence"] == [
        {
            "summary_id": summary["summary_id"],
            "statement_index": 1,
            "raw_start_segment_id": raw["segments"][0]["segment_id"],
            "raw_end_segment_id": raw["segments"][1]["segment_id"],
            "raw_start_ms": 0,
            "raw_end_ms": 3_000,
        }
    ]


def test_summary_evidence_rejects_a_range_with_a_missing_refinement_sequence() -> None:
    with pytest.raises(MeetingError, match="contiguous"):
        MeetingApplication._validate_summary_evidence(
            [SimpleNamespace(sequence=1), SimpleNamespace(sequence=3)],
            [SummaryStatement("summary", "근거 없는 중간 구간은 안 됩니다.", 1, 3)],
        )


def test_human_speaker_mapping_is_a_separate_revision_and_overrides_refinement_label(tmp_path) -> None:
    provider = RefinementProvider()
    client = _client(tmp_path, provider=provider)
    meeting = _meeting(client)
    headers = {"X-Demo-Persona": "mina"}
    started = client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/start",
        headers=headers,
        json={"purpose": "화자 확인"},
    ).json()
    assert client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/{started['recording_id']}/stop",
        headers=headers,
        data={"expected_version": str(started["version"])},
        files={"audio": ("raw.webm", b"audio", "audio/webm")},
    ).status_code == 200
    application = client.app.state.workflow_application
    raw = application.record_final_meeting_transcript(
        recording_id=UUID(started["recording_id"]),
        provider="soniox",
        provider_reference="async:speaker-source",
        segments=[
            FinalTranscriptSegment("provider-segment-1", 0, 1_500, "안녕하세요", "Speaker 1"),
            FinalTranscriptSegment("provider-segment-2", 1_500, 3_000, "일정을 논의합니다", "Speaker 1"),
        ],
    )
    assert raw["segments"][0]["confirmed_member_id"] is None
    assigned = client.post(
        f"/api/meetings/{meeting['meeting_id']}/speaker-assignments",
        headers=headers,
        json={
            "transcript_revision_id": raw["transcript_revision_id"],
            "speaker_label": "Speaker 1",
            "member_id": "jiho",
            "scope": "speaker_track",
            "raw_start_source_key": "provider-segment-1",
            "raw_end_source_key": "provider-segment-2",
        },
    )
    assert assigned.status_code == 201, assigned.text
    assert assigned.json() == {
        "speaker_assignment_id": assigned.json()["speaker_assignment_id"],
        "transcript_revision_id": raw["transcript_revision_id"],
        "speaker_label": "Speaker 1",
        "member_id": "jiho",
        "scope": "speaker_track",
        "raw_start_segment_id": raw["segments"][0]["segment_id"],
        "raw_end_segment_id": raw["segments"][1]["segment_id"],
        "source_audio_start_ms": 0,
        "source_audio_end_ms": 3_000,
        "source": "human_confirmed",
        "state": "active",
    }
    refined = application.refine_meeting_transcript(UUID(raw["transcript_revision_id"]))
    assert refined["segments"][0]["speaker_label"] is None  # provider did not assert a member identity
    assert refined["segments"][0]["confirmed_member_id"] == "jiho"  # human mapping wins
    denied = client.post(
        f"/api/meetings/{meeting['meeting_id']}/speaker-assignments",
        headers={"X-Demo-Persona": "sora"},
        json={
            "transcript_revision_id": raw["transcript_revision_id"],
            "speaker_label": "Speaker 1",
            "member_id": "sora",
            "scope": "segment_range",
            "raw_start_source_key": "provider-segment-1",
            "raw_end_source_key": "provider-segment-1",
        },
    )
    assert denied.status_code in {403, 404}


def test_expired_finalization_claim_is_reclaimed_and_stale_worker_cannot_persist_raw_transcript(tmp_path) -> None:
    client = _client(tmp_path)
    meeting = _meeting(client)
    headers = {"X-Demo-Persona": "mina"}
    started = client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/start",
        headers=headers,
        json={"purpose": "crash recovery"},
    ).json()
    assert client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/{started['recording_id']}/stop",
        headers=headers,
        data={"expected_version": str(started["version"])},
        files={"audio": ("raw.webm", b"audio", "audio/webm")},
    ).status_code == 200
    application = client.app.state.workflow_application
    first_lease = uuid4()
    first = application.meeting_finalization_input(
        UUID(started["recording_id"]), lease_token=first_lease, stale_after_seconds=60
    )
    assert first["reclaimed_stale_attempt"] is False
    assert application.meeting_finalization_input(
        UUID(started["recording_id"]), lease_token=uuid4(), stale_after_seconds=60
    ) == {"contended": True}

    database_url = f"sqlite:///{tmp_path / 'ax_demo.db'}"
    with make_session_factory(database_url)() as session:
        recording = session.get(MeetingRecordingRecord, UUID(started["recording_id"]))
        assert recording is not None
        recording.finalization_started_at = datetime.now(UTC) - timedelta(seconds=120)
        session.commit()

    replacement_lease = uuid4()
    reclaimed = application.meeting_finalization_input(
        UUID(started["recording_id"]), lease_token=replacement_lease, stale_after_seconds=60
    )
    assert reclaimed["reclaimed_stale_attempt"] is True
    with pytest.raises(MeetingVersionConflict, match="lease is stale"):
        application.record_final_meeting_transcript(
            recording_id=UUID(started["recording_id"]),
            provider="soniox",
            provider_reference="async:stale-worker",
            segments=[FinalTranscriptSegment("old", 0, 500, "늦은 결과")],
            finalization_lease_token=first_lease,
        )
    completed = application.record_final_meeting_transcript(
        recording_id=UUID(started["recording_id"]),
        provider="soniox",
        provider_reference="async:replacement-worker",
        segments=[FinalTranscriptSegment("new", 0, 500, "새 worker 결과")],
        finalization_lease_token=replacement_lease,
    )
    assert completed["segments"][0]["text"] == "새 worker 결과"


def test_finalization_resumes_from_raw_then_refinement_and_completes_only_after_final_summary(tmp_path) -> None:
    provider = RefinementProvider()
    client = _client(tmp_path, provider=provider)
    meeting = _meeting(client)
    headers = {"X-Demo-Persona": "mina"}
    started = client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/start",
        headers=headers,
        json={"purpose": "stage resume"},
    ).json()
    client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/{started['recording_id']}/stop",
        headers=headers,
        data={"expected_version": str(started["version"])},
        files={"audio": ("raw.webm", b"audio", "audio/webm")},
    ).raise_for_status()
    recording_id = UUID(started["recording_id"])
    application = client.app.state.workflow_application
    raw_lease = uuid4()
    assert application.meeting_finalization_input(recording_id, lease_token=raw_lease, stale_after_seconds=60)["stage"] == "transcribe"
    raw = application.record_final_meeting_transcript(
        recording_id=recording_id,
        provider="soniox",
        provider_reference="async:raw-before-crash",
        segments=[
            FinalTranscriptSegment("provider-segment-1", 0, 1_500, "안녕하세요"),
            FinalTranscriptSegment("provider-segment-2", 1_500, 3_000, "일정을 논의합니다."),
        ],
        finalization_lease_token=raw_lease,
    )

    database_url = f"sqlite:///{tmp_path / 'ax_demo.db'}"
    with make_session_factory(database_url)() as session:
        recording = session.get(MeetingRecordingRecord, recording_id)
        assert recording is not None and recording.state == "transcribing"
        recording.finalization_started_at = datetime.now(UTC) - timedelta(seconds=120)
        session.commit()

    refinement_lease = uuid4()
    plan = application.meeting_finalization_input(recording_id, lease_token=refinement_lease, stale_after_seconds=60)
    assert plan == {
        "stage": "refinement",
        "transcript_revision_id": raw["transcript_revision_id"],
        "reclaimed_stale_attempt": True,
    }
    with pytest.raises(MeetingVersionConflict, match="lease is stale"):
        application.refine_meeting_transcript(
            UUID(raw["transcript_revision_id"]), finalization_lease_token=raw_lease
        )
    refinement = application.refine_meeting_transcript(
        UUID(raw["transcript_revision_id"]), finalization_lease_token=refinement_lease
    )

    with make_session_factory(database_url)() as session:
        recording = session.get(MeetingRecordingRecord, recording_id)
        assert recording is not None and recording.state == "transcribing"
        recording.finalization_started_at = datetime.now(UTC) - timedelta(seconds=120)
        session.commit()

    summary_lease = uuid4()
    plan = application.meeting_finalization_input(recording_id, lease_token=summary_lease, stale_after_seconds=60)
    assert plan == {
        "stage": "summary",
        "refinement_revision_id": refinement["refinement_revision_id"],
        "reclaimed_stale_attempt": True,
    }
    with pytest.raises(MeetingVersionConflict, match="lease is stale"):
        application.summarize_meeting_transcript(
            UUID(refinement["refinement_revision_id"]), finalization_lease_token=refinement_lease
        )
    application.summarize_meeting_transcript(
        UUID(refinement["refinement_revision_id"]), finalization_lease_token=summary_lease
    )
    application.complete_meeting_finalization(recording_id, lease_token=summary_lease)
    detail = client.get(f"/api/meetings/{meeting['meeting_id']}", headers=headers).json()
    assert detail["recordings"][0]["state"] == "transcribed"


def test_stale_finalization_lease_cannot_fail_retry_or_write_cleanup_warning(tmp_path) -> None:
    client = _client(tmp_path)
    meeting = _meeting(client)
    headers = {"X-Demo-Persona": "mina"}
    started = client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/start",
        headers=headers,
        json={"purpose": "fenced failure writes"},
    ).json()
    client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/{started['recording_id']}/stop",
        headers=headers,
        data={"expected_version": str(started["version"])},
        files={"audio": ("raw.webm", b"audio", "audio/webm")},
    ).raise_for_status()
    application = client.app.state.workflow_application
    old_lease = uuid4()
    application.meeting_finalization_input(UUID(started["recording_id"]), lease_token=old_lease, stale_after_seconds=0)
    replacement_lease = uuid4()
    application.meeting_finalization_input(
        UUID(started["recording_id"]), lease_token=replacement_lease, stale_after_seconds=0
    )

    with pytest.raises(MeetingVersionConflict, match="lease is stale"):
        application.retry_meeting_finalization(
            UUID(started["recording_id"]), lease_token=old_lease, code="provider_timeout"
        )
    with pytest.raises(MeetingVersionConflict, match="lease is stale"):
        application.fail_meeting_finalization(
            UUID(started["recording_id"]), lease_token=old_lease, code="provider_timeout"
        )
    with pytest.raises(MeetingVersionConflict, match="lease is stale"):
        application.record_meeting_finalization_cleanup_warning(
            UUID(started["recording_id"]),
            lease_token=old_lease,
            warnings=("cleanup:files:provider_request_failed",),
        )

    application.fail_meeting_finalization(
        UUID(started["recording_id"]), lease_token=replacement_lease, code="provider_timeout"
    )
    with make_session_factory(f"sqlite:///{tmp_path / 'ax_demo.db'}")() as session:
        recording = session.get(MeetingRecordingRecord, UUID(started["recording_id"]))
        assert recording is not None
        assert recording.state == "failed"
        assert recording.error_code == "provider_timeout"


def test_worker_records_codex_failure_and_provider_cleanup_warning_instead_of_completing_silently(tmp_path) -> None:
    client = _client(tmp_path)
    meeting = _meeting(client)
    headers = {"X-Demo-Persona": "mina"}
    started = client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/start",
        headers=headers,
        json={"purpose": "Codex failure"},
    ).json()
    client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/{started['recording_id']}/stop",
        headers=headers,
        data={"expected_version": str(started["version"])},
        files={"audio": ("raw.webm", b"audio", "audio/webm")},
    ).raise_for_status()
    settings = Settings(
        RuntimeProfile.TEST,
        f"sqlite:///{tmp_path / 'ax_demo.db'}",
        recordings_dir=str(tmp_path / "recordings"),
        meeting_queue_max_attempts=1,
    )
    worker = MeetingFinalizationWorker(
        settings,
        transcriber=CompleteTranscriber(),
        provider=FailingMeetingProvider(),
    )
    assert worker.process(UUID(started["recording_id"]), lease_token=uuid4(), attempt=1) == "failed"
    with make_session_factory(settings.database_url)() as session:
        recording = session.get(MeetingRecordingRecord, UUID(started["recording_id"]))
        assert recording is not None
        assert recording.state == "failed"
        assert recording.error_code == "meeting_ai_provider_failed"

    # A Soniox error with an unsuccessful cleanup persists the warning before terminal failure.
    second = client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/start",
        headers=headers,
        json={"purpose": "cleanup warning"},
    ).json()
    client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/{second['recording_id']}/stop",
        headers=headers,
        data={"expected_version": str(second["version"])},
        files={"audio": ("raw-2.webm", b"audio", "audio/webm")},
    ).raise_for_status()
    cleanup_worker = MeetingFinalizationWorker(
        settings,
        transcriber=CleanupFailingTranscriber(),
        provider=FailingMeetingProvider(),
    )
    assert cleanup_worker.process(UUID(second["recording_id"]), lease_token=uuid4(), attempt=1) == "failed"
    with make_session_factory(settings.database_url)() as session:
        recording = session.get(MeetingRecordingRecord, UUID(second["recording_id"]))
        assert recording is not None
        assert recording.state == "failed"
        event_kinds = set(
            session.scalars(
                select(ActivityEventRecord.event_kind).where(ActivityEventRecord.target_id == str(meeting["meeting_id"]))
            )
        )
        assert "meeting.transcription_failed" in event_kinds
        assert "meeting.transcription_provider_cleanup_warning" in event_kinds


def test_worker_heartbeats_long_transcription_before_visibility_timeout_and_prevents_reclaim(tmp_path) -> None:
    provider = RefinementProvider()
    client = _client(tmp_path, provider=provider)
    meeting = _meeting(client)
    headers = {"X-Demo-Persona": "mina"}
    started = client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/start",
        headers=headers,
        json={"purpose": "heartbeat"},
    ).json()
    client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/{started['recording_id']}/stop",
        headers=headers,
        data={"expected_version": str(started["version"])},
        files={"audio": ("heartbeat.webm", b"audio", "audio/webm")},
    ).raise_for_status()
    settings = Settings(
        RuntimeProfile.TEST,
        f"sqlite:///{tmp_path / 'ax_demo.db'}",
        recordings_dir=str(tmp_path / "recordings"),
        meeting_queue_visibility_timeout=2,
        meeting_worker_concurrency=1,
    )
    queue = MemoryDurableJobQueue()
    MeetingFinalizationQueue(queue).enqueue(MeetingFinalizationJob(UUID(started["recording_id"])))
    transcriber = SlowCompleteTranscriber()
    worker = MeetingFinalizationWorker(
        settings,
        provider=provider,
        transcriber=transcriber,
        queue_factory=lambda _session: queue,
    )

    async def scenario() -> None:
        running = asyncio.create_task(worker.run_once())
        assert await asyncio.to_thread(transcriber.started.wait, 2)
        # The original two-second visibility interval elapsed, but heartbeat
        # extended the fenced job. A second worker cannot issue Soniox work.
        await asyncio.sleep(2.2)
        assert queue.claim(JOB_KIND_MEETING_FINALIZE, limit=1, lease_seconds=2, worker_id="second") == []
        assert await running is True

    asyncio.run(scenario())
    assert queue.snapshot()[0]["state"] == "completed", queue.snapshot()


def test_worker_drops_late_provider_result_when_heartbeat_loses_its_fenced_lease(tmp_path) -> None:
    provider = RefinementProvider()
    client = _client(tmp_path, provider=provider)
    meeting = _meeting(client)
    headers = {"X-Demo-Persona": "mina"}
    started = client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/start",
        headers=headers,
        json={"purpose": "lost heartbeat"},
    ).json()
    client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/{started['recording_id']}/stop",
        headers=headers,
        data={"expected_version": str(started["version"])},
        files={"audio": ("lost-heartbeat.webm", b"audio", "audio/webm")},
    ).raise_for_status()
    settings = Settings(
        RuntimeProfile.TEST,
        f"sqlite:///{tmp_path / 'ax_demo.db'}",
        recordings_dir=str(tmp_path / "recordings"),
        meeting_queue_visibility_timeout=2,
    )
    queue = LeaseRejectingQueue()
    MeetingFinalizationQueue(queue).enqueue(MeetingFinalizationJob(UUID(started["recording_id"])))
    worker = MeetingFinalizationWorker(
        settings,
        provider=provider,
        transcriber=SlowCompleteTranscriber(),
        queue_factory=lambda _session: queue,
    )
    assert asyncio.run(worker.run_once()) is True
    # No raw result is persisted after heartbeat failure, and the durable job
    # remains recoverable by a new owner once its original visibility expires.
    with make_session_factory(settings.database_url)() as session:
        assert session.scalar(select(MeetingRawTranscriptRevisionRecord).where(MeetingRawTranscriptRevisionRecord.recording_id == UUID(started["recording_id"]))) is None
        recording = session.get(MeetingRecordingRecord, UUID(started["recording_id"]))
        assert recording is not None and recording.state == "transcribing"
    reclaimed = queue.claim(JOB_KIND_MEETING_FINALIZE, limit=1, lease_seconds=2, worker_id="recovery")
    assert len(reclaimed) == 1


def test_meeting_detail_carries_the_whole_record_for_someone_allowed_to_read_it(tmp_path) -> None:
    """One authorized read gives the UI everything it renders: note, recordings, transcript layers, summaries.

    The layering stays visible in the projection — immutable raw STT under a versioned refinement under an
    evidence-bound summary — so a client can show the refined transcript by default and still reach the original.
    """
    provider = RefinementProvider()
    client = _client(tmp_path, provider=provider)
    meeting = _meeting(client)
    mina = {"X-Demo-Persona": "mina"}
    started = client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/start", headers=mina, json={"purpose": "상세 확인"}
    ).json()
    client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/{started['recording_id']}/stop",
        headers=mina,
        data={"expected_version": str(started["version"])},
        files={"audio": ("raw.webm", b"audio", "audio/webm")},
    )
    application = client.app.state.workflow_application
    raw = application.record_final_meeting_transcript(
        recording_id=UUID(started["recording_id"]),
        provider="soniox",
        provider_reference="async:detail-source",
        segments=[
            FinalTranscriptSegment("provider-segment-1", 0, 1_500, "안녕 하세요", "Speaker 1"),
            FinalTranscriptSegment("provider-segment-2", 1_500, 3_000, "일정을 논의 합니다", "Speaker 1"),
        ],
    )
    refined = application.refine_meeting_transcript(UUID(raw["transcript_revision_id"]))
    summary = application.summarize_meeting_transcript(UUID(refined["refinement_revision_id"]))

    detail = client.get(f"/api/meetings/{meeting['meeting_id']}", headers=mina)
    assert detail.status_code == 200, detail.text
    body = detail.json()

    [recording] = body["recordings"]
    assert recording["recording_id"] == started["recording_id"]
    # The raw transcript is the source truth and stays reachable.
    assert recording["raw_transcript"]["transcript_revision_id"] == raw["transcript_revision_id"]
    assert [segment["text"] for segment in recording["raw_transcript"]["segments"]] == ["안녕 하세요", "일정을 논의 합니다"]
    # The refinement is a derived projection that points back at the raw segments it covers.
    assert recording["refinement"]["refinement_revision_id"] == refined["refinement_revision_id"]
    assert recording["refinement"]["raw_transcript_revision_id"] == raw["transcript_revision_id"]
    assert recording["refinement"]["segments"][0]["raw_start_segment_id"] == raw["segments"][0]["segment_id"]
    # Speaker confirmations belong to the transcript, not to the summary.
    assert recording["speaker_assignments"] == []

    # Summaries hang off the meeting with their evidence, so a click can scroll to the segment it came from.
    [projected_summary] = body["summaries"]
    assert projected_summary["summary_id"] == summary["summary_id"]
    assert projected_summary["kind"] == "final" and projected_summary["state"] == "completed"
    assert projected_summary["evidence"][0]["raw_start_ms"] == 0
    assert projected_summary["evidence"][0]["refinement_start_segment_id"] == refined["segments"][0]["segment_id"]

    # A storage key or provider file handle is never a browser capability.
    assert recording["storage_key"] is None
    assert "provider_reference" not in recording


def test_meeting_detail_stays_closed_to_someone_without_a_relationship(tmp_path) -> None:
    """A private meeting leaks nothing through the detail read, and only a busy block through the calendar."""
    provider = RefinementProvider()
    client = _client(tmp_path, provider=provider)
    meeting = _meeting(client)
    mina = {"X-Demo-Persona": "mina"}
    started = client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/start", headers=mina, json={"purpose": "비공개"}
    ).json()
    client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/{started['recording_id']}/stop",
        headers=mina,
        data={"expected_version": str(started["version"])},
        files={"audio": ("raw.webm", b"audio", "audio/webm")},
    )
    application = client.app.state.workflow_application
    raw = application.record_final_meeting_transcript(
        recording_id=UUID(started["recording_id"]),
        provider="soniox",
        provider_reference="async:private-source",
        segments=[
            FinalTranscriptSegment("provider-segment-1", 0, 1_500, "비밀 논의 입니다", "Speaker 1"),
            FinalTranscriptSegment("provider-segment-2", 1_500, 3_000, "공개 하지 않습니다", "Speaker 1"),
        ],
    )
    refined = application.refine_meeting_transcript(UUID(raw["transcript_revision_id"]))
    application.summarize_meeting_transcript(UUID(refined["refinement_revision_id"]))

    outsider = {"X-Demo-Persona": "sora"}
    assert client.get(f"/api/meetings/{meeting['meeting_id']}", headers=outsider).status_code == 404
    listed = client.get("/api/meetings", headers=outsider).json()
    assert [row["kind"] for row in listed] == ["busy"]
    # The busy block carries time and nothing else: no title, transcript, summary or attendee count.
    assert set(listed[0]) == {"kind", "starts_at", "ends_at"}
    assert "비밀 논의" not in str(listed)
