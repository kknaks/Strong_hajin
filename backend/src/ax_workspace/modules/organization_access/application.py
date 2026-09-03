"""Public authorized organization queries."""
from __future__ import annotations

from typing import Any, Protocol

from ax_workspace.modules.organization_access.domain import Principal


class OrganizationRepository(Protocol):
    def profile_for(self, member_id: str) -> dict[str, Any] | None: ...


class OrganizationApplication:
    def __init__(self, repository: OrganizationRepository) -> None:
        self._repository = repository

    def my_profile(self, principal: Principal) -> dict[str, Any]:
        profile = self._repository.profile_for(str(principal.id))
        if profile is None:
            raise LookupError("organization member was not found")
        return profile
