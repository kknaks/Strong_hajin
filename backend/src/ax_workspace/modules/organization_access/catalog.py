"""제품이 설치하는 capability 목록과 권장 역할 template.

Two things live here and they are not the same thing. A **capability** is a name for something the product can
actually do; if an operation exists, its capability is in this list, and a key that is not in it is a mistake rather
than a silently powerless grant. A **role template** is the product's recommendation for how those capabilities are
usually bundled — 구성원, 팀장, 인사 담당자, 대표.

A template is a starting point, not a rule. Once installed, the role belongs to the organization: the seed will add a
role it has never installed, but it never rewrites one a person has changed. What a role means today is read from the
database, never from this file.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    id: str
    label: str
    group: str


@dataclass(frozen=True, slots=True)
class RoleTemplate:
    """A recommended bundle. `key`/`version` say which recommendation an installed role came from."""

    key: str
    label: str
    version: int
    capabilities: tuple[str, ...]
    #: How wide the grant that comes with this role reaches: the appointed unit and below, or the whole organization.
    scope_template: str = "descendants"

    @property
    def role_id(self) -> str:
        return f"role:{self.key}"


class UnknownCapability(ValueError):
    """A role was asked to carry a capability the product does not implement."""


CAPABILITIES: tuple[CapabilitySpec, ...] = (
    CapabilitySpec("work.read", "업무 읽기", "work"),
    CapabilitySpec("task.read", "업무 상세 읽기", "work"),
    CapabilitySpec("task.self_manage", "내 업무 관리", "work"),
    CapabilitySpec("task.accept", "배정 수락·거절", "work"),
    CapabilitySpec("task.assign", "업무 배정", "work"),
    CapabilitySpec("work_request.read", "업무 요청 읽기", "work"),
    CapabilitySpec("work_request.create", "업무 요청 보내기", "work"),
    CapabilitySpec("work_request.decide", "업무 요청 판단", "work"),
    CapabilitySpec("action.read", "판단 항목 읽기", "work"),
    CapabilitySpec("action.decide", "판단 항목 결정", "work"),
    CapabilitySpec("team.manage", "팀 관리", "organization"),
    CapabilitySpec("report.review", "보고 확인", "report"),
    CapabilitySpec("daily_report.read", "일일보고 읽기", "report"),
    CapabilitySpec("daily_report.generate", "일일보고 초안 생성", "report"),
    CapabilitySpec("daily_report.edit", "일일보고 편집", "report"),
    CapabilitySpec("daily_report.submit", "일일보고 제출", "report"),
    CapabilitySpec("meeting.read", "회의 읽기", "meeting"),
    CapabilitySpec("meeting.read.private", "비공개 회의 읽기", "meeting"),
    CapabilitySpec("meeting.manage", "회의 만들기·수정", "meeting"),
    CapabilitySpec("meeting.share", "회의 공유", "meeting"),
    CapabilitySpec("meeting.record", "회의 녹음", "meeting"),
    CapabilitySpec("meeting.followup.request", "후속 업무 요청", "meeting"),
    CapabilitySpec("meeting.followup.assign", "후속 업무 배정", "meeting"),
    CapabilitySpec("material.purge", "자료 완전 삭제", "material"),
)

CAPABILITY_IDS = frozenset(capability.id for capability in CAPABILITIES)

_MEMBER_CAPABILITIES = (
    "work.read",
    "task.read",
    "task.self_manage",
    "task.accept",
    "work_request.read",
    "work_request.create",
    "action.read",
    "action.decide",
    "daily_report.read",
    "daily_report.generate",
    "daily_report.edit",
    "daily_report.submit",
    "meeting.read",
    "meeting.manage",
    "meeting.share",
    "meeting.record",
    "meeting.followup.request",
)

# 팀장 is not 구성원 plus extras. A lead judges requests, assigns work and reviews reports; writing one's own daily
# report is the 구성원 side of the same organization, and a lead who also needs it holds the 구성원 role as well.
_LEAD_CAPABILITIES = (
    "work.read",
    "task.read",
    "task.self_manage",
    "task.assign",
    "work_request.read",
    "work_request.decide",
    "action.read",
    "action.decide",
    "report.review",
    "team.manage",
    "meeting.read",
    "meeting.manage",
    "meeting.share",
    "meeting.record",
    "meeting.followup.assign",
)

_PEOPLE_CAPABILITIES = _MEMBER_CAPABILITIES + (
    "task.assign",
    "team.manage",
    "report.review",
    "meeting.followup.assign",
    # 인사 담당자 answers for the data about people, which is why deleting a file for good sits here.
    "material.purge",
)

ROLE_TEMPLATES: tuple[RoleTemplate, ...] = (
    # Someone who comes to meetings and nothing else — an outside adviser, a contractor between engagements.
    RoleTemplate("guest", "외부 참여자", 1, ("meeting.read",)),
    RoleTemplate("member", "구성원", 1, _MEMBER_CAPABILITIES),
    RoleTemplate("team-lead", "팀장", 1, _LEAD_CAPABILITIES),
    RoleTemplate("people-manager", "인사 담당자", 1, _PEOPLE_CAPABILITIES),
    # 대표 is appointed at the company, so the same role reaches the whole organization rather than one unit's subtree.
    RoleTemplate(
        "executive",
        "대표",
        1,
        _PEOPLE_CAPABILITIES + ("work_request.decide", "meeting.read.private"),
        scope_template="organization",
    ),
)

ROLE_TEMPLATES_BY_KEY = {template.key: template for template in ROLE_TEMPLATES}


def validate(template: RoleTemplate) -> RoleTemplate:
    """A role may only carry capabilities the product implements; an invented key is a failure, not a no-op."""
    unknown = sorted(set(template.capabilities) - CAPABILITY_IDS)
    if unknown:
        raise UnknownCapability(f"구현된 기능이 아닌 capability입니다: {', '.join(unknown)}")
    return template
