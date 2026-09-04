"""SQLAlchemy adapter for Meeting-owned identity, authorization relations, and note history."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ax_workspace.platform.persistence import (
    ActivityEventRecord,
    EmploymentPeriodRecord,
    MeetingAttendeeRecord,
    MeetingNoteRecord,
    MeetingNoteVersionRecord,
    MeetingRecordingRecord,
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

    def create_note(self, meeting: MeetingRecord, body: str, author_id: str) -> MeetingNoteRecord:
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
                source_evidence=[],
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
