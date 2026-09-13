"""Public extraction progress, including verification of a stored projection."""
from pydantic import JsonValue
from typing_extensions import NotRequired, TypedDict


class ExtractionView(TypedDict):
    extraction_id: str
    status: str
    extractor: str | None
    failure_reason: str | None
    failure_text: str | None
    chunk_count: int
    char_count: int
    page_count: int | None
    warnings: list[JsonValue]
    coverage: JsonValue
    parser_version: str | None
    integrity_ref: str | None
    attempt_count: int
    requested_at: str | None
    completed_at: str | None
    stored_status: NotRequired[str]
    reextraction_required: NotRequired[bool]
