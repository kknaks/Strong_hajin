from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Identity, Index, Integer, JSON, String, Text, Uuid, UniqueConstraint, create_engine, text
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
    account_ref: Mapped[str | None] = mapped_column(String(200))
    record_status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))


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
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="private")
    lifecycle: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled")
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
    created_by: Mapped[str] = mapped_column(ForeignKey("members.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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


class ActionItemAuditEventRecord(Base):
    __tablename__ = "action_item_audit_events"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    action_id: Mapped[UUID] = mapped_column(ForeignKey("action_items.id"), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(100), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TaskRecord(Base):
    __tablename__ = "tasks"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[str] = mapped_column(String(100), nullable=False)
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
    request_thread_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    source_work_request_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    source_decision_item_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    source_submission_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    source_review_decision_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    source_action_item_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    source_task_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    causation_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    assignments: Mapped[list["TaskAssignmentRecord"]] = relationship(
        "TaskAssignmentRecord", order_by="TaskAssignmentRecord.created_at", lazy="selectin"
    )


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
    safe_summary: Mapped[str] = mapped_column(String(300), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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


class MaterialExtractionRecord(Base):
    """Derived projection of one Attachment version: extraction lifecycle (queued/running/completed/failed/unsupported)."""

    __tablename__ = "material_extractions"
    __table_args__ = (UniqueConstraint("attachment_id", "integrity_ref", name="uq_material_extraction_version"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    attachment_id: Mapped[UUID] = mapped_column(ForeignKey("attachments.id"), nullable=False, index=True)
    integrity_ref: Mapped[str] = mapped_column(String(80), nullable=False)
    extractor: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    failure_reason: Mapped[str | None] = mapped_column(String(40))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    page_count: Mapped[int | None] = mapped_column(Integer)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MaterialChunkRecord(Base):
    """Bounded searchable span of extracted text; identity = (extraction, sequence)."""

    __tablename__ = "material_chunks"
    __table_args__ = (UniqueConstraint("extraction_id", "sequence", name="uq_material_chunk_sequence"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    extraction_id: Mapped[UUID] = mapped_column(ForeignKey("material_extractions.id"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    text: Mapped[str] = mapped_column(Text, nullable=False)


class ConversationMaterialEvidenceRecord(Base):
    """Material excerpts a delegated AX turn actually retrieved (SPEC-008 evidence card)."""

    __tablename__ = "conversation_material_evidence"
    __table_args__ = (UniqueConstraint("turn_id", "chunk_id", name="uq_conversation_material_evidence"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    turn_id: Mapped[UUID] = mapped_column(ForeignKey("conversation_turns.id"), nullable=False, index=True)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"), nullable=False, index=True)
    execution_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    task_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    material_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    attachment_id: Mapped[UUID] = mapped_column(ForeignKey("attachments.id"), nullable=False)
    chunk_id: Mapped[UUID] = mapped_column(ForeignKey("material_chunks.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    integrity_ref: Mapped[str] = mapped_column(String(80), nullable=False)
    page: Mapped[int | None] = mapped_column(Integer)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    query: Mapped[str] = mapped_column(String(300), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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
    request_thread_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    subject_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    organization_context_id: Mapped[str | None] = mapped_column(String(100))
    requester_id: Mapped[str] = mapped_column(String(100), nullable=False)
    assignee_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    due_date: Mapped[date | None] = mapped_column(Date)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    conditions: Mapped[dict | None] = mapped_column(JSON)
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
    assigned_by: Mapped[str] = mapped_column(String(100), nullable=False)
    assignment_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    source_work_request_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    source_decision_item_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    source_review_decision_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
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
