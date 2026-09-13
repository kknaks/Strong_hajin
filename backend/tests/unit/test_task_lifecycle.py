from datetime import date

import pytest

from ax_workspace.modules.work.lifecycle import (
    ChangeTaskState,
    InvalidTaskTransition,
    Task,
    TaskCompletionContext,
    TaskState,
    TaskStateChanged,
    transition_task,
)

TODAY = date(2026, 9, 12)


def _task(**changes: object) -> Task:
    values: dict[str, object] = {
        "id": "task-1",
        "title": "고객 자료 정리",
        "state": TaskState.OPEN,
        "version": 3,
        "start_date": None,
        "block_reason": None,
    }
    values.update(changes)
    return Task(**values)  # type: ignore[arg-type]


def _transition(
    task: Task,
    target: TaskState,
    *,
    expected_version: int | None = None,
    reason: str | None = None,
    requires_review: bool = False,
    unfinished_children: tuple[str, ...] = (),
):
    return transition_task(
        task,
        ChangeTaskState(
            target=target,
            expected_version=task.version if expected_version is None else expected_version,
            reason=reason,
            today=TODAY,
        ),
        TaskCompletionContext(
            requires_completion_review=requires_review,
            unfinished_child_titles=unfinished_children,
        ),
    )


def test_starting_open_work_returns_a_new_task_and_its_domain_event() -> None:
    original = _task()

    transition = _transition(original, TaskState.IN_PROGRESS)

    assert original.state is TaskState.OPEN
    assert transition.task == _task(state=TaskState.IN_PROGRESS, version=4, start_date=TODAY)
    assert transition.event == TaskStateChanged(
        summary="업무 상태 open → in_progress: 고객 자료 정리",
        before_ref="task:task-1@3:open",
        reason=None,
    )


def test_starting_work_never_replaces_an_existing_planned_date() -> None:
    for planned in (date(2020, 8, 31), date(2099, 8, 31)):
        transition = _transition(_task(start_date=planned), TaskState.IN_PROGRESS)

        assert transition.task.start_date == planned


def test_a_transition_refuses_the_task_version_the_caller_did_not_see() -> None:
    with pytest.raises(InvalidTaskTransition, match="task version is stale"):
        _transition(_task(version=4), TaskState.IN_PROGRESS, expected_version=3)


def test_blocking_running_work_keeps_the_reason_as_a_task_fact_and_event_reason() -> None:
    transition = _transition(
        _task(state=TaskState.IN_PROGRESS, version=7, start_date=date(2026, 9, 10)),
        TaskState.BLOCKED,
        reason="  고객 자료 대기  ",
    )

    assert transition.task == _task(
        state=TaskState.BLOCKED,
        version=8,
        start_date=date(2026, 9, 10),
        block_reason="고객 자료 대기",
    )
    assert transition.event.reason == "고객 자료 대기"


def test_blocking_requires_a_reason() -> None:
    with pytest.raises(InvalidTaskTransition, match="block reason is required"):
        _transition(_task(state=TaskState.IN_PROGRESS, version=7), TaskState.BLOCKED, reason="   ")


def test_requested_work_must_be_submitted_for_review_instead_of_marked_done() -> None:
    with pytest.raises(InvalidTaskTransition, match="요청자의 확인이 필요합니다"):
        _transition(
            _task(state=TaskState.IN_PROGRESS, version=5),
            TaskState.DONE,
            requires_review=True,
        )


def test_work_cannot_finish_while_a_child_is_open() -> None:
    with pytest.raises(InvalidTaskTransition, match="끝나지 않은 하위 업무가 있습니다: 자료 수집, 초안 작성"):
        _transition(
            _task(state=TaskState.IN_PROGRESS, version=5),
            TaskState.DONE,
            unfinished_children=("자료 수집", "초안 작성"),
        )


def test_running_work_finishes_when_no_review_or_child_is_outstanding() -> None:
    transition = _transition(
        _task(state=TaskState.IN_PROGRESS, version=5, start_date=date(2026, 9, 10)),
        TaskState.DONE,
    )

    assert transition.task.state is TaskState.DONE
    assert transition.task.version == 6
    assert transition.task.start_date == date(2026, 9, 10)
    assert transition.task.block_reason is None


@pytest.mark.parametrize(
    ("state", "target"),
    [
        (TaskState.OPEN, TaskState.CANCELLED),
        (TaskState.IN_PROGRESS, TaskState.CANCELLED),
        (TaskState.BLOCKED, TaskState.IN_PROGRESS),
        (TaskState.BLOCKED, TaskState.CANCELLED),
        (TaskState.COMPLETION_SUBMITTED, TaskState.CANCELLED),
        (TaskState.DONE, TaskState.IN_PROGRESS),
    ],
)
def test_the_remaining_lifecycle_moves_are_explicitly_allowed(state: TaskState, target: TaskState) -> None:
    transition = _transition(
        _task(state=state, version=2, start_date=date(2026, 9, 10)),
        target,
    )

    assert transition.task.state is target
    assert transition.task.version == 3
    assert transition.task.block_reason is None
