"""외부 채널 사건이 **살아 있는 PostgreSQL 에서** `LISTEN/NOTIFY` 로 오는가 (BE-1 검수 W-9 ① · SPEC-008 §4.4).

sqlite 에서는 `user_events.publish` 가 아무것도 안 하므로 contract 시험이 이 경로를 재지 못한다. 여기서는 연결 콜백 ·
방 추가 · 수집 저장이 낸 사건이 **커밋 뒤에** 두 채널(`ax_user_events`·`ax_external_sync`)로 도착하는지 센다.
빈 격리 데이터베이스에서만 돈다(`AX_POSTGRES_TEST_URL`).
"""
from __future__ import annotations

import json
import tempfile

from fastapi.testclient import TestClient
import psycopg
import pytest

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.external_channels.application import OAuthGrant, SlackRoomInfo
from ax_workspace.modules.external_channels.sync import ExternalSync
from ax_workspace.platform.external_channels_sync_store import SqlAlchemyExternalChannelsSyncStore
from ax_workspace.platform.external_tokens import FernetTokenCipher, generate_key
from ax_workspace.platform.persistence import make_session_factory

from slack_directory_support import FakeSlackDirectory
from test_postgres_integration import _postgres_test_url

pytestmark = pytest.mark.integration
MINA = {"X-Demo-Persona": "mina"}


class Provider:
    def authorize_url(self, *, state, redirect_uri):
        return "https://consent.example/?state=" + state

    def exchange(self, *, code, redirect_uri):
        return OAuthGrant("T0001", "Medi", {"team_id": "T0001"}, "xoxp-pg", None, None, "")


def _drain(connection) -> list[tuple[str, dict]]:
    return [(note.channel, json.loads(note.payload)) for note in connection.notifies(timeout=1.0, stop_after=50)]


def test_integration_and_message_events_arrive_over_listen_notify_after_commit() -> None:
    database_url = _postgres_test_url()
    reset_database(database_url)
    key = generate_key()
    app = create_app(Settings(RuntimeProfile.TEST, database_url, materials_dir=tempfile.mkdtemp(), external_token_encryption_key=key))
    app.state.workflow_application._external_oauth = {"slack": Provider()}
    app.state.workflow_application._slack_directory = FakeSlackDirectory(default={"C1": "channel"})
    client = TestClient(app, follow_redirects=False)
    listener = psycopg.connect(database_url.replace("postgresql+psycopg://", "postgresql://", 1), autocommit=True)
    listener.execute("LISTEN ax_user_events")
    listener.execute("LISTEN ax_external_sync")

    state = client.post("/api/integrations/slack/connect", headers=MINA).json()["state"]
    assert client.get("/api/integrations/slack/callback", params={"code": "c", "state": state}).status_code == 302
    integration = client.get("/api/integrations", headers=MINA).json()[0]["id"]
    assert client.post(f"/api/integrations/{integration}/rooms", headers=MINA, json={"room_ids": ["C1"]}).status_code == 202
    arrived = _drain(listener)
    assert ("ax_external_sync", {"v": 1, "integration_id": integration, "reason": "connected"}) in arrived
    assert ("ax_external_sync", {"v": 1, "integration_id": integration, "reason": "rooms"}) in arrived
    changed = [payload for channel, payload in arrived if channel == "ax_user_events"]
    assert changed and all(p["type"] == "integration.changed" and p["member_id"] == "mina" for p in changed)

    # 수집기가 저장하면 같은 트랜잭션의 NOTIFY 로 새 메시지가 알려진다.
    store = SqlAlchemyExternalChannelsSyncStore(make_session_factory(database_url))
    sync = ExternalSync(store, cipher=FernetTokenCipher(key), slack=None, gmail=None, gmail_topic=None)
    event = {"team_id": "T0001", "event": {"type": "message", "channel": "C1", "user": "U1", "text": "hi", "ts": "1790000001.000100"}}
    assert sync.handle_slack_event(event) == 1
    [(channel, payload)] = [item for item in _drain(listener) if item[1].get("type") == "inbox.message_arrived"]
    assert channel == "ax_user_events" and payload["member_id"] == "mina" and payload["source_kind"] == "slack"
    listener.close()


def test_a_single_live_save_and_a_cursor_only_step_announce_the_integration_change() -> None:
    """설정 연동 화면의 숫자가 바뀌는 저장마다 `integration.changed` (SPEC-008 §4.4 v0.6.0 · D-25 · WORK-012 WP1-BE).

    실시간 1건 저장은 `inbox.message_arrived` 와 함께 연동 변경 한 건을 낸다(옛 판은 21건 이상일 때만). 백필의 커서만
    옮긴 걸음도 상태가 그대로여도 낸다. 묶어 내기(1초에 한 번)는 듣는 쪽(`UserEventHub`) 몫이라 NOTIFY 는 저장마다 나간다.
    """
    database_url = _postgres_test_url()
    reset_database(database_url)
    key = generate_key()
    app = create_app(Settings(RuntimeProfile.TEST, database_url, materials_dir=tempfile.mkdtemp(), external_token_encryption_key=key))
    app.state.workflow_application._external_oauth = {"slack": Provider()}
    app.state.workflow_application._slack_directory = FakeSlackDirectory(default={"C1": "channel"})
    client = TestClient(app, follow_redirects=False)
    state = client.post("/api/integrations/slack/connect", headers=MINA).json()["state"]
    assert client.get("/api/integrations/slack/callback", params={"code": "c", "state": state}).status_code == 302
    integration = client.get("/api/integrations", headers=MINA).json()[0]["id"]
    assert client.post(f"/api/integrations/{integration}/rooms", headers=MINA, json={"room_ids": ["C1"]}).status_code == 202
    [room] = client.get(f"/api/integrations/{integration}/rooms", headers=MINA).json()

    listener = psycopg.connect(database_url.replace("postgresql+psycopg://", "postgresql://", 1), autocommit=True)
    listener.execute("LISTEN ax_user_events")
    store = SqlAlchemyExternalChannelsSyncStore(make_session_factory(database_url))
    sync = ExternalSync(store, cipher=FernetTokenCipher(key), slack=None, gmail=None, gmail_topic=None)
    event = {"team_id": "T0001", "event": {"type": "message", "channel": "C1", "user": "U1", "text": "hi", "ts": "1790000002.000100"}}
    assert sync.handle_slack_event(event) == 1
    kinds = [payload["type"] for _, payload in _drain(listener)]
    assert kinds == ["inbox.message_arrived", "integration.changed"]

    store.update_room(room["room_id"], backfill_cursor="cursor-2")
    [(_, changed)] = _drain(listener)
    assert changed["type"] == "integration.changed" and changed["integration_id"] == integration
    store.update_integration(integration, backfill_cursor="page-2")
    assert [payload["type"] for _, payload in _drain(listener)] == ["integration.changed"]
    store.update_integration(integration, watch_expires_at=None)  # 화면 숫자와 무관한 칸 — 사건 없음
    assert _drain(listener) == []
    listener.close()
