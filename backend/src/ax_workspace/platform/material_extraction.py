"""Adapters for material extraction: pypdf/UTF-8 extractor, SQL projection repository, durable-job enqueue, evidence ledger."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from ax_workspace.modules.jobs.domain import JOB_KIND_MATERIAL_EXTRACTION, DurableJobQueue, JobEnvelope
from dataclasses import replace
from typing import Any

from ax_workspace.modules.work.material_extraction import (
    MAX_TEXT_CHARS,
    ExtractedChunk,
    ExtractedBlock,
    ExtractionOutcome,
    MaterialExtractionJob,
    chunk_text,
    classify,
    decode_utf8_text,
)
from ax_workspace.platform.persistence import (
    AttachmentRecord,
    ConversationMaterialEvidenceRecord,
    ConversationRecord,
    ConversationTurnRecord,
    MaterialBlockRecord,
    MaterialChunkRecord,
    MaterialExtractionRecord,
)

MATERIAL_EXTRACTION_QUEUE = "ax_material_extraction"
MAX_PDF_PAGES = 300


#: Bumped when the parsers change what they produce. A new version supersedes the old reading; it never rewrites it.
PARSER_VERSION = "1"

#: Why a document could not be read, in the vocabulary the material surfaces already speak.
_PARSER_FAILURES = {
    "empty": "empty_content",
    "needs_ocr": "needs_ocr",
    "unsupported": "unsupported_format",
    "encrypted": "encrypted_document",
    "corrupt": "corrupt_document",
    "budget_exceeded": "budget_exceeded",
}


class PypdfTextExtractor:
    """UTF-8 text/Markdown plus DOCX/XLSX/PPTX/PDF through the document parser. Failures are reason codes only."""

    def __init__(self, documents: Any = None) -> None:
        from ax_workspace.platform.document_parsers import OfficeDocumentParser

        self._documents = documents or OfficeDocumentParser()

    def extract(self, *, name: str, content_type: str, data: bytes) -> ExtractionOutcome:
        extractor = classify(name, content_type)
        if extractor in {"text", "markdown"}:
            decoded = decode_utf8_text(data)
            if decoded is None:
                return ExtractionOutcome(status="failed", extractor=extractor, failure_reason="not_utf8_text")
            chunks = chunk_text(decoded)
            if not chunks:
                return ExtractionOutcome(status="failed", extractor=extractor, failure_reason="empty_content")
            blocks = (ExtractedBlock(sequence=1, kind="paragraph", text=decoded[:MAX_TEXT_CHARS], locator_label="본문"),)
            chunks = tuple(replace(chunk, block_sequence=1) for chunk in chunks)
            return ExtractionOutcome(
                status="completed", extractor=extractor, chunks=chunks, blocks=blocks,
                char_count=min(len(decoded), MAX_TEXT_CHARS),
            )
        # Everything else the parser understands: the document's own shape, then chunks derived from it.
        document_format = self._documents.supports(name=name, content_type=content_type)
        if document_format is None:
            return ExtractionOutcome(status="unsupported", extractor=extractor, failure_reason="unsupported_format")
        parsed = self._documents.parse(name=name, content_type=content_type, data=data)
        if parsed.status != "ok" or not parsed.blocks:
            reason = _PARSER_FAILURES.get(parsed.status, "extractor_error")
            status = "needs_ocr" if reason == "needs_ocr" else ("unsupported" if reason == "unsupported_format" else "failed")
            return ExtractionOutcome(
                status=status, extractor=document_format, failure_reason=reason, page_count=parsed.page_count
            )
        blocks: list[ExtractedBlock] = []
        chunks: list[ExtractedChunk] = []
        total = 0
        for block in parsed.blocks:
            if total >= MAX_TEXT_CHARS:
                break
            text = block.text[: MAX_TEXT_CHARS - total]
            locator = block.locator
            blocks.append(
                ExtractedBlock(
                    sequence=len(blocks) + 1,
                    kind=block.kind,
                    text=text,
                    locator_label=locator.label,
                    page=getattr(locator, "page", None),
                    sheet=getattr(locator, "sheet", None),
                    slide=getattr(locator, "slide", None),
                    # A sheet row's ordinal lives in the locator's index; other kinds have none.
                    row=getattr(locator, "index", None) if block.kind == "sheet_row" else None,
                )
            )
            for chunk in chunk_text(text, page=getattr(locator, "page", None), sequence_start=len(chunks), char_offset=total):
                chunks.append(replace(chunk, block_sequence=len(blocks)))
            total += len(text)
        if not chunks:
            return ExtractionOutcome(status="failed", extractor=document_format, failure_reason="empty_content", page_count=parsed.page_count)
        return ExtractionOutcome(
            status="completed", extractor=document_format, chunks=tuple(chunks), blocks=tuple(blocks),
            char_count=total, page_count=parsed.page_count,
        )

    @staticmethod
    def _extract_pdf(data: bytes) -> ExtractionOutcome:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError

        try:
            reader = PdfReader(BytesIO(data), strict=False)
            if reader.is_encrypted:
                try:
                    if reader.decrypt("") == 0:
                        return ExtractionOutcome(status="failed", extractor="pdf", failure_reason="encrypted_pdf")
                except Exception:  # noqa: BLE001
                    return ExtractionOutcome(status="failed", extractor="pdf", failure_reason="encrypted_pdf")
            page_count = len(reader.pages)
        except PdfReadError:
            return ExtractionOutcome(status="failed", extractor="pdf", failure_reason="corrupt_pdf")
        except Exception:  # noqa: BLE001
            return ExtractionOutcome(status="failed", extractor="pdf", failure_reason="corrupt_pdf")
        chunks: list[ExtractedChunk] = []
        total = 0
        for index, page in enumerate(reader.pages):
            if index >= MAX_PDF_PAGES or total >= MAX_TEXT_CHARS:
                break
            try:
                page_text = page.extract_text() or ""
            except Exception:  # noqa: BLE001 - one bad page must not hide the rest; a fully unreadable file yields no chunks
                continue
            page_text = page_text.strip()
            if not page_text:
                continue
            page_text = page_text[: MAX_TEXT_CHARS - total]
            chunks.extend(chunk_text(page_text, page=index + 1, sequence_start=len(chunks), char_offset=total))
            total += len(page_text)
        if not chunks:
            return ExtractionOutcome(status="failed", extractor="pdf", failure_reason="empty_content", page_count=page_count)
        return ExtractionOutcome(status="completed", extractor="pdf", chunks=tuple(chunks), char_count=total, page_count=page_count)


class SqlAlchemyMaterialExtractionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def request(self, attachment: AttachmentRecord, *, parser_version: str = PARSER_VERSION) -> MaterialExtractionRecord:
        """One extraction per (file version, parser version); requesting the same pair returns the existing row."""
        existing = self._session.scalar(
            select(MaterialExtractionRecord).where(
                MaterialExtractionRecord.attachment_id == attachment.id,
                MaterialExtractionRecord.integrity_ref == attachment.integrity_ref,
                MaterialExtractionRecord.parser_version == parser_version,
            )
        )
        if existing is not None:
            return existing
        # A newer parser reads the same file again; the older reading stays, marked as superseded.
        for older in self._session.scalars(
            select(MaterialExtractionRecord).where(
                MaterialExtractionRecord.attachment_id == attachment.id,
                MaterialExtractionRecord.superseded_at.is_(None),
                MaterialExtractionRecord.parser_version != parser_version,
            )
        ):
            older.superseded_at = datetime.now(UTC)
        record = MaterialExtractionRecord(
            attachment_id=attachment.id,
            integrity_ref=attachment.integrity_ref,
            extractor=classify(attachment.name, attachment.content_type),
            parser_version=parser_version,
            status="queued",
            requested_at=datetime.now(UTC),
        )
        self._session.add(record)
        self._session.flush()
        return record

    def for_attachments(self, attachment_ids: list[UUID]) -> dict[UUID, MaterialExtractionRecord]:
        if not attachment_ids:
            return {}
        rows = self._session.scalars(
            select(MaterialExtractionRecord)
            .where(MaterialExtractionRecord.attachment_id.in_(attachment_ids), MaterialExtractionRecord.superseded_at.is_(None))
            .order_by(MaterialExtractionRecord.requested_at)
        ).all()
        return {row.attachment_id: row for row in rows}

    def get(self, extraction_id: UUID) -> MaterialExtractionRecord | None:
        return self._session.get(MaterialExtractionRecord, extraction_id)

    def is_terminal(self, extraction_id: UUID) -> bool:
        extraction = self._session.get(MaterialExtractionRecord, extraction_id)
        return extraction is None or extraction.status in {"completed", "unsupported", "failed"}

    def claim(self, extraction_id: UUID, *, stale_after_seconds: int) -> tuple[MaterialExtractionRecord, AttachmentRecord] | None:
        statement = select(MaterialExtractionRecord).where(MaterialExtractionRecord.id == extraction_id)
        if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        extraction = self._session.scalar(statement)
        if extraction is None or extraction.status in {"completed", "unsupported", "failed"}:
            return None
        now = datetime.now(UTC)
        if extraction.status == "running":
            started = extraction.started_at
            if started is not None and started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            if started is not None and now - started < timedelta(seconds=stale_after_seconds):
                return None  # a live worker holds it
        attachment = self._session.get(AttachmentRecord, extraction.attachment_id)
        if attachment is None:
            extraction.status = "failed"
            extraction.failure_reason = "extractor_error"
            extraction.completed_at = now
            return None
        extraction.status = "running"
        extraction.started_at = now
        extraction.attempt_count = int(extraction.attempt_count or 0) + 1
        self._session.flush()
        return extraction, attachment

    def running(self, extraction_id: UUID, attempt: int) -> MaterialExtractionRecord | None:
        """The extraction row only if it is still running under the given attempt (fencing for the domain projection)."""
        statement = select(MaterialExtractionRecord).where(
            MaterialExtractionRecord.id == extraction_id, MaterialExtractionRecord.status == "running", MaterialExtractionRecord.attempt_count == attempt
        )
        if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def complete(self, extraction: MaterialExtractionRecord, outcome: ExtractionOutcome) -> None:
        # Re-processing the same reading replaces its blocks and chunks instead of appending duplicates.
        self._session.execute(delete(MaterialChunkRecord).where(MaterialChunkRecord.extraction_id == extraction.id))
        self._session.execute(delete(MaterialBlockRecord).where(MaterialBlockRecord.extraction_id == extraction.id))
        block_ids: dict[int, UUID] = {}
        for block in outcome.blocks:
            record = MaterialBlockRecord(
                extraction_id=extraction.id,
                sequence=block.sequence,
                kind=block.kind,
                text=block.text,
                locator_label=block.locator_label,
                page=block.page,
                sheet=block.sheet,
                slide=block.slide,
                row=block.row,
            )
            self._session.add(record)
            self._session.flush()
            block_ids[block.sequence] = record.id
        for chunk in outcome.chunks:
            self._session.add(
                MaterialChunkRecord(
                    extraction_id=extraction.id,
                    block_id=block_ids.get(chunk.block_sequence) if chunk.block_sequence else None,
                    sequence=chunk.sequence,
                    page=chunk.page,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    text=chunk.text,
                )
            )
        extraction.status = "completed"
        extraction.extractor = outcome.extractor
        extraction.failure_reason = None
        extraction.chunk_count = len(outcome.chunks)
        extraction.char_count = outcome.char_count
        extraction.page_count = outcome.page_count
        extraction.completed_at = datetime.now(UTC)
        self._session.flush()

    def fail(self, extraction: MaterialExtractionRecord, reason: str, *, status: str = "failed") -> None:
        self._session.execute(delete(MaterialChunkRecord).where(MaterialChunkRecord.extraction_id == extraction.id))
        self._session.execute(delete(MaterialBlockRecord).where(MaterialBlockRecord.extraction_id == extraction.id))
        extraction.status = status
        extraction.failure_reason = reason
        extraction.chunk_count = 0
        extraction.completed_at = datetime.now(UTC)
        self._session.flush()

    def release(self, extraction: MaterialExtractionRecord) -> None:
        """A transient failure goes back to queued so the transport redelivery can retry it."""
        extraction.status = "queued"
        extraction.failure_reason = "extractor_error"
        extraction.started_at = None
        self._session.flush()

    def chunks_for(self, extraction_ids: list[UUID]) -> list[MaterialChunkRecord]:
        if not extraction_ids:
            return []
        return list(
            self._session.scalars(
                select(MaterialChunkRecord).where(MaterialChunkRecord.extraction_id.in_(extraction_ids)).order_by(MaterialChunkRecord.extraction_id, MaterialChunkRecord.sequence)
            )
        )


# ---- transport --------------------------------------------------------------------------------------------------


class MaterialJobQueue:
    """Enqueue-only adapter used inside the upload transaction; the worker claims through the shared job port."""

    def __init__(self, jobs: DurableJobQueue) -> None:
        self._jobs = jobs

    def enqueue(self, job: MaterialExtractionJob) -> None:
        self._jobs.enqueue(
            JobEnvelope(
                kind=JOB_KIND_MATERIAL_EXTRACTION,
                ordering_key=str(job.extraction_id),
                idempotency_key=f"{JOB_KIND_MATERIAL_EXTRACTION}:{job.extraction_id}",
                payload={"extraction_id": str(job.extraction_id), "attachment_id": str(job.attachment_id)},
            )
        )


# ---- evidence ledger ---------------------------------------------------------------------------------------------


class SqlAlchemyMaterialEvidenceRepository:
    """What a delegated AX turn actually read: bounded excerpts tied to the turn, never the whole file."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(self, execution_id: UUID, principal_id: str, task_id: UUID, query: str, hits: list[dict[str, Any]]) -> list[ConversationMaterialEvidenceRecord]:
        turn = self._session.scalar(select(ConversationTurnRecord).where(ConversationTurnRecord.execution_id == execution_id))
        if turn is None:
            raise ValueError("delegated conversation execution was not found")
        conversation = self._session.get(ConversationRecord, turn.conversation_id)
        if conversation is None or str(conversation.owner_id) != principal_id:  # fail closed
            raise ValueError("delegated conversation belongs to another principal")
        now = datetime.now(UTC)
        records: list[ConversationMaterialEvidenceRecord] = []
        for rank, hit in enumerate(hits, start=1):
            existing = self._session.scalar(
                select(ConversationMaterialEvidenceRecord).where(
                    ConversationMaterialEvidenceRecord.turn_id == turn.id,
                    ConversationMaterialEvidenceRecord.chunk_id == UUID(str(hit["chunk_id"])),
                )
            )
            if existing is not None:
                records.append(existing)
                continue
            record = ConversationMaterialEvidenceRecord(
                turn_id=turn.id,
                conversation_id=turn.conversation_id,
                execution_id=execution_id,
                task_id=task_id,
                material_id=UUID(str(hit["material_id"])),
                attachment_id=UUID(str(hit["attachment_id"])),
                chunk_id=UUID(str(hit["chunk_id"])),
                name=str(hit["name"]),
                integrity_ref=str(hit["integrity_ref"]),
                page=hit.get("page"),
                excerpt=str(hit["excerpt"])[:400],
                query=query[:300],
                rank=rank,
                recorded_at=now,
            )
            self._session.add(record)
            records.append(record)
        self._session.flush()
        return records

    def for_conversation(self, conversation_id: UUID) -> list[ConversationMaterialEvidenceRecord]:
        return list(
            self._session.scalars(
                select(ConversationMaterialEvidenceRecord)
                .where(ConversationMaterialEvidenceRecord.conversation_id == conversation_id)
                .order_by(ConversationMaterialEvidenceRecord.recorded_at, ConversationMaterialEvidenceRecord.rank)
            )
        )
