"""`task_schedules` 의 **하루 한 칸**을 살아 있는 PostgreSQL 에서 센다 (WORK-004 Phase BE-1).

**모델 선언이나 `create_all` 성공을 근거로 삼지 않는다.** `pg_index`·`pg_constraint` 에 그 제약이
실제로 서 있고 `valid` 한지를 묻고, **잘못된 행이 실제로 거절되는 것**과 **닫힌 날에 다시 설 수
있는 것**을 한 건씩 남긴다. SQLite 는 부분 unique 와 CHECK 을 여기와 같은 모양으로 답하지 않으므로,
그 확인은 이 파일이 갖는다 (SPEC-004 §6 · `be-survey-report.md` §2 3).

**빈 격리 데이터베이스에서만 돈다** (`AX_POSTGRES_TEST_URL`).

**착수 전 선행 점검 (2026-09-21 · 읽기 전용).** 실행 PostgreSQL(`127.0.0.1:54329` · `ax_demo`)의
`pg_index` 에 서 있던 **부분 unique 는 여덟**이었다 — 모델이 선언한 아홉 중 `uq_task_predecessors_active`
하나가 없었고, 그 표(`task_predecessors`) 자체가 없었다(표 88 / 모델 90). **ORM 선언과 살아 있는
스키마는 같지 않았다**는 사실이 이 파일이 존재하는 이유다. `make sync-demo-schema` 가 그 둘과
`task_schedules` 를 함께 세웠고 `manual[]` 은 비어 있었다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from ax_workspace.platform.persistence import TaskScheduleRecord, make_session_factory

from test_postgres_integration import _postgres_test_url
from v2_pg_support import MINA, own_task, pg_stack

pytestmark = pytest.mark.integration

#: 자동 취소·오늘 판정이 닿지 않는 먼 미래. 여기서 세는 것은 제약이지 달력이 아니다.
FIRST = date(2027, 3, 1)
LAST = date(2027, 3, 5)
MIDDLE = date(2027, 3, 3)


def _index(connection, name: str) -> tuple[bool, str] | None:
    row = connection.execute(
        text(
            "SELECT i.indisvalid, pg_get_indexdef(i.indexrelid) "
            "FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :name"
        ),
        {"name": name},
    ).first()
    return (bool(row[0]), row[1]) if row else None


def _constraint(connection, name: str) -> tuple[str, bool] | None:
    row = connection.execute(
        text("SELECT contype, convalidated FROM pg_constraint WHERE conname = :name"), {"name": name}
    ).first()
    return (row[0], bool(row[1])) if row else None


def _spanned_task(client) -> str:
    return own_task(client, "월~금짜리 업무", MINA, start_date=FIRST.isoformat(), due_date=LAST.isoformat())


def _slot(client, task_id: str, on_date: date, start: str, end: str):
    return client.post(
        f"/api/tasks/{task_id}/schedules",
        headers=MINA,
        json={"on_date": on_date.isoformat(), "starts_at": start, "ends_at": end},
    )


def test_the_table_and_its_partial_unique_actually_stand_in_postgres() -> None:
    """① **있고 valid 하다** — 선언이 아니라 `information_schema`·`pg_index` 가 답한다."""
    database_url = _postgres_test_url()
    pg_stack(database_url)

    with make_session_factory(database_url)() as session:
        connection = session.connection()
        live = connection.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_name = 'task_schedules'")
        ).scalar_one_or_none()
        assert live == "task_schedules"

        index = _index(connection, "uq_task_schedules_active")
        assert index is not None, "uq_task_schedules_active 가 서지 않았다"
        valid, definition = index
        assert valid is True
        assert "UNIQUE" in definition
        # **하루 한 칸의 축은 (업무, 날)** 이다 — 시각이 축이 되면 같은 날 두 칸이 선다.
        assert "task_id" in definition and "on_date" in definition

        # 값 수준 불변식은 CHECK 이 답한다 — 어떤 상태에서도 참이 될 수 없는 값이다.
        assert _constraint(connection, "ck_task_schedules_time_order") == ("c", True)


def test_the_predicate_counts_only_the_rows_that_are_not_closed() -> None:
    """② 술어가 **「닫히지 않은 행만」** 이다 — 닫힌 행이 유일성에서 빠지는 근거가 이 한 줄이다."""
    database_url = _postgres_test_url()
    pg_stack(database_url)

    with make_session_factory(database_url)() as session:
        _, definition = _index(session.connection(), "uq_task_schedules_active")

    assert "WHERE" in definition and "released_at IS NULL" in definition


def test_a_second_live_slot_on_the_same_day_is_refused_by_the_database() -> None:
    """③ **application 검사를 믿지 않는다** — 같은 (업무, 날)의 두 번째 살아 있는 행을 DB 가 거절한다.

    두 명령이 동시에 들어오면 둘 다 「없다」를 보는 틈이 남는다. 그 틈을 닫는 것은 여기다.
    """
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    task_id = _spanned_task(client)
    assert _slot(client, task_id, MIDDLE, "10:00", "11:00").status_code == 201

    factory = make_session_factory(database_url)
    with factory() as session:
        now = datetime.now(UTC)
        session.add(
            TaskScheduleRecord(
                task_id=UUID(task_id),
                on_date=MIDDLE,
                starts_at=time(14, 0),
                ends_at=time(15, 0),
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    # **다른 날은 막히지 않는다** — 유일성은 (업무, 날) 쌍이지 업무 하나가 아니다.
    assert _slot(client, task_id, LAST, "10:00", "11:00").status_code == 201
    with factory() as session:
        rows = session.scalars(select(TaskScheduleRecord)).all()
        assert sorted(row.on_date for row in rows) == [MIDDLE, LAST]


def test_a_day_whose_slot_was_closed_takes_a_new_one() -> None:
    """④ **닫힌 행은 유일성에서 빠진다** — 그 날에 다시 배정할 수 있고 닫힌 행은 이력으로 남는다.

    닫기는 **Phase BE-2** 가 여는 경로(기간 조정)의 결과이지만, **그 자리가 실제로 비는가**는
    표의 성질이라 여기서 센다 — 닫는 명령을 기다리지 않고 저장소 수준에서 확인한다.
    """
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    task_id = _spanned_task(client)
    first = _slot(client, task_id, MIDDLE, "10:00", "11:00")
    assert first.status_code == 201, first.text

    factory = make_session_factory(database_url)
    with factory() as session:
        closed = session.get(TaskScheduleRecord, UUID(first.json()["schedule_id"]))
        closed.released_at = datetime.now(UTC)
        closed.released_reason = "out_of_range"
        session.commit()

    again = _slot(client, task_id, MIDDLE, "14:00", "15:00")

    assert again.status_code == 201, again.text
    assert again.json()["schedule_id"] != first.json()["schedule_id"]
    with factory() as session:
        rows = session.scalars(select(TaskScheduleRecord)).all()
        assert len(rows) == 2
        assert len([row for row in rows if row.released_at is None]) == 1
    # **닫힌 배정은 조회에 실리지 않는다** — 외부에서는 존재를 알 수 없다.
    calendar = client.get(
        "/api/calendar", headers=MINA, params={"from": FIRST.isoformat(), "to": LAST.isoformat()}
    ).json()
    row = next(row for row in calendar if row["kind"] == "task" and row["task_id"] == task_id)
    assert [entry["schedule_id"] for entry in row["schedules"]] == [again.json()["schedule_id"]]


def test_the_database_refuses_a_slot_that_ends_before_it_starts() -> None:
    """표면의 `422` 와 별개로 **CHECK 이 마지막 문을 닫는다** — 선행의 자기참조 금지와 같은 자리다."""
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    task_id = _spanned_task(client)

    with make_session_factory(database_url)() as session:
        now = datetime.now(UTC)
        session.add(
            TaskScheduleRecord(
                task_id=UUID(task_id),
                on_date=MIDDLE,
                starts_at=time(15, 0),
                ends_at=time(14, 0),
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
