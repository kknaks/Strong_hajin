"""Public Meeting commands and authorized projections.

This module knows no transport or ORM. The repository owns persistence and append-only
audit facts; HTTP, MCP, Calendar, Materials, and AX call these commands rather than tables.
"""
from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from ax_workspace.modules.meetings.domain import (
    MeetingStaleWrite,
    LINE_TRACKS,
    MAX_AGENDAS_PER_MEETING,
    MeetingAccessDenied,
    MeetingError,
    MeetingNotFound,
    MeetingStateConflict,
    MeetingStatus,
    AI_AGENDA_SOURCE,
    ensure_agenda_capacity,
    ensure_agenda_source,
    ensure_transition,
    normalize_agenda_order,
    normalize_agenda_title,
    normalize_external_attendees,
    normalize_optional_text,
    parse_status,
    validate_meeting_schedule,
)
from ax_workspace.modules.meetings.finalize import describe_day
from ax_workspace.modules.meetings.policy import (
    MeetingActorContext,
    MeetingView,
    MeetingViewContext,
    ensure_share_revocable,
    ensure_todo_actionable,
    is_meeting_past,
    needs_auto_settlement,
    normalize_memo_text,
    normalize_note_lines,
    auto_settled_status,
    meeting_attendees,
    new_share_targets,
    project_meeting_view,
)
from ax_workspace.modules.meetings.retranscribe import Recording
from ax_workspace.modules.meetings.rooms import Attendee, RoomReservation, headcount
from ax_workspace.modules.meetings.stream_service import MeetingAdmission
from ax_workspace.modules.organization_access.domain import Principal


PAST_PAGE_SIZE = 20
QUICK_START_LENGTH = timedelta(hours=1)
# 바로 시작한 회의가 갖고 서는 기본 안건. 사람이 이름을 고쳐 쓸 자리이지 AI 가 세운 자리가 아니다 (D32).
QUICK_START_AGENDA_TITLE = "안건 1"

MEETING_READ = "meeting.read"
MEETING_READ_PRIVATE = "meeting.read.private"
MEETING_MANAGE = "meeting.manage"
MEETING_SHARE = "meeting.share"


class MeetingRepository(Protocol):
    def create(
        self,
        *,
        organization_id: str,
        owner_id: str,
        title: str | None,
        purpose: str | None,
        starts_at: datetime,
        ends_at: datetime,
        location: str | None,
        status: str,
        attendee_ids: list[str],
        external_attendees: list[str],
        carried_from_meeting_id: UUID | None,
        started_at: datetime | None = None,
    ) -> Any: ...
    def replace_attendees(self, meeting: Any, attendee_ids: list[str], actor_id: str) -> None: ...
    def primary_organization(self, member_id: str) -> str | None: ...
    def meetings_in_organizations(self, organization_ids: frozenset[str]) -> list[Any]: ...
    def meetings_visible_to(self, organization_ids: frozenset[str], member_id: str) -> list[Any]: ...
    def meeting(self, meeting_id: UUID, *, lock: bool = False) -> Any | None: ...
    def attendee_ids(self, meeting: Any) -> set[str]: ...
    def is_shared_with(self, meeting: Any, member_id: str) -> bool: ...
    def shared_member_ids(self, meeting: Any) -> list[str]: ...
    def member_display_name(self, member_id: str) -> str | None: ...
    def is_active_member_in_organization(self, member_id: str, organization_id: str) -> bool: ...
    def is_active_member(self, member_id: str) -> bool: ...
    def add_share(self, meeting: Any, member_id: str, actor_id: str) -> None: ...
    def revoke_share(self, meeting: Any, member_id: str) -> bool: ...
    def touch(self, meeting: Any) -> None: ...
    def append_audit(self, meeting: Any, actor_id: str, event_kind: str, summary: str, *, before_ref: str | None = None) -> None: ...
    def agendas(self, meeting: Any) -> list[Any]: ...
    def agenda(self, meeting: Any, agenda_id: UUID, *, lock: bool = False) -> Any | None: ...
    def agenda_count(self, meeting: Any) -> int: ...
    def next_agenda_order(self, meeting: Any) -> int: ...
    def create_agenda(self, meeting: Any, *, title: str, source: str, order_index: int) -> Any: ...
    def touch_agenda(self, agenda: Any) -> None: ...
    def delete_agenda(self, agenda: Any) -> None: ...
    def lines(self, meeting: Any) -> list[Any]: ...
    def line_count(self, meeting: Any) -> int: ...
    def append_line(self, agenda: Any, *, track: str, text: str, author_id: str | None, evidence: list[dict[str, Any]] | None = None, at_ms: int | None = None) -> Any: ...
    def memo_lines(self, meeting: Any) -> list[Any]: ...
    def replace_track(self, meeting: Any, track: str) -> None: ...
    def record_ai_session(self, meeting_id: UUID, *, provider_session_ref: str, persona_id: str) -> None: ...
    def ai_session(self, meeting_id: UUID) -> Any | None: ...
    def succeeded_batch_cursor(self, meeting_id: UUID) -> int: ...
    def next_batch_seq(self, meeting_id: UUID) -> int: ...
    def latest_succeeded_batch_seq(self, meeting_id: UUID) -> int: ...
    def record_batch_run(self, meeting_id: UUID, *, seq: int, status: str, trigger_cause: str, from_seq: int | None, to_seq: int | None, reason: str | None = None) -> None: ...
    def pending_transcript_chars(self, meeting_id: UUID, after_seq: int) -> int: ...
    def replace_lines(self, agenda: Any, *, track: str, texts: list[str], author_id: str | None) -> list[Any]: ...
    def todos(self, meeting: Any) -> list[Any]: ...
    def replace_todos(self, meeting: Any, drafts: list[dict[str, Any]]) -> None: ...
    def todo(self, meeting: Any, todo_id: UUID, *, lock: bool = False) -> Any | None: ...
    def link_todo(self, todo: Any, *, work_request_id: UUID) -> None: ...
    def delete_todo(self, todo: Any) -> None: ...
    def next_meeting_after(self, meeting: Any) -> Any | None: ...
    def delete_note_content(self, meeting: Any) -> None: ...
    def append_transcript_block(
        self, meeting_id: UUID, *, speaker_label: str, at_ms: int, end_ms: int, text: str
    ) -> Any: ...
    def transcript_blocks(self, meeting: Any) -> list[Any]: ...
    def transcript_blocks_after(self, meeting_id: UUID, after_seq: int) -> list[Any]: ...
    def transcript_speaker_count(self, meeting_id: UUID) -> int: ...
    def record_recording_file(self, meeting_id: UUID, *, storage_key: str, content_type: str) -> None: ...

