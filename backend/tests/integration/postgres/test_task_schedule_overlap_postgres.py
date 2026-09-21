"""겹침 금지를 **살아 있는 PostgreSQL** 에서 센다 (SPEC-004 §2.9 · DEC-003 증보 8 K22 · Phase BE-3).

**왜 이 파일이 필요한가 — 두 가지다.**

1. **SQLite 는 여기서 거짓말을 한다.** 회의는 `timestamptz` 로 살고 배정은 `Date` + `Time` 로 산다.
   SQLite 의 `DATETIME` bind 는 **tzinfo 를 버리고 벽시계만** 넘기므로, 사무실 시각을 UTC 로 묶지
   않으면 저장된 값과 **아홉 시간 어긋난 문자열 비교**가 된다. 계약 테스트는 그 어긋남을 못 본다 —
   **양쪽이 같이 틀리면 통과하기 때문이다.** 진짜 `timestamptz` 위에서 같은 답이 나오는지는 여기서 묻는다.
2. **제약으로 올리지 않았다는 것을 적극적으로 센다.** 「하루 한 칸」은 부분 unique 가 **데이터베이스에서**
   답하지만 겹침은 **application 이** 답한다 — 두 표(`task_schedules`·`meetings`)에 걸쳐 있어
   걸 자리가 없다. `EXCLUDE USING gist` 로 막고 싶어지는 자리이고, **막지 않았다**는 것이 계약이다
   (§2.9 한계 표). 안 만든 것은 `pg_constraint` 에 물어야 증명된다.

**남는 틈을 감추지 않는다.** 동시 요청 둘이 각각 검사를 통과해 **둘 다 설 수 있다.**
이 파일은 그 틈이 없다고 주장하지 않는다 — 「하루 한 칸」과 **같은 세기의 보장이 아니다.**

**빈 격리 데이터베이스에서만 돈다** (`AX_POSTGRES_TEST_URL`).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from ax_workspace.platform.persistence import make_session_factory

from test_postgres_integration import _postgres_test_url
from v2_pg_support import JIHO, MINA, own_task, pg_stack

pytestmark = pytest.mark.integration

FIRST = date(2027, 3, 1)
LAST = date(2027, 3, 5)
DAY = date(2027, 3, 3)
NEXT = date(2027, 3, 4)
OFFICE = ZoneInfo("Asia/Seoul")
OVERLAP_MESSAGE = "이미 다른 일정이 있는 시간입니다"


def _spanned_task(client, title: str = "월~금짜리 업무", headers: dict[str, str] = MINA) -> str:
    return own_task(client, title, headers, start_date=FIRST.isoformat(), due_date=LAST.isoformat())


def _slot(client, task_id: str, on_date: date, start: str, end: str, headers: dict[str, str] = MINA):
    return client.post(
        f"/api/tasks/{task_id}/schedules",
        headers=headers,
        json={"on_date": on_date.isoformat(), "starts_at": start, "ends_at": end},
    )


def _meeting(client, starts: datetime, ends: datetime, headers: dict[str, str] = MINA, **body):
    created = client.post(
        "/api/meetings",
        headers=headers,
        json={"title": "겹침을 만드는 회의", "starts_at": starts.isoformat(), "ends_at": ends.isoformat(), **body},
    )
    assert created.status_code == 201, created.text
    return created.json()["meeting"]


def _office(on_date: date, hour: int, minute: int = 0) -> datetime:
    return datetime(on_date.year, on_date.month, on_date.day, hour, minute, tzinfo=OFFICE)


def test_overlap_is_not_a_database_constraint_and_must_not_become_one() -> None:
    """**안 만든 것**을 센다 — 두 표에 걸친 불변식이라 걸 자리가 없다 (§2.9).

    「하루 한 칸」은 그대로 DB 가 답한다. 그 둘이 **같은 세기가 아니라는 것**이 계약이고,
    누군가 배제 제약을 얹으면 그 계약이 조용히 달라지므로 여기서 막는다.
    """
    database_url = _postgres_test_url()
    pg_stack(database_url)

    with make_session_factory(database_url)() as session:
        connection = session.connection()
        kinds = connection.execute(
            text(
                "SELECT conname, contype FROM pg_constraint "
                "WHERE conrelid = 'task_schedules'::regclass ORDER BY conname"
            )
        ).all()
        # `x` = EXCLUDE. 시각을 축으로 하는 배제 제약은 **없다.**
        assert [name for name, contype in kinds if contype == "x"] == []
        # 그리고 「하루 한 칸」은 여전히 데이터베이스가 답한다 — 이 판이 그것을 약화시키지 않았다.
        partial = connection.execute(
            text("SELECT indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                 "WHERE c.relname = 'uq_task_schedules_active'")
        ).scalar_one()
        assert partial is True
        # `meetings` 쪽에도 시각 배제 제약을 만들지 않았다 — 검증은 application 한 자리다.
        meeting_excludes = connection.execute(
            text("SELECT conname FROM pg_constraint WHERE conrelid = 'meetings'::regclass AND contype = 'x'")
        ).all()
        assert meeting_excludes == []


def test_a_slot_that_overlaps_another_task_is_refused_on_real_postgres() -> None:
    """배정 대 배정 — `Date` + `Time` 축만 쓰는 갈래. 경계는 여전히 통과한다."""
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    held, wanted, touching = (
        _spanned_task(client, "먼저 잡은 일"),
        _spanned_task(client, "나중에 온 일"),
        _spanned_task(client, "경계에 붙는 일"),
    )
    assert _slot(client, held, DAY, "10:00", "11:00").status_code == 201

    refused = _slot(client, wanted, DAY, "10:30", "11:30")

    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == OVERLAP_MESSAGE
    assert _slot(client, touching, DAY, "11:00", "12:00").status_code == 201


def test_a_timestamptz_meeting_blocks_the_office_hours_it_actually_holds() -> None:
    """**SQLite 가 가려 주는 자리.** 사무실 10–11 시 회의는 진짜 `timestamptz` 위에서도 10–11 시다.

    시간대 변환이 어긋나 있으면 여기서 드러난다 — 아홉 시간 밀린 창을 보면 10:30 배정이 통과하고
    01:30 배정이 막힌다.
    """
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    inside, dawn, touching = (
        _spanned_task(client, "회의와 겹치는 일"),
        _spanned_task(client, "UTC 시각에 놓는 일"),
        _spanned_task(client, "회의 직후"),
    )
    _meeting(client, _office(DAY, 10), _office(DAY, 11))

    refused = _slot(client, inside, DAY, "10:30", "11:30")

    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == OVERLAP_MESSAGE
    # **UTC 로 읽으면 01:00–02:00 인 회의다.** 그 시각이 막히면 시간대가 어긋난 것이다.
    assert _slot(client, dawn, DAY, "01:30", "02:30").status_code == 201
    assert _slot(client, touching, DAY, "11:00", "12:00").status_code == 201


def test_a_meeting_that_crosses_midnight_blocks_the_next_dawn_on_real_postgres() -> None:
    """자정 분할 — 회의는 자정을 넘고 배정은 못 넘는다. 쪼개지 않으면 다음 날 새벽이 비어 보인다."""
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    late, dawn, after = (
        _spanned_task(client, "밤"),
        _spanned_task(client, "새벽"),
        _spanned_task(client, "새벽 뒤"),
    )
    _meeting(client, _office(DAY, 23), _office(NEXT, 1))

    assert _slot(client, late, DAY, "23:30", "23:45").status_code == 409
    blocked = _slot(client, dawn, NEXT, "00:30", "01:30")
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"] == OVERLAP_MESSAGE
    assert _slot(client, after, NEXT, "01:00", "02:00").status_code == 201


def test_only_my_own_time_is_read_on_the_assignment_side() -> None:
    """배정 쪽은 **나의 시간만** 본다 — 주최자·참석자 전원을 보는 것은 회의 쪽(Phase BE-4)이다."""
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    mine = _spanned_task(client)
    _meeting(client, _office(DAY, 10), _office(DAY, 11), headers=JIHO)

    assert _slot(client, mine, DAY, "10:30", "11:30").status_code == 201

    # 내가 참석자로 적힌 회의는 이미 내 캘린더에 서 있으므로 내 시간을 차지한다.
    _meeting(client, _office(NEXT, 10), _office(NEXT, 11), headers=JIHO, attendee_ids=["mina"])
    refused = _slot(client, _spanned_task(client, "참석 회의와 겹치는 일"), NEXT, "10:30", "11:30")
    assert refused.status_code == 409, refused.text


def test_sliding_one_slot_over_itself_is_not_an_overlap_on_real_postgres() -> None:
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    created = _slot(client, _spanned_task(client), DAY, "10:00", "11:00")
    assert created.status_code == 201, created.text
    slot = created.json()

    slid = client.patch(
        f"/api/task-schedules/{slot['schedule_id']}",
        headers=MINA,
        json={"expected_version": slot["version"], "starts_at": "10:30", "ends_at": "11:30"},
    )

    assert slid.status_code == 200, slid.text
    assert (slid.json()["starts_at"], slid.json()["version"]) == ("10:30", 2)


def test_already_overlapping_history_is_left_alone_on_real_postgres() -> None:
    """검증은 **새 쓰기에만** 걸린다 — 이미 겹쳐 있는 것을 소급해 닫는 쓰기를 만들지 않았다."""
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    one, two = _spanned_task(client, "먼저"), _spanned_task(client, "겹쳐 쌓인 것")
    assert _slot(client, one, DAY, "10:00", "11:00").status_code == 201
    with make_session_factory(database_url)() as session:
        # 검증을 지나지 않는 자리에서 직접 세운다 — 검증 이전에 쌓인 행을 재현한다.
        session.execute(
            text(
                "INSERT INTO task_schedules (id, task_id, on_date, starts_at, ends_at, version, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :task_id, :on_date, '10:30', '11:30', 1, now(), now())"
            ),
            {"task_id": two, "on_date": DAY},
        )
        session.commit()

    assert _slot(client, one, DAY + timedelta(days=1), "10:00", "11:00").status_code == 201

    with make_session_factory(database_url)() as session:
        alive = session.execute(
            text("SELECT count(*) FROM task_schedules WHERE released_at IS NULL AND on_date = :on_date"),
            {"on_date": DAY},
        ).scalar_one()
    assert alive == 2
