"""Document parser port: DOCX / XLSX / PPTX / PDF -> bounded, ordered `ParsedDocument`.

This is a pure application contract. A `ParsedDocument` is not a persistence entity and is never written to the
material chunk projection by this module; where and at what granularity parser output is stored is a separate,
user-gated data-management decision. Adapters must never execute macros or formulas, follow external links, or
touch the network.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable, Protocol

# ---- supported formats -------------------------------------------------------------------------------------------

FORMAT_DOCX = "docx"
FORMAT_XLSX = "xlsx"
FORMAT_PPTX = "pptx"
FORMAT_PDF = "pdf"

_EXTENSIONS = {
    ".docx": FORMAT_DOCX,
    ".docm": FORMAT_DOCX,
    ".xlsx": FORMAT_XLSX,
    ".xlsm": FORMAT_XLSX,
    ".pptx": FORMAT_PPTX,
    ".pptm": FORMAT_PPTX,
    ".pdf": FORMAT_PDF,
}
_MEDIA_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": FORMAT_DOCX,
    "application/vnd.ms-word.document.macroenabled.12": FORMAT_DOCX,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": FORMAT_XLSX,
    "application/vnd.ms-excel.sheet.macroenabled.12": FORMAT_XLSX,
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": FORMAT_PPTX,
    "application/vnd.ms-powerpoint.presentation.macroenabled.12": FORMAT_PPTX,
    "application/pdf": FORMAT_PDF,
}


def classify_document(name: str, content_type: str) -> str | None:
    """Pick the parser format from the declared name/MIME; None means "not an Office/PDF document"."""
    lowered = name.lower()
    for extension, fmt in _EXTENSIONS.items():
        if lowered.endswith(extension):
            return fmt
    media = (content_type or "").split(";")[0].strip().lower()
    return _MEDIA_TYPES.get(media)


# ---- statuses --------------------------------------------------------------------------------------------------------

STATUS_OK = "ok"
STATUS_EMPTY = "empty"  # opened fine, contains no text (and, for PDF, no images either)
STATUS_NEEDS_OCR = "needs_ocr"  # PDF pages carry images but no text layer; must not be shown as read
STATUS_UNSUPPORTED = "unsupported"  # legacy binary Office, unknown format
STATUS_ENCRYPTED = "encrypted"  # password-protected package; not opened
STATUS_CORRUPT = "corrupt"  # not a readable container / malformed parts
STATUS_BUDGET_EXCEEDED = "budget_exceeded"  # preflight refused the archive (zip bomb, oversize part, too many members)

TERMINAL_FAILURE_STATUSES = frozenset({STATUS_UNSUPPORTED, STATUS_ENCRYPTED, STATUS_CORRUPT, STATUS_BUDGET_EXCEEDED})


@dataclass(frozen=True, slots=True)
class ParseBudget:
    """Hard limits applied before and during parsing. Exceeding a preflight limit refuses the file; exceeding an
    output limit truncates with a warning."""

    max_archive_members: int = 4_000
    max_member_bytes: int = 64 * 1024 * 1024
    max_total_uncompressed_bytes: int = 256 * 1024 * 1024
    max_compression_ratio: int = 200
    max_pdf_content_bytes: int = 8 * 1024 * 1024
    max_pdf_total_content_bytes: int = 64 * 1024 * 1024
    max_pdf_form_invocations: int = 4_000
    max_pdf_form_depth: int = 32
    max_blocks: int | None = None
    max_chars: int | None = None
    max_block_chars: int | None = None
    max_rows_per_sheet: int | None = None
    max_cells_per_row: int | None = None
    max_pdf_pages: int | None = None


@dataclass(frozen=True, slots=True)
class SourceLocator:
    """Where a block came from, in the source document's own coordinates. `label` is the human-readable form."""

    kind: str  # paragraph | table | header | footer | sheet_row | slide | notes | page
    page: int | None = None
    sheet: str | None = None
    cell_range: str | None = None
    slide: int | None = None
    index: int | None = None  # ordinal within its kind (paragraph #, table #, section #)

    variant: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    column_start: int | None = None
    column_end: int | None = None
    container: str | None = None
    section: int | None = None

    def as_dict(self) -> dict:
        return {key: value for key, value in asdict(self).items() if value is not None}

    @property
    def label(self) -> str:
        if self.kind == "page":
            return f"{self.page}쪽"
        if self.kind == "sheet_row":
            return f"{self.sheet}!{self.cell_range}"
        if self.kind == "slide":
            return f"슬라이드 {self.slide}"
        if self.kind == "notes":
            return f"슬라이드 {self.slide} 발표자 노트"
        if self.kind == "table":
            prefix = f"슬라이드 {self.slide} " if self.slide else (f"{self.container} {self.section} " if self.container else "")
            region = f", {self.row_start}–{self.row_end}행 {self.column_start}–{self.column_end}열" if self.row_start is not None else ""
            return f"{prefix}표 {self.index}{region}"
        if self.kind in {"header", "footer"}:
            return f"{'머리글' if self.kind == 'header' else '바닥글'} {self.index}" + (f" ({self.variant})" if self.variant else "")
        return f"문단 {self.index}"


