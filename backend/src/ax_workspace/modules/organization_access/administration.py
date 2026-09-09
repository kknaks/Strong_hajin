"""역할과 권한을 바꾸는 command.

Access can be changed while the product is running, but not in ways that leave the organization unable to manage
itself. Three things are refused rather than performed: taking away the last authority that covers the whole
organization, taking away your own last authority, and writing over a role someone else has just changed.

Every change is an act by a person: the administrator is the actor, the person whose access moved is the target, and
the reason they gave is kept with it.
"""
from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from ax_workspace.modules.organization_access.catalog import CAPABILITY_IDS, UnknownCapability
from ax_workspace.modules.organization_access.domain import Principal

ORGANIZATION_MANAGE = "organization.manage"


def manages_any_of(principal: Principal, units: frozenset[str]) -> bool:
    """이 사람의 자리 중 하나라도 관리 범위에 들어오는가.

    판정은 여기 한 곳에만 산다. 바꾸는 쪽은 이것이 거짓이면 거절하고, 읽는 쪽은 거짓이면 민감한 field를 비운다 —
    같은 규칙을 두 곳에 적어 두면 언젠가 한쪽만 고쳐진다.
    """
    return any(principal.allows(ORGANIZATION_MANAGE, unit=unit) for unit in units)


class AccessAdministrationError(Exception):
    """The change would leave the organization in a state it must not be in."""


class AccessAdministrationDenied(Exception):
    """The administrator's own authority does not cover this person or this role."""


class AccessNotFound(Exception):
    pass


class AccessVersionConflict(Exception):
    """Someone else changed this role first; the edit was written against a version that no longer exists."""


class AccessAdministrationRepository(Protocol):
    def principal_for(self, member_id: str) -> Principal | None: ...
    def member_units(self, member_id: str) -> frozenset[str]: ...
    def organization_root(self, *, near: str | None = None) -> str | None: ...
    def role_exists(self, role_id: str) -> bool: ...
    def installed_roles(self) -> list[dict[str, Any]]: ...
    def profile_for(self, member_id: str) -> dict[str, Any] | None: ...
    def role_version(self, role_id: str) -> int | None: ...
    def add_role_grant(
        self,
        *,
        member_id: str,
        role_id: str,
        scope_kind: str,
        scope_ref: str,
        include_descendants: bool,
        granted_by: str,
    ) -> dict[str, Any]: ...
    def grant(self, grant_id: UUID) -> Any | None: ...
    def revoke_grant(self, grant_id: UUID) -> None: ...
    def replace_role_capabilities(self, role_id: str, capabilities: list[str]) -> int: ...
    def members_administering(self, unit: str) -> set[str]: ...
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
    ) -> None: ...


