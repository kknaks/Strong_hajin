"""검증된 dataset을 제품의 원장에 넣는다 — 두 번 넣어도 한 번 넣은 것과 같다.

The importer is an adapter, not a second way of creating an organization. It writes the rows the product already
writes, found again by the keys a person chose, so running it twice finds what is there rather than making it twice.

Nothing here invents a fact. A date the source did not state stays empty and the record simply starts when it is
written; a person with no position gets no appointment. Access is not something a login carries: it comes from the
role the organization gave a person and reaches as far as that role's own scope says.

Passwords never travel in a dataset. A login row names a member and an address; the secret comes from the
environment at import time and is never written back out.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ax_workspace.bootstrap.seed import install_role_catalog
from ax_workspace.modules.organization_access.catalog import ROLE_TEMPLATES_BY_KEY, RoleTemplate
from ax_workspace.modules.organization_access.credentials import hash_password, normalize_email
from ax_workspace.platform.persistence import (
    AccessGrantRecord,
    AppointmentRecord,
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
    ProjectAssignmentRecord,
    ProjectRecord,
    RoleRecord,
    StandardGrantRuleRecord,
)


@dataclass(slots=True)
class ImportResult:
    """무엇이 새로 생겼고 무엇이 이미 있었는지, 그리고 무엇을 하지 않았는지."""

    created: dict[str, int] = field(default_factory=dict)
    unchanged: dict[str, int] = field(default_factory=dict)
    #: 하지 않은 일과 그 이유. 조용히 넘어가는 것이 없도록 남긴다.
    skipped: list[str] = field(default_factory=list)

    def track(self, table: str, *, made: bool) -> None:
        target = self.created if made else self.unchanged
        target[table] = target.get(table, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "created": dict(sorted(self.created.items())),
            "unchanged": dict(sorted(self.unchanged.items())),
            "skipped": self.skipped,
        }


class DatasetImportError(RuntimeError):
    """The dataset cannot be applied as it stands. Nothing is written."""


def _text(row: dict[str, str], column: str) -> str:
    return (row.get(column) or "").strip()


def _day(value: str) -> date | None:
    """사람이 적은 날짜. 빈 칸은 오늘이 되지 않고 그대로 비어 있다."""
    return date.fromisoformat(value) if value else None


def _moment(value: str) -> datetime | None:
    """A date someone wrote, at the start of that day. An empty cell stays empty rather than becoming today."""
    if not value:
        return None
    day = date.fromisoformat(value)
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _optional(**values: Any) -> dict[str, Any]:
    """Only the columns the source actually stated; the rest keep the record's own defaults."""
    return {name: value for name, value in values.items() if value is not None}


def import_dataset(session: Session, rows: dict[str, list[dict[str, str]]], *, password: str | None = None) -> ImportResult:
    """Apply a validated dataset. The key a person wrote is the identity the product keeps."""
    result = ImportResult()
    _import_units(session, rows, result)
    _import_vocabulary(session, rows, result)
    session.flush()
    roles = _install_roles(session, rows, result)
    session.flush()
    implied = _import_members(session, rows, result)
    session.flush()
    _import_links(session, rows, implied, result)
    _import_appointments(session, rows, roles, result)
    session.flush()
    _import_projects(session, rows, result)
    session.flush()
    _import_logins(session, rows, roles, password, result)
    session.flush()
    return result


