from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ax_workspace.platform.persistence import (
    AccessGrantRecord,
    AppointmentRecord,
    CapabilityRecord,
    EmploymentPeriodRecord,
    MemberRecord,
    MeetingEvidenceRecord,
    MembershipRecord,
    OrganizationUnitRecord,
    RoleCapabilityRecord,
    RoleRecord,
    WorkRecord,
    WorkflowDefinitionRecord,
    WorkflowDefinitionVersionRecord,
)
from ax_workspace.modules.ax_execution.domain import catalog_definitions
from ax_workspace.modules.reports.workflow_metadata import content_hash, daily_report_generation_v1


def seed_catalog(session: Session) -> None:
    """Install the product demo's persisted configuration after an explicit reset."""
    _seed_organization_access(session)
    _install_daily_report_generation(session)
    session.commit()


def seed_technical_workflow_spike(session: Session) -> None:
    """Install legacy generic examples only for isolated runtime regression tests."""
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


def _install_daily_report_generation(session: Session) -> None:
    workflow_id = "daily-report-generation"
    definition = daily_report_generation_v1()
    if session.get(WorkflowDefinitionRecord, workflow_id) is None:
        session.add(
            WorkflowDefinitionRecord(
                id=workflow_id,
                title="개인 일일보고 초안 생성",
                owner_id="scax",
                scope="scax",
            )
        )
    existing = session.scalar(
        select(WorkflowDefinitionVersionRecord).where(
            WorkflowDefinitionVersionRecord.workflow_id == workflow_id,
            WorkflowDefinitionVersionRecord.version == "1",
        )
    )
    if existing is None:
        session.add(
            WorkflowDefinitionVersionRecord(
                workflow_id=workflow_id,
                version="1",
                definition=definition,
                schema_version=definition["schema_version"],
                status="published",
                content_hash=content_hash(definition),
                created_at=datetime.now(UTC),
                published_at=datetime.now(UTC),
            )
        )


def _seed_organization_access(session: Session) -> None:
    organizations = {"scax": "SCAX", "product": "제품팀", "legal": "법무팀", "finance": "재무팀", "people": "피플팀"}
    for organization_id, name in organizations.items():
        if session.get(OrganizationUnitRecord, organization_id) is None:
            session.add(OrganizationUnitRecord(id=organization_id, name=name))

    from ax_workspace.modules.organization_access.domain import SEED_PERSONAS

    direct_grant_capabilities = {
        "task.accept",
        "meeting.followup.request",
        "meeting.followup.assign",
        "demo.admin",
    }

    all_capabilities = sorted(
        {capability for principal in SEED_PERSONAS.values() for capability in principal.capabilities}
    )
    for capability in all_capabilities:
        if session.get(CapabilityRecord, capability) is None:
            session.add(CapabilityRecord(id=capability, label=capability, version=1))

    for principal in SEED_PERSONAS.values():
        member_id = str(principal.id)
        role_id = f"seed-role:{member_id}"
        if session.get(MemberRecord, member_id) is None:
            session.add(MemberRecord(id=member_id, display_name=principal.display_name, employment_state="active"))
        if session.scalar(select(EmploymentPeriodRecord).where(EmploymentPeriodRecord.member_id == member_id)) is None:
            session.add(EmploymentPeriodRecord(member_id=member_id, state="active"))
        for organization_id in principal.organization_scope:
            exists = session.scalar(
                select(MembershipRecord).where(
                    MembershipRecord.member_id == member_id,
                    MembershipRecord.organization_id == organization_id,
                )
            )
            if exists is None:
                session.add(
                    MembershipRecord(
                        member_id=member_id,
                        organization_id=organization_id,
                        is_primary=organization_id == "scax",
                    )
                )
        if session.get(RoleRecord, role_id) is None:
            session.add(RoleRecord(id=role_id, label=f"{principal.display_name} 기본 역할", version=1))
        if session.scalar(select(AppointmentRecord).where(AppointmentRecord.member_id == member_id)) is None:
            session.add(
                AppointmentRecord(
                    member_id=member_id,
                    organization_id="scax",
                    role_id=role_id,
                )
            )
        for capability in principal.capabilities:
            role_capability = session.scalar(
                select(RoleCapabilityRecord).where(
                    RoleCapabilityRecord.role_id == role_id,
                    RoleCapabilityRecord.capability_id == capability,
                )
            )
            if role_capability is None:
                session.add(RoleCapabilityRecord(role_id=role_id, capability_id=capability, mapping_version=1))
            if capability in direct_grant_capabilities:
                exists = session.scalar(
                    select(AccessGrantRecord).where(
                        AccessGrantRecord.member_id == member_id,
                        AccessGrantRecord.capability_id == capability,
                    )
                )
                if exists is None:
                    session.add(
                        AccessGrantRecord(
                            member_id=member_id,
                            capability_id=capability,
                            scope_organization_id="scax",
                            granted_by_member_id=member_id,
                        )
                    )
