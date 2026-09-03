from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ax_workspace.modules.organization_access.domain import PersonaId, Principal
from ax_workspace.platform.persistence import AccessGrantRecord, EmploymentPeriodRecord, MemberRecord, MembershipRecord, OrganizationUnitRecord


class SqlAlchemyOrganizationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def profile_for(self, member_id: str) -> dict[str, Any] | None:
        member = self._session.get(MemberRecord, member_id)
        employment = self._session.scalar(select(EmploymentPeriodRecord).where(EmploymentPeriodRecord.member_id == member_id))
        if member is None or member.employment_state != "active" or employment is None or employment.state != "active":
            return None
        organizations = self._session.execute(
            select(OrganizationUnitRecord.id, OrganizationUnitRecord.name)
            .join(MembershipRecord, MembershipRecord.organization_id == OrganizationUnitRecord.id)
            .where(MembershipRecord.member_id == member_id)
            .order_by(OrganizationUnitRecord.id)
        ).all()
        capabilities = self._session.scalars(
            select(AccessGrantRecord.capability)
            .where(AccessGrantRecord.member_id == member_id)
            .order_by(AccessGrantRecord.capability)
        ).all()
        return {
            "member_id": member.id,
            "display_name": member.display_name,
            "organizations": [{"id": item.id, "name": item.name} for item in organizations],
            "capabilities": list(capabilities),
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
