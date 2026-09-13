from dataclasses import replace

from ax_workspace.modules.actions.replay import (
    AssignmentReplayContext,
    RecordedDeliveryDecision,
    WorkRequestReplayContext,
    is_assignment_replay,
    is_delivery_replay,
    is_work_request_replay,
)


def test_work_request_replay_is_pinned_to_actor_version_decision_and_payload() -> None:
    facts = WorkRequestReplayContext(
        request_state="negotiating",
        request_version=5,
        requester_id="mina",
        submission_actor_id="mina",
        revision_consumed_version=None,
        current_snapshot={},
        previous_snapshot=None,
        decision_actor_id="jiho",
        decision="negotiate",
        decision_expected_version=4,
        decision_reason="기한을 늦춰 주세요",
        decision_suggested_changes={"due_date": "2026-12-01"},
    )
    payload = {
        "expected_version": 4,
        "reason": " 기한을 늦춰 주세요 ",
        "changes": {"due_date": "2026-12-01"},
    }

    assert is_work_request_replay(facts, actor_id="jiho", command="adjust", payload=payload)
    for candidate, actor, command, changed_payload in (
        (replace(facts, decision_expected_version=3), "jiho", "adjust", payload),
        (facts, "mina", "adjust", payload),
        (facts, "jiho", "reject", payload),
        (facts, "jiho", "adjust", {**payload, "reason": "다른 사유"}),
        (facts, "jiho", "adjust", {**payload, "changes": {"title": "다른 제안"}}),
    ):
        assert not is_work_request_replay(candidate, actor_id=actor, command=command, payload=changed_payload)


def test_revision_replay_reconstructs_the_exact_round_it_produced() -> None:
    previous = {"title": "원안", "description": "설명", "due_date": "2026-10-01", "assignee_id": "jiho"}
    current = {"title": "수정안", "description": None, "due_date": None, "assignee_id": "jiho"}
    facts = WorkRequestReplayContext(
        request_state="pending",
        request_version=7,
        requester_id="mina",
        submission_actor_id="mina",
        revision_consumed_version=5,
        current_snapshot=current,
        previous_snapshot=previous,
        decision_actor_id=None,
        decision=None,
        decision_expected_version=None,
        decision_reason=None,
        decision_suggested_changes={},
    )
    payload = {
        "expected_version": 5,
        "changes": {"title": " 수정안 ", "description": " ", "clear_due_date": True},
    }

    assert is_work_request_replay(facts, actor_id="mina", command="revise", payload=payload)
    assert not is_work_request_replay(
        facts, actor_id="mina", command="revise", payload={**payload, "expected_version": 6}
    )
    assert not is_work_request_replay(
        facts, actor_id="mina", command="revise",
        payload={**payload, "changes": {"title": "보내지 않은 수정"}},
    )


def test_assignment_replay_uses_the_task_version_relation_and_decline_reason() -> None:
    accepted = AssignmentReplayContext(status="active", assignee_id="mina", task_version=4, decline_reason=None)
    declined = AssignmentReplayContext(status="declined", assignee_id="mina", task_version=5, decline_reason="여력 없음")

    assert is_assignment_replay(accepted, actor_id="mina", command="accept", expected_version=4, reason=None)
    assert is_assignment_replay(declined, actor_id="mina", command="decline", expected_version=4, reason=" 여력 없음 ")
    assert not is_assignment_replay(declined, actor_id="mina", command="decline", expected_version=5, reason="여력 없음")
    assert not is_assignment_replay(declined, actor_id="mina", command="decline", expected_version=4, reason="다른 사유")


def test_assignment_replay_prefers_the_recorded_decision_after_the_task_changes() -> None:
    accepted = AssignmentReplayContext(
        status="active",
        assignee_id="mina",
        task_version=7,
        decline_reason=None,
        has_recorded_decision=True,
        decision_actor_id="mina",
        decision="accept",
        decision_consumed_version=4,
        decision_reason=None,
    )

    assert is_assignment_replay(accepted, actor_id="mina", command="accept", expected_version=4, reason=None)
    assert not is_assignment_replay(accepted, actor_id="mina", command="accept", expected_version=7, reason=None)


def test_delivery_replay_matches_the_recorded_answer_not_the_current_task_state() -> None:
    decisions = (
        RecordedDeliveryDecision(actor_id="mina", decision="negotiate", expected_version=8, reason="보강 필요"),
    )

    assert is_delivery_replay(decisions, actor_id="mina", command="request_changes", expected_version=8, reason="보강 필요")
    assert not is_delivery_replay(decisions, actor_id="mina", command="request_changes", expected_version=9, reason="보강 필요")
    assert not is_delivery_replay(decisions, actor_id="mina", command="request_changes", expected_version=8, reason="다른 사유")
