"""전달받은 자료를 열기 전에 분류한다.

Someone hands over a folder of real documents. Before any of it is read, each file is placed by what can be known
without opening it — its path, its name, its extension, its size. That ordering is the point: a file that must never
be parsed is refused by its path, so it is never opened even to decide.

Nothing is imported by default. A file becomes `import` only because a person put it on an explicit allowlist; every
unlisted document waits in `manual-review`. This module knows nothing about databases, parsers or storage — it is the
decision alone, so the decision can be tested without any of them.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatch
from pathlib import PurePosixPath
import unicodedata


class Disposition(StrEnum):
    #: Never opened, never parsed, never copied. Matched on the path alone.
    DENY = "deny"
    #: A person has to look at this before it can be imported. The default for anything not listed.
    MANUAL_REVIEW = "manual-review"
    #: Its existence, name, size and hash may be recorded; its content is not read.
    METADATA_ONLY = "metadata-only"
    #: Explicitly allowed: the content may be read through the product's own parser.
    IMPORT = "import"


#: Names that say a file carries credentials or account secrets. Matched against the path, before any read.
DENY_PATTERNS: tuple[str, ...] = (
    "*계정정보*",
    "*계정 정보*",
    "*비밀번호*",
    "*패스워드*",
    "*password*",
    "*passwd*",
    "*credential*",
    "*secret*",
    "*.env",
    "*.pem",
    "*.key",
    "*id_rsa*",
)

#: What the product can actually read. An extension outside this is metadata at most.
READABLE_SUFFIXES: frozenset[str] = frozenset({".docx", ".xlsx", ".pptx", ".pdf", ".md", ".txt", ".csv", ".html", ".htm"})

#: A file so large that reading it is its own decision rather than a default.
LARGE_FILE_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class InventoryEntry:
    """One file, placed by what its path says. `reason` is written for a person deciding what to do next."""

    path: str
    suffix: str
    size_bytes: int
    disposition: Disposition
    reason: str

    @property
    def name(self) -> str:
        return PurePosixPath(self.path).name


def normalize(path: str) -> str:
    """The same name written the same way.

    macOS stores 한글 file names decomposed (NFD) while a pattern typed anywhere else is composed (NFC). Comparing
    them raw silently fails to match — which is how a credential file first slipped past this rule — so every name and
    every pattern is composed before anything is compared.
    """
    return unicodedata.normalize("NFC", str(path))


def is_denied(path: str) -> bool:
    """Whether this path is refused outright. Content is never consulted, so this holds for an unreadable file too."""
    whole = normalize(path).lower()
    lowered = PurePosixPath(whole).name
    patterns = [normalize(pattern).lower() for pattern in DENY_PATTERNS]
    return any(fnmatch(lowered, pattern) or fnmatch(whole, pattern) for pattern in patterns)


def classify(path: str, *, size_bytes: int, allowlist: tuple[str, ...] = ()) -> InventoryEntry:
    """Place one file without opening it.

    The order is the policy: a denied name loses before anything else is considered, an unreadable format can only
    ever be metadata, and everything else waits for a person unless they already put it on the allowlist.
    """
    path = normalize(path)
    suffix = PurePosixPath(path).suffix.lower()
    if is_denied(path):
        return InventoryEntry(path, suffix, size_bytes, Disposition.DENY, "이름이 계정·비밀번호 자료임을 말합니다")
    if suffix not in READABLE_SUFFIXES:
        return InventoryEntry(path, suffix, size_bytes, Disposition.METADATA_ONLY, "제품이 읽을 수 있는 형식이 아닙니다")
    if size_bytes > LARGE_FILE_BYTES:
        return InventoryEntry(path, suffix, size_bytes, Disposition.METADATA_ONLY, "한 번에 읽기에 너무 큽니다")
    if _allowed(path, allowlist):
        return InventoryEntry(path, suffix, size_bytes, Disposition.IMPORT, "허용 목록에 사람이 올렸습니다")
    return InventoryEntry(path, suffix, size_bytes, Disposition.MANUAL_REVIEW, "아직 허용 목록에 없습니다")


def _allowed(path: str, allowlist: tuple[str, ...]) -> bool:
    whole = normalize(path)
    name = PurePosixPath(whole).name
    return any(fnmatch(whole, normalize(pattern)) or fnmatch(name, normalize(pattern)) for pattern in allowlist)


def summarize(entries: list[InventoryEntry]) -> dict[str, object]:
    """Counts a person can act on: how many wait for them, how many are refused, and what kinds are in there."""
    by_disposition: dict[str, int] = {}
    by_suffix: dict[str, int] = {}
    for entry in entries:
        by_disposition[str(entry.disposition)] = by_disposition.get(str(entry.disposition), 0) + 1
        by_suffix[entry.suffix or "(없음)"] = by_suffix.get(entry.suffix or "(없음)", 0) + 1
    return {
        "files": len(entries),
        "bytes": sum(entry.size_bytes for entry in entries),
        "by_disposition": dict(sorted(by_disposition.items())),
        "by_suffix": dict(sorted(by_suffix.items(), key=lambda item: (-item[1], item[0]))),
    }
