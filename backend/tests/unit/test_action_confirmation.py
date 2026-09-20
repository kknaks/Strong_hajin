from dataclasses import replace
from uuid import UUID

import pytest

from ax_workspace.modules.actions.confirmation import (
    AxConfirmationContext,
    AxReplayContext,
    OpenAxSubmissionRound,
    decide_ax_confirmation,
    is_ax_replay,
)
from ax_workspace.modules.actions.domain import ActionError


def _task_draft(**changes):
    return {
        "title": "분기 보고",
        "description": None,
        "start_date": None,
        "due_date": "2026-09-30",
        "checklist": [],
        "reference_task_ids": [],
        "parent_task_id": None,
        "project_id": None,
        # 참조자 — 내 업무와 업무 요청이 함께 쓰는 공통 payload 의 칸. 초안 정규화가 같은 집합을 쓴다.
        "cc_member_ids": [],
        # 선행업무 — 같은 공통 payload 의 칸. 생성 표면 전부가 같은 배열을 받는다 (SPEC-001 §5).
        "preceding_task_ids": [],
        # 결재자 — `업무` 갈래만 여는 칸 (SPEC-001 §7 OQ-M).
        "approver_id": None,
        # 담당 — W1 이 생성 입력에 연 필드. 초안 정규화가 같은 집합을 쓴다.
        "assignee_id": None,
        **changes,
    }


def _context(**changes) -> AxConfirmationContext:
    values = {
        "action_type": "task.create_self",
        "state": "pending",
        "version": 4,
        "owner_id": "mina",
        "decision_open": True,
        "submission_version": 1,
        "base_snapshot": _task_draft(),
        "has_active_assignment": True,
    }
    values.update(changes)
    return AxConfirmationContext(**values)


def test_an_unchanged_confirmation_keeps_the_original_submission() -> None:
    decision = decide_ax_confirmation(
        _context(),
        {
            "expected_version": 4,
            "base_submission_version": 1,
            "draft": _task_draft(title="  분기 보고  "),
        },
    )

    assert decision.open_round is None
    assert decision.final_snapshot == _task_draft()
    assert decision.attachment_draft_ids == ()


def test_an_edited_confirmation_requests_one_new_submission_with_its_exact_diff() -> None:
    attachment_id = UUID("00000000-0000-0000-0000-000000000501")

    decision = decide_ax_confirmation(
        _context(has_active_assignment=False),
        {
            "expected_version": 4,
            "base_submission_version": 1,
            "draft": _task_draft(description="수치까지 포함"),
            "attachment_draft_ids": [attachment_id, str(attachment_id)],
        },
    )

    assert decision.attachment_draft_ids == (str(attachment_id),)
    assert decision.open_round == OpenAxSubmissionRound(
        submission_version=2,
        snapshot={
            **_task_draft(description="수치까지 포함"),
            "attachment_draft_ids": [str(attachment_id)],
        },
        diff={
            "attachment_draft_ids": {"before": None, "after": [str(attachment_id)]},
            "description": {"before": None, "after": "수치까지 포함"},
        },
    )


def test_confirmation_preconditions_fail_before_any_effect_is_returned() -> None:
    context = _context()
    payload = {"expected_version": 4, "base_submission_version": 1, "draft": _task_draft()}
    cases = [
        (replace(context, state="approved"), payload, "action is no longer pending"),
        (context, {key: value for key, value in payload.items() if key != "expected_version"}, "expected_version is required"),
        (context, {**payload, "expected_version": 3}, "action version is stale"),
        (
            context,
            {key: value for key, value in payload.items() if key != "base_submission_version"},
            "base_submission_version is required",
        ),
        (context, {**payload, "base_submission_version": 2}, "base submission version is stale"),
        (replace(context, has_active_assignment=False), payload, "active AX review assignment was not found"),
    ]

    for candidate, command, message in cases:
        with pytest.raises(ActionError, match=message):
            decide_ax_confirmation(candidate, command)


def test_only_the_exact_confirmed_payload_is_a_replay_receipt() -> None:
    normalized = {
        "expected_version": 4,
        "base_submission_version": 1,
        "draft": _task_draft(),
        "attachment_draft_ids": [],
    }
    context = AxReplayContext(
        state="approved",
        version=5,
        has_decision_item=True,
        stored_actor_id="mina",
        stored_decision="confirm",
        stored_conditions={
            "expected_version": 4,
            "base_submission_version": 1,
            "payload_hash": "70b69591b85bc49b9c757a14418cc4b2dd8f1df83206b6c3ed3e3294ea116d7a",
            "attachment_draft_ids": [],
        },
    )

    assert is_ax_replay(context, actor_id="mina", command="confirm", normalized_payload=normalized) is True
    for candidate, actor, command in (
        (replace(context, version=6), "mina", "confirm"),
        (replace(context, stored_actor_id="jiho"), "mina", "confirm"),
        (replace(context, stored_decision="reject"), "mina", "confirm"),
        (context, "mina", "reject"),
    ):
        assert is_ax_replay(candidate, actor_id=actor, command=command, normalized_payload=normalized) is False