def _import_units(session: Session, rows: dict[str, list[dict[str, str]]], result: ImportResult) -> None:
    """Units first, and parents before children, so a tree written in any order still lands as a tree."""
    listed = {_text(row, "key"): row for row in rows.get("organization_units", [])}
    for type_id in sorted({_text(row, "unit_type") for row in listed.values() if _text(row, "unit_type")}):
        if session.get(OrganizationUnitTypeRecord, type_id) is None:
            raise DatasetImportError(f"이 제품에 없는 조직 단위 종류입니다: {type_id}")

    remaining = dict(listed)
    while remaining:
        # A parent outside this dataset is one that already exists here; only a parent inside it must come first.
        ready = [key for key, row in remaining.items() if _text(row, "parent_key") not in remaining]
        if not ready:
            raise DatasetImportError(f"조직이 자기 아래로 들어갑니다: {', '.join(sorted(remaining))}")
        for key in sorted(ready):
            row = remaining.pop(key)
            parent = _text(row, "parent_key") or None
            if parent and parent not in listed and session.get(OrganizationUnitRecord, parent) is None:
                raise DatasetImportError(f"없는 조직을 상위로 가리킵니다: {key} → {parent}")
            unit = session.get(OrganizationUnitRecord, key)
            order = int(_text(row, "display_order") or 0)
            if unit is None:
                session.add(
                    OrganizationUnitRecord(
                        id=key, name=_text(row, "name"), unit_type_id=_text(row, "unit_type"), parent_id=parent, display_order=order
                    )
                )
                result.track("organization_units", made=True)
            else:
                unit.name, unit.unit_type_id, unit.parent_id, unit.display_order = (
                    _text(row, "name"),
                    _text(row, "unit_type"),
                    parent,
                    order,
                )
                result.track("organization_units", made=False)
            session.flush()


def _import_vocabulary(session: Session, rows: dict[str, list[dict[str, str]]], result: ImportResult) -> None:
    for row in rows.get("grades", []):
        key = _text(row, "key")
        if session.get(GradeRecord, key) is None:
            session.add(GradeRecord(id=key, name=_text(row, "name"), display_order=int(_text(row, "display_order") or 0)))
            result.track("grades", made=True)
        else:
            result.track("grades", made=False)
    for row in rows.get("jobs", []):
        key = _text(row, "key")
        if session.get(JobRecord, key) is None:
            session.add(JobRecord(id=key, name=_text(row, "name")))
            result.track("jobs", made=True)
        else:
            result.track("jobs", made=False)
    for row in rows.get("positions", []):
        key = _text(row, "key")
        if session.get(PositionDefinitionRecord, key) is None:
            session.add(
                PositionDefinitionRecord(
                    id=key,
                    organization_unit_type_id=_text(row, "unit_type"),
                    name=_text(row, "name"),
                    slot_key=_text(row, "slot") or "head",
                )
            )
            result.track("positions", made=True)
        else:
            result.track("positions", made=False)


def _install_roles(session: Session, rows: dict[str, list[dict[str, str]]], result: ImportResult) -> dict[str, RoleTemplate]:
    """Install the roles this dataset names — the recommended bundle, with the capabilities it actually carries.

    It is the same installation the seed does, so an import never leaves a role that exists but grants nothing. A
    role the organization has made its own is left exactly as it is: an import proposes, it does not rewrite.
    """
    named = {_text(row, "role_key") for row in rows.get("members", [])}
    named |= {_text(row, "role_key") for row in rows.get("positions", [])}
    templates: dict[str, RoleTemplate] = {}
    for key in sorted(named - {""}):
        template = ROLE_TEMPLATES_BY_KEY.get(key)
        if template is None:
            raise DatasetImportError(f"이 제품에 없는 역할입니다: {key}")
        templates[key] = template
        result.track("roles", made=session.get(RoleRecord, template.role_id) is None)
    install_role_catalog(session, templates.values())
    return templates


