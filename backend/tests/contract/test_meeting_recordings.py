"""Recording intake is a Meeting command, not an unauthorised blob upload."""
from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database


def _client(tmp_path) -> TestClient:
    database_url = f"sqlite:///{tmp_path / 'ax_demo.db'}"
    reset_database(database_url)
    return TestClient(
        create_app(
            Settings(
                RuntimeProfile.TEST,
                database_url,
                recordings_dir=str(tmp_path / "recordings"),
            )
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
