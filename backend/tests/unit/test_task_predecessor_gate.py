"""시작 게이트는 **전이 판정 한 자리**에 있다 (SPEC-001 §4 State / Lifecycle · WORK-003 Phase 2).

여기서 닫는 것은 「어느 전이에 거는가」 하나다 — 무엇이 막는지를 세는 일은 저장소를 아는
application 이 하고, 이 파일은 그 답을 받아 **두 문에만** 거는지를 본다.
"""
from datetime import date

import pytest

from ax_workspace.modules.work.errors import TaskChildrenUnfinished, TaskPredecessorsUnfinished
from ax_workspace.modules.work.lifecycle import (
    ChangeTaskState,
    Task,
    TaskCompletionContext,
    TaskPredecessorGate,
    TaskState,
    transition_task,
)

TODAY = date(2026, 9, 19)
NOTHING_BLOCKS_COMPLETION = TaskCompletionContext(requires_completion_review=False, unfinished_child_titles=())


def _task(state: TaskState, *, version: int = 1) -> Task:
    return Task(id="t1", title="후행 업무", state=state, version=version, start_date=None, block_reason=None)


def _change(target: TaskState, *, version: int = 1) -> ChangeTaskState:
    return ChangeTaskState(target=target, expected_version=version, reason="사유", today=TODAY)


def _blocked_by(*titles: str) -> TaskPredecessorGate:
    return TaskPredecessorGate(blocks=True, unfinished_titles=titles)


@pytest.mark.parametrize("target", [TaskState.IN_PROGRESS, TaskState.DONE])
def test_an_unfinished_predecessor_closes_both_doors_out_of_open_with_the_same_code(target) -> None:
    """**시작과 `시작 전 → 완료` 직행 둘 다** 막고 **같은 코드**를 쓴다 — 게이트에 우회로가 없다."""
    with pytest.raises(TaskPredecessorsUnfinished) as refused:
        transition_task(_task(TaskState.OPEN), _change(target), NOTHING_BLOCKS_COMPLETION, _blocked_by("표지 디자인"))
    # **막는 선행의 이름을 본문에 낸다** — 무엇이 막는지 모르면 다음 걸음을 고를 수 없다.
    assert "표지 디자인" in str(refused.value)


def test_the_refusal_names_what_the_caller_may_read_and_stays_silent_otherwise() -> None:
    """읽을 수 있는 이름만 낸다. 하나도 읽을 수 없으면 **이름 없이** 막는다 — 남의 제목이 새지 않는다."""
    with pytest.raises(TaskPredecessorsUnfinished) as hidden:
        transition_task(
            _task(TaskState.OPEN), _change(TaskState.IN_PROGRESS), NOTHING_BLOCKS_COMPLETION, _blocked_by()
        )
    assert str(hidden.value) == "끝나지 않은 선행업무가 있습니다"
    assert hidden.value.blocking == ()


@pytest.mark.parametrize(
    "state, target",
    [
        # 막는 것은 **시작**이다 — 이미 시작한 일의 완료는 선행을 보지 않는다.
        (TaskState.IN_PROGRESS, TaskState.DONE),
        # 재개는 시작이 아니다. 여기 걸면 「시작 뒤 선행이 다시 열려도 되돌리지 않는다」가 뒷문으로 깨진다.
        (TaskState.BLOCKED, TaskState.IN_PROGRESS),
        # 취소는 전진이 아니다.
        (TaskState.OPEN, TaskState.CANCELLED),
        (TaskState.IN_PROGRESS, TaskState.CANCELLED),
        # 보완 요청이 다시 여는 길도 막지 않는다.
        (TaskState.DONE, TaskState.IN_PROGRESS),
    ],
)
def test_every_other_transition_passes_even_with_an_unfinished_predecessor(state, target) -> None:
    moved = transition_task(_task(state), _change(target), NOTHING_BLOCKS_COMPLETION, _blocked_by("표지 디자인"))
    assert moved.task.state is target


def test_a_gate_that_does_not_block_changes_nothing() -> None:
    """선행이 전부 완료거나 취소면 `blocks` 가 거짓이고 시작이 열린다 (U-14)."""
    moved = transition_task(
        _task(TaskState.OPEN), _change(TaskState.IN_PROGRESS), NOTHING_BLOCKS_COMPLETION, TaskPredecessorGate()
    )
    assert moved.task.state is TaskState.IN_PROGRESS and moved.task.start_date == TODAY


def test_the_gate_defaults_to_open_so_no_caller_silently_acquires_it() -> None:
    """네 번째 인자를 주지 않는 기존 호출은 **그대로 동작한다** — 게이트는 실어 보낸 사실로만 선다."""
    moved = transition_task(_task(TaskState.OPEN), _change(TaskState.IN_PROGRESS), NOTHING_BLOCKS_COMPLETION)
    assert moved.task.state is TaskState.IN_PROGRESS


def test_the_gate_reaches_exactly_two_of_every_possible_transition() -> None:
    """**전수로 센다.** 상태 넷·목표 넷의 모든 조합을 돌려 게이트가 막는 것이 정확히 둘인지 본다.

    파라미터 목록을 손으로 적으면 나중에 상태가 하나 늘었을 때 그 줄이 조용히 빠진다 — 여기서는
    `TaskState` 를 직접 돌므로 **새 상태가 생기면 이 시험이 먼저 말한다.**
    """
    gate = _blocked_by("표지 디자인")
    blocked = []
    for state in TaskState:
        for target in TaskState:
            if state is target:
                continue
            try:
                transition_task(_task(state), _change(target), NOTHING_BLOCKS_COMPLETION, gate)
            except TaskPredecessorsUnfinished:
                blocked.append((state.value, target.value))
            except Exception:
                # 다른 규칙(허용되지 않는 전이·사유 누락 등)이 막은 것은 이 시험의 주제가 아니다.
                pass
    assert blocked == [("open", "in_progress"), ("open", "done")]


def test_the_predecessor_refusal_is_a_different_code_from_the_unfinished_children_one() -> None:
    """**하위는 완료를, 선행은 시작을 막는다.** 한 코드로 합치면 무엇을 먼저 해야 하는지가 사라진다."""
    both = TaskCompletionContext(
        requires_completion_review=False, unfinished_child_titles=("내용 작성",), children_block=True
    )
    # `시작 전` 에서는 **선행이 먼저다** — 시작도 못 하는 일의 하위를 먼저 말하면 다음 걸음이 어긋난다.
    with pytest.raises(TaskPredecessorsUnfinished):
        transition_task(_task(TaskState.OPEN), _change(TaskState.DONE), both, _blocked_by("표지 디자인"))
    # 시작한 뒤에는 선행이 빠지고 **하위 규칙이 그대로** 남는다.
    with pytest.raises(TaskChildrenUnfinished):
        transition_task(_task(TaskState.IN_PROGRESS), _change(TaskState.DONE), both, _blocked_by("표지 디자인"))
    assert not issubclass(TaskPredecessorsUnfinished, TaskChildrenUnfinished)
    assert not issubclass(TaskChildrenUnfinished, TaskPredecessorsUnfinished)
