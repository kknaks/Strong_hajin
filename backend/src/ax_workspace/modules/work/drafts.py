"""Canonical values shared by Task proposal presentation and confirmation."""
from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from ax_workspace.modules.work.application import TaskError, clean_checklist, validate_schedule
from ax_workspace.modules.work.requests import WorkRequestError


TASK_DRAFT_FIELDS = {
    "title",
    "description",
    "start_date",
    "due_date",
    "checklist",
    "reference_task_ids",
    "parent_task_id",
    "project_id",
}

WORK_REQUEST_DRAFT_FIELDS = {
    "title",
    "description",
    "assignee_id",
    "due_date",
    "cc_member_ids",
    "checklist",
    "reference_task_ids",
}


def normalize_task_draft(value: Any) -> dict[str, Any]:
    """Normalize every Task creation field before comparison or execution."""
    if not isinstance(value, dict):
        raise TaskError("업무 초안은 필드별 값이어야 합니다")
    unknown = sorted(set(value) - TASK_DRAFT_FIELDS)
    if unknown:
        raise TaskError(f"업무 초안에서 바꿀 수 없는 항목입니다: {', '.join(unknown)}")
    title = str(value.get("title") or "").strip()
    if not title:
        raise TaskError("업무 제목은 비울 수 없습니다")
    description = str(value.get("description") or "").strip() or None
    start = date.fromisoformat(str(value["start_date"])) if value.get("start_date") else None
    due = date.fromisoformat(str(value["due_date"])) if value.get("due_date") else None
    validate_schedule(start, due)
    references = list(dict.fromkeys(str(UUID(str(item))) for item in value.get("reference_task_ids") or []))
    parent = str(UUID(str(value["parent_task_id"]))) if value.get("parent_task_id") else None
    project = str(UUID(str(value["project_id"]))) if value.get("project_id") else None
    checklist = clean_checklist(value.get("checklist"))
    return {
        "title": title,
        "description": description,
        "start_date": start.isoformat() if start else None,
        "due_date": due.isoformat() if due else None,
        "checklist": checklist,
        "reference_task_ids": references,
        "parent_task_id": parent,
        "project_id": project,
    }


def normalize_assigned_task_draft(value: Any) -> dict[str, Any]:
    """Normalize the shared Task fields plus the assignee chosen for a request."""
    if not isinstance(value, dict):
        raise TaskError("업무 초안은 필드별 값이어야 합니다")
    assignee_id = str(value.get("assignee_id") or "").strip()
    if not assignee_id:
        raise TaskError("담당자를 선택해 주세요")
    task = normalize_task_draft({key: item for key, item in value.items() if key != "assignee_id"})
    return {**task, "assignee_id": assignee_id}


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
    title = str(value.get("title") or "").strip()
    if not title:
        raise WorkRequestError("업무 요청 제목은 비울 수 없습니다")
    assignee_id = str(value.get("assignee_id") or "").strip()
    if not assignee_id:
        raise WorkRequestError("요청 대상을 선택해 주세요")
    try:
        due = date.fromisoformat(str(value["due_date"])) if value.get("due_date") else None
        references = list(dict.fromkeys(str(UUID(str(item))) for item in value.get("reference_task_ids") or []))
    except (TypeError, ValueError) as error:
        raise WorkRequestError("업무 요청의 날짜 또는 참고 업무 형식이 올바르지 않습니다") from error
    excluded_cc = {assignee_id}
    if requester_id and str(requester_id).strip():
        excluded_cc.add(str(requester_id).strip())
    cc_member_ids = list(dict.fromkeys(
        member
        for item in value.get("cc_member_ids") or []
        if (member := str(item).strip()) and member not in excluded_cc
    ))
    return {
        "title": title,
        "description": str(value.get("description") or "").strip() or None,
        "assignee_id": assignee_id,
        "due_date": due.isoformat() if due else None,
        "cc_member_ids": cc_member_ids,
        "checklist": clean_checklist(value.get("checklist")),
        "reference_task_ids": references,
    }
