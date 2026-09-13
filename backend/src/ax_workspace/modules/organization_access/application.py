"""Public authorized organization queries."""
from __future__ import annotations

from ax_workspace.modules.organization_access.results import MemberAxisHistoryView, MemberCandidateView, MemberDetailView, MemberDirectoryView, MyOrganizationProfileView, OrganizationActivityView, OrganizationProfileView, OrganizationUnitView, UnitMemberView

from typing import Any, Protocol

from ax_workspace.modules.organization_access.administration import (
    AccessAdministrationDenied,
    AccessNotFound,
    ORGANIZATION_MANAGE,
    manages_any_of,
)
from ax_workspace.modules.organization_access.credentials import (
    AuthenticationFailed,
    LocalCredential,
    verify_password,
)
from ax_workspace.modules.organization_access.domain import MEMBER_HISTORY_AXES, Principal


from ax_workspace.modules.organization_access.commands import AssistantCharacterInput, AssistantCharacterResult, ASSISTANT_CHARACTER_KEYS

DEFAULT_ASSISTANT_CHARACTER = "cream-cat"



class UnsupportedAssistantCharacter(ValueError):
    pass


class AssistantCharacterPreferenceConflict(RuntimeError):
    pass


class OrganizationRepository(Protocol):
    def profile_for(self, member_id: str) -> OrganizationProfileView | None: ...
    def credential_for_email(self, email: str) -> LocalCredential | None: ...
    def member_directory(self) -> list[MemberDirectoryView]: ...
    def demo_accounts(self, email_domain: str) -> list[dict[str, str]]: ...
    def principal_for(self, member_id: str) -> Principal | None: ...
    def work_request_assignee_candidates(self, principal: Principal) -> list[MemberCandidateView]: ...
    def task_assignment_candidates(self, principal: Principal) -> list[MemberCandidateView]: ...
    def member_candidates(self, principal: Principal) -> list[MemberCandidateView]: ...
    def organization_tree(self) -> list[OrganizationUnitView]: ...
    def unit_members(self, unit_id: str, *, include_descendants: bool = True) -> list[UnitMemberView]: ...
    def assistant_character_preference(self, member_id: str) -> dict[str, Any] | None: ...
    def save_assistant_character_preference(
        self, member_id: str, character_key: str, expected_version: int
    ) -> dict[str, Any]: ...
    def member_detail(self, member_id: str) -> MemberDetailView | None: ...
    def member_axis_history(self, member_id: str, axis: str) -> list[MemberAxisHistoryView]: ...
    def member_units(self, member_id: str) -> frozenset[str]: ...
    def member_ids_in(self, units: frozenset[str]) -> frozenset[str]: ...
    def unit_descendants(self, unit_id: str) -> frozenset[str]: ...
    def organization_root(self, *, near: str | None = None) -> str | None: ...
    def organization_activity(
        self, *, member_ids: frozenset[str] | None, limit: int, cursor: str | None
    ) -> list[OrganizationActivityView]: ...


