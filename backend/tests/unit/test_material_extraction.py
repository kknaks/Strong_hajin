"""Pure extraction policy is bounded and keeps source text as inert data."""

import unicodedata

from ax_workspace.modules.work.material_extraction import (
    CHUNK_CHARS,
    EXCERPT_CHARS,
    LexicalMaterialRetriever,
    chunk_text,
    classify,
    excerpt,
    query_tokens,
    score_text,
)


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


def test_search_policy_treats_composed_and_decomposed_korean_as_the_same_text() -> None:
    title = "한빛의원 통합 마케팅"
    pasted = unicodedata.normalize("NFD", title)
    assert pasted != title
    assert query_tokens(pasted) == query_tokens(title)
    assert score_text(title, query_tokens("한빛")) == score_text(pasted, query_tokens("한빛")) == (1, 1)


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

    def claim(self, extraction_id, *, stale_after_seconds, owner_token=None, actor_id=None):
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