class MeetingApplication:
    def __init__(self, repository: MeetingRepository, recordings: Any = None) -> None:
        self._repository = repository
        # 종료 뒤 재전사가 음원을 읽는 자리 (D44). 없으면 재전사를 건너뛴다 — 나머지 명령은 그대로 돈다.
        self._recordings = recordings

    def list(self, principal: Principal) -> list[dict[str, Any]]:
        """Calendar-safe projection: a meeting this person may not open contributes only a busy block.

        This stays as it was for the calendar and the MCP tools. The meeting screen reads `board` instead, which
        never mentions a meeting the viewer cannot open at all.
        """
        rows: list[dict[str, Any]] = []
        for meeting in self._repository.meetings_in_organizations(principal.organization_scope):
            if self._can_read_calendar_detail(principal, meeting):
                self._settle_auto_cancel(meeting)
                rows.append(self._calendar_row(principal, meeting))
            else:
                rows.append({"kind": "busy", "starts_at": _iso(meeting.starts_at), "ends_at": _iso(meeting.ends_at)})
        return rows

    def readable_rows(self, principal: Principal) -> list[dict[str, Any]]:
        """이 사람이 열 수 있는 회의들 — 자료 검색이 소유자를 물을 때 읽는 축이다.

        캘린더 투영과 다르다: 조직 범위가 아니라 **참석과 공유**가 축이고, 상태를 옮기지 않는다.
        """
        rows: list[dict[str, Any]] = []
        for meeting in self._repository.meetings_visible_to(principal.organization_scope, str(principal.id)):
            if not self._can_read_detail(principal, meeting):
                continue
            rows.append(
                {
                    "meeting_id": str(meeting.id),
                    "title": meeting.title,
                    "title_candidate": meeting.title_candidate,
                    "status": meeting.status,
                }
            )
        return rows

    def board(self, principal: Principal, *, cursor: str | None = None, page_size: int = PAST_PAGE_SIZE) -> dict[str, Any]:
        """회의 목록의 두 구획. 「예정」은 전부 내고 「지난」은 20건씩 잇는다 (SPEC §3.4-1).

        열 수 없는 회의는 여기 아예 서지 않는다 — 목록도 없는 것처럼 응답하는 자리다 (§3.2-1).
        """
        upcoming: list[dict[str, Any]] = []
        past: list[dict[str, Any]] = []
        for meeting in self._repository.meetings_visible_to(principal.organization_scope, str(principal.id)):
            if not self._can_read_detail(principal, meeting):
                continue
            self._settle_auto_cancel(meeting)
            row = self._row(principal, meeting)
            (past if self._is_past(meeting, row["viewer_relation"]) else upcoming).append(row)
        upcoming.sort(key=lambda row: (row["starts_at"] or "", row["meeting_id"]))
        past.sort(key=lambda row: (row["starts_at"] or "", row["meeting_id"]), reverse=True)
        page, next_cursor = _page(past, cursor, page_size)
        return {"upcoming": upcoming, "past": {"items": page, "next_cursor": next_cursor}}

    def get(self, principal: Principal, meeting_id: UUID) -> dict[str, Any]:
        meeting = self._readable(principal, meeting_id)
        return self._detail(principal, meeting)

    # ------------------------------------------------------------------ 세우기 · 고치기 · 지우기

    def create(
        self,
        principal: Principal,
        *,
        title: str | None,
        starts_at: datetime,
        ends_at: datetime,
        purpose: str | None = None,
        location: str | None = None,
        attendee_ids: list[str] | None = None,
        external_attendees: list[str] | None = None,
        agendas: list[dict[str, Any]] | None = None,
        carried_from_meeting_id: UUID | None = None,
        organization_id: str | None = None,
    ) -> dict[str, Any]:
        """예약. 회의를 세우는 사람은 그 회의의 참석자이기도 하다 — 목록에서 자기 회의를 잃지 않는다."""
        self._require(principal, MEETING_MANAGE)
        organization_id = organization_id or self._repository.primary_organization(str(principal.id))
        if organization_id is None or organization_id not in principal.organization_scope:
            raise MeetingAccessDenied("meeting organization is outside the principal scope")
        clean_title = normalize_optional_text(title, label="meeting title", limit=300)
        validate_meeting_schedule(starts_at, ends_at)
        attendees = self._resolved_attendees(principal, attendee_ids or [])
        carried = self._carried_source(principal, carried_from_meeting_id)
        drafts = [normalize_agenda_title(row.get("title")) for row in (agendas or [])]
        ensure_agenda_capacity(max(len(drafts) - 1, 0))
        meeting = self._repository.create(
            organization_id=organization_id,
            owner_id=str(principal.id),
            title=clean_title,
            purpose=normalize_optional_text(purpose, label="meeting purpose", limit=1000),
            starts_at=starts_at,
            ends_at=ends_at,
            location=normalize_optional_text(location, label="meeting location", limit=300),
            status=MeetingStatus.SCHEDULED.value,
            attendee_ids=attendees,
            external_attendees=list(normalize_external_attendees(external_attendees or [])),
            carried_from_meeting_id=carried,
        )
        source = "carried" if carried is not None else "manual"
        for order, agenda_title in enumerate(drafts, start=1):
            self._create_agenda(meeting, title=agenda_title, source=source, order_index=order)
        self._repository.append_audit(meeting, str(principal.id), "meeting.created", f"회의 생성: {meeting.title or '제목 없는 회의'}")
        return self._detail(principal, meeting)

    def quick_start(self, principal: Principal) -> dict[str, Any]:
        """바로 시작 — 값을 묻지 않고 세우고 곧장 연다. 그동안은 켠 사람만 본다 (SPEC §3.1-6 · `X-125`)."""
        self._require(principal, MEETING_MANAGE)
        organization_id = self._repository.primary_organization(str(principal.id))
        if organization_id is None or organization_id not in principal.organization_scope:
            raise MeetingAccessDenied("meeting organization is outside the principal scope")
        now = datetime.now(UTC)
        meeting = self._repository.create(
            organization_id=organization_id,
            owner_id=str(principal.id),
            title=None,
            purpose=None,
            starts_at=now,
            ends_at=now + QUICK_START_LENGTH,
            location=None,
            status=MeetingStatus.IN_PROGRESS.value,
            started_at=now,
            attendee_ids=[str(principal.id)],
            external_attendees=[],
            carried_from_meeting_id=None,
        )
        # 값을 묻지 않고 세운 회의에도 **메모가 붙을 자리**는 있어야 한다 — 안건이 0개면 메모 composer 의
        # 안건 고르기가 비어 사람이 아무것도 던지지 못한다 (코디 결정 D32). 사람이 세운 안건과 같은 자격이므로
        # `source` 는 manual 이고, 합성의 「사람 안건 보존」 대상이 된다 — AI 가 새로 세운 안건은 그 뒤에 선다.
        # 제목이 아니라 **빈 칸**이다 (D6) — 회의 중 배치가 실제 화제로 갈아 끼운다.
        self._create_agenda(
            meeting, title=QUICK_START_AGENDA_TITLE, source="manual", order_index=0, title_placeholder=True
        )
        self._repository.append_audit(meeting, str(principal.id), "meeting.quick_started", "회의 바로 시작")
        return self._detail(principal, meeting)

    def update_info(self, principal: Principal, meeting_id: UUID, changes: dict[str, Any]) -> dict[str, Any]:
        """회의 정보 편집 — 제목·일시·장소·참석자. 「예정」·「완료」에서만, 참석자 전원이 (SPEC §3.1-7 · §3.3)."""
        self._require(principal, MEETING_MANAGE)
        meeting = self._readable(principal, meeting_id, lock=True)
        view = self._view_plan(principal, meeting)
        if not view.is_attendee:
            raise MeetingAccessDenied("only an attendee may edit this meeting's information")
        unknown = set(changes) - {"title", "purpose", "starts_at", "ends_at", "location", "attendee_ids", "external_attendees"}
        if unknown:
            raise MeetingError(f"unsupported meeting fields: {sorted(unknown)}")
        if not view.can_edit_info:
            raise MeetingStateConflict("meeting information may be edited only while scheduled or done")
        starts_at = _aware(changes.get("starts_at") or meeting.starts_at)
        ends_at = _aware(changes.get("ends_at") or meeting.ends_at)
        validate_meeting_schedule(starts_at, ends_at)
        if "title" in changes:
            meeting.title = normalize_optional_text(changes["title"], label="meeting title", limit=300)
        if "purpose" in changes:
            meeting.purpose = normalize_optional_text(changes["purpose"], label="meeting purpose", limit=1000)
        if "location" in changes:
            meeting.location = normalize_optional_text(changes["location"], label="meeting location", limit=300)
        meeting.starts_at = starts_at
        meeting.ends_at = ends_at
        if "attendee_ids" in changes:
            attendees = self._resolved_attendees(principal, list(changes["attendee_ids"] or []), owner_id=meeting.owner_id)
            self._repository.replace_attendees(meeting, attendees, str(principal.id))
        if "external_attendees" in changes:
            meeting.external_attendees = list(
                normalize_external_attendees(list(changes["external_attendees"] or []))
            )
        before = meeting.version
        meeting.version += 1
        self._repository.touch(meeting)
        self._repository.append_audit(meeting, str(principal.id), "meeting.updated", f"회의 정보 수정: {meeting.title or '제목 없는 회의'}", before_ref=f"meeting:{meeting.id}@{before}")
        return self._detail(principal, meeting)

    def cancel(self, principal: Principal, meeting_id: UUID) -> None:
        """[회의 취소] — 회의 자체를 취소한다. 회의록·안건·자료가 함께 사라진다 (SPEC §3.1-9)."""
        self._require(principal, MEETING_MANAGE)
        meeting = self._readable(principal, meeting_id, lock=True)
        if not self._view_plan(principal, meeting).is_attendee:
            raise MeetingAccessDenied("only an attendee may cancel this meeting")
        meeting.status = ensure_transition(meeting.status, MeetingStatus.CANCELLED).value
        for agenda in self._repository.agendas(meeting):
            self._repository.delete_agenda(agenda)
        self._repository.delete_note_content(meeting)
        meeting.version += 1
        self._repository.touch(meeting)
        self._repository.append_audit(meeting, str(principal.id), "meeting.cancelled", "회의 취소")

    def delete_note(self, principal: Principal, meeting_id: UUID) -> None:
        """[회의록만 삭제] — 회의록과 자료를 지우고 회의 예약은 남긴다 (SPEC §3.1-9)."""
        self._require(principal, MEETING_MANAGE)
        meeting = self._readable(principal, meeting_id, lock=True)
        view = self._view_plan(principal, meeting)
        if not view.is_attendee:
            raise MeetingAccessDenied("only an attendee may delete this meeting note")
        if parse_status(meeting.status) is not MeetingStatus.SCHEDULED:
            raise MeetingStateConflict("a meeting note may be deleted only while the meeting is scheduled")
        self._repository.delete_note_content(meeting)
        self._repository.touch(meeting)
        self._repository.append_audit(meeting, str(principal.id), "meeting.note_deleted", "회의록만 삭제")

    def start(self, principal: Principal, meeting_id: UUID) -> dict[str, Any]:
        """[회의 시작]. 취소된 회의를 시작하면 자동 취소가 먼저 풀린다 (SPEC §5.1 취소됨 행)."""
        self._require(principal, MEETING_MANAGE)
        meeting = self._readable(principal, meeting_id, lock=True)
        if not self._view_plan(principal, meeting).is_attendee:
            raise MeetingAccessDenied("only an attendee may start this meeting")
        if parse_status(meeting.status) is MeetingStatus.CANCELLED:
            meeting.status = ensure_transition(meeting.status, MeetingStatus.SCHEDULED).value
        meeting.status = ensure_transition(meeting.status, MeetingStatus.IN_PROGRESS).value
        # 확정 발화의 `at_ms` 는 예정 시각이 아니라 이 시각을 기준으로 잰다 (SCAX-SPEC-004 §5.3 `ready`).
        meeting.started_at = datetime.now(UTC)
        self._repository.touch(meeting)
        self._repository.append_audit(meeting, str(principal.id), "meeting.started", "회의 시작")
        return self._detail(principal, meeting)

    def end(self, principal: Principal, meeting_id: UUID) -> dict[str, Any]:
        """[회의 종료] — 상태를 「정리 중」으로 옮기는 데까지가 이 WP다.

        스트림 닫기와 합성 job 등록은 SCAX-WP-002·004가 이 표면을 소비해 얹는다.
        """
        self._require(principal, MEETING_MANAGE)
        meeting = self._readable(principal, meeting_id, lock=True)
        if not self._view_plan(principal, meeting).is_attendee:
            raise MeetingAccessDenied("only an attendee may end this meeting")
        meeting.status = ensure_transition(meeting.status, MeetingStatus.SUMMARIZING).value
        self._repository.touch(meeting)
        self._repository.append_audit(meeting, str(principal.id), "meeting.ended", "회의 종료")
        return self._detail(principal, meeting)

    def write_memo(self, principal: Principal, meeting_id: UUID, agenda_id: UUID, text: str) -> dict[str, Any]:
        """메모 한 줄. **쓰는 사람은 회의를 만든 사람 하나이고 「진행 중」에만 선다** (SPEC-004 §6-1·2).

        시각은 클라이언트가 아니라 서버가 매긴다 — 회의 시작부터의 경과 밀리초다 (§6-5).
        이미 던진 메모는 안건을 옮기지 않는다 (§6-6) — 고치는 자리는 합성 뒤의 회의록이다.
        """
        self._require(principal, MEETING_MANAGE)
        meeting = self._readable(principal, meeting_id, lock=True)
        view = self._view_plan(principal, meeting)
        if not view.is_owner:
            raise MeetingAccessDenied("only the person who made this meeting may write its memo lines")
        if not view.can_write_memo:
            raise MeetingStateConflict("memo lines are written only while the meeting is running")
        body = normalize_memo_text(text)
        agenda = self._repository.agenda(meeting, agenda_id)
        if agenda is None:
            raise MeetingNotFound("meeting agenda was not found")
        line = self._repository.append_line(
            agenda, track="memo", text=body, author_id=str(principal.id), at_ms=self._elapsed_ms(meeting)
        )
        meeting.last_saved_at = datetime.now(UTC)
        # 기록이 생기면 자동 취소가 풀린다 (SPEC-004 §3.1-8).
        self._settle_auto_cancel(meeting)
        self._repository.touch(meeting)
        return self._line_view(line)

    def append_line(self, principal: Principal, meeting_id: UUID, agenda_id: UUID, *, track: str, text: str) -> dict[str, Any]:
        """트랙을 골라 줄 하나를 매단다 — 시나리오 seed 와 합성(SCAX-WP-004)이 쓰는 낮은 표면이다.

        사람이 쓰는 자리는 `write_memo` 하나다: 메모 트랙은 그 게이트를 지난다.
        """
        self._require(principal, MEETING_MANAGE)
        meeting = self._readable(principal, meeting_id, lock=True)
        if str(principal.id) != meeting.owner_id:
            raise MeetingAccessDenied("only the person who made this meeting may write its note lines")
        if track not in LINE_TRACKS:
            raise MeetingError(f"line track must be one of {sorted(LINE_TRACKS)}")
        body = str(text or "").strip()
        if not body:
            raise MeetingError("a note line needs text")
        agenda = self._repository.agenda(meeting, agenda_id)
        if agenda is None:
            raise MeetingNotFound("meeting agenda was not found")
        line = self._repository.append_line(agenda, track=track, text=body, author_id=str(principal.id))
        meeting.last_saved_at = datetime.now(UTC)
        self._settle_auto_cancel(meeting)
        self._repository.touch(meeting)
        return self._line_view(line)

    def _elapsed_ms(self, meeting: Any) -> int:
        """회의 시작부터의 경과 밀리초. 「진행 중」이면 `started_at` 이 있다 — 없으면 0 이다."""
        if meeting.started_at is None:
            return 0
        return max(0, int((datetime.now(UTC) - _aware(meeting.started_at)).total_seconds() * 1000))

    # ------------------------------------------------------------------ 종료 합성 (SCAX-WP-004)

    def finalize_input(self, meeting_id: UUID) -> dict[str, Any] | None:
        """합성 입력 — 안건 · 두 트랙의 줄 · 확정 발화(콜드 폴백용) · 세션 참조.

        「정리 중」이 아니면 `None` 이다: 잡의 재배달이거나 사람이 되돌린 회의다.
        """
        meeting = self._repository.meeting(meeting_id)
        if meeting is None or parse_status(meeting.status) is not MeetingStatus.SUMMARIZING:
            return None
        agendas = self._repository.agendas(meeting)
        lines = self._grouped_lines(meeting)
        blocks = self._repository.transcript_blocks(meeting)
        session = self._repository.ai_session(meeting_id)
        next_meeting = self._repository.next_meeting_after(meeting)
        carried: dict[str, Any] | None = None
        if meeting.carried_from_meeting_id is not None:
            source = self._repository.meeting(meeting.carried_from_meeting_id)
            if source is not None:
                carried = {
                    "title": source.title,
                    "agendas": [
                        {"title": agenda.title, "concluded": bool(agenda.concluded)}
                        for agenda in self._repository.agendas(source)
                    ],
                }
        return {
            "meeting_id": str(meeting.id),
            "persona_id": meeting.owner_id,
            "session_ref": None if session is None else session.provider_session_ref,
            "meeting": {
                "meeting_id": str(meeting.id),
                "title": meeting.title,
                "purpose": meeting.purpose,
                # 기준일 — 「이번 주 금요일」을 ISO 로 환산하려면 이 회의가 언제 열렸는지가 있어야 한다.
                "starts_on": describe_day(_aware(meeting.starts_at).date()),
                "next_meeting_on": describe_day(
                    None if next_meeting is None else _aware(next_meeting.starts_at).date()
                ),
                "carried_from": carried,
            },
            "agendas": [
                {
                    "agenda_id": str(agenda.id),
                    "order": agenda.order_index,
                    "title": agenda.title,
                    "source": agenda.source,
                    "concluded": bool(agenda.concluded),
                }
                for agenda in agendas
            ],
            # 사람이 세운 안건 — **넷 모두 사람 쪽이다**. AI 가 세운 것 하나만 가른다 (D38).
            "human_agenda_ids": [str(agenda.id) for agenda in agendas if agenda.source != AI_AGENDA_SOURCE],
            "memo_lines": _track_view(lines, "memo"),
            "ai_lines": _track_view(lines, "ai"),
            "transcript": [
                {"speakerLabel": block.speaker_label, "atMs": block.at_ms, "endMs": block.end_ms, "text": block.text}
                for block in blocks
            ],
            "covered_ms": (0, max((block.end_ms for block in blocks), default=0)),
            "next_meeting_starts_on": None if next_meeting is None else _aware(next_meeting.starts_at).date(),
        }

    # ------------------------------------------------------------------ 종료 뒤 재전사 (D44)

    def recording_for_retranscribe(self, meeting_id: UUID) -> Recording | None:
        """다시 전사할 음원. 녹음이 없으면 `None` — 건너뛰는 것이지 실패가 아니다.

        `base_ms` 는 회의 시작과 녹음 시작의 차이다: 파일의 0초가 회의의 0초가 아니므로 그만큼 밀어
        새 원문의 `at_ms` 를 **회의 시작 기준으로 되돌린다** — 근거 타임칩이 그 기준에 걸려 있다.
        """
        meeting = self._repository.meeting(meeting_id)
        if meeting is None:
            return None
        row = self._repository.recording_file(meeting_id)
        if row is None or self._recordings is None:
            return None
        try:
            data = self._recordings.get(row.storage_key)
        except FileNotFoundError:
            return None
        if not data:
            return None
        started_at = _aware(meeting.started_at) if meeting.started_at else None
        recorded_at = _aware(row.started_at) if row.started_at else None
        offset = 0
        if started_at is not None and recorded_at is not None:
            offset = max(0, int((recorded_at - started_at).total_seconds() * 1000))
        return Recording(data=data, filename=row.storage_key.rsplit("/", 1)[-1], base_ms=offset)

    def replace_transcript(self, meeting_id: UUID, blocks: list[Any]) -> int:
        """새 원문으로 전량 교체."""
        return self._repository.replace_transcript(meeting_id, blocks)

    def set_transcript_source(self, meeting_id: UUID, source: str) -> None:
        """이 회의록이 어느 원문으로 만들어졌는지 남긴다 — 화면이 그 한 줄을 사람에게 말한다."""
        meeting = self._repository.meeting(meeting_id, lock=True)
        if meeting is None:
            raise MeetingNotFound("meeting was not found")
        meeting.transcript_source = source
        self._repository.touch(meeting)

    def commit_finalized(self, meeting_id: UUID, notes: Any) -> dict[str, Any]:
        """한 트랜잭션 — 최종 줄 전량 교체 · 후보 전량 교체 · 제목 후보 · 상태 done (SPEC-004 §8-7).

        **회의록을 처음부터 새로 쓴 결과를 받는다** (사용자 결정 2026-09-11). 그래서 `agenda_id` 가 오면
        사람 안건이든 AI 안건이든 **그 안건을 이어 쓰고**, 없거나 모르는 id 면 새로 세운다. 출처는
        이어 쓰는 안건의 것을 그대로 두고(사람이 세운 안건은 계속 사람 것이다) 새 안건만 `ai` 다.

        **지금 서 있는 안건을 지우지 않는다.** 지우면 그 안건에 매달린 사람의 메모가 함께 사라지고,
        AI 가 이어 쓰겠다고 적어 준 id 도 허공을 가리킨다 — 비우는 것은 `final` 트랙의 줄뿐이다.
        """
        meeting = self._repository.meeting(meeting_id, lock=True)
        if meeting is None:
            raise MeetingNotFound("meeting was not found")
        # 회의 중 후보는 여기서 끝난다 (D46) — 최종이 같은 자리를 다시 채운다. 남겨 두면 같은 일이
        # 후보로 두 번 서고, 그중 하나는 아무도 승격할 수 없는 읽기 전용이다.
        self._repository.clear_provisional_todos(meeting)
        self._repository.clear_track(meeting, "final")
        existing = {str(agenda.id): agenda for agenda in self._repository.agendas(meeting)}
        drafts: list[dict[str, Any]] = []
        for output in notes.agendas:
            agenda = existing.get(str(output.agenda_id)) if output.agenda_id else None
            if agenda is None:
                if self._repository.agenda_count(meeting) >= MAX_AGENDAS_PER_MEETING:
                    continue
                agenda = self._create_agenda(
                    meeting,
                    title=normalize_agenda_title(output.title),
                    source=AI_AGENDA_SOURCE,
                    order_index=self._repository.next_agenda_order(meeting),
                )
            elif output.title:
                # 이어 쓰는 안건의 제목도 AI 가 다시 잡는다 — 묶고 나눈 결과가 제목에 나타난다.
                agenda.title = normalize_agenda_title(output.title)
            # 결론 표시는 AI 가 내고 회의를 만든 사람이 고친다 (SPEC-004 §4.1-3).
            agenda.concluded = bool(output.concluded)
            self._repository.touch_agenda(agenda)
            for line in output.lines:
                self._repository.append_line(
                    agenda, track="final", text=line.text, author_id=None, evidence=list(line.evidence)
                )
            for order, todo in enumerate(output.todos, start=1):
                drafts.append(
                    {
                        "agenda_id": agenda.id,
                        "order_index": order,
                        "title": todo.title,
                        "description": todo.description,
                        "due_candidate": todo.due_candidate,
                        "checklist_candidate": list(todo.checklist_candidate),
                        "reference": {
                            "meeting_id": str(meeting.id),
                            "agenda_id": str(agenda.id),
                            "line_ids": list(todo.line_ids),
                        },
                    }
                )
        self._repository.replace_todos(meeting, drafts)
        if not meeting.title and notes.title_candidate:
            # 사람이 저장해야 제목이 된다 — 그전까지는 「제목 없는 회의」다 (SPEC-004 §3.1-6 · D14).
            meeting.title_candidate = notes.title_candidate
        meeting.failure_reason = None
        meeting.last_saved_at = datetime.now(UTC)
        meeting.status = ensure_transition(meeting.status, MeetingStatus.DONE).value
        self._repository.touch(meeting)
        self._repository.append_audit(meeting, meeting.owner_id, "meeting.finalized", "회의록 합성 완료")
        return self._detail_for_owner(meeting)

    def fail_finalize(self, meeting_id: UUID, reason: str) -> None:
        """합성 실패 — 받은 발화와 메모는 그대로 남고 상태만 「실패」다 (SPEC-004 §8-8)."""
        meeting = self._repository.meeting(meeting_id, lock=True)
        if meeting is None or parse_status(meeting.status) is not MeetingStatus.SUMMARIZING:
            return
        meeting.status = ensure_transition(meeting.status, MeetingStatus.FAILED).value
        meeting.failure_reason = (reason or "")[:2000] or None
        self._repository.touch(meeting)
        self._repository.append_audit(meeting, meeting.owner_id, "meeting.finalize_failed", "회의록 합성 실패")

    def retry_finalize(self, principal: Principal, meeting_id: UUID) -> dict[str, Any]:
        """[다시 시도] — 「실패」에서만. 합성만 다시 걸고 원문을 건드리지 않는다 (SPEC-004 §8-8)."""
        self._require(principal, MEETING_MANAGE)
        meeting = self._readable(principal, meeting_id, lock=True)
        if not self._is_attendee(principal, meeting):
            raise MeetingAccessDenied("only an attendee may retry this meeting's merge")
        meeting.status = ensure_transition(meeting.status, MeetingStatus.SUMMARIZING).value
        meeting.failure_reason = None
        self._repository.touch(meeting)
        self._repository.append_audit(meeting, str(principal.id), "meeting.finalize_retried", "회의록 합성 다시 시도")
        return self._detail(principal, meeting)

    # ------------------------------------------------------------------ 다음 할 일 · 승격

    def todo_for_promotion(self, principal: Principal, meeting_id: UUID, todo_id: UUID) -> tuple[Any, Any]:
        """승격이 딛는 후보 하나. 승격은 참석자 전원이 한다 (SPEC-004 §3.3 · §9-5)."""
        self._require(principal, MEETING_MANAGE)
        meeting = self._readable(principal, meeting_id, lock=True)
        if not self._is_attendee(principal, meeting):
            raise MeetingAccessDenied("only an attendee may promote a follow-up candidate")
        todo = self._repository.todo(meeting, todo_id, lock=True)
        if todo is None:
            raise MeetingNotFound("meeting follow-up candidate was not found")
        ensure_todo_actionable(
            provisional=bool(getattr(todo, "provisional", False)),
            linked_work_request_id=todo.linked_work_request_id,
            action="promote",
        )
        return meeting, todo

    def attendee_ids(self, meeting: Any) -> set[str]:
        """이 회의에 담긴 사람들 — 승격의 담당 후보가 여기서 먼저 난다 (SPEC-004 §9-5)."""
        return set(self._repository.attendee_ids(meeting)) | {meeting.owner_id}

    def link_promoted_todo(self, todo: Any, *, work_request_id: UUID) -> dict[str, Any]:
        self._repository.link_todo(todo, work_request_id=work_request_id)
        return self._todo_view(todo)

    def remove_todo(self, principal: Principal, meeting_id: UUID, todo_id: UUID) -> None:
        """안 만들 후보는 확인 없이 지운다 — 아직 업무가 아니라 남에게 가는 것도 사라지는 내용도 없다 (§9-9)."""
        self._require(principal, MEETING_MANAGE)
        meeting = self._readable(principal, meeting_id, lock=True)
        if not self._is_attendee(principal, meeting):
            raise MeetingAccessDenied("only an attendee may delete a follow-up candidate")
        todo = self._repository.todo(meeting, todo_id, lock=True)
        if todo is None:
            raise MeetingNotFound("meeting follow-up candidate was not found")
        ensure_todo_actionable(
            provisional=bool(getattr(todo, "provisional", False)),
            linked_work_request_id=todo.linked_work_request_id,
            action="delete",
        )
        self._repository.delete_todo(todo)

    def export(self, principal: Principal, meeting_id: UUID) -> dict[str, Any]:
        """내보내기가 딛는 마지막 저장분 — 회의 정보 · 안건별 줄 · 다음 할 일 (SPEC-004 §8-10).

        **저장 위치·provider 참조 같은 내부 값은 담지 않는다.**
        """
        meeting = self._readable(principal, meeting_id)
        detail = self._detail(principal, meeting)
        return {
            "meeting": detail["meeting"],
            "agendas": [
                {
                    **agenda,
                    "lines": [line for line in agenda["lines"] if line["track"] == "final"] or agenda["lines"],
                }
                for agenda in detail["agendas"]
            ],
        }

    def _detail_for_owner(self, meeting: Any) -> dict[str, Any]:
        """잡이 만든 결과를 그대로 돌려줄 때 쓰는 투영 — 사람의 요청이 아니라 회의 자신의 시점이다."""
        lines = self._grouped_lines(meeting)
        todos = self._grouped_todos(meeting)
        return {
            "meeting_id": str(meeting.id),
            "status": meeting.status,
            "title_candidate": meeting.title_candidate,
            "agendas": [self._agenda_view(agenda, lines, todos) for agenda in self._repository.agendas(meeting)],
        }

    # ------------------------------------------------------------------ 공유 (SCAX-WP-005 인계 예정)

    def viewers(self, principal: Principal, meeting_id: UUID) -> list[dict[str, Any]]:
        """「볼 수 있는 사람」 — 참석과 공유를 한 목록으로 낸다 (SPEC-004 §3.2-3).

        `basis` 가 둘을 가른다: 참석은 거둘 수 없고 공유만 거둔다 (§3.2-6).
        """
        meeting = self._readable(principal, meeting_id)
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for member_id in sorted(self._repository.attendee_ids(meeting) | {meeting.owner_id}):
            seen.add(member_id)
            rows.append(
                {
                    "member_id": member_id,
                    "name": self._repository.member_display_name(member_id) or member_id,
                    "basis": "attendee",
                }
            )
        for member_id in sorted(self._repository.shared_member_ids(meeting)):
            if member_id in seen:
                continue
            rows.append(
                {
                    "member_id": member_id,
                    "name": self._repository.member_display_name(member_id) or member_id,
                    "basis": "share",
                }
            )
        return rows

    def share_many(self, principal: Principal, meeting_id: UUID, member_ids: list[str]) -> list[dict[str, Any]]:
        """여러 명에게 한 번에 연다. **이미 참석이거나 이미 열람인 사람은 조용히 건너뛴다** (SPEC-004 §3.2-3).

        알림은 가지 않는다 — 목록에 담기는 것이 유일한 도달 경로다 (§2.2).
        """
        self._require(principal, MEETING_SHARE)
        meeting = self._readable(principal, meeting_id, lock=True)
        if not self._is_attendee(principal, meeting):
            raise MeetingAccessDenied("only an attendee may share this meeting")
        already = self._repository.attendee_ids(meeting) | {meeting.owner_id} | set(
            self._repository.shared_member_ids(meeting)
        )
        opened = 0
        for member_id in new_share_targets(member_ids, existing_member_ids=already):
            if not self._repository.is_active_member(member_id):
                raise MeetingError("share target is not an active member")
            self._repository.add_share(meeting, member_id, str(principal.id))
            opened += 1
        if opened:
            self._repository.touch(meeting)
            self._repository.append_audit(meeting, str(principal.id), "meeting.shared", "회의 열람 공유")
        return self.viewers(principal, meeting_id)

    def share(self, principal: Principal, meeting_id: UUID, member_id: str) -> dict[str, Any]:
        """공유는 열람만 연다 — 수정 권한을 주지 않는다 (SPEC §3.2-2). 모달과 거두기는 SCAX-WP-005다."""
        self._require(principal, MEETING_SHARE)
        meeting = self._readable(principal, meeting_id, lock=True)
        if not self._is_attendee(principal, meeting):
            raise MeetingAccessDenied("only an attendee may share this meeting")
        if not self._repository.is_active_member(member_id):
            raise MeetingError("share target is not an active member")
        self._repository.add_share(meeting, member_id, str(principal.id))
        self._repository.touch(meeting)
        self._repository.append_audit(meeting, str(principal.id), "meeting.shared", "회의 열람 공유")
        return self._row(principal, meeting)

    def revoke_share(self, principal: Principal, meeting_id: UUID, member_id: str) -> list[dict[str, Any]]:
        """공유로 들어온 열람만 거둘 수 있다 — 참석을 빼는 자리는 회의 정보 편집이다 (SPEC §3.2-6)."""
        self._require(principal, MEETING_SHARE)
        meeting = self._readable(principal, meeting_id, lock=True)
        if not self._is_attendee(principal, meeting):
            raise MeetingAccessDenied("only an attendee may revoke a share on this meeting")
        ensure_share_revocable(
            member_id,
            attendee_ids=self._repository.attendee_ids(meeting) | {meeting.owner_id},
        )
        if not self._repository.revoke_share(meeting, member_id):
            raise MeetingNotFound("meeting share was not found")
        self._repository.touch(meeting)
        self._repository.append_audit(meeting, str(principal.id), "meeting.share_revoked", "회의 열람 공유 회수")
        # 거둔 뒤의 「볼 수 있는 사람」을 그대로 돌려준다 — 화면이 다시 물어보지 않는다.
        return self.viewers(principal, meeting_id)

    # ------------------------------------------------------------------ 안건

    def add_agenda(self, principal: Principal, meeting_id: UUID, title: str) -> dict[str, Any]:
        """안건 하나를 세운다 — **회의가 도는 동안에도** 만든 사람은 세울 수 있다 (D45)."""
        meeting = self._agenda_target(principal, meeting_id, adding=True)
        clean = normalize_agenda_title(title)
        ensure_agenda_capacity(self._repository.agenda_count(meeting))
        agenda = self._create_agenda(
            meeting, title=clean, source="manual", order_index=self._repository.next_agenda_order(meeting)
        )
        self._repository.append_audit(meeting, str(principal.id), "meeting.agenda_added", f"안건 추가: {clean}")
        return self._agenda_view(agenda, self._grouped_lines(meeting), self._grouped_todos(meeting))

    def update_agenda(self, principal: Principal, meeting_id: UUID, agenda_id: UUID, changes: dict[str, Any]) -> dict[str, Any]:
        meeting = self._agenda_target(principal, meeting_id)
        agenda = self._repository.agenda(meeting, agenda_id, lock=True)
        if agenda is None:
            raise MeetingNotFound("meeting agenda was not found")
        unknown = set(changes) - {"title", "concluded", "order", "lines", "expected_last_saved_at"}
        if unknown:
            raise MeetingError(f"unsupported agenda fields: {sorted(unknown)}")
        if "expected_last_saved_at" in changes:
            # 같은 사람이 다른 탭에서 먼저 저장했으면 덮어쓰지 않고 차이를 낸다 — **판정은 안건 단위다**
            # (SPEC-004 §8-9 · `SCR-106-I16`). 읽은 시각과 지금 저장 시각이 다르면 그 사이에 누가 저장한 것이다.
            expected = changes["expected_last_saved_at"]
            if _iso(agenda.updated_at) != (expected or None):
                raise MeetingStaleWrite(
                    "이 안건은 그 사이에 저장됐습니다", self._agenda_view(agenda, self._grouped_lines(meeting), self._grouped_todos(meeting))
                )
        if "title" in changes:
            agenda.title = normalize_agenda_title(changes["title"])
            # 사람이 이름을 붙였다 — 더 이상 빈 칸이 아니므로 AI 가 그 뒤로 바꾸지 않는다 (D6).
            agenda.title_placeholder = False
        if "concluded" in changes:
            agenda.concluded = bool(changes["concluded"])
        if "order" in changes:
            agenda.order_index = normalize_agenda_order(changes["order"])
        if "lines" in changes:
            self._rewrite_note_lines(principal, meeting, agenda, changes["lines"])
        self._repository.touch_agenda(agenda)
        self._repository.append_audit(meeting, str(principal.id), "meeting.agenda_updated", f"안건 수정: {agenda.title}")
        return self._agenda_view(agenda, self._grouped_lines(meeting), self._grouped_todos(meeting))

    def _rewrite_note_lines(self, principal: Principal, meeting: Any, agenda: Any, lines: object) -> None:
        """[수정] 하나로 열리고 [저장] 하나로 닫히는 줄 단위 편집 (SPEC §4.2-6 · `X-186`).

        판을 쌓지 않는다 — 이 안건의 합성 트랙 줄 목록을 통째로 덮어쓰고 마지막 저장분이 그 회의록이다.
        빈 줄은 저장할 때 버린다.
        """
        if not self._view_plan(principal, meeting).can_edit_note:
            raise MeetingStateConflict("meeting note lines may be edited only after the meeting is done or failed")
        texts = normalize_note_lines(lines)
        self._repository.replace_lines(agenda, track="final", texts=texts, author_id=str(principal.id))
        meeting.last_saved_at = datetime.now(UTC)
        # 회의록을 쓰면 자동 취소가 풀린다 — 마지막 줄을 지우면 다시 걸린다 (SPEC §3.1-8).
        self._settle_auto_cancel(meeting)
        self._repository.touch(meeting)

    def remove_agenda(self, principal: Principal, meeting_id: UUID, agenda_id: UUID) -> None:
        meeting = self._agenda_target(principal, meeting_id)
        agenda = self._repository.agenda(meeting, agenda_id, lock=True)
        if agenda is None:
            raise MeetingNotFound("meeting agenda was not found")
        title = agenda.title
        self._repository.delete_agenda(agenda)
        self._repository.append_audit(meeting, str(principal.id), "meeting.agenda_removed", f"안건 삭제: {title}")

    # ------------------------------------------------------------------ 스트림 적재 (SCAX-WP-002)

    def stream_admission(self, principal: Principal, meeting_id: UUID) -> Any | None:
        """스트림이 묻는 것 — 이 사람이 이 회의를 열 수 있는가, 그리고 언제 시작했는가.

        열 수 없으면 `None` 이다. 없는 회의와 참석 아닌 회의를 가르지 않는다 — 존재를 알리지 않는다 (§3.2-1).
        """
        meeting = self._repository.meeting(meeting_id)
        if meeting is None or not self._can_read_detail(principal, meeting):
            return None
        return MeetingAdmission(
            meeting_id=str(meeting.id),
            status=meeting.status,
            started_at=_aware(meeting.started_at or meeting.starts_at),
            # 업스트림 자리는 회의를 만든 사람의 것이다 (코디 결정 D27). 구독은 참석·공유 그대로다.
            is_owner=str(principal.id) == meeting.owner_id,
        )

    def append_transcript_block(
        self, meeting_id: UUID, *, speaker_label: str, at_ms: int, end_ms: int, text: str
    ) -> str:
        """확정 발화 블록 한 행. 잠정은 여기 오지 않는다 (§10-11).

        열람 판정은 연결이 설 때 이미 끝났다 — 이 자리는 그 세션이 부르는 적재 하나다.
        """
        block = self._repository.append_transcript_block(
            meeting_id, speaker_label=speaker_label, at_ms=at_ms, end_ms=end_ms, text=text
        )
        return str(block.id)

    def transcript_ready_state(self, meeting_id: UUID) -> tuple[int, int]:
        """`ready` 가 싣는 두 값 — 지금까지 성공한 배치의 최대 회차와 화자 수.

        배치는 SCAX-WP-003 이 만든다. 그때까지 회차는 0 이다.
        """
        return 0, self._repository.transcript_speaker_count(meeting_id)

    def note_recording_file(self, meeting_id: UUID, storage_key: str, *, content_type: str) -> None:
        """오디오 원본의 자리를 한 번 기록한다. 이 값은 어느 응답에도 나가지 않는다 (§5.5-4)."""
        self._repository.record_recording_file(meeting_id, storage_key=storage_key, content_type=content_type)

    def transcript(self, principal: Principal, meeting_id: UUID) -> dict[str, Any]:
        """「스크립트」 탭 — 확정 발화 원문과 회의 중 메모를 함께 낸다 (SPEC-004 §5.4-7·8).

        열람 축은 상세와 같다: 참석 또는 공유. 아직 아무 말도 없는 회의는 빈 목록이지 없는 회의가 아니다.
        """
        meeting = self._readable(principal, meeting_id)
        return {
            "items": [
                {
                    "id": str(block.id),
                    "speakerLabel": block.speaker_label,
                    "atMs": block.at_ms,
                    "endMs": block.end_ms,
                    "content": block.text,
                }
                for block in self._repository.transcript_blocks(meeting)
            ],
            "memos": [
                {
                    "line_id": str(line.id),
                    "agenda_id": str(line.agenda_id),
                    "text": line.text,
                    "author": line.author_id,
                    "atMs": line.at_ms,
                }
                for line in self._repository.memo_lines(meeting)
            ],
        }

    # ------------------------------------------------------------------ AI 배치 (SCAX-WP-003)

    def warm_start_context(self, meeting_id: UUID) -> dict[str, Any] | None:
        """웜스타트 첫 turn 이 실을 맥락 — 회의 정보 · 안건 · 참석자 수 · 이어진 이전 회의 (SPEC §7.1).

        **참석자 실명을 싣지 않는다** — 화자는 익명이고 프롬프트가 사람 이름을 들고 다닐 이유가 없다.
        도구는 회의를 만든 사람으로 선다 (§13 `OQ-315` 잠정값).
        """
        meeting = self._repository.meeting(meeting_id)
        if meeting is None:
            return None
        carried: dict[str, Any] | None = None
        if meeting.carried_from_meeting_id is not None:
            source = self._repository.meeting(meeting.carried_from_meeting_id)
            if source is not None:
                carried = {
                    "meeting_id": str(source.id),
                    "title": source.title,
                    "agendas": [
                        {"title": agenda.title, "concluded": bool(agenda.concluded)}
                        for agenda in self._repository.agendas(source)
                    ],
                }
        return {
            "persona_id": meeting.owner_id,
            "meeting_id": str(meeting.id),
            "title": meeting.title,
            "purpose": meeting.purpose,
            "location": meeting.location,
            "attendee_count": len(self._repository.attendee_ids(meeting)),
            "agendas": [
                {"agenda_id": str(agenda.id), "title": agenda.title, "source": agenda.source}
                for agenda in self._repository.agendas(meeting)
            ],
            "carried_from": carried,
        }

    def record_ai_session(self, meeting_id: UUID, *, session_ref: str, persona_id: str) -> None:
        self._repository.record_ai_session(meeting_id, provider_session_ref=session_ref, persona_id=persona_id)

    def ai_session_ref(self, meeting_id: UUID) -> str | None:
        """SCAX-WP-004 의 종료 합성이 **같은 세션**을 이어 쓸 때 읽는 자리다 (SPEC §8-3)."""
        row = self._repository.ai_session(meeting_id)
        return None if row is None else row.provider_session_ref

    def pending_batch_chars(self, meeting_id: UUID) -> int:
        return self._repository.pending_transcript_chars(
            meeting_id, self._repository.succeeded_batch_cursor(meeting_id)
        )

    def batch_input(self, meeting_id: UUID) -> dict[str, Any] | None:
        """제출할 것이 있으면 증분, 없으면 `None`.

        세션이 없으면 제출하지 않는다 — 트리거만 평가하고 구간은 미처리로 남는다 (SPEC §7.1 세션 행).
        「진행 중」이 아니면 새 배치가 없다 — 종료 뒤 합성은 SCAX-WP-004 의 다른 진입점이다.
        """
        meeting = self._repository.meeting(meeting_id)
        if meeting is None or parse_status(meeting.status) is not MeetingStatus.IN_PROGRESS:
            return None
        session = self._repository.ai_session(meeting_id)
        if session is None:
            return None
        cursor = self._repository.succeeded_batch_cursor(meeting_id)
        blocks = self._repository.transcript_blocks_after(meeting_id, cursor)
        if not blocks:
            return None
        memos = [
            line
            for line in self._repository.memo_lines(meeting)
            if line.at_ms is not None and line.at_ms >= blocks[0].at_ms
        ]
        return {
            "seq": self._repository.next_batch_seq(meeting_id),
            "session_ref": session.provider_session_ref,
            "persona_id": session.persona_id,
            "blocks": [
                {
                    "speakerLabel": block.speaker_label,
                    "atMs": block.at_ms,
                    "endMs": block.end_ms,
                    "text": block.text,
                }
                for block in blocks
            ],
            "memos": [
                {"agenda_id": str(line.agenda_id), "text": line.text, "author": line.author_id}
                for line in memos
            ],
            "from_seq": blocks[0].seq,
            "to_seq": blocks[-1].seq,
            # 근거 검증 구간은 **회의 전체**다 — 시작부터 이번 배치 끝까지 (사용자 결정 D5, 2026-09-11).
            #
            # 배치는 매번 AI 트랙 전체를 다시 쓴다. 검증 구간을 「이번 배치의 미처리 발화」로 두면 앞
            # 구간을 근거로 단 줄이 매 회차 근거를 떼여, 화면에는 **최신 구간 줄의 시간 칩만** 남는다.
            # 실물 2회차에서 그 일이 그대로 났다. 최종 합성의 `(0, 마지막 end_ms)` 와 같은 결로 맞춘다.
            "covered_ms": (0, max(block.end_ms for block in blocks)),
        }

    def record_batch_run(
        self, meeting_id: UUID, *, seq: int, status: str, cause: str, from_seq: int, to_seq: int, reason: str | None
    ) -> None:
        self._repository.record_batch_run(
            meeting_id, seq=seq, status=status, trigger_cause=cause, from_seq=from_seq, to_seq=to_seq, reason=reason
        )

    def replace_ai_track(self, meeting_id: UUID, agendas: list[Any]) -> list[dict[str, Any]]:
        """검증 통과분으로 **AI 트랙 전량 교체** (SPEC §7.1 적재 · §7.3 경계).

        사람이 만든 안건은 제목도 출처도 건드리지 않는다 — AI 는 그 안건에 자기 줄만 매단다.
        AI 가 세운 안건은 배치마다 새로 서므로 이전 것을 지우고 다시 만든다.
        """
        meeting = self._repository.meeting(meeting_id, lock=True)
        if meeting is None:
            raise MeetingNotFound("meeting was not found")
        self._repository.replace_track(meeting, "ai")
        existing = {str(agenda.id): agenda for agenda in self._repository.agendas(meeting)}
        drafts: list[dict[str, Any]] = []
        for output in agendas:
            agenda = existing.get(str(output.agenda_id)) if output.agenda_id else None
            if agenda is None:
                # AI 가 세운 안건 — 출처는 「AI 정리」이고 사람 안건 뒤에 선다 (SPEC §4.1-2·6).
                title = normalize_agenda_title(output.title)
                if self._repository.agenda_count(meeting) >= MAX_AGENDAS_PER_MEETING:
                    continue
                agenda = self._create_agenda(
                    meeting, title=title, source="ai", order_index=self._repository.next_agenda_order(meeting)
                )
            if getattr(agenda, "title_placeholder", False):
                # 자리표시 제목은 AI 가 채운다 (D6). **출처는 사람 것 그대로** 둔다 — 그 안건을 세운 것은
                # 사람이고 AI 는 이름만 붙였다. 매 배치 최신화하되, 사람이 한 번 고치면 그 뒤로는 불변이다.
                filled = normalize_agenda_title(output.title)
                if filled and filled != agenda.title:
                    agenda.title = filled
                    self._repository.touch_agenda(agenda)
            for line in output.lines:
                self._repository.append_line(
                    agenda, track="ai", text=line.text, author_id=None, evidence=list(line.evidence)
                )
            for order, todo in enumerate(getattr(output, "todos", None) or [], start=1):
                # 회의 **중** 후보다 (D46) — 읽기 전용이고 다음 배치가 통째로 갈아 끼운다.
                # 담당자는 없고, 회의 중이라 체크리스트는 비어 있을 수 있다.
                drafts.append(
                    {
                        "agenda_id": agenda.id,
                        "order_index": order,
                        "title": todo.title,
                        "description": todo.description,
                        "due_candidate": todo.due_candidate,
                        "checklist_candidate": list(todo.checklist_candidate),
                        "reference": {
                            "meeting_id": str(meeting.id),
                            "agenda_id": str(agenda.id),
                            "line_ids": list(todo.line_ids),
                        },
                    }
                )
        self._repository.replace_provisional_todos(meeting, drafts)
        lines = self._grouped_lines(meeting)
        todos = self._grouped_todos(meeting)
        return [self._agenda_view(agenda, lines, todos) for agenda in self._repository.agendas(meeting)]

    def unprocessed_transcript_cursor(self, meeting_id: UUID, *, after_seq: int = 0) -> list[dict[str, Any]]:
        """아직 배치가 읽지 않은 확정 블록들. SCAX-WP-003 의 트리거가 여기 걸린다 — 지금은 호출자가 없다."""
        return [
            {
                "id": str(block.id),
                "seq": block.seq,
                "speaker_label": block.speaker_label,
                "at_ms": block.at_ms,
                "end_ms": block.end_ms,
                "content": block.text,
            }
            for block in self._repository.transcript_blocks_after(meeting_id, after_seq)
        ]

    def _readable(self, principal: Principal, meeting_id: UUID, *, lock: bool = False) -> Any:
        """열람 판정이 실패하면 없는 것처럼 응답한다 — 존재를 알리지 않는다 (SPEC §3.2-1 · §10-1)."""
        meeting = self._repository.meeting(meeting_id, lock=lock)
        if meeting is None or not self._can_read_detail(principal, meeting):
            raise MeetingNotFound("meeting was not found")
        self._settle_auto_cancel(meeting)
        return meeting

    def _agenda_target(self, principal: Principal, meeting_id: UUID, *, adding: bool = False) -> Any:
        """안건을 더하고 지우고 결론 표시를 고치는 사람은 회의를 만든 사람이다 (SPEC §3.3).

        회의가 도는 동안 **이미 선 안건**은 사람이 손대지 않는다 — 그것을 딛고 있는 메모와 AI 줄이
        발밑에서 바뀐다 (SPEC §4.1-6). 다만 **새로 세우는 것은 다르다**: 말이 새 주제로 넘어가는 순간이
        곧 안건이 필요한 순간이고, 새 안건은 아직 아무것도 딛고 있지 않다 (D45). `adding` 이 그 자리다.
        """
        self._require(principal, MEETING_MANAGE)
        meeting = self._readable(principal, meeting_id, lock=True)
        view = self._view_plan(principal, meeting)
        if not view.is_owner:
            raise MeetingAccessDenied("only the person who made this meeting may change its agendas")
        allowed = view.can_add_agenda if adding else view.can_edit_agendas
        if not allowed:
            raise MeetingStateConflict("agendas are not edited by hand while the meeting is running or summarizing")
        return meeting

    def _can_read_detail(self, principal: Principal, meeting: Any) -> bool:
        """회의를 여는 사람은 그 회의의 참석자다. 공유가 유일한 예외다 (SPEC §3.2-1·2 · §10-1).

        축은 둘뿐이다 — 조직 범위로 남의 회의를 여는 셋째 축은 두지 않는다. 대표라도 참석하거나 공유받지 않은
        회의는 없는 것처럼 응답한다. 캘린더의 시간 덩어리는 회의를 여는 것이 아니므로 `list`가 따로 판정한다.
        """
        return self._view_plan(principal, meeting).detail_readable

    def _can_read_calendar_detail(self, principal: Principal, meeting: Any) -> bool:
        """캘린더가 시간 덩어리 대신 제목까지 낼 수 있는가.

        전체 조회 권한(`meeting.read.private`)이 조직 범위 안에서만 닿는 자리는 여기 하나로 남긴다 — 회의 화면의
        열람 경계(§3.2)와 캘린더의 투영은 다른 물음이다.
        """
        return self._view_plan(principal, meeting).calendar_detail_readable

    def _is_attendee(self, principal: Principal, meeting: Any) -> bool:
        return self._view_plan(principal, meeting).is_attendee

    def _viewer_relation(self, principal: Principal, meeting: Any) -> str:
        return self._view_plan(principal, meeting).relation

    def _is_past(self, meeting: Any, viewer_relation: str) -> bool:
        """공유받은 회의는 「지난」에 담긴다 — 캘린더에 서지 않는다 (SPEC §3.2-5)."""
        return is_meeting_past(
            meeting.status,
            ends_at=_aware(meeting.ends_at),
            relation=viewer_relation,
            now=datetime.now(UTC),
        )

    def _view_plan(
        self,
        principal: Principal,
        meeting: Any,
        *,
        attendee_ids: set[str] | None = None,
    ) -> MeetingView:
        attendees = attendee_ids if attendee_ids is not None else self._repository.attendee_ids(meeting)
        member_id = str(principal.id)
        shared = (
            frozenset()
            if member_id == str(meeting.owner_id) or member_id in attendees
            else frozenset(self._repository.shared_member_ids(meeting))
        )
        return project_meeting_view(
            MeetingViewContext(
                status=meeting.status,
                owner_id=str(meeting.owner_id),
                organization_id=str(meeting.organization_id),
                attendee_ids=frozenset(attendees),
                shared_member_ids=shared,
                ends_at=_aware(meeting.ends_at),
            ),
            MeetingActorContext(
                member_id=member_id,
                can_read=MEETING_READ in principal.capabilities,
                can_read_private=MEETING_READ_PRIVATE in principal.capabilities,
                organization_scope=frozenset(principal.organization_scope),
            ),
            now=datetime.now(UTC),
        )

    def _settle_auto_cancel(self, meeting: Any) -> None:
        """자동 취소는 스케줄러 없이 조회 시점에 판정한다 — 판정과 해제가 같은 자리에 있다 (WP-001 Open Issue).

        종료 시각까지 줄이 하나도 없이 지난 「예정」은 「취소됨」이 되고, 줄이 생기면 도로 「예정」이 된다.
        """
        if not needs_auto_settlement(meeting.status):
            return
        has_record = self._repository.line_count(meeting) > 0
        settled = auto_settled_status(
            meeting.status,
            ends_at=_aware(meeting.ends_at),
            created_at=_aware(meeting.created_at),
            has_record=has_record,
            now=datetime.now(UTC),
        )
        if settled is not parse_status(meeting.status):
            meeting.status = settled.value
            self._repository.touch(meeting)

    def _create_agenda(
        self, meeting: Any, *, title: str, source: str, order_index: int, title_placeholder: bool = False
    ) -> Any:
        """안건 하나를 세운다. **출처는 여기서 한 번 검사한다** — 아는 다섯 말고는 저장되지 않는다 (D38).

        지금 이 자리를 지나는 출처는 `manual`·`carried`·`ai` 셋뿐이다. `set`·`derived` 는 값만 열려 있고
        만드는 경로가 아직 없다 — 그 경로가 생기면 여기를 지나므로 검사를 새로 세울 일이 없다.
        """
        return self._repository.create_agenda(
            meeting, title=title, source=ensure_agenda_source(source), order_index=order_index,
            title_placeholder=title_placeholder,
        )

    def _resolved_attendees(self, principal: Principal, attendee_ids: list[str], *, owner_id: str | None = None) -> list[str]:
        """회의를 만든 사람은 언제나 참석자다 — 자기 회의를 목록에서 잃지 않는다."""
        wanted = list(
            meeting_attendees(
                attendee_ids or [],
                owner_id=owner_id or str(principal.id),
            )
        )
        for member_id in wanted:
            if not self._repository.is_active_member(member_id):
                raise MeetingError("attendee is not an active member")
        return wanted

    def _carried_source(self, principal: Principal, carried_from_meeting_id: UUID | None) -> UUID | None:
        if carried_from_meeting_id is None:
            return None
        source = self._repository.meeting(carried_from_meeting_id)
        if source is None or not self._can_read_detail(principal, source):
            raise MeetingNotFound("the meeting this one continues was not found")
        return source.id

    # ------------------------------------------------------------------ 응답 만들기

    def _row(self, principal: Principal, meeting: Any) -> dict[str, Any]:
        return {
            "meeting_id": str(meeting.id),
            "title": meeting.title,
            "starts_at": _iso(meeting.starts_at),
            "ends_at": _iso(meeting.ends_at),
            "location": meeting.location,
            "status": meeting.status,
            "viewer_relation": self._viewer_relation(principal, meeting),
            "created_by": meeting.owner_id,
            "attendee_count": len(self._repository.attendee_ids(meeting)),
        }

    def _calendar_row(self, principal: Principal, meeting: Any) -> dict[str, Any]:
        """캘린더·관계 그래프·자료 검색이 읽는 행. 화면 계약(`MeetingRow`)보다 이름이 넓다."""
        attendee_ids = sorted(self._repository.attendee_ids(meeting))
        return {
            **self._row(principal, meeting),
            "kind": "meeting",
            "owner_id": meeting.owner_id,
            "attendees": [
                {"member_id": member_id, "display_name": self._repository.member_display_name(member_id) or member_id}
                for member_id in attendee_ids
            ],
        }

    def _detail(self, principal: Principal, meeting: Any) -> dict[str, Any]:
        attendee_ids = sorted(self._repository.attendee_ids(meeting))
        view = self._view_plan(principal, meeting, attendee_ids=set(attendee_ids))
        relation = view.relation
        lines = self._grouped_lines(meeting)
        todos = self._grouped_todos(meeting)
        return {
            "meeting": {
                "meeting_id": str(meeting.id),
                "title": meeting.title,
                "purpose": meeting.purpose,
                "starts_at": _iso(meeting.starts_at),
                "ends_at": _iso(meeting.ends_at),
                "location": meeting.location,
                "status": meeting.status,
                "created_by": meeting.owner_id,
                "attendees": [
                    {"member_id": member_id, "display_name": self._repository.member_display_name(member_id) or member_id}
                    for member_id in attendee_ids
                ],
                "external_attendees": list(meeting.external_attendees or []),
                "viewer_relation": relation,
                # 회의 정보는 참석자 전원이, 회의록 줄과 안건은 만든 사람 하나가 고친다 (SPEC §3.3).
                # 줄과 안건은 열리는 상태가 다르다 — 「예정」은 안건만, 「완료」·「실패」는 둘 다 연다.
                "can_edit_info": view.can_edit_info,
                "can_edit_note": view.can_edit_note,
                "can_edit_agendas": view.can_edit_agendas,
                # 「+ 새 안건」이 서는 자리 — 편집보다 한 자리 넓다(진행 중에도 세운다, D45).
                "can_add_agenda": view.can_add_agenda,
                # 메모는 회의를 만든 사람이 「진행 중」에만 쓴다 (SPEC-004 §6-1·2). 화면이 이 값으로 입력 칸을 세운다.
                "can_write_memo": view.can_write_memo,
                "last_saved_at": _iso(meeting.last_saved_at),
                # `at_ms` 의 기준점. 예정 시각이 아니라 「진행 중」으로 옮긴 실제 시각이다.
                "started_at": _iso(meeting.started_at),
                "carried_from_meeting_id": str(meeting.carried_from_meeting_id) if meeting.carried_from_meeting_id else None,
                # 제목이 비었을 때 합성이 낸 후보. 사람이 머리 편집에서 저장해야 제목이 된다.
                "title_candidate": meeting.title_candidate,
                "failure_reason": meeting.failure_reason,
                # 사옥 회의실 예약의 상태 (SCAX-WP-007). 회의실을 안 고른 회의는 `null` 이다 —
                # **상태·회의실 이름·사유 셋만 나간다**: 외부 식별자도 예약 계정도 화면이 알 일이 아니다.
                "room_reservation": self._reservation_view(meeting),
                # 이 회의록이 어느 원문으로 만들어졌는가 (D44). `realtime` 이면 화면이 안내 한 줄을 세운다.
                "transcript_source": meeting.transcript_source,
            },
            "agendas": [self._agenda_view(agenda, lines, todos) for agenda in self._repository.agendas(meeting)],
        }

    # ------------------------------------------------------------------ 회의실 예약 (SCAX-WP-007)

    @staticmethod
    def _reservation_view(meeting: Any) -> dict[str, Any] | None:
        reservation = RoomReservation.restored(getattr(meeting, "room_reservation", None))
        return None if reservation is None else reservation.view()

    def reservation_draft(
        self,
        principal: Principal,
        *,
        title: str | None,
        starts_at: datetime,
        ends_at: datetime,
        attendee_ids: list[str] | None,
        external_attendees: list[str] | None,
    ) -> dict[str, Any]:
        """**아직 없는 회의**의 예약 입력. 예약을 먼저 하고 성공한 뒤에 회의를 세우기 때문이다 (D36-2).

        참석자 판정은 `create` 와 같은 규칙을 쓴다 — 만든 사람은 언제나 참석자이므로 인원수에 한 번만 센다.
        """
        inside_ids = self._resolved_attendees(principal, list(attendee_ids or []))
        inside = [
            Attendee(
                name=self._repository.member_display_name(member_id) or member_id,
                email=self._repository.member_email(member_id),
            )
            for member_id in inside_ids
        ]
        outside = list(normalize_external_attendees(list(external_attendees or [])))
        return {
            "title": title,
            "starts_at": _aware(starts_at),
            "ends_at": _aware(ends_at),
            "owner_name": self._repository.member_display_name(str(principal.id)) or str(principal.id),
            "inside": inside,
            "outside": outside,
            "people": headcount(inside=len(inside), outside=len(outside)),
        }

    def reservation_input(self, meeting_id: UUID) -> dict[str, Any]:
        """예약 한 건을 세우는 데 필요한 것만. **읽기 트랜잭션에서 끝내고 그 밖에서 예약을 부른다.**"""
        meeting = self._repository.meeting(meeting_id)
        if meeting is None:
            raise MeetingNotFound("meeting was not found")
        inside = [
            Attendee(
                name=self._repository.member_display_name(member_id) or member_id,
                email=self._repository.member_email(member_id),
            )
            for member_id in sorted(self._repository.attendee_ids(meeting) | {meeting.owner_id})
        ]
        return {
            "title": meeting.title,
            "starts_at": _aware(meeting.starts_at),
            "ends_at": _aware(meeting.ends_at),
            "owner_name": self._repository.member_display_name(meeting.owner_id) or meeting.owner_id,
            "inside": inside,
            # 사외 참석자는 이름뿐이다 — 계정이 없으니 표시 문자열로 간다 (rooms.build_reservation).
            "outside": [str(name) for name in (meeting.external_attendees or [])],
            "reservation": RoomReservation.restored(meeting.room_reservation),
        }

    def attach_reservation(self, meeting_id: UUID, reservation: RoomReservation, *, location: str | None) -> None:
        """예약을 부른 **뒤** 그 결과를 회의에 붙인다. 실패도 붙인다 — 화면이 사유를 읽어야 한다."""
        meeting = self._repository.meeting(meeting_id, lock=True)
        if meeting is None:
            raise MeetingNotFound("meeting was not found")
        meeting.room_reservation = reservation.stored()
        # 자리를 못 잡았으면 장소를 비운다 — 잡히지도 않은 방 이름이 회의에 적혀 있으면 안 된다.
        meeting.location = location
        self._repository.touch(meeting)
        self._repository.append_audit(
            meeting,
            meeting.owner_id,
            f"meeting.room_{reservation.status}",
            f"회의실 예약 {reservation.status}: {reservation.room_name or reservation.reason or ''}".strip(),
        )

    @staticmethod
    def _todo_view(todo: Any) -> dict[str, Any]:
        """SCAX-SPEC-004 §8.1의 여덟 값. 담당자 칸은 없다 — AI가 고르지 않는다.

        `linked`는 승격 전에 `null`이고, 승격하면 `work_request_id`가, 상대가 수락하면 `task_id`가 함께 찬다.
        """
        reference = dict(todo.reference or {})
        return {
            "todo_id": str(todo.id),
            "agenda_id": str(todo.agenda_id),
            "title": todo.title,
            "description": todo.description or "",
            "due_candidate": todo.due_candidate.isoformat() if todo.due_candidate else None,
            "checklist_candidate": list(todo.checklist_candidate or []),
            # 회의 중 배치가 낸 후보인가 (D46). 참이면 읽기 전용이고 다음 배치가 갈아 끼운다.
            "provisional": bool(getattr(todo, "provisional", False)),
            "reference": {
                "meeting_id": str(reference.get("meeting_id") or todo.meeting_id),
                "agenda_id": str(reference.get("agenda_id") or todo.agenda_id),
                "line_ids": [str(line_id) for line_id in reference.get("line_ids") or []],
            },
            "linked": (
                {
                    "work_request_id": str(todo.linked_work_request_id),
                    "task_id": str(todo.linked_task_id) if todo.linked_task_id else None,
                }
                if todo.linked_work_request_id
                else None
            ),
        }

    def _grouped_lines(self, meeting: Any) -> dict[Any, list[Any]]:
        grouped: dict[Any, list[Any]] = {}
        for line in self._repository.lines(meeting):
            grouped.setdefault(line.agenda_id, []).append(line)
        return grouped

    def _grouped_todos(self, meeting: Any) -> dict[Any, list[Any]]:
        grouped: dict[Any, list[Any]] = {}
        for todo in self._repository.todos(meeting):
            grouped.setdefault(todo.agenda_id, []).append(todo)
        return grouped

    @staticmethod
    def _line_view(line: Any) -> dict[str, Any]:
        """줄 하나. `at_ms` 는 메모 줄에만 값이 있다 — AI·합성 줄은 시각이 아니라 구간에 걸린다."""
        return {
            "line_id": str(line.id),
            "track": line.track,
            "order": line.order_index,
            "text": line.text,
            "author": line.author_id,
            "at_ms": line.at_ms,
            "evidence": [_evidence_span(span) for span in line.evidence or []],
        }

    def _agenda_view(self, agenda: Any, lines: dict[Any, list[Any]], todos: dict[Any, list[Any]]) -> dict[str, Any]:
        return {
            "agenda_id": str(agenda.id),
            # 이 안건의 마지막 저장 시각. 다음 저장이 이 값을 함께 보내 「그 사이에 누가 저장했나」를 가른다.
            "last_saved_at": _iso(agenda.updated_at),
            "order": agenda.order_index,
            "title": agenda.title,
            "source": agenda.source,
            "concluded": bool(agenda.concluded),
            # 제목이 아직 자리표시인가 (D6) — 화면이 그 자리를 다르게 그릴 근거다.
            "title_placeholder": bool(getattr(agenda, "title_placeholder", False)),
            "lines": [self._line_view(line) for line in lines.get(agenda.id, [])],
            "todos": [self._todo_view(todo) for todo in todos.get(agenda.id, [])],
        }

    @staticmethod
    def _require(principal: Principal, capability: str) -> None:
        if capability not in principal.capabilities:
            raise MeetingAccessDenied(f"{capability} capability is required")

