from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Identity, Index, Integer, JSON, String, Text, Uuid, UniqueConstraint, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


class Base(DeclarativeBase):
    pass


class OrganizationUnitTypeRecord(Base):
    """ERD ORGANIZATION_UNIT_TYPE — organization vocabulary (회사·본부·실·팀·파트); depth is not tied to type."""

    __tablename__ = "organization_unit_types"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class OrganizationUnitRecord(Base):
    """ERD ORGANIZATION_UNIT — hierarchy is a self reference; hierarchy never derives authorization."""

    __tablename__ = "organization_units"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("organization_units.id"))
    unit_type_id: Mapped[str | None] = mapped_column(ForeignKey("organization_unit_types.id"))
    lifecycle: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    abolished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class MemberRecord(Base):
    """ERD MEMBER — the person identity; the login account is a separate optional reference."""

    __tablename__ = "members"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    employment_state: Mapped[str] = mapped_column(String(40), nullable=False)
    #: 정규직·시간제처럼 어떤 형태로 일하는지. 출처가 말하지 않으면 비어 있고, 추정해서 채우지 않는다.
    employment_type: Mapped[str | None] = mapped_column(String(40))
    #: 연락처와 생년월일. 인사 정보라 명부에는 조직 관리 권한이 있는 사람에게만 실린다. 출처가 말하지 않으면
    #: 비어 있다 — 사람마다 있을 수도 없을 수도 있는 것이라 없는 것을 지어내지 않는다.
    #: 이 두 열은 로컬 demo DB에 `make sync-demo-schema`로만 적용된다 — 운영 스키마 반영은 별도 gate다.
    phone: Mapped[str | None] = mapped_column(String(40))
    birth_date: Mapped[date | None] = mapped_column(Date)
    account_ref: Mapped[str | None] = mapped_column(String(200))
    record_status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))


class AssistantCharacterPreferenceRecord(Base):
    """One member's presentation preference; never copied into Conversation or Message records."""

    __tablename__ = "assistant_character_preferences"

    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), primary_key=True)
    character_key: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class MemberCredentialRecord(Base):
    """A local email/password credential for one member. The password itself is never stored."""

    __tablename__ = "member_credentials"

    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(400), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))


class EmploymentPeriodRecord(Base):
    __tablename__ = "employment_periods"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    change_reason_ref: Mapped[str | None] = mapped_column(String(200))


class MembershipRecord(Base):
    __tablename__ = "memberships"
    __table_args__ = (Index("ix_memberships_member_org", "member_id", "organization_id"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), nullable=False)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organization_units.id"), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    membership_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="additional")
    change_reason_ref: Mapped[str | None] = mapped_column(String(200))


class PositionDefinitionRecord(Base):
    """ERD POSITION_DEFINITION — appointment vocabulary allowed per unit type; slot_key expresses exclusivity."""

    __tablename__ = "position_definitions"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    organization_unit_type_id: Mapped[str] = mapped_column(ForeignKey("organization_unit_types.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slot_key: Mapped[str] = mapped_column(String(100), nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class GradeRecord(Base):
    __tablename__ = "grades"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lifecycle: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class GradeAssignmentRecord(Base):
    __tablename__ = "grade_assignments"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), nullable=False)
    grade_id: Mapped[str] = mapped_column(ForeignKey("grades.id"), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobRecord(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class JobAssignmentRecord(Base):
    __tablename__ = "job_assignments"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), nullable=False)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    assignment_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="primary")
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CapabilityRecord(Base):
    __tablename__ = "capabilities"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    description: Mapped[str | None] = mapped_column(Text)
    group: Mapped[str | None] = mapped_column(String(100))


class RoleRecord(Base):
    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    sensitivity: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")
    lifecycle: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    #: Which product recommendation this role was installed from, and at which revision of it.
    template_key: Mapped[str | None] = mapped_column(String(100))
    template_version: Mapped[int | None] = mapped_column()
    #: Set once the organization changes the role. After that the product proposes; it does not rewrite.
    customized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RoleCapabilityRecord(Base):
    __tablename__ = "role_capabilities"
    __table_args__ = (UniqueConstraint("role_id", "capability_id", "mapping_version"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id"), nullable=False)
    capability_id: Mapped[str] = mapped_column(ForeignKey("capabilities.id"), nullable=False)
    mapping_version: Mapped[int] = mapped_column(nullable=False, default=1)


class AppointmentRecord(Base):
    """ERD APPOINTMENT — a position held at a unit; the linked role is the standard role snapshot that came with it."""

    __tablename__ = "appointments"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), nullable=False)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organization_units.id"), nullable=False)
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id"), nullable=False)
    position_definition_id: Mapped[str | None] = mapped_column(ForeignKey("position_definitions.id"))
    appointment_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="primary")
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    change_reason_ref: Mapped[str | None] = mapped_column(String(200))


class StandardGrantRuleRecord(Base):
    """ERD STANDARD_GRANT_RULE — which role an employment/membership/appointment suggests, and at what scope."""

    __tablename__ = "standard_grant_rules"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    trigger_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    trigger_source_ref: Mapped[str | None] = mapped_column(String(200))
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id"), nullable=False)
    scope_template: Mapped[str] = mapped_column(String(20), nullable=False)
    rule_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    lifecycle: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class AccessGrantRecord(Base):
    """ERD ACCESS_GRANT — a capability (or role snapshot) granted at a scope; revocation is independent of validity."""

    __tablename__ = "access_grants"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), nullable=False)
    capability_id: Mapped[str | None] = mapped_column(ForeignKey("capabilities.id"))
    role_id: Mapped[str | None] = mapped_column(ForeignKey("roles.id"))
    role_capability_version: Mapped[int | None] = mapped_column(Integer)
    scope_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="unit")
    scope_organization_id: Mapped[str | None] = mapped_column(ForeignKey("organization_units.id"))
    scope_ref: Mapped[str | None] = mapped_column(String(200))
    include_descendants: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    granted_by_member_id: Mapped[str | None] = mapped_column(String(100))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    origin_rule_id: Mapped[str | None] = mapped_column(ForeignKey("standard_grant_rules.id"))
    origin_rule_version: Mapped[int | None] = mapped_column(Integer)
    #: 프로젝트 참여가 만든 grant만 그 참여 종료에 따라 회수할 수 있게 하는 발생 근거.
    origin_project_assignment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("project_assignments.id"),
        index=True,
    )


