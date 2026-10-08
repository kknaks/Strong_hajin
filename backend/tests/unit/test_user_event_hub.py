"""`integration.changed` 묶어 내기 — 같은 회원·같은 연동은 1초에 한 번, 마지막 하나는 놓치지 않는다
(SPEC-008 §4.4 v0.6.0 · OQ-818 · WORK-012 WP1-BE). 타이머는 대역으로 바꿔 스레드를 띄우지 않는다.
"""
from __future__ import annotations

import asyncio

from ax_workspace.modules.external_channels.events import UserEvent, UserEventType
from ax_workspace.platform.user_event_hub import UserEventHub


def _payload(kind: str, *, member: str = "mina", integration: str = "I1", room: str | None = None) -> str:
    return UserEvent(kind, member, integration_id=integration, room_id=room).to_payload()


def _drain(queue: asyncio.Queue) -> list[UserEvent]:
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


def test_integration_changes_are_coalesced_to_one_per_window_and_the_last_is_kept() -> None:
    async def scenario() -> None:
        timers: list = []
        hub = UserEventHub("sqlite://", schedule=lambda delay, callback: timers.append((delay, callback)))
        queue = hub.subscribe("mina")

        hub.dispatch(_payload(UserEventType.INTEGRATION_CHANGED, room="R1"))  # 창의 첫 사건 — 곧바로
        hub.dispatch(_payload(UserEventType.INTEGRATION_CHANGED, room="R1"))  # 창 안 — 밀림
        hub.dispatch(_payload(UserEventType.INTEGRATION_CHANGED, room="R2"))  # 다른 방 — 연동 전체로
        hub.dispatch(_payload(UserEventType.INTEGRATION_CHANGED, integration="I2"))  # 다른 연동 — 따로 곧바로
        hub.dispatch(_payload(UserEventType.MESSAGE_ARRIVED, room="R1"))  # 새 메시지는 묶지 않는다
        await asyncio.sleep(0)
        first = _drain(queue)
        assert [(event.type, event.integration_id, event.room_id) for event in first] == [
            (UserEventType.INTEGRATION_CHANGED, "I1", "R1"),
            (UserEventType.INTEGRATION_CHANGED, "I2", None),
            (UserEventType.MESSAGE_ARRIVED, "I1", "R1"),
        ]
        assert [delay for delay, _ in timers] == [1.0, 1.0]

        # 창이 닫히면 밀린 마지막 하나가 나가고, 계속 몰릴 때를 위해 창이 하나 더 열린다.
        timers.pop(0)[1]()
        await asyncio.sleep(0)
        [trailing] = _drain(queue)
        assert (trailing.type, trailing.integration_id, trailing.room_id) == (UserEventType.INTEGRATION_CHANGED, "I1", None)
        assert len(timers) == 2

        # 그다음 창에 아무것도 없으면 닫히고 끝 — 다음 사건은 다시 곧바로 나간다.
        timers.pop()[1]()
        await asyncio.sleep(0)
        assert _drain(queue) == []
        hub.dispatch(_payload(UserEventType.INTEGRATION_CHANGED))
        await asyncio.sleep(0)
        assert len(_drain(queue)) == 1

    asyncio.run(scenario())


def test_other_members_windows_do_not_swallow_each_other() -> None:
    async def scenario() -> None:
        hub = UserEventHub("sqlite://", schedule=lambda delay, callback: None)
        mina, jiho = hub.subscribe("mina"), hub.subscribe("jiho")
        hub.dispatch(_payload(UserEventType.INTEGRATION_CHANGED, member="mina"))
        hub.dispatch(_payload(UserEventType.INTEGRATION_CHANGED, member="jiho"))
        await asyncio.sleep(0)
        assert len(_drain(mina)) == 1 and len(_drain(jiho)) == 1

    asyncio.run(scenario())


def test_an_overflowing_queue_keeps_the_newest_and_marks_that_it_dropped(monkeypatch) -> None:
    """큐 200칸이 넘치면 가장 오래된 것을 버리고 **버렸다는 표시**를 남긴다 — SSE 가 `resync(dropped)` 를 낸다(SPEC-011 §4.1-4)."""
    from ax_workspace.platform import user_event_hub as hub_module

    monkeypatch.setattr(hub_module, "QUEUE_LIMIT", 2)

    async def scenario() -> None:
        hub = UserEventHub("sqlite://", schedule=lambda delay, callback: None)
        queue = hub.subscribe("mina")
        assert queue.dropped is False
        for room in ("R1", "R2"):
            hub.dispatch(_payload(UserEventType.MESSAGE_ARRIVED, room=room))
        await asyncio.sleep(0)
        assert queue.dropped is False  # 꽉 찼을 뿐 아직 버리지 않았다
        hub.dispatch(_payload(UserEventType.MESSAGE_ARRIVED, room="R3"))
        await asyncio.sleep(0)
        assert queue.dropped is True
        assert [event.room_id for event in _drain(queue)] == ["R2", "R3"]

    asyncio.run(scenario())


def test_notification_events_carry_only_the_short_payload() -> None:
    """알림 사건의 NOTIFY 는 `{v, type, member_id, notification_id, seq, created}` 만 — 항목은 API 가 DB 에서 읽는다(§4.1-4)."""
    import json

    event = UserEvent(UserEventType.NOTIFICATION_UPSERTED, "jiho", notification_id="N1", seq=42, created=False)
    assert json.loads(event.to_payload()) == {
        "v": 1, "type": "notification.upserted", "member_id": "jiho", "notification_id": "N1", "seq": 42, "created": False,
    }
    again = UserEvent.from_payload(event.to_payload())
    assert again is not None and (again.notification_id, again.seq, again.created) == ("N1", 42, False)
    read = UserEvent(UserEventType.NOTIFICATION_READ, "jiho", data={"all": True, "theme": None})
    assert json.loads(read.to_payload()) == {"v": 1, "type": "notification.read", "member_id": "jiho", "data": {"all": True, "theme": None}}
    # 메시지함 사건의 모양은 그대로다 — 새 칸이 비어 있으면 싣지 않는다
    assert json.loads(UserEvent(UserEventType.MESSAGE_ARRIVED, "mina", room_id="R1").to_payload()) == {
        "v": 1, "type": "inbox.message_arrived", "member_id": "mina", "room_id": "R1",
    }
