from datetime import UTC, datetime

import pytest

from ax_workspace.modules.work.checklist import ChecklistItem, UpdateChecklistItem, update_checklist_item
from ax_workspace.modules.work.errors import TaskError

CHANGED_AT = datetime(2026, 9, 12, 3, 30, tzinfo=UTC)


def _item(**changes: object) -> ChecklistItem:
    values: dict[str, object] = {
        "id": "item-1",
        "task_id": "task-1",
        "text": "자료 모으기",
        "done": False,
        "version": 2,
        "completed_by": None,
        "completed_at": None,
    }
    values.update(changes)
    return ChecklistItem(**values)  # type: ignore[arg-type]


def _update(
    item: ChecklistItem,
    *,
    text: str | None = None,
    done: bool | None = None,
    expected_version: int | None = None,
):
    return update_checklist_item(
        item,
        UpdateChecklistItem(
            text=text,
            done=done,
            expected_version=item.version if expected_version is None else expected_version,
            actor_id="mina",
            changed_at=CHANGED_AT,
        ),
    )


def test_checking_a_step_returns_the_changed_entity_and_one_meaningful_event() -> None:
    original = _item()

    change = _update(original, done=True)

    assert original.done is False
    assert change.item == _item(
        done=True,
        version=3,
        completed_by="mina",
        completed_at=CHANGED_AT,
    )
    assert [(event.kind, event.summary) for event in change.activities] == [
        ("task.checklist.checked", "체크리스트 완료: 자료 모으기")
    ]


def test_a_step_update_refuses_a_stale_step_version() -> None:
    with pytest.raises(TaskError, match="checklist item version is stale"):
        _update(_item(version=3), text="자료 정리하기", expected_version=2)


def test_renaming_a_step_normalizes_text_and_records_the_change() -> None:
    change = _update(_item(version=1), text="  자료   정리하기  ")

    assert change.item.text == "자료 정리하기"
    assert change.item.version == 2
    assert [(event.kind, event.summary) for event in change.activities] == [
        ("task.checklist.edited", "체크리스트 수정: 자료 모으기 → 자료 정리하기")
    ]


def test_a_step_cannot_be_renamed_to_empty_text() -> None:
    with pytest.raises(TaskError, match="checklist item text is required"):
        _update(_item(version=1), text="   ")


def test_unchecking_clears_the_old_completion_actor_and_time() -> None:
    change = _update(
        _item(
            done=True,
            version=3,
            completed_by="mina",
            completed_at=datetime(2026, 9, 11, 8, 0, tzinfo=UTC),
        ),
        done=False,
    )

    assert change.item.done is False
    assert change.item.completed_by is None
    assert change.item.completed_at is None
    assert change.item.version == 4
    assert [(event.kind, event.summary) for event in change.activities] == [
        ("task.checklist.unchecked", "체크리스트 해제: 자료 모으기")
    ]


def test_repeating_the_same_values_does_not_invent_a_version_or_event() -> None:
    completed_at = datetime(2026, 9, 11, 8, 0, tzinfo=UTC)
    original = _item(done=True, version=3, completed_by="mina", completed_at=completed_at)

    change = _update(original, text=" 자료  모으기 ", done=True)

    assert change.item == original
    assert change.activities == ()