def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    # SQLite drops timezone offsets in fast contract tests; public meeting transport is always UTC.
    return (value if value.tzinfo is not None else value.replace(tzinfo=UTC)).isoformat()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _place_of(row: dict[str, Any]) -> str:
    return f"{row['starts_at']}|{row['meeting_id']}"


def _cursor_of(row: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(_place_of(row).encode()).decode().rstrip("=")


def _page(rows: list[dict[str, Any]], cursor: str | None, page_size: int) -> tuple[list[dict[str, Any]], str | None]:
    """커서는 마지막으로 낸 행의 자리다 — 그 행 다음부터 한 판을 더 낸다."""
    start = 0
    if cursor:
        try:
            marker = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        except Exception as error:  # noqa: BLE001 — a malformed cursor is a caller mistake, not a server fault
            raise MeetingError("meeting list cursor is malformed") from error
        start = next((index + 1 for index, row in enumerate(rows) if _place_of(row) == marker), len(rows))
    page = rows[start : start + page_size]
    has_more = start + page_size < len(rows)
    return page, _cursor_of(page[-1]) if page and has_more else None


def _evidence_span(span: dict[str, Any]) -> dict[str, int]:
    """근거 구간 하나를 계약의 이름으로 낸다 — 화면 타임칩이 읽는 것은 `start_ms`·`end_ms` 다 (SPEC-004 §4.1).

    AI 출력 스키마는 안에서 `from_ms`·`to_ms` 로 말한다. 그 이름이 응답으로 새면 칩이 시각을 못 읽는다 —
    저장은 받은 그대로 두고 **나가는 자리에서 한 번** 옮긴다.
    """
    start = span.get("start_ms", span.get("from_ms"))
    end = span.get("end_ms", span.get("to_ms"))
    return {"start_ms": int(start or 0), "end_ms": int(end or 0)}


def _track_view(grouped: dict[Any, list[Any]], track: str) -> list[dict[str, Any]]:
    """한 트랙의 줄 전량 — 합성 입력이 읽는 모양이다."""
    rows: list[dict[str, Any]] = []
    for agenda_id, lines in grouped.items():
        for line in lines:
            if line.track != track:
                continue
            rows.append(
                {
                    "line_id": str(line.id),
                    "agenda_id": str(agenda_id),
                    "text": line.text,
                    "at_ms": line.at_ms,
                }
            )
    return rows
