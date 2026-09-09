from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Grant:
    """One capability, and how far it reaches.

    A capability without its scope is a different thing from the capability that was granted: 팀장 authority over
    제품팀 is not 팀장 authority. `units` is that reach already resolved — the unit it was granted at, plus everything
    under it when the grant says so.
    """

    capability: str
    scope_kind: str
    scope_ref: str
    units: frozenset[str]
    #: 프로젝트로 준 권한이 닿는 프로젝트들. 조직 단위와 나란한 두 번째 축이며, 한쪽이 다른 쪽을 대신하지 않는다.
    projects: frozenset[str] = frozenset()
    role_id: str | None = None
    role_capability_version: int | None = None
    origin_rule_id: str | None = None


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated person, as the Organization & Access ledger sees them.

    `id` is a plain member identity, not a login kind: which credential proved who this is says nothing about what they
    may do, and the same person keeps the same identity whichever provider they signed in with.

    `capabilities` says what this person may do *somewhere*; `grants` says where. A check that has a place to name
    should ask `allows(capability, unit=...)`, so one team's authority never becomes another's.
    """

    id: str
    display_name: str
    organization_scope: frozenset[str]
    capabilities: frozenset[str]
    grants: tuple[Grant, ...] = ()

    def scope_for(self, capability: str) -> frozenset[str]:
        """Every unit this capability actually reaches. Empty means it was never granted."""
        reach: set[str] = set()
        for grant in self.grants:
            if grant.capability == capability:
                reach |= grant.units
        return frozenset(reach)

    def projects_for(self, capability: str) -> frozenset[str]:
        """Every project this capability reaches. A project grant carries no unit, and a unit grant no project."""
        reach: set[str] = set()
        for grant in self.grants:
            if grant.capability == capability:
                reach |= grant.projects
        return frozenset(reach)

    def allows(self, capability: str, *, unit: str | None = None, project: str | None = None) -> bool:
        """어디에서 할 수 있는지를 묻는다. 두 축 중 하나라도 닿으면 된다 — 프로젝트 담당은 부서와 무관하다."""
        if capability not in self.capabilities:
            return False
        if unit is None and project is None:
            return True
        if unit is not None and unit in self.scope_for(capability):
            return True
        return project is not None and project in self.projects_for(capability)


#: 변경 기록이 조직 화면의 어느 축으로 읽히는가. 여기 없는 event_kind는 조직 축이 아니므로 기록에 오르지 않는다 —
#: 업무·요청·회의 사건이 사람의 조직 이력에 섞이면 「이 사람에게 무엇이 있었나」가 흐려진다.
#: 소속·직책 축의 kind는 아직 그 command가 없어 비어 있지만, 이름은 여기서 먼저 정해 둔다 — 나중에 command가
#: 생길 때 매핑을 다시 궁리하지 않도록.
ORGANIZATION_ACTIVITY_AXES: dict[str, str] = {
    "access.grant_added": "권한",
    "access.grant_revoked": "권한",
    "access.role_changed": "권한",
    "organization.membership_added": "소속",
    "organization.membership_changed": "소속",
    "organization.membership_ended": "소속",
    "organization.appointment_added": "직책",
    "organization.appointment_ended": "직책",
    "organization.member_added": "조직",
    "organization.member_employment_changed": "조직",
}

#: 이력을 되짚을 수 있는 축. 화면의 「이력」 버튼 하나가 이 중 하나를 부른다.
MEMBER_HISTORY_AXES = ("membership", "appointment", "grade", "job", "grant")


TASK_READ = "task.read"
#: 조직 전체 업무 조회. 읽을 수 있다는 것이지 남의 판단을 대신할 수 있다는 뜻이 아니다.
WORK_READ_ALL = "work.read.all"
TASK_SELF_MANAGE = "task.self_manage"
TASK_ASSIGN = "task.assign"
#: 프로젝트를 만들고 사람을 붙인다. 조직 단위 관리와 다른 권한이다 — 프로젝트는 부서를 가로지른다.
PROJECT_MANAGE = "project.manage"
#: 프로젝트를 읽는다. 어느 프로젝트를 읽는지는 grant의 범위가 정한다.
PROJECT_READ = "project.read"
WORK_REQUEST_READ = "work_request.read"
WORK_REQUEST_CREATE = "work_request.create"
WORK_REQUEST_DECIDE = "work_request.decide"
DAILY_REPORT_READ = "daily_report.read"
DAILY_REPORT_GENERATE = "daily_report.generate"
DAILY_REPORT_EDIT = "daily_report.edit"
DAILY_REPORT_SUBMIT = "daily_report.submit"
ACTION_READ = "action.read"
ACTION_DECIDE = "action.decide"
MEETING_READ = "meeting.read"
MEETING_READ_PRIVATE = "meeting.read.private"
MEETING_MANAGE = "meeting.manage"
MEETING_SHARE = "meeting.share"
MEETING_RECORD = "meeting.record"
