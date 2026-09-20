"""WORK-003 이 세우는 **DB 제약 셋을 살아 있는 PostgreSQL 에서** 센다 (WORK-003 § Domain/Schema).

세는 것은 셋이다 — **읽음 영수증 유일성** · **선행 활성 행 유일성**(부분 unique) · **자기참조 금지**(CHECK).

**모델 선언이나 `create_all` 성공을 근거로 삼지 않는다.** `pg_constraint`·`pg_index` 에 그 제약이
실제로 서 있고 `valid` 한지를 묻고, **잘못된 행이 실제로 거절되는 것**을 한 건씩 남긴다.
SQLite 는 부분 unique 와 CHECK 을 여기와 같은 모양으로 답하지 않으므로, 그 확인은 이 파일이 갖는다.

**빈 격리 데이터베이스에서만 돈다** (`AX_POSTGRES_TEST_URL`). 실사용 데이터베이스에 제약을
적용하거나 전환하는 일은 이 work 의 범위 밖이다 (DEC-001 D-17b).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from ax_workspace.platform.persistence import (
    TaskPredecessorRecord,
    WorkRequestReadReceiptRecord,
    make_session_factory,
)

from test_postgres_integration import _postgres_test_url
from v2_pg_support import JIHO, MINA, own_task, pg_stack, send_request

pytestmark = pytest.mark.integration


def _project(client, *members: str) -> str:
    created = client.post("/api/projects", headers=JIHO, json={"name": "순서가 있는 프로젝트"})
    assert created.status_code == 201, created.text
    project_id = created.json()["project_id"]
    for member_id in members:
        joined = client.post(f"/api/projects/{project_id}/members", headers=JIHO, json={"member_id": member_id})
        assert joined.status_code == 201, joined.text
    return project_id


def _constraint(connection, name: str) -> tuple[str, bool] | None:
    row = connection.execute(
        text("SELECT contype, convalidated FROM pg_constraint WHERE conname = :name"), {"name": name}
    ).first()
    return (row[0], bool(row[1])) if row else None


def _index(connection, name: str) -> tuple[bool, str] | None:
    row = connection.execute(
        text(
            "SELECT i.indisvalid, pg_get_indexdef(i.indexrelid) "
            "FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :name"
        ),
        {"name": name},
    ).first()
    return (bool(row[0]), row[1]) if row else None


def test_the_three_new_constraints_actually_stand_in_postgres() -> None:
    """**서 있는지를 데이터베이스에 묻는다** — 선언이 아니라 `pg_constraint`·`pg_index` 가 답한다."""
    database_url = _postgres_test_url()
    pg_stack(database_url)
    factory = make_session_factory(database_url)
    with factory() as session:
        connection = session.connection()

        # 1. 읽음 영수증 — (요청 · 사람) 한 쌍에 걸린 유일성.
        assert _constraint(connection, "uq_work_request_read_receipt") == ("u", True)

        # 2. 선행 — **활성 행만** 걸리는 부분 unique. 닫힌 행은 몇 개든 남을 수 있어야 한다.
        valid, definition = _index(connection, "uq_task_predecessors_active")
        assert valid is True
        assert "UNIQUE" in definition and "released_at IS NULL" in definition

        # 3. 선행 — 자기참조 금지 CHECK.
        assert _constraint(connection, "ck_task_predecessors_not_self") == ("c", True)


def test_a_second_read_receipt_for_the_same_pair_is_refused_by_the_database() -> None:
    """**application 검사를 믿지 않는다** — 같은 쌍의 두 번째 행을 데이터베이스가 거절한다."""
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    request_id = send_request(client, "참조가 걸린 요청", "jiho", MINA, cc_member_ids=["yuna"])["request_id"]

    read = client.post(f"/api/work-requests/{request_id}/read", headers={"X-Demo-Persona": "yuna"})
    assert read.status_code == 200, read.text

    factory = make_session_factory(database_url)
    with factory() as session:
        session.add(
            WorkRequestReadReceiptRecord(
                work_request_id=UUID(request_id), member_id="yuna", read_at=datetime.now(UTC)
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    # **다른 사람의 읽음은 막히지 않는다** — 유일성은 (요청 · 사람) 쌍이지 요청 하나가 아니다.
    with factory() as session:
        session.add(
            WorkRequestReadReceiptRecord(
                work_request_id=UUID(request_id), member_id="minseok", read_at=datetime.now(UTC)
            )
        )
        session.flush()
        session.commit()
    with factory() as session:
        assert len(session.scalars(select(WorkRequestReadReceiptRecord)).all()) == 2


def test_the_partial_unique_counts_only_active_predecessor_rows() -> None:
    """같은 관계의 **활성 행은 하나**다. 닫힌 행은 몇 개든 남는다 — 이력을 지우지 않기 때문이다."""
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    project_id = _project(client, "mina")
    first = own_task(client, "선행", MINA, project_id=project_id)
    later = own_task(client, "후행", MINA, project_id=project_id, preceding_task_ids=[first])

    factory = make_session_factory(database_url)
    with factory() as session:
        session.add(
            TaskPredecessorRecord(
                task_id=UUID(later),
                predecessor_task_id=UUID(first),
                position=1,
                created_by="mina",
                created_at=datetime.now(UTC),
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    # 떼고 다시 다는 길은 열려 있다 — 닫힌 행 옆에 새 활성 행이 선다.
    version = client.get(f"/api/tasks/{later}", headers=MINA).json()["version"]
    cleared = client.patch(
        f"/api/tasks/{later}", headers=MINA, json={"expected_version": version, "preceding_task_ids": []}
    )
    assert cleared.status_code == 200, cleared.text
    again = client.patch(
        f"/api/tasks/{later}",
        headers=MINA,
        json={"expected_version": cleared.json()["version"], "preceding_task_ids": [first]},
    )
    assert again.status_code == 200, again.text

    with factory() as session:
        rows = session.scalars(select(TaskPredecessorRecord)).all()
        assert len(rows) == 2
        assert len([row for row in rows if row.released_at is None]) == 1


def test_the_database_refuses_a_task_that_precedes_itself() -> None:
    """**자기 자신은 선행이 될 수 없다** — application 거절과 별개로 CHECK 이 마지막 문을 닫는다."""
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    project_id = _project(client, "mina")
    task_id = own_task(client, "혼자인 업무", MINA, project_id=project_id)

    with make_session_factory(database_url)() as session:
        session.add(
            TaskPredecessorRecord(
                task_id=UUID(task_id),
                predecessor_task_id=UUID(task_id),
                position=0,
                created_by="mina",
                created_at=datetime.now(UTC),
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_the_request_approver_column_stands_and_reaches_the_task_it_creates() -> None:
    """요청의 결재자가 **실제 PostgreSQL 에서** 저장되고 그 요청이 세운 업무로 이어진다.

    제약이 붙지 않는 값이라 이 파일의 나머지와 성격이 다르다 — 여기서 세는 것은 **열이 실제로 서고
    두 표의 값이 같다**는 것 하나다. SQLite 계약 시험이 보는 것과 같은 사실을 살아 있는 스키마에서
    다시 확인해, 모델 선언만으로 넘어가지 않는다.
    """
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    request = send_request(client, "결재자를 실은 요청", "jiho", MINA, approver_id="yuna")
    assert request["approver_id"] == "yuna"

    factory = make_session_factory(database_url)
    with factory() as session:
        stored = session.execute(
            text("SELECT approver_id FROM work_requests WHERE id = :id"), {"id": request["request_id"]}
        ).scalar_one()
        assert stored == "yuna"
        derived = session.execute(
            text("SELECT approver_id FROM tasks WHERE source_work_request_id = :id"),
            {"id": request["request_id"]},
        ).scalar_one()
        assert derived == "yuna"

    # 비우고 보내는 길도 그대로다 — 0..1 이라 비어 있는 것이 정상이다.
    blank = send_request(client, "결재자 없는 요청", "jiho", MINA)
    assert blank["approver_id"] is None


def test_two_transactions_marking_the_same_reference_read_converge_on_one_row() -> None:
    """**같은 사람의 동시 두 번이 행 하나**다 (§5 동시성). 진 쪽은 이긴 행을 읽어 같은 시각을 답한다."""
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    request_id = UUID(send_request(client, "동시에 읽는 요청", "jiho", MINA, cc_member_ids=["yuna"])["request_id"])

    from ax_workspace.platform.work_tasks import SqlAlchemyWorkRequestRepository

    factory = make_session_factory(database_url)
    with factory() as first_session, factory() as second_session:
        # 둘 다 **「없다」를 본 상태**에서 시작한다.
        assert SqlAlchemyWorkRequestRepository(first_session).read_receipt(request_id, "yuna") is None
        assert SqlAlchemyWorkRequestRepository(second_session).read_receipt(request_id, "yuna") is None
        first = SqlAlchemyWorkRequestRepository(first_session).mark_read(request_id, "yuna")
        first_session.commit()
        second = SqlAlchemyWorkRequestRepository(second_session).mark_read(request_id, "yuna")
        second_session.commit()
        assert second.read_at == first.read_at

    with factory() as session:
        assert len(session.scalars(select(WorkRequestReadReceiptRecord)).all()) == 1


def test_two_transactions_writing_the_same_predecessor_converge_on_one_active_row() -> None:
    """같은 관계를 두 transaction 이 동시에 세워도 **활성 행은 하나**다 — 부분 unique 가 답한다."""
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    project_id = _project(client, "mina")
    first = UUID(own_task(client, "선행", MINA, project_id=project_id))
    later = UUID(own_task(client, "후행", MINA, project_id=project_id))

    from ax_workspace.platform.work_tasks import SqlAlchemyTaskRepository

    factory = make_session_factory(database_url)
    with factory() as first_session, factory() as second_session:
        SqlAlchemyTaskRepository(first_session).replace_predecessors(later, [first], "mina")
        first_session.commit()
        # 둘째는 이미 활성인 관계를 그대로 다시 쓴다 — 행을 더하지 않는다.
        SqlAlchemyTaskRepository(second_session).replace_predecessors(later, [first], "mina")
        second_session.commit()

    with factory() as session:
        active = [row for row in session.scalars(select(TaskPredecessorRecord)) if row.released_at is None]
        assert len(active) == 1
