"""Pure command policy for every ActionItem kind."""

from __future__ import annotations

from dataclasses import dataclass

from ax_workspace.modules.actions.domain import AWAITING_REVIEW, AWAITING_REVISION, ActionCommand
from ax_workspace.modules.ax_execution.command_contracts import COMMAND_CONTRACTS
from ax_workspace.modules.organization_access.domain import (
    ACTION_DECIDE,
    TASK_ASSIGN,
    TASK_READ,
    TASK_SELF_MANAGE,
    WORK_REQUEST_DECIDE,
    Principal,
)


CONFIRM_LABELS = {
    **{action_type: "이 내용으로 반영" for action_type in COMMAND_CONTRACTS},
    "task.create_self": "이 내용으로 업무 생성",
    "task.assign": "이 내용으로 업무 요청",
    "work_request.create": "이 내용으로 업무 요청",
    "meeting.reservation.create": "이 내용으로 회의 생성",
    "task.progress.batch": "이 내용으로 반영",
}

#: Action types whose execution path was withdrawn, and the reason a person sees instead.
#: Rows left behind stay readable in history; nothing proposes them and nothing runs them again.
RETIRED_ACTION_TYPES = {
    "meeting.create": "옛 회의 생성 제안은 더 이상 실행할 수 없습니다. 회의 화면에서 새로 예약해 주세요",
}


@dataclass(frozen=True, slots=True)
class AssignmentActionContext:
    pending: bool
    assignee_id: str


def available_assignment_commands(
    context: AssignmentActionContext,
    principal: Principal,
) -> list[ActionCommand]:
    if not (
        context.pending
        and context.assignee_id == str(principal.id)
        and TASK_SELF_MANAGE in principal.capabilities
    ):
        return []
    return [
        ActionCommand("accept", "수락", "primary"),
        ActionCommand("decline", "거절", "danger", requires_reason=True),
    ]


@dataclass(frozen=True, slots=True)
class DeliveryActionContext:
    waiting: bool
    reviewer_id: str


def available_delivery_commands(context: DeliveryActionContext, principal: Principal) -> list[ActionCommand]:
    if not (
        context.waiting
        and context.reviewer_id == str(principal.id)
        and TASK_READ in principal.capabilities
    ):
        return []
    return [
        ActionCommand("accept", "완료 인정", "primary"),
        ActionCommand("request_changes", "보완 요청", "neutral", requires_reason=True),
    ]


@dataclass(frozen=True, slots=True)
class WorkRequestActionContext:
    status: str
    actor_id: str | None


def available_work_request_commands(
    context: WorkRequestActionContext,
    principal: Principal,
) -> list[ActionCommand]:
    if context.actor_id != str(principal.id):
        return []
    if context.status == AWAITING_REVIEW and WORK_REQUEST_DECIDE in principal.capabilities:
        return [
            ActionCommand("accept", "수락", "primary"),
            ActionCommand("adjust", "조정 요청", "neutral", requires_reason=True),
            ActionCommand("reject", "거절", "danger", requires_reason=True),
        ]
    if context.status == AWAITING_REVISION:
        return [
            ActionCommand("revise", "수정안 재상신", "primary"),
            ActionCommand("withdraw", "요청 철회", "neutral"),
        ]
    return []


@dataclass(frozen=True, slots=True)
class AxProposalActionContext:
    action_type: str
    pending: bool
    resolved: bool
    has_submission: bool
    obsolete: bool
    assignment_status: str | None
    assigned_by: str | None
    allow_reject: bool = True


def available_ax_proposal_commands(
    context: AxProposalActionContext,
    principal: Principal,
) -> list[ActionCommand]:
    if context.pending and ACTION_DECIDE in principal.capabilities:
        commands: list[ActionCommand] = []
        if not context.obsolete:
            commands.append(
                ActionCommand("confirm", CONFIRM_LABELS[context.action_type], "primary")
                if context.has_submission and context.action_type in CONFIRM_LABELS
                else ActionCommand("approve", "승인", "primary")
            )
        if context.allow_reject:
            commands.append(ActionCommand("reject", "거절", "neutral"))
        return commands
    if (
        context.resolved
        and context.action_type == "task.assign"
        and context.assignment_status == "pending"
        and context.assigned_by == str(principal.id)
        and TASK_ASSIGN in principal.capabilities
    ):
        return [ActionCommand("cancel_assignment", "취소", "danger")]
    return []