class OrganizationApplication:
    def __init__(self, repository: OrganizationRepository) -> None:
        self._repository = repository

    def my_profile(self, principal: Principal) -> MyOrganizationProfileView:
        profile = self._repository.profile_for(str(principal.id))
        if profile is None:
            raise LookupError("organization member was not found")
        preference = self._repository.assistant_character_preference(str(principal.id))
        return {**profile, "assistant_character": preference or {
            "character_key": DEFAULT_ASSISTANT_CHARACTER,
            "version": 0,
        }}

    def set_assistant_character(
        self, principal: Principal, character_key: str, expected_version: int
    ) -> AssistantCharacterResult:
        command = AssistantCharacterInput(character_key=character_key, expected_version=expected_version)
        character_key, expected_version = command.character_key, command.expected_version
        if character_key not in ASSISTANT_CHARACTER_KEYS:
            raise UnsupportedAssistantCharacter("지원하지 않는 AX 캐릭터입니다.")
        return self._repository.save_assistant_character_preference(
            str(principal.id), character_key, expected_version
        )

    def authenticate_with_password(self, email: str, password: str) -> Principal:
        """Prove who someone is. What they may then do is read from the ledger, never from the login.

        Unknown address, wrong password and ended membership all fail the same way: an attacker who guesses addresses
        must not learn which of them belong to someone who works here.
        """
        credential = self._repository.credential_for_email(email)
        if credential is None or not verify_password(password, credential.password_hash):
            raise AuthenticationFailed("이메일 또는 비밀번호가 올바르지 않습니다.")
        principal = self._repository.principal_for(credential.member_id)
        if principal is None:
            raise AuthenticationFailed("이메일 또는 비밀번호가 올바르지 않습니다.")
        return principal

    def demo_accounts(self, email_domain: str) -> list[dict[str, str]]:
        """Local demo sign-in shortcuts. Not an authorized read of anything: the caller is not signed in yet."""
        return self._repository.demo_accounts(email_domain)

    def member_directory(self, principal: Principal) -> list[MemberDirectoryView]:
        """이름은 누구에게나, 인사 정보는 조직을 관리하는 사람에게만.

        SPEC-005 §2: 결과 field는 현재 Principal의 권한에 맞게 제한한다. 감추는 방법으로 field를 지우지는
        않는다 — 응답의 모양이 사람마다 달라지면 client가 그 모양으로 권한을 추측하게 되고, 화면마다 다른
        규칙이 생긴다. 자리는 그대로 두고 값을 비운다.
        """
        rows = self._repository.member_directory()
        if principal.allows(ORGANIZATION_MANAGE):
            return rows
        return [{**row, "phone": None, "birth_date": None} for row in rows]

    def authenticated_principal(self, persona_id: str) -> Principal:
        principal = self._repository.principal_for(persona_id)
        if principal is None:
            raise LookupError("active organization member was not found")
        return principal

    def work_request_assignee_candidates(self, principal: Principal) -> list[MemberCandidateView]:
        return self._repository.work_request_assignee_candidates(principal)

    def organization_tree(self, principal: Principal) -> list[OrganizationUnitView]:
        """Read-only navigation for any active principal; the tree never widens work or HR scope."""
        return self._repository.organization_tree()

    #: 이 사람의 여섯 축 중 아무나 보아도 되는 것과, 본인·관리자만 보는 것.
    #: 계층·소속·직책·직급·직무·재직·계정 유무는 명부와 같은 기준으로 열려 있다.
    SENSITIVE_MEMBER_FIELDS = ("phone", "birth_date")
    SENSITIVE_MEMBER_LISTS = ("grants", "revoked_grants")

    def member_detail(self, principal: Principal, member_id: str) -> MemberDetailView:
        """한 사람을 여섯 축으로 한 번에. 볼 수 없는 축은 자리를 남기고 값을 비운다.

        SPEC-005 §2: 결과 field는 현재 Principal의 권한에 맞게 제한한다. 거절하지 않고 비우는 이유는, 이 화면이
        누구에게나 열리는 조직도이기 때문이다 — 이름과 자리는 명부가 이미 말하고, 연락처와 권한만 닫으면 된다.
        응답의 모양은 누구에게나 같다: 모양이 사람마다 달라지면 client가 그 모양으로 권한을 추측하게 된다.
        """
        detail = self._repository.member_detail(member_id)
        if detail is None:
            raise AccessNotFound("재직 중인 구성원을 찾을 수 없습니다")
        if self._may_read_sensitive(principal, member_id):
            return detail
        hidden = dict(detail)
        for field in self.SENSITIVE_MEMBER_FIELDS:
            hidden[field] = None
        for field in self.SENSITIVE_MEMBER_LISTS:
            hidden[field] = []
        return hidden

    def member_axis_history(self, principal: Principal, member_id: str, axis: str) -> list[MemberAxisHistoryView]:
        """한 축이 지나온 기간들. 이력은 지금 값보다 많은 것을 말하므로, 볼 자격을 먼저 묻는다."""
        if axis not in MEMBER_HISTORY_AXES:
            raise ValueError(f"모르는 축입니다: {axis} (가능: {', '.join(MEMBER_HISTORY_AXES)})")
        if self._repository.member_detail(member_id) is None:
            raise AccessNotFound("재직 중인 구성원을 찾을 수 없습니다")
        if not self._may_read_sensitive(principal, member_id):
            raise AccessNotFound("구성원 이력을 찾을 수 없습니다")
        return self._repository.member_axis_history(member_id, axis)

    def organization_activity(
        self, principal: Principal, *, unit_id: str | None = None, limit: int = 50, cursor: str | None = None
    ) -> list[OrganizationActivityView]:
        """조직 축의 변경 기록. 한 조직을 물으면 그 아래 사람들의 사건만 남는다.

        변경 기록은 누가 무엇을 왜 바꿨는지를 사람 단위로 모아 보여 준다 — 그 조직을 관리할 수 있는 사람에게만
        연다. 조직을 말하지 않으면 **그 사람의 조직 전체**를 물은 것이다: 한 데이터베이스에 회사가 둘 있을 수
        있으므로 「전체」는 이 데이터베이스 전부가 아니라 묻는 사람이 속한 회사다. 자격을 확인한 그 범위가
        그대로 조회 범위가 된다 — 둘이 어긋나면 거절한 것을 조건 없이 내주게 된다(PR #2 F8).
        """
        scope = unit_id or self._own_root(principal)
        if scope is None:
            raise AccessNotFound("조직이 아직 없습니다")
        if not principal.allows(ORGANIZATION_MANAGE, unit=scope):
            raise AccessAdministrationDenied("이 범위의 변경 기록을 볼 수 있는 자격이 없습니다")
        member_ids = self._repository.member_ids_in(self._repository.unit_descendants(scope))
        return self._repository.organization_activity(member_ids=member_ids, limit=limit, cursor=cursor)

    def _own_root(self, principal: Principal) -> str | None:
        """이 사람의 회사 꼭대기. 자기 자리에서 위로 올라가 만나는 곳이지, 데이터베이스의 첫 뿌리가 아니다."""
        own = sorted(self._repository.member_units(str(principal.id)))
        if own:
            return self._repository.organization_root(near=own[0])
        return self._repository.organization_root()

    def _may_read_sensitive(self, principal: Principal, member_id: str) -> bool:
        """본인이거나, 그 사람의 자리를 관리할 수 있는 사람."""
        return str(principal.id) == member_id or manages_any_of(principal, self._repository.member_units(member_id))

    def unit_members(self, principal: Principal, unit_id: str) -> list[UnitMemberView]:
        if not any(unit["id"] == unit_id for unit in self._repository.organization_tree()):
            raise AccessNotFound("조직을 찾을 수 없습니다")
        return self._repository.unit_members(unit_id)

    def task_assignment_candidates(self, principal: Principal) -> list[MemberCandidateView]:
        return self._repository.task_assignment_candidates(principal)

    def member_candidates(self, principal: Principal) -> list[MemberCandidateView]:
        return self._repository.member_candidates(principal)

    def is_active_member(self, principal: Principal, member_id: str) -> bool:
        return any(candidate["id"] == member_id for candidate in self.member_candidates(principal))

    def is_task_assignee(self, principal: Principal, assignee_id: str) -> bool:
        return any(candidate["id"] == assignee_id for candidate in self.task_assignment_candidates(principal))

    def is_work_request_assignee(self, principal: Principal, assignee_id: str) -> bool:
        return any(candidate["id"] == assignee_id for candidate in self.work_request_assignee_candidates(principal))
