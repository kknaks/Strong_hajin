from datetime import UTC, datetime, timedelta

from ax_workspace.modules.meetings.domain import MeetingError, MeetingStateConflict, MeetingStatus
from ax_workspace.modules.meetings.policy import (
    MeetingActorContext,
    MeetingViewContext,
    normalize_final_line_rows,
    normalize_memo_text,
    ensure_todo_actionable,
    ensure_share_revocable,
    meeting_attendees,
    new_share_targets,
    needs_auto_settlement,
    auto_settled_status,
    project_meeting_view,
)
from ax_workspace.modules.meetings.material_policy import (
    MeetingMaterialContext,
    decide_material_access,
)
import pytest


NOW = datetime(2026, 9, 13, 3, 0, tzinfo=UTC)


def _meeting(**changes) -> MeetingViewContext:
    values = {
        "status": MeetingStatus.SCHEDULED,
        "owner_id": "mina",
        "organization_id": "scax",
        "attendee_ids": frozenset({"mina", "jiho"}),
        "shared_member_ids": frozenset({"sora"}),
        "ends_at": NOW + timedelta(hours=1),
    }
    values.update(changes)
    return MeetingViewContext(**values)


def _actor(member_id: str, *, read: bool = True, private: bool = False, scope=("scax",)) -> MeetingActorContext:
    return MeetingActorContext(
        member_id=member_id,
        can_read=read,
        can_read_private=private,
        organization_scope=frozenset(scope),
    )


def test_detail_visibility_has_only_attendee_and_explicit_share_axes() -> None:
    attendee = project_meeting_view(_meeting(), _actor("jiho"), now=NOW)
    shared = project_meeting_view(_meeting(), _actor("sora"), now=NOW)
    executive = project_meeting_view(_meeting(), _actor("yuna", private=True), now=NOW)
    revoked = project_meeting_view(_meeting(), _actor("jiho", read=False), now=NOW)

    assert (attendee.detail_readable, attendee.relation) == (True, "attendee")
    assert (shared.detail_readable, shared.relation) == (True, "shared")
    assert executive.detail_readable is False
    assert executive.calendar_detail_readable is True
    assert revoked.detail_readable is False


def test_private_calendar_authority_stops_at_the_organization_boundary() -> None:
    inside = project_meeting_view(_meeting(), _actor("yuna", private=True), now=NOW)
    outside = project_meeting_view(_meeting(), _actor("yuna", private=True, scope=("other",)), now=NOW)

    assert inside.calendar_detail_readable is True
    assert outside.calendar_detail_readable is False


def test_controls_belong_to_role_and_status_not_to_the_transport() -> None:
    scheduled_owner = project_meeting_view(_meeting(), _actor("mina"), now=NOW)
    scheduled_attendee = project_meeting_view(_meeting(), _actor("jiho"), now=NOW)
    running_owner = project_meeting_view(
        _meeting(status=MeetingStatus.IN_PROGRESS), _actor("mina"), now=NOW
    )
    done_owner = project_meeting_view(_meeting(status=MeetingStatus.DONE), _actor("mina"), now=NOW)
    shared = project_meeting_view(_meeting(), _actor("sora"), now=NOW)

    assert scheduled_owner.can_edit_info and scheduled_owner.can_edit_agendas.memo
    assert scheduled_owner.can_add_agenda.memo and not scheduled_owner.can_write_memo
    assert scheduled_attendee.can_edit_info and not scheduled_attendee.can_edit_agendas.any()
    assert running_owner.can_add_agenda.memo and running_owner.can_write_memo
    # **진행 중에도 사람 벌은 고치고 지운다** — 임시 재료이므로 임시로 다룬다
    # (사용자 결정 「최종 회의록만 회의록이다」 2026-09-14).
    assert running_owner.can_edit_agendas.memo and not running_owner.can_edit_info
    assert done_owner.can_edit_info and done_owner.can_edit_note and done_owner.can_edit_agendas.final
    assert not shared.can_edit_info and not shared.can_edit_note


