"""Adapters for material extraction: pypdf/UTF-8 extractor, SQL projection repository, durable-job enqueue, evidence ledger."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from itertools import batched
from uuid import UUID, uuid5

from sqlalchemy import case, delete, insert, func, literal, literal_column, select
from sqlalchemy.orm import Session

from ax_workspace.modules.jobs.domain import JOB_KIND_MATERIAL_EXTRACTION, DurableJobQueue, JobEnvelope
from dataclasses import replace

from ax_workspace.platform.extraction_spool import ExtractionSpool
from ax_workspace.platform.korean import analyzer
from ax_workspace.modules.work.material_extraction import (
    ExtractedChunk,
    ExtractedBlock,
    ExtractionOutcome,
    MaterialExtractionJob,
    PARSER_VERSION,
    UPGRADABLE_PARSER_VERSIONS,
    ParserVersionConflict,
    iter_chunks,
    classify,
    decode_utf8_text,
)
from ax_workspace.platform.persistence import (
    AttachmentRecord,
    ConversationContentEvidenceRecord,
    ConversationRecord,
    ConversationTurnRecord,
    MaterialBlockRecord,
    MaterialChunkRecord,
    MaterialExtractionAttemptRecord,
    MaterialExtractionRecord,
)

MATERIAL_EXTRACTION_QUEUE = "ax_material_extraction"


#: Why a document could not be read, in the vocabulary the material surfaces already speak.
_PARSER_FAILURES = {
    "empty": "empty_content",
    "needs_ocr": "needs_ocr",
    "unsupported": "unsupported_format",
    "encrypted": "encrypted_document",
    "corrupt": "corrupt_document",
    "budget_exceeded": "too_large",
}


class PypdfTextExtractor:
    """UTF-8 text/Markdown plus DOCX/XLSX/PPTX/PDF through the document parser. Failures are reason codes only."""

    def __init__(self, documents: Any = None) -> None:
        from ax_workspace.platform.document_parsers import OfficeDocumentParser

        self._documents = documents or OfficeDocumentParser()

    def extract(self, *, name: str, content_type: str, data: bytes) -> ExtractionOutcome:
        blocks = ExtractionSpool(ExtractedBlock)
        chunks = ExtractionSpool(ExtractedChunk)
        try:
            outcome = self._extract(name=name, content_type=content_type, data=data, blocks=blocks, chunks=chunks)
        except BaseException:
            blocks.close()
            chunks.close()
            raise
        if outcome.status not in {"completed", "partial"}:
            blocks.close()
            chunks.close()
        return outcome

    def _extract(self, *, name, content_type, data, blocks, chunks) -> ExtractionOutcome:
        total = 0

        def append(block: ExtractedBlock) -> None:
            nonlocal total
            blocks.append(block)
            for chunk in iter_chunks(block.text, page=block.page, sequence_start=len(chunks), char_offset=total):
                chunks.append(replace(chunk, block_sequence=block.sequence, context_text=(block.header_context or {}).get("text", "")))
            total += len(block.text)

        extractor = classify(name, content_type)
        if extractor in {"text", "markdown"}:
            decoded = decode_utf8_text(data)
            if decoded is None:
                return ExtractionOutcome(status="failed", extractor=extractor, failure_reason="not_utf8_text")
            if not decoded.strip():
                return ExtractionOutcome(status="failed", extractor=extractor, failure_reason="empty_content")
            append(ExtractedBlock(sequence=1, kind="paragraph", text=decoded, locator_label="본문"))
            return ExtractionOutcome(
                status="completed", extractor=extractor, chunks=chunks, blocks=blocks, char_count=total,
                coverage={"complete": True, "indexed_blocks": len(blocks), "indexed_chars": total},
            )
        document_format = self._documents.supports(name=name, content_type=content_type)
        if document_format is None:
            return ExtractionOutcome(status="unsupported", extractor=extractor, failure_reason="unsupported_format")

        def receive(block) -> None:
            locator = block.locator
            append(ExtractedBlock(
                sequence=len(blocks) + 1, kind=block.kind, text=block.text,
                locator_label=locator.label, page=locator.page, sheet=locator.sheet, slide=locator.slide,
                row=locator.index if block.kind == "sheet_row" else None,
                source_locator=locator.as_dict(), header_context=block.header_context,
            ))

        parsed = self._documents.parse(name=name, content_type=content_type, data=data, sink=receive)
        if parsed.truncated:
            return ExtractionOutcome(status="too_large", extractor=document_format, failure_reason="too_large",
                                     warnings=parsed.warnings, coverage={"complete": False})
        if parsed.status not in {"ok", "partial"} or not chunks:
            reason = _PARSER_FAILURES.get(parsed.status, "extractor_error")
            status = "needs_ocr" if reason == "needs_ocr" else ("unsupported" if reason == "unsupported_format" else ("too_large" if reason == "too_large" else "failed"))
            return ExtractionOutcome(status=status, extractor=document_format, failure_reason=reason,
                                     page_count=parsed.page_count, warnings=parsed.warnings,
                                     coverage={**parsed.coverage, "complete": False, "reason": parsed.detail})
        return ExtractionOutcome(
            status="partial" if parsed.status == "partial" else "completed", extractor=document_format,
            chunks=chunks, blocks=blocks, char_count=total, page_count=parsed.page_count,
            warnings=parsed.warnings,
            coverage={**parsed.coverage, "complete": parsed.status == "ok", "indexed_blocks": len(blocks), "indexed_chars": total},
        )


class SqlAlchemyMaterialExtractionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def request(self, attachment: AttachmentRecord, *, parser_version: str = PARSER_VERSION) -> MaterialExtractionRecord:
        """One extraction per (file version, parser version); requesting the same pair returns the existing row."""
        if attachment.source_kind == "native_recording":
            raise ValueError("native audio has no text extraction; search its transcript revisions")
        if parser_version != PARSER_VERSION:
            raise ValueError("parser version is not installed")
        # Serialize concurrent upgrade/upload requests on the immutable artifact, before checking the unique pair.
        self._session.execute(select(AttachmentRecord.id).where(AttachmentRecord.id == attachment.id).with_for_update())
        active_versions = self._session.scalars(select(MaterialExtractionRecord.parser_version).where(
            MaterialExtractionRecord.attachment_id == attachment.id, MaterialExtractionRecord.superseded_at.is_(None),
        )).all()
        if any(version not in UPGRADABLE_PARSER_VERSIONS | {PARSER_VERSION} for version in active_versions):
            raise ParserVersionConflict("active parser version is incompatible")
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
            extractor="native_revision" if attachment.source_kind == "native_revision" else classify(attachment.name, attachment.content_type),
            parser_version=parser_version,
            status="queued",
            requested_at=datetime.now(UTC),
        )
        self._session.add(record)
        self._session.flush()
        return record

    def request_upgrades(self, *, limit: int) -> list[MaterialExtractionJob]:
        """Schedule a bounded batch of old active projections; source bytes and historical blocks stay untouched."""
        statement = (
            select(AttachmentRecord)
            .join(MaterialExtractionRecord, MaterialExtractionRecord.attachment_id == AttachmentRecord.id)
            .where(AttachmentRecord.source_kind.in_(("file", "native_revision")), AttachmentRecord.lifecycle != "purged",
                   MaterialExtractionRecord.superseded_at.is_(None),
                   MaterialExtractionRecord.integrity_ref == AttachmentRecord.integrity_ref,
                   MaterialExtractionRecord.parser_version.in_(UPGRADABLE_PARSER_VERSIONS))
            .order_by(MaterialExtractionRecord.requested_at, AttachmentRecord.id)
            .limit(max(1, limit))
        )
        if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True, of=AttachmentRecord)
        jobs = []
        for attachment in self._session.scalars(statement).all():
            try:
                extraction = self.request(attachment)
            except ParserVersionConflict:
                continue  # A newer deployment won the artifact lock after candidate selection.
            jobs.append(MaterialExtractionJob(extraction.id, attachment.id, attachment.uploaded_by))
        return jobs

    def request_missing(self, *, limit: int) -> list[MaterialExtractionJob]:
        """Worker maintenance backfills legacy files without making a user's read schedule work."""
        statement = (select(AttachmentRecord)
                     .where(AttachmentRecord.source_kind.in_(('file', 'native_revision')), AttachmentRecord.lifecycle != 'purged',
                            ~select(MaterialExtractionRecord.id).where(MaterialExtractionRecord.attachment_id == AttachmentRecord.id).exists())
                     .order_by(AttachmentRecord.created_at, AttachmentRecord.id).limit(max(1, limit)))
        if self._session.bind is not None and self._session.bind.dialect.name == 'postgresql':
            statement = statement.with_for_update(skip_locked=True, of=AttachmentRecord)
        return [MaterialExtractionJob(self.request(attachment).id, attachment.id, attachment.uploaded_by)
                for attachment in self._session.scalars(statement).all()]

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
        return extraction is None or extraction.superseded_at is not None or extraction.status in {"completed", "partial", "too_large", "unsupported", "failed", "purged"}

    def claim(self, extraction_id: UUID, *, stale_after_seconds: int, owner_token: UUID | None = None,
              actor_id: str | None = None) -> tuple[MaterialExtractionRecord, AttachmentRecord] | None:
        statement = select(MaterialExtractionRecord).where(MaterialExtractionRecord.id == extraction_id)
        if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        extraction = self._session.scalar(statement)
        if extraction is None or extraction.superseded_at is not None or extraction.status in {"completed", "partial", "too_large", "unsupported", "failed", "purged"}:
            return None
        now = datetime.now(UTC)
        if extraction.status == "running":
            started = extraction.heartbeat_at or extraction.started_at
            if started is not None and started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            if started is not None and now - started < timedelta(seconds=stale_after_seconds):
                return None  # a live worker holds it
            previous = self._attempt(extraction)
            if previous is not None and previous.state == "running":
                previous.state = "abandoned"
                previous.failure_reason = "lease_expired"
                previous.completed_at = now
        attachment = self._session.get(AttachmentRecord, extraction.attachment_id)
        if attachment is None:
            extraction.status = "failed"
            extraction.failure_reason = "extractor_error"
            extraction.completed_at = now
            return None
        extraction.status = "running"
        extraction.started_at = now
        extraction.heartbeat_at = now
        extraction.attempt_count = int(extraction.attempt_count or 0) + 1
        extraction.worker_token = owner_token
        self._session.add(MaterialExtractionAttemptRecord(
            extraction_id=extraction.id,
            attempt_number=extraction.attempt_count,
            actor_id=actor_id,
            owner_token=owner_token,
            state="running",
            started_at=now,
            heartbeat_at=now,
        ))
        self._session.flush()
        return extraction, attachment

    def _attempt(self, extraction: MaterialExtractionRecord) -> MaterialExtractionAttemptRecord | None:
        return self._session.scalar(select(MaterialExtractionAttemptRecord).where(
            MaterialExtractionAttemptRecord.extraction_id == extraction.id,
            MaterialExtractionAttemptRecord.attempt_number == extraction.attempt_count,
            *((MaterialExtractionAttemptRecord.owner_token == extraction.worker_token,) if extraction.worker_token is not None else ()),
        ))

    def _finish_attempt(self, extraction: MaterialExtractionRecord, state: str, failure_reason: str | None = None) -> None:
        attempt = self._attempt(extraction)
        if attempt is not None and attempt.state == "running":
            attempt.state = state
            attempt.failure_reason = failure_reason
            attempt.heartbeat_at = datetime.now(UTC)
            attempt.completed_at = attempt.heartbeat_at

    def running(self, extraction_id: UUID, attempt: int, owner_token: UUID | None = None) -> MaterialExtractionRecord | None:
        """The extraction row only if it is still running under the given attempt (fencing for the domain projection)."""
        statement = select(MaterialExtractionRecord).where(
            MaterialExtractionRecord.id == extraction_id, MaterialExtractionRecord.status == "running",
            MaterialExtractionRecord.attempt_count == attempt, MaterialExtractionRecord.superseded_at.is_(None),
            *((MaterialExtractionRecord.worker_token == owner_token,) if owner_token is not None else ()),
        )
        if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def heartbeat(self, extraction_id: UUID, attempt: int, owner_token: UUID) -> bool:
        extraction = self.running(extraction_id, attempt, owner_token)
        if extraction is None:
            return False
        extraction.heartbeat_at = datetime.now(UTC)
        attempt = self._attempt(extraction)
        if attempt is not None and attempt.state == "running":
            attempt.heartbeat_at = extraction.heartbeat_at
        self._session.flush()
        return True

    def complete(self, extraction: MaterialExtractionRecord, outcome: ExtractionOutcome) -> None:
        # Re-processing the same reading replaces its blocks and chunks instead of appending duplicates.
        self._session.execute(delete(MaterialChunkRecord).where(MaterialChunkRecord.extraction_id == extraction.id))
        self._session.execute(delete(MaterialBlockRecord).where(MaterialBlockRecord.extraction_id == extraction.id))
        # Stable identities make a replay replace the same projection. Insert batches stay bounded; the surrounding
        # transaction publishes blocks, chunks and terminal status together, or rolls the entire attempt back.
        def block_id(sequence: int) -> UUID:
            return uuid5(extraction.id, f"block:{sequence}")

        for batch in batched(outcome.blocks, 128):
            self._session.execute(insert(MaterialBlockRecord), [
                {"id": block_id(block.sequence), "extraction_id": extraction.id, "sequence": block.sequence,
                 "kind": block.kind, "text": block.text, "locator_label": block.locator_label,
                 "page": block.page, "sheet": block.sheet, "slide": block.slide, "row": block.row,
                 "source_locator": block.source_locator, "header_context": block.header_context}
                for block in batch
            ])
        korean = analyzer()
        for batch in batched(outcome.chunks, 128):
            self._session.execute(insert(MaterialChunkRecord), [
                {"id": uuid5(extraction.id, f"chunk:{chunk.sequence}"), "extraction_id": extraction.id,
                 "block_id": block_id(chunk.block_sequence) if chunk.block_sequence is not None else None,
                 "sequence": chunk.sequence, "page": chunk.page, "char_start": chunk.char_start,
                 "char_end": chunk.char_end, "text": chunk.text,
                 "context_text": chunk.context_text,
                 "search_text": korean.index_text(chunk.context_text + " " + chunk.text), "analyzer_version": korean.version}
                for chunk in batch
            ])
        self._finish_attempt(extraction, outcome.status)
        extraction.status = outcome.status
        extraction.warnings = list(outcome.warnings)
        extraction.coverage = outcome.coverage
        extraction.extractor = outcome.extractor
        extraction.failure_reason = None
        extraction.chunk_count = len(outcome.chunks)
        extraction.char_count = outcome.char_count
        extraction.page_count = outcome.page_count
        extraction.completed_at = datetime.now(UTC)
        extraction.heartbeat_at = None
        extraction.worker_token = None
        self._session.flush()

    def fail(self, extraction: MaterialExtractionRecord, reason: str, *, status: str = "failed", outcome: ExtractionOutcome | None = None) -> None:
        self._session.execute(delete(MaterialChunkRecord).where(MaterialChunkRecord.extraction_id == extraction.id))
        self._session.execute(delete(MaterialBlockRecord).where(MaterialBlockRecord.extraction_id == extraction.id))
        self._finish_attempt(extraction, status, reason)
        extraction.status = status
        extraction.failure_reason = reason
        extraction.chunk_count = 0
        extraction.char_count = 0
        extraction.page_count = outcome.page_count if outcome else None
        extraction.warnings = list(outcome.warnings) if outcome else []
        extraction.coverage = outcome.coverage if outcome else {"complete": False}
        extraction.completed_at = datetime.now(UTC)
        extraction.heartbeat_at = None
        extraction.worker_token = None
        self._session.flush()

    def release(self, extraction: MaterialExtractionRecord) -> None:
        """A transient failure goes back to queued so the transport redelivery can retry it."""
        self._finish_attempt(extraction, "retry", "extractor_error")
        extraction.status = "queued"
        extraction.failure_reason = "extractor_error"
        extraction.started_at = None
        extraction.heartbeat_at = None
        extraction.worker_token = None
        self._session.flush()

    def chunks_for(self, extraction_ids: list[UUID]) -> list[MaterialChunkRecord]:
        if not extraction_ids:
            return []
        return list(
            self._session.scalars(
                select(MaterialChunkRecord).where(MaterialChunkRecord.extraction_id.in_(extraction_ids)).order_by(MaterialChunkRecord.extraction_id, MaterialChunkRecord.sequence)
            )
        )

    def chunk_contexts(self, chunk_ids: list[UUID]) -> dict[UUID, dict[str, Any]]:
        if not chunk_ids:
            return {}
        rows = self._session.execute(
            select(MaterialChunkRecord.id, MaterialChunkRecord.char_start, MaterialChunkRecord.char_end,
                   MaterialBlockRecord.source_locator, MaterialBlockRecord.header_context, MaterialBlockRecord.locator_label)
            .outerjoin(MaterialBlockRecord, MaterialChunkRecord.block_id == MaterialBlockRecord.id)
            .where(MaterialChunkRecord.id.in_(chunk_ids))
        )
        return {row.id: {"source_locator": {**(row.source_locator or {}), "char_start": row.char_start, "char_end": row.char_end, "char_offset_basis": "extracted_projection"},
                         "header_context": row.header_context, "locator_label": row.locator_label} for row in rows}


