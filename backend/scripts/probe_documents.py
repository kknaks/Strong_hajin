"""Read-only compatibility probe for a local directory of real documents.

Prints one line per file with name, size, format, status, block/page counts, and warning kinds. It never prints,
stores, or copies document text. Files whose name matches an exclusion pattern (default: 계정정보) are skipped.

    uv run python scripts/probe_documents.py /path/to/dir [--exclude 계정정보 --exclude secret]
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys
import unicodedata

from ax_workspace.modules.work.document_parsing import STATUS_OK, classify_document
from ax_workspace.platform.document_parsers import OfficeDocumentParser


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("directory", type=Path)
    parser.add_argument("--exclude", action="append", default=["계정정보"], help="skip files whose name contains this text")
    args = parser.parse_args()
    root: Path = args.directory
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2
    engine = OfficeDocumentParser()
    summary: Counter[tuple[str, str]] = Counter()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.name.startswith(".")):
        # macOS stores names in NFD; normalize both sides so a Korean exclusion token always matches.
        normalized_name = unicodedata.normalize("NFC", path.name)
        if any(unicodedata.normalize("NFC", token) in normalized_name for token in args.exclude):
            print(f"SKIP      {path.relative_to(root)}  (excluded by name)")
            continue
        fmt = classify_document(path.name, "")
        if fmt is None:
            summary[("-", "not_a_document")] += 1
            print(f"IGNORE    {path.relative_to(root)}  ({path.suffix.lower() or 'no suffix'})")
            continue
        data = path.read_bytes()  # read-only; bytes never leave this process
        result = engine.parse(name=path.name, content_type="", data=data)
        summary[(fmt, result.status)] += 1
        counts = []
        if result.page_count is not None:
            counts.append(f"pages={result.page_count}")
        if result.sheet_count is not None:
            counts.append(f"sheets={result.sheet_count}")
        if result.slide_count is not None:
            counts.append(f"slides={result.slide_count}")
        counts.append(f"blocks={len(result.blocks)}")
        counts.append(f"chars={result.char_count}")
        if result.truncated:
            counts.append("truncated")
        warning_kinds = sorted({w.split(":")[0] for w in result.warnings})
        marker = "OK  " if result.status == STATUS_OK else "STAT"
        print(f"{marker} {fmt:<4} {result.status:<15} {path.stat().st_size // 1024:>6}KB  {path.relative_to(root)}  {' '.join(counts)}" + (f"  warnings={warning_kinds}" if warning_kinds else "") + (f"  detail={result.detail}" if result.detail else ""))
    print("\nsummary:")
    for (fmt, status), count in sorted(summary.items()):
        print(f"  {fmt:<4} {status:<15} {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
