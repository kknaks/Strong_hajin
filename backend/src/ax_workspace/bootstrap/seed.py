from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ax_workspace.platform.persistence import (
    AccessGrantRecord,
    AppointmentRecord,
    CapabilityRecord,
    EmploymentPeriodRecord,
    GradeAssignmentRecord,
    GradeRecord,
    JobAssignmentRecord,
    JobRecord,
    MemberRecord,
    MembershipRecord,
    OrganizationUnitRecord,
    OrganizationUnitTypeRecord,
    PositionDefinitionRecord,
    RoleCapabilityRecord,
    RoleRecord,
    StandardGrantRuleRecord,
    WorkflowDefinitionRecord,
    WorkflowDefinitionVersionRecord,
)
from ax_workspace.modules.reports.workflow_metadata import content_hash, daily_report_generation_v1


def seed_catalog(session: Session) -> None:
    """Install the product demo's persisted configuration after an explicit reset."""
    _seed_organization_access(session)
    _install_daily_report_generation(session)
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


ORGANIZATION_UNIT_TYPES = [
    ("company", "회사", 0),
    ("division", "본부", 1),
    ("office", "실", 2),
    ("team", "팀", 3),
    ("part", "파트", 4),
]

# id, name, type, parent, display_order — depth is independent of type (파트 sits under a 팀, 인사팀 sits directly under a 본부).
ORGANIZATION_UNITS = [
    ("scax", "SCAX", "company", None, 0),
    ("product-division", "제품본부", "division", "scax", 0),
    ("platform-office", "플랫폼실", "office", "product-division", 0),
    ("product", "제품팀", "team", "platform-office", 0),
    ("engineering", "개발팀", "team", "platform-office", 1),
    ("infra-part", "인프라파트", "part", "engineering", 0),
    ("management-division", "경영지원본부", "division", "scax", 1),
    ("legal", "법무팀", "team", "management-division", 0),
    ("finance", "재무팀", "team", "management-division", 1),
    ("people", "피플팀", "team", "management-division", 2),
]

POSITION_DEFINITIONS = [
    ("ceo", "company", "대표", "head"),
    ("division-head", "division", "본부장", "head"),
    ("office-head", "office", "실장", "head"),
    ("team-lead", "team", "팀장", "head"),
    ("team-deputy", "team", "부팀장", "deputy"),
    ("part-lead", "part", "파트장", "head"),
]

GRADES = [("staff", "사원", 0), ("associate", "대리", 1), ("manager", "과장", 2), ("senior-manager", "차장", 3), ("director", "부장", 4)]
JOBS = [("planning", "기획"), ("engineering", "개발"), ("legal", "법무"), ("finance", "재무"), ("people", "인사")]

# persona -> (grade, job, primary unit, position)
PERSONA_PLACEMENT = {
    "mina": ("associate", "planning", "product", None),
    "jiho": ("senior-manager", "planning", "product", "team-lead"),
    "sora": ("manager", "legal", "legal", None),
    "minseok": ("manager", "finance", "finance", None),
    "demo-admin": ("director", "people", "people", None),
}


def _seed_organization_access(session: Session) -> None:
    for type_id, name, order in ORGANIZATION_UNIT_TYPES:
        if session.get(OrganizationUnitTypeRecord, type_id) is None:
            session.add(OrganizationUnitTypeRecord(id=type_id, name=name, display_order=order))
    for unit_id, name, type_id, parent_id, order in ORGANIZATION_UNITS:
        unit = session.get(OrganizationUnitRecord, unit_id)
        if unit is None:
            session.add(OrganizationUnitRecord(id=unit_id, name=name, unit_type_id=type_id, parent_id=parent_id, display_order=order))
        else:
            unit.unit_type_id, unit.parent_id, unit.display_order = type_id, parent_id, order
    for position_id, type_id, name, slot in POSITION_DEFINITIONS:
        if session.get(PositionDefinitionRecord, position_id) is None:
            session.add(PositionDefinitionRecord(id=position_id, organization_unit_type_id=type_id, name=name, slot_key=slot))
    for grade_id, name, order in GRADES:
        if session.get(GradeRecord, grade_id) is None:
            session.add(GradeRecord(id=grade_id, name=name, display_order=order))
    for job_id, name in JOBS:
        if session.get(JobRecord, job_id) is None:
            session.add(JobRecord(id=job_id, name=name))
    session.flush()

    from ax_workspace.modules.organization_access.domain import SEED_PERSONAS

    all_capabilities = sorted(
        {capability for principal in SEED_PERSONAS.values() for capability in principal.capabilities}
    )
    for capability in all_capabilities:
        if session.get(CapabilityRecord, capability) is None:
            session.add(CapabilityRecord(id=capability, label=capability, version=1, group=capability.split(".")[0]))

    for principal in SEED_PERSONAS.values():
        member_id = str(principal.id)
        role_id = f"seed-role:{member_id}"
        grade_id, job_id, primary_unit, position_id = PERSONA_PLACEMENT[member_id]
        if session.get(MemberRecord, member_id) is None:
            session.add(MemberRecord(id=member_id, display_name=principal.display_name, employment_state="active", account_ref=f"developer:{member_id}"))
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
                is_primary = organization_id == primary_unit
                session.add(
                    MembershipRecord(
                        member_id=member_id,
                        organization_id=organization_id,
                        is_primary=is_primary,
                        membership_kind="primary" if is_primary else "additional",
                    )
                )
        if session.scalar(select(GradeAssignmentRecord).where(GradeAssignmentRecord.member_id == member_id)) is None:
            session.add(GradeAssignmentRecord(member_id=member_id, grade_id=grade_id))
        if session.scalar(select(JobAssignmentRecord).where(JobAssignmentRecord.member_id == member_id)) is None:
            session.add(JobAssignmentRecord(member_id=member_id, job_id=job_id, assignment_kind="primary"))
        if session.get(RoleRecord, role_id) is None:
            session.add(RoleRecord(id=role_id, label=f"{principal.display_name} 기본 역할", version=1))
        if session.scalar(select(AppointmentRecord).where(AppointmentRecord.member_id == member_id)) is None:
            session.add(
                AppointmentRecord(
                    member_id=member_id,
                    organization_id=primary_unit if position_id else "scax",
                    role_id=role_id,
                    position_definition_id=position_id,
                    appointment_kind="primary",
                )
            )
        # ERD STANDARD_GRANT_RULE: the appointment (a position, or plain membership) fixes which role is granted and at what scope.
        rule_id = f"standard:{position_id or 'member'}:{role_id}"
        if session.get(StandardGrantRuleRecord, rule_id) is None:
            session.add(
                StandardGrantRuleRecord(
                    id=rule_id,
                    trigger_kind="appointment",
                    trigger_source_ref=position_id or "member",
                    role_id=role_id,
                    scope_template="descendants",
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
        # ERD ACCESS_GRANT: the role is granted as a snapshot (role_capability_version) at the appointment's unit scope.
        grant = session.scalar(
            select(AccessGrantRecord).where(AccessGrantRecord.member_id == member_id, AccessGrantRecord.role_id == role_id)
        )
        if grant is None:
            session.add(
                AccessGrantRecord(
                    member_id=member_id,
                    capability_id=None,
                    role_id=role_id,
                    role_capability_version=1,
                    scope_kind="unit",
                    scope_organization_id=primary_unit,
                    scope_ref=primary_unit,
                    include_descendants=True,
                    granted_by_member_id="system",
                    origin_rule_id=rule_id,
                    origin_rule_version=1,
                )
            )
