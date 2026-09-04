"""Public commands for a principal's directly owned tasks."""
from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from ax_workspace.modules.organization_access.domain import Principal, TASK_READ, TASK_SELF_MANAGE, WORK_REQUEST_READ


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
        source_action_item_id: UUID | None = None,
    ) -> Any: ...
    def task(self, task_id: UUID, owner_id: str, *, lock: bool = False) -> Any: ...
    def task_by_id(self, task_id: UUID) -> Any | None: ...
    def tasks_for(self, owner_id: str, *, include_closed: bool = False) -> list[Any]: ...
    def touch(self, task: Any) -> None: ...
    def checklist_for(self, task_id: UUID) -> list[Any]: ...
    def checklist_progress_for(self, task_ids: list[UUID]) -> dict[UUID, tuple[int, int]]: ...
    def origin_facts(self, tasks: list[Any]) -> dict[UUID, dict[str, Any]]: ...
    def member_display_name(self, member_id: str) -> str | None: ...
    def add_checklist_item(self, task_id: UUID, text: str) -> Any: ...
    def checklist_item(self, task_id: UUID, item_id: UUID, *, lock: bool = False) -> Any: ...
    def remove_checklist_item(self, item: Any) -> None: ...
    def record_activity(self, task: Any, actor_id: str, event_kind: str, summary: str, *, before_ref: str | None = None, reason: str | None = None) -> None: ...


class TaskApplication:
    def __init__(self, repository: TaskRepository, requests: Any | None = None) -> None:
        self.repository = repository
        # Reading a Task's origin may need the request behind it, always through that module's own authorized list.
        self._requests = requests

    def create_self(
        self,
        principal: Principal,
        title: str,
        causation_key: str | None = None,
        *,
        description: str | None = None,
        start_date: date | None = None,
        due_date: date | None = None,
        source_action_item_id: UUID | None = None,
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
                source_action_item_id=source_action_item_id,
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
        """The list carries the checklist count, not its items: enough for a progress cue, cheap enough for a table."""
        self._require(principal, TASK_READ)
        tasks = self.repository.tasks_for(str(principal.id), include_closed=include_closed)
        progress = self.repository.checklist_progress_for([task.id for task in tasks])
        origins = self._origin_projection(principal, tasks)
        views = []
        for task in tasks:
            done, total = progress.get(task.id, (0, 0))
            views.append({**self._view(task), "checklist_progress": {"done": done, "total": total}, "origin": origins.get(task.id)})
        return views

    def get(self, principal: Principal, task_id: UUID) -> dict[str, Any]:
        """The holder's workspace, or a read-only view for someone related through the Task's source.

        A requester is not the assignee: they may see the work their request produced, but reading it is not holding
        it. The read-only view carries no checklist and grants no command; the mutating routes keep their own guard.
        """
        self._require(principal, TASK_READ)
        try:
            return {**self._with_checklist(self.repository.task(task_id, str(principal.id)), principal), "access": "owner"}
        except TaskNotFound:
            return {**self._related_view(principal, task_id), "access": "read_only"}

    def _related_view(self, principal: Principal, task_id: UUID) -> dict[str, Any]:
        task = self.repository.task_by_id(task_id)
        if task is None or task.source_work_request_id is None:
            raise TaskNotFound("task was not found")
        # The only relationship that opens someone else's Task is one the request module itself grants.
        if task.source_work_request_id not in self._readable_request_ids(principal, {task.source_work_request_id}):
            raise TaskNotFound("task was not found")
        return {**self._view(task), "origin": self._origin_projection(principal, [task]).get(task.id)}

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

    def _origin_projection(self, principal: Principal, tasks: list[Any]) -> dict[UUID, dict[str, Any]]:
        """Where each Task came from, and which role that actor actually played.

        Requester, assigner, assignee and administrator are different things. A Task created by accepting a request
        names the requester even though the acceptance created the row; a directly assigned Task names the assigner,
        never the assignee; a self-created Task names its creator. An administrator capability is not provenance.

        The source resource is included only when this principal may read it. When it may not, the actor label still
        stands — that is the Task's own fact — but the source is withheld rather than partially disclosed.
        """
        facts = self.repository.origin_facts(tasks)
        readable_requests = self._readable_request_ids(principal, {fact["source_work_request_id"] for fact in facts.values()})
        projections: dict[UUID, dict[str, Any]] = {}
        for task in tasks:
            fact = facts.get(task.id, {})
            request_id = fact.get("source_work_request_id")
            if request_id is not None:
                kind, actor_role, actor_id = "work_request", "요청자", fact.get("request_requester_id")
                source = (
                    {"type": "work_request", "id": str(request_id), "title": fact.get("request_title")}
                    if request_id in readable_requests
                    else None
                )
            elif fact.get("assignment_kind") == "direct":
                kind, actor_role, actor_id, source = "direct_assignment", "배정자", fact.get("assigned_by"), None
            else:
                kind, actor_role, actor_id, source = "self_created", "생성자", task.owner_id, None
            projections[task.id] = {
                "kind": kind,
                "actor_role": actor_role,
                "actor": self._actor(actor_id),
                "source": source,
            }
        return projections

    def _readable_request_ids(self, principal: Principal, request_ids: set[Any]) -> set[Any]:
        """Which of these requests this principal may actually read.

        Both halves are required. Holding the Task, or even being its assignee, is not permission to read the request
        behind it: that resource belongs to the request module, so its own capability must be held as well as a
        relationship to the request.
        """
        wanted = {request_id for request_id in request_ids if request_id is not None}
        if not wanted or self._requests is None or WORK_REQUEST_READ not in principal.capabilities:
            return set()
        member_id = str(principal.id)
        return {request.id for request in self._requests.list_for(member_id) if request.id in wanted}

    def _actor(self, member_id: Any) -> dict[str, str] | None:
        if not member_id:
            return None
        return {"member_id": str(member_id), "display_name": self.repository.member_display_name(str(member_id)) or str(member_id)}

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

    def _with_checklist(self, task: Any, principal: Principal) -> dict[str, Any]:
        items = [_checklist_view(item) for item in self.repository.checklist_for(task.id)]
        return {
            **self._view(task),
            "checklist": items,
            "checklist_progress": {"done": sum(1 for item in items if item["done"]), "total": len(items)},
            "origin": self._origin_projection(principal, [task]).get(task.id),
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