@dataclass(frozen=True, slots=True)
class ParsedBlock:
    order: int
    kind: str  # paragraph | table | header | footer | sheet_row | slide_text | slide_table | notes | page
    text: str
    locator: SourceLocator
    warnings: tuple[str, ...] = ()
    header_context: dict | None = None


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    format: str | None
    status: str
    blocks: tuple[ParsedBlock, ...] = ()
    warnings: tuple[str, ...] = ()
    page_count: int | None = None
    sheet_count: int | None = None
    slide_count: int | None = None
    truncated: bool = False
    detail: str | None = None  # explainable reason for a failure status; never raw parser output

    coverage: dict = field(default_factory=dict)

    @property
    def char_count(self) -> int:
        return sum(len(block.text) for block in self.blocks)

    @property
    def readable(self) -> bool:
        return self.status == STATUS_OK and bool(self.blocks)


class DocumentParser(Protocol):
    def supports(self, *, name: str, content_type: str) -> str | None: ...

    def parse(self, *, name: str, content_type: str, data: bytes, sink: Callable[[ParsedBlock], None] | None = None) -> ParsedDocument: ...


# ---- shared helpers for adapters (pure) --------------------------------------------------------------------------


class ProjectionWriteError(Exception):
    """Output storage failed; this says nothing about whether the source document is valid."""


@dataclass
class BlockCollector:
    """Accumulates blocks under the budget; records truncation instead of raising."""

    budget: ParseBudget
    blocks: list[ParsedBlock] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    chars: int = 0
    truncated: bool = False
    sink: Callable[[ParsedBlock], None] | None = None
    count: int = 0

    def add(self, kind: str, text: str, locator: SourceLocator, *warnings: str, header_context: dict | None = None) -> bool:
        """Returns False once the budget is exhausted; callers stop iterating."""
        if self.truncated:
            return False
        cleaned = " ".join(text.split()) if kind in {"sheet_row", "table", "slide_table"} else text.strip()
        if not cleaned:
            return True
        if (self.budget.max_blocks is not None and self.count >= self.budget.max_blocks) or (self.budget.max_chars is not None and self.chars >= self.budget.max_chars):
            self.truncated = True
            self.warn("output truncated at budget")
            return False
        if self.budget.max_block_chars is not None and len(cleaned) > self.budget.max_block_chars:
            self.truncated = True
            cleaned = cleaned[: self.budget.max_block_chars]
            warnings = (*warnings, "block truncated")
        remaining = self.budget.max_chars - self.chars if self.budget.max_chars is not None else len(cleaned)
        if len(cleaned) > remaining:
            cleaned = cleaned[:remaining]
            warnings = (*warnings, "block truncated")
            self.truncated = True
        for warning in warnings:
            self.warn(warning)
        block = ParsedBlock(self.count, kind, cleaned, locator, tuple(warnings), header_context)
        if self.sink is None:
            self.blocks.append(block)
        else:
            try:
                self.sink(block)
            except Exception as error:
                raise ProjectionWriteError("document output could not be stored") from error
        self.count += 1
        self.chars += len(cleaned)
        return not self.truncated

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)
