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

    def allows(self, capability: str, *, unit: str | None = None) -> bool:
        if capability not in self.capabilities:
            return False
        if unit is None:
            return True
        return unit in self.scope_for(capability)


TASK_READ = "task.read"
#: 조직 전체 업무 조회. 읽을 수 있다는 것이지 남의 판단을 대신할 수 있다는 뜻이 아니다.
WORK_READ_ALL = "work.read.all"
TASK_SELF_MANAGE = "task.self_manage"
TASK_ASSIGN = "task.assign"
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
