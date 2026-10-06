"""슬랙 방 고르기 목록이 빠르다 — 이름표 한 번(`users.list`) · 그룹 DM 은 `mpdm-` 이름으로 · 목록 캐시 (BE 수정 판 6)."""
from __future__ import annotations

from collections import Counter
import threading

import pytest

from ax_workspace.modules.external_channels.domain import UpstreamUnavailableError
from ax_workspace.modules.external_channels.sync import UpstreamRateLimited, UpstreamRoomDenied
from ax_workspace.platform import external_slack
from ax_workspace.platform.external_slack import SlackRoomDirectory, SlackUserCache, SlackWebApi, group_dm_handles

USERS = [
    {"id": "U1", "name": "mina.kim", "profile": {"display_name": "김민아", "image_72": "https://a/U1.png"}},
    {"id": "U2", "name": "jiho.park", "profile": {"real_name": "박지호"}},
    {"id": "U3", "name": "sora", "profile": {"display_name": "소라"}},
    {"id": "B1", "name": "alertbot", "is_bot": True, "profile": {"real_name": "알림봇"}},
]
CHANNELS = [
    {"id": "C1", "name": "general", "is_channel": True},
    {"id": "D1", "is_im": True, "user": "U2"},
    {"id": "D2", "is_im": True, "user": "B1"},
    {"id": "G1", "name": "mpdm-mina.kim--jiho.park--sora-1", "is_mpim": True},
    {"id": "G2", "name": "mpdm-mina.kim--ghost-1", "is_mpim": True},  # 이름표에 없는 사람 — 그때만 참여자를 묻는다
]


class FakeSlack:
    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()
        self.lock = threading.Lock()
        self.limit_users_list: float | None = None
        self.unknown: set[str] = {"U-gone"}

    def __call__(self, method, token, params=None, **kwargs):
        with self.lock:
            self.calls[method] += 1
        if method == "users.list":
            if self.limit_users_list is not None:
                wait, self.limit_users_list = self.limit_users_list, None
                raise UpstreamRateLimited(wait)
            if (params or {}).get("cursor") is None:
                return {"ok": True, "members": USERS[:2], "response_metadata": {"next_cursor": "u2"}}
            return {"ok": True, "members": USERS[2:], "response_metadata": {"next_cursor": ""}}
        if method == "users.conversations":
            return {"ok": True, "channels": CHANNELS, "response_metadata": {"next_cursor": ""}}
        if method == "conversations.members":
            return {"ok": True, "members": ["U1", "U-gone"]}
        if method == "users.info":
            if params["user"] in self.unknown:
                raise UpstreamRoomDenied("user_not_found")
            return {"ok": True, "user": {"id": params["user"], "name": "x", "profile": {"real_name": "새사람"}}}
        raise AssertionError(method)


@pytest.fixture
def slack(monkeypatch) -> FakeSlack:
    fake = FakeSlack()
    monkeypatch.setattr(external_slack, "call", fake)
    return fake


def test_names_come_from_one_users_list_and_group_dms_from_their_mpdm_name(slack) -> None:
    directory = SlackRoomDirectory(SlackUserCache())
    rooms, cursor = directory.list_page("xoxp-a", None)
    by_id = {room.room_id: room for room in rooms}
    assert cursor is None
    assert by_id["D1"].name == "박지호" and not by_id["D1"].is_bot
    assert by_id["D2"].name == "알림봇" and by_id["D2"].is_bot
    assert by_id["G1"].name == "김민아, 박지호, 소라" and by_id["G1"].member_count == 3  # 참여자 호출 없이
    assert by_id["G2"].name == "김민아"  # 모르는 사람이 있으면 그 방만 참여자를 묻는다
    assert slack.calls == Counter({"users.list": 2, "users.conversations": 1, "conversations.members": 1, "users.info": 1})

    # 「방 추가」 창을 다시 열면 즉시 — 슬랙을 다시 부르지 않는다.
    before = slack.calls.copy()
    assert directory.list_page("xoxp-a", None)[0] == rooms
    assert slack.calls == before


def test_the_worker_shares_the_same_name_cache(slack) -> None:
    users = SlackUserCache()
    SlackRoomDirectory(users).list_page("xoxp-a", None)
    api = SlackWebApi(users)
    before = slack.calls.copy()
    assert api.user_name("xoxp-a", "U3") == "소라"
    assert api.user_tag("xoxp-a", "U1") == {"name": "김민아", "is_bot": False, "avatar": "https://a/U1.png"}
    assert api.user_name("xoxp-a", "U-gone") is None and api.user_name("xoxp-a", "U-gone") is None
    assert slack.calls == before  # 이름표도, 「없는 사람」이라는 사실도 방 목록과 같은 캐시에서 — 슬랙을 다시 부르지 않는다


def test_a_short_rate_limit_is_waited_out_and_a_long_one_uses_stale_names(slack, monkeypatch) -> None:
    waited: list[float] = []
    monkeypatch.setattr(external_slack.time, "sleep", waited.append)
    users = SlackUserCache(ttl=0)
    slack.limit_users_list = 1.0
    users.directory("xoxp-a")
    assert waited == [1.0]  # Retry-After 를 지키고 한 번 더
    slack.limit_users_list = 120.0
    names, _ = users.directory("xoxp-a")  # 길면 기다리지 않고 묵은 이름표
    assert names["U1"].name == "김민아" and waited == [1.0]

    fresh = SlackRoomDirectory(SlackUserCache())
    slack.limit_users_list = 120.0
    with pytest.raises(UpstreamUnavailableError):
        fresh.list_page("xoxp-b", None)  # 묵은 것도 없으면 502 로(화면이 다시 시도)


def test_group_dm_handles_follow_slacks_naming() -> None:
    assert group_dm_handles("mpdm-kim--lee.j--park-1") == ["kim", "lee.j", "park"]
    assert group_dm_handles("general") is None and group_dm_handles("mpdm-") is None
