"""Public commands for a principal's directly owned tasks."""
from __future__ import annotations

from datetime import UTC, date, datetime
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
    def checklist_for(self, task_id: UUID) -> list[Any]: ...
    def add_checklist_item(self, task_id: UUID, text: str) -> Any: ...
    def checklist_item(self, task_id: UUID, item_id: UUID, *, lock: bool = False) -> Any: ...
    def remove_checklist_item(self, item: Any) -> None: ...
    def record_activity(self, task: Any, actor_id: str, event_kind: str, summary: str, *, before_ref: str | None = None, reason: str | None = None) -> None: ...


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
        self.repository.record_activity(
            task, str(principal.id), "task.updated", f"업무 내용 수정: {task.title} ({', '.join(sorted(changes))})",
            before_ref=f"task:{task.id}@{expected_version}",
        )
        return self._view(task)

    def list_for(self, principal: Principal, *, include_closed: bool = False) -> list[dict[str, Any]]:
        self._require(principal, TASK_READ)
        return [self._view(task) for task in self.repository.tasks_for(str(principal.id), include_closed=include_closed)]

    def get(self, principal: Principal, task_id: UUID) -> dict[str, Any]:
        self._require(principal, TASK_READ)
        return self._with_checklist(self.repository.task(task_id, str(principal.id)))

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
        previous_state = task.state
        task.state = target
        task.block_reason = reason.strip() if target is TaskState.BLOCKED else None
        task.version += 1
        self.repository.touch(task)
        self.repository.record_activity(
            task,
            str(principal.id),
            "task.state_changed",
            f"업무 상태 {previous_state} → {target.value}: {task.title}",
            before_ref=f"task:{task.id}@{expected_version}:{previous_state}",
            reason=task.block_reason,
        )
        return self._view(task)

    @staticmethod
    def _require(principal: Principal, capability: str) -> None:
        if capability not in principal.capabilities:
            raise TaskAccessDenied(f"{capability} capability is required")

    # ---- checklist: the steps inside one Task ----

    def add_checklist_item(self, principal: Principal, task_id: UUID, text: str) -> dict[str, Any]:
        """Only the person who holds the Task may add a step, and the text must say something."""
        self._require(principal, TASK_SELF_MANAGE)
        task = self.repository.task(task_id, str(principal.id))
        cleaned = " ".join(text.split())
        if not cleaned:
            raise TaskError("checklist item text is required")
        item = self.repository.add_checklist_item(task.id, cleaned[:300])
        self.repository.record_activity(task, str(principal.id), "task.checklist.added", f"체크리스트 추가: {cleaned[:80]}")
        return _checklist_view(item)

    def update_checklist_item(self, principal: Principal, task_id: UUID, item_id: UUID, *, text: str | None = None, done: bool | None = None) -> dict[str, Any]:
        """Checking a step records who did it and when; unchecking clears those facts rather than keeping a stale actor."""
        self._require(principal, TASK_SELF_MANAGE)
        task = self.repository.task(task_id, str(principal.id))
        item = self.repository.checklist_item(task.id, item_id, lock=True)
        if item is None:
            raise TaskNotFound("checklist item was not found")
        if text is not None:
            cleaned = " ".join(text.split())
            if not cleaned:
                raise TaskError("checklist item text is required")
            item.text = cleaned[:300]
        if done is not None and done != item.done:
            item.done = done
            item.completed_by = str(principal.id) if done else None
            item.completed_at = datetime.now(UTC) if done else None
            self.repository.record_activity(
                task, str(principal.id), "task.checklist.checked" if done else "task.checklist.unchecked",
                f"체크리스트 {'완료' if done else '해제'}: {item.text[:80]}",
            )
        item.updated_at = datetime.now(UTC)
        return _checklist_view(item)

    def remove_checklist_item(self, principal: Principal, task_id: UUID, item_id: UUID) -> None:
        self._require(principal, TASK_SELF_MANAGE)
        task = self.repository.task(task_id, str(principal.id))
        item = self.repository.checklist_item(task.id, item_id, lock=True)
        if item is None:
            raise TaskNotFound("checklist item was not found")
        self.repository.record_activity(task, str(principal.id), "task.checklist.removed", f"체크리스트 삭제: {item.text[:80]}")
        self.repository.remove_checklist_item(item)

    def _with_checklist(self, task: Any) -> dict[str, Any]:
        items = [_checklist_view(item) for item in self.repository.checklist_for(task.id)]
        return {
            **self._view(task),
            "checklist": items,
            "checklist_progress": {"done": sum(1 for item in items if item["done"]), "total": len(items)},
        }

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
            "organization_unit_id": getattr(task, "organization_unit_id", None),
            "origin_kind": getattr(task, "origin_kind", "direct"),
            "visibility": getattr(task, "visibility", "scope_default"),
            "assignment": _assignment_view(getattr(task, "assignments", None)),
            "lineage": {
                "request_thread_id": _str(getattr(task, "request_thread_id", None)),
                "source_work_request_id": _str(getattr(task, "source_work_request_id", None)),
                "source_decision_item_id": _str(getattr(task, "source_decision_item_id", None)),
                "source_submission_id": _str(getattr(task, "source_submission_id", None)),
                "source_review_decision_id": _str(getattr(task, "source_review_decision_id", None)),
                "source_action_item_id": _str(getattr(task, "source_action_item_id", None)),
                "source_task_id": _str(getattr(task, "source_task_id", None)),
            },
        }


def _assignment_view(assignments: Any) -> dict[str, Any] | None:
    if not assignments:
        return None
    current = assignments[-1]
    return {
        "assignment_id": str(current.id),
        "kind": current.assignment_kind,
        "status": current.status,
        "assigned_by": current.assigned_by,
        "accepted_at": _iso(current.accepted_at),
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


def _str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _checklist_view(item: Any) -> dict[str, Any]:
    return {
        "item_id": str(item.id),
        "text": item.text,
        "position": int(item.position),
        "done": bool(item.done),
        "completed_by": item.completed_by,
        "completed_at": _iso(getattr(item, "completed_at", None)),
    }