class AccessAdministration:
    def __init__(self, repository: AccessAdministrationRepository) -> None:
        self._repository = repository

    # ---- queries --------------------------------------------------------

    def installed_roles(self, principal: Principal) -> list[dict[str, Any]]:
        """The roles this organization actually has, as they are now — not the product's recommendation."""
        self._require_authority_over_unit(principal, self._root())
        return self._repository.installed_roles()

    def member_access(self, principal: Principal, member_id: str) -> dict[str, Any]:
        """What one person may do and where, for someone whose authority covers them."""
        self._require_authority_over_member(principal, member_id)
        profile = self._repository.profile_for(member_id)
        if profile is None:
            raise AccessNotFound("재직 중인 구성원을 찾을 수 없습니다")
        return {
            "member_id": profile["member_id"],
            "display_name": profile["display_name"],
            "roles": profile["roles"],
            "capabilities": profile["capabilities"],
            "grants": profile["grants"],
        }

    # ---- commands -------------------------------------------------------

    def grant_role(
        self,
        principal: Principal,
        *,
        member_id: str,
        role_id: str,
        scope_kind: str = "unit",
        scope_ref: str | None = None,
        include_descendants: bool = True,
        reason: str | None = None,
    ) -> dict[str, Any]:
        # 범위를 말하지 않으면 조직 전체다. 그 조직이 무엇으로 불리는지는 원장이 안다.
        scope_ref = scope_ref or self._root()
        self._require_authority_over_member(principal, member_id)
        self._require_authority_over_unit(principal, scope_ref if scope_kind == "unit" else self._root())
        if not self._repository.role_exists(role_id):
            raise AccessNotFound("역할을 찾을 수 없습니다")
        if self._repository.principal_for(member_id) is None:
            raise AccessNotFound("재직 중인 구성원을 찾을 수 없습니다")
        granted = self._repository.add_role_grant(
            member_id=member_id,
            role_id=role_id,
            scope_kind=scope_kind,
            scope_ref=scope_ref,
            include_descendants=include_descendants,
            granted_by=str(principal.id),
        )
        self._repository.append_access_audit(
            target_type="member",
            target_id=member_id,
            event_kind="access.grant_added",
            actor_id=str(principal.id),
            summary=f"{role_id} 권한을 {scope_ref} 범위로 부여",
            reason=reason,
            after_ref=f"access_grant:{granted['grant_id']}",
        )
        return granted

    def revoke_grant(self, principal: Principal, grant_id: UUID, *, reason: str | None = None) -> dict[str, Any]:
        grant = self._repository.grant(grant_id)
        if grant is None or grant.revoked_at is not None:
            raise AccessNotFound("권한 부여를 찾을 수 없습니다")
        member_id = str(grant.member_id)
        self._require_authority_over_member(principal, member_id)
        self._repository.revoke_grant(grant_id)
        self._require_someone_still_administers(principal, changed_member=member_id)
        self._repository.append_access_audit(
            target_type="member",
            target_id=member_id,
            event_kind="access.grant_revoked",
            actor_id=str(principal.id),
            summary=f"{grant.role_id or grant.capability_id} 권한 회수",
            reason=reason,
            before_ref=f"access_grant:{grant_id}",
        )
        return {"grant_id": str(grant_id), "member_id": member_id, "revoked": True}

    def set_role_capabilities(
        self,
        principal: Principal,
        role_id: str,
        capabilities: list[str],
        *,
        expected_version: int,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """What a role means is the organization's to decide — within what the product can actually do."""
        self._require_authority_over_unit(principal, self._root())
        current = self._repository.role_version(role_id)
        if current is None:
            raise AccessNotFound("역할을 찾을 수 없습니다")
        if current != expected_version:
            raise AccessVersionConflict("이 역할은 그 사이에 다른 곳에서 바뀌었습니다")
        unknown = sorted({capability for capability in capabilities} - CAPABILITY_IDS)
        if unknown:
            raise UnknownCapability(f"구현된 기능이 아닌 capability입니다: {', '.join(unknown)}")
        version = self._repository.replace_role_capabilities(role_id, sorted(set(capabilities)))
        self._require_someone_still_administers(principal, changed_member=None)
        self._repository.append_access_audit(
            target_type="role",
            target_id=role_id,
            event_kind="access.role_changed",
            actor_id=str(principal.id),
            summary=f"{role_id} 역할의 기능 {len(set(capabilities))}개로 변경",
            reason=reason,
            before_ref=f"role:{role_id}@{expected_version}",
            after_ref=f"role:{role_id}@{version}",
        )
        return {"role_id": role_id, "version": version, "customized": True, "capabilities": sorted(set(capabilities))}

    # ---- guards ---------------------------------------------------------

    def _require_authority_over_member(self, principal: Principal, member_id: str) -> None:
        if not manages_any_of(principal, self._repository.member_units(member_id)):
            raise AccessAdministrationDenied("이 구성원의 권한을 바꿀 수 있는 범위가 아닙니다")

    def _root(self) -> str:
        """이 조직의 꼭대기. 상수로 알고 있지 않고 원장에 묻는다 — 회사 이름은 고객마다 다르다."""
        root = self._repository.organization_root()
        if root is None:
            raise AccessNotFound("조직이 아직 없습니다")
        return root

    def _require_authority_over_unit(self, principal: Principal, unit: str) -> None:
        if not principal.allows(ORGANIZATION_MANAGE, unit=unit):
            raise AccessAdministrationDenied("이 범위의 권한을 바꿀 수 있는 자격이 없습니다")

    def _require_someone_still_administers(self, principal: Principal, *, changed_member: str | None) -> None:
        """After the change, someone must still be able to administer the whole organization — and be told if it is not you.

        This runs after the write and raises, so the caller's transaction is the thing that undoes it: a check made
        before the change would be answering about a state that no longer exists by the time it lands.
        """
        remaining = self._repository.members_administering(self._root())
        if not remaining:
            if changed_member is not None and changed_member == str(principal.id):
                raise AccessAdministrationError("자기 자신의 마지막 관리 권한은 이렇게 회수할 수 없습니다")
            raise AccessAdministrationError("조직을 관리할 수 있는 사람이 최소 한 명은 남아 있어야 합니다")
