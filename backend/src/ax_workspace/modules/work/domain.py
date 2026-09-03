"""Work feature values shared by the workflow runtime and inbound views."""

from typing import Protocol


class ActiveAssignment(Protocol):
    id: object
    title: str
    state: str
    run_id: object


def my_work_item(assignment: ActiveAssignment) -> dict[str, str]:
    return {
        "assignment_id": str(assignment.id),
        "title": assignment.title,
        "state": assignment.state,
        "run_id": str(assignment.run_id),
    }
