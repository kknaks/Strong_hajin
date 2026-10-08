"""알림 생성기의 규칙 — 원칙 셋 · 관계 우선 · 설정 거름 · 판정 재료 (SPEC-011 §4.3 · §4.4 · WORK-013 WP2-BE).

저장소는 대역이다 — DB 없이 「누가 어떤 꼬리표로 한 줄을 받는가」만 잰다.
"""
from __future__ import annotations

from typing import Any

import pytest

from ax_workspace.modules.notification_events import (
    THEMES,
    NotificationEvent,
    NotificationGenerator,
    default_settings,
    mail_from_me,
    mail_relation,
    message_notification,
    recipients,
    settings_allow,
    slack_from_me,
    slack_mentions_me,
    theme_item,
)


class FakeStore:
    def __init__(self, *, inactive: set[str] | None = None, settings: dict[str, dict] | None = None) -> None:
        self.inactive = inactive or set()
        self.settings = settings or {}
        self.written: list[Any] = []

    def is_active_member(self, member_id: str) -> bool:
        return member_id not in self.inactive

    def settings_for(self, member_id: str) -> dict | None:
        return self.settings.get(member_id)

    def write(self, draft):
        self.written.append(draft)
        return draft


def _event(*pairs, actor: str | None = "mina", kind: str = "work.commented") -> NotificationEvent:
    return NotificationEvent(
        row="W36", kind=kind, source_kind="test", source_id="1", recipients=recipients(*pairs),
        subject={"type": "task", "id": "T", "title": "업무"}, actor_member_id=actor,
    )


def _got(store: FakeStore) -> list[tuple[str, str]]:
    return [(draft.recipient_member_id, draft.relation) for draft in store.written]


def test_one_row_per_person_with_the_first_tag_in_priority_order() -> None:
    store = FakeStore(settings={member: _all_on() for member in ("jiho", "hyeon")})
    NotificationGenerator(store).notify(_event(("jiho", "cc"), ("jiho", "assignee"), ("hyeon", "cc"), ("hyeon", "requester")))
    assert _got(store) == [("jiho", "assignee"), ("hyeon", "requester")]


def test_meeting_priority_is_owner_then_attendee_then_shared() -> None:
    store = FakeStore()
    NotificationGenerator(store).notify(
        _event(("mina", "attendee"), ("mina", "owner"), ("jiho", "shared"), ("jiho", "attendee"), actor=None, kind="meeting.minutes_ready")
    )
    assert _got(store) == [("mina", "owner"), ("jiho", "attendee")]


def test_the_actor_inactive_members_and_system_seats_get_nothing() -> None:
    store = FakeStore(inactive={"hyeon"}, settings={"jiho": _all_on()})
    NotificationGenerator(store).notify(_event(("mina", "assignee"), ("hyeon", "cc"), ("system:meeting", "requester"), ("jiho", "cc")))
    assert _got(store) == [("jiho", "cc")]


def _all_on() -> dict:
    settings = default_settings()
    for group in settings["themes"].values():
        group["items"] = {item: True for item in group["items"]}
    return settings


def test_settings_filter_by_whole_theme_and_item_and_defaults_turn_comments_off() -> None:
    defaults = default_settings()
    assert sum(len(items) for items in THEMES.values()) == 16
    assert settings_allow(None, "work", "request") is True
    assert settings_allow(None, "work", "comment") is False  # 시안 `on: false`
    off = default_settings()
    off["enabled"] = False
    assert settings_allow(off, "work", "request") is False
    theme_off = default_settings()
    theme_off["themes"]["meeting"]["on"] = False
    assert settings_allow(theme_off, "meeting", "invite") is False and settings_allow(theme_off, "work", "request") is True
    item_off = default_settings()
    item_off["themes"]["message"]["items"]["slack"] = False
    assert settings_allow(item_off, "message", "slack") is False and settings_allow(item_off, "message", "mail") is True
    assert defaults["themes"]["work"]["on"] is True


def test_every_kind_has_one_theme_and_item_and_integration_lost_takes_the_channel() -> None:
    assert theme_item("work.request_received") == ("work", "request")
    assert theme_item("meeting.minutes_failed") == ("meeting", "minutes-fail")
    assert theme_item("message.integration_lost", "kakao") == ("message", "kakao")
    with pytest.raises(ValueError):
        theme_item("message.integration_lost")


def _gmail(**headers: str) -> dict:
    return {"payload": {"headers": [{"name": name.title(), "value": value} for name, value in headers.items()]}}


def test_mail_materials_from_me_to_cc_other() -> None:
    raw = _gmail(**{"from": "Me <ME@Company.example>", "to": "Partner <p@example.com>"})
    assert mail_from_me(raw, "me@company.example") is True
    assert mail_from_me(_gmail(**{"from": "p@example.com"}), "me@company.example") is False
    assert mail_from_me({}, "me@company.example") is None
    assert mail_relation(_gmail(to='"Me, Kim" <me@company.example>, b@x.example'), "me@company.example") == "to"
    assert mail_relation(_gmail(to="b@x.example", cc="me@company.example"), "me@company.example") == "mail-cc"
    assert mail_relation(_gmail(to="list@x.example"), "me@company.example") == "mail-other"


def test_slack_materials_use_raw_user_and_only_a_real_mention() -> None:
    assert slack_from_me({"user": "U1"}, "U1") is True and slack_from_me({"user": "U2"}, "U1") is False
    assert slack_from_me({}, "U1") is None
    assert slack_mentions_me({"text": "<@U1> 봐 주세요"}, "U1") is True
    assert slack_mentions_me({"text": "<!here> <!channel> <!subteam^S1>"}, "U1") is False


def test_a_line_i_sent_is_never_an_event() -> None:
    common = dict(
        member_id="mina", integration_id="I", account_key=None, account_label=None, my_user_id="U1",
        room={"id": "R", "room_type": "channel", "name": "general"}, message_id="M", raw={"user": "U1", "text": "x"},
        author="나", preview="x", subject=None, sent_at=None, attachment_count=0,
    )
    assert message_notification(kind="slack", from_me=True, **common) is None
    event = message_notification(kind="slack", from_me=None, **common)
    assert event is not None and event.row == "X06" and event.coalesce_key == "slack-channel:R"
