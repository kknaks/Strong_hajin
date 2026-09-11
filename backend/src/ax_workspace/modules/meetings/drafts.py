"""Canonical values shared by Meeting proposal presentation and confirmation."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ax_workspace.modules.meetings.domain import MeetingError


MEETING_DRAFT_FIELDS = {
    "organization_id",
    "title",
    "description",
    "starts_at",
    "ends_at",
    "visibility",
    "attendee_ids",
    "reference_task_ids",
    "include_initial_note",
    "initial_note_body",
    "initial_note_source_status",
    "initial_note_source_evidence",
}

MEETING_SOURCE_STATUSES = {"not_requested", "current_turn", "resolved", "not_found"}


def _instant(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise MeetingError(f"{label}은(는) ISO 날짜·시간이어야 합니다") from error
    if parsed.tzinfo is None:
        raise MeetingError(f"{label}에는 시간대가 필요합니다")
    return parsed.astimezone(UTC)


def normalize_meeting_draft(value: Any) -> dict[str, Any]:
    """Normalize every editable value and the server-frozen note provenance."""
    if not isinstance(value, dict):
        raise MeetingError("회의 초안은 필드별 값이어야 합니다")
    unknown = sorted(set(value) - MEETING_DRAFT_FIELDS)
    if unknown:
        raise MeetingError(f"회의 초안에서 바꿀 수 없는 항목입니다: {', '.join(unknown)}")
    organization_id = str(value.get("organization_id") or "").strip()
    if not organization_id:
        raise MeetingError("회의 조직을 선택해 주세요")
    title = str(value.get("title") or "").strip()
    if not title:
        raise MeetingError("회의 제목은 비울 수 없습니다")
    description = str(value.get("description") or "").strip() or None
    starts_at = _instant(value.get("starts_at"), "시작 시각")
    ends_at = _instant(value.get("ends_at"), "종료 시각")
    if starts_at >= ends_at:
        raise MeetingError("meeting end must be after start")
    visibility = str(value.get("visibility") or "private")
    if visibility not in {"public", "private"}:
        raise MeetingError("meeting visibility must be public or private")
    attendees = list(dict.fromkeys(str(item).strip() for item in value.get("attendee_ids") or [] if str(item).strip()))
    references = list(dict.fromkeys(str(item).strip() for item in value.get("reference_task_ids") or [] if str(item).strip()))
    include_note = bool(value.get("include_initial_note", False))
    note_body = str(value.get("initial_note_body") or "").strip() or None
    source_status = str(value.get("initial_note_source_status") or ("current_turn" if include_note else "not_requested"))
    source_evidence = value.get("initial_note_source_evidence") or []
    if source_status not in MEETING_SOURCE_STATUSES:
        raise MeetingError("회의록 초안 source 상태가 올바르지 않습니다")
    if not isinstance(source_evidence, list) or any(not isinstance(item, dict) for item in source_evidence):
        raise MeetingError("회의록 초안 source는 항목 목록이어야 합니다")
    if include_note and note_body is None:
        raise MeetingError("포함할 회의록 초안 내용을 입력해 주세요")
    if not include_note:
        note_body = None
        source_status = "not_requested"
        source_evidence = []
    return {
        "organization_id": organization_id,
        "title": title,
        "description": description,
        "starts_at": starts_at.isoformat(),
        "ends_at": ends_at.isoformat(),
        "visibility": visibility,
        "attendee_ids": attendees,
        "reference_task_ids": references,
        "include_initial_note": include_note,
        "initial_note_body": note_body,
        "initial_note_source_status": source_status,
        "initial_note_source_evidence": [dict(item) for item in source_evidence],
    }
