"""Meeting assignment values retained across request and acceptance gates."""


def pending_assignment_result(assignment_id: object, assignee_id: str, state: str) -> dict[str, str]:
    return {"assignment_id": str(assignment_id), "assignee_id": assignee_id, "state": state}
