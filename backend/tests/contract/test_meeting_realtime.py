"""녹음 중에 들린 것은 들린 대로만 남는다.

A live transcript arrives as tokens: some still changing, some settled. Only the settled ones are written, in the
order they were heard, under a revision that says it came from the live stream — the file that is transcribed
afterwards is a different, authoritative reading and never gets overwritten by this one. Only the person recording
may write to it, and only while the recording is open.
"""
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import MeetingRawTranscriptRevisionRecord, make_session_factory

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(
        RuntimeProfile.TEST, database_url,
        materials_dir=str(tmp_path / "materials"), recordings_dir=str(tmp_path / "recordings"),
    )
    app = create_app(settings)
    return TestClient(app), database_url


def _recording(client) -> tuple[str, dict]:
    meeting = client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "organization_id": "scax", "title": "실시간 회의",
            "starts_at": "2026-09-10T01:00:00Z", "ends_at": "2026-09-10T02:00:00Z",
            "visibility": "private", "attendee_ids": ["jiho"],
        },
    ).json()
    started = client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/start", headers=MINA, json={"purpose": "실시간 전사"}
    ).json()
    return meeting["meeting_id"], started


def _post(client, meeting_id: str, recording, segments, headers=MINA):
    return client.post(
        f"/api/meetings/{meeting_id}/recordings/{recording['recording_id']}/realtime-segments",
        headers=headers,
        json={"segments": segments},
    )


def test_settled_tokens_are_appended_in_the_order_they_were_heard(tmp_path) -> None:
    client, database_url = _stack(tmp_path)
    meeting_id, recording = _recording(client)

    first = _post(client, meeting_id, recording, [
        {"text": "안녕하세요.", "start_ms": 0, "end_ms": 1200, "speaker_label": "화자 1"},
        {"text": "일정을 정리하겠습니다.", "start_ms": 1200, "end_ms": 3000, "speaker_label": "화자 1"},
    ])
    assert first.status_code == 200, first.text
    assert first.json()["segment_count"] == 2

    second = _post(client, meeting_id, recording, [
        {"text": "계약서는 다음 주에 봅니다.", "start_ms": 3000, "end_ms": 5000, "speaker_label": "화자 2"},
    ])
    assert second.json()["segment_count"] == 3

    detail = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()
    live = detail["recordings"][0]["raw_transcript"]
    assert live["source_kind"] == "realtime"
    assert [segment["text"] for segment in live["segments"]] == [
        "안녕하세요.", "일정을 정리하겠습니다.", "계약서는 다음 주에 봅니다.",
    ]
    assert [segment["sequence"] for segment in live["segments"]] == [1, 2, 3]

    with make_session_factory(database_url)() as session:
        revisions = list(session.scalars(select(MeetingRawTranscriptRevisionRecord)))
    # One live revision that grows, not one per batch.
    assert len(revisions) == 1 and revisions[0].source_kind == "realtime"


def test_only_the_person_recording_may_write_while_it_is_open(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    meeting_id, recording = _recording(client)

    # An attendee who is not recording cannot write to the live transcript.
    assert _post(client, meeting_id, recording, [{"text": "몰래", "start_ms": 0, "end_ms": 100}], JIHO).status_code in {403, 404}

    stopped = client.post(
        f"/api/meetings/{meeting_id}/recordings/{recording['recording_id']}/stop",
        headers=MINA,
        data={"expected_version": str(recording["version"])},
        files={"audio": ("raw.webm", b"audio", "audio/webm")},
    )
    assert stopped.status_code == 200, stopped.text
    # After the recording is closed the live stream is over; what happens next is the file's own reading.
    late = _post(client, meeting_id, recording, [{"text": "늦게 온 말", "start_ms": 6000, "end_ms": 7000}])
    assert late.status_code == 422


def test_a_live_reading_never_overwrites_the_recorded_one(tmp_path) -> None:
    client, database_url = _stack(tmp_path)
    from ax_workspace.modules.meetings.transcription import FinalTranscriptSegment

    meeting_id, recording = _recording(client)
    _post(client, meeting_id, recording, [{"text": "들리는 대로 적은 말", "start_ms": 0, "end_ms": 1000}])
    client.post(
        f"/api/meetings/{meeting_id}/recordings/{recording['recording_id']}/stop",
        headers=MINA,
        data={"expected_version": str(recording["version"])},
        files={"audio": ("raw.webm", b"audio", "audio/webm")},
    )

    application = client.app.state.workflow_application
    final = application.record_final_meeting_transcript(
        recording_id=UUID(recording["recording_id"]),
        provider="soniox",
        provider_reference="async:final-1",
        segments=[FinalTranscriptSegment("seg-1", 0, 1_000, "파일에서 다시 읽은 말", "Speaker 1")],
    )

    with make_session_factory(database_url)() as session:
        revisions = list(session.scalars(select(MeetingRawTranscriptRevisionRecord).order_by(MeetingRawTranscriptRevisionRecord.revision)))
    assert [row.source_kind for row in revisions] == ["realtime", "async_final"]
    assert str(revisions[-1].id) == final["transcript_revision_id"]
    # The live reading is still there, as what was heard at the time.
    detail = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()
    assert detail["recordings"][0]["raw_transcript"]["source_kind"] == "async_final"


def test_nothing_that_is_still_changing_is_written(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    meeting_id, recording = _recording(client)

    for label, segments in (
        ("빈 텍스트", [{"text": "   ", "start_ms": 0, "end_ms": 100}]),
        ("끝이 시작보다 앞", [{"text": "거꾸로", "start_ms": 500, "end_ms": 100}]),
        ("음수 시각", [{"text": "음수", "start_ms": -1, "end_ms": 100}]),
        ("빈 배치", []),
    ):
        assert _post(client, meeting_id, recording, segments).status_code == 422, label

    detail = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()
    assert detail["recordings"][0]["raw_transcript"] is None


def test_the_live_reading_never_stands_in_for_reading_the_file(tmp_path) -> None:
    """A recording that was streamed still has to be transcribed from its own audio.

    The live revision is a reading of what was heard while the room was talking. If finalization mistook it for the
    recording's transcript, the file would never be read at all and the authoritative revision would never exist.
    """
    client, _ = _stack(tmp_path)
    meeting_id, recording = _recording(client)
    _post(client, meeting_id, recording, [{"text": "들으면서 적은 말", "start_ms": 0, "end_ms": 1_000}])
    client.post(
        f"/api/meetings/{meeting_id}/recordings/{recording['recording_id']}/stop",
        headers=MINA,
        data={"expected_version": str(recording["version"])},
        files={"audio": ("raw.webm", b"audio", "audio/webm")},
    )

    application = client.app.state.workflow_application
    plan = application.meeting_finalization_input(
        UUID(recording["recording_id"]), lease_token=uuid4(), stale_after_seconds=60
    )
    assert plan["stage"] == "transcribe"
    assert plan["storage_key"]
