"""dataset CLI — 전달받은 자료를 열지 않고 살펴보고, 사람이 정한 것만 들여보낸다.

`inspect` walks a folder that lives outside this repository and reports what is in it using only what a path can say.
It writes its result next to that folder, never here: the repository owns the schema and the commands, never the data.

    uv run python -m ax_workspace.entrypoints.dataset inspect ~/Downloads/thesc
    uv run python -m ax_workspace.entrypoints.dataset inspect ~/Downloads/thesc --allow "*월간 업무보고*" --out inventory.json

`init` and `validate` shape and check a dataset without touching any database. `import` applies a dataset that
already validates, and only ever to a local demo database; it is safe to run twice.

    uv run python -m ax_workspace.entrypoints.dataset import ~/scax-dataset --password "$SCAX_DATASET_PASSWORD"

Nothing here opens a delivered file. Content only ever enters through a later command, and only for what someone has
allowed.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys

import csv

import yaml

from ax_workspace.modules.datasets.inventory import Disposition, InventoryEntry, classify, summarize
from ax_workspace.modules.datasets.schema import SCHEMA_VERSION, TABLES
from ax_workspace.modules.datasets.validation import cycles, validate

#: Noise every macOS folder carries. Not data, and not a decision anyone should have to make.
SKIP_NAMES = {".DS_Store", "Thumbs.db", "Icon\r"}


def walk(source: Path, *, allowlist: tuple[str, ...]) -> list[InventoryEntry]:
    entries: list[InventoryEntry] = []
    for path in sorted(source.rglob("*")):
        if path.is_dir() or path.name in SKIP_NAMES or path.name.startswith("._"):
            continue
        relative = path.relative_to(source).as_posix()
        # `stat` reads the directory entry, never the file's content — a denied name is still never opened.
        entries.append(classify(relative, size_bytes=path.stat().st_size, allowlist=allowlist))
    return entries


def render(source: Path, entries: list[InventoryEntry], *, show_names: bool) -> dict[str, object]:
    return {
        "source": str(source),
        "inspected_at": datetime.now(UTC).isoformat(),
        "summary": summarize(entries),
        "files": [
            {
                "path": entry.path if show_names else f"<{entry.suffix or 'no-suffix'} · {entry.size_bytes}B>",
                "suffix": entry.suffix,
                "size_bytes": entry.size_bytes,
                "disposition": str(entry.disposition),
                "reason": entry.reason,
            }
            for entry in entries
        ],
    }


def initialize(target: Path, *, name: str, as_of: str) -> None:
    """Write the empty shape of a dataset: a manifest, one CSV header per kind of fact, and a place for the files."""
    (target / "files").mkdir(parents=True, exist_ok=True)
    for table in TABLES:
        destination = target / table.filename
        if destination.exists():
            continue
        with destination.open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerow(table.header)
    manifest = target / "manifest.yaml"
    if not manifest.exists():
        manifest.write_text(
            yaml.safe_dump(
                {
                    "schema_version": SCHEMA_VERSION,
                    "name": name,
                    "as_of": as_of,
                    "tables": [table.filename for table in TABLES],
                    "files": [],
                    "notes": "원본 파일은 files/ 아래에 두고, 이 목록에 상대 경로와 sha256을 적습니다.",
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
    (target / "README.md").write_text(
        "\n".join(
            [
                f"# {name}",
                "",
                f"SCAX dataset · schema v{SCHEMA_VERSION} · 기준일 {as_of}",
                "",
                "- 표는 사람이 읽을 수 있는 key로 서로를 가리킵니다. DB 식별자는 쓰지 않습니다.",
                "- 원문에 없는 날짜는 비워 둡니다. 만들어 내지 않습니다.",
                "- 비밀번호·계정 정보는 이 폴더에 두지 않습니다.",
                "",
                "## 표",
                *[f"- `{table.filename}` — {table.note or table.name}" for table in TABLES],
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def read_tables(target: Path) -> dict[str, list[dict[str, str]]]:
    rows: dict[str, list[dict[str, str]]] = {}
    for table in TABLES:
        source = target / table.filename
        if not source.exists():
            continue
        with source.open(encoding="utf-8-sig", newline="") as handle:
            rows[table.name] = [dict(row) for row in csv.DictReader(handle)]
    return rows


def apply_dataset(target: Path, rows: dict[str, list[dict[str, str]]], *, password: str | None) -> int:
    """Write the dataset into the local demo database, all of it or none of it.

    The import is only ever pointed at a database this repository is willing to reset, and it is one transaction:
    a dataset that turns out to be unapplicable leaves nothing behind for someone to clean up by hand.
    """
    from ax_workspace.bootstrap.dataset_import import DatasetImportError, import_into
    from ax_workspace.bootstrap.settings import Settings
    from ax_workspace.entrypoints.reset_demo import require_safe_demo_database

    settings = Settings.from_environment()
    if not settings.developer_auth_enabled:
        print("dataset import는 개발·테스트 프로파일에서만 씁니다", file=sys.stderr)
        return 2
    try:
        require_safe_demo_database(settings.database_url)
    except ValueError:
        print(f"이 데이터베이스에는 넣지 않습니다: {settings.database_url}", file=sys.stderr)
        return 2

    try:
        result = import_into(settings.database_url, rows, password=password)
    except DatasetImportError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps({"dataset": str(target), **result.as_dict()}, ensure_ascii=False, indent=2))
    for note in result.skipped:
        print(f"\n하지 않은 것: {note}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dataset", description="Look at delivered material without opening it.")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="classify a delivered folder by path alone")
    inspect.add_argument("source", type=Path)
    inspect.add_argument("--allow", action="append", default=[], help="glob a person has approved for import")
    inspect.add_argument("--out", type=Path, help="write the inventory here (defaults beside the source folder)")
    inspect.add_argument("--hide-names", action="store_true", help="report shapes only, without file names")

    start = commands.add_parser("init", help="create an empty dataset outside this repository")
    start.add_argument("target", type=Path)
    start.add_argument("--name", default="scax-dataset")
    start.add_argument("--as-of", default=datetime.now(UTC).date().isoformat())

    check = commands.add_parser("validate", help="check a dataset without touching any database")
    check.add_argument("target", type=Path)

    apply = commands.add_parser("import", help="apply a validated dataset to the local demo database")
    apply.add_argument("target", type=Path)
    apply.add_argument(
        "--password",
        default=os.environ.get("SCAX_DATASET_PASSWORD"),
        help="the one local password every imported login gets; without it no login is made",
    )

    arguments = parser.parse_args(argv)
    repository = Path(__file__).resolve().parents[4]

    if arguments.command in {"init", "validate", "import"}:
        target = arguments.target.expanduser().resolve()
        if target.is_relative_to(repository):
            print(f"dataset은 저장소 밖에 두어야 합니다: {target}", file=sys.stderr)
            return 2
        if arguments.command == "init":
            target.mkdir(parents=True, exist_ok=True)
            initialize(target, name=arguments.name, as_of=arguments.as_of)
            print(json.dumps({"dataset": str(target), "schema_version": SCHEMA_VERSION, "tables": len(TABLES)}, ensure_ascii=False, indent=2))
            return 0
        if not target.is_dir():
            print(f"dataset 폴더가 없습니다: {target}", file=sys.stderr)
            return 2
        rows = read_tables(target)
        report = validate(rows)
        looping = cycles(rows.get("organization_units", []), key="key", parent="parent_key")
        problems = [str(problem) for problem in report.problems]
        problems.extend(f"organization_units · {key} 가 자기 아래에 들어갑니다" for key in looping)
        if arguments.command == "validate":
            print(
                json.dumps(
                    {"dataset": str(target), "rows": report.counts, "problems": problems[:200], "problem_count": len(problems)},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0 if not problems else 1
        # 검사를 통과하지 못한 dataset은 절반만 들어가지 않는다. 하나도 쓰지 않고 멈춘다.
        if problems:
            print(f"먼저 dataset을 고쳐야 합니다. {len(problems)}건:", file=sys.stderr)
            for problem in problems[:20]:
                print(f"  - {problem}", file=sys.stderr)
            return 1
        return apply_dataset(target, rows, password=arguments.password)

    source = arguments.source.expanduser().resolve()
    if not source.is_dir():
        print(f"살펴볼 폴더가 없습니다: {source}", file=sys.stderr)
        return 2
    repository = Path(__file__).resolve().parents[4]
    if source.is_relative_to(repository):
        # The repository owns code, never delivered material.
        print(f"전달받은 자료는 저장소 밖에 두어야 합니다: {source}", file=sys.stderr)
        return 2

    entries = walk(source, allowlist=tuple(arguments.allow))
    report = render(source, entries, show_names=not arguments.hide_names)
    destination = (arguments.out or source.parent / f"{source.name}-inventory.json").expanduser().resolve()
    if destination.is_relative_to(repository):
        print(f"목록도 저장소 밖에 써야 합니다: {destination}", file=sys.stderr)
        return 2
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = report["summary"]
    print(json.dumps({"source": str(source), **summary, "inventory": str(destination)}, ensure_ascii=False, indent=2))
    waiting = summary["by_disposition"].get(str(Disposition.MANUAL_REVIEW), 0)  # type: ignore[union-attr]
    if waiting:
        print(f"\n{waiting}개가 사람의 판단을 기다립니다. --allow 로 하나씩 올리면 그때부터 읽을 수 있습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
