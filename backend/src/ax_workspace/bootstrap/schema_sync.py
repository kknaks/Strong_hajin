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
from sqlalchemy.schema import CreateIndex, CreateTable

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
            ddl = f"ALTER TABLE {name} ADD COLUMN {column.name} {column.type.compile(engine.dialect)}"
            if not column.nullable and column.default is None and column.server_default is None:
                # A NOT NULL column on an existing table needs a value for the rows already there.
                manual.append(f"{name}.{column.name}: NOT NULL 컬럼은 기존 행의 값을 사람이 정해야 합니다")
                continue
            statements.append(ddl + ";")
        for name_in_db in set(live_columns) - {column.name for column in table.columns}:
            manual.append(f"{name}.{name_in_db}: 모델에 없는 컬럼입니다 (삭제는 사람이 결정)")
    engine.dispose()
    return {"statements": statements, "manual": manual}


def apply(database_url: str) -> dict[str, Any]:
    made = plan(database_url)
    if made["statements"]:
        engine = create_engine(database_url)
        with engine.begin() as connection:
            for statement in made["statements"]:
                connection.execute(text(statement))
        engine.dispose()
    return made
