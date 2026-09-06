"""dataset이 스스로 말이 되는지 확인한다 — 값은 보이지 않고, 어디가 문제인지만 말한다.

Validation reads the person's own CSVs, never the delivered originals. What it reports is where a problem is — the
file, the row, the column — and what kind of problem it is. It does not print the cell, because the cell may be
someone's name, a client, a number nobody asked to see on a terminal.

Nothing here touches a database. A dataset that passes this is still only a dataset; importing it is a separate act.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ax_workspace.modules.datasets.schema import TABLES, TABLES_BY_NAME, Table


@dataclass(frozen=True, slots=True)
class Problem:
    table: str
    row: int | None
    column: str | None
    kind: str
    detail: str

    def __str__(self) -> str:
        place = f"{self.table}.csv"
        if self.row is not None:
            place += f":{self.row}"
        if self.column:
            place += f" [{self.column}]"
        return f"{place} · {self.detail}"


@dataclass(frozen=True, slots=True)
class Report:
    counts: dict[str, int]
    problems: list[Problem]

    @property
    def ok(self) -> bool:
        return not self.problems


def validate(rows_by_table: dict[str, list[dict[str, str]]]) -> Report:
    """Check a whole dataset at once: shape, keys, references, enums and dates."""
    problems: list[Problem] = []
    counts = {table.name: len(rows_by_table.get(table.name, [])) for table in TABLES}
    keys: dict[str, set[str]] = {}

    for table in TABLES:
        rows = rows_by_table.get(table.name)
        if rows is None:
            problems.append(Problem(table.name, None, None, "missing_file", "이 표가 없습니다"))
            continue
        keys[table.name] = _collect_keys(table, rows, problems)

    for table in TABLES:
        for index, row in enumerate(rows_by_table.get(table.name, []), start=2):  # row 1 is the header
            _check_row(table, index, row, keys, problems)

    return Report(counts, problems)


def _collect_keys(table: Table, rows: list[dict[str, str]], problems: list[Problem]) -> set[str]:
    seen: set[str] = set()
    if table.key is None:
        pairs: set[tuple[str, ...]] = set()
        for index, row in enumerate(rows, start=2):
            pair = tuple((row.get(column) or "").strip() for column in table.unique)
            if all(pair) and pair in pairs:
                problems.append(Problem(table.name, index, ", ".join(table.unique), "duplicate", "같은 조합이 이미 있습니다"))
            pairs.add(pair)
        return seen
    for index, row in enumerate(rows, start=2):
        value = (row.get(table.key) or "").strip()
        if not value:
            continue
        if value in seen:
            problems.append(Problem(table.name, index, table.key, "duplicate", "같은 키가 이미 있습니다"))
        seen.add(value)
    return seen


def _check_row(table: Table, index: int, row: dict[str, str], keys: dict[str, set[str]], problems: list[Problem]) -> None:
    for column in table.columns:
        raw = (row.get(column.name) or "").strip()
        if not raw:
            if column.required:
                problems.append(Problem(table.name, index, column.name, "missing_value", "값이 필요합니다"))
            continue
        if column.kind == "enum" and raw not in column.values:
            problems.append(
                Problem(table.name, index, column.name, "unknown_value", f"허용된 값이 아닙니다 ({', '.join(column.values)})")
            )
        if column.kind == "int" and not raw.lstrip("-").isdigit():
            problems.append(Problem(table.name, index, column.name, "not_a_number", "숫자가 아닙니다"))
        if column.kind == "date":
            try:
                date.fromisoformat(raw)
            except ValueError:
                problems.append(Problem(table.name, index, column.name, "not_a_date", "YYYY-MM-DD 형식이 아닙니다"))
        if column.references:
            known = keys.get(column.references, set())
            if raw not in known:
                problems.append(
                    Problem(table.name, index, column.name, "unknown_reference", f"{column.references}에 없는 키를 가리킵니다")
                )
    unknown = set(row) - {column.name for column in table.columns} - {""}
    for extra in sorted(name for name in unknown if name):
        problems.append(Problem(table.name, index, extra, "unknown_column", "이 표에 없는 열입니다"))


def cycles(rows: list[dict[str, str]], *, key: str, parent: str) -> list[str]:
    """Keys that end up under themselves. An organization that contains itself is a mistake, not a structure."""
    parents = {(row.get(key) or "").strip(): (row.get(parent) or "").strip() for row in rows}
    looping: list[str] = []
    for start in parents:
        seen: set[str] = set()
        current = start
        while current:
            if current in seen:
                looping.append(start)
                break
            seen.add(current)
            current = parents.get(current, "")
    return sorted(set(looping))


def table_of(name: str) -> Table:
    return TABLES_BY_NAME[name]
