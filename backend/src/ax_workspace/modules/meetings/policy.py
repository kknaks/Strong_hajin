"""Pure Meeting visibility, control, and lifecycle projection policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ax_workspace.modules.meetings.domain import (
    INFO_EDITABLE_STATUSES,
    TRACK_AI,
    TRACK_FINAL,
    TRACK_MEMO,
    MeetingError,
    MeetingStateConflict,
    MeetingStatus,
    is_auto_cancel_released,
    is_auto_cancellable,
    parse_status,
)


PAST_STATUSES = frozenset({MeetingStatus.DONE, MeetingStatus.FAILED, MeetingStatus.CANCELLED})
NOTE_EDITABLE_STATUSES = frozenset({MeetingStatus.DONE, MeetingStatus.FAILED, MeetingStatus.CANCELLED})

# 안건 게이트는 **벌마다 다르고, 더하는 것과 고치고 지우는 것이 또 다르다** (SPEC-004 v0.5.1 §4.1-6).
# 여는 사람은 어느 칸이든 회의를 만든 사람이다.
#
#   | 벌      | 안건 추가              | 안건 제목 고치기 · 안건 삭제 |
#   |---------|------------------------|------------------------------|
#   | `memo` | 예정 · **진행 중** · 취소 | 예정 · 취소                  |
#   | `ai`    | **없다**               | **없다** — 언제나 거짓        |
#   | `final` | 종료 · 실패            | 종료 · 실패 (`[수정]` 안에서) |
#
# 「진행 중」에 사람 벌 안건을 **더할 수는 있지만 고칠 수는 없다** — 이미 줄이 매달린 안건이 흔들리면
# 매달린 메모가 갈 곳을 잃는다. **「종료」·「실패」에서 원본 두 벌은 읽기 전용이다** (D53): 원본은
# 최종 벌을 대조하는 근거이고 고칠 수 있으면 근거가 되지 못한다. **정리가 도는 동안에는 어느 벌도
# 열리지 않는다** — 그래서 「정리 중」이 어느 집합에도 없다.
MEMO_AGENDA_EDITABLE_STATUSES = frozenset({MeetingStatus.SCHEDULED, MeetingStatus.CANCELLED})
MEMO_AGENDA_ADDABLE_STATUSES = MEMO_AGENDA_EDITABLE_STATUSES | {MeetingStatus.IN_PROGRESS}
FINAL_AGENDA_EDITABLE_STATUSES = frozenset({MeetingStatus.DONE, MeetingStatus.FAILED})
FINAL_AGENDA_ADDABLE_STATUSES = FINAL_AGENDA_EDITABLE_STATUSES
#: **AI 벌은 사람이 언제도 손대지 않는다** — 그것은 AI 의 기록이고 배치가 매 회차 전량 교체한다 (§4.0-1).
AI_AGENDA_EDITABLE_STATUSES: frozenset[MeetingStatus] = frozenset()
AI_AGENDA_ADDABLE_STATUSES: frozenset[MeetingStatus] = frozenset()


def normalize_memo_text(value: object) -> str:
    """Return one durable memo sentence, independent of an HTTP payload."""
    text = str(value or "").strip()
    if not text:
        raise MeetingError("a memo line needs text")
    if len(text) > 2000:
        raise MeetingError("a memo line must be at most 2000 characters")
    return text


@dataclass(frozen=True, slots=True)
class FinalLineRow:
    """저장이 싣는 최종 줄 하나 — **줄 id 를 함께 든다** (SPEC-004 v0.5.1 §8-9).

    id 가 없으면 서버는 어느 줄이 그대로이고 어느 줄이 고쳐졌는지 알 수 없고, 그러면 **손대지 않은 줄의
    계보까지 첫 저장에 사라진다.** 계보는 「내가 적은 안건이 어디로 갔나」에 답하는 유일한 길이라(§4.1-3)
    줄 id 를 싣는 비용이 그것을 잃는 비용보다 싸다.
    """

    line_id: str | None
    text: str


def normalize_final_line_rows(values: object) -> tuple[FinalLineRow, ...]:
    """최종 벌 저장의 줄 목록 — 줄마다 `{line_id, text}` 다. **빈 줄은 저장할 때 버린다** (§4.2-8).

    `line_id` 가 없거나 `null` 인 줄은 **새 줄**이고 계보가 없다 (§8-9). 「모르는 id」 판정은 그 안건의
    줄 목록을 아는 원장이 한다 — 여기는 모양만 본다.
    """
    if not isinstance(values, (list, tuple)):
        raise MeetingError("agenda lines must be a list of {line_id, text} rows")
    rows: list[FinalLineRow] = []
    for value in values:
        if isinstance(value, dict):
            raw_id = value.get("line_id")
            unknown = set(value) - {"line_id", "text"}
            if unknown:
                raise MeetingError(f"unsupported agenda line fields: {sorted(unknown)}")
            text = str(value.get("text") or "").strip()
        else:
            # 글자 하나로 오면 계보를 이을 수 없다 — 「한 덩어리 저장」이 계보를 통째로 지우던 모양이라
            # 받지 않는다 (§8-9 · 검수 F-3). 새 줄은 `{"text": ...}` 로 온다.
            raise MeetingError("each agenda line must carry its line_id — send {line_id, text}")
        if not text:
            continue
        if len(text) > 2000:
            raise MeetingError("a note line must be at most 2000 characters")
        line_id = None if raw_id is None else str(raw_id).strip() or None
        rows.append(FinalLineRow(line_id=line_id, text=text))
    return tuple(rows)


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
class AgendaTrackGates:
    """**벌별 판정 셋** (SPEC-004 v0.5.1 §3.3 · §4.1-6).

    불리언 하나를 버렸다: 하나로는 「종료에서 최종 벌은 열리고 사람 벌은 닫힌다」를 낼 수 없다.
    벌이 셋이므로 판정도 셋이고, `ai` 는 언제나 거짓이지만 **키는 낸다** — 화면이 세 벌을 같은 모양으로 묻는다.
    """

    memo: bool
    ai: bool
    final: bool

    def __getitem__(self, track: str) -> bool:
        try:
            return bool(getattr(self, track))
        except AttributeError as error:
            raise MeetingError(f"unknown meeting note track: {track}") from error

    def as_dict(self) -> dict[str, bool]:
        """응답이 싣는 모양 — `{"memo": bool, "ai": bool, "final": bool}`."""
        return {TRACK_MEMO: self.memo, TRACK_AI: self.ai, TRACK_FINAL: self.final}

    def any(self) -> bool:
        return self.memo or self.ai or self.final


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
    #: 안건을 **고치고 지우는** 판정 — 벌별 셋이다 (§4.1-6 「판정 필드」).
    can_edit_agendas: AgendaTrackGates
    #: 안건을 **더하는** 판정 — 편집보다 한 자리 넓다(사람 벌은 「진행 중」에도 세운다, D45).
    can_add_agenda: AgendaTrackGates
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
        can_edit_agendas=_agenda_gates(
            status,
            owner=owner,
            memo=MEMO_AGENDA_EDITABLE_STATUSES,
            ai=AI_AGENDA_EDITABLE_STATUSES,
            final=FINAL_AGENDA_EDITABLE_STATUSES,
        ),
        can_add_agenda=_agenda_gates(
            status,
            owner=owner,
            memo=MEMO_AGENDA_ADDABLE_STATUSES,
            ai=AI_AGENDA_ADDABLE_STATUSES,
            final=FINAL_AGENDA_ADDABLE_STATUSES,
        ),
        can_write_memo=owner and status is MeetingStatus.IN_PROGRESS,
    )


def _agenda_gates(
    status: MeetingStatus,
    *,
    owner: bool,
    memo: frozenset[MeetingStatus],
    ai: frozenset[MeetingStatus],
    final: frozenset[MeetingStatus],
) -> AgendaTrackGates:
    """벌 셋을 한 상태로 재단한다. 여는 사람은 어느 칸이든 회의를 만든 사람이다 (§3.3 · §4.1-6)."""
    return AgendaTrackGates(
        memo=owner and status in memo,
        ai=owner and status in ai,
        final=owner and status in final,
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
