"""Public commands for a principal's directly owned tasks."""
from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from ax_workspace.modules.organization_access.domain import Principal, TASK_READ, TASK_SELF_MANAGE


class TaskState(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskError(Exception):
    pass


class TaskNotFound(TaskError):
    pass


class InvalidTaskTransition(TaskError):
    pass


class TaskAccessDenied(TaskError):
    pass


class TaskRepository(Protocol):
    def create_self_task(
        self,
        owner_id: str,
        title: str,
        causation_key: str | None = None,
        *,
        description: str | None = None,
        start_date: date | None = None,
        due_date: date | None = None,
    ) -> Any: ...
    def task(self, task_id: UUID, owner_id: str, *, lock: bool = False) -> Any: ...
    def tasks_for(self, owner_id: str, *, include_closed: bool = False) -> list[Any]: ...
    def touch(self, task: Any) -> None: ...


class TaskApplication:
    def __init__(self, repository: TaskRepository) -> None:
        self.repository = repository

    def create_self(
        self,
        principal: Principal,
        title: str,
        causation_key: str | None = None,
        *,
        description: str | None = None,
        start_date: date | None = None,
        due_date: date | None = None,
    ) -> dict[str, Any]:
        self._require(principal, TASK_SELF_MANAGE)
        if not title.strip():
            raise TaskError("title is required")
        validate_schedule(start_date, due_date)
        return self._view(
            self.repository.create_self_task(
                str(principal.id),
                title.strip(),
                causation_key,
                description=_clean_text(description),
                start_date=start_date,
                due_date=due_date,
            )
        )

    def update(
        self,
        task_id: UUID,
        principal: Principal,
        expected_version: int,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        """Owner-only field edits (title, description, schedule); no approval gate and no state change."""
        self._require(principal, TASK_SELF_MANAGE)
        task = self.repository.task(task_id, str(principal.id), lock=True)
        if task.version != expected_version:
            raise InvalidTaskTransition("task version is stale")
        if TaskState(task.state) is TaskState.CANCELLED:
            raise TaskError("cancelled tasks cannot be edited")
        unknown = set(changes) - {"title", "description", "start_date", "due_date"}
        if unknown:
            raise TaskError(f"unsupported task fields: {sorted(unknown)}")
        if "title" in changes:
            title = str(changes["title"] or "").strip()
            if not title:
                raise TaskError("title is required")
            task.title = title
        if "description" in changes:
            task.description = _clean_text(changes["description"])
        start_date = changes.get("start_date", task.start_date)
        due_date = changes.get("due_date", task.due_date)
        validate_schedule(start_date, due_date)
        task.start_date = start_date
        task.due_date = due_date
        task.version += 1
        self.repository.touch(task)
        return self._view(task)

    def list_for(self, principal: Principal, *, include_closed: bool = False) -> list[dict[str, Any]]:
        self._require(principal, TASK_READ)
        return [self._view(task) for task in self.repository.tasks_for(str(principal.id), include_closed=include_closed)]

    def get(self, principal: Principal, task_id: UUID) -> dict[str, Any]:
        self._require(principal, TASK_READ)
        return self._view(self.repository.task(task_id, str(principal.id)))

    def transition(
        self,
        task_id: UUID,
        principal: Principal,
        target: TaskState,
        reason: str | None = None,
        expected_version: int = 0,
    ) -> dict[str, Any]:
        self._require(principal, TASK_SELF_MANAGE)
        task = self.repository.task(task_id, str(principal.id), lock=True)
        allowed = {
            TaskState.OPEN: {TaskState.IN_PROGRESS, TaskState.CANCELLED},
            TaskState.IN_PROGRESS: {TaskState.BLOCKED, TaskState.DONE, TaskState.CANCELLED},
            TaskState.BLOCKED: {TaskState.IN_PROGRESS, TaskState.CANCELLED},
            # A mistaken completion can be reopened; cancellation stays terminal.
            TaskState.DONE: {TaskState.IN_PROGRESS},
        }
        if task.version != expected_version:
            raise InvalidTaskTransition("task version is stale")
        if target not in allowed.get(TaskState(task.state), set()):
            raise InvalidTaskTransition("task state transition is not allowed")
        if target is TaskState.BLOCKED and not (reason or "").strip():
            raise InvalidTaskTransition("block reason is required")
        task.state = target
        task.block_reason = reason.strip() if target is TaskState.BLOCKED else None
        task.version += 1
        self.repository.touch(task)
        return self._view(task)

    @staticmethod
    def _require(principal: Principal, capability: str) -> None:
        if capability not in principal.capabilities:
            raise TaskAccessDenied(f"{capability} capability is required")

    @staticmethod
    def _view(task: Any) -> dict[str, Any]:
        return {
            "task_id": str(task.id),
            "title": task.title,
            "state": task.state,
            "version": task.version,
            "block_reason": task.block_reason,
            "description": getattr(task, "description", None),
            "start_date": _iso(getattr(task, "start_date", None)),
            "due_date": _iso(getattr(task, "due_date", None)),
            "created_at": _iso(getattr(task, "created_at", None)),
            "updated_at": _iso(getattr(task, "updated_at", None)),
        }


def validate_schedule(start_date: date | None, due_date: date | None) -> None:
    if start_date is not None and due_date is not None and start_date > due_date:
        raise TaskError("start date cannot be later than the due date")


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None