def _import_members(session: Session, rows: dict[str, list[dict[str, str]]], result: ImportResult) -> list[dict[str, str]]:
    """구성원과, 그 사람의 자리가 소속 표에 없을 때 그 자리가 뜻하는 소속."""
    implied: list[dict[str, str]] = []
    for row in rows.get("members", []):
        key = _text(row, "key")
        state = _text(row, "employment_state")
        kind = _text(row, "employment_type") or None
        member = session.get(MemberRecord, key)
        if member is None:
            session.add(
                MemberRecord(id=key, display_name=_text(row, "display_name"), employment_state=state, employment_type=kind)
            )
            result.track("members", made=True)
        else:
            member.display_name, member.employment_state = _text(row, "display_name"), state
            # 비어 있는 칸은 지우는 말이 아니다. 원문이 말하지 않는 것을 없앴다고 기록하지 않는다.
            if kind is not None:
                member.employment_type = kind
            result.track("members", made=False)
        if session.scalar(select(EmploymentPeriodRecord).where(EmploymentPeriodRecord.member_id == key)) is None:
            session.add(
                EmploymentPeriodRecord(
                    member_id=key,
                    state="active" if state == "active" else "ended",
                    ended_at=_moment(_text(row, "employed_until")),
                    **_optional(started_at=_moment(_text(row, "employed_from"))),
                )
            )
        grade = _text(row, "grade_key")
        if grade and session.scalar(select(GradeAssignmentRecord).where(GradeAssignmentRecord.member_id == key)) is None:
            session.add(GradeAssignmentRecord(member_id=key, grade_id=grade))
        # 소속은 memberships가 정본이다. primary_unit_key는 그 사람의 자리를 한 번 더 말하는 것이고, 표에 그 행이
        # 없으면 그 자리가 소속을 뜻한다 — 사람이 같은 사실을 두 곳에 쓰지 않아도 되도록.
        primary = _text(row, "primary_unit_key")
        if primary and not any(
            _text(link, "member_key") == key and _text(link, "unit_key") == primary for link in rows.get("memberships", [])
        ):
            implied.append({"member_key": key, "unit_key": primary, "kind": "primary"})
    return implied


def _import_links(
    session: Session,
    rows: dict[str, list[dict[str, str]]],
    implied: list[dict[str, str]],
    result: ImportResult,
) -> None:
    for row in [*rows.get("memberships", []), *implied]:
        member_key, unit_key = _text(row, "member_key"), _text(row, "unit_key")
        kind = _text(row, "kind") or "additional"
        exists = session.scalar(
            select(MembershipRecord).where(MembershipRecord.member_id == member_key, MembershipRecord.organization_id == unit_key)
        )
        if exists is not None:
            result.track("memberships", made=False)
            continue
        session.add(
            MembershipRecord(
                member_id=member_key,
                organization_id=unit_key,
                is_primary=kind == "primary",
                membership_kind=kind,
                valid_until=_moment(_text(row, "valid_until")),
                **_optional(valid_from=_moment(_text(row, "valid_from"))),
            )
        )
        result.track("memberships", made=True)

    for row in rows.get("job_assignments", []):
        member_key, job_key = _text(row, "member_key"), _text(row, "job_key")
        exists = session.scalar(
            select(JobAssignmentRecord).where(JobAssignmentRecord.member_id == member_key, JobAssignmentRecord.job_id == job_key)
        )
        if exists is None:
            session.add(JobAssignmentRecord(member_id=member_key, job_id=job_key, assignment_kind=_text(row, "kind") or "primary"))
            result.track("job_assignments", made=True)
        else:
            result.track("job_assignments", made=False)


