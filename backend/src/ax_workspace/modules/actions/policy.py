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

#: 「초안 저장」 — 확정 없이 고친 초안을 다음 회차로 남기는 명령 (SPEC-002 §4 「초안 저장」 · WORK-009 1-1).
#: 기존 `revise`(업무 요청 판단의 「수정안 재상신」)와 이름을 나누는 이유: 그쪽은 협의로 돌아온 요청을 상대에게
#: **다시 보내** 판단 대기를 상대 차례로 되돌리고 `changes`(제목·내용·기한)만 받는다. 이 명령은 **내 차례 그대로**
#: 회차만 올리고 생성 명령의 필드 전체(`draft`)를 받는다 — 같은 id 를 쓰면 봉투가 다른 두 계약을 한 이름으로 내린다.
SAVE_DRAFT_COMMAND = "save_draft"
SAVE_DRAFT_LABEL = "저장"
#: 저장이 열리는 AX 초안 — 「새 업무 추가」를 AI 가 채운 두 kind 만이다 (SPEC-002 §4 · P-1).
DRAFT_SAVE_ACTION_TYPES = frozenset({"task.create_self", "work_request.create"})

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
            if context.has_submission and context.action_type in DRAFT_SAVE_ACTION_TYPES:
                # 확정하지 않는 저장 — 봉투가 내려 준 사람만 부른다(§5 「명령의 권한은 봉투 자체다」).
                commands.append(ActionCommand(SAVE_DRAFT_COMMAND, SAVE_DRAFT_LABEL, "neutral"))
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
