"""로컬 demo DB를 현재 모델에 맞춘다 — 지우지 않고, 더하기만.

This session applied several additive DDL changes to the local demo database by hand so a running stack could keep
its data. That is not repeatable, so this makes it so: it creates tables the model has and the database does not,
and adds columns the same way. It never drops or retypes anything — a change that would lose data is printed as a
decision for a person, not performed. It is a development convenience, not a migration tool: production schema
change is a separate, gated piece of work.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.schema import Column, CreateColumn, CreateIndex, CreateTable

from ax_workspace.platform.persistence import Base


def plan(database_url: str) -> dict[str, list[str]]:
    """What would be added, and what a person has to decide. Nothing is executed here."""
    engine = create_engine(database_url)
    inspector = inspect(engine)
    live_tables = set(inspector.get_table_names())
    statements: list[str] = []
    manual: list[str] = []

    for name, table in Base.metadata.tables.items():
        if name not in live_tables:
            statements.append(str(CreateTable(table).compile(engine)).strip() + ";")
            statements.extend(str(CreateIndex(index).compile(engine)).strip() + ";" for index in table.indexes)
            continue
        live_columns = {column["name"]: column for column in inspector.get_columns(name)}
        for column in table.columns:
            if column.name in live_columns:
                continue
            if not column.nullable and column.server_default is None:
                # NOT NULL 컬럼은 **기존 행에 넣을 값**이 DDL 에 있어야 한다. `server_default` 가 그 값이고,
                # 파이썬 쪽 `default=` 는 아니다 — 그것은 ORM 이 INSERT 할 때만 쓰이므로 이미 있는 행에
                # 닿지 않는다. 값이 없으면 **사람이 정한다**: 조용히 nullable 로 붙이면 기존 행이 전부
                # NULL 이 되고, 그 컬럼으로 거르는 모든 조회에서 그 행들이 사라진다.
                manual.append(f"{name}.{column.name}: NOT NULL 컬럼은 기존 행의 값을 사람이 정해야 합니다")
                continue
            statements.append(f"ALTER TABLE {name} ADD COLUMN {_column_spec(column, engine)};")
        for name_in_db in set(live_columns) - {column.name for column in table.columns}:
            manual.append(f"{name}.{name_in_db}: 모델에 없는 컬럼입니다 (삭제는 사람이 결정)")
    engine.dispose()
    return {"statements": statements, "manual": manual}


def _column_spec(column: Column, engine: Any) -> str:
    """`ADD COLUMN` 이 실을 컬럼 정의 — **모델의 제약을 그대로 싣는다.**

    타입만 싣고 `DEFAULT` 와 `NOT NULL` 을 빼면 기존 행이 전부 NULL 이 된다. 그러면 그 컬럼으로
    거르는 조회에서 이미 쌓인 행이 통째로 사라진다 — 실물에서 안건 52건이 그렇게 될 뻔했다.
    **기본값이 있는 NOT NULL 컬럼을 더하면 기존 행이 그 값으로 찬다**(postgres·sqlite 둘 다), 그래서
    이 한 자리가 「기존 행을 어떻게 하나」를 통째로 없앤다.

    손으로 조립하지 않고 **SQLAlchemy 자신의 컬럼 정의 컴파일러**를 쓴다 — `CreateTable` 이 내는 것과
    같은 글자이고, 기본값을 어떻게 인용하는지도 dialect 가 안다.
    """
    return str(CreateColumn(column).compile(engine)).strip()


def apply(database_url: str) -> dict[str, Any]:
    made = plan(database_url)
    if made["statements"]:
        engine = create_engine(database_url)
        with engine.begin() as connection:
            for statement in made["statements"]:
                connection.execute(text(statement))
        engine.dispose()
    return made
