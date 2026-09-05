"""Manager-assigned Tasks: a TaskAssignment stays pending until the assignee accepts it (ERD TASK_ASSIGNMENT + acceptance ActionItem)."""
from __future__ import annotations

from datetime import date
from typing import Any, Protocol
from uuid import UUID

from ax_workspace.modules.organization_access.domain import Principal, TASK_ASSIGN, TASK_READ, TASK_SELF_MANAGE
from ax_workspace.modules.work.application import InvalidTaskTransition, TaskAccessDenied, TaskApplication, TaskError, TaskNotFound, validate_schedule, _clean_text, _iso


class TaskAssignmentRepository(Protocol):
    def create_assigned_task(
        self, assigner_id: str, assignee_id: str, title: str, *, description: str | None = None, start_date: date | None = None, due_date: date | None = None, causation_key: str | None = None
    ) -> tuple[Any, Any]: ...
    def assignment(self, assignment_id: UUID, *, lock: bool = False) -> Any: ...
    def task_for(self, assignment: Any) -> Any: ...
    def pending_for(self, assignee_id: str) -> list[tuple[Any, Any]]: ...
    def assigned_by(self, assigner_id: str) -> list[tuple[Any, Any]]: ...
    def decide(self, assignment: Any, actor_id: str, decision: str, *, reason: str | None = None) -> Any: ...
    def task_by_id(self, task_id: UUID, *, lock: bool = False) -> Any: ...
    def active_assignment_for(self, task_id: UUID, *, lock: bool = False) -> Any: ...
    def reassign(self, task: Any, current: Any, assigner_id: str, assignee_id: str, reason: str | None) -> Any: ...


class TaskAssigneeDirectory(Protocol):
    def task_assignment_candidates(self, principal: Principal) -> list[dict[str, str]]: ...
    def is_task_assignee(self, principal: Principal, assignee_id: str) -> bool: ...


class TaskAssignmentApplication:
    def __init__(self, repository: TaskAssignmentRepository, directory: TaskAssigneeDirectory) -> None:
        self._repository = repository
        self._directory = directory

    def candidates(self, principal: Principal) -> list[dict[str, str]]:
        self._require(principal, TASK_ASSIGN)
        return self._directory.task_assignment_candidates(principal)

    def assign(
        self,
        principal: Principal,
        title: str,
        assignee_id: str,
        *,
        description: str | None = None,
        start_date: date | None = None,
        due_date: date | None = None,
        causation_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a Task for someone else. It enters their My Work only after they accept the assignment."""
        self._require(principal, TASK_ASSIGN)
        if not title.strip():
            raise TaskError("title is required")
        if assignee_id == str(principal.id):
            raise TaskError("use a self-owned task instead of assigning yourself")
        if not self._directory.is_task_assignee(principal, assignee_id):
            raise TaskError("assignee is not within your assignment scope")
        validate_schedule(start_date, due_date)
        task, assignment = self._repository.create_assigned_task(
            str(principal.id), assignee_id, title.strip(),
            description=_clean_text(description), start_date=start_date, due_date=due_date, causation_key=causation_key,
        )
        return self._view(assignment, task)

    def reassign(self, principal: Principal, task_id: UUID, expected_version: int, assignee_id: str, reason: str | None = None) -> dict[str, Any]:
        """Put someone else on work that is already underway.

        Changing who holds the work is its own command, never a field on the Task edit form: it moves a relationship,
        and the person taking it on still gets to accept or decline.
        """
        self._require(principal, TASK_ASSIGN)
        # Take the Task row first: the version this command answers must not be read before someone else's move.
        task = self._repository.task_by_id(task_id, lock=True)
        if task is None:
            raise TaskNotFound("task was not found")
        current = self._repository.active_assignment_for(task_id, lock=True)
        if current is None:
            raise TaskError("this task has nobody to move it from")
        if task.version != expected_version:
            raise InvalidTaskTransition("task version is stale")
        if assignee_id == current.assignee_id:
            raise TaskError("that person already holds this task")
        # Taking the work on yourself is not assigning to yourself: the candidate list is about who you may put on
        # someone else's work, and one may always take it back.
        if assignee_id != str(principal.id) and not self._directory.is_task_assignee(principal, assignee_id):
            raise TaskError("assignee is not within your assignment scope")
        appended = self._repository.reassign(task, current, str(principal.id), assignee_id, (reason or "").strip() or None)
        return self._view(appended, task)

    def inbox(self, principal: Principal) -> list[dict[str, Any]]:
        """Assignments waiting for my acceptance (ERD work_inbox: 배정 수락)."""
        self._require(principal, TASK_READ)
        return [self._view(assignment, task) for assignment, task in self._repository.pending_for(str(principal.id))]

    def sent(self, principal: Principal) -> list[dict[str, Any]]:
        self._require(principal, TASK_ASSIGN)
        return [self._view(assignment, task) for assignment, task in self._repository.assigned_by(str(principal.id))]

    def accept(self, principal: Principal, assignment_id: UUID) -> dict[str, Any]:
        self._require(principal, TASK_SELF_MANAGE)
        assignment = self._pending_target(principal, assignment_id)
        self._repository.decide(assignment, str(principal.id), "accept")
        return self._view(assignment, self._repository.task_for(assignment))

    def decline(self, principal: Principal, assignment_id: UUID, reason: str) -> dict[str, Any]:
        self._require(principal, TASK_SELF_MANAGE)
        if not reason.strip():
            raise TaskError("decline reason is required")
        assignment = self._pending_target(principal, assignment_id)
        self._repository.decide(assignment, str(principal.id), "reject", reason=reason.strip())
        return self._view(assignment, self._repository.task_for(assignment))

    def _pending_target(self, principal: Principal, assignment_id: UUID) -> Any:
        assignment = self._repository.assignment(assignment_id, lock=True)
        if assignment is None or assignment.assignee_id != str(principal.id):
            raise TaskNotFound("task assignment was not found")
        if assignment.status != "pending":
            raise TaskError("task assignment is not awaiting acceptance")
        return assignment

    @staticmethod
    def _require(principal: Principal, capability: str) -> None:
        if capability not in principal.capabilities:
            raise TaskAccessDenied(f"{capability} capability is required")

    @staticmethod
    def _view(assignment: Any, task: Any) -> dict[str, Any]:
        return {
            "assignment_id": str(assignment.id),
            "assignment_kind": assignment.assignment_kind,
            "status": assignment.status,
            "assignee_id": assignment.assignee_id,
            "assigned_by": assignment.assigned_by,
            "decline_reason": assignment.decline_reason,
            "created_at": _iso(assignment.created_at),
            "accepted_at": _iso(assignment.accepted_at),
            "declined_at": _iso(assignment.declined_at),
            "task": TaskApplication._view(task),
        }
