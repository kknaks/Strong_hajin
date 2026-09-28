"""후행 역방향 조회의 인덱스를 **실제 PostgreSQL 에서** 센다 (WORK-007 Phase B-1 · SPEC-007 §4).

**무엇을 증명하는가.** 모델 선언이나 `create_all` 이 성공했다는 사실을 근거로 삼지 않는다 — 살아 있는
PostgreSQL 의 `pg_indexes` · `pg_index` 에 그 인덱스가 **실제로 서 있고 valid 한지**를 묻고,
**결손을 인위로 재현한 뒤 적용 단위(.sql)로 닫는 것**을 한 건씩 남긴다.

**OQ-703 의 답이 여기 있다.** `schema_sync`(= `make sync-demo-schema`)는 **없는 표를 만들고 없는 컬럼을
더할 뿐 기존 표에 인덱스를 더하지 않는다**(BASE-002 O-24 · `bootstrap/schema_sync.py` 가 `CreateIndex`
를 「없는 표」 갈래에서만 낸다). `task_predecessors` 는 이미 사는 표이므로 이 인덱스는 **자동으로
생기지 않는다.** 그래서 적용 단위가 레포 안의 `.sql` 둘이고, W2 가 세운 그 선례를 그대로 쓴다.

**SQL 파일은 고치지 않는다.** 이 파일은 그 둘을 **적용하고 검증할 뿐**이다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from ax_workspace.bootstrap import schema_sync
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import Base

from test_postgres_integration import _postgres_test_url

#: Phase B-1 이 남긴 적용 단위 둘. **이 파일들이 증거다** — 여기서 고치지 않는다.
MANUAL_SQL = Path(__file__).resolve().parents[3] / "migrations" / "manual"
TRANSACTIONAL_SQL = MANUAL_SQL / "2026-09-28-w7-successor-index.sql"
CONCURRENT_SQL = MANUAL_SQL / "2026-09-28-w7-successor-index.concurrent.sql"

#: 기존 표(`task_predecessors`)에 더하는 인덱스 — `--sync` 가 만들지 않는 자리 (O-24).
SUCCESSOR_INDEX = "ix_task_predecessors_predecessor_task_id"


def _statements(path: Path) -> list[str]:
    """주석을 걷고 `;` 로 가른 실제 DDL 만."""
    body = "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("--")
    )
    return [" ".join(part.split()) for part in body.split(";") if part.strip()]


def _index_names(connection, table: str) -> set[str]:
    return set(
        connection.scalars(
            text("SELECT indexname FROM pg_indexes WHERE tablename = :table"), {"table": table}
        ).all()
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


def test_the_model_declares_the_index_and_adds_no_table() -> None:
    """**새 표 0건 · 새 컬럼 0건 · 새 인덱스 1건** — metadata 가 그 SoT 다 (WORK-007 § Domain/Schema).

    PostgreSQL 이 없어도 도는 선언 검사다. 아래 두 테스트가 살아 있는 DB 에서 같은 사실을 다시 센다.
    """
    table = Base.metadata.tables["task_predecessors"]
    names = {index.name for index in table.indexes}
    assert SUCCESSOR_INDEX in names, sorted(names)
    # 거는 열이 **후행 쪽**이다 — `task_id` 인덱스와 다른 질문에 답한다.
    index = next(item for item in table.indexes if item.name == SUCCESSOR_INDEX)
    assert [column.name for column in index.columns] == ["predecessor_task_id"]
    assert not index.unique

    # **새 표가 0건이다** — 후행은 저장되지 않으므로 그 이름을 가진 표가 metadata 에 없다.
    assert not [name for name in Base.metadata.tables if "successor" in name], sorted(Base.metadata.tables)
    # **새 컬럼도 0건이다** — `task_predecessors` 의 열은 앞판 그대로다.
    assert sorted(column.name for column in table.columns) == [
        "created_at", "created_by", "id", "position", "predecessor_task_id",
        "released_at", "released_by", "task_id",
    ]


@pytest.mark.integration
def test_postgres_w7_the_successor_index_stands_and_is_valid() -> None:
    """새로 만든 DB 는 `create_all` 이 이 인덱스를 함께 세운다 — `pg_index` 가 valid 라고 말한다."""
    database_url = _postgres_test_url()
    reset_database(database_url)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert SUCCESSOR_INDEX in _index_names(connection, "task_predecessors")
            assert _index_validity(connection, SUCCESSOR_INDEX) == (True, True)
    finally:
        engine.dispose()


@pytest.mark.integration
def test_postgres_w7_a_missing_index_on_this_existing_table_needs_the_manual_sql_not_schema_sync() -> None:
    """**OQ-703 의 답을 재현해 닫는다** (WORK-007 Phase B-1 첫 작업).

    ① 인덱스를 내린다 → ② `schema_sync` 를 돌려도 **돌아오지 않는다**(그것이 O-24 이고, 이 판이
    `schema_sync` 를 **고치지 않기로** 한 근거다 — 그 한계를 고정한 계약 테스트가 이미 있다) →
    ③ `2026-09-28-w7-successor-index.sql`(비-CONCURRENTLY)을 트랜잭션 안에서 적용하면 선다 →
    ④ **한 번 더 적용해도 실패하지 않는다**(`IF NOT EXISTS`) → ⑤ `pg_index` 가 valid 라고 말한다.
    """
    database_url = _postgres_test_url()
    reset_database(database_url)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert SUCCESSOR_INDEX in _index_names(connection, "task_predecessors")

        # ① 결손을 만든다.
        with engine.begin() as connection:
            connection.execute(text(f"DROP INDEX IF EXISTS {SUCCESSOR_INDEX}"))
        with engine.connect() as connection:
            assert SUCCESSOR_INDEX not in _index_names(connection, "task_predecessors")

        # ② `--sync` 는 **기존 표의 인덱스를 만들지 않는다.**
        made = schema_sync.apply(database_url)
        assert all(SUCCESSOR_INDEX not in statement for statement in made["statements"]), made["statements"]
        with engine.connect() as connection:
            assert SUCCESSOR_INDEX not in _index_names(connection, "task_predecessors")

        # ③ 적용 단위는 레포 안의 `.sql` 이다 — 트랜잭션 안에서 돈다.
        statements = _statements(TRANSACTIONAL_SQL)
        assert statements and all(
            statement.upper().startswith("CREATE INDEX IF NOT EXISTS") for statement in statements
        ), statements
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
        with engine.connect() as connection:
            assert SUCCESSOR_INDEX in _index_names(connection, "task_predecessors")

        # ④ 재적용 가능하다.
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))

        # ⑤ valid 하다.
        with engine.connect() as connection:
            assert _index_validity(connection, SUCCESSOR_INDEX) == (True, True)
    finally:
        engine.dispose()


def test_the_two_manual_sql_files_declare_the_same_index() -> None:
    """**두 판의 정의가 같아야 한다** — 운영판만 `CONCURRENTLY` 가 붙는다 (W2 선례 그대로).

    PostgreSQL 없이 도는 텍스트 검사다. 정의가 갈리면 격리에서 검증한 것과 운영에 서는 것이 달라진다.
    """
    transactional = _statements(TRANSACTIONAL_SQL)
    concurrent = _statements(CONCURRENT_SQL)
    assert len(transactional) == len(concurrent) == 1
    assert concurrent[0].upper().startswith("CREATE INDEX CONCURRENTLY IF NOT EXISTS")
    # `CONCURRENTLY` 한 낱말만 빼면 같은 문장이다.
    assert concurrent[0].replace("CONCURRENTLY ", "", 1) == transactional[0]
    assert SUCCESSOR_INDEX in transactional[0]
