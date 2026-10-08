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
    # 실시간 1건은 그 회원의 알림 한 줄도 같은 트랜잭션에서 낸다(SPEC-011 X06 · WORK-013 WP2-BE).
    assert kinds == ["inbox.message_arrived", "integration.changed", "notification.upserted"]

    store.update_room(room["room_id"], backfill_cursor="cursor-2")
    [(_, changed)] = _drain(listener)
    assert changed["type"] == "integration.changed" and changed["integration_id"] == integration
    store.update_integration(integration, backfill_cursor="page-2")
    assert [payload["type"] for _, payload in _drain(listener)] == ["integration.changed"]
    store.update_integration(integration, watch_expires_at=None)  # 화면 숫자와 무관한 칸 — 사건 없음
    assert _drain(listener) == []
    listener.close()


# ── 알림 사건 — 사건 채널 SSE (SPEC-011 §4.1 · WORK-013 WP1-BE) ─────────────────────────────────


JIHO = {"X-Demo-Persona": "jiho"}


def _psycopg_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def test_a_notification_is_announced_with_the_short_payload_only_after_its_transaction_commits() -> None:
    """알림 NOTIFY 는 `{v, type, member_id, notification_id, seq, created}` 만 — 같은 트랜잭션 · 커밋 뒤(§4.1-4).

    API 밖의 프로세스(meeting_worker 등)도 같은 길이다: DB 세션 하나와 그 세션의 생성기면 된다. 워커가 쓰는
    `make_session_factory` 세션으로 직접 쓰고, 롤백한 것은 아무것도 내지 않는 것을 함께 본다.
    """
    from datetime import UTC, datetime

    from ax_workspace.modules.notification_events import NotificationEvent, Recipient
    from ax_workspace.platform.notifications import notification_generator
    from ax_workspace.platform.persistence import NotificationRecord

    database_url = _postgres_test_url()
    reset_database(database_url)
    client = TestClient(create_app(Settings(RuntimeProfile.TEST, database_url, materials_dir=tempfile.mkdtemp())))
    listener = psycopg.connect(_psycopg_url(database_url), autocommit=True)
    listener.execute("LISTEN ax_user_events")

    request = client.post("/api/work-requests", headers=MINA, json={"title": "알림 사건", "assignee_id": "jiho"}).json()
    [(_, announced)] = [item for item in _drain(listener) if item[1]["type"] == "notification.upserted"]
    sessions = make_session_factory(database_url)
    with sessions() as session:
        row = session.query(NotificationRecord).filter_by(recipient_member_id="jiho").one()
        assert row.resource_id == request["request_id"] and row.seq is not None and row.updated_at is not None
    assert announced == {"v": 1, "type": "notification.upserted", "member_id": "jiho", "notification_id": str(row.id), "seq": row.seq, "created": True}

    # 워커와 같은 세션 공장 — 커밋해야 나가고, 순번은 전역 시퀀스에서 커진다
    def emit(session, source_id: str) -> NotificationRecord:
        [made] = notification_generator(session).notify(
            NotificationEvent(
                row="M09", kind="meeting.minutes_ready", source_kind="test_event", source_id=source_id,
                recipients=(Recipient("jiho", "owner"),), subject={"type": "work_request", "id": request["request_id"], "title": "알림 사건"},
                data={}, target={"surface": "work", "work_request_id": request["request_id"]},
            )
        )
        return made

    with sessions() as session:
        emit(session, "rolled-back")
        session.rollback()
    assert _drain(listener) == []
    with sessions() as session:
        made = emit(session, "committed")
        assert _drain(listener) == []  # 커밋 전에는 아무것도 없다
        session.commit()
        later_seq, later_id = made.seq, str(made.id)
    [(_, worker_event)] = _drain(listener)
    assert worker_event["notification_id"] == later_id and worker_event["seq"] == later_seq > row.seq
    listener.close()


