from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, Uuid, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class OrganizationUnitRecord(Base):
    __tablename__ = "organization_units"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)


class MemberRecord(Base):
    __tablename__ = "members"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    employment_state: Mapped[str] = mapped_column(String(40), nullable=False)


class EmploymentPeriodRecord(Base):
    __tablename__ = "employment_periods"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(40), nullable=False)


class MembershipRecord(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("member_id", "organization_id", name="uq_member_organization"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), nullable=False)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organization_units.id"), nullable=False)


class AccessGrantRecord(Base):
    __tablename__ = "access_grants"
    __table_args__ = (UniqueConstraint("member_id", "capability", name="uq_member_capability"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), nullable=False)
    capability: Mapped[str] = mapped_column(String(120), nullable=False)


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
    cli_run_ref: Mapped[str | None] = mapped_column(String(200))
    cli_thread_ref: Mapped[str | None] = mapped_column(String(200))
    requested_model: Mapped[str] = mapped_column(String(120), nullable=False)
    observed_model: Mapped[str | None] = mapped_column(String(120))
    requested_tier: Mapped[str] = mapped_column(String(80), nullable=False)
    observed_tier: Mapped[str | None] = mapped_column(String(80))
    latency_ms: Mapped[int | None] = mapped_column(nullable=True)
    usage: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    normalized_error: Mapped[str | None] = mapped_column(Text)


class HumanDecisionRecord(Base):
    __tablename__ = "human_decisions"
    __table_args__ = (UniqueConstraint("node_execution_id", name="uq_human_decision_node_execution"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    node_execution_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflow_node_executions.id"), nullable=False
    )
    principal_id: Mapped[str] = mapped_column(String(100), nullable=False)
    decision: Mapped[str] = mapped_column(String(40), nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ToolExecutionRecord(Base):
    __tablename__ = "tool_executions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    node_execution_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflow_node_executions.id"), nullable=False
    )
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditEventRecord(Base):
    __tablename__ = "audit_events"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkRecord(Base):
    __tablename__ = "work_records"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)


class TaskRecord(Base):
    __tablename__ = "tasks"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    block_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TaskActivityRecord(Base):
    __tablename__ = "task_activities"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    task_version: Mapped[int] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MeetingEvidenceRecord(Base):
    __tablename__ = "meeting_evidence"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    candidate_task: Mapped[str] = mapped_column(String(300), nullable=False)


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


class ReportAuditEventRecord(Base):
    __tablename__ = "report_audit_events"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    report_id: Mapped[UUID] = mapped_column(ForeignKey("daily_reports.id"), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(100), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TaskAssignmentRecord(Base):
    __tablename__ = "task_assignments"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False, unique=True)
    assignee_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ContractApprovalRecord(Base):
    __tablename__ = "contract_approvals"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False, unique=True)
    contract_id: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def make_session_factory(database_url: str):
    return sessionmaker(bind=create_engine(database_url, pool_pre_ping=True), expire_on_commit=False)
