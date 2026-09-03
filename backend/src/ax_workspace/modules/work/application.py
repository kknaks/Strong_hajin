"""Public commands for a principal's directly owned tasks."""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from ax_workspace.modules.organization_access.domain import Principal


class TaskState(StrEnum):
    ACTIVE = "active"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskError(Exception): pass
class TaskNotFound(TaskError): pass
class InvalidTaskTransition(TaskError): pass


class TaskRepository(Protocol):
    def create_self_task(self, owner_id: str, title: str) -> Any: ...
    def task(self, task_id: UUID, owner_id: str) -> Any: ...
    def tasks_for(self, owner_id: str) -> list[Any]: ...
    def touch(self, task: Any) -> None: ...


class TaskApplication:
    def __init__(self, repository: TaskRepository) -> None: self.repository = repository

    def create_self(self, principal: Principal, title: str) -> dict[str, Any]:
        if not title.strip(): raise TaskError("title is required")
        return self._view(self.repository.create_self_task(str(principal.id), title.strip()))

    def list_for(self, principal: Principal) -> list[dict[str, Any]]:
        return [self._view(task) for task in self.repository.tasks_for(str(principal.id))]

    def transition(self, task_id: UUID, principal: Principal, target: TaskState, reason: str | None = None) -> dict[str, Any]:
        task = self.repository.task(task_id, str(principal.id))
        allowed = {
            TaskState.ACTIVE: {TaskState.IN_PROGRESS, TaskState.CANCELLED},
            TaskState.IN_PROGRESS: {TaskState.BLOCKED, TaskState.COMPLETED, TaskState.CANCELLED},
            TaskState.BLOCKED: {TaskState.IN_PROGRESS, TaskState.CANCELLED},
        }
        if target not in allowed.get(TaskState(task.state), set()): raise InvalidTaskTransition("task state transition is not allowed")
        if target is TaskState.BLOCKED and not (reason or "").strip(): raise InvalidTaskTransition("block reason is required")
        task.state, task.block_reason, task.version = target, reason.strip() if target is TaskState.BLOCKED else None, task.version + 1
        self.repository.touch(task)
        return self._view(task)

    @staticmethod
    def _view(task: Any) -> dict[str, Any]:
        return {"task_id": str(task.id), "title": task.title, "state": task.state, "version": task.version, "block_reason": task.block_reason}
