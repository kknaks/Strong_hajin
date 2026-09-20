"""WORK-002 v2 의 **스키마·FK·인덱스를 실제 PostgreSQL 에서** 센다 (WORK-002 Phase 1 · §Domain/Schema).

**무엇을 증명하는가.** 모델 선언이나 `create_all` 이 성공했다는 사실을 근거로 삼지 않는다 — 살아 있는
PostgreSQL 의 `information_schema` · `pg_constraint` · `pg_index` 에 **그 제약이 실제로 서 있고
valid 한지**를 묻고, **잘못된 행이 거절되는 것**을 한 건씩 남긴다. SQLite 선언은 여기서 아무것도
증명하지 않는다 (§검증 계획 P-7).

**O-24 를 인위로 재현한다.** `schema_sync`(= `make sync-demo-schema`)는 **없는 표를 만들고 없는 컬럼을
더할 뿐 기존 표에 인덱스를 더하지 않는다.** 그래서 기존 표에 더할 인덱스는 레포 안의 `.sql` 둘이
적용 단위다. 여기서는 그 인덱스를 **일부러 내리고**, `--sync` 만으로는 돌아오지 않는 것을 먼저 보인
뒤 `.sql` 을 적용해 닫는다.

**SQL 파일은 고치지 않는다.** 이 파일은 그 둘을 **적용하고 검증할 뿐**이고, 정의가 어긋나면 그것은
보고할 결함이지 여기서 손댈 자리가 아니다.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from ax_workspace.bootstrap import schema_sync
from ax_workspace.entrypoints.reset_demo import reset_database

from test_postgres_integration import _postgres_test_url
from v2_pg_support import MINA, own_task, pg_stack, send_request

#: Phase 1 이 남긴 적용 단위 둘. **이 파일들이 증거다** — 여기서 고치지 않는다.
MANUAL_SQL = Path(__file__).resolve().parents[3] / "migrations" / "manual"
TRANSACTIONAL_SQL = MANUAL_SQL / "2026-09-17-w2-indexes.sql"
CONCURRENT_SQL = MANUAL_SQL / "2026-09-17-w2-indexes.concurrent.sql"

#: 기존 표(`work_requests`)에 더하는 인덱스 — `--sync` 가 만들지 않는 자리 (O-24).
EXISTING_TABLE_INDEXES = ("ix_work_requests_parent_task_id", "ix_work_requests_supersedes_request_id")


def _statements(path: Path) -> list[str]:
    """주석을 걷고 `;` 로 가른 실제 DDL 만."""
    body = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("--"))
    return [" ".join(part.split()) for part in body.split(";") if part.strip()]


def _index_names(connection, table: str) -> set[str]:
    return set(
        connection.scalars(text("SELECT indexname FROM pg_indexes WHERE tablename = :table"), {"table": table}).all()
    )


def _index_validity(connection, name: str) -> tuple[bool, bool] | None:
    row = connection.execute(
        text(
            "SELECT i.indisvalid, i.indisready FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :name"
        ),
        {"name": name},
    ).first()
    return None if row is None else (bool(row[0]), bool(row[1]))


def _column(connection, table: str, column: str) -> tuple[str, str] | None:
    row = connection.execute(
        text(
            "SELECT data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    ).first()
    return None if row is None else (str(row[0]), str(row[1]))


def _foreign_key(connection, table: str, column: str) -> tuple[str, str, bool] | None:
    """`table.column` 하나로 선 FK 의 (이름, 가리키는 표, valid 여부)."""
    row = connection.execute(
        text(
            """
            SELECT con.conname, target.relname, con.convalidated
              FROM pg_constraint con
              JOIN pg_class source ON source.oid = con.conrelid
              JOIN pg_class target ON target.oid = con.confrelid
              JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = con.conkey[1]
             WHERE con.contype = 'f'
               AND source.relname = :table
               AND att.attname = :column
               AND array_length(con.conkey, 1) = 1
            """
        ),
        {"table": table, "column": column},
    ).first()
    return None if row is None else (str(row[0]), str(row[1]), bool(row[2]))


# ---------------------------------------------------------------------------
# 새 컬럼과 새 표 — additive 인지, 사람이 정할 자리를 남기지 않았는지
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_postgres_w2_additive_columns_and_tables_stand_and_need_no_human_decision() -> None:
    """새 컬럼은 **전부 nullable**, 새 표는 **새 표**다 — `--sync` 가 `manual` 에 올릴 자리가 없다 (O-25).

    `plan()` 은 **모델에 없는 기존 컬럼**도 `manual` 에 넣으므로 목록 전체가 비기를 기대하지 않는다
    (`schema_sync.py:44-45`). **이번에 더한 이름들이 그 안에 없다**는 것이 이 단계의 계약이다.
    """
    database_url = _postgres_test_url()
    reset_database(database_url)
    added = {
        "work_requests": ("parent_task_id", "supersedes_request_id"),
        "tasks": ("cancel_reason", "started_at", "completed_at", "reopened_at"),
    }
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            for table, columns in added.items():
                for column in columns:
                    found = _column(connection, table, column)
                    assert found is not None, f"{table}.{column} 이 실제 PostgreSQL 에 없다"
                    assert found[1] == "YES", f"{table}.{column} 은 nullable 이어야 한다 ({found})"
            for table in ("task_proposals", "work_request_list_entries"):
                exists = connection.scalar(
                    text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"public.{table}"}
                )
                assert exists, f"{table} 표가 실제 PostgreSQL 에 없다"
    finally:
        engine.dispose()

    plan = schema_sync.plan(database_url)
    names = [f"{table}.{column}" for table, columns in added.items() for column in columns]
    assert plan["statements"] == [], plan["statements"]
    for name in [*names, "task_proposals", "work_request_list_entries"]:
        assert not any(name in note for note in plan["manual"]), (name, plan["manual"])


# ---------------------------------------------------------------------------
# FK — 실제로 서 있고 valid 하며, 가리킬 곳 없는 행을 거절한다
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_postgres_w2_new_foreign_keys_are_valid_and_refuse_a_row_that_points_nowhere() -> None:
    """신규 FK 넷이 **valid** 하고, 없는 곳을 가리키는 행을 데이터베이스가 거절한다.

    `work_requests.parent_task_id` 는 `use_alter` 로 나중에 붙는 FK 다 —
    `tasks.source_work_request_id` 가 이쪽을 가리켜 두 표가 서로 참조하기 때문이다. `use_alter` 를
    빼면 SQLAlchemy 가 「unresolvable cycles」로 **제약을 통째로 건너뛴다**: 그때도 `create_all` 은
    성공하므로, **성공했다는 사실이 아니라 `pg_constraint` 가** 이 단언의 근거다.
    """
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    parent = own_task(client, "상위 업무", MINA)
    sent = send_request(client, "이전 요청", "jiho", MINA)

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            expected = {
                ("work_requests", "parent_task_id"): "tasks",
                ("work_requests", "supersedes_request_id"): "work_requests",
                ("task_proposals", "task_id"): "tasks",
                ("work_request_list_entries", "work_request_id"): "work_requests",
            }
            for (table, column), target in expected.items():
                found = _foreign_key(connection, table, column)
                assert found is not None, f"{table}.{column} 의 FK 가 실제 PostgreSQL 에 없다"
                assert found[1] == target, (table, column, found)
                assert found[2] is True, f"{table}.{column} 의 FK 가 valid 하지 않다 ({found})"
            # `use_alter` 가 준 이름이 그대로 서 있다 — 이름이 다르면 롤백 절차가 그 제약을 못 찾는다.
            assert _foreign_key(connection, "work_requests", "parent_task_id")[0] == "fk_work_requests_parent_task_id"

        insert = text(
            "INSERT INTO work_requests "
            "(id, requester_id, assignee_id, title, state, version, created_at, updated_at,"
            " parent_task_id, supersedes_request_id) "
            "VALUES (:id, 'mina', 'jiho', 'FK 시험', 'pending', 1, now(), now(), :parent, :supersedes)"
        )
        # ① 없는 상위를 가리키는 하위 요청은 **거절된다.**
        with pytest.raises(IntegrityError) as orphan_parent:
            with engine.begin() as connection:
                connection.execute(
                    insert, {"id": uuid4(), "parent": uuid4(), "supersedes": None}
                )
        assert isinstance(orphan_parent.value.orig, psycopg.errors.ForeignKeyViolation)
        assert "fk_work_requests_parent_task_id" in str(orphan_parent.value.orig)

        # ② 없는 이전 요청을 가리키는 재요청도 **거절된다.**
        with pytest.raises(IntegrityError) as orphan_supersedes:
            with engine.begin() as connection:
                connection.execute(insert, {"id": uuid4(), "parent": None, "supersedes": uuid4()})
        assert isinstance(orphan_supersedes.value.orig, psycopg.errors.ForeignKeyViolation)

        # ③ 제대로 가리키는 행은 선다 — 거절이 다른 이유 때문이 아니었다는 뒷받침이다.
        standing = uuid4()
        with engine.begin() as connection:
            connection.execute(
                insert,
                {"id": standing, "parent": UUID(parent), "supersedes": UUID(sent["request_id"])},
            )
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT parent_task_id, supersedes_request_id FROM work_requests WHERE id = :id"),
                {"id": standing},
            ).first()
            assert (str(row[0]), str(row[1])) == (parent, sent["request_id"])

        # ④ 없는 업무에 붙는 제안도 거절된다.
        with pytest.raises(IntegrityError) as orphan_proposal:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO task_proposals (id, task_id, kind, state, proposed_by, task_version, created_at) "
                        "VALUES (:id, :task, 'cancellation', 'pending', 'mina', 1, now())"
                    ),
                    {"id": uuid4(), "task": uuid4()},
                )
        assert isinstance(orphan_proposal.value.orig, psycopg.errors.ForeignKeyViolation)
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 부분 유일 인덱스 — 활성 담당 하나 · 같은 종류의 대기 제안 하나
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_postgres_w2_partial_unique_indexes_refuse_the_second_row_that_matters() -> None:
    """**활성 담당은 0 또는 1**, **같은 종류의 대기 제안은 하나** — application 검사가 아니라 DB 가 막는다.

    부분 유일이라는 것이 중요하다: `active` 옆에 `pending` 이 **함께 설 수 있어야** 담당 변경 대기가
    책임 공백 없이 선다 (정책 V-18). 유일성이 상태를 가리지 않고 걸리면 그 모양이 불가능해진다.
    """
    database_url = _postgres_test_url()
    client = pg_stack(database_url)
    task_id = UUID(own_task(client, "제약을 시험할 업무", MINA))

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            for name, table in (
                ("uq_task_assignments_active", "task_assignments"),
                ("uq_task_proposals_pending_kind", "task_proposals"),
            ):
                assert name in _index_names(connection, table), name
                assert _index_validity(connection, name) == (True, True), (name, _index_validity(connection, name))
                definition = connection.scalar(
                    text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"), {"name": name}
                )
                assert "UNIQUE" in definition and "WHERE" in definition, definition

        assignment = text(
            "INSERT INTO task_assignments (id, task_id, assignee_id, assignment_kind, status, created_at) "
            "VALUES (:id, :task, :assignee, 'direct', :status, now())"
        )
        # 생성이 이미 활성 담당 하나를 세웠다 — 두 번째 활성은 거절된다.
        with pytest.raises(IntegrityError) as second_active:
            with engine.begin() as connection:
                connection.execute(
                    assignment, {"id": uuid4(), "task": task_id, "assignee": "jiho", "status": "active"}
                )
        assert isinstance(second_active.value.orig, psycopg.errors.UniqueViolation)
        assert "uq_task_assignments_active" in str(second_active.value.orig)

        # **대기 제안은 활성 옆에 선다** (V-18) — 부분 유일이 상태를 가린다.
        with engine.begin() as connection:
            connection.execute(
                assignment, {"id": uuid4(), "task": task_id, "assignee": "jiho", "status": "pending"}
            )

        proposal = text(
            "INSERT INTO task_proposals (id, task_id, kind, state, proposed_by, task_version, created_at) "
            "VALUES (:id, :task, :kind, :state, 'mina', 1, now())"
        )
        with engine.begin() as connection:
            connection.execute(
                proposal, {"id": uuid4(), "task": task_id, "kind": "cancellation", "state": "pending"}
            )
        # 같은 종류의 두 번째 대기 제안은 거절된다.
        with pytest.raises(IntegrityError) as second_pending:
            with engine.begin() as connection:
                connection.execute(
                    proposal, {"id": uuid4(), "task": task_id, "kind": "cancellation", "state": "pending"}
                )
        assert isinstance(second_pending.value.orig, psycopg.errors.UniqueViolation)
        assert "uq_task_proposals_pending_kind" in str(second_pending.value.orig)
        # 다른 종류는 함께 설 수 있고, 답이 끝난 제안은 다음 회차를 막지 않는다.
        with engine.begin() as connection:
            connection.execute(
                proposal, {"id": uuid4(), "task": task_id, "kind": "terms_change", "state": "pending"}
            )
            connection.execute(
                proposal, {"id": uuid4(), "task": task_id, "kind": "cancellation", "state": "withdrawn"}
            )

        # 요청자 목록 정리는 **사람마다 한 줄** — 두 번 눌러도 한 건이다.
        sent = send_request(client, "정리될 요청", "jiho", MINA)
        entry = text(
            "INSERT INTO work_request_list_entries (id, work_request_id, member_id, removed_at) "
            "VALUES (:id, :request, 'mina', now())"
        )
        with engine.begin() as connection:
            connection.execute(entry, {"id": uuid4(), "request": UUID(sent["request_id"])})
        with pytest.raises(IntegrityError) as twice:
            with engine.begin() as connection:
                connection.execute(entry, {"id": uuid4(), "request": UUID(sent["request_id"])})
        assert isinstance(twice.value.orig, psycopg.errors.UniqueViolation)
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# O-24 — 기존 표의 인덱스 결손은 `--sync` 로 돌아오지 않는다
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_postgres_w2_a_dropped_index_on_an_existing_table_needs_the_manual_sql_not_schema_sync() -> None:
    """인덱스 결손을 **인위로 재현하고** 닫는다 (WORK-002 Phase 1 검증 · O-24).

    ① 대상 인덱스를 내린다 → ② `schema_sync` 를 돌려도 **돌아오지 않는다**(그것이 O-24 의 재현이다)
    → ③ `2026-09-17-w2-indexes.sql`(비-CONCURRENTLY)을 트랜잭션 안에서 적용하면 선다 →
    ④ **한 번 더 적용해도 실패하지 않는다**(`IF NOT EXISTS`) → ⑤ `pg_index` 가 valid 라고 말한다.
    """
    database_url = _postgres_test_url()
    reset_database(database_url)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            before = _index_names(connection, "work_requests")
        assert set(EXISTING_TABLE_INDEXES) <= before, sorted(before)

        # ① 결손을 만든다.
        with engine.begin() as connection:
            for name in EXISTING_TABLE_INDEXES:
                connection.execute(text(f"DROP INDEX IF EXISTS {name}"))
        with engine.connect() as connection:
            assert not (set(EXISTING_TABLE_INDEXES) & _index_names(connection, "work_requests"))

        # ② `--sync` 는 **기존 표의 인덱스를 만들지 않는다.**
        made = schema_sync.apply(database_url)
        assert all(
            name not in statement for statement in made["statements"] for name in EXISTING_TABLE_INDEXES
        ), made["statements"]
        with engine.connect() as connection:
            still_missing = set(EXISTING_TABLE_INDEXES) & _index_names(connection, "work_requests")
        assert still_missing == set(), sorted(still_missing)

        # ③ 적용 단위는 레포 안의 `.sql` 이다 — 트랜잭션 안에서 돈다.
        statements = _statements(TRANSACTIONAL_SQL)
        assert statements and all(statement.upper().startswith("CREATE INDEX IF NOT EXISTS") for statement in statements)
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
        with engine.connect() as connection:
            assert set(EXISTING_TABLE_INDEXES) <= _index_names(connection, "work_requests")

        # ④ 재적용 가능하다.
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))

        # ⑤ 선 인덱스는 valid 하다 — 있다는 것과 쓸 수 있다는 것은 다른 말이다.
        with engine.connect() as connection:
            for name in EXISTING_TABLE_INDEXES:
                assert _index_validity(connection, name) == (True, True), (name, _index_validity(connection, name))
    finally:
        engine.dispose()


@pytest.mark.integration
def test_postgres_w2_the_concurrent_index_file_matches_and_cannot_run_inside_a_transaction() -> None:
    """운영 적용용 판은 **같은 정의**이고 **트랜잭션 밖에서만** 돈다 (Rollout ③-b).

    `CONCURRENTLY` 는 쓰기를 막지 않는 대신 트랜잭션 블록 안에서 돌 수 없다. 그 제약을 여기서
    **실제로 확인한다** — 문서의 문장이 아니라 PostgreSQL 의 거절이 근거다. 그리고 실패가 남기는
    INVALID 인덱스를 찾는 조회를 함께 돌려, 회복 절차가 실제로 답을 내는지 본다.

    **이것은 격리 검증이고 운영 적용이 아니다.** 운영 적용은 사람이 한다.
    """
    database_url = _postgres_test_url()
    reset_database(database_url)
    plain, concurrent = _statements(TRANSACTIONAL_SQL), _statements(CONCURRENT_SQL)
    # **두 파일의 정의가 같다** — `CONCURRENTLY` 한 낱말을 빼면 글자까지 같아야 한다.
    assert [re.sub(r"\bCONCURRENTLY\s+", "", statement) for statement in concurrent] == plain
    assert all("CONCURRENTLY" in statement for statement in concurrent)

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            for name in EXISTING_TABLE_INDEXES:
                connection.execute(text(f"DROP INDEX IF EXISTS {name}"))

        # ① 트랜잭션 **안에서는** PostgreSQL 이 거절한다.
        with pytest.raises(DBAPIError) as inside:
            with engine.begin() as connection:
                for statement in concurrent:
                    connection.execute(text(statement))
        assert isinstance(inside.value.orig, psycopg.errors.ActiveSqlTransaction), inside.value.orig
        with engine.connect() as connection:
            assert not (set(EXISTING_TABLE_INDEXES) & _index_names(connection, "work_requests"))

        # ② autocommit 으로는 돈다.
        autocommit = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        try:
            for statement in concurrent:
                autocommit.execute(text(statement))
            assert set(EXISTING_TABLE_INDEXES) <= _index_names(autocommit, "work_requests")
            # ③ 재적용해도 실패하지 않는다 (`IF NOT EXISTS`).
            for statement in concurrent:
                autocommit.execute(text(statement))
            # ④ 선 인덱스는 valid 하다 — 회복 절차가 찾는 INVALID 목록은 비어 있다.
            for name in EXISTING_TABLE_INDEXES:
                assert _index_validity(autocommit, name) == (True, True), name
            invalid = autocommit.scalars(
                text(
                    "SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                    "WHERE NOT i.indisvalid"
                )
            ).all()
            assert list(invalid) == [], invalid
        finally:
            autocommit.close()
    finally:
        engine.dispose()
