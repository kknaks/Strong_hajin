from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.mcp import McpReportsFacade
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import ConversationTurnRecord, make_session_factory


MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
SORA = {"X-Demo-Persona": "sora"}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    app = create_app(settings)
    return TestClient(app), app.state.workflow_application, settings


def _execution(client: TestClient, settings: Settings) -> UUID:
    conversation = client.post("/api/conversations", headers=MINA, json={"title": "회의 공유"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": "meeting-share-turn"},
        json={"body": "이 회의를 소라에게 공유해줘", "context": []},
    ).json()
    with make_session_factory(settings.database_url)() as session:
        return session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id


def test_meeting_share_is_a_confirmed_action_with_one_reauthorized_notification(tmp_path, monkeypatch) -> None:
    client, _, settings = _stack(tmp_path)
    meeting = client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "organization_id": "scax",
            "title": "고객 온보딩 공유 회의",
            "starts_at": "2026-09-11T01:00:00Z",
            "ends_at": "2026-09-11T02:00:00Z",
            "visibility": "private",
            "attendee_ids": [],
        },
    ).json()
    monkeypatch.setenv("AX_MCP_CAUSATION_ID", str(_execution(client, settings)))

    proposal = McpReportsFacade(settings, "mina").share_meeting(
        meeting["meeting_id"], "sora", meeting["version"]
    )
    assert proposal["action_type"] == "meeting.share"
    assert client.get(f"/api/meetings/{meeting['meeting_id']}", headers=SORA).status_code == 404
    assert client.get("/api/notifications", headers=SORA).json() == []

    item = client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()
    assert item["operation_label"] == "회의 공유"
    assert {row["label"]: row["value"] for row in item["preview"]} == {
        "회의": "고객 온보딩 공유 회의",
        "공유 대상": "소라",
        "공유 범위": "회의 열람",
    }
    payload = {"expected_version": item["expected_version"]}
    first = client.post(
        f"/api/action-items/{item['action_item_id']}/commands/approve", headers=MINA, json=payload
    )
    replay = client.post(
        f"/api/action-items/{item['action_item_id']}/commands/approve", headers=MINA, json=payload
    )
    assert first.status_code == replay.status_code == 200
    assert replay.json() == first.json()
    shared = client.get(f"/api/meetings/{meeting['meeting_id']}", headers=SORA)
    assert shared.status_code == 200

    [notification] = client.get("/api/notifications", headers=SORA).json()
    assert notification["kind"] == "meeting.shared"
    assert notification["summary"] == "민아님이 ‘고객 온보딩 공유 회의’ 회의를 공유했습니다."
    assert notification["resource"] == {
        "type": "meeting",
        "id": meeting["meeting_id"],
        "version": meeting["version"] + 1,
        "title": "고객 온보딩 공유 회의",
    }
    assert notification["read_at"] is None
    assert client.post(f"/api/notifications/{notification['notification_id']}/read", headers=JIHO).status_code == 404
    read = client.post(f"/api/notifications/{notification['notification_id']}/read", headers=SORA)
    assert read.status_code == 200 and read.json()["read_at"] is not None

    revoked = client.request(
        "DELETE",
        f"/api/meetings/{meeting['meeting_id']}/shares/sora",
        headers=MINA,
        json={"expected_version": meeting["version"] + 1},
    )
    assert revoked.status_code == 200
    assert client.get(f"/api/meetings/{meeting['meeting_id']}", headers=SORA).status_code == 404
    assert client.get("/api/notifications", headers=SORA).json() == []


def test_work_request_delivery_and_acceptance_notify_the_other_party_once(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    request = client.post(
        "/api/work-requests",
        headers=MINA,
        json={"title": "배포 체크 요청", "assignee_id": "jiho"},
    ).json()
    [received] = client.get("/api/notifications", headers=JIHO).json()
    assert received["kind"] == "work_request.received"
    assert received["resource"]["id"] == request["request_id"]
    assert received["resource"]["title"] == "배포 체크 요청"

    accepted = client.post(
        f"/api/work-requests/{request['request_id']}/accept",
        headers=JIHO,
        json={"expected_version": request["version"]},
    )
    assert accepted.status_code == 200
    [completed] = client.get("/api/notifications", headers=MINA).json()
    assert completed["kind"] == "work_request.accepted"
    assert completed["summary"] == "지호님이 ‘배포 체크 요청’ 업무 요청을 수락했습니다."
    assert completed["resource"]["type"] == "work_request"
    assert completed["resource"]["version"] == request["version"] + 1