def test_the_event_stream_receives_a_notification_over_listen_and_replays_after_a_reconnect() -> None:
    """살아 있는 PostgreSQL 에서 — 허브의 LISTEN 이 API 가 아닌 세션의 NOTIFY 를 받아 SSE 로 보내고, 다시 붙으면 이어 받는다."""
    import asyncio

    from ax_workspace.modules.external_channels.events import UserEvent, UserEventType
    from sse_stream_support import SseConnection

    database_url = _postgres_test_url()
    reset_database(database_url)
    app = create_app(Settings(RuntimeProfile.TEST, database_url, materials_dir=tempfile.mkdtemp()))
    client = TestClient(app)
    poke = psycopg.connect(_psycopg_url(database_url), autocommit=True)

    async def listening(stream: SseConnection) -> None:
        """허브의 듣는 스레드가 LISTEN 을 걸 때까지 — 표시 사건을 되풀이해 보내 하나가 닿으면 시작이다."""
        probe = UserEvent(UserEventType.INTEGRATION_CHANGED, "jiho", integration_id="probe").to_payload()
        for _ in range(50):
            poke.execute("SELECT pg_notify('ax_user_events', %s)", (probe,))
            try:
                frame = await stream.frame(timeout=0.2)
            except TimeoutError:
                continue
            if frame.get("event") == "integration.changed":
                return
        raise AssertionError("the hub never started listening")

    async def scenario() -> None:
        async with SseConnection(app, "/api/events/stream", JIHO) as stream:
            assert await stream.frame() == {"retry": "3000"}
            assert (await stream.frame())["event"] == "ready"
            await listening(stream)
            await asyncio.to_thread(client.post, "/api/work-requests", headers=MINA, json={"title": "첫째", "assignee_id": "jiho"})
            frame = await stream.frames_until("notification.upserted")
            first = frame[-1]
            assert first["data"]["created"] is True and first["data"]["notification"]["seq"] == first["id"]
        await asyncio.to_thread(client.post, "/api/work-requests", headers=MINA, json={"title": "끊긴 동안", "assignee_id": "jiho"})
        async with SseConnection(app, "/api/events/stream", {**JIHO, "Last-Event-ID": str(first["id"])}) as again:
            assert await again.frame() == {"retry": "3000"}
            assert (await again.frame())["event"] == "ready"
            frames = await again.frames_until("resync")
        titles = [item["data"]["notification"]["subject"]["title"] for item in frames if item.get("event") == "notification.upserted"]
        assert titles == ["첫째", "끊긴 동안"] and frames[-1]["data"]["reason"] == "reconnected"

    asyncio.run(scenario())
    poke.close()
    app.state.workflow_application.user_event_hub.close()


def test_the_v2_sql_numbers_old_rows_and_sync_adds_a_missing_sequence() -> None:
    """운영 SQL(`2026-10-08-notifications-v2.sql` + 인덱스 판)이 옛 행에 순번을 주고, 시퀀스가 없는 demo DB 는 sync 가 더한다."""
    from pathlib import Path

    from ax_workspace.bootstrap import schema_sync

    database_url = _postgres_test_url()
    reset_database(database_url)
    client = TestClient(create_app(Settings(RuntimeProfile.TEST, database_url, materials_dir=tempfile.mkdtemp())))
    for title in ("옛 행 1", "옛 행 2"):
        client.post("/api/work-requests", headers=MINA, json={"title": title, "assignee_id": "jiho"})
    manual = Path(__file__).resolve().parents[3] / "migrations" / "manual"
    with psycopg.connect(_psycopg_url(database_url), autocommit=True) as connection:
        # 옛 모양 — 칸 · 시퀀스 · 인덱스 · 설정 표 · from_me 가 없던 때(옛 종류 값)
        connection.execute("DROP INDEX ix_notifications_recipient_seq")
        connection.execute(
            "ALTER TABLE notifications DROP COLUMN seq, DROP COLUMN updated_at, DROP COLUMN theme, DROP COLUMN item, "
            "DROP COLUMN relation, DROP COLUMN failure, DROP COLUMN actor, DROP COLUMN data, DROP COLUMN target, DROP COLUMN coalesce_key"
        )
        connection.execute("UPDATE notifications SET kind = 'work_request.received'")
        connection.execute("DROP TABLE notification_settings")
        connection.execute("ALTER TABLE external_messages DROP COLUMN from_me")
        connection.execute("DROP SEQUENCE notification_seq")
        planned = schema_sync.plan(database_url)["statements"]
        assert "CREATE SEQUENCE notification_seq;" in planned
        assert any("ADD COLUMN seq BIGINT" in statement for statement in planned)
        with connection.transaction():
            connection.execute((manual / "2026-10-08-notifications-v2.sql").read_text())
            connection.execute((manual / "2026-10-08-notifications-v2-index.sql").read_text())
        with connection.transaction():  # 다시 돌려도 같다(IF NOT EXISTS · 빈 행만)
            connection.execute((manual / "2026-10-08-notifications-v2.sql").read_text())
        rows = connection.execute("SELECT seq, updated_at = created_at FROM notifications ORDER BY created_at, id").fetchall()
        assert [seq for seq, _ in rows] == [1, 2] and all(same for _, same in rows)
        kinds = connection.execute("SELECT DISTINCT kind, theme, item, relation, data::text FROM notifications").fetchall()
        assert kinds == [("work.request_received", "work", "request", "assignee", '{"resubmitted": false}')]
        assert connection.execute("SELECT to_regclass('notification_settings')").fetchone()[0] == "notification_settings"
        assert connection.execute("SELECT nextval('notification_seq')").fetchone()[0] == 3
        assert connection.execute("SELECT to_regclass('ix_notifications_recipient_seq')").fetchone()[0] == "ix_notifications_recipient_seq"
    assert schema_sync.plan(database_url)["statements"] == []


