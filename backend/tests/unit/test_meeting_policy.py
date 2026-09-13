from datetime import UTC, datetime, timedelta

from ax_workspace.modules.meetings.domain import MeetingError, MeetingStateConflict, MeetingStatus
from ax_workspace.modules.meetings.policy import (
    MeetingActorContext,
    MeetingViewContext,
    normalize_memo_text,
    normalize_note_lines,
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

    assert scheduled_owner.can_edit_info and scheduled_owner.can_edit_agendas
    assert scheduled_owner.can_add_agenda and not scheduled_owner.can_write_memo
    assert scheduled_attendee.can_edit_info and not scheduled_attendee.can_edit_agendas
    assert running_owner.can_add_agenda and running_owner.can_write_memo
    assert not running_owner.can_edit_info and not running_owner.can_edit_agendas
    assert done_owner.can_edit_info and done_owner.can_edit_note and done_owner.can_edit_agendas
    assert not shared.can_edit_info and not shared.can_edit_note


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


def test_note_lines_keep_meaningful_trimmed_sentences_in_order() -> None:
    assert normalize_note_lines(["  첫 줄  ", " ", None, "둘째 줄"]) == ("첫 줄", "둘째 줄")

    with pytest.raises(MeetingError, match="list of sentences"):
        normalize_note_lines("한 줄")
    with pytest.raises(MeetingError, match="at most 2000"):
        normalize_note_lines(["가" * 2001])


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
