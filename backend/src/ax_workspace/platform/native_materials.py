"""Immutable native revisions remain in their owner ledger; Attachment only gives them a material identity.

회의는 판을 쌓지 않으므로 **회의당 자료 하나**다 (SCAX-SPEC-004 §5.4-3 · §11.1) — 옛 판 계열
(`meeting_raw`·`meeting_refinement`·`meeting_recording`)은 파일 재전사와 함께 폐기했고, 그 자리를
`meeting_transcript` 하나가 대신한다: 확정 발화 전량이 곧 그 회의의 원문이다.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select

from ax_workspace.modules.work.material_extraction import ExtractedBlock, ExtractedChunk, ExtractionOutcome, iter_chunks
from ax_workspace.platform.extraction_spool import ExtractionSpool
from ax_workspace.platform.persistence import (
    DailyReportRecord, DailyReportSubmissionRecord,
    AttachmentBindingRecord, AttachmentRecord, MeetingRecord, MeetingTranscriptRecord,
)

#: 제목이 없는 회의도 자료 목록에 이름으로 선다 — 후보가 있으면 그것을, 없으면 이 말을 쓴다.
TITLELESS_MEETING = "제목 없는 회의"


def meeting_material_title(meeting) -> str:
    return meeting.title or meeting.title_candidate or TITLELESS_MEETING

NATIVE_TYPES: dict[str, type] = {}
NATIVE_CONTENT_TYPE = "application/vnd.scax.native-revision+json"


#: 회의 전사 — revision id 자리에 **회의 id** 가 온다. 판이 없으므로 회의 하나에 자료 하나다.
MEETING_TRANSCRIPT = "meeting_transcript"


def source_ref(kind: str, revision_id: UUID) -> str:
    if kind not in NATIVE_TYPES and kind not in {"report_submission", MEETING_TRANSCRIPT}:
        raise ValueError("unknown native material kind")
    return f"native:{kind}:{revision_id}"


def canonical_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def material_id_for(kind: str, revision_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"scax:material:{source_ref(kind, revision_id)}")


class NativeMaterialRepository:
    def __init__(self, session):
        self._session = session

    def report_source(self, submission_id, *, lock=False):
        statement = select(DailyReportSubmissionRecord).where(DailyReportSubmissionRecord.id == submission_id)
        submission = self._session.scalar(statement.with_for_update() if lock else statement)
        report = self._session.get(DailyReportRecord, submission.report_id) if submission is not None else None
        if submission is None or report is None:
            raise ValueError("report submission was not found")
        return submission, report

    def meeting_transcript_source(self, meeting_id, *, lock=False):
        """그 회의의 확정 발화 전량. 없으면 자료가 될 것이 없다."""
        statement = select(MeetingRecord).where(MeetingRecord.id == meeting_id)
        meeting = self._session.scalar(statement.with_for_update() if lock else statement)
        if meeting is None:
            raise ValueError("meeting was not found")
        blocks = list(
            self._session.scalars(
                select(MeetingTranscriptRecord)
                .where(MeetingTranscriptRecord.meeting_id == meeting_id)
                .order_by(MeetingTranscriptRecord.seq)
            )
        )
        if not blocks:
            raise ValueError("meeting has no settled speech yet")
        return meeting, blocks

    def payload(self, kind, revision_id):
        if kind == MEETING_TRANSCRIPT:
            meeting, blocks = self.meeting_transcript_source(revision_id)
            return {
                "format_version": 1,
                "source_kind": kind,
                "source_revision_id": str(meeting.id),
                "meeting_id": str(meeting.id),
                "title": meeting_material_title(meeting),
                "blocks": [
                    {
                        "block_id": str(block.id),
                        "seq": block.seq,
                        "speaker_label": block.speaker_label,
                        "at_ms": block.at_ms,
                        "end_ms": block.end_ms,
                        "text": block.text,
                    }
                    for block in blocks
                ],
            }
        if kind != "report_submission":
            raise ValueError("unknown native material kind")
        submission, report = self.report_source(revision_id)
        return {"format_version": 1, "source_kind": kind, "source_revision_id": str(submission.id),
                "report_id": str(report.id), "report_date": submission.report_date, "revision": submission.submission_version,
                "body": submission.body, "source_refs": submission.source_refs}

    def ensure_report(self, submission_id):
        submission, report = self.report_source(submission_id, lock=True)
        identifier = material_id_for("report_submission", submission_id)
        attachment = self._session.get(AttachmentRecord, identifier)
        if attachment is None:
            data = canonical_bytes(self.payload("report_submission", submission_id))
            reference = source_ref("report_submission", submission_id)
            attachment = AttachmentRecord(id=identifier, source_kind="native_revision", source_ref=reference,
                name=f"일일 보고 · {submission.report_date} · v{submission.submission_version}", content_type=NATIVE_CONTENT_TYPE,
                size_bytes=len(data), integrity_ref=f"sha256:{hashlib.sha256(data).hexdigest()}", provenance=reference,
                uploaded_by=report.owner_id, created_at=submission.submitted_at)
            self._session.add(attachment)
            self._session.flush()
        return self._binding(attachment, "report_submission", submission_id, report.owner_id), attachment

    def ensure(self, kind, revision_id):
        if kind == MEETING_TRANSCRIPT:
            return self.ensure_meeting_transcript(revision_id)
        if kind != "report_submission":
            raise ValueError("unknown native material kind")
        return self.ensure_report(revision_id)

    def registered(self, kind, revision_id):
        """Return an existing native owner binding without creating source data."""
        identifier = material_id_for(kind, revision_id)
        attachment = self._session.get(AttachmentRecord, identifier)
        if attachment is None:
            return None
        binding = self._session.get(AttachmentBindingRecord, uuid5(identifier, "native-owner-binding"))
        return None if binding is None else (binding, attachment)

    def ensure_meeting_transcript(self, meeting_id):
        """회의 전사 자료 하나. **자라는 원문이라 내용이 바뀌면 무결성 ref 도 바뀐다** — 그때 새 추출이 걸린다."""
        meeting, _ = self.meeting_transcript_source(meeting_id, lock=True)
        identifier = material_id_for(MEETING_TRANSCRIPT, meeting_id)
        data = canonical_bytes(self.payload(MEETING_TRANSCRIPT, meeting_id))
        digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
        reference = source_ref(MEETING_TRANSCRIPT, meeting_id)
        attachment = self._session.get(AttachmentRecord, identifier)
        name = f"회의 원문 · {meeting_material_title(meeting)}"[:300]
        if attachment is None:
            attachment = AttachmentRecord(
                id=identifier, source_kind="native_revision", source_ref=reference, name=name,
                content_type=NATIVE_CONTENT_TYPE, size_bytes=len(data), integrity_ref=digest,
                provenance=reference, uploaded_by=meeting.owner_id, created_at=meeting.created_at,
            )
            self._session.add(attachment)
            self._session.flush()
        elif attachment.integrity_ref != digest:
            # 회의가 더 말했다 — 같은 자료의 새 내용이다. 판을 만들지 않고 그 자리를 갱신한다.
            attachment.integrity_ref = digest
            attachment.size_bytes = len(data)
            attachment.name = name
            self._session.flush()
        return self._binding(attachment, MEETING_TRANSCRIPT, meeting_id, meeting.owner_id), attachment

    def _binding(self, attachment, kind, revision_id, actor_id):
        binding_id = uuid5(attachment.id, "native-owner-binding")
        binding = self._session.get(AttachmentBindingRecord, binding_id)
        if binding is None:
            binding = AttachmentBindingRecord(id=binding_id, attachment_id=attachment.id, context_type=kind, context_id=str(revision_id),
                                              role="input", bound_by=actor_id, bound_at=datetime.now(UTC))
            self._session.add(binding)
            self._session.flush()
        return binding


class NativeRevisionStorage:
    def __init__(self, sessions):
        self._sessions = sessions

    def get(self, reference):
        prefix, kind, identifier = reference.split(":", 2)
        if prefix != "native":
            raise ValueError("invalid native revision reference")
        with self._sessions() as session:
            return canonical_bytes(NativeMaterialRepository(session).payload(kind, UUID(identifier)))


class NativeRevisionExtractor:
    def extract(self, *, name, content_type, data):
        del name, content_type
        payload = json.loads(data)
        if payload.get("format_version") != 1:
            raise ValueError("unsupported native revision format")
        blocks, chunks = ExtractionSpool(ExtractedBlock), ExtractionSpool(ExtractedChunk)
        total = 0
        try:
            if payload["source_kind"] == "report_submission":
                locator = {"kind": "report_submission", "source_revision_id": payload["source_revision_id"],
                           "report_id": payload["report_id"], "submission_version": payload["revision"]}
                blocks.append(ExtractedBlock(0, "report_body", payload["body"], locator_label=f"제출본 v{payload['revision']}", source_locator=locator))
                for chunk in iter_chunks(payload["body"]):
                    chunks.append(replace(chunk, block_sequence=0))
                if not chunks:
                    blocks.close()
                    chunks.close()
                    return ExtractionOutcome(status="failed", extractor="native_revision", failure_reason="empty_content", coverage={"complete": False})
                return ExtractionOutcome(status="completed", extractor="native_revision", blocks=blocks, chunks=chunks,
                    char_count=len(payload["body"]), coverage={"complete": True, "unit": "report_body", "total_units": 1, "processed_units": 1})
            if payload["source_kind"] == "meeting_transcript":
                # 한 블록이 한 조각이다 — 근거 칩이 딛는 단위와 검색이 세는 단위를 같게 둔다.
                for index, block in enumerate(payload["blocks"]):
                    locator = {
                        "kind": "meeting_transcript", "source_revision_id": payload["source_revision_id"],
                        "meeting_id": payload["meeting_id"], "block_id": block["block_id"],
                        "seq": block["seq"], "start_ms": block["at_ms"], "end_ms": block["end_ms"],
                        "speaker_label": block["speaker_label"],
                    }
                    text = f"화자 {block['speaker_label']} [{_clock(block['at_ms'])}] {block['text']}"
                    extracted = ExtractedBlock(
                        index, "transcript_block", text,
                        locator_label=f"화자 {block['speaker_label']} · {_clock(block['at_ms'])}",
                        source_locator=locator,
                    )
                    blocks.append(extracted)
                    for chunk in iter_chunks(extracted.text, sequence_start=len(chunks), char_offset=total):
                        chunks.append(replace(chunk, block_sequence=extracted.sequence))
                    total += len(extracted.text)
                if not chunks:
                    blocks.close()
                    chunks.close()
                    return ExtractionOutcome(status="failed", extractor="native_revision", failure_reason="empty_content", coverage={"complete": False})
                return ExtractionOutcome(status="completed", extractor="native_revision", blocks=blocks, chunks=chunks,
                    char_count=total, coverage={"complete": True, "unit": "transcript_block", "total_units": len(blocks), "processed_units": len(blocks)})
            raise ValueError("unsupported native revision source")
        except BaseException:
            blocks.close()
            chunks.close()
            raise


def _clock(at_ms: int) -> str:
    """`mm:ss` — 사람이 스크립트에서 찾아 들을 수 있는 모양."""
    seconds = max(0, int(at_ms)) // 1000
    return f"{seconds // 60:02d}:{seconds % 60:02d}"
