from datetime import date

import pytest

from ax_workspace.modules.work.request_lifecycle import (
    OpenWorkRequestRound,
    RequestCreationContext,
    ReviseWorkRequest,
    SYSTEM_MEETING_REQUESTER,
    WithdrawWorkRequest,
    WorkRequest,
    decide_request_creation,
    revise_work_request,
    withdraw_work_request,
)
from ax_workspace.modules.work.request_errors import WorkRequestAccessDenied, WorkRequestError


def _request(**changes: object) -> WorkRequest:
    values: dict[str, object] = {
        "id": "request-1",
        "assignee_id": "jiho",
        "state": "pending",
        "version": 3,
        "requester_id": "mina",
        "promoted_by_member_id": None,
        "title": "분기 자료 요청",
        "description": "초안",
        "due_date": date(2026, 10, 1),
    }
    values.update(changes)
    return WorkRequest(**values)  # type: ignore[arg-type]


def _revise(request: WorkRequest, **changes: object):
    values: dict[str, object] = {
        "actor_id": "mina",
        "expected_version": request.version,
        "mode": "amend",
        "title": None,
        "description": None,
        "due_date": None,
        "clear_due_date": False,
        "exact_replay": False,
    }
    values.update(changes)
    return revise_work_request(request, ReviseWorkRequest(**values))  # type: ignore[arg-type]


def test_amendment_returns_a_new_request_and_the_round_to_open() -> None:
    original = _request()

    revision = _revise(
        original,
        title="  분기 자료 보강 요청  ",
        description="   ",
        clear_due_date=True,
    )

    assert original.title == "분기 자료 요청"
    assert revision.request == _request(
        title="분기 자료 보강 요청",
        description=None,
        due_date=None,
        version=4,
    )
    assert revision.replay is False
    assert revision.clear_conditions is False
    assert revision.open_round == OpenWorkRequestRound(
        title="분기 자료 보강 요청",
        description=None,
        due_date=None,
        assignee_id="jiho",
    )


def test_revision_refuses_blank_titles_and_rounds_that_change_nothing() -> None:
    with pytest.raises(WorkRequestError, match="title is required"):
        _revise(_request(), title="   ")
    with pytest.raises(WorkRequestError, match="must change something"):
        _revise(
            _request(),
            title="분기 자료 요청",
            description="초안",
            due_date=date(2026, 10, 1),
        )


def test_amendment_preconditions_are_domain_rules() -> None:
    cases = [
        (_request(requester_id="jiho"), "only the requester may amend", WorkRequestAccessDenied),
        (_request(state="negotiating"), "판단하고 있는 요청만 수정", WorkRequestError),
        (_request(version=4), "version is stale", WorkRequestError),
    ]
    for request, message, error in cases:
        with pytest.raises(error, match=message):
            _revise(request, expected_version=3, title="바뀐 요청")


def test_exact_amendment_retry_returns_the_unchanged_receipt_without_an_effect() -> None:
    original = _request(version=4)

    revision = _revise(
        original,
        expected_version=3,
        title="분기 자료 보강 요청",
        exact_replay=True,
    )

    assert revision.replay is True
    assert revision.request is original
    assert revision.open_round is None
    assert revision.clear_conditions is False


def test_resubmission_reopens_a_negotiating_request_and_clears_conditions() -> None:
    revision = _revise(
        _request(state="negotiating"),
        mode="resubmit",
        title="합의한 요청",
    )

    assert revision.request.state == "pending"
    assert revision.request.version == 4
    assert revision.request.title == "합의한 요청"
    assert revision.clear_conditions is True
    assert revision.open_round is not None


def test_withdrawal_is_available_only_to_the_requester_while_open() -> None:
    withdrawn = withdraw_work_request(
        _request(state="negotiating"),
        WithdrawWorkRequest(actor_id="mina", expected_version=3),
    )
    assert (withdrawn.request.state, withdrawn.request.version) == ("withdrawn", 4)
    assert withdrawn.clear_conditions is True

    cases = [
        (_request(requester_id="jiho"), 3, "only the requester may withdraw"),
        (_request(version=4), 3, "version is stale"),
        (_request(state="accepted"), 3, "only an open work request"),
    ]
    for request, version, message in cases:
        with pytest.raises(WorkRequestError, match=message):
            withdraw_work_request(request, WithdrawWorkRequest(actor_id="mina", expected_version=version))


def test_meeting_promoter_stands_in_the_system_requester_role() -> None:
    request = _request(requester_id="system", promoted_by_member_id="mina", state="negotiating")

    revised = _revise(request, mode="resubmit", title="승격 요청 수정")
    withdrawn = withdraw_work_request(request, WithdrawWorkRequest(actor_id="mina", expected_version=3))

    assert revised.request.version == 4
    assert withdrawn.request.state == "withdrawn"


def test_ordinary_request_creation_keeps_scoped_assignee_and_active_cc_checks() -> None:
    decision = decide_request_creation(
        RequestCreationContext(
            actor_id="mina",
            title="  계약 검토 요청  ",
            description="  초안을 먼저 봐주세요.  ",
            assignee_id="jiho",
            cc_member_ids=("mina", "jiho", "sora", "sora", "hyeon"),
            allow_self_assignment=False,
            promoted_by_member_id=None,
        )
    )

    assert decision.requester_id == "mina"
    assert decision.title == "계약 검토 요청"
    assert decision.description == "초안을 먼저 봐주세요."
    assert decision.assignee_validation == "scoped"
    assert decision.cc_member_ids == ("sora", "hyeon")
    assert decision.active_member_checks == ("sora", "hyeon")


def test_meeting_promotion_is_a_system_request_with_global_assignee_and_promoter_visibility() -> None:
    external = decide_request_creation(
        RequestCreationContext(
            actor_id="mina",
            title="후속 조치",
            description="   ",
            assignee_id="sora",
            cc_member_ids=("hyeon",),
            allow_self_assignment=True,
            promoted_by_member_id="mina",
        )
    )
    self_assigned = decide_request_creation(
        RequestCreationContext(
            actor_id="mina",
            title="내가 맡을 일",
            description=None,
            assignee_id="mina",
            cc_member_ids=(),
            allow_self_assignment=True,
            promoted_by_member_id="mina",
        )
    )

    assert external.requester_id == SYSTEM_MEETING_REQUESTER
    assert external.assignee_validation == "active"
    assert external.cc_member_ids == ("hyeon", "mina")
    assert external.active_member_checks == ("hyeon",)
    assert external.description is None
    assert self_assigned.assignee_validation == "none"
    assert self_assigned.cc_member_ids == ()


def test_request_creation_rejects_an_empty_title_before_any_directory_or_repository_work() -> None:
    with pytest.raises(WorkRequestError, match="title is required"):
        decide_request_creation(
            RequestCreationContext(
                actor_id="mina",
                title="   ",
                description=None,
                assignee_id="jiho",
                cc_member_ids=(),
                allow_self_assignment=False,
                promoted_by_member_id=None,
            )
        )
