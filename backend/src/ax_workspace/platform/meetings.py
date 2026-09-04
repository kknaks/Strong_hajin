"""SQLAlchemy adapter for Meeting-owned identity, authorization relations, and note history."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ax_workspace.modules.meetings.transcription import FinalTranscriptSegment
from ax_workspace.modules.meetings.refinement import RefinedTranscriptSegment
from ax_workspace.modules.meetings.summary import SummaryStatement
from ax_workspace.platform.persistence import (
    ActivityEventRecord,
    EmploymentPeriodRecord,
    MeetingAttendeeRecord,
    MeetingNoteRecord,
    MeetingNoteVersionRecord,
    MeetingRecordingRecord,
    MeetingRawTranscriptRevisionRecord,
    MeetingRawTranscriptSegmentRecord,
    MeetingTranscriptRefinementRevisionRecord,
    MeetingTranscriptRefinementSegmentRecord,
    MeetingSummaryEvidenceRecord,
    MeetingSummarySuggestionRecord,
    MeetingSpeakerIdentityAssignmentRecord,
    MeetingRecord,
    MemberRecord,
    MembershipRecord,
    ResourceRelationshipRecord,
)


class SqlAlchemyMeetingRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        organization_id: str,
        owner_id: str,
        title: str,
        starts_at: datetime,
        ends_at: datetime,
        visibility: str,
        attendee_ids: list[str],
    ) -> MeetingRecord:
        now = datetime.now(UTC)
        meeting = MeetingRecord(
            organization_id=organization_id,
            owner_id=owner_id,
            title=title,
            starts_at=starts_at,
            ends_at=ends_at,
            visibility=visibility,
            lifecycle="scheduled",
            version=1,
            created_at=now,
            updated_at=now,
        )
        self._session.add(meeting)
        self._session.flush()
        for member_id in attendee_ids:
            self._session.add(
                MeetingAttendeeRecord(
                    meeting_id=meeting.id,
                    member_id=member_id,
                    invited_by=owner_id,
                    attendance_state="invited",
                    added_at=now,
                )
            )
        return meeting

    def meetings_in_organizations(self, organization_ids: frozenset[str]) -> list[MeetingRecord]:
        if not organization_ids:
            return []
        return list(
            self._session.scalars(
                select(MeetingRecord)
                .where(MeetingRecord.organization_id.in_(organization_ids))
                .order_by(MeetingRecord.starts_at, MeetingRecord.id)
            )
        )

    def meeting(self, meeting_id: UUID, *, lock: bool = False) -> MeetingRecord | None:
        statement = select(MeetingRecord).where(MeetingRecord.id == meeting_id)
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self._session.scalar(statement)

    def attendee_ids(self, meeting: MeetingRecord) -> set[str]:
        return set(
            self._session.scalars(
                select(MeetingAttendeeRecord.member_id).where(
                    MeetingAttendeeRecord.meeting_id == meeting.id,
                    MeetingAttendeeRecord.removed_at.is_(None),
                )
            )
        )

    def is_shared_with(self, meeting: MeetingRecord, member_id: str) -> bool:
        now = datetime.now(UTC)
        return (
            self._session.scalar(
                select(ResourceRelationshipRecord.id).where(
                    ResourceRelationshipRecord.resource_type == "meeting",
                    ResourceRelationshipRecord.resource_id == str(meeting.id),
                    ResourceRelationshipRecord.relationship_kind == "share",
                    ResourceRelationshipRecord.member_id == member_id,
                    ResourceRelationshipRecord.valid_from <= now,
                    or_(
                        ResourceRelationshipRecord.valid_until.is_(None),
                        ResourceRelationshipRecord.valid_until > now,
                    ),
                )
            )
            is not None
        )

    def member_display_name(self, member_id: str) -> str | None:
        member = self._session.get(MemberRecord, member_id)
        return member.display_name if member is not None else None

    def is_active_member_in_organization(self, member_id: str, organization_id: str) -> bool:
        now = datetime.now(UTC)
        return (
            self._session.scalar(
                select(MemberRecord.id)
                .join(EmploymentPeriodRecord, EmploymentPeriodRecord.member_id == MemberRecord.id)
                .join(MembershipRecord, MembershipRecord.member_id == MemberRecord.id)
                .where(
                    MemberRecord.id == member_id,
                    MemberRecord.employment_state == "active",
                    EmploymentPeriodRecord.state == "active",
                    EmploymentPeriodRecord.ended_at.is_(None),
                    MembershipRecord.organization_id == organization_id,
                    MembershipRecord.valid_from <= now,
                    or_(MembershipRecord.valid_until.is_(None), MembershipRecord.valid_until > now),
                )
            )
            is not None
        )

    def add_share(self, meeting: MeetingRecord, member_id: str, actor_id: str) -> None:
        now = datetime.now(UTC)
        existing = self._session.scalar(
            select(ResourceRelationshipRecord)
            .where(
                ResourceRelationshipRecord.resource_type == "meeting",
                ResourceRelationshipRecord.resource_id == str(meeting.id),
                ResourceRelationshipRecord.relationship_kind == "share",
                ResourceRelationshipRecord.member_id == member_id,
            )
            .order_by(ResourceRelationshipRecord.valid_from.desc())
        )
        if existing is not None and existing.valid_until is None:
            return
        self._session.add(
            ResourceRelationshipRecord(
                member_id=member_id,
                resource_type="meeting",
                resource_id=str(meeting.id),
                relationship_kind="share",
                valid_from=now,
            )
        )

    def revoke_share(self, meeting: MeetingRecord, member_id: str) -> bool:
        relationship = self._session.scalar(
            select(ResourceRelationshipRecord)
            .where(
                ResourceRelationshipRecord.resource_type == "meeting",
                ResourceRelationshipRecord.resource_id == str(meeting.id),
                ResourceRelationshipRecord.relationship_kind == "share",
                ResourceRelationshipRecord.member_id == member_id,
                ResourceRelationshipRecord.valid_until.is_(None),
            )
            .with_for_update()
        )
        if relationship is None:
            return False
        relationship.valid_until = datetime.now(UTC)
        return True

    def touch(self, meeting: MeetingRecord) -> None:
        meeting.updated_at = datetime.now(UTC)

    def append_audit(
        self,
        meeting: MeetingRecord,
        actor_id: str,
        event_kind: str,
        summary: str,
        *,
        before_ref: str | None = None,
    ) -> None:
        self._session.add(
            ActivityEventRecord(
                target_type="meeting",
                target_id=str(meeting.id),
                event_kind=event_kind,
                actor_kind="member",
                actor_id=actor_id,
                before_ref=before_ref,
                after_ref=f"meeting:{meeting.id}@{meeting.version}",
                reason=None,
                safe_summary=summary[:300],
                occurred_at=datetime.now(UTC),
            )
        )

    def note(self, meeting: MeetingRecord, *, lock: bool = False) -> MeetingNoteRecord | None:
        statement = select(MeetingNoteRecord).where(MeetingNoteRecord.meeting_id == meeting.id)
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self._session.scalar(statement)

    def create_note(
        self,
        meeting: MeetingRecord,
        body: str,
        author_id: str,
        source_evidence: list[dict[str, Any]] | None = None,
    ) -> MeetingNoteRecord:
        now = datetime.now(UTC)
        note = MeetingNoteRecord(
            meeting_id=meeting.id,
            lifecycle="draft",
            current_version=1,
            created_at=now,
            finalized_at=None,
            finalized_by=None,
        )
        self._session.add(note)
        self._session.flush()
        self._session.add(
            MeetingNoteVersionRecord(
                note_id=note.id,
                version=1,
                body=body,
                source_evidence=source_evidence or [],
                created_by=author_id,
                created_at=now,
            )
        )
        return note

    def append_note_version(
        self,
        note: MeetingNoteRecord,
        body: str,
        author_id: str,
        source_evidence: list[dict[str, Any]] | None = None,
    ) -> MeetingNoteVersionRecord:
        note.current_version += 1
        version = MeetingNoteVersionRecord(
            note_id=note.id,
            version=note.current_version,
            body=body,
            source_evidence=source_evidence or [],
            created_by=author_id,
            created_at=datetime.now(UTC),
        )
        self._session.add(version)
        self._session.flush()
        return version

    def note_versions(self, note: MeetingNoteRecord) -> list[MeetingNoteVersionRecord]:
        return list(
            self._session.scalars(
                select(MeetingNoteVersionRecord)
                .where(MeetingNoteVersionRecord.note_id == note.id)
                .order_by(MeetingNoteVersionRecord.version)
            )
        )

    def finalize_note(self, note: MeetingNoteRecord, actor_id: str) -> None:
        note.lifecycle = "finalized"
        note.finalized_at = datetime.now(UTC)
        note.finalized_by = actor_id

    def create_recording(self, meeting: MeetingRecord, actor_id: str, purpose: str) -> MeetingRecordingRecord:
        now = datetime.now(UTC)
        recording = MeetingRecordingRecord(
            meeting_id=meeting.id,
            actor_id=actor_id,
            purpose=purpose,
            state="recording",
            version=1,
            provider_client_reference_id="pending",
            started_at=now,
            ended_at=None,
            created_at=now,
            updated_at=now,
        )
        self._session.add(recording)
        self._session.flush()
        recording.provider_client_reference_id = f"meeting-recording:{recording.id}"
        return recording

    def recording(
        self,
        meeting: MeetingRecord,
        recording_id: UUID,
        *,
        lock: bool = False,
    ) -> MeetingRecordingRecord | None:
        statement = select(MeetingRecordingRecord).where(
            MeetingRecordingRecord.id == recording_id,
            MeetingRecordingRecord.meeting_id == meeting.id,
        )
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self._session.scalar(statement)

    def recordings(self, meeting: MeetingRecord) -> list[MeetingRecordingRecord]:
        return list(
            self._session.scalars(
                select(MeetingRecordingRecord)
                .where(MeetingRecordingRecord.meeting_id == meeting.id)
                .order_by(MeetingRecordingRecord.created_at, MeetingRecordingRecord.id)
            )
        )

    def recording_by_id(self, recording_id: UUID, *, lock: bool = False) -> MeetingRecordingRecord | None:
        statement = select(MeetingRecordingRecord).where(MeetingRecordingRecord.id == recording_id)
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self._session.scalar(statement)

    def raw_transcript_for_provider(
        self,
        recording: MeetingRecordingRecord,
        provider_reference: str,
    ) -> MeetingRawTranscriptRevisionRecord | None:
        return self._session.scalar(
            select(MeetingRawTranscriptRevisionRecord).where(
                MeetingRawTranscriptRevisionRecord.recording_id == recording.id,
                MeetingRawTranscriptRevisionRecord.provider_reference == provider_reference,
            )
        )

    def create_raw_transcript(
        self,
        recording: MeetingRecordingRecord,
        *,
        provider: str,
        provider_reference: str,
        segments: list[FinalTranscriptSegment],
    ) -> MeetingRawTranscriptRevisionRecord:
        now = datetime.now(UTC)
        latest = self._session.scalar(
            select(MeetingRawTranscriptRevisionRecord.revision)
            .where(MeetingRawTranscriptRevisionRecord.recording_id == recording.id)
            .order_by(MeetingRawTranscriptRevisionRecord.revision.desc())
            .limit(1)
        )
        transcript = MeetingRawTranscriptRevisionRecord(
            recording_id=recording.id,
            revision=(int(latest) if latest is not None else 0) + 1,
            state="completed",
            source_kind="async_final",
            provider=provider,
            provider_reference=provider_reference,
            created_at=now,
            finalized_at=now,
        )
        self._session.add(transcript)
        self._session.flush()
        for sequence, segment in enumerate(segments, start=1):
            self._session.add(
                MeetingRawTranscriptSegmentRecord(
                    transcript_revision_id=transcript.id,
                    sequence=sequence,
                    source_segment_key=segment.source_segment_key.strip(),
                    start_ms=segment.start_ms,
                    end_ms=segment.end_ms,
                    text=segment.text.strip(),
                    speaker_label=segment.speaker_label.strip() if segment.speaker_label else None,
                    confirmed_member_id=None,
                    created_at=now,
                )
            )
        recording.state = "transcribed"
        recording.updated_at = now
        self._session.flush()
        return transcript

    def raw_transcript_segments(
        self,
        transcript: MeetingRawTranscriptRevisionRecord,
    ) -> list[MeetingRawTranscriptSegmentRecord]:
        return list(
            self._session.scalars(
                select(MeetingRawTranscriptSegmentRecord)
                .where(MeetingRawTranscriptSegmentRecord.transcript_revision_id == transcript.id)
                .order_by(MeetingRawTranscriptSegmentRecord.sequence)
            )
        )

    def raw_transcript(
        self,
        transcript_id: UUID,
        *,
        lock: bool = False,
    ) -> MeetingRawTranscriptRevisionRecord | None:
        statement = select(MeetingRawTranscriptRevisionRecord).where(
            MeetingRawTranscriptRevisionRecord.id == transcript_id
        )
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self._session.scalar(statement)

    def latest_refinement(
        self,
        transcript: MeetingRawTranscriptRevisionRecord,
    ) -> MeetingTranscriptRefinementRevisionRecord | None:
        return self._session.scalar(
            select(MeetingTranscriptRefinementRevisionRecord)
            .where(MeetingTranscriptRefinementRevisionRecord.raw_transcript_revision_id == transcript.id)
            .order_by(MeetingTranscriptRefinementRevisionRecord.revision.desc())
            .limit(1)
        )

    def create_refinement(
        self,
        transcript: MeetingRawTranscriptRevisionRecord,
        *,
        provider_call_ref: str | None,
        content_hash: str,
        segments: list[RefinedTranscriptSegment],
    ) -> MeetingTranscriptRefinementRevisionRecord:
        import hashlib

        now = datetime.now(UTC)
        latest = self.latest_refinement(transcript)
        refinement = MeetingTranscriptRefinementRevisionRecord(
            raw_transcript_revision_id=transcript.id,
            revision=(int(latest.revision) if latest is not None else 0) + 1,
            state="completed",
            provider_call_ref=provider_call_ref,
            content_hash=content_hash or hashlib.sha256(b"").hexdigest(),
            created_at=now,
            completed_at=now,
        )
        self._session.add(refinement)
        self._session.flush()
        raw_by_key = {
            row.source_segment_key: row
            for row in self.raw_transcript_segments(transcript)
        }
        for sequence, segment in enumerate(segments, start=1):
            covered = [
                raw_by_key[key]
                for key in raw_by_key
                if raw_by_key[segment.raw_start_source_key].sequence
                <= raw_by_key[key].sequence
                <= raw_by_key[segment.raw_end_source_key].sequence
            ]
            confirmed_ids = {row.confirmed_member_id for row in covered}
            self._session.add(
                MeetingTranscriptRefinementSegmentRecord(
                    refinement_revision_id=refinement.id,
                    sequence=sequence,
                    raw_start_segment_id=raw_by_key[segment.raw_start_source_key].id,
                    raw_end_segment_id=raw_by_key[segment.raw_end_source_key].id,
                    start_ms=segment.start_ms,
                    end_ms=segment.end_ms,
                    text=segment.text,
                    speaker_label=segment.speaker_label,
                    # Human assignment wins over any model-provided turn label.
                    confirmed_member_id=next(iter(confirmed_ids)) if len(confirmed_ids) == 1 else None,
                    correction_kind=segment.correction_kind,
                    confidence=segment.confidence,
                    created_at=now,
                )
            )
        self._session.flush()
        return refinement

    def refinement_segments(
        self,
        refinement: MeetingTranscriptRefinementRevisionRecord,
    ) -> list[MeetingTranscriptRefinementSegmentRecord]:
        return list(
            self._session.scalars(
                select(MeetingTranscriptRefinementSegmentRecord)
                .where(MeetingTranscriptRefinementSegmentRecord.refinement_revision_id == refinement.id)
                .order_by(MeetingTranscriptRefinementSegmentRecord.sequence)
            )
        )

    def refinement(
        self,
        refinement_id: UUID,
        *,
        lock: bool = False,
    ) -> MeetingTranscriptRefinementRevisionRecord | None:
        statement = select(MeetingTranscriptRefinementRevisionRecord).where(
            MeetingTranscriptRefinementRevisionRecord.id == refinement_id
        )
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self._session.scalar(statement)

    def summary_for_refinement(
        self,
        refinement: MeetingTranscriptRefinementRevisionRecord,
        kind: str,
    ) -> MeetingSummarySuggestionRecord | None:
        return self._session.scalar(
            select(MeetingSummarySuggestionRecord).where(
                MeetingSummarySuggestionRecord.refinement_revision_id == refinement.id,
                MeetingSummarySuggestionRecord.kind == kind,
            )
        )

    def create_summary(
        self,
        refinement: MeetingTranscriptRefinementRevisionRecord,
        *,
        kind: str,
        body: str,
        provider_call_ref: str | None,
        content_hash: str,
        statements: list[SummaryStatement],
    ) -> MeetingSummarySuggestionRecord:
        now = datetime.now(UTC)
        raw = self._session.get(
            MeetingRawTranscriptRevisionRecord,
            refinement.raw_transcript_revision_id,
        )
        if raw is None:
            raise ValueError("refinement raw transcript was not found")
        recording = self._session.get(MeetingRecordingRecord, raw.recording_id)
        if recording is None:
            raise ValueError("summary recording was not found")
        summary = MeetingSummarySuggestionRecord(
            meeting_id=recording.meeting_id,
            raw_transcript_revision_id=raw.id,
            refinement_revision_id=refinement.id,
            kind=kind,
            state="completed",
            body=body,
            provider_call_ref=provider_call_ref,
            content_hash=content_hash,
            created_at=now,
            completed_at=now,
        )
        self._session.add(summary)
        self._session.flush()
        refined = {segment.sequence: segment for segment in self.refinement_segments(refinement)}
        raw_by_id = {
            segment.id: segment
            for segment in self.raw_transcript_segments(raw)
        }
        for index, statement in enumerate(statements, start=1):
            start = refined[statement.refinement_start_sequence]
            end = refined[statement.refinement_end_sequence]
            self._session.add(
                MeetingSummaryEvidenceRecord(
                    summary_id=summary.id,
                    statement_index=index,
                    statement_kind=statement.kind,
                    statement_text=statement.text,
                    refinement_start_segment_id=start.id,
                    refinement_end_segment_id=end.id,
                    raw_start_segment_id=raw_by_id[start.raw_start_segment_id].id,
                    raw_end_segment_id=raw_by_id[end.raw_end_segment_id].id,
                    raw_start_ms=raw_by_id[start.raw_start_segment_id].start_ms,
                    raw_end_ms=raw_by_id[end.raw_end_segment_id].end_ms,
                    created_at=now,
                )
            )
        self._session.flush()
        return summary

    def summary_evidence(
        self,
        summary: MeetingSummarySuggestionRecord,
    ) -> list[MeetingSummaryEvidenceRecord]:
        return list(
            self._session.scalars(
                select(MeetingSummaryEvidenceRecord)
                .where(MeetingSummaryEvidenceRecord.summary_id == summary.id)
                .order_by(MeetingSummaryEvidenceRecord.statement_index)
            )
        )

    def summary(
        self,
        meeting: MeetingRecord,
        summary_id: UUID,
        *,
        lock: bool = False,
    ) -> MeetingSummarySuggestionRecord | None:
        statement = select(MeetingSummarySuggestionRecord).where(
            MeetingSummarySuggestionRecord.id == summary_id,
            MeetingSummarySuggestionRecord.meeting_id == meeting.id,
        )
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self._session.scalar(statement)

    def speaker_assignments(
        self,
        transcript: MeetingRawTranscriptRevisionRecord,
    ) -> list[MeetingSpeakerIdentityAssignmentRecord]:
        return list(
            self._session.scalars(
                select(MeetingSpeakerIdentityAssignmentRecord)
                .where(
                    MeetingSpeakerIdentityAssignmentRecord.transcript_revision_id == transcript.id,
                    MeetingSpeakerIdentityAssignmentRecord.state == "active",
                )
                .order_by(MeetingSpeakerIdentityAssignmentRecord.confirmed_at, MeetingSpeakerIdentityAssignmentRecord.id)
            )
        )

    def assign_speaker_identity(
        self,
        meeting: MeetingRecord,
        transcript: MeetingRawTranscriptRevisionRecord,
        *,
        speaker_label: str,
        member_id: str,
        scope: str,
        raw_start_segment: MeetingRawTranscriptSegmentRecord,
        raw_end_segment: MeetingRawTranscriptSegmentRecord,
        confirmed_by: str,
    ) -> MeetingSpeakerIdentityAssignmentRecord:
        now = datetime.now(UTC)
        assignment = MeetingSpeakerIdentityAssignmentRecord(
            meeting_id=meeting.id,
            transcript_revision_id=transcript.id,
            speaker_label=speaker_label,
            member_id=member_id,
            scope=scope,
            raw_start_segment_id=raw_start_segment.id,
            raw_end_segment_id=raw_end_segment.id,
            source_audio_start_ms=raw_start_segment.start_ms,
            source_audio_end_ms=raw_end_segment.end_ms,
            source="human_confirmed",
            state="active",
            confirmed_by=confirmed_by,
            confirmed_at=now,
        )
        self._session.add(assignment)
        self._session.flush()
        return assignment

    def adopt_summary(
        self,
        summary: MeetingSummarySuggestionRecord,
        note_version: MeetingNoteVersionRecord,
        actor_id: str,
    ) -> None:
        summary.state = "adopted"
        summary.version += 1
        summary.adopted_note_version_id = note_version.id
        summary.adopted_by = actor_id
        summary.adopted_at = datetime.now(UTC)
        self._session.flush()

    def complete_recording(self, recording: MeetingRecordingRecord, stored: Any) -> None:
        now = datetime.now(UTC)
        recording.storage_key = stored.storage_key
        recording.original_name = stored.original_name
        recording.content_type = stored.content_type
        recording.size_bytes = stored.size_bytes
        recording.sha256 = stored.sha256
        recording.state = "uploaded"
        recording.version += 1
        recording.ended_at = now
        recording.updated_at = now
