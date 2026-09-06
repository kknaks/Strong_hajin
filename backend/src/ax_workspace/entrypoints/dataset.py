"""dataset CLI — 전달받은 자료를 열지 않고 살펴보고, 사람이 정한 것만 들여보낸다.

`inspect` walks a folder that lives outside this repository and reports what is in it using only what a path can say.
It writes its result next to that folder, never here: the repository owns the schema and the commands, never the data.

    uv run python -m ax_workspace.entrypoints.dataset inspect ~/Downloads/thesc
    uv run python -m ax_workspace.entrypoints.dataset inspect ~/Downloads/thesc --allow "*월간 업무보고*" --out inventory.json

Nothing here opens a file. Content only ever enters through a later command, and only for what someone has allowed.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys

from ax_workspace.modules.datasets.inventory import Disposition, InventoryEntry, classify, summarize

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dataset", description="Look at delivered material without opening it.")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="classify a delivered folder by path alone")
    inspect.add_argument("source", type=Path)
    inspect.add_argument("--allow", action="append", default=[], help="glob a person has approved for import")
    inspect.add_argument("--out", type=Path, help="write the inventory here (defaults beside the source folder)")
    inspect.add_argument("--hide-names", action="store_true", help="report shapes only, without file names")
    arguments = parser.parse_args(argv)

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
