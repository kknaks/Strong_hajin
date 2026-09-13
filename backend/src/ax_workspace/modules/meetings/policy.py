"""Pure Meeting visibility, control, and lifecycle projection policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ax_workspace.modules.meetings.domain import (
    INFO_EDITABLE_STATUSES,
    MeetingError,
    MeetingStateConflict,
    MeetingStatus,
    is_auto_cancel_released,
    is_auto_cancellable,
    parse_status,
)


PAST_STATUSES = frozenset({MeetingStatus.DONE, MeetingStatus.FAILED, MeetingStatus.CANCELLED})
NOTE_EDITABLE_STATUSES = frozenset({MeetingStatus.DONE, MeetingStatus.FAILED, MeetingStatus.CANCELLED})
AGENDA_EDITABLE_STATUSES = frozenset(
    {MeetingStatus.SCHEDULED, MeetingStatus.DONE, MeetingStatus.FAILED, MeetingStatus.CANCELLED}
)
AGENDA_ADDABLE_STATUSES = AGENDA_EDITABLE_STATUSES | {MeetingStatus.IN_PROGRESS}


def normalize_memo_text(value: object) -> str:
    """Return one durable memo sentence, independent of an HTTP payload."""
    text = str(value or "").strip()
    if not text:
        raise MeetingError("a memo line needs text")
    if len(text) > 2000:
        raise MeetingError("a memo line must be at most 2000 characters")
    return text


def normalize_note_lines(values: object) -> tuple[str, ...]:
    """Normalize the complete final-note replacement while preserving sentence order."""
    if not isinstance(values, (list, tuple)):
        raise MeetingError("agenda lines must be a list of sentences")
    lines: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        if len(text) > 2000:
            raise MeetingError("a note line must be at most 2000 characters")
        lines.append(text)
    return tuple(lines)


def ensure_todo_actionable(
    *,
    provisional: bool,
    linked_work_request_id: object | None,
    action: Literal["promote", "delete"],
) -> None:
    """Guard follow-up actions using durable candidate facts, without loading a repository record."""
    if provisional:
        raise MeetingStateConflict("todo_provisional")
    if linked_work_request_id is None:
        return
    if action == "promote":
        raise MeetingStateConflict("this follow-up candidate has already been requested")
    raise MeetingStateConflict("a requested follow-up candidate is not deleted")


def meeting_attendees(requested_member_ids: list[str], *, owner_id: str) -> tuple[str, ...]:
    """Return the stable attendee set; a Meeting always includes its owner."""
    return _distinct_members([*requested_member_ids, owner_id])


def new_share_targets(
    requested_member_ids: list[str], *, existing_member_ids: tuple[str, ...] | set[str]
) -> tuple[str, ...]:
    """Skip duplicate shares and people who already see the Meeting through attendance."""
    existing = set(existing_member_ids)
    return tuple(member_id for member_id in _distinct_members(requested_member_ids) if member_id not in existing)


def ensure_share_revocable(member_id: str, *, attendee_ids: tuple[str, ...] | set[str]) -> None:
    """Attendance is edited as Meeting information and cannot be revoked as a share."""
    if member_id in attendee_ids:
        raise MeetingStateConflict("attendance is not revoked here; edit the meeting information instead")


def _distinct_members(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


@dataclass(frozen=True, slots=True)
class MeetingViewContext:
    status: MeetingStatus | str
    owner_id: str
    organization_id: str
    attendee_ids: frozenset[str]
    shared_member_ids: frozenset[str]
    ends_at: datetime


@dataclass(frozen=True, slots=True)
class MeetingActorContext:
    member_id: str
    can_read: bool
    can_read_private: bool
    organization_scope: frozenset[str]


@dataclass(frozen=True, slots=True)
class MeetingView:
    detail_readable: bool
    calendar_detail_readable: bool
    relation: Literal["attendee", "shared"]
    is_attendee: bool
    is_owner: bool
    past: bool
    can_edit_info: bool
    can_edit_note: bool
    can_edit_agendas: bool
    can_add_agenda: bool
    can_write_memo: bool


def project_meeting_view(
    meeting: MeetingViewContext,
    actor: MeetingActorContext,
    *,
    now: datetime,
) -> MeetingView:
    """Project one Meeting for one actor without consulting transport or persistence."""
    status = parse_status(meeting.status)
    attendee = actor.member_id == meeting.owner_id or actor.member_id in meeting.attendee_ids
    explicitly_shared = actor.member_id in meeting.shared_member_ids
    detail_readable = actor.can_read and (attendee or explicitly_shared)
    calendar_detail_readable = detail_readable or (
        actor.can_read
        and actor.can_read_private
        and meeting.organization_id in actor.organization_scope
    )
    relation: Literal["attendee", "shared"] = "attendee" if attendee else "shared"
    owner = actor.member_id == meeting.owner_id
    return MeetingView(
        detail_readable=detail_readable,
        calendar_detail_readable=calendar_detail_readable,
        relation=relation,
        is_attendee=attendee,
        is_owner=owner,
        past=is_meeting_past(status, ends_at=meeting.ends_at, relation=relation, now=now),
        can_edit_info=attendee and status in INFO_EDITABLE_STATUSES,
        can_edit_note=owner and status in NOTE_EDITABLE_STATUSES,
        can_edit_agendas=owner and status in AGENDA_EDITABLE_STATUSES,
        can_add_agenda=owner and status in AGENDA_ADDABLE_STATUSES,
        can_write_memo=owner and status is MeetingStatus.IN_PROGRESS,
    )


def is_meeting_past(
    status: MeetingStatus | str,
    *,
    ends_at: datetime,
    relation: Literal["attendee", "shared"],
    now: datetime,
) -> bool:
    """Shared meetings and meetings whose own lifecycle/time elapsed belong to the past board."""
    return relation == "shared" or parse_status(status) in PAST_STATUSES or ends_at <= now


def auto_settled_status(
    status: MeetingStatus | str,
    *,
    ends_at: datetime,
    created_at: datetime,
    has_record: bool,
    now: datetime,
) -> MeetingStatus:
    """Return the status an observation should persist for an unattended meeting."""
    current = parse_status(status)
    if is_auto_cancellable(current, ends_at, now, has_record=has_record, created_at=created_at):
        return MeetingStatus.CANCELLED
    if is_auto_cancel_released(current, has_record=has_record):
        return MeetingStatus.SCHEDULED
    return current


def needs_auto_settlement(status: MeetingStatus | str) -> bool:
    """Whether observing record existence can change this Meeting's status."""
    return parse_status(status) in {MeetingStatus.SCHEDULED, MeetingStatus.CANCELLED}
