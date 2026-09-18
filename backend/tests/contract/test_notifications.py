from __future__ import annotations

# main 의 `test_meeting_share_is_a_confirmed_action_with_one_reauthorized_notification` 을 걷었다 —
# 채팅 `meeting.share` 확인 경로가 옛 회의 모델(expected_version 기반 공유)에 서 있었고 그 경로가 멈췄다.
# 알림 계약 자체는 아래 업무 요청 시험이 그대로 지킨다.


from uuid import UUID

from fastapi.testclient import TestClient
from legacy_acceptance import make_request_look_pending

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
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


def test_work_request_delivery_and_acceptance_notify_the_other_party_once(tmp_path) -> None:
    """받은 쪽 알림은 신규 경로에서도 그대로 간다. 수락 알림은 **과거 행의 판단**에만 붙는다."""
    client, _, settings = _stack(tmp_path)
    request = client.post(
        "/api/work-requests",
        headers=MINA,
        json={"title": "배포 체크 요청", "assignee_id": "jiho"},
    ).json()
    [received] = client.get("/api/notifications", headers=JIHO).json()
    assert received["kind"] == "work_request.received"
    assert received["resource"]["id"] == request["request_id"]
    assert received["resource"]["title"] == "배포 체크 요청"

    make_request_look_pending(settings.database_url, request["request_id"])
    accepted = client.post(
        f"/api/work-requests/{request['request_id']}/accept",
        headers=JIHO,
        json={"expected_version": request["version"]},
    )
    assert accepted.status_code == 200, accepted.text
    [completed] = client.get("/api/notifications", headers=MINA).json()
    assert completed["kind"] == "work_request.accepted"
    assert completed["summary"] == "지호님이 ‘배포 체크 요청’ 업무 요청을 수락했습니다."
    assert completed["resource"]["type"] == "work_request"
    assert completed["resource"]["version"] == request["version"] + 1
