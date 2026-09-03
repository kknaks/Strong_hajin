"""Public authorized organization queries."""
from __future__ import annotations

from typing import Any, Protocol

from ax_workspace.modules.organization_access.domain import Principal


class OrganizationRepository(Protocol):
    def profile_for(self, member_id: str) -> dict[str, Any] | None: ...
    def principal_for(self, member_id: str) -> Principal | None: ...
    def work_request_assignee_candidates(self, principal: Principal) -> list[dict[str, str]]: ...


class OrganizationApplication:
    def __init__(self, repository: OrganizationRepository) -> None:
        self._repository = repository

    def my_profile(self, principal: Principal) -> dict[str, Any]:
        profile = self._repository.profile_for(str(principal.id))
        if profile is None:
            raise LookupError("organization member was not found")
        return profile

    def authenticated_principal(self, persona_id: str) -> Principal:
        principal = self._repository.principal_for(persona_id)
        if principal is None:
            raise LookupError("active organization member was not found")
        return principal

    def work_request_assignee_candidates(self, principal: Principal) -> list[dict[str, str]]:
        return self._repository.work_request_assignee_candidates(principal)

    def is_work_request_assignee(self, principal: Principal, assignee_id: str) -> bool:
        return any(candidate["id"] == assignee_id for candidate in self.work_request_assignee_candidates(principal))
