"""Public commands for a principal's directly owned tasks."""
from __future__ import annotations

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
    def create_self_task(self, owner_id: str, title: str, causation_key: str | None = None) -> Any: ...
    def task(self, task_id: UUID, owner_id: str, *, lock: bool = False) -> Any: ...
    def tasks_for(self, owner_id: str) -> list[Any]: ...
    def touch(self, task: Any) -> None: ...


class TaskApplication:
    def __init__(self, repository: TaskRepository) -> None:
        self.repository = repository

    def create_self(
        self,
        principal: Principal,
        title: str,
        causation_key: str | None = None,
    ) -> dict[str, Any]:
        self._require(principal, TASK_SELF_MANAGE)
        if not title.strip():
            raise TaskError("title is required")
        return self._view(
            self.repository.create_self_task(str(principal.id), title.strip(), causation_key)
        )

    def list_for(self, principal: Principal) -> list[dict[str, Any]]:
        self._require(principal, TASK_READ)
        return [self._view(task) for task in self.repository.tasks_for(str(principal.id))]

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
        return {"task_id": str(task.id), "title": task.title, "state": task.state, "version": task.version, "block_reason": task.block_reason}
