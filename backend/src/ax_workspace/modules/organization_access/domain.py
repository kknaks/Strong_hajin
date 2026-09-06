from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated person, as the Organization & Access ledger sees them.

    `id` is a plain member identity, not a login kind: which credential proved who this is says nothing about what they
    may do, and the same person keeps the same identity whichever provider they signed in with.
    """

    id: str
    display_name: str
    organization_scope: frozenset[str]
    capabilities: frozenset[str]


TASK_READ = "task.read"
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
