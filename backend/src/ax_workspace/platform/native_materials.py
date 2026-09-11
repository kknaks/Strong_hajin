"""Immutable native revisions remain in their owner ledger; Attachment only gives them a material identity."""
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
    AttachmentBindingRecord, AttachmentRecord, MeetingNoteRecord, MeetingNoteVersionRecord, MeetingRawTranscriptRevisionRecord, MeetingRawTranscriptSegmentRecord,
    MeetingRecordingRecord, MeetingTranscriptRefinementRevisionRecord, MeetingTranscriptRefinementSegmentRecord,
)

NATIVE_TYPES = {"meeting_raw": MeetingRawTranscriptRevisionRecord, "meeting_refinement": MeetingTranscriptRefinementRevisionRecord}
NATIVE_CONTENT_TYPE = "application/vnd.scax.native-revision+json"


def source_ref(kind: str, revision_id: UUID) -> str:
    if kind not in NATIVE_TYPES and kind not in {"meeting_note", "meeting_recording", "report_submission"}:
        raise ValueError("unknown native material kind")
    return f"native:{kind}:{revision_id}"


def canonical_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def material_id_for(kind: str, revision_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"scax:material:{source_ref(kind, revision_id)}")


class NativeMaterialRepository:
    def __init__(self, session):
        self._session = session

    def _revision(self, kind, revision_id, *, lock=False):
        model = NATIVE_TYPES.get(kind)
        if model is None:
            raise ValueError("unknown native material kind")
        statement = select(model).where(model.id == revision_id)
        row = self._session.scalar(statement.with_for_update() if lock else statement)
        if row is None or row.state != "completed":
            raise ValueError("native revision is not complete")
        raw = row if kind == "meeting_raw" else self._session.get(MeetingRawTranscriptRevisionRecord, row.raw_transcript_revision_id)
        if raw is None or raw.source_kind == "realtime" or raw.state != "completed":
            raise ValueError("native revision is not a finalized recording transcript")
        recording = self._session.get(MeetingRecordingRecord, raw.recording_id)
        if recording is None or not recording.storage_key or not recording.sha256:
            raise ValueError("native revision has no recorded original")
        return row, raw, recording

    def report_source(self, submission_id, *, lock=False):
        statement = select(DailyReportSubmissionRecord).where(DailyReportSubmissionRecord.id == submission_id)
        submission = self._session.scalar(statement.with_for_update() if lock else statement)
        report = self._session.get(DailyReportRecord, submission.report_id) if submission is not None else None
        if submission is None or report is None:
            raise ValueError("report submission was not found")
        return submission, report

    def note_source(self, version_id, *, lock=False):
        statement = select(MeetingNoteVersionRecord).where(MeetingNoteVersionRecord.id == version_id)
        version = self._session.scalar(statement.with_for_update() if lock else statement)
        note = self._session.get(MeetingNoteRecord, version.note_id) if version is not None else None
        if version is None or note is None:
            raise ValueError("meeting note version was not found")
        return version, note

    def payload(self, kind, revision_id):
        if kind == "meeting_note":
            version, note = self.note_source(revision_id)
            return {
                "format_version": 1,
                "source_kind": kind,
                "source_revision_id": str(version.id),
                "meeting_id": str(note.meeting_id),
                "note_id": str(note.id),
                "revision": version.version,
                "body": version.body,
                "created_by": version.created_by,
                "created_at": version.created_at.isoformat(),
            }
        if kind == "report_submission":
            submission, report = self.report_source(revision_id)
            return {"format_version": 1, "source_kind": kind, "source_revision_id": str(submission.id),
                    "report_id": str(report.id), "report_date": submission.report_date, "revision": submission.submission_version,
                    "body": submission.body, "source_refs": submission.source_refs}
        row, raw, recording = self._revision(kind, revision_id)
        model = MeetingRawTranscriptSegmentRecord if kind == "meeting_raw" else MeetingTranscriptRefinementSegmentRecord
        owner_column = model.transcript_revision_id if kind == "meeting_raw" else model.refinement_revision_id
        segments = []
        for segment in self._session.scalars(select(model).where(owner_column == row.id).order_by(model.sequence)):
            item = {"segment_id": str(segment.id), "sequence": segment.sequence, "start_ms": segment.start_ms, "end_ms": segment.end_ms,
                    "text": segment.text, "speaker_label": segment.speaker_label}
            if kind == "meeting_refinement":
                item.update(raw_start_segment_id=str(segment.raw_start_segment_id), raw_end_segment_id=str(segment.raw_end_segment_id),
                            correction_kind=segment.correction_kind, confidence=segment.confidence)
            else:
                item["source_segment_key"] = segment.source_segment_key
            segments.append(item)
        return {"format_version": 1, "source_kind": kind, "source_revision_id": str(row.id), "revision": row.revision,
                "raw_transcript_revision_id": str(raw.id), "recording_id": str(recording.id),
                "recording_integrity_ref": f"sha256:{recording.sha256}", "segments": segments}

    def recording_source(self, recording_id, *, lock=False):
        statement = select(MeetingRecordingRecord).where(MeetingRecordingRecord.id == recording_id)
        recording = self._session.scalar(statement.with_for_update() if lock else statement)
        if recording is None or not recording.storage_key or not recording.sha256 or not recording.size_bytes:
            raise ValueError("recording has no uploaded original")
        return recording

    def ensure_recording(self, recording_id):
        recording = self.recording_source(recording_id, lock=True)
        identifier = material_id_for("meeting_recording", recording_id)
        attachment = self._session.get(AttachmentRecord, identifier)
        if attachment is None:
            reference = source_ref("meeting_recording", recording_id)
            attachment = AttachmentRecord(id=identifier, source_kind="native_recording", source_ref=reference,
                name=recording.original_name or "녹음", content_type=recording.content_type or "application/octet-stream",
                size_bytes=recording.size_bytes, integrity_ref=f"sha256:{recording.sha256}", provenance=reference,
                uploaded_by=recording.actor_id, created_at=recording.ended_at or recording.created_at)
            self._session.add(attachment)
            self._session.flush()
        return self._binding(attachment, "meeting_recording", recording_id, recording.actor_id), attachment

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

    def ensure_note(self, version_id):
        version, note = self.note_source(version_id, lock=True)
        identifier = material_id_for("meeting_note", version_id)
        attachment = self._session.get(AttachmentRecord, identifier)
        if attachment is None:
            data = canonical_bytes(self.payload("meeting_note", version_id))
            reference = source_ref("meeting_note", version_id)
            attachment = AttachmentRecord(
                id=identifier,
                source_kind="native_revision",
                source_ref=reference,
                name=f"회의록 · v{version.version}",
                content_type=NATIVE_CONTENT_TYPE,
                size_bytes=len(data),
                integrity_ref=f"sha256:{hashlib.sha256(data).hexdigest()}",
                provenance=reference,
                uploaded_by=version.created_by,
                created_at=version.created_at,
            )
            self._session.add(attachment)
            self._session.flush()
        return self._binding(attachment, "meeting_note", version_id, version.created_by), attachment

    def ensure(self, kind, revision_id):
        if kind == "meeting_note":
            return self.ensure_note(revision_id)
        if kind == "report_submission":
            return self.ensure_report(revision_id)
        if kind == "meeting_recording":
            return self.ensure_recording(revision_id)
        # Serialize first discovery and canonical writer retries on the existing immutable owner row.
        row, _, recording = self._revision(kind, revision_id, lock=True)
        reference = source_ref(kind, revision_id)
        identifier = material_id_for(kind, revision_id)
        attachment = self._session.get(AttachmentRecord, identifier)
        if attachment is None:
            data = canonical_bytes(self.payload(kind, revision_id))
            attachment = AttachmentRecord(id=identifier, source_kind="native_revision", source_ref=reference,
                name=f"{'원본 전사' if kind == 'meeting_raw' else '정제 전사'} · {recording.original_name or '녹음'} · r{row.revision}"[:300],
                content_type=NATIVE_CONTENT_TYPE, size_bytes=len(data), integrity_ref=f"sha256:{hashlib.sha256(data).hexdigest()}",
                provenance=reference, uploaded_by=recording.actor_id, created_at=row.created_at)
            self._session.add(attachment)
            self._session.flush()
        return self._binding(attachment, kind, revision_id, recording.actor_id), attachment

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
            if payload["source_kind"] == "meeting_note":
                locator = {
                    "kind": "meeting_note",
                    "meeting_id": payload["meeting_id"],
                    "note_id": payload["note_id"],
                    "source_revision_id": payload["source_revision_id"],
                    "note_version": payload["revision"],
                }
                blocks.append(ExtractedBlock(
                    0,
                    "meeting_note_body",
                    payload["body"],
                    locator_label=f"회의록 v{payload['revision']}",
                    source_locator=locator,
                ))
                for chunk in iter_chunks(payload["body"]):
                    chunks.append(replace(chunk, block_sequence=0))
                if not chunks:
                    blocks.close()
                    chunks.close()
                    return ExtractionOutcome(status="failed", extractor="native_revision", failure_reason="empty_content", coverage={"complete": False})
                return ExtractionOutcome(
                    status="completed",
                    extractor="native_revision",
                    blocks=blocks,
                    chunks=chunks,
                    char_count=len(payload["body"]),
                    coverage={"complete": True, "unit": "meeting_note_body", "total_units": 1, "processed_units": 1},
                )
            for segment in payload["segments"]:
                locator = {"kind": "meeting_transcript", "source_revision_id": payload["source_revision_id"],
                           "recording_id": payload["recording_id"], "segment_id": segment["segment_id"],
                           "segment_sequence": segment["sequence"], "start_ms": segment["start_ms"], "end_ms": segment["end_ms"],
                           "speaker_label": segment["speaker_label"],
                           **{key: segment[key] for key in ("raw_start_segment_id", "raw_end_segment_id") if key in segment}}
                block = ExtractedBlock(len(blocks), "transcript_segment", segment["text"],
                                       locator_label=f"발화 {segment['sequence']} · {segment['start_ms']}–{segment['end_ms']} ms", source_locator=locator)
                blocks.append(block)
                for chunk in iter_chunks(block.text, sequence_start=len(chunks), char_offset=total):
                    chunks.append(replace(chunk, block_sequence=block.sequence))
                total += len(block.text)
            if not chunks:
                blocks.close()
                chunks.close()
                return ExtractionOutcome(status="failed", extractor="native_revision", failure_reason="empty_content", coverage={"complete": False})
            return ExtractionOutcome(status="completed", extractor="native_revision", blocks=blocks, chunks=chunks, char_count=total,
                                     coverage={"complete": True, "unit": "segment", "total_units": len(blocks), "processed_units": len(blocks)})
        except BaseException:
            blocks.close()
            chunks.close()
            raise
