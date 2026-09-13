"""Pure update decisions for the checklist inside a Task."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ax_workspace.modules.work.errors import TaskError


@dataclass(frozen=True, slots=True)
class ChecklistItem:
    id: str
    task_id: str
    text: str
    done: bool
    version: int
    completed_by: str | None
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class UpdateChecklistItem:
    text: str | None
    done: bool | None
    expected_version: int | None
    actor_id: str
    changed_at: datetime


@dataclass(frozen=True, slots=True)
class ChecklistActivity:
    kind: str
    summary: str


@dataclass(frozen=True, slots=True)
class ChecklistChange:
    item: ChecklistItem
    activities: tuple[ChecklistActivity, ...]


def update_checklist_item(item: ChecklistItem, command: UpdateChecklistItem) -> ChecklistChange:
    if command.expected_version is not None and item.version != command.expected_version:
        raise TaskError("checklist item version is stale")
    cleaned_text = item.text if command.text is None else " ".join(command.text.split())[:300]
    if command.text is not None and not cleaned_text:
        raise TaskError("checklist item text is required")
    text_changed = cleaned_text != item.text
    done_changed = command.done is not None and command.done != item.done
    changes = int(text_changed) + int(done_changed)
    activities: list[ChecklistActivity] = []
    if text_changed:
        activities.append(
            ChecklistActivity(
                "task.checklist.edited",
                f"체크리스트 수정: {item.text[:40]} → {cleaned_text[:40]}",
            )
        )
    if done_changed:
        activities.append(
            ChecklistActivity(
                "task.checklist.checked" if command.done else "task.checklist.unchecked",
                f"체크리스트 {'완료' if command.done else '해제'}: {cleaned_text[:80]}",
            )
        )
    return ChecklistChange(
        item=ChecklistItem(
            id=item.id,
            task_id=item.task_id,
            text=cleaned_text,
            done=command.done if done_changed else item.done,
            version=item.version + changes,
            completed_by=(command.actor_id if command.done else None) if done_changed else item.completed_by,
            completed_at=(command.changed_at if command.done else None) if done_changed else item.completed_at,
        ),
        activities=tuple(activities),
    )
