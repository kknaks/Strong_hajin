"""Material content extraction and authorized search.

An Attachment stays the artifact of record; extraction output is a versioned derived projection keyed by the
attachment's integrity hash. File text is untrusted evidence: it is chunked, bounded, and only excerpts that the
caller is authorized to see leave this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Protocol
from uuid import UUID

from ax_workspace.modules.work.search import folded

SUPPORTED_EXTRACTORS = ("text", "markdown", "pdf")
MAX_TEXT_CHARS = 200_000
CHUNK_CHARS = 1_000
CHUNK_OVERLAP = 120
MAX_CHUNKS = 400
EXCERPT_CHARS = 280
MAX_SEARCH_HITS = 8

# status: queued -> running -> completed | failed | unsupported
FAILURE_REASONS = {
    "empty_content": "본문에 텍스트가 없습니다 (스캔 이미지 PDF 또는 빈 파일)",
    "encrypted_pdf": "암호가 걸린 PDF는 읽을 수 없습니다",
    "corrupt_pdf": "손상된 PDF 파일입니다",
    "not_utf8_text": "UTF-8 텍스트가 아닙니다",
    "extractor_error": "추출 중 오류가 반복되어 중단했습니다",
    "unsupported_format": "지원하지 않는 형식입니다 (UTF-8 텍스트·Markdown·PDF·DOCX·XLSX·PPTX만 지원)",
    "needs_ocr": "스캔 문서로 보입니다 — 텍스트 층이 없어 내용을 읽을 수 없습니다 (OCR 미도입)",
    "encrypted_document": "암호가 걸린 문서는 읽을 수 없습니다",
    "corrupt_document": "손상된 문서 파일입니다",
    "budget_exceeded": "문서가 너무 크거나 구조가 안전하지 않아 읽지 않았습니다",
}


@dataclass(frozen=True, slots=True)
class ExtractedChunk:
    sequence: int
    text: str
    char_start: int
    char_end: int
    page: int | None = None
    #: Which block this span came from, so a hit can name the part of the document a person would recognise.
    block_sequence: int | None = None


@dataclass(frozen=True, slots=True)
class ExtractedBlock:
    """One part of the document as it is actually shaped. Chunks are derived from these, never the other way round."""

    sequence: int
    kind: str
    text: str
    locator_label: str | None = None
    page: int | None = None
    sheet: str | None = None
    slide: int | None = None
    row: int | None = None


@dataclass(frozen=True, slots=True)
class ExtractionOutcome:
    status: str  # completed | failed | unsupported | needs_ocr
    extractor: str | None
    chunks: tuple[ExtractedChunk, ...] = ()
    blocks: tuple[ExtractedBlock, ...] = ()
    failure_reason: str | None = None
    char_count: int = 0
    page_count: int | None = None
    transient: bool = False


class MaterialTextExtractor(Protocol):
    def extract(self, *, name: str, content_type: str, data: bytes) -> ExtractionOutcome: ...


@dataclass(frozen=True, slots=True)
class MaterialExtractionJob:
    extraction_id: UUID
    attachment_id: UUID


class MaterialExtractionQueue(Protocol):
    """Enqueue-only port used in the upload transaction; delivery is the shared DurableJobQueue."""

    def enqueue(self, job: MaterialExtractionJob) -> None: ...


class MaterialExtractionRepository(Protocol):
    def request(self, attachment: Any) -> Any: ...
    def for_attachments(self, attachment_ids: list[UUID]) -> dict[UUID, Any]: ...
    def claim(self, extraction_id: UUID, *, stale_after_seconds: int) -> tuple[Any, Any] | None: ...
    def is_terminal(self, extraction_id: UUID) -> bool: ...
    def running(self, extraction_id: UUID, attempt: int) -> Any | None: ...
    def complete(self, extraction: Any, outcome: ExtractionOutcome) -> None: ...
    def fail(self, extraction: Any, reason: str, *, status: str = "failed") -> None: ...
    def release(self, extraction: Any) -> None: ...
    def chunks_for(self, extraction_ids: list[UUID]) -> list[Any]: ...


@dataclass(frozen=True, slots=True)
class ClaimedExtraction:
    extraction_id: UUID
    attempt: int
    name: str
    content_type: str
    source_ref: str


def classify(name: str, content_type: str) -> str | None:
    """Pick the extractor for a file; None means the format is not supported in this slice."""
    lowered = name.lower()
    media = (content_type or "").split(";")[0].strip().lower()
    if media == "application/pdf" or lowered.endswith(".pdf"):
        return "pdf"
    if media == "text/markdown" or lowered.endswith((".md", ".markdown")):
        return "markdown"
    if media.startswith("text/") or lowered.endswith((".txt", ".text", ".csv", ".log")):
        return "text"
    return None


def decode_utf8_text(data: bytes) -> str | None:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    return text.replace("\r\n", "\n").replace("\r", "\n")


def chunk_text(text: str, *, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP, max_chunks: int = MAX_CHUNKS, page: int | None = None, sequence_start: int = 0, char_offset: int = 0) -> list[ExtractedChunk]:
    """Split bounded text into overlapping windows that prefer paragraph and sentence boundaries."""
    normalized = text[:MAX_TEXT_CHARS]
    chunks: list[ExtractedChunk] = []
    position = 0
    length = len(normalized)
    while position < length and len(chunks) < max_chunks:
        end = min(position + size, length)
        if end < length:
            window = normalized[position:end]
            cut = max(window.rfind("\n\n"), window.rfind(". "), window.rfind("\n"))
            if cut > size // 2:
                end = position + cut + 1
        piece = normalized[position:end].strip()
        if piece:
            chunks.append(ExtractedChunk(sequence_start + len(chunks), piece, char_offset + position, char_offset + end, page))
        if end >= length:
            break
        position = max(end - overlap, position + 1)
    return chunks


_TOKEN = re.compile(r"[0-9A-Za-z가-힣]+")


#: 질문을 낱말로 만드는 규칙. 문서를 색인할 때 쓴 것과 같은 것이어야 하며, 주지 않으면 아래의 단순한 규칙을 쓴다.
_analyze: Any = None


def use_analyzer(analyzer: Any) -> None:
    """문서와 질문에 같은 분석을 적용한다. 한쪽만 분석하면 찾을 수 있는 것과 없는 것이 이유 없이 갈린다."""
    global _analyze
    _analyze = analyzer


def query_tokens(query: str) -> list[str]:
    if _analyze is not None:
        return _analyze.tokens(query)
    tokens: list[str] = []
    # `[가-힣]`는 합쳐진 글자만 맞는다. 자모가 풀린 채로 들어온 질의는 토큰이 하나도 나오지 않으므로,
    # 자르기 전에 모양을 맞춘다.
    for raw in _TOKEN.findall(folded(query)):
        if len(raw) < 2:
            continue
        tokens.append(raw)
        # Korean particles cling to nouns; also match a shorter stem so "견적서를" finds "견적서".
        if len(raw) >= 4 and re.search(r"[가-힣]", raw):
            tokens.append(raw[:-1])
    return list(dict.fromkeys(tokens))


def score_text(text: str, tokens: list[str]) -> tuple[int, int]:
    lowered = folded(text)
    matched = 0
    occurrences = 0
    for token in tokens:
        count = lowered.count(token)
        if count:
            matched += 1
            occurrences += count
    return matched, occurrences


def excerpt(text: str, tokens: list[str], *, width: int = EXCERPT_CHARS) -> str:
    """A bounded window around the first matching token; never the whole chunk."""
    flat = " ".join(text.split())
    lowered = flat.lower()
    anchor = min((lowered.find(token) for token in tokens if lowered.find(token) >= 0), default=0)
    start = max(0, anchor - width // 3)
    end = min(len(flat), start + width)
    start = max(0, end - width)
    piece = flat[start:end]
    if start > 0:
        piece = "…" + piece
    if end < len(flat):
        piece = piece + "…"
    return piece


@dataclass(frozen=True, slots=True)
class MaterialHit:
    chunk_id: UUID
    extraction_id: UUID
    sequence: int
    page: int | None
    excerpt: str
    matched_tokens: int
    occurrences: int


class MaterialRetriever(Protocol):
    def search(self, extraction_ids: list[UUID], query: str, *, limit: int) -> list[MaterialHit]: ...


class ChunkIndexPort(Protocol):
    """찾는 일을 데이터베이스에게 맡기는 자리.

    허용된 자료의 chunk를 전부 application으로 가져와 Python에서 고르면, 자료가 늘어날수록 한 번의 검색이
    읽어야 하는 양이 함께 늘어난다. 조건과 순위와 개수 제한을 데이터베이스 안에서 끝내고, 여기로는 답만 온다.
    """

    def top_matches(self, extraction_ids: list[UUID], tokens: list[str], *, limit: int) -> list[Any]: ...


class LexicalMaterialRetriever:
    """Bounded lexical retrieval. 순위와 개수는 색인이 정하고, 여기서는 사람이 읽을 발췌만 만든다."""

    def __init__(self, repository: MaterialExtractionRepository, index: ChunkIndexPort | None = None) -> None:
        self._repository = repository
        self._index = index

    def search(self, extraction_ids: list[UUID], query: str, *, limit: int) -> list[MaterialHit]:
        tokens = query_tokens(query)
        if not tokens or not extraction_ids:
            return []
        bounded = max(1, min(limit, MAX_SEARCH_HITS))
        if self._index is not None:
            return [
                MaterialHit(
                    chunk.id, chunk.extraction_id, chunk.sequence, chunk.page, excerpt(chunk.text, tokens),
                    *score_text(chunk.text, tokens),
                )
                for chunk in self._index.top_matches(extraction_ids, tokens, limit=bounded)
            ]
        # 색인이 없는 곳에서도 답은 나와야 한다. 같은 순위 규칙을 여기서 쓴다.
        scored: list[tuple[tuple[int, int], Any]] = []
        for chunk in self._repository.chunks_for(extraction_ids):
            matched, occurrences = score_text(chunk.text, tokens)
            if matched:
                scored.append(((matched, occurrences), chunk))
        scored.sort(key=lambda item: (-item[0][0], -item[0][1], item[1].sequence))
        return [
            MaterialHit(chunk.id, chunk.extraction_id, chunk.sequence, chunk.page, excerpt(chunk.text, tokens), matched, occurrences)
            for (matched, occurrences), chunk in scored[:bounded]
        ]


class MaterialExtractionService:
    """Worker-side use case in three short steps so no database transaction spans the parser:
    claim (tx) -> extract (no tx) -> finish (tx, fenced by extraction id + attempt)."""

    def __init__(self, repository: MaterialExtractionRepository, *, max_attempts: int = 3, stale_after_seconds: int = 300) -> None:
        self._repository = repository
        self._max_attempts = max_attempts
        self._stale_after_seconds = stale_after_seconds

    def claim(self, job: MaterialExtractionJob) -> ClaimedExtraction | str:
        """A ClaimedExtraction, or "skipped" (already terminal) / "contended" (a live worker holds it; retry later).
        The two None-like outcomes are distinct so a contended delivery is never archived as done."""
        claimed = self._repository.claim(job.extraction_id, stale_after_seconds=self._stale_after_seconds)
        if claimed is None:
            return "skipped" if self._repository.is_terminal(job.extraction_id) else "contended"
        extraction, attachment = claimed
        return ClaimedExtraction(extraction.id, int(extraction.attempt_count), attachment.name, attachment.content_type, attachment.source_ref)

    @staticmethod
    def extract(claimed: ClaimedExtraction, storage: Any, extractor: MaterialTextExtractor) -> ExtractionOutcome:
        try:
            data = storage.get(claimed.source_ref)
            return extractor.extract(name=claimed.name, content_type=claimed.content_type, data=data)
        except Exception:  # noqa: BLE001 - the reason code is what leaves this boundary, never the exception text
            return ExtractionOutcome(status="failed", extractor=classify(claimed.name, claimed.content_type), failure_reason="extractor_error", transient=True)

    def finish(self, claimed: ClaimedExtraction, outcome: ExtractionOutcome) -> str:
        """Returns completed | failed | unsupported | retry, or "stale" when another attempt owns the row now."""
        extraction = self._repository.running(claimed.extraction_id, claimed.attempt)
        if extraction is None:
            return "stale"
        if outcome.transient and extraction.attempt_count < self._max_attempts:
            self._repository.release(extraction)
            return "retry"
        if outcome.status == "completed":
            self._repository.complete(extraction, outcome)
            return "completed"
        status = outcome.status if outcome.status in {"failed", "unsupported"} else "failed"
        self._repository.fail(extraction, outcome.failure_reason or "extractor_error", status=status)
        return status


def extraction_view(extraction: Any | None) -> dict[str, Any] | None:
    if extraction is None:
        return None
    return {
        "extraction_id": str(extraction.id),
        "status": extraction.status,
        "extractor": extraction.extractor,
        "failure_reason": extraction.failure_reason,
        "failure_text": FAILURE_REASONS.get(extraction.failure_reason or "", extraction.failure_reason),
        "chunk_count": int(extraction.chunk_count or 0),
        "char_count": int(extraction.char_count or 0),
        "page_count": extraction.page_count,
        "attempt_count": int(extraction.attempt_count or 0),
        "requested_at": extraction.requested_at.isoformat() if extraction.requested_at else None,
        "completed_at": extraction.completed_at.isoformat() if extraction.completed_at else None,
    }
