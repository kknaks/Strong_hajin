from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ax_workspace.modules.organization_access.catalog import (
    CAPABILITIES,
    ROLE_TEMPLATES,
    ROLE_TEMPLATES_BY_KEY,
    RoleTemplate,
    validate,
)
from ax_workspace.modules.organization_access.credentials import hash_password, normalize_email
from ax_workspace.platform.persistence import (
    AccessGrantRecord,
    AppointmentRecord,
    CapabilityRecord,
    EmploymentPeriodRecord,
    GradeAssignmentRecord,
    GradeRecord,
    JobAssignmentRecord,
    JobRecord,
    MemberCredentialRecord,
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


# The local demo's shared password. It exists only behind `reset_demo`, which refuses anything but a local demo
# database, and the login route that accepts it is not registered in the production profile.
DEMO_PASSWORD = "scax-demo-1234"
DEMO_EMAIL_DOMAIN = "scax.example"


def demo_email(member_id: str) -> str:
    return normalize_email(f"{member_id}@{DEMO_EMAIL_DOMAIN}")


def seed_catalog(session: Session) -> None:
    """Install the product demo's persisted configuration after an explicit reset."""
    _seed_organization_access(session)
    _seed_local_credentials(session)
    _install_daily_report_generation(session)
    session.commit()


def _seed_local_credentials(session: Session) -> None:
    """Give every seeded member an ordinary email/password login, so the demo signs in like the product does."""
    for member_id in session.scalars(select(MemberRecord.id).order_by(MemberRecord.id)):
        if session.get(MemberCredentialRecord, member_id) is not None:
            continue
        session.add(
            MemberCredentialRecord(
                member_id=member_id,
                email=demo_email(member_id),
                password_hash=hash_password(DEMO_PASSWORD),
            )
        )


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

@dataclass(frozen=True, slots=True)
class SeededMember:
    """One person in the demo organization: where they sit, and which recommended role came with that."""

    id: str
    display_name: str
    unit: str
    grade: str
    job: str
    role_key: str
    position: str | None = None
    #: Units this person also belongs to, beyond their own unit and the company.
    additional_units: tuple[str, ...] = ()


SEEDED_MEMBERS: tuple[SeededMember, ...] = (
    SeededMember("yuna", "유나 (대표)", "scax", "director", "planning", "executive", position="ceo"),
    SeededMember("jiho", "지호 (팀장)", "product", "senior-manager", "planning", "team-lead", position="team-lead"),
    SeededMember("mina", "민아 (구성원)", "product", "associate", "planning", "member"),
    SeededMember("hyeon", "현우 (인사)", "people", "director", "people", "people-manager"),
    # 외부 법률 자문: 회의에만 참여하고 업무 원장에는 들어오지 않는다.
    SeededMember("sora", "소라 (법무 자문)", "legal", "manager", "legal", "guest"),
    SeededMember("minseok", "민석 (재무)", "finance", "manager", "finance", "member"),
)


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

    install_role_catalog(session, ROLE_TEMPLATES)
    session.flush()

    for member in SEEDED_MEMBERS:
        template = ROLE_TEMPLATES_BY_KEY[member.role_key]
        if session.get(MemberRecord, member.id) is None:
            session.add(
                MemberRecord(
                    id=member.id,
                    display_name=member.display_name,
                    employment_state="active",
                    account_ref=f"local:{demo_email(member.id)}",
                )
            )
        if session.scalar(select(EmploymentPeriodRecord).where(EmploymentPeriodRecord.member_id == member.id)) is None:
            session.add(EmploymentPeriodRecord(member_id=member.id, state="active"))
        for organization_id in sorted({"scax", member.unit, *member.additional_units}):
            exists = session.scalar(
                select(MembershipRecord).where(
                    MembershipRecord.member_id == member.id,
                    MembershipRecord.organization_id == organization_id,
                )
            )
            if exists is None:
                is_primary = organization_id == member.unit
                session.add(
                    MembershipRecord(
                        member_id=member.id,
                        organization_id=organization_id,
                        is_primary=is_primary,
                        membership_kind="primary" if is_primary else "additional",
                    )
                )
        if session.scalar(select(GradeAssignmentRecord).where(GradeAssignmentRecord.member_id == member.id)) is None:
            session.add(GradeAssignmentRecord(member_id=member.id, grade_id=member.grade))
        if session.scalar(select(JobAssignmentRecord).where(JobAssignmentRecord.member_id == member.id)) is None:
            session.add(JobAssignmentRecord(member_id=member.id, job_id=member.job, assignment_kind="primary"))
        if session.scalar(select(AppointmentRecord).where(AppointmentRecord.member_id == member.id)) is None:
            session.add(
                AppointmentRecord(
                    member_id=member.id,
                    organization_id=member.unit,
                    role_id=template.role_id,
                    position_definition_id=member.position,
                    appointment_kind="primary",
                )
            )
        # ERD STANDARD_GRANT_RULE: the appointment — a position, or plain membership — fixes which role is granted
        # and how wide it reaches. 대표 is appointed at the company, so that role covers the whole organization.
        rule_id = f"standard:{member.position or 'member'}:{template.role_id}"
        if session.get(StandardGrantRuleRecord, rule_id) is None:
            session.add(
                StandardGrantRuleRecord(
                    id=rule_id,
                    trigger_kind="appointment",
                    trigger_source_ref=member.position or "member",
                    role_id=template.role_id,
                    scope_template=template.scope_template,
                )
            )
        # ERD ACCESS_GRANT: the role is granted as a snapshot (role_capability_version) at the appointment's scope.
        organization_wide = template.scope_template == "organization"
        grant = session.scalar(
            select(AccessGrantRecord).where(
                AccessGrantRecord.member_id == member.id, AccessGrantRecord.role_id == template.role_id
            )
        )
        if grant is None:
            session.add(
                AccessGrantRecord(
                    member_id=member.id,
                    capability_id=None,
                    role_id=template.role_id,
                    role_capability_version=template.version,
                    scope_kind="organization" if organization_wide else "unit",
                    scope_organization_id="scax" if organization_wide else member.unit,
                    scope_ref="scax" if organization_wide else member.unit,
                    include_descendants=True,
                    granted_by_member_id="system",
                    origin_rule_id=rule_id,
                    origin_rule_version=1,
                )
            )


def install_role_catalog(session: Session, templates: Iterable[RoleTemplate]) -> None:
    """이 제품이 아는 권한 전부와, 요청된 역할들을 설치한다.

    권한 목록은 제품의 것이므로 언제나 최신으로 맞춘다. 역할은 권하는 것이고, 조직이 자기 것으로 만든 역할
    (`customized_at`)은 손대지 않는다. seed와 dataset import가 같은 이 한 곳을 쓴다.
    """
    for capability in CAPABILITIES:
        record = session.get(CapabilityRecord, capability.id)
        if record is None:
            session.add(CapabilityRecord(id=capability.id, label=capability.label, version=1, group=capability.group))
        else:
            record.label, record.group = capability.label, capability.group
    for template in templates:
        _install_role_template(session, template)


def _install_role_template(session: Session, template: RoleTemplate) -> None:
    """Install a recommended role once. A role the organization has changed is left exactly as it is.

    A product update may propose new capabilities, but it never silently widens a role someone here decided on:
    `customized_at` is the organization saying "this role is ours now".
    """
    validate(template)
    role = session.get(RoleRecord, template.role_id)
    if role is None:
        session.add(
            RoleRecord(
                id=template.role_id,
                label=template.label,
                version=template.version,
                template_key=template.key,
                template_version=template.version,
            )
        )
    elif role.customized_at is not None:
        return
    for capability in template.capabilities:
        exists = session.scalar(
            select(RoleCapabilityRecord).where(
                RoleCapabilityRecord.role_id == template.role_id,
                RoleCapabilityRecord.capability_id == capability,
            )
        )
        if exists is None:
            session.add(
                RoleCapabilityRecord(
                    role_id=template.role_id, capability_id=capability, mapping_version=template.version
                )
            )
