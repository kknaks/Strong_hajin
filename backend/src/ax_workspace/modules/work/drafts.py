"""Canonical values shared by Task proposal presentation and confirmation."""
from __future__ import annotations

from typing import Any

from ax_workspace.modules.work.errors import TaskError
from ax_workspace.modules.work.task_creation import TaskAssignmentInput, TaskCreateInput
from ax_workspace.modules.work.requests import WorkRequestError
from ax_workspace.modules.work.request_commands import WorkRequestCreateInput


TASK_DRAFT_FIELDS = frozenset(TaskCreateInput.model_fields)

WORK_REQUEST_DRAFT_FIELDS = frozenset(WorkRequestCreateInput.model_fields)

def normalize_task_draft(value: Any) -> dict[str, Any]:
    """Normalize every Task creation field before comparison or execution."""
    if not isinstance(value, dict):
        raise TaskError("업무 초안은 필드별 값이어야 합니다")
    unknown = sorted(set(value) - TASK_DRAFT_FIELDS)
    if unknown:
        raise TaskError(f"업무 초안에서 바꿀 수 없는 항목입니다: {', '.join(unknown)}")
    try:
        return TaskCreateInput.model_validate(value).model_dump(mode='json')
    except ValueError as error:
        raise TaskError(str(error)) from error


def normalize_assigned_task_draft(value: Any) -> dict[str, Any]:
    """Normalize the shared Task fields plus the assignee chosen for a request."""
    if not isinstance(value, dict):
        raise TaskError("업무 초안은 필드별 값이어야 합니다")
    try:
        # Preserve the old canonical empty field for pending payload hashes.
        return {**TaskAssignmentInput.model_validate(value).model_dump(mode='json'), 'project_id': None}
    except ValueError as error:
        raise TaskError(str(error)) from error


def normalize_work_request_draft(
    value: Any,
    *,
    requester_id: str | None = None,
) -> dict[str, Any]:
    """Normalize the fields a requester may review before sending a WorkRequest."""
    if not isinstance(value, dict):
        raise WorkRequestError("업무 요청 초안은 필드별 값이어야 합니다")
    unknown = sorted(set(value) - WORK_REQUEST_DRAFT_FIELDS)
    if unknown:
        raise WorkRequestError(f"업무 요청 초안에서 바꿀 수 없는 항목입니다: {', '.join(unknown)}")
    try:
        return WorkRequestCreateInput.model_validate(value).for_requester(requester_id).model_dump(mode='json')
    except ValueError as error:
        raise WorkRequestError(str(error)) from error
