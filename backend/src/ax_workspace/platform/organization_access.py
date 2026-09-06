from __future__ import annotations

from typing import Any
from uuid import UUID

from datetime import UTC, datetime

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from ax_workspace.modules.organization_access.credentials import LocalCredential, normalize_email
from ax_workspace.modules.organization_access.domain import TASK_ASSIGN, Grant, Principal
from ax_workspace.platform.persistence import (
    AccessGrantRecord,
    ActivityEventRecord,
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
        appointments = list(
            self._session.scalars(
                select(AppointmentRecord).where(
                    AppointmentRecord.member_id == member_id,
                    AppointmentRecord.valid_from <= now,
                    or_(AppointmentRecord.valid_until.is_(None), AppointmentRecord.valid_until > now),
                )
            )
        )
        appointed_roles = {item.role_id for item in appointments}
        grants = list(
            self._session.scalars(
                select(AccessGrantRecord)
                .where(
                    AccessGrantRecord.member_id == member_id,
                    AccessGrantRecord.valid_from <= now,
                    AccessGrantRecord.revoked_at.is_(None),
                    or_(AccessGrantRecord.valid_until.is_(None), AccessGrantRecord.valid_until > now),
                )
                .order_by(AccessGrantRecord.valid_from)
            )
        )
        capabilities: set[str] = set()
        effective_grants: list[AccessGrantRecord] = []
        granted_capabilities: dict[UUID, set[str]] = {}
        for grant in grants:
            # A grant materialized by a standard rule exists because of an appointment; it ends with that appointment.
            if grant.origin_rule_id is not None and grant.role_id not in appointed_roles:
                continue
            effective_grants.append(grant)
            carried = granted_capabilities.setdefault(grant.id, set())
            if grant.capability_id:
                capabilities.add(grant.capability_id)
                carried.add(grant.capability_id)
            if grant.role_id:
                # Role snapshot: only mappings that existed at the pinned role_capability_version apply.
                pinned = grant.role_capability_version or 1
                mapped = set(
                    self._session.scalars(
                        select(RoleCapabilityRecord.capability_id).where(
                            RoleCapabilityRecord.role_id == grant.role_id, RoleCapabilityRecord.mapping_version <= pinned
                        )
                    )
                )
                capabilities.update(mapped)
                carried.update(mapped)
        role_ids = sorted(appointed_roles | {grant.role_id for grant in effective_grants if grant.role_id})
        role_labels = {item.id: item.label for item in self._session.scalars(select(RoleRecord).where(RoleRecord.id.in_(role_ids)))} if role_ids else {}
        roles = [role_labels[role_id] for role_id in role_ids if role_id in role_labels]
        unit_names = {item.id: item.name for item in self._session.scalars(select(OrganizationUnitRecord))}
        capabilities = sorted(capabilities)
        return {
            "member_id": member.id,
            "display_name": member.display_name,
            "organizations": [{"id": item.id, "name": item.name} for item in organizations],
            "roles": roles,
            "capabilities": capabilities,
            "grants": [
                {
                    "grant_id": str(grant.id),
                    "role_id": grant.role_id,
                    "role_label": role_labels.get(grant.role_id or "", None),
                    "capability_id": grant.capability_id,
                    "role_capability_version": grant.role_capability_version,
                    "scope_kind": grant.scope_kind,
                    "scope_ref": grant.scope_ref,
                    "scope_name": unit_names.get(grant.scope_ref or "", grant.scope_ref),
                    "include_descendants": grant.include_descendants,
                    "capabilities": sorted(granted_capabilities.get(grant.id, set())),
                    "origin_rule_id": grant.origin_rule_id,
                    "granted_by": grant.granted_by_member_id,
                    "valid_from": grant.valid_from.isoformat(),
                    "valid_until": grant.valid_until.isoformat() if grant.valid_until else None,
                }
                for grant in effective_grants
            ],
        }

    def principal_for(self, member_id: str) -> Principal | None:
        profile = self.profile_for(member_id)
        if profile is None:
            return None
        descendants = self._unit_descendants()
        every_unit = frozenset(descendants)
        grants: list[Grant] = []
        for row in profile["grants"]:
            scope_ref = str(row["scope_ref"] or "")
            if row["scope_kind"] == "organization":
                units = every_unit
            elif row["include_descendants"]:
                units = descendants.get(scope_ref, frozenset({scope_ref}))
            else:
                units = frozenset({scope_ref})
            for capability in row["capabilities"]:
                grants.append(
                    Grant(
                        capability=capability,
                        scope_kind=str(row["scope_kind"]),
                        scope_ref=scope_ref,
                        units=units,
                        role_id=row["role_id"],
                        role_capability_version=row["role_capability_version"],
                        origin_rule_id=row["origin_rule_id"],
                    )
                )
        return Principal(
            id=member_id,
            display_name=str(profile["display_name"]),
            organization_scope=frozenset(item["id"] for item in profile["organizations"]),
            capabilities=frozenset(profile["capabilities"]),
            grants=tuple(grants),
        )

    def _unit_descendants(self) -> dict[str, frozenset[str]]:
        """Each unit with everything under it, so a scoped grant can be resolved to the units it actually reaches."""
        children: dict[str, list[str]] = {}
        unit_ids: list[str] = []
        for unit_id, parent_id in self._session.execute(
            select(OrganizationUnitRecord.id, OrganizationUnitRecord.parent_id)
        ).all():
            unit_ids.append(unit_id)
            if parent_id is not None:
                children.setdefault(parent_id, []).append(unit_id)
        resolved: dict[str, frozenset[str]] = {}
        for unit_id in unit_ids:
            reach = {unit_id}
            stack = [unit_id]
            while stack:
                for child in children.get(stack.pop(), []):
                    if child not in reach:
                        reach.add(child)
                        stack.append(child)
            resolved[unit_id] = frozenset(reach)
        return resolved

    def credential_for_email(self, email: str) -> LocalCredential | None:
        record = self._session.scalar(
            select(MemberCredentialRecord).where(MemberCredentialRecord.email == normalize_email(email))
        )
        if record is None:
            return None
        return LocalCredential(member_id=record.member_id, email=record.email, password_hash=record.password_hash)

    # ---- access administration (ERD ACCESS_GRANT / ROLE_CAPABILITY writes) ----

    def member_units(self, member_id: str) -> frozenset[str]:
        now = datetime.now(UTC)
        return frozenset(
            self._session.scalars(
                select(MembershipRecord.organization_id).where(
                    MembershipRecord.member_id == member_id,
                    MembershipRecord.valid_from <= now,
                    or_(MembershipRecord.valid_until.is_(None), MembershipRecord.valid_until > now),
                )
            )
        )

    def installed_roles(self) -> list[dict[str, Any]]:
        return [
            {
                "role_id": role.id,
                "label": role.label,
                "version": role.version,
                "template_key": role.template_key,
                "customized": role.customized_at is not None,
                "capabilities": sorted(
                    self._session.scalars(
                        select(RoleCapabilityRecord.capability_id).where(
                            RoleCapabilityRecord.role_id == role.id,
                            RoleCapabilityRecord.mapping_version <= role.version,
                        )
                    )
                ),
            }
            for role in self._session.scalars(select(RoleRecord).order_by(RoleRecord.id))
        ]

    def role_exists(self, role_id: str) -> bool:
        return self._session.get(RoleRecord, role_id) is not None

    def role_version(self, role_id: str) -> int | None:
        role = self._session.get(RoleRecord, role_id)
        return None if role is None else role.version

    def add_role_grant(
        self,
        *,
        member_id: str,
        role_id: str,
        scope_kind: str,
        scope_ref: str,
        include_descendants: bool,
        granted_by: str,
    ) -> dict[str, Any]:
        role = self._session.get(RoleRecord, role_id)
        grant = AccessGrantRecord(
            member_id=member_id,
            role_id=role_id,
            # The grant pins the role as it is now: a later change to the role does not travel backwards into it.
            role_capability_version=role.version if role is not None else 1,
            scope_kind=scope_kind,
            scope_organization_id=scope_ref if scope_kind == "unit" else "scax",
            scope_ref=scope_ref,
            include_descendants=include_descendants,
            granted_by_member_id=granted_by,
        )
        self._session.add(grant)
        self._session.flush()
        return {
            "grant_id": str(grant.id),
            "member_id": member_id,
            "role_id": role_id,
            "scope_kind": scope_kind,
            "scope_ref": scope_ref,
            "include_descendants": include_descendants,
            "role_capability_version": grant.role_capability_version,
        }

    def grant(self, grant_id: UUID) -> AccessGrantRecord | None:
        return self._session.get(AccessGrantRecord, grant_id)

    def revoke_grant(self, grant_id: UUID) -> None:
        grant = self._session.get(AccessGrantRecord, grant_id)
        if grant is not None and grant.revoked_at is None:
            grant.revoked_at = datetime.now(UTC)
            self._session.flush()

    def replace_role_capabilities(self, role_id: str, capabilities: list[str]) -> int:
        """A new mapping version, and the role marked as the organization's own from now on."""
        role = self._session.get(RoleRecord, role_id)
        if role is None:
            raise LookupError(role_id)
        version = role.version + 1
        self._session.execute(delete(RoleCapabilityRecord).where(RoleCapabilityRecord.role_id == role_id))
        for capability in capabilities:
            self._session.add(
                RoleCapabilityRecord(role_id=role_id, capability_id=capability, mapping_version=version)
            )
        role.version = version
        role.customized_at = datetime.now(UTC)
        # Grants that pinned an older snapshot would otherwise keep the previous meaning forever; the organization
        # changed what the role means, so the grants that carry that role move with it.
        self._session.execute(
            update(AccessGrantRecord)
            .where(AccessGrantRecord.role_id == role_id, AccessGrantRecord.revoked_at.is_(None))
            .values(role_capability_version=version)
        )
        self._session.flush()
        return version

    def members_administering(self, unit: str) -> set[str]:
        """Everyone who can still administer access covering this unit, as the ledger stands right now."""
        administering: set[str] = set()
        for member_id in self._session.scalars(select(MemberRecord.id)):
            principal = self.principal_for(member_id)
            if principal is not None and principal.allows("organization.manage", unit=unit):
                administering.add(member_id)
        return administering

    def append_access_audit(
        self,
        *,
        target_type: str,
        target_id: str,
        event_kind: str,
        actor_id: str,
        summary: str,
        reason: str | None,
        before_ref: str | None = None,
        after_ref: str | None = None,
    ) -> None:
        self._session.add(
            ActivityEventRecord(
                target_type=target_type,
                target_id=target_id,
                event_kind=event_kind,
                actor_id=actor_id,
                before_ref=before_ref,
                after_ref=after_ref,
                reason=reason,
                safe_summary=summary[:300],
                occurred_at=datetime.now(UTC),
            )
        )
        self._session.flush()

    def member_ids_in(self, units: frozenset[str]) -> frozenset[str]:
        """Active members whose current membership sits in one of these units."""
        if not units:
            return frozenset()
        now = datetime.now(UTC)
        active = set(
            self._session.scalars(
                select(EmploymentPeriodRecord.member_id).where(
                    EmploymentPeriodRecord.state == "active", EmploymentPeriodRecord.ended_at.is_(None)
                )
            )
        )
        rows = self._session.scalars(
            select(MembershipRecord.member_id).where(
                MembershipRecord.organization_id.in_(units),
                MembershipRecord.valid_from <= now,
                or_(MembershipRecord.valid_until.is_(None), MembershipRecord.valid_until > now),
            )
        )
        return frozenset(member_id for member_id in rows if member_id in active)

    def member_directory(self) -> list[dict[str, str]]:
        """Every active member's name, so the product can say who did what. It carries no capability."""
        return [
            {"id": str(member["member_id"]), "display_name": str(member["display_name"])}
            for member in self.unit_members("scax", include_descendants=True)
        ]

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


    def member_candidates(self, principal: Principal) -> list[dict[str, str]]:
        """Every active member other than the principal (참조자 후보); the org tree stays the navigation boundary."""
        return [
            {"id": str(member["member_id"]), "display_name": str(member["display_name"])}
            for member in self.unit_members("scax", include_descendants=True)
            if str(member["member_id"]) != str(principal.id)
        ]

    def task_assignment_candidates(self, principal: Principal) -> list[dict[str, str]]:
        """Active members inside the scope this person's assign authority was granted at, who can run a Task themselves.

        Belonging to a team is not the same as having authority over it: the reach comes from the grant, so a lead of
        one team never gains the ability to put work on another team by also being a member of it.
        """
        units = sorted(principal.scope_for(TASK_ASSIGN))
        seen: dict[str, str] = {}
        for unit_id in units:
            for member in self.unit_members(unit_id, include_descendants=True):
                member_id = str(member["member_id"])
                if member_id == str(principal.id) or member_id in seen:
                    continue
                candidate = self.principal_for(member_id)
                if candidate is None or "task.self_manage" not in candidate.capabilities:
                    continue
                seen[member_id] = candidate.display_name
        return [{"id": member_id, "display_name": name} for member_id, name in sorted(seen.items())]

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
