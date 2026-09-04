"""Recording intake is a Meeting command, not an unauthorised blob upload."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.ax_execution.ai import AiConversationResult, AiGeneration
from ax_workspace.modules.meetings.application import MeetingApplication, MeetingError
from ax_workspace.modules.meetings.summary import SummaryStatement
from ax_workspace.modules.meetings.transcription import FinalTranscriptSegment


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
    assert detail["recordings"][0]["state"] == "transcribed"


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