def _import_appointments(
    session: Session,
    rows: dict[str, list[dict[str, str]]],
    roles: dict[str, RoleTemplate],
    result: ImportResult,
) -> None:
    """A position held at a unit, and the standard role that comes with holding it.

    Two different things are true about someone at once, and both are written down. The position they hold carries
    the role that comes with that position; the person carries the role their organization gave them. 인사총무팀장은
    팀장이면서 인사 담당자다 — 보직이 그 사람의 역할을 덮어쓰지 않는다.

    The rule behind each grant is written down as the product's own STANDARD_GRANT_RULE, so a grant can say where it
    came from and, later, whether it ends with a position or lasts as long as the person is here.
    """
    member_roles = {_text(row, "key"): _text(row, "role_key") for row in rows.get("members", [])}
    position_roles = {_text(row, "key"): _text(row, "role_key") for row in rows.get("positions", [])}
    for row in rows.get("appointments", []):
        member_key, unit_key, position_key = _text(row, "member_key"), _text(row, "unit_key"), _text(row, "position_key")
        role_key = position_roles.get(position_key) or member_roles.get(member_key, "")
        template = roles.get(role_key)
        if template is None:
            result.skipped.append(f"appointments:{member_key}@{unit_key} · 역할을 알 수 없어 보직을 만들지 않았습니다")
            continue
        rule_id = _standard_rule(session, template, "appointment", position_key or "member")
        exists = session.scalar(
            select(AppointmentRecord).where(
                AppointmentRecord.member_id == member_key,
                AppointmentRecord.organization_id == unit_key,
                AppointmentRecord.position_definition_id == position_key,
            )
        )
        if exists is None:
            session.add(
                AppointmentRecord(
                    member_id=member_key,
                    organization_id=unit_key,
                    role_id=template.role_id,
                    position_definition_id=position_key or None,
                    appointment_kind=_text(row, "kind") or "primary",
                    valid_until=_moment(_text(row, "valid_until")),
                    **_optional(valid_from=_moment(_text(row, "valid_from"))),
                )
            )
            result.track("appointments", made=True)
        else:
            result.track("appointments", made=False)
        _grant(session, member_key, template, unit_key, rule_id, result)

    # 그리고 모든 사람은 자기 역할을 갖는다. 보직을 맡았다고 그 사람이 원래 하던 일이 사라지지 않는다.
    # 이 grant는 보직이 아니라 소속이 만든 것이므로 보직이 끝나도 남는다.
    for row in rows.get("members", []):
        member_key = _text(row, "key")
        template = roles.get(_text(row, "role_key"))
        unit_key = _text(row, "primary_unit_key")
        if template is None or not unit_key:
            continue
        _grant(session, member_key, template, unit_key, _standard_rule(session, template, "membership", "member"), result)


def _standard_rule(session: Session, template: RoleTemplate, trigger_kind: str, source_ref: str) -> str:
    """ERD STANDARD_GRANT_RULE: what holding this position, or simply being here, suggests.

    무엇이 이 역할을 불러왔는지가 이름에 들어 있다. 같은 보직 이름과 같은 역할이라도 보직이 부른 것과 소속이 부른
    것은 끝나는 조건이 다르므로 한 규칙으로 합치지 않는다.
    """
    rule_id = f"standard:{trigger_kind}:{source_ref}:{template.role_id}"
    if session.get(StandardGrantRuleRecord, rule_id) is None:
        session.add(
            StandardGrantRuleRecord(
                id=rule_id,
                trigger_kind=trigger_kind,
                trigger_source_ref=source_ref,
                role_id=template.role_id,
                scope_template=template.scope_template,
            )
        )
        session.flush()
    return rule_id


def _grant(session: Session, member_key: str, template: RoleTemplate, unit_key: str, rule_id: str, result: ImportResult) -> None:
    """ERD ACCESS_GRANT — the role as a snapshot, at the scope the role's own template reaches."""
    organization_wide = template.scope_template == "organization"
    scope_ref = _root_of(session, unit_key) if organization_wide else unit_key
    exists = session.scalar(
        select(AccessGrantRecord).where(
            AccessGrantRecord.member_id == member_key,
            AccessGrantRecord.role_id == template.role_id,
            AccessGrantRecord.scope_ref == scope_ref,
        )
    )
    if exists is not None:
        result.track("grants", made=False)
        return
    session.add(
        AccessGrantRecord(
            member_id=member_key,
            role_id=template.role_id,
            role_capability_version=template.version,
            scope_kind="organization" if organization_wide else "unit",
            scope_organization_id=scope_ref,
            scope_ref=scope_ref,
            include_descendants=True,
            granted_by_member_id="dataset",
            origin_rule_id=rule_id,
            origin_rule_version=1,
        )
    )
    result.track("grants", made=True)


def _root_of(session: Session, unit_key: str) -> str:
    """조직 전체에 닿는 역할이 어디에 붙는지: 이 사람 자리에서 위로 끝까지 올라간 곳."""
    seen: set[str] = set()
    current = unit_key
    while current and current not in seen:
        seen.add(current)
        unit = session.get(OrganizationUnitRecord, current)
        if unit is None or not unit.parent_id:
            return current
        current = unit.parent_id
    return unit_key


