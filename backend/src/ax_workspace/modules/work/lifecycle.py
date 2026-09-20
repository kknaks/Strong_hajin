"""Pure lifecycle transitions for a Task.

Persistence owns locking and applies the returned state and event. This module
owns whether a requested state change is valid and which Task it produces.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum

from ax_workspace.modules.work.errors import (
    InvalidTaskTransition,
    TaskChildrenUnfinished,
    TaskPredecessorsUnfinished,
)


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
    """Facts from outside the Task boundary that can prevent completion.

    **막는가와 무엇이 막는가를 가른다.** `children_block` 이 막을지를 정하고,
    `unfinished_child_titles` 는 **부르는 사람에게 말해도 되는 이름**만 담는다 — 읽을 수 없는 하위가
    막고 있으면 그 목록은 비고, 거절은 이름 없이 나간다.
    """

    requires_completion_review: bool
    unfinished_child_titles: tuple[str, ...]
    children_block: bool = False


@dataclass(frozen=True, slots=True)
class TaskPredecessorGate:
    """**끝나지 않은 선행이 있는가**, 그리고 **부르는 사람에게 말해도 되는 이름**은 무엇인가.

    `TaskCompletionContext` 와 같은 모양을 일부러 쓴다 — 막는가와 무엇이 막는가를 가르는 자리가
    둘이면 한쪽만 고쳤을 때 조용히 갈린다. 계산은 저장소를 아는 application 이 미리 하고
    (WORK-003 § Internal Interface Contract), 여기서는 **어느 전이에 거는가**만 안다.

    `blocks` 는 **취소가 아닌 미완 선행**이 하나라도 있다는 뜻이다 — 취소된 선행은 막지 않는다
    (SPEC-001 U-14). `unfinished_titles` 는 읽을 수 있는 것만 담고, 하나도 읽을 수 없으면 비어서
    거절이 이름 없이 나간다.
    """

    blocks: bool = False
    unfinished_titles: tuple[str, ...] = ()


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
    # `open → done` — **SPEC-001 §4 State 와 SPEC-003 §4 State 가 둘 다 그린 전이다.** 시작하지 않고
    # 끝나는 일이 실제로 있다(누가 이미 해 둔 일, 필요 없어진 일이 아니라 끝난 일). 막아 두면 사람이
    # 「시작」을 형식으로 누르고 바로 완료하게 되고, 그러면 `started_at` 이 거짓이 된다.
    # 요청 Task 는 여전히 거부된다 — 아래 `requires_completion_review` 가드가 먼저 걸린다.
    # 하위 완결 검사도 그대로 걸린다.
    TaskState.OPEN: frozenset({TaskState.IN_PROGRESS, TaskState.DONE, TaskState.CANCELLED}),
    TaskState.IN_PROGRESS: frozenset({TaskState.BLOCKED, TaskState.DONE, TaskState.CANCELLED}),
    TaskState.BLOCKED: frozenset({TaskState.IN_PROGRESS, TaskState.CANCELLED}),
    TaskState.COMPLETION_SUBMITTED: frozenset({TaskState.CANCELLED}),
    TaskState.DONE: frozenset({TaskState.IN_PROGRESS}),
}


def transition_task(
    task: Task,
    command: ChangeTaskState,
    completion: TaskCompletionContext,
    predecessors: TaskPredecessorGate = TaskPredecessorGate(),
) -> TaskTransition:
    """Return the next immutable Task and the domain event caused by the command.

    **선행 게이트가 여기 하나에 있다** (SPEC-001 §4 State / Lifecycle · WORK-003 Phase 2).
    전이 판정이 모이는 자리이므로, 이 함수를 지나지 않고 상태를 바꾸는 길이 없는 한 게이트에
    우회로가 생기지 않는다.

    거는 곳은 **`시작 전` 에서 나가는 두 문**뿐이다 — 시작(`open → in_progress`)과 상세 상단의
    직행(`open → done`). 그 둘에 **같은 코드**를 쓴다: 사람에게는 같은 사실이다(선행이 안 끝났다).

    **그 밖에는 걸지 않는다.** `in_progress → done` 은 막는 것이 시작이라서, `blocked → in_progress`
    (재개)는 이미 시작한 일이라서, 취소와 보완 요청은 전진이 아니라서 걸지 않는다. 특히 재개에 걸면
    「시작한 뒤 선행이 다시 열려도 후행을 되돌리지 않는다」(U-14)가 뒷문으로 깨진다.

    **미완 하위 게이트보다 먼저 본다.** `시작 전` 업무가 둘 다에 걸려 있으면 사람이 먼저 해야 하는
    것은 선행이다 — 시작도 못 하는 일의 하위를 먼저 말하면 다음 걸음이 어긋난다.
    """

    if predecessors.blocks and task.state is TaskState.OPEN and command.target in {
        TaskState.IN_PROGRESS,
        TaskState.DONE,
    }:
        names = ", ".join(predecessors.unfinished_titles[:3])
        # **409 다** — 명령 자체는 말이 되는데 지금 그 업무의 상태가 받지 않는다
        # (SPEC-001 Case Matrix `WORK_PREDECESSORS_UNFINISHED`). 미완 하위와 **다른 코드**다.
        raise TaskPredecessorsUnfinished(
            f"끝나지 않은 선행업무가 있습니다: {names}" if names else "끝나지 않은 선행업무가 있습니다",
            tuple({"title": title} for title in predecessors.unfinished_titles),
        )
    if command.target is TaskState.DONE and completion.requires_completion_review:
        raise InvalidTaskTransition("이 업무는 요청자의 확인이 필요합니다. 완료 보고로 제출하세요")
    if command.target is TaskState.DONE and (completion.children_block or completion.unfinished_child_titles):
        names = ", ".join(completion.unfinished_child_titles[:3])
        # **409 다** — 명령 자체는 말이 되는데 지금 그 업무의 상태가 받지 않는다
        # (SPEC-003 Case Matrix `WORK_CHILDREN_UNFINISHED`). 입력이 틀린 422 와 다른 사실이다.
        raise TaskChildrenUnfinished(
            f"끝나지 않은 하위 업무가 있습니다: {names}" if names else "끝나지 않은 하위 업무가 있습니다"
        )
    if task.version != command.expected_version:
        raise InvalidTaskTransition("task version is stale")
    if command.target not in _ALLOWED_TRANSITIONS.get(task.state, frozenset()):
        raise InvalidTaskTransition("task state transition is not allowed")
    cleaned_reason = " ".join((command.reason or "").split())
    if command.target is TaskState.BLOCKED and not cleaned_reason:
        raise InvalidTaskTransition("block reason is required")
    if command.target is TaskState.CANCELLED and not cleaned_reason:
        # **왜 접었는지가 남아야 한다** (SPEC-003 §4 Validation). 사유 없이 사라진 업무는 남은 사람에게
        # 「왜 없어졌는지」가 아무 데도 없는 일이 된다. 공백만 있는 문자열도 사유가 아니다.
        raise InvalidTaskTransition("취소에는 사유가 필요합니다")
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
            # **사유는 이력으로 간다.** 차단은 `block_reason` 열에도 남아 화면에 걸리지만, 취소는
            # 그 열을 쓰지 않는다(`block_reason` 은 「왜 막혔나」다). 그래서 취소 사유가 진행 기록에
            # 실리지 않으면 아무 데도 남지 않는다 — 여기서 함께 싣는다.
            reason=changed.block_reason if command.target is TaskState.BLOCKED else (cleaned_reason or None),
        ),
    )
