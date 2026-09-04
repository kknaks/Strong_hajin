"""Meeting core contracts: identity, authorized projection, and immutable notes."""
from __future__ import annotations

from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database


def _client(tmp_path) -> TestClient:
    database_url = f"sqlite:///{tmp_path / 'ax_demo.db'}"
    reset_database(database_url)
    return TestClient(create_app(Settings(RuntimeProfile.TEST, database_url)))


def _create_private_meeting(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/meetings",
        headers={"X-Demo-Persona": "mina"},
        json={
            "organization_id": "scax",
            "title": "비공개 제품 회의",
            "starts_at": "2026-09-10T01:00:00Z",
            "ends_at": "2026-09-10T02:00:00Z",
            "visibility": "private",
            "attendee_ids": ["jiho"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_private_meeting_is_busy_only_until_attendee_or_share_relationship_allows_detail(tmp_path) -> None:
    client = _client(tmp_path)
    meeting = _create_private_meeting(client)
    meeting_id = meeting["meeting_id"]

    attendee = client.get(f"/api/meetings/{meeting_id}", headers={"X-Demo-Persona": "jiho"})
    assert attendee.status_code == 200
    assert attendee.json()["title"] == "비공개 제품 회의"
    assert attendee.json()["attendees"] == [{"member_id": "jiho", "display_name": "지호 (팀장)"}]

    concealed = client.get("/api/meetings", headers={"X-Demo-Persona": "sora"})
    assert concealed.status_code == 200
    assert concealed.json() == [
        {
            "kind": "busy",
            "starts_at": "2026-09-10T01:00:00+00:00",
            "ends_at": "2026-09-10T02:00:00+00:00",
        }
    ]
    assert client.get(f"/api/meetings/{meeting_id}", headers={"X-Demo-Persona": "sora"}).status_code == 404

    shared = client.post(
        f"/api/meetings/{meeting_id}/shares",
        headers={"X-Demo-Persona": "mina"},
        json={"member_id": "sora", "expected_version": meeting["version"]},
    )
    assert shared.status_code == 200, shared.text
    assert shared.json()["version"] == meeting["version"] + 1
    assert client.get(f"/api/meetings/{meeting_id}", headers={"X-Demo-Persona": "sora"}).json()["title"] == "비공개 제품 회의"

    revoked = client.request(
        "DELETE",
        f"/api/meetings/{meeting_id}/shares/sora",
        headers={"X-Demo-Persona": "mina"},
        json={"expected_version": shared.json()["version"]},
    )
    assert revoked.status_code == 200, revoked.text
    assert client.get(f"/api/meetings/{meeting_id}", headers={"X-Demo-Persona": "sora"}).status_code == 404


def test_meeting_note_appends_immutable_versions_and_rejects_stale_write(tmp_path) -> None:
    client = _client(tmp_path)
    meeting = _create_private_meeting(client)
    meeting_id = meeting["meeting_id"]
    headers = {"X-Demo-Persona": "mina"}

    created = client.post(
        f"/api/meetings/{meeting_id}/note",
        headers=headers,
        json={"body": "안건: 권한 모델 확인"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["version"] == 1
    assert created.json()["body"] == "안건: 권한 모델 확인"

    saved = client.patch(
        f"/api/meetings/{meeting_id}/note",
        headers=headers,
        json={"expected_version": 1, "body": "안건: 권한 모델 확인\n결정: private busy projection"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2

    stale = client.patch(
        f"/api/meetings/{meeting_id}/note",
        headers=headers,
        json={"expected_version": 1, "body": "뒤늦은 덮어쓰기"},
    )
    assert stale.status_code == 422

    detail = client.get(f"/api/meetings/{meeting_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["note"]["version"] == 2
    assert [version["body"] for version in detail.json()["note"]["versions"]] == [
        "안건: 권한 모델 확인",
        "안건: 권한 모델 확인\n결정: private busy projection",
    ]
