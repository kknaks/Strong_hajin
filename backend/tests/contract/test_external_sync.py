"""연동 수집의 계약 — 사람별 팬아웃·중복 버림·백필·재시작 메우기·Gmail history·끊김·개발 토큰 이음새 (WORK-011 BE-2)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from uuid import UUID

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from ax_workspace.bootstrap.external_worker import DevelopmentSeamForbidden, connect_dev_slack
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.external_channels.application import OAuthGrant, SlackRoomInfo
from ax_workspace.modules.external_channels.sync import ExternalSync, HistoryExpired, UpstreamAuthRevoked, UpstreamRoomDenied
from ax_workspace.platform.external_channels_sync_store import SqlAlchemyExternalChannelsSyncStore
from ax_workspace.platform.external_tokens import FernetTokenCipher, generate_key
from slack_directory_support import FakeSlackDirectory
from ax_workspace.platform.persistence import (
    ExternalAttachmentRecord,
    ExternalIntegrationRecord,
    ExternalMessageRecord,
    ExternalRoomRecord,
    make_session_factory,
)

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
TEAM = "T0001"


def ts(n: int) -> str:
    return f"1790000{n:03d}.000100"


class FakeSlack:
    def __init__(self) -> None:
        self.pages: dict[str, list[tuple[list[dict], str | None]]] = {}
        self.since: dict[str, list[dict]] = {}
        self.threads: dict[str, list[dict]] = {}
        self.info: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.revoked = False
        self.denied: dict[str, set[str]] = {}

    def history(self, token, channel, *, cursor=None, oldest=None, limit=200):
        if self.revoked:
            raise UpstreamAuthRevoked("token_revoked")
        assert token.startswith("xoxp-")
        self.calls.append(("history", channel, cursor, oldest))
        if oldest is not None:
            return [m for m in self.since.get(channel, []) if float(m["ts"]) > float(oldest)], None
        pages = self.pages.get(channel, [([], None)])
        index = 0 if cursor is None else int(cursor)
        return pages[index]

    def replies(self, token, channel, thread_ts, *, cursor=None, oldest=None):
        self.calls.append(("replies", channel, thread_ts, oldest))
        replies = self.threads.get(thread_ts, [])
        return ([r for r in replies if float(r["ts"]) > float(oldest)] if oldest else replies), None

    def conversation_info(self, token, channel):
        if channel in self.denied.get(token, set()):
            raise UpstreamRoomDenied("channel_not_found")
        return self.info.get(channel, {"id": channel, "name": channel, "is_channel": True, "is_member": True})

    def conversation_members(self, token, channel):
        return ["U1", "U2"]

    def user_name(self, token, user_id):
        return {"U1": "김민아", "U2": "박지호"}.get(user_id)

    def user_tag(self, token, user_id):
        name = self.user_name(token, user_id)
        return {"name": name, "is_bot": False, "avatar": f"https://avatars.example/{user_id}.png"} if name else None

    def identity(self, token):
        return {"ok": True, "url": "https://medi.slack.com/", "team_id": TEAM}


class FakeGmail:
    def __init__(self) -> None:
        self.inbox_pages: list[list[str]] = [["m1", "m2"], ["m3"]]
        self.messages = {f"m{i}": self._message(f"m{i}") for i in range(1, 10)}
        self.history_ids: list[str] = []
        self.history_expired = False
        self.watched: list[str] = []
        self.refreshed = 0
        self.revoked = False

    @staticmethod
    def _message(message_id: str, labels=("INBOX",)) -> dict:
        return {
            "id": message_id, "threadId": "t-" + message_id, "labelIds": list(labels), "snippet": "안녕 &amp; 반가워",
            "internalDate": "1790000000000",
            "payload": {"headers": [{"name": "Subject", "value": f"제목 {message_id}"}, {"name": "From", "value": "a@b.example"}],
                        "parts": [{"partId": "1", "filename": "report.pdf", "mimeType": "application/pdf",
                                   "body": {"attachmentId": "ATT-" + message_id, "size": 1234}}]},
        }

    def refresh(self, refresh_token):
        if self.revoked:
            raise UpstreamAuthRevoked("invalid_grant")
        self.refreshed += 1
        return "fresh-access", datetime.now(UTC) + timedelta(hours=1)

    def profile(self, token):
        return {"emailAddress": "mina@corp.example", "historyId": "100"}

    def watch(self, token, topic):
        self.watched.append(topic)
        return "100", datetime.now(UTC) + timedelta(days=7)

    def list_inbox(self, token, *, page_token=None, query=None):
        if query:
            return ["m9"], None
        index = int(page_token or 0)
        return self.inbox_pages[index], (str(index + 1) if index + 1 < len(self.inbox_pages) else None)

    def get_message(self, token, message_id):
        return self.messages.get(message_id)

    def history(self, token, start_history_id, *, page_token=None):
        if self.history_expired:
            raise HistoryExpired(start_history_id)
        return list(self.history_ids), None, "200"


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    key = generate_key()
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"),
                        web_origin="http://127.0.0.1:5176", api_origin="http://127.0.0.1:8001", external_token_encryption_key=key)
    app = create_app(settings)
    # 슬랙 방 접근 확인(F-1)의 대역 — 기본은 누구에게나 보이는 방. 시험이 토큰별 목록을 덮어쓴다.
    app.state.workflow_application._slack_directory = FakeSlackDirectory(default={
        "C1": "channel", "C2": "channel", "G1": "group_dm", "D1": "dm",
    })
    client = TestClient(app, follow_redirects=False)
    sessions = make_session_factory(database_url)
    store = SqlAlchemyExternalChannelsSyncStore(sessions)
    return settings, client, app, store, sessions, FernetTokenCipher(key)


def _slack_member(settings, client, member: str, headers, rooms: list[dict]) -> str:
    who = {"team_id": TEAM, "team": "Medi", "user_id": "U-" + member, "_scopes": "channels:history"}
    integration_id = connect_dev_slack(settings, member, f"xoxp-test-{member}", identity=lambda token: who)["integration_id"]
    assert client.post(f"/api/integrations/{integration_id}/rooms", headers=headers,
                       json={"room_ids": [r["room_id"] for r in rooms], "rooms": rooms}).status_code == 202
    return integration_id


def _messages(sessions, integration_id: str) -> list[ExternalMessageRecord]:
    with sessions() as session:
        return list(session.scalars(select(ExternalMessageRecord).where(
            ExternalMessageRecord.integration_id == UUID(integration_id)).order_by(ExternalMessageRecord.sent_at)))


def _event(channel: str, n: int, **extra) -> dict:
    return {"team_id": TEAM, "event": {"type": "message", "channel": channel, "user": "U1", "text": f"메시지 {n}",
                                       "ts": ts(n), "event_ts": ts(n), "channel_type": "channel", **extra}}


# ── 슬랙 ───────────────────────────────────────────────────────────────────────────────


def test_one_slack_event_is_stored_once_per_member_who_picked_that_room(tmp_path) -> None:
    settings, client, _, store, sessions, cipher = _stack(tmp_path)
    mina = _slack_member(settings, client, "mina", MINA, [{"room_id": "C1", "type": "channel"}, {"room_id": "C2", "type": "channel"}])
    jiho = _slack_member(settings, client, "jiho", JIHO, [{"room_id": "C1", "type": "channel"}])
    sync = ExternalSync(store, cipher=cipher, slack=FakeSlack(), gmail=None, gmail_topic=None)

    assert sync.handle_slack_event(_event("C1", 1)) == 2  # 사람마다 한 벌(W-8 · D-25)
    assert sync.handle_slack_event(_event("C1", 1)) == 0  # 재전달은 중복 키 (연동, channel, ts) 로 버린다
    assert sync.handle_slack_event(_event("C2", 2)) == 1  # C2 는 mina 만 골랐다
    assert sync.handle_slack_event({**_event("C1", 3), "team_id": "T-OTHER"}) == 0  # 다른 워크스페이스
    assert sync.handle_slack_event(_event("C9", 4)) == 0  # 아무도 안 고른 방(AC-04)
    assert [m.external_key for m in _messages(sessions, mina)] == [ts(1), ts(2)]
    [copy] = _messages(sessions, jiho)
    assert copy.raw["text"] == "메시지 1" and copy.container_key == "C1" and copy.source_kind == "slack"
    assert "channel_type" not in copy.raw
    with sessions() as session:
        room = session.scalar(select(ExternalRoomRecord).where(ExternalRoomRecord.integration_id == UUID(jiho)))
        assert room.last_message_key == ts(1) and room.synced_count == 1
        assert session.get(ExternalIntegrationRecord, UUID(mina)).synced_count == 2


def test_thread_replies_files_edits_and_deletes_keep_the_original_rows(tmp_path) -> None:
    settings, client, _, store, sessions, cipher = _stack(tmp_path)
    mina = _slack_member(settings, client, "mina", MINA, [{"room_id": "C1", "type": "channel"}])
    sync = ExternalSync(store, cipher=cipher, slack=FakeSlack(), gmail=None, gmail_topic=None)
    sync.handle_slack_event(_event("C1", 1, files=[{"id": "F1", "name": "a.png", "mimetype": "image/png", "size": 10}]))
    sync.handle_slack_event(_event("C1", 2, thread_ts=ts(1)))
    edited = {"type": "message", "user": "U1", "text": "고친 글", "ts": ts(1), "edited": {"ts": ts(5)}}
    sync.handle_slack_event({"team_id": TEAM, "event": {"type": "message", "subtype": "message_changed", "channel": "C1",
                                                        "message": edited, "ts": ts(5)}})
    sync.handle_slack_event({"team_id": TEAM, "event": {"type": "message", "subtype": "message_deleted", "channel": "C1",
                                                        "deleted_ts": ts(2), "ts": ts(6)}})
    first, reply = _messages(sessions, mina)
    assert first.raw["text"] == "고친 글" and first.preview == "고친 글"
    assert reply.thread_key == ts(1) and reply.raw["ax_deleted"] is True  # 지우지 않는다 — 표지만
    with sessions() as session:
        [attachment] = session.scalars(select(ExternalAttachmentRecord)).all()
        assert attachment.kind == "image" and attachment.state == "reference" and attachment.external_ref == "F1"
        assert len(attachment.aid) == 64


def test_room_backfill_walks_every_page_with_threads_then_goes_live(tmp_path) -> None:
    settings, client, _, store, sessions, cipher = _stack(tmp_path)
    mina = _slack_member(settings, client, "mina", MINA, [{"room_id": "G1", "type": "channel"}, {"room_id": "D1", "type": "dm"}])
    slack = FakeSlack()
    slack.info = {"G1": {"id": "G1", "name": "mpdm-mina--jiho-1", "is_mpim": True, "num_members": 2},
                  "D1": {"id": "D1", "is_im": True, "user": "U2"}}
    slack.pages["G1"] = [
        ([{"ts": ts(3), "text": "c", "user": "U1"}, {"ts": ts(2), "text": "b", "thread_ts": ts(2), "reply_count": 1}], "1"),
        ([{"ts": ts(1), "text": "a", "user": "U2"}], None),
    ]
    slack.threads[ts(2)] = [{"ts": ts(2), "text": "b"}, {"ts": ts(4), "text": "답글", "thread_ts": ts(2)}]
    sync = ExternalSync(store, cipher=cipher, slack=slack, gmail=None, gmail_topic=None)

    assert sync.tick() is True  # 첫 쪽 — 아직 남았다
    rooms = {r["external_id"]: r for r in client.get(f"/api/integrations/{mina}/rooms", headers=MINA).json()}
    assert rooms["G1"]["status"] == "backfilling" and rooms["G1"]["name"] == "김민아, 박지호"  # 그룹 DM 실명(D-12)
    assert rooms["G1"]["type"] == "group_dm" and rooms["D1"]["name"] == "박지호"
    assert sync.tick() is False
    rooms = {r["external_id"]: r for r in client.get(f"/api/integrations/{mina}/rooms", headers=MINA).json()}
    assert rooms["G1"]["status"] == "live" and rooms["G1"]["synced_count"] == 4
    assert sorted(m.external_key for m in _messages(sessions, mina)) == [ts(1), ts(2), ts(3), ts(4)]
    with sessions() as session:
        room = session.scalar(select(ExternalRoomRecord).where(ExternalRoomRecord.external_id == "G1"))
        assert room.backfill_count == 4 and room.last_message_key == ts(4) and room.backfill_done_at is not None
        integration = session.get(ExternalIntegrationRecord, UUID(mina))
        assert integration.status == "connected"


def test_restart_fills_only_the_gap_after_the_last_key(tmp_path) -> None:
    settings, client, _, store, sessions, cipher = _stack(tmp_path)
    mina = _slack_member(settings, client, "mina", MINA, [{"room_id": "C1", "type": "channel"}])
    slack = FakeSlack()
    slack.pages["C1"] = [([{"ts": ts(1), "text": "a"}, {"ts": ts(2), "text": "b"}], None)]
    ExternalSync(store, cipher=cipher, slack=slack, gmail=None, gmail_topic=None).tick()
    # 워커가 내려가 있던 사이에 둘이 더 왔다.
    slack.since["C1"] = [{"ts": ts(2), "text": "b"}, {"ts": ts(3), "text": "c"}, {"ts": ts(4), "text": "d"}]
    restarted = ExternalSync(store, cipher=cipher, slack=slack, gmail=None, gmail_topic=None)
    restarted.tick()
    assert ("history", "C1", None, ts(2)) in slack.calls  # 마지막 반영 지점 이후만
    assert [m.external_key for m in _messages(sessions, mina)] == [ts(1), ts(2), ts(3), ts(4)]
    calls = len(slack.calls)
    restarted.tick()
    assert len(slack.calls) == calls  # 메우기는 한 번 — 그 뒤는 실시간 이벤트가 받는다


def test_a_revoked_slack_token_disconnects_the_integration(tmp_path) -> None:
    settings, client, _, store, sessions, cipher = _stack(tmp_path)
    mina = _slack_member(settings, client, "mina", MINA, [{"room_id": "C1", "type": "channel"}])
    slack = FakeSlack()
    slack.revoked = True
    ExternalSync(store, cipher=cipher, slack=slack, gmail=None, gmail_topic=None).tick()
    [row] = client.get("/api/integrations", headers=MINA).json()
    assert row["id"] == mina and row["status"] == "disconnected"
    with sessions() as session:
        assert session.get(ExternalIntegrationRecord, UUID(mina)).disconnected_reason == "token_revoked"


# ── Gmail ──────────────────────────────────────────────────────────────────────────────


def _gmail_member(client, app, *, expires_at=None) -> str:
    class Provider:
        def authorize_url(self, *, state, redirect_uri):
            return "https://consent.example/?state=" + state

        def exchange(self, *, code, redirect_uri):
            return OAuthGrant("mina@corp.example", "mina@corp.example", {}, "old-access", "refresh-1", expires_at, "")

    app.state.workflow_application._external_oauth = {"mail": Provider()}
    state = client.post("/api/integrations/mail/connect", headers=MINA).json()["state"]
    assert client.get("/api/integrations/mail/callback", params={"code": "c", "state": state}).status_code == 302
    return client.get("/api/integrations", headers=MINA).json()[0]["id"]


def test_gmail_takes_the_history_id_first_backfills_the_inbox_then_follows_history(tmp_path) -> None:
    settings, client, app, store, sessions, cipher = _stack(tmp_path)
    mail = _gmail_member(client, app, expires_at=datetime.now(UTC) + timedelta(hours=1))
    gmail = FakeGmail()
    gmail.messages["m2"] = FakeGmail._message("m2", labels=("SENT",))  # 받은편지함이 아니면 버린다(D-10)
    sync = ExternalSync(store, cipher=cipher, slack=None, gmail=gmail, gmail_topic="projects/p/topics/gmail")

    assert sync.tick() is True
    with sessions() as session:
        row = session.get(ExternalIntegrationRecord, UUID(mail))
        # 백필 전에 잡은 100 에서 history 를 한 번 훑어 200 까지 왔다 — 백필 중 도착분을 놓치지 않는다.
        assert row.sync_cursor == "200" and row.backfill_cursor == "1" and row.status == "backfilling"
        assert row.watch_expires_at is not None
    assert gmail.watched == ["projects/p/topics/gmail"]
    assert sync.tick() is False
    [row] = client.get("/api/integrations", headers=MINA).json()
    assert row["status"] == "connected" and row["backfill_count"] == 2 and row["synced_count"] == 2
    first = _messages(sessions, mail)[0]
    assert first.subject == "제목 m1" and first.author == "a@b.example" and first.preview == "안녕 & 반가워"
    assert first.container_key == "" and first.thread_key == "t-m1" and first.raw["id"] == "m1"

    gmail.history_ids = ["m4", "m1"]  # 새 메일 하나 + 이미 받은 것
    sync.tick(due_mail=sync.mail_due("MINA@corp.example"))
    assert sorted(m.external_key for m in _messages(sessions, mail)) == ["m1", "m3", "m4"]
    with sessions() as session:
        assert session.get(ExternalIntegrationRecord, UUID(mail)).sync_cursor == "200"
    assert gmail.watched == ["projects/p/topics/gmail"]  # 갱신은 하루 한 번 꼴 — 매 바퀴가 아니다


def test_gmail_expired_history_falls_back_to_dates_and_expired_tokens_refresh(tmp_path) -> None:
    settings, client, app, store, sessions, cipher = _stack(tmp_path)
    mail = _gmail_member(client, app, expires_at=datetime.now(UTC) - timedelta(minutes=1))
    gmail = FakeGmail()
    gmail.inbox_pages = [[]]
    gmail.history_expired = True
    ExternalSync(store, cipher=cipher, slack=None, gmail=gmail, gmail_topic=None).tick()
    assert gmail.refreshed == 1
    assert [m.external_key for m in _messages(sessions, mail)] == ["m9"]
    with sessions() as session:
        row = session.get(ExternalIntegrationRecord, UUID(mail))
        assert cipher.decrypt(row.access_token_encrypted) == "fresh-access"
        assert "fresh-access" not in row.access_token_encrypted


def test_a_refused_gmail_refresh_disconnects_the_account(tmp_path) -> None:
    settings, client, app, store, sessions, cipher = _stack(tmp_path)
    _gmail_member(client, app, expires_at=datetime.now(UTC) - timedelta(minutes=1))
    gmail = FakeGmail()
    gmail.revoked = True
    ExternalSync(store, cipher=cipher, slack=None, gmail=gmail, gmail_topic=None).tick()
    [row] = client.get("/api/integrations", headers=MINA).json()
    assert row["status"] == "disconnected"


# ── 개발 토큰 이음새 (★3) ──────────────────────────────────────────────────────────────


def test_development_slack_token_seam_is_refused_in_production(tmp_path) -> None:
    production = Settings(RuntimeProfile.PRODUCTION, f"sqlite:///{tmp_path / 'demo.db'}", external_token_encryption_key=generate_key())

    def never(token):
        raise AssertionError("must refuse before calling slack")

    with pytest.raises(DevelopmentSeamForbidden):
        connect_dev_slack(production, "mina", "xoxp-anything", identity=never)


def test_development_slack_token_seam_stores_an_encrypted_connected_integration(tmp_path) -> None:
    settings, client, _, _, sessions, cipher = _stack(tmp_path)
    who = {"team_id": TEAM, "team": "Medi", "user_id": "U1", "_scopes": "channels:history,files:write"}
    first = connect_dev_slack(settings, "mina", "xoxp-dev-token\n", identity=lambda token: who)
    again = connect_dev_slack(settings, "mina", "xoxp-dev-token", identity=lambda token: who)
    assert first["integration_id"] == again["integration_id"]
    [row] = client.get("/api/integrations", headers=MINA).json()
    assert row["kind"] == "slack" and row["status"] == "connected" and row["display_name"] == "Medi"
    with sessions() as session:
        record = session.get(ExternalIntegrationRecord, UUID(first["integration_id"]))
        assert cipher.decrypt(record.access_token_encrypted) == "xoxp-dev-token"
    with pytest.raises(ValueError):
        connect_dev_slack(settings, "mina", "not-a-user-token", identity=lambda token: who)
    with pytest.raises(LookupError):
        connect_dev_slack(settings, "nobody", "xoxp-dev-token", identity=lambda token: who)


# ── F-1 — 남의 슬랙 방은 고를 수도, 팬아웃 받을 수도 없다 (BE-2·3 검수) ───────────────────────────


def test_a_member_cannot_pick_someone_elses_dm_and_never_receives_its_events(tmp_path) -> None:
    settings, client, app, store, sessions, cipher = _stack(tmp_path)
    directory = app.state.workflow_application._slack_directory
    directory.default = {}
    directory.rooms = {
        "xoxp-test-mina": [SlackRoomInfo("D-MINA", "dm", "박지호")],
        "xoxp-test-jiho": [SlackRoomInfo("C-OPEN", "channel", "공지")],
    }
    mina = _slack_member(settings, client, "mina", MINA, [{"room_id": "D-MINA", "type": "dm"}])
    who = {"team_id": TEAM, "team": "Medi", "user_id": "U-jiho"}
    jiho = connect_dev_slack(settings, "jiho", "xoxp-test-jiho", identity=lambda token: who)["integration_id"]
    # jiho 가 mina 의 DM id 를 안다(링크·스크린샷) — 자기 토큰으로는 그 방이 안 보이니 422 다.
    refused = client.post(f"/api/integrations/{jiho}/rooms", headers=JIHO, json={"room_ids": ["D-MINA"], "rooms": [{"room_id": "D-MINA", "type": "dm"}]})
    assert refused.status_code == 422
    assert client.get(f"/api/integrations/{jiho}/rooms", headers=JIHO).json() == []

    # 확인 없이 들어간 방(이 수정 전 판에 고른 방)이 있어도 팬아웃 대상이 아니다.
    with sessions() as session:
        now = datetime.now(UTC)
        session.add(ExternalRoomRecord(integration_id=UUID(jiho), external_id="D-MINA", room_type="dm", name="x", room_meta={},
                                       status="backfilling", created_at=now, updated_at=now))
        session.commit()
    slack = FakeSlack()
    slack.denied = {"xoxp-test-jiho": {"D-MINA"}}
    sync = ExternalSync(store, cipher=cipher, slack=slack, gmail=None, gmail_topic=None)
    event = {"team_id": TEAM, "event": {"type": "message", "channel": "D-MINA", "user": "U2", "text": "비밀", "ts": ts(1)}}
    assert sync.handle_slack_event(event) == 1  # mina 한 벌뿐
    assert _messages(sessions, jiho) == [] and len(_messages(sessions, mina)) == 1

    # 워커의 재확인이 그 방을 「멈춤」+사유로 내린다(다시 확인될 때까지 팬아웃·수집에서 빠진다).
    sync.tick()
    with sessions() as session:
        stale = session.scalar(select(ExternalRoomRecord).where(ExternalRoomRecord.integration_id == UUID(jiho)))
        assert stale.status == "paused" and stale.room_meta["access_lost"] == "channel_not_found"
    assert sync.handle_slack_event({**event, "event": {**event["event"], "ts": ts(2)}}) == 1
    assert _messages(sessions, jiho) == []


def test_a_room_whose_access_is_lost_pauses_and_recovers_when_access_returns(tmp_path) -> None:
    settings, client, _, store, sessions, cipher = _stack(tmp_path)
    mina = _slack_member(settings, client, "mina", MINA, [{"room_id": "C1", "type": "channel"}])
    slack = FakeSlack()
    slack.info["C1"] = {"id": "C1", "name": "general", "is_channel": True, "is_member": False}  # 채널에서 나갔다
    ExternalSync(store, cipher=cipher, slack=slack, gmail=None, gmail_topic=None).tick()
    [room] = client.get(f"/api/integrations/{mina}/rooms", headers=MINA).json()
    assert room["status"] == "paused"
    sync = ExternalSync(store, cipher=cipher, slack=slack, gmail=None, gmail_topic=None)
    assert sync.handle_slack_event(_event("C1", 1)) == 0
    slack.info["C1"]["is_member"] = True  # 다시 들어왔다 — 다음 실행의 재확인이 되살린다
    sync.tick()
    [room] = client.get(f"/api/integrations/{mina}/rooms", headers=MINA).json()
    assert room["status"] == "live" and room["name"] == "general"
    assert sync.handle_slack_event(_event("C1", 2)) == 1


# ── F-2 — 방 고르기 목록 ───────────────────────────────────────────────────────────────────


def test_available_rooms_lists_what_my_token_can_see_with_added_marks(tmp_path) -> None:
    settings, client, app, store, sessions, cipher = _stack(tmp_path)
    directory = app.state.workflow_application._slack_directory
    directory.default = {}
    directory.rooms = {"xoxp-test-mina": [
        SlackRoomInfo("C1", "channel", "general", member_count=12),
        SlackRoomInfo("G1", "group_dm", "김민아, 박지호", member_count=2),
        SlackRoomInfo("D9", "dm", "알림봇", is_bot=True, member_count=2),
    ]}
    assert client.get("/api/integrations/slack/available-rooms", headers=MINA).status_code == 404  # 슬랙 연동 없음
    mina = _slack_member(settings, client, "mina", MINA, [{"room_id": "C1", "type": "channel"}])
    first = client.get("/api/integrations/slack/available-rooms", headers=MINA).json()
    assert first == {
        "rooms": [
            {"room_id": "C1", "type": "channel", "name": "general", "is_bot": False, "member_count": 12, "already_added": True},
            {"room_id": "G1", "type": "group_dm", "name": "김민아, 박지호", "is_bot": False, "member_count": 2, "already_added": False},
        ],
        "next_cursor": "2",
    }
    rest = client.get("/api/integrations/slack/available-rooms", headers=MINA, params={"cursor": "2"}).json()
    assert [row["room_id"] for row in rest["rooms"]] == ["D9"] and rest["rooms"][0]["is_bot"] is True and rest["next_cursor"] is None
    filtered = client.get("/api/integrations/slack/available-rooms", headers=MINA, params={"q": "박지"}).json()
    assert [row["room_id"] for row in filtered["rooms"]] == ["G1"]
    store.mark_disconnected(mina, "token_revoked")
    gone = client.get("/api/integrations/slack/available-rooms", headers=MINA)
    assert gone.status_code == 409 and gone.json()["detail"]["code"] == "disconnected"


# ── 판 3 — 이름표 · 퍼머링크 · 워크스페이스 도메인 ───────────────────────────────────────────────


def test_room_messages_carry_user_tags_permalinks_and_the_workspace_domain(tmp_path) -> None:
    settings, client, _, store, sessions, cipher = _stack(tmp_path)
    mina = _slack_member(settings, client, "mina", MINA, [{"room_id": "C1", "type": "channel"}])
    sync = ExternalSync(store, cipher=cipher, slack=FakeSlack(), gmail=None, gmail_topic=None)
    sync.tick()  # 워크스페이스 주소를 알아 두고 방을 확인한다
    sync.handle_slack_event(_event("C1", 1, text="<@U2> 확인 부탁"))
    sync.handle_slack_event(_event("C1", 2, user="U2", thread_ts=ts(1)))
    [integration] = client.get("/api/integrations", headers=MINA).json()
    assert integration["domain"] == "medi.slack.com"
    [room] = client.get(f"/api/integrations/{mina}/rooms", headers=MINA).json()
    page = client.get(f"/api/inbox/rooms/{room['room_id']}/messages", headers=MINA).json()
    assert page["users"] == {
        "U1": {"name": "김민아", "is_bot": False, "avatar": "https://avatars.example/U1.png"},
        "U2": {"name": "박지호", "is_bot": False, "avatar": "https://avatars.example/U2.png"},
    }
    assert page["room"]["permalink"] == "https://medi.slack.com/archives/C1"
    [first] = page["messages"]  # 대화 줄은 최상위 글만 — 답글은 스레드 패널(`thread_ts`)이 따로 읽는다
    [_, reply] = client.get(f"/api/inbox/rooms/{room['room_id']}/messages", headers=MINA, params={"thread_ts": ts(1)}).json()["messages"]
    assert first["permalink"] == f"https://medi.slack.com/archives/C1/p{ts(1).replace('.', '')}"
    assert reply["permalink"].endswith(f"?thread_ts={ts(1)}&cid=C1")
    assert first["author"] == "김민아"  # 목록 카드도 id 가 아니라 이름


def test_restart_gap_fill_also_picks_up_new_replies_in_old_threads(tmp_path) -> None:
    settings, client, _, store, sessions, cipher = _stack(tmp_path)
    mina = _slack_member(settings, client, "mina", MINA, [{"room_id": "C1", "type": "channel"}])
    slack = FakeSlack()
    slack.pages["C1"] = [([{"ts": ts(5), "text": "새 글"}, {"ts": ts(1), "text": "옛 스레드", "thread_ts": ts(1), "reply_count": 1}], None)]
    slack.threads[ts(1)] = [{"ts": ts(1), "text": "옛 스레드", "thread_ts": ts(1)}, {"ts": ts(2), "text": "답 1", "thread_ts": ts(1)}]
    ExternalSync(store, cipher=cipher, slack=slack, gmail=None, gmail_topic=None).tick()
    # 꺼져 있던 사이 옛 스레드에 답글이 달렸다 — 최상위 history 에는 안 보인다.
    slack.threads[ts(1)].append({"ts": ts(7), "text": "답 2", "thread_ts": ts(1)})
    ExternalSync(store, cipher=cipher, slack=slack, gmail=None, gmail_topic=None).tick()
    assert ts(7) in {m.external_key for m in _messages(sessions, mina)}


# ── BE 수정 판 4 — 403 은 토큰 폐기가 아니다 · 남의 토큰 폐기로 내 연동을 끊지 않는다 ─────────────────────


def _http_error(status: int, body: dict):
    import io
    from urllib import error as urlerror

    def raise_(request, timeout):
        raise urlerror.HTTPError(request.full_url, status, "x", {}, io.BytesIO(json.dumps(body).encode()))

    return raise_


def test_gmail_errors_are_classified_by_reason_not_by_403_alone(monkeypatch, caplog) -> None:
    from ax_workspace.modules.external_channels.sync import UpstreamCallDenied, UpstreamRateLimited
    from ax_workspace.platform.external_gmail import GmailApi

    api = GmailApi("cid", "secret")
    target = "ax_workspace.platform.external_gmail.urlrequest.urlopen"
    denied = {"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "Request had insufficient authentication scopes.",
                        "errors": [{"reason": "insufficientPermissions"}]}}
    monkeypatch.setattr(target, _http_error(403, denied))
    with caplog.at_level("WARNING"), pytest.raises(UpstreamCallDenied):
        api.history("token", "100")
    assert any("insufficientPermissions" in r.getMessage() and "PERMISSION_DENIED" in r.getMessage() for r in caplog.records)
    rate = {"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "User-rate limit exceeded.",
                      "errors": [{"reason": "userRateLimitExceeded"}]}}
    monkeypatch.setattr(target, _http_error(403, rate))
    with pytest.raises(UpstreamRateLimited):
        api.list_inbox("token")
    monkeypatch.setattr(target, _http_error(401, {"error": {"code": 401, "status": "UNAUTHENTICATED"}}))
    with pytest.raises(UpstreamAuthRevoked):
        api.profile("token")
    monkeypatch.setattr(target, _http_error(400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}))
    with pytest.raises(UpstreamAuthRevoked):
        api.refresh("refresh")


def test_a_denied_mail_or_call_keeps_the_gmail_integration_connected(tmp_path) -> None:
    from ax_workspace.modules.external_channels.sync import UpstreamCallDenied

    settings, client, app, store, sessions, cipher = _stack(tmp_path)
    mail = _gmail_member(client, app, expires_at=datetime.now(UTC) + timedelta(hours=1))
    gmail = FakeGmail()
    real_get = gmail.get_message

    def get_message(token, message_id):
        if message_id == "m2":
            raise UpstreamCallDenied("gmail messages: PERMISSION_DENIED")
        return real_get(token, message_id)

    gmail.get_message = get_message
    sync = ExternalSync(store, cipher=cipher, slack=None, gmail=gmail, gmail_topic=None)
    sync.tick()
    sync.tick()
    assert sorted(m.external_key for m in _messages(sessions, mail)) == ["m1", "m3"]  # 그 메일 하나만 건너뛴다
    gmail.history = lambda token, start, page_token=None: (_ for _ in ()).throw(UpstreamCallDenied("history: PERMISSION_DENIED"))
    sync.tick(due_mail=[mail])
    [row] = client.get("/api/integrations", headers=MINA).json()
    assert row["status"] == "connected" and row["backfill_count"] == 2  # 끊기지 않는다 — 그 호출만 물러선다


def test_slack_token_revocation_events_only_disconnect_the_named_user(tmp_path, caplog) -> None:
    settings, client, _, store, sessions, cipher = _stack(tmp_path)
    mina = _slack_member(settings, client, "mina", MINA, [{"room_id": "C1", "type": "channel"}])
    jiho = _slack_member(settings, client, "jiho", JIHO, [{"room_id": "C1", "type": "channel"}])
    sync = ExternalSync(store, cipher=cipher, slack=FakeSlack(), gmail=None, gmail_topic=None)
    # 같은 워크스페이스의 다른 사람 토큰이 폐기됐다 — 우리 연동 누구도 끊지 않는다.
    sync.handle_slack_event({"team_id": TEAM, "event": {"type": "tokens_revoked", "tokens": {"oauth": ["U-someone"], "bot": []}}})
    assert {row["status"] for row in client.get("/api/integrations", headers=MINA).json()} == {"connected"}
    with caplog.at_level("WARNING"):
        sync.handle_slack_event({"team_id": TEAM, "event": {"type": "tokens_revoked", "tokens": {"oauth": ["U-jiho"]}}})
    assert client.get("/api/integrations", headers=JIHO).json()[0]["status"] == "disconnected"
    assert client.get("/api/integrations", headers=MINA).json()[0]["status"] == "connected"
    assert any("tokens_revoked" in r.getMessage() for r in caplog.records)
    sync.handle_slack_event({"team_id": "T-OTHER", "event": {"type": "app_uninstalled"}})
    assert client.get("/api/integrations", headers=MINA).json()[0]["status"] == "connected"
    sync.handle_slack_event({"team_id": TEAM, "event": {"type": "app_uninstalled"}})
    assert client.get("/api/integrations", headers=MINA).json()[0]["status"] == "disconnected"
    del mina, jiho
