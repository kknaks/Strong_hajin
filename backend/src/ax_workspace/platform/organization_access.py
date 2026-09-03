from __future__ import annotations

from typing import Any

from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ax_workspace.modules.organization_access.domain import PersonaId, Principal
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
)


class SqlAlchemyOrganizationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def profile_for(self, member_id: str) -> dict[str, Any] | None:
        member = self._session.get(MemberRecord, member_id)
        now = datetime.now(UTC)
        employment = self._session.scalar(
            select(EmploymentPeriodRecord)
            .where(
                EmploymentPeriodRecord.member_id == member_id,
                EmploymentPeriodRecord.state == "active",
                EmploymentPeriodRecord.ended_at.is_(None),
            )
            .order_by(EmploymentPeriodRecord.started_at.desc())
        )
        if member is None or member.employment_state != "active" or employment is None or employment.state != "active":
            return None
        organizations = self._session.execute(
            select(OrganizationUnitRecord.id, OrganizationUnitRecord.name)
            .join(MembershipRecord, MembershipRecord.organization_id == OrganizationUnitRecord.id)
            .where(
                MembershipRecord.member_id == member_id,
                MembershipRecord.valid_from <= now,
                or_(MembershipRecord.valid_until.is_(None), MembershipRecord.valid_until > now),
            )
            .order_by(OrganizationUnitRecord.id)
        ).all()
        direct_capabilities = self._session.scalars(
            select(CapabilityRecord.id)
            .join(AccessGrantRecord, AccessGrantRecord.capability_id == CapabilityRecord.id)
            .where(
                AccessGrantRecord.member_id == member_id,
                AccessGrantRecord.valid_from <= now,
                AccessGrantRecord.revoked_at.is_(None),
                or_(AccessGrantRecord.valid_until.is_(None), AccessGrantRecord.valid_until > now),
            )
        ).all()
        role_capabilities = self._session.scalars(
            select(CapabilityRecord.id)
            .join(RoleCapabilityRecord, RoleCapabilityRecord.capability_id == CapabilityRecord.id)
            .join(RoleRecord, RoleRecord.id == RoleCapabilityRecord.role_id)
            .join(AppointmentRecord, AppointmentRecord.role_id == RoleRecord.id)
            .where(
                AppointmentRecord.member_id == member_id,
                AppointmentRecord.valid_from <= now,
                or_(AppointmentRecord.valid_until.is_(None), AppointmentRecord.valid_until > now),
            )
        ).all()
        roles = self._session.scalars(
            select(RoleRecord.label)
            .join(AppointmentRecord, AppointmentRecord.role_id == RoleRecord.id)
            .where(
                AppointmentRecord.member_id == member_id,
                AppointmentRecord.valid_from <= now,
                or_(AppointmentRecord.valid_until.is_(None), AppointmentRecord.valid_until > now),
            )
            .order_by(RoleRecord.id)
        ).all()
        capabilities = sorted(set(direct_capabilities).union(role_capabilities))
        return {
            "member_id": member.id,
            "display_name": member.display_name,
            "organizations": [{"id": item.id, "name": item.name} for item in organizations],
            "roles": list(roles),
            "capabilities": capabilities,
        }

    def principal_for(self, member_id: str) -> Principal | None:
        profile = self.profile_for(member_id)
        if profile is None:
            return None
        return Principal(
            id=PersonaId(member_id),
            display_name=str(profile["display_name"]),
            organization_scope=frozenset(item["id"] for item in profile["organizations"]),
            capabilities=frozenset(profile["capabilities"]),
        )

    def work_request_assignee_candidates(self, principal: Principal) -> list[dict[str, str]]:
        """Return active decision-capable peers whose current org scope overlaps the requester."""
        candidates: list[dict[str, str]] = []
        member_ids = self._session.scalars(select(MemberRecord.id).order_by(MemberRecord.id))
        for member_id in member_ids:
            if member_id == str(principal.id):
                continue
            candidate = self.principal_for(member_id)
            if candidate is None:
                continue
            if "work_request.decide" not in candidate.capabilities:
                continue
            if not principal.organization_scope.intersection(candidate.organization_scope):
                continue
            candidates.append({"id": str(candidate.id), "display_name": candidate.display_name})
        return candidates


    # ---- read-only organization navigation (ERD member_organization_view projection) ----

    def organization_tree(self) -> list[dict[str, Any]]:
        now = datetime.now(UTC)
        units = list(self._session.scalars(select(OrganizationUnitRecord).order_by(OrganizationUnitRecord.display_order, OrganizationUnitRecord.id)))
        types = {item.id: item for item in self._session.scalars(select(OrganizationUnitTypeRecord))}
        active_member_ids = set(
            self._session.scalars(
                select(EmploymentPeriodRecord.member_id).where(EmploymentPeriodRecord.state == "active", EmploymentPeriodRecord.ended_at.is_(None))
            )
        )
        memberships = self._session.execute(
            select(MembershipRecord.organization_id, MembershipRecord.member_id).where(
                MembershipRecord.valid_from <= now,
                or_(MembershipRecord.valid_until.is_(None), MembershipRecord.valid_until > now),
            )
        ).all()
        direct: dict[str, set[str]] = {}
        for organization_id, member_id in memberships:
            if member_id in active_member_ids:
                direct.setdefault(organization_id, set()).add(member_id)
        children: dict[str | None, list[OrganizationUnitRecord]] = {}
        for unit in units:
            children.setdefault(unit.parent_id, []).append(unit)

        def subtree_members(unit_id: str) -> set[str]:
            members = set(direct.get(unit_id, set()))
            for child in children.get(unit_id, []):
                members |= subtree_members(child.id)
            return members

        heads = self._session.execute(
            select(AppointmentRecord.organization_id, MemberRecord.display_name, PositionDefinitionRecord.name, AppointmentRecord.appointment_kind)
            .join(MemberRecord, MemberRecord.id == AppointmentRecord.member_id)
            .join(PositionDefinitionRecord, PositionDefinitionRecord.id == AppointmentRecord.position_definition_id)
            .where(AppointmentRecord.valid_from <= now, or_(AppointmentRecord.valid_until.is_(None), AppointmentRecord.valid_until > now))
        ).all()
        leaders: dict[str, list[dict[str, str]]] = {}
        for organization_id, display_name, position_name, kind in heads:
            leaders.setdefault(organization_id, []).append({"display_name": display_name, "position": position_name, "kind": kind})
        return [
            {
                "id": unit.id,
                "name": unit.name,
                "parent_id": unit.parent_id,
                "unit_type": types[unit.unit_type_id].name if unit.unit_type_id in types else None,
                "lifecycle": unit.lifecycle,
                "display_order": unit.display_order,
                "member_count": len(subtree_members(unit.id)),
                "direct_member_count": len(direct.get(unit.id, set())),
                "leaders": leaders.get(unit.id, []),
            }
            for unit in units
        ]

    def unit_members(self, unit_id: str, *, include_descendants: bool = True) -> list[dict[str, Any]]:
        now = datetime.now(UTC)
        units = list(self._session.scalars(select(OrganizationUnitRecord)))
        children: dict[str | None, list[str]] = {}
        for unit in units:
            children.setdefault(unit.parent_id, []).append(unit.id)
        scope = {unit_id}
        if include_descendants:
            stack = [unit_id]
            while stack:
                current = stack.pop()
                for child in children.get(current, []):
                    if child not in scope:
                        scope.add(child)
                        stack.append(child)
        unit_names = {unit.id: unit.name for unit in units}
        rows = self._session.execute(
            select(MembershipRecord.member_id, MembershipRecord.organization_id, MembershipRecord.membership_kind)
            .where(
                MembershipRecord.organization_id.in_(scope),
                MembershipRecord.valid_from <= now,
                or_(MembershipRecord.valid_until.is_(None), MembershipRecord.valid_until > now),
            )
        ).all()
        member_ids = sorted({row.member_id for row in rows})
        result: list[dict[str, Any]] = []
        for member_id in member_ids:
            profile = self.profile_for(member_id)
            if profile is None:
                continue
            memberships = [
                {"organization_id": row.organization_id, "organization_name": unit_names.get(row.organization_id, row.organization_id), "kind": row.membership_kind}
                for row in rows
                if row.member_id == member_id
            ]
            positions = self._session.execute(
                select(PositionDefinitionRecord.name, OrganizationUnitRecord.name, AppointmentRecord.appointment_kind)
                .join(PositionDefinitionRecord, PositionDefinitionRecord.id == AppointmentRecord.position_definition_id)
                .join(OrganizationUnitRecord, OrganizationUnitRecord.id == AppointmentRecord.organization_id)
                .where(
                    AppointmentRecord.member_id == member_id,
                    AppointmentRecord.valid_from <= now,
                    or_(AppointmentRecord.valid_until.is_(None), AppointmentRecord.valid_until > now),
                )
            ).all()
            grade = self._session.scalar(
                select(GradeRecord.name)
                .join(GradeAssignmentRecord, GradeAssignmentRecord.grade_id == GradeRecord.id)
                .where(GradeAssignmentRecord.member_id == member_id, or_(GradeAssignmentRecord.valid_until.is_(None), GradeAssignmentRecord.valid_until > now))
            )
            jobs = list(
                self._session.scalars(
                    select(JobRecord.name)
                    .join(JobAssignmentRecord, JobAssignmentRecord.job_id == JobRecord.id)
                    .where(JobAssignmentRecord.member_id == member_id, or_(JobAssignmentRecord.valid_until.is_(None), JobAssignmentRecord.valid_until > now))
                )
            )
            result.append(
                {
                    "member_id": member_id,
                    "display_name": profile["display_name"],
                    "memberships": memberships,
                    "positions": [{"position": name, "organization_name": unit, "kind": kind} for name, unit, kind in positions],
                    "grade": grade,
                    "jobs": jobs,
                }
            )
        return result
