from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ax_workspace.platform.persistence import AccessGrantRecord, MemberRecord, MembershipRecord, OrganizationUnitRecord


class SqlAlchemyOrganizationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def profile_for(self, member_id: str) -> dict[str, Any] | None:
        member = self._session.get(MemberRecord, member_id)
        if member is None or member.employment_state != "active":
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