def test_agenda_gates_are_three_verdicts_and_the_ai_track_is_never_open() -> None:
    """**벌별 판정 셋** (SPEC-004 v0.5.1 §4.1-6). 불리언 하나로는 「종료에서 최종 벌은 열리고 사람 벌은
    닫힌다」를 낼 수 없다 — 그 한 문장이 D-4(원본 불가침)가 지키는 것이다.
    """

    def gates(status: MeetingStatus):
        view = project_meeting_view(_meeting(status=status), _actor("mina"), now=NOW)
        return view.can_add_agenda.as_dict(), view.can_edit_agendas.as_dict()

    add, edit = gates(MeetingStatus.SCHEDULED)
    assert add == {"memo": True, "ai": False, "final": False}
    assert edit == {"memo": True, "ai": False, "final": False}

    # **「진행 중」에도 사람 벌은 더하고 고치고 지운다** (사용자 결정 2026-09-14 §바뀌는 것 1).
    # 사람 벌은 최종 회의록을 지을 **임시 재료**이므로, 오타로 세운 안건이 회의가 끝날 때까지
    # 박제되면 안 된다. 0.4.x 의 「진행 중에는 이미 선 안건을 손대지 않는다」는 원본이 최종본처럼
    # 잠겨 있던 때의 규칙이었다.
    add, edit = gates(MeetingStatus.IN_PROGRESS)
    assert add == {"memo": True, "ai": False, "final": False}
    assert edit == {"memo": True, "ai": False, "final": False}

    # 정리가 도는 동안에는 **어느 벌도** 열리지 않는다.
    add, edit = gates(MeetingStatus.SUMMARIZING)
    assert add == edit == {"memo": False, "ai": False, "final": False}

    # 「종료」·「실패」는 **최종 벌만** 열린다 — 원본 두 벌은 읽기 전용이다 (D53).
    for closed in (MeetingStatus.DONE, MeetingStatus.FAILED):
        add, edit = gates(closed)
        assert add == {"memo": False, "ai": False, "final": True}
        assert edit == {"memo": False, "ai": False, "final": True}

    # `ai` 는 어느 상태에서도 거짓이다. **키는 언제나 낸다** — 화면이 세 벌을 같은 모양으로 묻는다.
    for status in MeetingStatus:
        add, edit = gates(status)
        assert add["ai"] is False and edit["ai"] is False
        assert set(add) == set(edit) == {"memo", "ai", "final"}


def test_board_places_shared_closed_and_elapsed_meetings_in_the_past() -> None:
    assert project_meeting_view(_meeting(), _actor("sora"), now=NOW).past is True
    assert project_meeting_view(_meeting(status=MeetingStatus.FAILED), _actor("mina"), now=NOW).past is True
    assert project_meeting_view(
        _meeting(ends_at=NOW - timedelta(seconds=1)), _actor("mina"), now=NOW
    ).past is True
    assert project_meeting_view(_meeting(), _actor("mina"), now=NOW).past is False


def test_auto_settlement_cancels_only_an_empty_meeting_that_was_scheduled_in_advance() -> None:
    assert needs_auto_settlement(MeetingStatus.SCHEDULED) is True
    assert needs_auto_settlement(MeetingStatus.CANCELLED) is True
    assert needs_auto_settlement(MeetingStatus.IN_PROGRESS) is False
    assert auto_settled_status(
        MeetingStatus.SCHEDULED,
        ends_at=NOW - timedelta(hours=1),
        created_at=NOW - timedelta(days=1),
        has_record=False,
        now=NOW,
    ) is MeetingStatus.CANCELLED
    assert auto_settled_status(
        MeetingStatus.CANCELLED,
        ends_at=NOW - timedelta(hours=1),
        created_at=NOW - timedelta(days=1),
        has_record=True,
        now=NOW,
    ) is MeetingStatus.SCHEDULED
    assert auto_settled_status(
        MeetingStatus.SCHEDULED,
        ends_at=NOW - timedelta(hours=1),
        created_at=NOW,
        has_record=False,
        now=NOW,
    ) is MeetingStatus.SCHEDULED
    assert auto_settled_status(
        MeetingStatus.IN_PROGRESS,
        ends_at=NOW - timedelta(hours=1),
        created_at=NOW - timedelta(days=1),
        has_record=False,
        now=NOW,
    ) is MeetingStatus.IN_PROGRESS


