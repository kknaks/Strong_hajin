from datetime import UTC, datetime, timedelta

from ax_workspace.modules.meetings.rooms import (
    Attendee,
    MeetingRoom,
    build_reservation,
    choose_replacement,
    headcount,
    map_participants,
)


def test_replacement_uses_the_smallest_room_that_fits_every_attendee() -> None:
    rooms = [
        MeetingRoom(2, "4인실", 4),
        MeetingRoom(3, "6인실", 6),
        MeetingRoom(5, "12인실", 12),
    ]

    assert headcount(inside=2, outside=1) == 3
    assert choose_replacement(rooms, people=3, exclude_room_id=3) == rooms[0]
    assert choose_replacement(rooms, people=5, exclude_room_id=3) == rooms[2]
    assert choose_replacement(rooms, people=13, exclude_room_id=3) is None


def test_booking_participants_prefer_company_accounts_and_keep_unmatched_names() -> None:
    attendees = [
        Attendee("민아", "demo@example.test"),
        Attendee("지호", "jiho@company.test"),
        Attendee("계정 없는 사람"),
    ]
    members = [
        {"name": "민아", "email": "mina@company.test"},
        {"name": "다른 지호", "email": "jiho@company.test"},
    ]

    mapping = map_participants(attendees, members)

    assert mapping.emails == ["mina@company.test", "jiho@company.test"]
    assert mapping.unmatched == ["계정 없는 사람"]


def test_reservation_uses_office_time_a_fallback_title_and_distinct_display_names() -> None:
    starts = datetime(2026, 9, 14, 1, 30, tzinfo=UTC)

    request = build_reservation(
        room_id=3,
        starts_at=starts,
        ends_at=starts + timedelta(hours=1),
        title=None,
        booker_name="민아",
        participant_emails=("mina@company.test",),
        outside_names=("김외부", "김외부"),
    )

    assert (request.date, request.start, request.end) == ("2026-09-14", "10:30", "11:30")
    assert request.title == "제목 없는 회의"
    assert request.display_names == ("민아", "김외부")
