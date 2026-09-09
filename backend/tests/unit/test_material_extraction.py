"""Extraction is bounded, explainable, and never treats file text as instructions."""
from io import BytesIO

from pypdf import PdfReader, PdfWriter

from ax_workspace.modules.work.material_extraction import (
    CHUNK_CHARS,
    EXCERPT_CHARS,
    LexicalMaterialRetriever,
    chunk_text,
    classify,
    excerpt,
    query_tokens,
)
from ax_workspace.platform.material_extraction import PypdfTextExtractor


def _text_pdf(*lines: str, encrypt: str | None = None) -> bytes:
    """A minimal single-page PDF with a Helvetica text stream, without any extra library."""
    content = "BT /F1 12 Tf 72 720 Td " + " ".join(f"({line}) Tj 0 -16 Td" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
    ]
    out = "%PDF-1.4\n"
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n{body}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n" + "".join(f"{offset:010d} 00000 n \n" for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    data = out.encode("latin-1")
    if encrypt is None:
        return data
    writer = PdfWriter(clone_from=PdfReader(BytesIO(data)))
    writer.encrypt(encrypt)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_classify_limits_the_first_slice_to_text_markdown_and_pdf() -> None:
    assert classify("견적서.pdf", "application/octet-stream") == "pdf"
    assert classify("notes.md", "text/markdown") == "markdown"
    assert classify("log.txt", "text/plain; charset=utf-8") == "text"
    assert classify("image.png", "image/png") is None
    assert classify("sheet.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") is None


def test_chunking_is_bounded_and_overlapping() -> None:
    text = ("문장입니다. " * 40 + "\n\n") * 60  # ~30k chars
    chunks = chunk_text(text)
    assert 20 <= len(chunks)
    assert all(len(chunk.text) <= CHUNK_CHARS for chunk in chunks)
    assert [chunk.sequence for chunk in chunks] == list(range(len(chunks)))
    assert chunks[1].char_start < chunks[0].char_end  # overlap keeps context across the boundary
    huge = chunk_text("a" * 400_000)
    assert len(huge) > 400 and huge[-1].char_end == 400_000


def test_query_tokens_handle_korean_particles_and_excerpt_stays_bounded() -> None:
    tokens = query_tokens("견적서를 확인해서 납기일을 알려줘")
    assert "견적서" in tokens and "납기일" in tokens
    body = "앞부분 " * 200 + "납기일은 9월 30일입니다." + " 뒷부분" * 200
    piece = excerpt(body, ["납기일"])
    assert "납기일은 9월 30일" in piece and len(piece) <= EXCERPT_CHARS + 2


def test_text_and_markdown_extract_utf8_only() -> None:
    extractor = PypdfTextExtractor()
    ok = extractor.extract(name="brief.md", content_type="text/markdown", data="# 견적\n납기일 9월 30일".encode())
    assert ok.status == "completed" and ok.extractor == "markdown" and ok.chunks[0].text.startswith("# 견적")
    latin = extractor.extract(name="legacy.txt", content_type="text/plain", data="caf\xe9".encode("cp1252") + b"\xff\xfe")
    assert (latin.status, latin.failure_reason) == ("failed", "not_utf8_text")
    empty = extractor.extract(name="empty.txt", content_type="text/plain", data=b"   \n")
    assert (empty.status, empty.failure_reason) == ("failed", "empty_content")
    image = extractor.extract(name="scan.png", content_type="image/png", data=b"\x89PNG")
    assert (image.status, image.failure_reason) == ("unsupported", "unsupported_format")


def test_pdf_extraction_reports_pages_encryption_corruption_and_empty_text() -> None:
    extractor = PypdfTextExtractor()
    good = extractor.extract(name="quote.pdf", content_type="application/pdf", data=_text_pdf("Quote total 1,200,000 KRW", "Delivery 2026-09-30"))
    assert good.status == "completed" and good.page_count == 1 and good.chunks[0].page == 1
    assert "Delivery 2026-09-30" in good.chunks[0].text
    locked = extractor.extract(name="locked.pdf", content_type="application/pdf", data=_text_pdf("secret", encrypt="pw"))
    assert (locked.status, locked.failure_reason) == ("failed", "encrypted_document")
    corrupt = extractor.extract(name="broken.pdf", content_type="application/pdf", data=b"%PDF-1.4 garbage without xref")
    assert (corrupt.status, corrupt.failure_reason) == ("failed", "corrupt_document")
    blank = extractor.extract(name="blank.pdf", content_type="application/pdf", data=_text_pdf())
    assert (blank.status, blank.failure_reason) == ("failed", "empty_content")


class _Chunk:
    def __init__(self, id: str, extraction_id: str, sequence: int, text: str) -> None:
        self.id, self.extraction_id, self.sequence, self.text, self.page = id, extraction_id, sequence, text, None


class _Repo:
    def __init__(self, chunks: list[_Chunk]) -> None:
        self._chunks = chunks

    def chunks_for(self, extraction_ids: list) -> list[_Chunk]:
        return [chunk for chunk in self._chunks if chunk.extraction_id in extraction_ids]


def test_lexical_retriever_ranks_by_matched_tokens_and_returns_excerpts_only() -> None:
    repo = _Repo(
        [
            _Chunk("c1", "e1", 0, "회의록: 예산 논의. " * 20),
            _Chunk("c2", "e1", 1, "견적서 총액 1,200,000원, 납기일 9월 30일 " + "기타 " * 100),
            _Chunk("c3", "e2", 0, "다른 업무의 납기일 자료"),  # not in the authorized extraction set
        ]
    )
    hits = LexicalMaterialRetriever(repo).search(["e1"], "견적서 납기일이 언제야", limit=5)
    assert [hit.chunk_id for hit in hits] == ["c2"]
    assert hits[0].matched_tokens >= 2 and len(hits[0].excerpt) <= EXCERPT_CHARS + 2
    assert LexicalMaterialRetriever(repo).search([], "납기일", limit=5) == []


class _ClaimRepo:
    """Fake extraction repository: claim() returns None for both a terminal row and a row held by a live worker."""

    def __init__(self, terminal: bool) -> None:
        self._terminal = terminal

    def claim(self, extraction_id, *, stale_after_seconds):
        return None

    def is_terminal(self, extraction_id) -> bool:
        return self._terminal


def test_service_distinguishes_a_terminal_extraction_from_a_contended_one() -> None:
    from uuid import uuid4

    from ax_workspace.modules.work.material_extraction import MaterialExtractionJob, MaterialExtractionService

    job = MaterialExtractionJob(uuid4(), uuid4())
    assert MaterialExtractionService(_ClaimRepo(terminal=True)).claim(job) == "skipped"
    # A live worker holds the row: the delivery must be handed back, never archived as done.
    assert MaterialExtractionService(_ClaimRepo(terminal=False)).claim(job) == "contended"
