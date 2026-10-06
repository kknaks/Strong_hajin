"""Authorized organization projections; candidates never carry directory HR fields."""
from typing_extensions import NotRequired, TypedDict
from ax_workspace.modules.organization_access.commands import AssistantCharacterResult


class MemberCandidateView(TypedDict):
    id: str
    display_name: str


class MemberDirectoryView(MemberCandidateView):
    phone: str | None
    birth_date: str | None
    has_account: bool


class NamedOrganizationView(TypedDict):
    id: str
    name: str


class OrganizationLeaderView(TypedDict):
    display_name: str
    position: str
    kind: str


class OrganizationUnitView(NamedOrganizationView):
    parent_id: str | None
    unit_type: str | None
    lifecycle: str
    display_order: int
    member_count: int
    direct_member_count: int
    leaders: list[OrganizationLeaderView]


class AccessGrantView(TypedDict):
    grant_id: str
    role_id: str | None
    role_label: str | None
    capability_id: str | None
    role_capability_version: int | None
    scope_kind: str
    scope_ref: str | None
    scope_name: str | None
    include_descendants: bool
    capabilities: list[str]
    origin_rule_id: str | None
    granted_by: str | None
    valid_from: str
    valid_until: str | None


class MemberAccessView(TypedDict):
    member_id: str
    display_name: str
    roles: list[str]
    capabilities: list[str]
    grants: list[AccessGrantView]


class OrganizationProfileView(MemberAccessView):
    organizations: list[NamedOrganizationView]


class MyOrganizationProfileView(OrganizationProfileView):
    assistant_character: AssistantCharacterResult
    #: 프로필 이미지 주소(`GET /api/profile/image?v=…`) — 없으면 null 이고 화면은 이니셜 아바타를 그린다(SPEC-008 §4.7).
    profile_image_url: NotRequired[str | None]
    #: 프로필 머리의 읽기 전용 명부 값(SPEC-008 §2.6 · BE 수정 판 3) — 주 보직의 직책 · 주 직무. 없으면 null.
    position: NotRequired[str | None]
    job: NotRequired[str | None]


class MemberHierarchyView(TypedDict):
    unit_id: str
    name: str
    unit_type: str | None


class MemberMembershipView(TypedDict):
    unit_id: str
    unit_name: str
    kind: str
    valid_from: str
    valid_until: str | None


class MemberAppointmentView(MemberMembershipView):
    position: str
    role_id: str


class MemberJobView(NamedOrganizationView):
    kind: str


class RevokedAccessGrantView(TypedDict):
    grant_id: str
    role_id: str | None
    role_label: str | None
    scope_kind: str
    scope_ref: str | None
    scope_name: str | None
    valid_from: str
    revoked_at: str | None


class MemberDetailView(TypedDict):
    member_id: str
    display_name: str
    employment_state: str
    employment_type: str | None
    has_account: bool
    phone: str | None
    birth_date: str | None
    hierarchy_path: list[MemberHierarchyView]
    memberships: list[MemberMembershipView]
    appointments: list[MemberAppointmentView]
    grade: NamedOrganizationView | None
    jobs: list[MemberJobView]
    grants: list[AccessGrantView]
    revoked_grants: list[RevokedAccessGrantView]


class UnitMemberMembershipView(TypedDict):
    organization_id: str
    organization_name: str
    kind: str


class UnitMemberPositionView(TypedDict):
    position: str
    organization_name: str
    kind: str


class UnitMemberView(TypedDict):
    member_id: str
    display_name: str
    memberships: list[UnitMemberMembershipView]
    positions: list[UnitMemberPositionView]
    grade: str | None
    jobs: list[str]


class MemberAxisHistoryView(TypedDict):
    value: str | None
    unit_name: str | None
    kind: str | None
    valid_from: str
    valid_until: str | None
    reason: str | None
    actor: str | None


class OrganizationActivityView(TypedDict):
    occurred_at: str
    axis: str
    event_kind: str
    summary: str
    reason: str | None
    actor_id: str
    actor_name: str
    target_id: str
    target_type: str
    cursor: str


class InstalledRoleView(TypedDict):
    role_id: str
    label: str
    version: int
    template_key: str | None
    customized: bool
    capabilities: list[str]
