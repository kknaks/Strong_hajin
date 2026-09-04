"""Meeting-owned errors and small values shared by the Meeting application boundary."""
from __future__ import annotations


class MeetingError(Exception):
    """The Meeting command is structurally invalid or conflicts with current state."""


class MeetingAccessDenied(MeetingError):
    """The caller may not mutate this Meeting."""


class MeetingNotFound(MeetingError):
    """The Meeting does not exist or is intentionally concealed from this principal."""


class MeetingVersionConflict(MeetingError):
    """A mutable Meeting or Note identity changed before the command arrived."""


def pending_assignment_result(assignment_id: object, assignee_id: str, state: str) -> dict[str, str]:
    """Compatibility value retained while TaskAssignment consumes this helper."""
    return {"assignment_id": str(assignment_id), "assignee_id": assignee_id, "state": state}