def _import_projects(session: Session, rows: dict[str, list[dict[str, str]]], result: ImportResult) -> None:
    """프로젝트와 그 사람들. 배정은 조직 단위를 묻지 않고, 권한은 제품의 표준 규칙이 만든다."""
    if not rows.get("projects") and not rows.get("project_assignments"):
        return
    from ax_workspace.platform.projects import PROJECT_ROLE_KEY, grant_project_access

    template = ROLE_TEMPLATES_BY_KEY[PROJECT_ROLE_KEY]
    if session.get(RoleRecord, template.role_id) is None:
        install_role_catalog(session, [template])
        session.flush()

    known: dict[str, ProjectRecord] = {}
    for row in rows.get("projects", []):
        key = _text(row, "key")
        unit = _text(row, "unit_key")
        project = session.scalar(select(ProjectRecord).where(ProjectRecord.external_key == key))
        if project is None:
            project = ProjectRecord(
                name=_text(row, "name"),
                description=_text(row, "description") or None,
                organization_unit_id=unit,
                state=_text(row, "state") or "active",
                starts_on=_day(_text(row, "starts_on")),
                ends_on=_day(_text(row, "ends_on")),
                external_key=key,
                created_by_actor_id="dataset",
            )
            session.add(project)
            session.flush()
            result.track("projects", made=True)
        else:
            project.name, project.organization_unit_id = _text(row, "name"), unit
            result.track("projects", made=False)
        known[key] = project

    for row in rows.get("project_assignments", []):
        member_key, project_key = _text(row, "member_key"), _text(row, "project_key")
        project = known.get(project_key) or session.scalar(
            select(ProjectRecord).where(ProjectRecord.external_key == project_key)
        )
        if project is None:
            result.skipped.append(f"project_assignments:{member_key} · 없는 프로젝트({project_key})")
            continue
        exists = session.scalar(
            select(ProjectAssignmentRecord).where(
                ProjectAssignmentRecord.project_id == project.id,
                ProjectAssignmentRecord.member_id == member_key,
            )
        )
        if exists is not None:
            result.track("project_assignments", made=False)
            continue
        session.add(
            ProjectAssignmentRecord(
                project_id=project.id,
                member_id=member_key,
                assignment_kind=_text(row, "kind") or "member",
                valid_from=_moment(_text(row, "valid_from")),
                valid_until=_moment(_text(row, "valid_until")),
                assigned_by_member_id="dataset",
            )
        )
        grant_project_access(session, project_id=project.id, member_id=member_key, granted_by="dataset")
        result.track("project_assignments", made=True)


def _import_logins(
    session: Session,
    rows: dict[str, list[dict[str, str]]],
    roles: dict[str, RoleTemplate],
    password: str | None,
    result: ImportResult,
) -> None:
    """A login is a way in, not a permission. What the person may do was already decided by their role."""
    for row in rows.get("logins", []):
        member_key = _text(row, "member_key")
        email = normalize_email(_text(row, "email"))
        credential = session.get(MemberCredentialRecord, member_key)
        if credential is not None:
            credential.email = email
            credential.updated_at = datetime.now(UTC)
            result.track("logins", made=False)
            continue
        if not password:
            # An address with no secret is not a login. It is said out loud rather than half-made.
            result.skipped.append(f"logins:{member_key} · 비밀번호를 주지 않아 로그인을 만들지 않았습니다")
            continue
        session.add(MemberCredentialRecord(member_id=member_key, email=email, password_hash=hash_password(password)))
        member = session.get(MemberRecord, member_key)
        if member is not None:
            member.account_ref = f"local:{email}"
        result.track("logins", made=True)


def import_into(database_url: str, rows: dict[str, list[dict[str, str]]], *, password: str | None = None) -> ImportResult:
    """One transaction: either the whole dataset is in, or the database is exactly as it was."""
    from ax_workspace.platform.persistence import make_session_factory

    with make_session_factory(database_url)() as session:
        try:
            result = import_dataset(session, rows, password=password)
        except Exception:
            session.rollback()
            raise
        session.commit()
    return result