def test_the_from_me_backfill_counts_first_and_applies_only_on_the_second_step() -> None:
    """백필 SQL 두 걸음(SPEC-011 §4.7 · 검수 W-3) — 셈은 아무것도 바꾸지 않고, 적용은 `from_me` 가 빈 줄만. 이름은 인자(가상)."""
    import shutil
    import subprocess
    import uuid
    from datetime import UTC, datetime
    from pathlib import Path

    psql = shutil.which("psql")
    if psql is None:
        pytest.skip("psql 이 이 기계에 없다")
    database_url = _postgres_test_url()
    reset_database(database_url)
    url = _psycopg_url(database_url)
    now = datetime.now(UTC)
    kakao, slack, mail = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(url, autocommit=True) as connection:
        for row_id, kind, key, meta in (
            (kakao, "kakao", "kakao", "{}"), (slack, "slack", "T1", '{"user_id": "U-ME"}'), (mail, "mail", "me@company.example", "{}"),
        ):
            connection.execute(
                "INSERT INTO external_integrations (id, member_id, kind, status, account_key, display_name, account_meta, scopes, "
                "backfill_count, synced_count, selected_rooms_version, created_at, updated_at) "
                "VALUES (%s, 'mina', %s, 'connected', %s, '', %s::json, '', 0, 0, 0, %s, %s)",
                (row_id, kind, key, meta, now, now),
            )
        lines = [
            (kakao, "kakao", "가상본인", {"text": "a"}), (kakao, "kakao", "가상동료", {"text": "b"}), (kakao, "kakao", None, {"text": "c"}),
            (slack, "slack", "표시 이름", {"user": "U-ME"}), (slack, "slack", "동료", {"user": "U-OTHER"}),
            (mail, "mail", "x", {"payload": {"headers": [{"name": "From", "value": "Me <ME@company.example>"}]}}),
            (mail, "mail", "y", {"payload": {"headers": [{"name": "From", "value": "p@example.com"}]}}),
        ]
        for index, (integration, kind, author, raw) in enumerate(lines):
            connection.execute(
                "INSERT INTO external_messages (id, integration_id, source_kind, container_key, external_key, sent_at, author, raw, created_at) "
                "VALUES (%s, %s, %s, '', %s, %s, %s, %s::json, %s)",
                (uuid.uuid4(), integration, kind, f"k{index}", now, author, json.dumps(raw), now),
            )
    script = Path(__file__).resolve().parents[3] / "migrations" / "manual" / "2026-10-08-notification-from-me-backfill.sql"

    def run(apply: int) -> str:
        done = subprocess.run(
            [psql, url, "-v", "ON_ERROR_STOP=1", "-v", f"integration_id={kakao}", "-v", "self_name=가상본인", "-v", f"apply={apply}", "-f", str(script)],
            capture_output=True, text=True, check=True,
        )
        return done.stdout

    def flags() -> dict[str, bool | None]:
        with psycopg.connect(url) as connection:
            return dict(connection.execute("SELECT external_key, from_me FROM external_messages ORDER BY external_key").fetchall())

    counted = run(0)
    assert "셈만 했습니다" in counted and set(flags().values()) == {None}
    run(1)
    assert flags() == {"k0": True, "k1": False, "k2": False, "k3": True, "k4": False, "k5": True, "k6": False}
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute("UPDATE external_messages SET from_me = NULL WHERE external_key = 'k1'")
        connection.execute("UPDATE external_messages SET from_me = false WHERE external_key = 'k0'")  # 이미 채운 줄은 건드리지 않는다
    run(1)
    assert flags()["k1"] is False and flags()["k0"] is False
