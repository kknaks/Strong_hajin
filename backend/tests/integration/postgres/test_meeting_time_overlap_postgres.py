"""회의 쪽 겹침과 `approval` 을 **살아 있는 PostgreSQL** 에서 센다 (SPEC-004 §2.9 · §4 · Phase BE-4).

**왜 여기가 따로 필요한가.** 회의는 `timestamptz` 로 살고 배정은 `Date` + `Time` 로 산다. SQLite 의
`DateTime` bind 는 **오프셋을 버리므로 양쪽이 같이 틀리면 계약 테스트가 통과한다** — 두 축을 맞춘
계산이 실제로 맞는지는 진짜 `timestamptz` 위에서만 답이 나온다. BE-3 이 배정 쪽을 그렇게 쟀고,
이 파일이 **회의 쪽**을 같은 방식으로 잰다.

**증보 10 K28 의 회귀도 여기서 본다** — 회의 시각 변경이 **UTC 로 정규화**되므로 SQLite 와
PostgreSQL 이 **같은 순간**을 남긴다. 그 한 줄이 빠지면 두 엔진의 답이 아홉 시간 갈린다.

**빈 격리 데이터베이스에서만 돈다** (`AX_POSTGRES_TEST_URL`).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ax_workspace.platform.persistence import make_session_factory  # noqa: F401  (격리 확인용 import 유지)

from test_postgres_integration import _postgres_test_url
from v2_pg_support import JIHO, MINA, own_task, pg_stack

pytestmark = pytest.mark.integration

FIRST = date(2027, 3, 1)
LAST = date(2027, 3, 5)
DAY = date(2027, 3, 3)
NEXT = date(2027, 3, 4)
OFFICE = ZoneInfo("Asia/Seoul")


def _office(on_date: date, hour: int, minute: int = 0) -> datetime:
    return datetime(on_date.year, on_date.month, on_date.day, hour, minute, tzinfo=OFFICE)


def _spanned_task(client, title: str = "월~금짜리 업무", headers: dict[str, str] = MINA) -> str:
    return own_task(client, title, headers, start_date=FIRST.isoformat(), due_date=LAST.isoformat())


def _slot(client, task_id: str, on_date: date, start: str, end: str, headers: dict[str, str] = MINA):
    created = client.post(
        f"/api/tasks/{task_id}/schedules",
        headers=headers,
        json={"on_date": on_date.isoformat(), "starts_at": start, "ends_at": end},
    )
    assert created.status_code == 201, created.text
    return created.json()


def _book(client, starts: datetime, ends: datetime, headers: dict[str, str] = JIHO, **body):
    return client.post(
        "/api/meetings",
        headers=headers,
        json={
            "title": body.pop("title", "예약하려는 회의"),
            "starts_at": starts.isoformat(),
            "ends_at": ends.isoformat(),
            **body,
        },
    )


def test_an_attendees_office_hours_block_a_meeting_on_real_postgres() -> None:
    """사무실 10–11 시 배정은 진짜 `timestamptz` 위에서도 10–11 시다 — 경계는 통과한다."""
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    _slot(client, _spanned_task(client), DAY, "10:00", "11:00")

    refused = _book(client, _office(DAY, 10, 30), _office(DAY, 11, 30), attendee_ids=["mina"])

    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == "민아 님의 일정과 겹칩니다"
    assert _book(client, _office(DAY, 11), _office(DAY, 12), attendee_ids=["mina"]).status_code == 201
    # **UTC 로 읽으면 01:00–02:00 인 배정이다.** 그 시각의 회의가 막히면 시간대가 어긋난 것이다.
    assert _book(client, _office(DAY, 1), _office(DAY, 2), attendee_ids=["mina"]).status_code == 201


def test_a_meeting_across_midnight_is_refused_against_the_next_dawn_on_real_postgres() -> None:
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    _slot(client, _spanned_task(client, "새벽 업무"), NEXT, "00:30", "01:30")

    refused = _book(client, _office(DAY, 23), _office(NEXT, 1), attendee_ids=["mina"])

    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == "민아 님의 일정과 겹칩니다"


def test_moving_a_meeting_keeps_the_instant_it_was_moved_to() -> None:
    """**증보 10 K28** — 시각 변경이 UTC 로 정규화되므로 두 엔진이 같은 순간을 남긴다.

    그리고 **자기 자신은 뺀다** — 빼지 않으면 시각을 한 칸도 옮길 수 없다.
    """
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    created = _book(client, _office(DAY, 10), _office(DAY, 11), attendee_ids=["mina"])
    assert created.status_code == 201, created.text
    meeting = created.json()["meeting"]
    assert meeting["starts_at"] == "2027-03-03T01:00:00+00:00"

    moved = client.patch(
        f"/api/meetings/{meeting['meeting_id']}",
        headers=JIHO,
        json={"starts_at": _office(DAY, 14).isoformat(), "ends_at": _office(DAY, 15).isoformat()},
    )

    assert moved.status_code == 200, moved.text
    assert moved.json()["meeting"]["starts_at"] == "2027-03-03T05:00:00+00:00"
    assert moved.json()["meeting"]["ends_at"] == "2027-03-03T06:00:00+00:00"


def test_moving_a_meeting_onto_an_attendees_time_is_refused_and_changes_nothing() -> None:
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    _slot(client, _spanned_task(client), DAY, "10:00", "11:00")
    created = _book(client, _office(DAY, 14), _office(DAY, 15), attendee_ids=["mina"])
    assert created.status_code == 201, created.text
    meeting = created.json()["meeting"]

    refused = client.patch(
        f"/api/meetings/{meeting['meeting_id']}",
        headers=JIHO,
        json={"starts_at": _office(DAY, 10, 30).isoformat(), "ends_at": _office(DAY, 11, 30).isoformat()},
    )

    assert refused.status_code == 409, refused.text
    current = client.get(f"/api/meetings/{meeting['meeting_id']}", headers=JIHO).json()["meeting"]
    assert current["starts_at"] == "2027-03-03T05:00:00+00:00"


def test_the_calendar_row_carries_approval_on_real_postgres() -> None:
    """`approval` 은 **열이 아니라 투영**이다 — 스키마를 바꾸지 않았고, 회차 질의 하나로 답한다."""
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    task_id = _spanned_task(client, "승인을 기다릴 업무", JIHO)

    rows = client.get(
        "/api/calendar", headers=JIHO, params={"from": FIRST.isoformat(), "to": LAST.isoformat()}
    ).json()

    row = next(item for item in rows if item["kind"] == "task" and item["task_id"] == task_id)
    assert row["approval"] is None
    assert "derived" not in row
    # 열이 생기지 않았다 — `tasks` 에 그 이름의 칼럼이 없다.
    with make_session_factory(database_url)() as session:
        from sqlalchemy import text

        columns = {
            name
            for (name,) in session.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'tasks'")
            )
        }
    assert "approval" not in columns


def test_a_meeting_with_only_external_attendees_still_stands_on_real_postgres() -> None:
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    _slot(client, _spanned_task(client), DAY, "10:00", "11:00")

    created = _book(
        client, _office(DAY, 10, 30), _office(DAY, 11, 30),
        external_attendees=["한빛상사 김대표"], title="사외만 부른 회의",
    )

    assert created.status_code == 201, created.text
    assert created.json()["meeting"]["external_attendees"] == ["한빛상사 김대표"]