class ResourceRelationshipRecord(Base):
    """ERD RESOURCE_RELATIONSHIP — a member's period-bound relationship (owner·requester·assignee·reviewer·cc) to a resource."""

    __tablename__ = "resource_relationships"
    __table_args__ = (Index("ix_resource_relationships_resource", "resource_type", "resource_id"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(100), nullable=False)
    relationship_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MeetingRecord(Base):
    """The stable Meeting identity; note, recording, and transcript lifecycles hang from it."""

    __tablename__ = "meetings"
    __table_args__ = (Index("ix_meetings_organization_starts_at", "organization_id", "starts_at"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organization_units.id"), nullable=False)
    owner_id: Mapped[str] = mapped_column(ForeignKey("members.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="private")
    lifecycle: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled")
    source_action_item_id: Mapped[UUID | None] = mapped_column(ForeignKey("action_items.id"))
    source_decision_item_id: Mapped[UUID | None] = mapped_column(ForeignKey("decision_items.id"))
    source_submission_id: Mapped[UUID | None] = mapped_column(ForeignKey("submissions.id"))
    source_review_decision_id: Mapped[UUID | None] = mapped_column(ForeignKey("review_decisions.id"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MeetingAttendeeRecord(Base):
    """Attendance is distinct from an explicit share: it says a person belongs in the meeting."""

    __tablename__ = "meeting_attendees"
    __table_args__ = (UniqueConstraint("meeting_id", "member_id", name="uq_meeting_attendee"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    meeting_id: Mapped[UUID] = mapped_column(ForeignKey("meetings.id"), nullable=False, index=True)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), nullable=False)
    invited_by: Mapped[str] = mapped_column(ForeignKey("members.id"), nullable=False)
    attendance_state: Mapped[str] = mapped_column(String(20), nullable=False, default="invited")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MeetingNoteRecord(Base):
    """One stable note identity per Meeting; content is only ever written as a version below."""

    __tablename__ = "meeting_notes"
    __table_args__ = (UniqueConstraint("meeting_id", name="uq_meeting_note"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    meeting_id: Mapped[UUID] = mapped_column(ForeignKey("meetings.id"), nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_by: Mapped[str | None] = mapped_column(ForeignKey("members.id"))


class MeetingNoteVersionRecord(Base):
    """Immutable human-authored note version, including the evidence refs it deliberately used."""

    __tablename__ = "meeting_note_versions"
    __table_args__ = (UniqueConstraint("note_id", "version", name="uq_meeting_note_version"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    note_id: Mapped[UUID] = mapped_column(ForeignKey("meeting_notes.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    source_evidence: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source_status: Mapped[str | None] = mapped_column(String(30))
    created_by: Mapped[str] = mapped_column(ForeignKey("members.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MeetingRecordingRecord(Base):
    """Audio object metadata. Bytes live behind RecordingStorage, never in this operational database."""

    __tablename__ = "meeting_recordings"
    __table_args__ = (Index("ix_meeting_recordings_meeting_created", "meeting_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    meeting_id: Mapped[UUID] = mapped_column(ForeignKey("meetings.id"), nullable=False)
    actor_id: Mapped[str] = mapped_column(ForeignKey("members.id"), nullable=False)
    purpose: Mapped[str] = mapped_column(String(300), nullable=False)
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="not_started")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    storage_key: Mapped[str | None] = mapped_column(String(500))
    original_name: Mapped[str | None] = mapped_column(String(300))
    content_type: Mapped[str | None] = mapped_column(String(200))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    provider_client_reference_id: Mapped[str] = mapped_column(String(200), nullable=False)
    provider_file_ref: Mapped[str | None] = mapped_column(String(300))
    provider_transcription_ref: Mapped[str | None] = mapped_column(String(300))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_detail: Mapped[str | None] = mapped_column(String(500))
    finalization_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    finalization_lease_token: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    finalization_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MeetingRawTranscriptRevisionRecord(Base):
    """An immutable STT result. Refinement and summary point at this rather than overwriting it."""

    __tablename__ = "meeting_raw_transcript_revisions"
    __table_args__ = (
        UniqueConstraint("recording_id", "revision", name="uq_meeting_raw_transcript_revision"),
        UniqueConstraint("recording_id", "provider_reference", name="uq_meeting_raw_transcript_provider_ref"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    recording_id: Mapped[UUID] = mapped_column(ForeignKey("meeting_recordings.id"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="processing")
    source_kind: Mapped[str] = mapped_column(String(40), nullable=False, default="async_final")
    provider: Mapped[str | None] = mapped_column(String(80))
    provider_reference: Mapped[str | None] = mapped_column(String(300))
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MeetingRawTranscriptSegmentRecord(Base):
    """Provider-produced final segment. Text and offsets are immutable once the raw revision completes."""

    __tablename__ = "meeting_raw_transcript_segments"
    __table_args__ = (
        UniqueConstraint("transcript_revision_id", "sequence", name="uq_meeting_raw_transcript_segment_sequence"),
        UniqueConstraint("transcript_revision_id", "source_segment_key", name="uq_meeting_raw_transcript_source_key"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    transcript_revision_id: Mapped[UUID] = mapped_column(ForeignKey("meeting_raw_transcript_revisions.id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    source_segment_key: Mapped[str] = mapped_column(String(200), nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    speaker_label: Mapped[str | None] = mapped_column(String(100))
    confirmed_member_id: Mapped[str | None] = mapped_column(ForeignKey("members.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MeetingSpeakerIdentityAssignmentRecord(Base):
    """Human-confirmed mapping of an anonymous STT track; it never rewrites provider raw text."""

    __tablename__ = "meeting_speaker_identity_assignments"
    __table_args__ = (
        Index("ix_meeting_speaker_assignment_transcript_label", "transcript_revision_id", "speaker_label"),
        Index("ix_meeting_speaker_assignment_meeting", "meeting_id", "confirmed_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    meeting_id: Mapped[UUID] = mapped_column(ForeignKey("meetings.id"), nullable=False)
    transcript_revision_id: Mapped[UUID] = mapped_column(ForeignKey("meeting_raw_transcript_revisions.id"), nullable=False)
    speaker_label: Mapped[str] = mapped_column(String(100), nullable=False)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), nullable=False)
    scope: Mapped[str] = mapped_column(String(30), nullable=False)  # segment_range | speaker_track
    raw_start_segment_id: Mapped[UUID] = mapped_column(ForeignKey("meeting_raw_transcript_segments.id"), nullable=False)
    raw_end_segment_id: Mapped[UUID] = mapped_column(ForeignKey("meeting_raw_transcript_segments.id"), nullable=False)
    source_audio_start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    source_audio_end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="human_confirmed")
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    confirmed_by: Mapped[str] = mapped_column(ForeignKey("members.id"), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[str | None] = mapped_column(ForeignKey("members.id"))


class MeetingTranscriptRefinementRevisionRecord(Base):
    """A derived, versioned reading layer over one raw STT revision; it can never mutate the raw source."""

    __tablename__ = "meeting_transcript_refinement_revisions"
    __table_args__ = (UniqueConstraint("raw_transcript_revision_id", "revision", name="uq_meeting_refinement_revision"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    raw_transcript_revision_id: Mapped[UUID] = mapped_column(ForeignKey("meeting_raw_transcript_revisions.id"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    provider_call_ref: Mapped[str | None] = mapped_column(String(300))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MeetingTranscriptRefinementSegmentRecord(Base):
    """Strict refinement output with a raw segment/time span and correction provenance per rendered turn."""

    __tablename__ = "meeting_transcript_refinement_segments"
    __table_args__ = (UniqueConstraint("refinement_revision_id", "sequence", name="uq_meeting_refinement_segment_sequence"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    refinement_revision_id: Mapped[UUID] = mapped_column(ForeignKey("meeting_transcript_refinement_revisions.id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_start_segment_id: Mapped[UUID] = mapped_column(ForeignKey("meeting_raw_transcript_segments.id"), nullable=False)
    raw_end_segment_id: Mapped[UUID] = mapped_column(ForeignKey("meeting_raw_transcript_segments.id"), nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    speaker_label: Mapped[str | None] = mapped_column(String(100))
    confirmed_member_id: Mapped[str | None] = mapped_column(ForeignKey("members.id"))
    correction_kind: Mapped[str] = mapped_column(String(40), nullable=False, default="none")
    confidence: Mapped[float | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MeetingSummarySuggestionRecord(Base):
    """Derived Meeting reading aid. Adoption is a separate append to MeetingNoteVersion."""

    __tablename__ = "meeting_summary_suggestions"
    __table_args__ = (
        UniqueConstraint("refinement_revision_id", "kind", name="uq_meeting_summary_refinement_kind"),
        Index("ix_meeting_summary_meeting_created", "meeting_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    meeting_id: Mapped[UUID] = mapped_column(ForeignKey("meetings.id"), nullable=False)
    raw_transcript_revision_id: Mapped[UUID] = mapped_column(ForeignKey("meeting_raw_transcript_revisions.id"), nullable=False)
    refinement_revision_id: Mapped[UUID] = mapped_column(ForeignKey("meeting_transcript_refinement_revisions.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)  # provisional | final
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    body: Mapped[str | None] = mapped_column(Text)
    provider_call_ref: Mapped[str | None] = mapped_column(String(300))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_detail: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    adopted_note_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("meeting_note_versions.id"))
    adopted_by: Mapped[str | None] = mapped_column(ForeignKey("members.id"))
    adopted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MeetingSummaryEvidenceRecord(Base):
    """Every generated statement carries a refinement span and its raw span for citation navigation."""

    __tablename__ = "meeting_summary_evidence"
    __table_args__ = (UniqueConstraint("summary_id", "statement_index", name="uq_meeting_summary_statement"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    summary_id: Mapped[UUID] = mapped_column(ForeignKey("meeting_summary_suggestions.id"), nullable=False)
    statement_index: Mapped[int] = mapped_column(Integer, nullable=False)
    statement_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    statement_text: Mapped[str] = mapped_column(Text, nullable=False)
    refinement_start_segment_id: Mapped[UUID] = mapped_column(ForeignKey("meeting_transcript_refinement_segments.id"), nullable=False)
    refinement_end_segment_id: Mapped[UUID] = mapped_column(ForeignKey("meeting_transcript_refinement_segments.id"), nullable=False)
    raw_start_segment_id: Mapped[UUID] = mapped_column(ForeignKey("meeting_raw_transcript_segments.id"), nullable=False)
    raw_end_segment_id: Mapped[UUID] = mapped_column(ForeignKey("meeting_raw_transcript_segments.id"), nullable=False)
    raw_start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MeetingFollowupPromotionRecord(Base):
    """A followup someone decided to act on, and the work it became.

    A summary statement is a candidate, not work. This row exists only after a person promoted it, so the same
    candidate is never turned into two Tasks and the meeting can say which of its candidates were acted on.
    """

    __tablename__ = "meeting_followup_promotions"
    __table_args__ = (UniqueConstraint("summary_id", "statement_index", name="uq_meeting_followup_promotion"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    meeting_id: Mapped[UUID] = mapped_column(ForeignKey("meetings.id"), nullable=False, index=True)
    summary_id: Mapped[UUID] = mapped_column(ForeignKey("meeting_summary_suggestions.id"), nullable=False)
    statement_index: Mapped[int] = mapped_column(Integer, nullable=False)
    task_id: Mapped[UUID | None] = mapped_column(ForeignKey("tasks.id"))
    work_request_id: Mapped[UUID | None] = mapped_column(ForeignKey("work_requests.id"))
    promoted_by: Mapped[str] = mapped_column(String(100), nullable=False)
    promoted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkflowDefinitionRecord(Base):
    __tablename__ = "workflow_definitions"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(100), nullable=False, default="scax")
    scope: Mapped[str] = mapped_column(String(100), nullable=False, default="scax")


class WorkflowDefinitionVersionRecord(Base):
    __tablename__ = "workflow_definition_versions"
    __table_args__ = (UniqueConstraint("workflow_id", "version", name="uq_workflow_definition_version"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow_definitions.id"), nullable=False)
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    definition: Mapped[dict] = mapped_column(JSON, nullable=False)
    schema_version: Mapped[int] = mapped_column(nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="published")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowRunRecord(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    definition_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflow_definition_versions.id"), nullable=False
    )
    workflow_id: Mapped[str] = mapped_column(String(100), nullable=False)
    initiator_id: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    input_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkflowNodeExecutionRecord(Base):
    __tablename__ = "workflow_node_executions"
    __table_args__ = (UniqueConstraint("run_id", "node_id", name="uq_workflow_run_node"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False)
    node_id: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    input_snapshot: Mapped[dict | None] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON)
    normalized_error: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProviderCallRecord(Base):
    __tablename__ = "provider_calls"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    node_execution_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_node_executions.id"), nullable=False)
    provider_run_ref: Mapped[str | None] = mapped_column(String(200))
    provider_session_ref: Mapped[str | None] = mapped_column(String(200))
    requested_model: Mapped[str | None] = mapped_column(String(120))
    observed_model: Mapped[str | None] = mapped_column(String(120))
    requested_tier: Mapped[str | None] = mapped_column(String(80))
    observed_tier: Mapped[str | None] = mapped_column(String(80))
    latency_ms: Mapped[int | None] = mapped_column(nullable=True)
    usage: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    normalized_error: Mapped[str | None] = mapped_column(Text)


class ConversationRecord(Base):
    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConversationTurnRecord(Base):
    __tablename__ = "conversation_turns"
    __table_args__ = (
        Index(
            "uq_conversation_active_turn",
            "conversation_id",
            unique=True,
            postgresql_where=text("state IN ('pending', 'running')"),
            sqlite_where=text("state IN ('pending', 'running')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    execution_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, default=uuid4, unique=True
    )
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_run_ref: Mapped[str | None] = mapped_column(String(200))
    provider_session_ref: Mapped[str | None] = mapped_column(String(200))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    normalized_error: Mapped[str | None] = mapped_column(Text)
    # Provider attempts are domain execution metadata. The durable job's transport
    # attempt_count also counts harmless redeliveries that lose the live-worker guard.
    execution_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # User-facing lifecycle projection (queued/preparing/tool_running/composing/retrying/completed/failed/cancelled),
    # persisted so re-entry hydrates the same state the live viewer saw. Only observed provider events feed it.
    progress_state: Mapped[str] = mapped_column(String(30), nullable=False, default="queued")
    current_tool_display_name: Mapped[str | None] = mapped_column(String(300))
    execution_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    usage: Mapped[dict | None] = mapped_column(JSON)
    # Retry lineage: a retry is a new Turn that references the terminal one; the key makes one-click retry idempotent.
    retry_of_turn_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    retry_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    # Server-normalized snapshot produced with the answer. Candidate ids stay stable across reads and reconnects.
    follow_up_candidates: Mapped[list[dict[str, str]] | None] = mapped_column(JSON)


class ConversationProviderSessionReferenceRecord(Base):
    __tablename__ = "conversation_provider_session_references"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    provider_session_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConversationAuditEventRecord(Base):
    __tablename__ = "conversation_audit_events"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    turn_id: Mapped[UUID | None] = mapped_column(ForeignKey("conversation_turns.id"))
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConversationMessageRecord(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uq_conversation_message_sequence"),
        UniqueConstraint("conversation_id", "idempotency_key", name="uq_conversation_message_idempotency"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    turn_id: Mapped[UUID | None] = mapped_column(ForeignKey("conversation_turns.id"))
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    # A candidate is a one-shot entry into the ordinary message path. Nullable unique makes that choice exactly once.
    follow_up_candidate_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), unique=True)
    # user rows are always final; assistant rows move streaming -> final | failed | cancelled and keep partial text.
    body_state: Mapped[str] = mapped_column(String(20), nullable=False, default="final")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ContextReferenceRecord(Base):
    __tablename__ = "conversation_context_references"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    turn_id: Mapped[UUID | None] = mapped_column(ForeignKey("conversation_turns.id"))
    message_id: Mapped[UUID] = mapped_column(ForeignKey("conversation_messages.id"), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(200), nullable=False)
    resource_version: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    included: Mapped[bool] = mapped_column(nullable=False)


class ToolInvocationRecord(Base):
    __tablename__ = "tool_invocations"
    __table_args__ = (UniqueConstraint("turn_id", "sequence", name="uq_tool_invocation_sequence"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    turn_id: Mapped[UUID] = mapped_column(ForeignKey("conversation_turns.id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_call_id: Mapped[str | None] = mapped_column(String(200))
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(String(300), nullable=False)
    input_summary: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    result_summary: Mapped[str | None] = mapped_column(Text)
    error_summary: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    target_resource_id: Mapped[str | None] = mapped_column(String(200))
    target_resource_version: Mapped[str | None] = mapped_column(String(100))
    audit_ref: Mapped[str | None] = mapped_column(String(200))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ActionItemRecord(Base):
    """A human confirmation owned by AX, not by the Workflow runtime."""

    __tablename__ = "action_items"
    __table_args__ = (
        UniqueConstraint("execution_id", "action_type", name="uq_action_execution_type"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[str] = mapped_column(String(100), nullable=False)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    turn_id: Mapped[UUID] = mapped_column(ForeignKey("conversation_turns.id"), nullable=False)
    execution_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversation_turns.execution_id"),
        nullable=False,
    )
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    result: Mapped[dict | None] = mapped_column(JSON)
    audit_ref: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ActionMaterialDraftRecord(Base):
    """An expiring pre-create material owned by one principal and one AX ActionItem.

    It is not a domain Attachment yet. Confirmation claims its metadata into an AttachmentBinding in the same
    transaction that creates the Task or Meeting; file bytes may exist earlier and are reclaimed independently.
    """

    __tablename__ = "action_material_drafts"
    __table_args__ = (Index("ix_action_material_drafts_action", "action_id", "state"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    action_id: Mapped[UUID] = mapped_column(ForeignKey("action_items.id"), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(100), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    content_type: Mapped[str] = mapped_column(String(200), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    integrity_ref: Mapped[str] = mapped_column(String(80), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="staged")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_task_id: Mapped[UUID | None] = mapped_column(ForeignKey("tasks.id"))
    claimed_meeting_id: Mapped[UUID | None] = mapped_column(ForeignKey("meetings.id"))


class ActionItemAuditEventRecord(Base):
    __tablename__ = "action_item_audit_events"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    action_id: Mapped[UUID] = mapped_column(ForeignKey("action_items.id"), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(100), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProjectRecord(Base):
    """부서를 가로질러 묶이는 일 하나.

    소유 조직 단위를 두지 않는다. 프로젝트는 부서를 가로지르려고 있는 것이라 — 마케팅 한 건에 국내사업부 AE와
    비주얼디자인팀 디자이너가 함께 붙는다 — 어느 한 부서의 것이라고 적는 순간 그 부서가 열쇠가 된다. 누가
    참여하는지는 `project_assignments`가 말하고, 그것이 이 프로젝트가 열리는 유일한 길이다.

    기간은 비어 있을 수 있다. 시작만 정해지고 끝은 아직 없는 일이 흔하다.
    """

    __tablename__ = "projects"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    #: 사람이 정한 읽을 수 있는 key. dataset이 같은 프로젝트를 다시 가리킬 때 쓴다.
    external_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    created_by_actor_id: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))


class ProjectAssignmentRecord(Base):
    """구성원 ↔ 프로젝트. 직급·직무·보직과 같은 모양이며, 조직 단위를 묻지 않는다.

    유효기간은 있을 수도 없을 수도 있다. 원문이 말하지 않으면 비워 두고, 비어 있는 것을 `지금부터 계속`으로 읽는다.
    """

    __tablename__ = "project_assignments"
    __table_args__ = (
        Index(
            "uq_project_assignment_active",
            "project_id",
            "member_id",
            unique=True,
            sqlite_where=text("ended_at IS NULL"),
            postgresql_where=text("ended_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), nullable=False, index=True)
    #: `lead`는 이 프로젝트를 책임지는 사람, `member`는 함께 하는 사람.
    assignment_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="member")
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_by_member_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    #: 실제 제외는 계획된 유효기간과 다른 사실이다. 사유는 입력되지 않았으면 비워 둔다.
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_by_member_id: Mapped[str | None] = mapped_column(String(100))
    end_reason: Mapped[str | None] = mapped_column(Text)


class TaskRecord(Base):
    __tablename__ = "tasks"
    # One accepted request produces exactly one Task. The uniqueness lives with the FK that carries the link.
    __table_args__ = (
        Index(
            "uq_tasks_source_work_request",
            "source_work_request_id",
            unique=True,
            sqlite_where=text("source_work_request_id IS NOT NULL"),
            postgresql_where=text("source_work_request_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    #: The one actor relationship a Task owns: who created it. Who sent the work is on the WorkRequest, and who holds
    #: it now is on the active TaskAssignment; neither is copied here.
    created_by_actor_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    block_reason: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    # ERD TASK attribution and lineage
    organization_unit_id: Mapped[str | None] = mapped_column(ForeignKey("organization_units.id"))
    origin_kind: Mapped[str] = mapped_column(String(30), nullable=False, default="direct")
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="scope_default")
    request_thread_id: Mapped[UUID | None] = mapped_column(ForeignKey("request_threads.id"))
    source_work_request_id: Mapped[UUID | None] = mapped_column(ForeignKey("work_requests.id"))
    source_decision_item_id: Mapped[UUID | None] = mapped_column(ForeignKey("decision_items.id"))
    source_submission_id: Mapped[UUID | None] = mapped_column(ForeignKey("submissions.id"))
    source_review_decision_id: Mapped[UUID | None] = mapped_column(ForeignKey("review_decisions.id"))
    source_action_item_id: Mapped[UUID | None] = mapped_column(ForeignKey("action_items.id"))
    source_task_id: Mapped[UUID | None] = mapped_column(ForeignKey("tasks.id"))
    #: The work this one is a part of. One level only for now: a child never becomes a parent, and the parent is
    #: context and a place to see progress — never the truth about this Task's own state.
    parent_task_id: Mapped[UUID | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    #: 어느 프로젝트의 일인가. 비어 있는 것이 정상이다 — 프로젝트 없이 하는 일이 조직에는 더 많다.
    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("projects.id"), index=True)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    causation_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    assignments: Mapped[list["TaskAssignmentRecord"]] = relationship(
        "TaskAssignmentRecord", order_by="TaskAssignmentRecord.created_at", lazy="selectin"
    )


class TaskChecklistItemRecord(Base):
    """A step inside one Task. Not a Task: no assignment, no lineage, no judgement — it lives and dies with its Task."""

    __tablename__ = "task_checklist_items"
    __table_args__ = (Index("ix_task_checklist_items_task_position", "task_id", "position"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    text: Mapped[str] = mapped_column(String(300), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: `active` or `archived`. A step someone no longer needs leaves the list without leaving the record.
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    #: Its own optimistic-concurrency counter, so two people editing two steps never conflict over one Task.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    completed_by: Mapped[str | None] = mapped_column(String(100))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_by: Mapped[str | None] = mapped_column(String(100))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RequestThreadRecord(Base):
    """ERD REQUEST_THREAD — the continuity of exactly one WorkRequest: comments, submissions, decisions, and the derived Task."""

    __tablename__ = "request_threads"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    initiated_by: Mapped[str] = mapped_column(String(100), nullable=False)
    organization_context_id: Mapped[str | None] = mapped_column(String(100))
    purpose: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SubjectRecord(Base):
    """ERD SUBJECT — the stable identity of what is being judged; versions freeze its content."""

    __tablename__ = "subjects"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    subject_type: Mapped[str] = mapped_column(String(30), nullable=False)
    owning_resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    owning_resource_id: Mapped[str] = mapped_column(String(100), nullable=False)
    workflow_run_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SubjectVersionRecord(Base):
    __tablename__ = "subject_versions"
    __table_args__ = (UniqueConstraint("subject_id", "version"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DecisionItemRecord(Base):
    """ERD ACTION_ITEM (human decision item). Named DecisionItem here because `action_items` already holds AX chat proposals."""

    __tablename__ = "decision_items"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    subject_id: Mapped[UUID] = mapped_column(ForeignKey("subjects.id"), nullable=False)
    context_type: Mapped[str | None] = mapped_column(String(40))
    context_id: Mapped[str | None] = mapped_column(String(100))
    supersedes_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    effect_identity: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="open")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SubmissionRecord(Base):
    """ERD SUBMISSION — one proposal round against a fixed SubjectVersion; revisions are new rows."""

    __tablename__ = "submissions"
    __table_args__ = (UniqueConstraint("decision_item_id", "submission_version"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    decision_item_id: Mapped[UUID] = mapped_column(ForeignKey("decision_items.id"), nullable=False)
    subject_version_id: Mapped[UUID] = mapped_column(ForeignKey("subject_versions.id"), nullable=False)
    submission_version: Mapped[int] = mapped_column(Integer, nullable=False)
    revises_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    submitted_by: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_policy_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    diff: Mapped[dict | None] = mapped_column(JSON)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewAssignmentRecord(Base):
    """ERD REVIEW_ASSIGNMENT — who currently owes an answer to a Submission; history 1..N, active at most 1."""

    __tablename__ = "review_assignments"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    submission_id: Mapped[UUID] = mapped_column(ForeignKey("submissions.id"), nullable=False)
    reviewer_member_id: Mapped[str] = mapped_column(String(100), nullable=False)
    supersedes_assignment_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    resolution_kind: Mapped[str | None] = mapped_column(String(20))
    resolution_ref: Mapped[str | None] = mapped_column(String(100))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewDecisionRecord(Base):
    """ERD REVIEW_DECISION — the immutable answer bound to one assignment, submission, and actor."""

    __tablename__ = "review_decisions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    review_assignment_id: Mapped[UUID] = mapped_column(ForeignKey("review_assignments.id"), nullable=False)
    submission_id: Mapped[UUID] = mapped_column(ForeignKey("submissions.id"), nullable=False)
    actor_member_id: Mapped[str] = mapped_column(String(100), nullable=False)
    decision: Mapped[str] = mapped_column(String(30), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    conditions: Mapped[dict | None] = mapped_column(JSON)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ActivityEventRecord(Base):
    """ERD ACTIVITY_EVENT — append-only official facts with a safe summary; never a copy of sensitive payloads."""

    __tablename__ = "activity_events"
    __table_args__ = (Index("ix_activity_events_target", "target_type", "target_id"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    request_thread_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    target_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_id: Mapped[str] = mapped_column(String(100), nullable=False)
    event_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(30), nullable=False, default="member")
    actor_id: Mapped[str] = mapped_column(String(100), nullable=False)
    before_ref: Mapped[str | None] = mapped_column(String(200))
    after_ref: Mapped[str | None] = mapped_column(String(200))
    reason: Mapped[str | None] = mapped_column(Text)
    #: What carried this change here, as `<kind>:<id>` — an approved AX confirmation, say. The actor stays the
    #: person who approved it; this only says which decision it travelled through.
    causation_ref: Mapped[str | None] = mapped_column(String(200))
    safe_summary: Mapped[str] = mapped_column(String(300), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NotificationRecord(Base):
    """A recipient projection of one canonical domain event; content is re-authorized before every read."""

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("recipient_member_id", "source_kind", "source_id", name="uq_notification_recipient_source"),
        Index("ix_notifications_recipient_created", "recipient_member_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    recipient_member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(60), nullable=False)
    source_id: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_version: Mapped[int | None] = mapped_column(Integer)
    resource_title: Mapped[str] = mapped_column(String(300), nullable=False)
    actor_member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), nullable=False)
    safe_summary: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CommentRecord(Base):
    """ERD COMMENT — discussion on a RequestThread; never a state transition."""

    __tablename__ = "comments"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    request_thread_id: Mapped[UUID] = mapped_column(ForeignKey("request_threads.id"), nullable=False, index=True)
    author_member_id: Mapped[str] = mapped_column(String(100), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AttachmentRecord(Base):
    """ERD ATTACHMENT — the artifact itself (file behind MaterialStorage, or a link); bindings say where it is used."""

    __tablename__ = "attachments"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    source_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    content_type: Mapped[str] = mapped_column(String(200), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    provenance: Mapped[str] = mapped_column(String(300), nullable=False)
    integrity_ref: Mapped[str] = mapped_column(String(80), nullable=False)
    uploaded_by: Mapped[str] = mapped_column(String(100), nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(20), nullable=False, default="available")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AttachmentBindingRecord(Base):
    """ERD ATTACHMENT_BINDING — exactly one context (comment·task·submission) and a role per binding."""

    __tablename__ = "attachment_bindings"
    __table_args__ = (Index("ix_attachment_bindings_context", "context_type", "context_id"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    attachment_id: Mapped[UUID] = mapped_column(ForeignKey("attachments.id"), nullable=False)
    context_type: Mapped[str] = mapped_column(String(20), nullable=False)
    context_id: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    bound_by: Mapped[str] = mapped_column(String(100), nullable=False)
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    unbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvidenceRecord(Base):
    """ERD EVIDENCE — an attachment explicitly adopted as decision basis for one Submission."""

    __tablename__ = "evidence"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    submission_id: Mapped[UUID] = mapped_column(ForeignKey("submissions.id"), nullable=False, index=True)
    attachment_id: Mapped[UUID | None] = mapped_column(ForeignKey("attachments.id"))
    evidence_role: Mapped[str] = mapped_column(String(20), nullable=False)
    fixed_snapshot_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    mutable_source: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    adopted_by: Mapped[str] = mapped_column(String(100), nullable=False)
    adopted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MaterialFolderRecord(Base):
    """Explicit owner for independent files; no ownership is inferred from Attachment.uploaded_by."""

    __tablename__ = "material_folders"
    __table_args__ = (
        CheckConstraint("(kind = 'personal' AND owner_member_id IS NOT NULL AND organization_id IS NULL) OR "
                        "(kind = 'team' AND owner_member_id IS NULL AND organization_id IS NOT NULL)", name="ck_material_folder_owner"),
    )
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    owner_member_id: Mapped[str | None] = mapped_column(ForeignKey("members.id"), index=True)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organization_units.id"), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("members.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MaterialExtractionRecord(Base):
    """Derived projection of one Attachment version: extraction lifecycle (queued/running/completed/failed/unsupported)."""

    __tablename__ = "material_extractions"
    #: One extraction per (file version, parser version): a newer parser adds a row, it does not rewrite the old one.
    __table_args__ = (
        UniqueConstraint("attachment_id", "integrity_ref", "parser_version", name="uq_material_extraction_version"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    attachment_id: Mapped[UUID] = mapped_column(ForeignKey("attachments.id"), nullable=False, index=True)
    integrity_ref: Mapped[str] = mapped_column(String(80), nullable=False)
    extractor: Mapped[str | None] = mapped_column(String(20))
    parser_version: Mapped[str] = mapped_column(String(40), nullable=False, default="1")
    #: Set when a newer parser version replaced this reading. The row and its blocks stay, so what an older answer
    #: stood on can still be pointed at.
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    failure_reason: Mapped[str | None] = mapped_column(String(40))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    page_count: Mapped[int | None] = mapped_column(Integer)
    warnings: Mapped[list | None] = mapped_column(JSON)
    coverage: Mapped[dict | None] = mapped_column(JSON)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MaterialBlockRecord(Base):
    """The document as it is actually shaped: one row per paragraph, table, sheet row, slide or page.

    Chunks for search are derived from these, so re-indexing never needs the original bytes again, and a citation can
    point at a part of the document a person would recognise. Blocks belong to one extraction of one file version.
    """

    __tablename__ = "material_blocks"
    __table_args__ = (UniqueConstraint("extraction_id", "sequence", name="uq_material_block_sequence"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    extraction_id: Mapped[UUID] = mapped_column(ForeignKey("material_extractions.id"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    #: paragraph | table | header | footer | sheet_row | slide_text | slide_table | notes | page
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    #: Where in the document this came from, in the words a reader would use ("표 2", "3쪽", "매출 시트 12행").
    locator_label: Mapped[str | None] = mapped_column(String(120))
    page: Mapped[int | None] = mapped_column(Integer)
    sheet: Mapped[str | None] = mapped_column(String(120))
    slide: Mapped[int | None] = mapped_column(Integer)
    row: Mapped[int | None] = mapped_column(Integer)

    source_locator: Mapped[dict | None] = mapped_column(JSON)
    header_context: Mapped[dict | None] = mapped_column(JSON)


class MaterialChunkRecord(Base):
    """Bounded searchable span of extracted text; identity = (extraction, sequence)."""

    __tablename__ = "material_chunks"
    __table_args__ = (
        UniqueConstraint("extraction_id", "sequence", name="uq_material_chunk_sequence"),
        # 찾는 일을 데이터베이스가 색인으로 한다. PostgreSQL에서만 만들어지며, 없는 곳에서는 같은 열을 훑는다.
        Index(
            "ix_material_chunks_search",
            text("to_tsvector('simple', coalesce(search_text, ''))"),
            postgresql_using="gin",
        ).ddl_if(dialect="postgresql"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    extraction_id: Mapped[UUID] = mapped_column(ForeignKey("material_extractions.id"), nullable=False, index=True)
    #: The block this span was derived from, so a hit can name the part of the document it came from.
    block_id: Mapped[UUID | None] = mapped_column(ForeignKey("material_blocks.id"))
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    #: 찾기 위한 형태 — 본문을 문서와 질문에 같은 규칙으로 잘라 이어 붙인 낱말들. 원문은 위의 `text`가 갖고
    #: 여기에는 사람이 읽을 것이 없다. 데이터베이스가 이 열을 색인한다.
    search_text: Mapped[str | None] = mapped_column(Text)
    #: 표의 첫 non-empty 행에서 얻은 bounded context. 원문 청크와 분리해 재색인에도 보존한다.
    context_text: Mapped[str | None] = mapped_column(Text)
    #: 어떤 분석 규칙으로 만들었는지. 규칙이 바뀌면 이 값이 달라지고 그 색인은 다시 만들어야 한다.
    analyzer_version: Mapped[str | None] = mapped_column(String(40))


class ConversationGraphReceiptRecord(Base):
    """Nodes and connections a delegated AX turn actually looked at, in the order it looked at them.

    Only what a graph tool really returned is written here — never a guess about how things relate, and never a name
    the asking persona could not already read. It is a receipt of a walk, not a stored graph.
    """

    __tablename__ = "conversation_graph_receipts"
    __table_args__ = (UniqueConstraint("turn_id", "sequence", name="uq_conversation_graph_receipt"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    turn_id: Mapped[UUID] = mapped_column(ForeignKey("conversation_turns.id"), nullable=False, index=True)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"), nullable=False, index=True)
    execution_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    #: `node` for something found, `edge` for a connection walked.
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    node_ref: Mapped[str | None] = mapped_column(String(120))
    node_title: Mapped[str | None] = mapped_column(String(300))
    edge_kind: Mapped[str | None] = mapped_column(String(40))
    from_ref: Mapped[str | None] = mapped_column(String(120))
    from_title: Mapped[str | None] = mapped_column(String(300))
    to_ref: Mapped[str | None] = mapped_column(String(120))
    to_title: Mapped[str | None] = mapped_column(String(300))
    source_contexts: Mapped[list | None] = mapped_column(JSON)
    integrity_ref: Mapped[str | None] = mapped_column(String(80))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConversationAnswerResourceRecord(Base):
    """Which canonical things a delegated turn actually read and named, in the order it named them.

    An answer that lists work is a list of resources, not a paragraph a client should parse: each row keeps the id and
    the version a tool really returned, so every item opens its own detail. Nothing here is shown without asking the
    owning module again at read time — a person who has since lost access sees no title and no count.
    """

    __tablename__ = "conversation_answer_resources"
    __table_args__ = (
        UniqueConstraint("turn_id", "resource_type", "resource_id", name="uq_conversation_answer_resource"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    turn_id: Mapped[UUID] = mapped_column(ForeignKey("conversation_turns.id"), nullable=False, index=True)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"), nullable=False, index=True)
    execution_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    #: `task` | `meeting` | `work_request` | `material` | `report` | `conversation_turn`
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(120), nullable=False)
    #: The version the tool saw, when that resource has one. It says what the answer stood on, not what is true now.
    resource_version: Mapped[int | None] = mapped_column(Integer)
    #: Material metadata actually observed by this read; new bindings cannot restore a revoked observation.
    source_contexts: Mapped[list | None] = mapped_column(JSON)
    integrity_ref: Mapped[str | None] = mapped_column(String(80))
    #: 원문의 어디였는지 — 쪽, 절, 구간처럼 그 자료가 스스로 부르는 자리다. 도구가 말해 준 만큼만 담고,
    #: 원문이나 발췌는 여기 복제하지 않는다. 없을 수 있으며 없는 것이 정상이다.
    source_locator: Mapped[dict | None] = mapped_column(JSON)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConversationContentEvidenceRecord(Base):
    """Observed canonical artifacts and owner contexts."""

    __tablename__ = "conversation_content_evidence"
    __table_args__ = (UniqueConstraint("turn_id", "chunk_id", name="uq_conversation_content_evidence"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    turn_id: Mapped[UUID] = mapped_column(ForeignKey("conversation_turns.id"), nullable=False, index=True)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"), nullable=False, index=True)
    execution_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    attachment_id: Mapped[UUID] = mapped_column(ForeignKey("attachments.id"), nullable=False)
    # A receipt survives removal of the projection and its chunks.
    chunk_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_contexts: Mapped[list] = mapped_column(JSON, nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    integrity_ref: Mapped[str] = mapped_column(String(80), nullable=False)
    page: Mapped[int | None] = mapped_column(Integer)
    source_locator: Mapped[dict | None] = mapped_column(JSON)
    header_context: Mapped[dict | None] = mapped_column(JSON)
    extraction_snapshot: Mapped[dict | None] = mapped_column(JSON)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    query: Mapped[str] = mapped_column(String(300), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TaskReferenceRecord(Base):
    """One Task pointing at another as context — `참고 업무`.

    It says only that someone found the other work worth looking at. There is no kind of relation to choose, and
    pointing never grants access: every read re-checks whether this person may open the work being pointed at.
    Letting go of a pointer closes the row rather than deleting it, so history still shows it was there.
    """

    __tablename__ = "task_references"
    __table_args__ = (
        Index(
            "uq_task_reference_active",
            "task_id",
            "referenced_task_id",
            unique=True,
            sqlite_where=text("released_at IS NULL"),
            postgresql_where=text("released_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    #: An explicit foreign key, so the database itself answers whether the referenced work still exists.
    referenced_task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_by: Mapped[str | None] = mapped_column(String(100))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkRequestReferenceRecord(Base):
    """Earlier work a requester pointed at. On acceptance the same pointers become the new Task's references."""

    __tablename__ = "work_request_references"
    __table_args__ = (UniqueConstraint("work_request_id", "referenced_task_id", name="uq_work_request_reference"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    work_request_id: Mapped[UUID] = mapped_column(ForeignKey("work_requests.id"), nullable=False, index=True)
    referenced_task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TaskVersionRecord(Base):
    """An immutable picture of one Task at one version, frozen in the transaction that made that version.

    It refers to artifacts rather than copying them: a material line carries the Attachment identity and its integrity
    hash, never bytes or extracted text. So history stays cheap and an artifact keeps exactly one identity.
    """

    __tablename__ = "task_versions"
    __table_args__ = (UniqueConstraint("task_id", "version", name="uq_task_version"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    #: What made this version, in the same words the activity ledger uses.
    change_kind: Mapped[str] = mapped_column(String(60), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TaskActivityRecord(Base):
    __tablename__ = "task_activities"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    task_version: Mapped[int] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkRequestRecord(Base):
    """ERD WORK_REQUEST — request-first ledger; its RequestThread and Subject are created with it."""

    __tablename__ = "work_requests"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    request_thread_id: Mapped[UUID | None] = mapped_column(ForeignKey("request_threads.id"))
    subject_id: Mapped[UUID | None] = mapped_column(ForeignKey("subjects.id"))
    organization_context_id: Mapped[str | None] = mapped_column(String(100))
    requester_id: Mapped[str] = mapped_column(String(100), nullable=False)
    assignee_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    due_date: Mapped[date | None] = mapped_column(Date)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    conditions: Mapped[dict | None] = mapped_column(JSON)
    #: The steps the requester already knew about, in order. Creation content, not something a round negotiates:
    #: they become the accepted Task's checklist, written by the person who asked.
    initial_checklist: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    causation_key: Mapped[str | None] = mapped_column(String(64), unique=True)


class WorkRequestAuditEventRecord(Base):
    __tablename__ = "work_request_audit_events"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    request_id: Mapped[UUID] = mapped_column(ForeignKey("work_requests.id"), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(100), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TaskAssignmentRecord(Base):
    """ERD TASK_ASSIGNMENT — who assigned whom to a Task, and whether the assignee has accepted that responsibility.

    assignment_kind: self (owner created it), request_effect (WorkRequest accepted), direct (manager assigned; pending until accepted).
    """

    __tablename__ = "task_assignments"
    __table_args__ = (Index("ix_task_assignments_assignee_status", "assignee_id", "status"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    assignee_id: Mapped[str] = mapped_column(String(100), nullable=False)
    #: Who put this person on the work. Empty when nobody did — a self assignment has no assigner.
    assigned_by: Mapped[str | None] = mapped_column(String(100))
    assignment_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    source_work_request_id: Mapped[UUID | None] = mapped_column(ForeignKey("work_requests.id"))
    source_decision_item_id: Mapped[UUID | None] = mapped_column(ForeignKey("decision_items.id"))
    source_review_decision_id: Mapped[UUID | None] = mapped_column(ForeignKey("review_decisions.id"))
    #: The assignment this one replaced, so a change of holder reads as an append-only chain.
    supersedes_assignment_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    decline_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    declined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DailyReportSubmissionRecord(Base):
    __tablename__ = "daily_report_submissions"
    __table_args__ = (UniqueConstraint("report_id", "submission_version", name="uq_daily_report_submission_version"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    report_id: Mapped[UUID] = mapped_column(ForeignKey("daily_reports.id"), nullable=False)
    submission_version: Mapped[int] = mapped_column(nullable=False)
    submitter_id: Mapped[str] = mapped_column(String(100), nullable=False)
    report_date: Mapped[str] = mapped_column(String(10), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    source_refs: Mapped[list] = mapped_column(JSON, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DailyReportRecord(Base):
    __tablename__ = "daily_reports"
    __table_args__ = (UniqueConstraint("owner_id", "report_date", name="uq_daily_report_owner_date"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[str] = mapped_column(String(100), nullable=False)
    report_date: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)


class ReportDraftRecord(Base):
    __tablename__ = "report_drafts"
    __table_args__ = (
        UniqueConstraint("owner_id", "causation_key", name="uq_report_draft_owner_causation"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[str] = mapped_column(String(100), nullable=False)
    report_date: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    source_refs: Mapped[list] = mapped_column(JSON, nullable=False)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    report_id: Mapped[UUID] = mapped_column(ForeignKey("daily_reports.id"), nullable=False)
    workflow_run_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False)
    definition_version_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_definition_versions.id"), nullable=False)
    causation_key: Mapped[str | None] = mapped_column(String(128))


class ReportAuditEventRecord(Base):
    __tablename__ = "report_audit_events"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    report_id: Mapped[UUID] = mapped_column(ForeignKey("daily_reports.id"), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(100), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DurableJobRecord(Base):
    """Extension-free PostgreSQL job transport row (see modules.jobs.domain). Domain state lives elsewhere."""

    __tablename__ = "durable_jobs"
    __table_args__ = (
        Index("ix_durable_jobs_kind_state", "kind", "state"),
        Index("ix_durable_jobs_ordering", "kind", "ordering_key", "sequence"),
        Index(
            "uq_durable_jobs_active_idempotency",
            "kind",
            "idempotency_key",
            unique=True,
            postgresql_where=text("state IN ('queued', 'running')"),
            sqlite_where=text("state IN ('queued', 'running')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    sequence: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), Identity(), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    ordering_key: Mapped[str] = mapped_column(String(200), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_token: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    leased_by: Mapped[str | None] = mapped_column(String(200))
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSessionRecord(Base):
    """Server-side login session; the cookie only carries this opaque id."""

    __tablename__ = "auth_sessions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    member_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def make_session_factory(database_url: str):
    return sessionmaker(bind=create_engine(database_url, pool_pre_ping=True), expire_on_commit=False)
