from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ax_workspace.platform.persistence import (
    AccessGrantRecord,
    MemberRecord,
    MeetingEvidenceRecord,
    MembershipRecord,
    OrganizationUnitRecord,
    WorkRecord,
    WorkflowDefinitionRecord,
    WorkflowDefinitionVersionRecord,
)
from ax_workspace.modules.ax_execution.domain import catalog_definitions


def seed_catalog(session: Session) -> None:
    """Idempotently seed code-owned immutable definition versions after an explicit reset."""
    _seed_organization_access(session)
    for definition in catalog_definitions():
        record = session.get(WorkflowDefinitionRecord, definition.workflow_id)
        if record is None:
            session.add(WorkflowDefinitionRecord(id=definition.workflow_id, title=definition.title))
        exists = session.scalar(
            select(WorkflowDefinitionVersionRecord).where(
                WorkflowDefinitionVersionRecord.workflow_id == definition.workflow_id,
                WorkflowDefinitionVersionRecord.version == definition.version,
            )
        )
        if exists is None:
            session.add(
                WorkflowDefinitionVersionRecord(
                    workflow_id=definition.workflow_id,
                    version=definition.version,
                    definition=definition.model_dump(mode="json"),
                    created_at=datetime.now(UTC),
            )
        )
    if session.scalar(select(WorkRecord).where(WorkRecord.owner_id == "mina")) is None:
        session.add_all(
            [
                WorkRecord(owner_id="mina", title="Catalog validation review", status="done"),
                WorkRecord(owner_id="mina", title="Demo script preparation", status="in_progress"),
            ]
        )
    if session.scalar(select(MeetingEvidenceRecord)) is None:
        session.add(
            MeetingEvidenceRecord(
                title="주간 운영 회의",
                candidate_task="회의 후속 업무 점검",
            )
        )
    session.commit()


def _seed_organization_access(session: Session) -> None:
    organizations = {"scax": "SCAX", "product": "제품팀", "legal": "법무팀", "finance": "재무팀"}
    for organization_id, name in organizations.items():
        if session.get(OrganizationUnitRecord, organization_id) is None:
            session.add(OrganizationUnitRecord(id=organization_id, name=name))

    from ax_workspace.modules.organization_access.domain import SEED_PERSONAS

    for principal in SEED_PERSONAS.values():
        member_id = str(principal.id)
        if session.get(MemberRecord, member_id) is None:
            session.add(MemberRecord(id=member_id, display_name=principal.display_name, employment_state="active"))
        for organization_id in principal.organization_scope:
            exists = session.scalar(
                select(MembershipRecord).where(
                    MembershipRecord.member_id == member_id,
                    MembershipRecord.organization_id == organization_id,
                )
            )
            if exists is None:
                session.add(MembershipRecord(member_id=member_id, organization_id=organization_id))
        for capability in principal.capabilities:
            exists = session.scalar(
                select(AccessGrantRecord).where(
                    AccessGrantRecord.member_id == member_id,
                    AccessGrantRecord.capability == capability,
                )
            )
            if exists is None:
                session.add(AccessGrantRecord(member_id=member_id, capability=capability))
