"""슬랙 방 목록·접근 확인의 대역 — 회원 토큰마다 「그 사람이 볼 수 있는 방」을 따로 갖는다(F-1 시험용)."""
from __future__ import annotations

from ax_workspace.modules.external_channels.application import SlackRoomInfo
from ax_workspace.modules.external_channels.domain import RoomAccessDenied


class FakeSlackDirectory:
    def __init__(self, rooms: dict[str, list[SlackRoomInfo]] | None = None, *, default: dict[str, str] | None = None) -> None:
        #: 토큰 → 그 토큰이 볼 수 있는 방. `default` 는 어떤 토큰이든 보이는 방 `{id: type}`.
        self.rooms = rooms or {}
        self.default = default if default is not None else {}
        self.described: list[tuple[str, str]] = []

    def _visible(self, token: str) -> dict[str, SlackRoomInfo]:
        visible = {room_id: SlackRoomInfo(room_id, room_type, room_id) for room_id, room_type in self.default.items()}
        visible.update({room.room_id: room for room in self.rooms.get(token, [])})
        return visible

    def describe(self, token: str, channel: str) -> SlackRoomInfo:
        self.described.append((token, channel))
        room = self._visible(token).get(channel)
        if room is None:
            raise RoomAccessDenied("channel_not_found")
        return room

    def list_page(self, token: str, cursor: str | None):
        rooms = list(self._visible(token).values())
        return (rooms[:2], "2") if cursor is None and len(rooms) > 2 else (rooms[2:] if cursor else rooms, None)
