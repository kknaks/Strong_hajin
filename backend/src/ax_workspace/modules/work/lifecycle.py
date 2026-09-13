"""Pure lifecycle transitions for a Task.

Persistence owns locking and applies the returned state and event. This module
owns whether a requested state change is valid and which Task it produces.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum

from ax_workspace.modules.work.errors import InvalidTaskTransition


class TaskState(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETION_SUBMITTED = "completion_submitted"
    DONE = "done"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class Task:
    """The Task-owned state needed to enforce its lifecycle invariants."""

    id: str
    title: str
    state: TaskState
    version: int
    start_date: date | None
    block_reason: str | None


@dataclass(frozen=True, slots=True)
class TaskCompletionContext:
    """Facts from outside the Task boundary that can prevent completion."""

    requires_completion_review: bool
    unfinished_child_titles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ChangeTaskState:
    target: TaskState
    expected_version: int
    reason: str | None
    today: date


@dataclass(frozen=True, slots=True)
class TaskStateChanged:
    summary: str
    before_ref: str
    reason: str | None


@dataclass(frozen=True, slots=True)
class TaskTransition:
    task: Task
    event: TaskStateChanged


_ALLOWED_TRANSITIONS = {
    TaskState.OPEN: frozenset({TaskState.IN_PROGRESS, TaskState.CANCELLED}),
    TaskState.IN_PROGRESS: frozenset({TaskState.BLOCKED, TaskState.DONE, TaskState.CANCELLED}),
    TaskState.BLOCKED: frozenset({TaskState.IN_PROGRESS, TaskState.CANCELLED}),
    TaskState.COMPLETION_SUBMITTED: frozenset({TaskState.CANCELLED}),
    TaskState.DONE: frozenset({TaskState.IN_PROGRESS}),
}


def transition_task(
    task: Task,
    command: ChangeTaskState,
    completion: TaskCompletionContext,
) -> TaskTransition:
    """Return the next immutable Task and the domain event caused by the command."""

    if command.target is TaskState.DONE and completion.requires_completion_review:
        raise InvalidTaskTransition("이 업무는 요청자의 확인이 필요합니다. 완료 보고로 제출하세요")
    if command.target is TaskState.DONE and completion.unfinished_child_titles:
        names = ", ".join(completion.unfinished_child_titles[:3])
        raise InvalidTaskTransition(f"끝나지 않은 하위 업무가 있습니다: {names}")
    if task.version != command.expected_version:
        raise InvalidTaskTransition("task version is stale")
    if command.target not in _ALLOWED_TRANSITIONS.get(task.state, frozenset()):
        raise InvalidTaskTransition("task state transition is not allowed")
    cleaned_reason = (command.reason or "").strip()
    if command.target is TaskState.BLOCKED and not cleaned_reason:
        raise InvalidTaskTransition("block reason is required")
    changed = replace(
        task,
        state=command.target,
        version=task.version + 1,
        start_date=(
            command.today
            if task.state is TaskState.OPEN
            and command.target is TaskState.IN_PROGRESS
            and task.start_date is None
            else task.start_date
        ),
        block_reason=cleaned_reason if command.target is TaskState.BLOCKED else None,
    )
    return TaskTransition(
        task=changed,
        event=TaskStateChanged(
            summary=f"업무 상태 {task.state.value} → {changed.state.value}: {task.title}",
            before_ref=f"task:{task.id}@{task.version}:{task.state.value}",
            reason=changed.block_reason,
        ),
    )
