"""Pure validation and comparison helpers for action command payloads."""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from ax_workspace.modules.actions.domain import ActionError
from ax_workspace.modules.ax_execution.actions import action_payload_hash
from ax_workspace.modules.work.errors import TaskError
from ax_workspace.modules.work.requests import (
    REVISABLE_FIELDS,
    WorkRequestError,
    normalize_proposed_changes,
)


def normalize_task_progress_batch(payload: dict[str, Any]) -> dict[str, Any]:
    operations = payload.get("operations")
    if not isinstance(operations, list) or not 1 <= len(operations) <= 20:
        raise TaskError("a task progress batch must contain between 1 and 20 operations")
    normalized: list[dict[str, Any]] = []
    seen_tasks: set[str] = set()
    for raw in operations:
        if not isinstance(raw, dict) or raw.get("kind") not in {"checklist.update", "progress.note"}:
            raise TaskError("unsupported task progress operation")
        task_id = str(UUID(str(raw.get("task_id"))))
        if task_id in seen_tasks:
            raise TaskError("a task progress batch may change each task only once")
        seen_tasks.add(task_id)
        expected_version = int(raw.get("expected_version"))
        if expected_version < 1:
            raise TaskError("task progress expected_version must be positive")
        if raw["kind"] == "progress.note":
            summary = " ".join(str(raw.get("summary") or "").split())
            if not summary:
                raise TaskError("a progress note must say what changed")
            operation = {
                "kind": "progress.note",
                "task_id": task_id,
                "expected_version": expected_version,
                "summary": summary[:300],
            }
        else:
            item_id = str(UUID(str(raw.get("item_id"))))
            text = " ".join(str(raw["text"]).split()) if raw.get("text") is not None else None
            done = raw.get("done") if isinstance(raw.get("done"), bool) else None
            if text is None and done is None:
                raise TaskError("a checklist update must change text or done")
            operation = {
                "kind": "checklist.update",
                "task_id": task_id,
                "item_id": item_id,
                "expected_version": expected_version,
                "text": text,
                "done": done,
            }
        operation["effect_id"] = action_payload_hash(operation)
        normalized.append(operation)
    return {"operations": normalized}


def validate_task_progress_batch_edit(base: dict[str, Any], final: dict[str, Any]) -> None:
    """A card may edit an effect's value or exclude it, never smuggle in another target or stale guard."""
    original = {str(operation["task_id"]): operation for operation in base.get("operations") or []}
    for operation in final.get("operations") or []:
        before = original.get(str(operation["task_id"]))
        if before is None:
            raise ActionError("원안에 없던 업무는 이 카드에서 추가할 수 없습니다")
        immutable = ("kind", "task_id", "expected_version")
        if operation["kind"] == "checklist.update":
            immutable = (*immutable, "item_id")
        if any(operation.get(field) != before.get(field) for field in immutable):
            raise ActionError("업무 대상이나 기준 버전은 이 카드에서 바꿀 수 없습니다")


def attachment_draft_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ActionError("attachment_draft_ids must be a list")
    try:
        return list(dict.fromkeys(str(UUID(str(item))) for item in value))
    except (TypeError, ValueError) as error:
        raise ActionError("attachment_draft_ids must contain UUID values") from error


def payload_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {
        key: {"before": before.get(key), "after": after.get(key)}
        for key in sorted(set(before) | set(after))
        if before.get(key) != after.get(key)
    }


def proposed_changes(value: Any) -> dict[str, Any]:
    """An adjustment's optional structured ask. The work module owns the allow-list, so every writer agrees with it."""
    try:
        return normalize_proposed_changes(value)
    except WorkRequestError as error:
        raise ActionError(str(error)) from error


def revision_changes(value: Any) -> dict[str, Any]:
    """What a revision may change. A field it does not own is refused, never dropped from a successful answer."""
    if not value:
        return {}
    if not isinstance(value, dict):
        raise ActionError("수정안은 필드별로 적어 주세요")
    unknown = sorted(set(value) - set(REVISABLE_FIELDS))
    if unknown:
        raise ActionError(f"수정안에서 바꿀 수 없는 항목입니다: {', '.join(unknown)}")
    changes: dict[str, Any] = {}
    if "title" in value:
        title = str(value["title"] or "").strip()
        if not title:
            raise ActionError("요청할 업무 제목은 비울 수 없습니다")
        changes["title"] = title
    if "description" in value:
        changes["description"] = str(value["description"] or "").strip()
    if value.get("clear_due_date"):
        changes["clear_due_date"] = True
    elif "due_date" in value:
        due_date = str(value["due_date"] or "").strip()
        if not due_date:
            raise ActionError("기한은 clear_due_date로만 지웁니다")
        date.fromisoformat(due_date)
        changes["due_date"] = due_date
    return changes


def suggested_changes(conditions: Any) -> dict[str, Any]:
    """The proposal a negotiate decision carries; a reason-only adjustment has none."""
    if not isinstance(conditions, dict):
        return {}
    return proposed_changes(conditions.get("changes"))
