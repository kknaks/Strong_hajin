"""사용자 사건 채널 SSE `GET /api/events/stream` (SPEC-011 §4.1 · SPEC-008 v0.7.0 §4.4 · WORK-013 WP1-BE).

끝나지 않는 스트림은 ASGI 로 직접 읽는다(`tests/sse_stream_support.py`). sqlite 에서는 NOTIFY 가 없으므로 알림 사건은 커밋 뒤 같은 프로세스 허브로 흐른다(`platform/user_events.py`).
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient
import pytest

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints import http_events
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules import notifications as notification_rules
from ax_workspace.modules.external_channels.events import UserEvent, UserEventType
from ax_workspace.platform import user_event_hub as hub_module
from ax_workspace.platform.persistence import NotificationRecord, make_session_factory

from sse_stream_support import SseConnection

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    app = create_app(settings)
    return app, TestClient(app), make_session_factory(database_url)


async def _opened(connection: SseConnection) -> None:
    """첫 두 줄 — `retry: 3000` 다음 `ready`."""
    assert await connection.frame() == {"retry": "3000"}
    ready = await connection.frame()
    assert ready["event"] == "ready" and ready["data"]["v"] == 1 and "server_time" in ready["data"]


def _request_for_jiho(client: TestClient, title: str) -> dict[str, Any]:
    response = client.post("/api/work-requests", headers=MINA, json={"title": title, "assignee_id": "jiho"})
    assert response.status_code in {200, 201}, response.text
    return response.json()


def _jiho_rows(sessions) -> list[NotificationRecord]:
    with sessions() as session:
        return list(
            session.query(NotificationRecord)
            .filter(NotificationRecord.recipient_member_id == "jiho")
            .order_by(NotificationRecord.seq)
        )


def _set_row(sessions, notification_id: UUID, *, seq: int | None = None, updated_at: datetime | None = None) -> None:
    with sessions() as session:
        row = session.get(NotificationRecord, notification_id)
        if seq is not None:
            row.seq = seq
        if updated_at is not None:
            row.updated_at = updated_at
        session.commit()


# ── 경로 · 응답 · 인증 (§4.1-1) ──────────────────────────────────────────────────────────


def test_the_stream_opens_with_retry_then_ready_and_the_proxy_safe_headers(tmp_path) -> None:
    app, _, _ = _stack(tmp_path)

    async def scenario() -> None:
        async with SseConnection(app, "/api/events/stream", MINA) as stream:
            assert stream.status == 200
            assert stream.headers["content-type"] == "text/event-stream; charset=utf-8"
            assert stream.headers["cache-control"] == "no-cache, no-transform"
            assert stream.headers["x-accel-buffering"] == "no"
            await _opened(stream)
            # 첫 연결(순번 없음)에는 이어 받기도 `resync` 도 없다(§4.1-3 ⑤)
            assert await stream.nothing_within(0.2)
            assert app.state.workflow_application.user_event_hub._subscribers.get("mina")
        # 화면이 떠나면 그 연결의 구독이 풀린다 — 쌓이면 회원마다 큐 200칸이 남는다(검수 W-4)
        assert not app.state.workflow_application.user_event_hub._subscribers.get("mina")

    asyncio.run(scenario())


def test_no_session_is_a_401_json_without_a_stream_and_a_foreign_origin_is_403(tmp_path) -> None:
    app, client, _ = _stack(tmp_path)
    anonymous = client.get("/api/events/stream")
    assert anonymous.status_code == 401
    assert anonymous.headers["content-type"].startswith("application/json") and "detail" in anonymous.json()
    foreign = client.get("/api/events/stream", headers={**MINA, "Origin": "https://evil.example"})
    assert foreign.status_code == 403
    assert foreign.headers["content-type"].startswith("application/json")

    async def scenario() -> None:
        # 웹 origin 과 같으면(로컬 루프백) 열린다 — 옛 WS 의 같은 출처 검사 그대로
        async with SseConnection(app, "/api/events/stream", {**MINA, "Origin": "http://localhost:5173"}) as stream:
            assert stream.status == 200
            await _opened(stream)

    asyncio.run(scenario())


def test_a_heartbeat_comment_keeps_an_idle_stream_alive(tmp_path, monkeypatch) -> None:
    app, _, _ = _stack(tmp_path)
    monkeypatch.setattr(http_events, "HEARTBEAT_SECONDS", 0.05)
    assert http_events.HEARTBEAT_SECONDS == 0.05

    async def scenario() -> None:
        async with SseConnection(app, "/api/events/stream", MINA) as stream:
            await _opened(stream)
            assert await stream.frame() == {"comment": "ping"}
            assert await stream.frame() == {"comment": "ping"}

    asyncio.run(scenario())


# ── 사건 종류 · 회원 거르기 (§4.1-2) ──────────────────────────────────────────────────────


def test_inbox_events_keep_their_name_and_fields_and_go_only_to_their_member(tmp_path) -> None:
    app, _, _ = _stack(tmp_path)
    hub = app.state.workflow_application.user_event_hub

    async def scenario() -> None:
        async with SseConnection(app, "/api/events/stream", MINA) as mine, SseConnection(app, "/api/events/stream", JIHO) as theirs:
            await _opened(mine)
            await _opened(theirs)
            hub.dispatch(UserEvent(UserEventType.MESSAGE_ARRIVED, "mina", integration_id="I1", room_id="R1", message_id="M1", source_kind="slack").to_payload())
            hub.dispatch(UserEvent(UserEventType.REPLY_RESULT, "mina", room_id="R1", data={"local_id": "L1", "status": "sent"}).to_payload())
            hub.dispatch(UserEvent(UserEventType.INTEGRATION_CHANGED, "jiho", integration_id="I9").to_payload())
            arrived = await mine.frame()
            assert arrived == {
                "event": "inbox.message_arrived",
                "data": {"v": 1, "type": "inbox.message_arrived", "member_id": "mina", "integration_id": "I1", "room_id": "R1", "message_id": "M1", "source_kind": "slack"},
            }
            reply = await mine.frame()
            assert reply["event"] == "inbox.reply_result" and reply["data"]["data"] == {"local_id": "L1", "status": "sent"}
            assert "id" not in arrived and "id" not in reply  # 메시지함 사건은 순번이 없다 — 다시 주지 않는다(§4.1-3 ④)
            # jiho 의 사건은 jiho 에게만
            changed = await theirs.frame()
            assert changed["event"] == "integration.changed" and changed["data"]["member_id"] == "jiho"
            assert await mine.nothing_within(0.2)

    asyncio.run(scenario())


def test_a_new_notification_arrives_with_its_seq_as_the_event_id(tmp_path) -> None:
    app, client, sessions = _stack(tmp_path)

    async def scenario() -> None:
        async with SseConnection(app, "/api/events/stream", JIHO) as stream, SseConnection(app, "/api/events/stream", MINA) as sender:
            await _opened(stream)
            await _opened(sender)
            request = _request_for_jiho(client, "배포 체크 요청")
            frame = await stream.frame()
            [row] = _jiho_rows(sessions)
            assert frame["event"] == "notification.upserted" and frame["id"] == row.seq
            assert frame["data"]["v"] == 1 and frame["data"]["created"] is True and frame["data"]["replayed"] is False
            item = frame["data"]["notification"]
            assert item["notification_id"] == str(row.id) and item["seq"] == row.seq
            assert item["kind"] == "work.request_received" and item["subject"]["id"] == request["request_id"]
            assert item["updated_at"]
            # 요청한 사람에게는 알림 사건이 가지 않는다 — 받는 사람의 큐에만
            assert await sender.nothing_within(0.2)

    asyncio.run(scenario())


def test_reading_a_notification_announces_notification_read(tmp_path) -> None:
    app, client, sessions = _stack(tmp_path)
    _request_for_jiho(client, "읽음 사건")
    [row] = _jiho_rows(sessions)

    async def scenario() -> None:
        async with SseConnection(app, "/api/events/stream", JIHO) as stream:
            await _opened(stream)
            assert client.post(f"/api/notifications/{row.id}/read", headers=JIHO).status_code == 200
            frame = await stream.frame()
            assert frame == {"event": "notification.read", "data": {"v": 1, "notification_ids": [str(row.id)]}}

    asyncio.run(scenario())


def test_unknown_event_types_are_not_forwarded(tmp_path) -> None:
    app, _, _ = _stack(tmp_path)
    hub = app.state.workflow_application.user_event_hub

    async def scenario() -> None:
        async with SseConnection(app, "/api/events/stream", MINA) as stream:
            await _opened(stream)
            hub.dispatch(json.dumps({"v": 1, "type": "something.else", "member_id": "mina"}))
            hub.dispatch(UserEvent(UserEventType.INTEGRATION_CHANGED, "mina", integration_id="I1").to_payload())
            assert (await stream.frame())["event"] == "integration.changed"

    asyncio.run(scenario())


# ── 이어 받기 (§4.1-3) ───────────────────────────────────────────────────────────────────


def test_reconnecting_with_the_query_cursor_after_a_non_200_replays_what_was_missed(tmp_path) -> None:
    """비-200 뒤 화면이 새 `EventSource` 를 만들면 머리가 없다 — `?last_event_id=` 로 그 사이 알림을 받는다(WORK-013 WP1-BE 시험)."""
    app, client, sessions = _stack(tmp_path)

    _request_for_jiho(client, "아주 오래된 것")
    [ancient] = _jiho_rows(sessions)
    # 한 시간 전 일 — 겹침 창(기준 − 60초) 밖이라 다시 오지 않는다
    _set_row(sessions, ancient.id, updated_at=datetime.now(UTC) - timedelta(hours=1))

    async def scenario() -> None:
        async with SseConnection(app, "/api/events/stream", JIHO) as first:
            await _opened(first)
            _request_for_jiho(client, "처음 것")
            seen = (await first.frame())["id"]
        # 끊긴 동안 선 알림
        _request_for_jiho(client, "끊긴 동안 1")
        _request_for_jiho(client, "끊긴 동안 2")
        rows = _jiho_rows(sessions)
        async with SseConnection(app, "/api/events/stream", JIHO, query={"last_event_id": str(seen)}) as again:
            await _opened(again)
            frames = await again.frames_until("resync")
        replayed = [frame for frame in frames if frame.get("event") == "notification.upserted"]
        # 기준 줄 자신은 겹침 창 안이라 다시 온다(화면이 (id · 순번)으로 거른다) — 그 뒤 놓친 둘 · 아주 오래된 것은 없다
        assert [frame["id"] for frame in replayed] == [seen, rows[2].seq, rows[3].seq]
        assert ancient.seq not in [frame["id"] for frame in replayed]
        assert all(frame["data"]["replayed"] is True for frame in replayed)
        assert [frame["data"]["notification"]["subject"]["title"] for frame in replayed] == ["처음 것", "끊긴 동안 1", "끊긴 동안 2"]
        assert frames[-1] == {"event": "resync", "data": {"v": 1, "reason": "reconnected"}}

    asyncio.run(scenario())


def test_the_last_event_id_header_wins_over_the_query(tmp_path) -> None:
    app, client, sessions = _stack(tmp_path)
    for title in ("하나", "둘", "셋"):
        _request_for_jiho(client, title)
    rows = _jiho_rows(sessions)
    _set_row(sessions, rows[0].id, updated_at=datetime.now(UTC) - timedelta(hours=3))  # 기준(둘째)의 창 밖
    for row in rows[1:]:
        _set_row(sessions, row.id, updated_at=datetime.now(UTC) - timedelta(hours=2))

    async def scenario() -> None:
        headers = {**JIHO, "Last-Event-ID": str(rows[1].seq)}
        async with SseConnection(app, "/api/events/stream", headers, query={"last_event_id": "0"}) as stream:
            await _opened(stream)
            frames = await stream.frames_until("resync")
        # 머리(두 번째 순번)가 기준 — 쿼리 0 이었다면 셋 다 왔다. 겹침 창 안의 기준 줄 자신은 다시 온다(화면이 거른다)
        assert [frame["id"] for frame in frames if frame.get("event") == "notification.upserted"] == [rows[1].seq, rows[2].seq]

    asyncio.run(scenario())


def test_a_smaller_seq_committed_later_is_caught_by_the_overlap_window(tmp_path) -> None:
    """순번 ≠ 커밋 순서(검수 W-2) — 작은 순번이 늦게 커밋되어 화면이 큰 순번을 먼저 받았어도 다시 붙으면 받는다."""
    app, client, sessions = _stack(tmp_path)
    for title in ("오래된 것", "늦게 커밋된 작은 순번", "먼저 받은 큰 순번"):
        _request_for_jiho(client, title)
    old, late, early = _jiho_rows(sessions)
    now = datetime.now(UTC)
    _set_row(sessions, old.id, updated_at=now - timedelta(hours=1))  # 창 밖
    _set_row(sessions, early.id, updated_at=now - timedelta(seconds=30))  # 화면이 받은 마지막(기준)
    _set_row(sessions, late.id, updated_at=now - timedelta(seconds=10))  # 순번은 작고 커밋은 뒤

    async def scenario() -> None:
        async with SseConnection(app, "/api/events/stream", JIHO, query={"last_event_id": str(early.seq)}) as stream:
            await _opened(stream)
            frames = await stream.frames_until("resync")
        replayed = [frame["id"] for frame in frames if frame.get("event") == "notification.upserted"]
        assert replayed == [late.seq, early.seq]  # 순번 순 · 오래된 것은 빠진다
        assert old.seq not in replayed

    asyncio.run(scenario())


def test_an_old_seq_whose_row_moved_to_a_new_seq_uses_the_largest_row_below_it(tmp_path) -> None:
    """합쳐져 순번이 바뀐 줄의 옛 순번으로 다시 붙기(r2 R-W1) — 기준 줄이 없으면 그 순번 이하 가장 큰 줄로 잰다."""
    app, client, sessions = _stack(tmp_path)
    for title in ("아래 줄", "합쳐진 줄"):
        _request_for_jiho(client, title)
    below, merged = _jiho_rows(sessions)
    now = datetime.now(UTC)
    old_seq = merged.seq
    _set_row(sessions, below.id, updated_at=now - timedelta(seconds=20))
    _set_row(sessions, merged.id, seq=old_seq + 50, updated_at=now)  # 합쳐지며 새 순번을 받았다 — 옛 순번의 줄은 없다

    async def scenario() -> None:
        async with SseConnection(app, "/api/events/stream", JIHO, query={"last_event_id": str(old_seq)}) as stream:
            await _opened(stream)
            frames = await stream.frames_until("resync")
        replayed = [frame["id"] for frame in frames if frame.get("event") == "notification.upserted"]
        # 기준 = 아래 줄(옛 순번 이하 가장 큰 순번) — 그 자신은 창 안이라 다시 오고, 새 순번의 합친 줄이 온다
        assert replayed == [below.seq, old_seq + 50]
        assert frames[-1]["data"]["reason"] == "reconnected"

    asyncio.run(scenario())


def test_with_no_row_at_or_below_the_cursor_only_resync_is_sent(tmp_path) -> None:
    app, client, sessions = _stack(tmp_path)
    _request_for_jiho(client, "나중 것")
    [row] = _jiho_rows(sessions)

    async def scenario() -> None:
        for cursor in (str(row.seq - 1), "-3"):
            async with SseConnection(app, "/api/events/stream", JIHO, query={"last_event_id": cursor}) as stream:
                await _opened(stream)
                assert await stream.frame() == {"event": "resync", "data": {"v": 1, "reason": "reconnected"}}
                assert await stream.nothing_within(0.2)

    asyncio.run(scenario())


def test_too_much_to_replay_sends_only_replay_overflow(tmp_path, monkeypatch) -> None:
    app, client, sessions = _stack(tmp_path)
    monkeypatch.setattr(notification_rules, "REPLAY_LIMIT", 2)
    _request_for_jiho(client, "기준")
    for title in ("하나", "둘", "셋"):
        _request_for_jiho(client, title)
    base = _jiho_rows(sessions)[0]

    async def scenario() -> None:
        async with SseConnection(app, "/api/events/stream", JIHO, query={"last_event_id": str(base.seq)}) as stream:
            await _opened(stream)
            assert await stream.frame() == {"event": "resync", "data": {"v": 1, "reason": "replay_overflow"}}
            assert await stream.nothing_within(0.2)

    asyncio.run(scenario())


def test_a_seq_already_replayed_is_not_sent_again_from_the_live_queue(tmp_path) -> None:
    """구독을 먼저 걸고 DB 를 읽으므로 같은 순번이 둘 다에 있을 수 있다 — 한 번만(§4.1-3-6)."""
    app, client, sessions = _stack(tmp_path)
    _request_for_jiho(client, "기준")
    _request_for_jiho(client, "다시 오는 것")
    base, later = _jiho_rows(sessions)
    hub = app.state.workflow_application.user_event_hub

    async def scenario() -> None:
        async with SseConnection(app, "/api/events/stream", JIHO, query={"last_event_id": str(base.seq)}) as stream:
            await _opened(stream)
            frames = await stream.frames_until("resync")
            assert later.seq in [frame.get("id") for frame in frames]
            hub.dispatch(UserEvent(UserEventType.NOTIFICATION_UPSERTED, "jiho", notification_id=str(later.id), seq=later.seq, created=True).to_payload())
            hub.dispatch(UserEvent(UserEventType.INTEGRATION_CHANGED, "jiho", integration_id="I1").to_payload())
            assert (await stream.frame())["event"] == "integration.changed"

    asyncio.run(scenario())


# ── 큐 넘침 (§4.1-4) ─────────────────────────────────────────────────────────────────────


def test_a_queue_overflow_tells_that_connection_to_resync(tmp_path, monkeypatch) -> None:
    app, _, _ = _stack(tmp_path)
    monkeypatch.setattr(hub_module, "QUEUE_LIMIT", 2)
    hub = app.state.workflow_application.user_event_hub

    async def scenario() -> None:
        async with SseConnection(app, "/api/events/stream", MINA) as stream:
            await _opened(stream)
            for index in range(5):  # 한 번에 몰린다 — 화면이 읽기 전에 큐가 넘친다
                hub.dispatch(UserEvent(UserEventType.MESSAGE_ARRIVED, "mina", room_id=f"R{index}").to_payload())
            frames = [await stream.frame() for _ in range(3)]
        assert frames[0] == {"event": "resync", "data": {"v": 1, "reason": "dropped"}}
        assert [frame["data"]["room_id"] for frame in frames[1:]] == ["R3", "R4"]  # 오래된 것을 잃고 마지막 둘이 남는다

    asyncio.run(scenario())


# ── 옛 WS 는 메시지함 넷만 (Rollback 여지) ─────────────────────────────────────────────────


def test_the_old_inbox_websocket_still_carries_only_the_four_inbox_events(tmp_path) -> None:
    app, client, _ = _stack(tmp_path)
    hub = app.state.workflow_application.user_event_hub
    with client.websocket_connect("/api/inbox/stream", headers=JIHO) as socket:
        assert socket.receive_json() == {"type": "ready"}
        _request_for_jiho(client, "옛 화면은 모르는 사건")  # notification.upserted — WS 로는 가지 않는다
        hub.dispatch(UserEvent(UserEventType.INTEGRATION_CHANGED, "jiho", integration_id="I1").to_payload())
        assert socket.receive_json() == {"type": "integration.changed", "integration_id": "I1"}


@pytest.mark.parametrize("value", ["", "  ", "not-a-number"])
def test_an_empty_or_non_integer_cursor_is_a_first_connection(tmp_path, value) -> None:
    """정수가 아닌 `Last-Event-ID` 는 없는 것으로 본다 — 첫 연결처럼 이어 받기도 `resync` 도 없다(SPEC-011 Validation)."""
    app, _, _ = _stack(tmp_path)

    async def scenario() -> None:
        async with SseConnection(app, "/api/events/stream", {**MINA, "Last-Event-ID": value}) as stream:
            await _opened(stream)
            assert await stream.nothing_within(0.2)

    asyncio.run(scenario())


def test_a_cursor_above_the_members_newest_seq_sends_only_resync(tmp_path) -> None:
    """커서가 그 회원의 가장 큰 순번보다 크면 이어 받기 없이 `resync` 만(SPEC-011 Validation · 검수 W-3)."""
    app, client, sessions = _stack(tmp_path)
    for title in ("하나", "둘"):
        _request_for_jiho(client, title)
    newest = _jiho_rows(sessions)[-1]

    async def scenario() -> None:
        async with SseConnection(app, "/api/events/stream", JIHO, query={"last_event_id": str(newest.seq + 5)}) as stream:
            await _opened(stream)
            assert await stream.frame() == {"event": "resync", "data": {"v": 1, "reason": "reconnected"}}
            assert await stream.nothing_within(0.2)

    asyncio.run(scenario())


def test_read_all_announces_notification_read_for_everything(tmp_path) -> None:
    """`read-all` 은 `{all: true, theme: null}`(SPEC-011 §4.5-1) — 다른 창 · 앱이 점을 맞춘다."""
    app, client, _ = _stack(tmp_path)
    _request_for_jiho(client, "모두 읽을 것")

    async def scenario() -> None:
        async with SseConnection(app, "/api/events/stream", JIHO) as stream:
            await _opened(stream)
            assert client.post("/api/notifications/read-all", headers=JIHO).json() == {"read": 1}
            assert await stream.frame() == {"event": "notification.read", "data": {"v": 1, "all": True, "theme": None}}

    asyncio.run(scenario())