def test_memo_text_is_trimmed_and_rejects_empty_or_oversized_content() -> None:
    assert normalize_memo_text("  결정 사항  ") == "결정 사항"

    with pytest.raises(MeetingError, match="needs text"):
        normalize_memo_text("   ")
    with pytest.raises(MeetingError, match="at most 2000"):
        normalize_memo_text("가" * 2001)


def test_final_line_rows_carry_their_id_and_drop_empty_sentences() -> None:
    """최종 벌 저장은 **줄마다 id 를 싣는다** (SPEC-004 v0.5.1 §8-9 · 검수 F-3).

    id 없이 글자만 보내면 서버는 어느 줄이 그대로인지 알 수 없고, 손대지 않은 줄의 계보까지 첫 저장에
    사라진다 — 그래서 글자 목록은 받지 않는다.
    """
    rows = normalize_final_line_rows(
        [{"line_id": "line-1", "text": "  첫 줄  "}, {"text": " "}, {"line_id": None, "text": "둘째 줄"}]
    )
    assert [(row.line_id, row.text) for row in rows] == [("line-1", "첫 줄"), (None, "둘째 줄")]

    with pytest.raises(MeetingError, match="line_id"):
        normalize_final_line_rows(["글자 하나"])
    with pytest.raises(MeetingError, match="list of"):
        normalize_final_line_rows("한 줄")
    with pytest.raises(MeetingError, match="at most 2000"):
        normalize_final_line_rows([{"text": "가" * 2001}])


def test_material_controls_belong_to_attendance_uploader_and_meeting_status() -> None:
    def access(status: MeetingStatus, *, attendee: bool, actor: str = "mina"):
        return decide_material_access(
            MeetingMaterialContext(
                status=status,
                actor_is_attendee=attendee,
                actor_id=actor,
                uploaded_by="mina",
            )
        )

    scheduled_owner = access(MeetingStatus.SCHEDULED, attendee=True)
    scheduled_other = access(MeetingStatus.SCHEDULED, attendee=True, actor="jiho")
    running_owner = access(MeetingStatus.IN_PROGRESS, attendee=True)
    done_owner = access(MeetingStatus.DONE, attendee=True)
    shared_uploader = access(MeetingStatus.SCHEDULED, attendee=False)

    assert scheduled_owner.can_attach and scheduled_owner.can_detach
    assert scheduled_other.can_attach and not scheduled_other.can_detach
    assert not running_owner.can_attach and not running_owner.can_detach
    assert done_owner.can_attach and not done_owner.can_detach
    assert not shared_uploader.can_attach and not shared_uploader.can_detach


def test_follow_up_candidate_actions_depend_only_on_settlement_and_existing_link() -> None:
    ensure_todo_actionable(provisional=False, linked_work_request_id=None, action="promote")
    ensure_todo_actionable(provisional=False, linked_work_request_id=None, action="delete")

    for action in ("promote", "delete"):
        with pytest.raises(MeetingStateConflict, match="todo_provisional"):
            ensure_todo_actionable(provisional=True, linked_work_request_id=None, action=action)

    with pytest.raises(MeetingStateConflict, match="already been requested"):
        ensure_todo_actionable(provisional=False, linked_work_request_id="request-1", action="promote")
    with pytest.raises(MeetingStateConflict, match="not deleted"):
        ensure_todo_actionable(provisional=False, linked_work_request_id="request-1", action="delete")


def test_attendance_and_share_plans_deduplicate_people_without_weakening_attendance() -> None:
    attendees = meeting_attendees([" jiho ", "mina", "jiho", ""], owner_id="mina")
    targets = new_share_targets([" sora ", "jiho", "sora", "hyeon"], existing_member_ids=attendees)

    assert attendees == ("jiho", "mina")
    assert targets == ("sora", "hyeon")
    ensure_share_revocable("sora", attendee_ids=attendees)
    with pytest.raises(MeetingStateConflict, match="attendance is not revoked"):
        ensure_share_revocable("jiho", attendee_ids=attendees)
