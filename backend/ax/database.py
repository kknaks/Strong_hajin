from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, Uuid, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class WorkflowDefinitionRecord(Base):
    __tablename__ = "workflow_definitions"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)


class WorkflowDefinitionVersionRecord(Base):
    __tablename__ = "workflow_definition_versions"
    __table_args__ = (UniqueConstraint("workflow_id", "version", name="uq_workflow_definition_version"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow_definitions.id"), nullable=False)
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    definition: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkflowRunRecord(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    definition_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflow_definition_versions.id"), nullable=False
    )
    initiator_id: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    input_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkflowNodeExecutionRecord(Base):
    __tablename__ = "workflow_node_executions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False)
    node_id: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    result: Mapped[dict | None] = mapped_column(JSON)


class HumanDecisionRecord(Base):
    __tablename__ = "human_decisions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    node_execution_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflow_node_executions.id"), nullable=False
    )
    principal_id: Mapped[str] = mapped_column(String(100), nullable=False)
    decision: Mapped[str] = mapped_column(String(40), nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)


class ToolExecutionRecord(Base):
    __tablename__ = "tool_executions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    node_execution_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflow_node_executions.id"), nullable=False
    )
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)


class AuditEventRecord(Base):
    __tablename__ = "audit_events"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def make_session_factory(database_url: str):
    return sessionmaker(bind=create_engine(database_url, pool_pre_ping=True), expire_on_commit=False)