def reindex_stale_chunks(session: Session, *, limit: int = 500) -> int:
    """분석 규칙이 바뀐 뒤 남아 있는 색인을 다시 만든다.

    같은 규칙으로 만든 것은 다시 만들지 않는다 — 여러 번 돌려도 한 번 돌린 것과 같다. 원문(`text`)은 건드리지
    않고 찾기 위한 형태만 바꾼다. 다시 만들지 못한 것은 그대로 남아 다음 차례를 기다린다.
    """
    korean = analyzer()
    stale = list(
        session.scalars(
            select(MaterialChunkRecord)
            .where(
                (MaterialChunkRecord.analyzer_version.is_(None))
                | (MaterialChunkRecord.analyzer_version != korean.version)
            )
            .limit(limit)
        )
    )
    for chunk in stale:
        chunk.search_text = korean.index_text((chunk.context_text or "") + " " + chunk.text)
        chunk.analyzer_version = korean.version
    session.flush()
    return len(stale)


class SqlChunkIndex:
    """찾는 일을 데이터베이스 안에서 끝낸다.

    PostgreSQL에서는 `tsvector`와 GIN 색인이 조건과 순위를 맡는다. 색인이 없는 곳에서는 같은 열을 좁히고 맞은
    낱말 수로 줄을 세운다 — 어느 쪽이든 조건·순위·개수가 데이터베이스 안에서 끝나고 application으로는 답만 온다.
    허용된 자료의 chunk를 전부 가져와 Python에서 고르면 자료가 늘어날수록 한 번의 검색이 읽는 양도 함께 늘어난다.

    낱말은 문서를 색인할 때 쓴 것과 같은 규칙으로 만든 것이며, 그 규칙이 바뀌면 `analyzer_version`이 달라진다.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def top_matches(self, extraction_ids: list[UUID], tokens: list[str], *, limit: int) -> list[MaterialChunkRecord]:
        safe = [token for token in tokens if token.strip()][:24]
        if not extraction_ids or not safe:
            return []
        indexed = func.coalesce(MaterialChunkRecord.search_text, "")
        scope = MaterialChunkRecord.extraction_id.in_(extraction_ids)
        if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
            # `simple`은 문자열이 아니라 검색 설정의 이름이다. 문자열로 넘기면 함수가 그런 것을 모른다고 답한다.
            config = literal_column("'simple'::regconfig")
            document = func.to_tsvector(config, indexed)
            terms = func.to_tsquery(config, " | ".join(safe))
            statement = (
                select(MaterialChunkRecord)
                .where(scope, document.op("@@")(terms))
                .order_by(func.ts_rank(document, terms).desc(), MaterialChunkRecord.sequence)
                .limit(limit)
            )
        else:
            # 낱말 통째로만 맞힌다. 부분문자열로 맞히면 `일`이 `일정`에 맞아 관계없는 자료가 섞인다.
            score = sum(
                (case((indexed.like(f"% {token} %"), 1), else_=0) for token in safe),
                literal(0),
            )
            statement = (
                select(MaterialChunkRecord)
                .where(scope, score > 0)
                .order_by(score.desc(), MaterialChunkRecord.sequence)
                .limit(limit)
            )
        return list(self._session.scalars(statement))


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
                payload={"extraction_id": str(job.extraction_id), "attachment_id": str(job.attachment_id), "actor_id": job.actor_id},
            )
        )


# ---- evidence ledger ---------------------------------------------------------------------------------------------


class SqlAlchemyMaterialEvidenceRepository:
    """What a delegated AX turn actually read: bounded excerpts tied to the turn, never the whole file."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(self, execution_id: UUID, principal_id: str, query: str, hits: list[dict[str, Any]]) -> None:
        # Share the artifact fence with purge; refresh rows already read during search before publishing copied text.
        artifact_ids = sorted({UUID(hit["material_id"]) for hit in hits}, key=str)
        artifacts = {str(row.id): row for row in self._session.scalars(select(AttachmentRecord).where(
            AttachmentRecord.id.in_(artifact_ids)).order_by(AttachmentRecord.id).with_for_update().execution_options(populate_existing=True))}
        # Artifact -> turn is the common publication lock order.
        turn = self._session.scalar(select(ConversationTurnRecord).where(ConversationTurnRecord.execution_id == execution_id).with_for_update())
        if turn is None:
            raise ValueError("delegated conversation execution was not found")
        conversation = self._session.get(ConversationRecord, turn.conversation_id)
        if conversation is None or str(conversation.owner_id) != principal_id:
            raise ValueError("delegated conversation belongs to another principal")
        for rank, hit in enumerate(hits, start=1):
            artifact = artifacts.get(hit["material_id"])
            if artifact is None or artifact.lifecycle == "purged" or artifact.integrity_ref != hit["integrity_ref"]:
                continue
            existing = self._session.scalar(select(ConversationContentEvidenceRecord).where(
                ConversationContentEvidenceRecord.turn_id == turn.id, ConversationContentEvidenceRecord.chunk_id == UUID(hit["chunk_id"])))
            if existing is not None:
                contexts = {(row["resource_type"], row["resource_id"], row["binding_id"]): row for row in existing.source_contexts}
                contexts.update({(row["resource_type"], row["resource_id"], row["binding_id"]): row for row in hit["source_contexts"]})
                existing.source_contexts = list(contexts.values())
                continue
            self._session.add(ConversationContentEvidenceRecord(turn_id=turn.id, conversation_id=turn.conversation_id,
                execution_id=execution_id, attachment_id=UUID(hit["material_id"]), chunk_id=UUID(hit["chunk_id"]),
                source_contexts=hit["source_contexts"], name=hit["name"], integrity_ref=hit["integrity_ref"], page=hit.get("page"),
                source_locator=hit.get("source_locator"), header_context=hit.get("header_context"), extraction_snapshot=hit.get("extraction"),
                excerpt=str(hit["excerpt"])[:400], query=query[:300], rank=rank, recorded_at=datetime.now(UTC)))
            self._session.flush()
