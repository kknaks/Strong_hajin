"""Pure lifecycle decisions for mutable Work Request rounds."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Literal

from ax_workspace.modules.work.request_errors import WorkRequestAccessDenied, WorkRequestError, WorkRequestLockedAfterAccept


#: 회의에서 승격한 후속 업무의 요청자는 누른 사람이 아니라 회의 시스템이다.
SYSTEM_MEETING_REQUESTER = "system:meeting"


@dataclass(frozen=True, slots=True)
class RequestCreationContext:
    actor_id: str
    title: str
    description: str | None
    assignee_id: str
    cc_member_ids: tuple[str, ...]
    allow_self_assignment: bool
    promoted_by_member_id: str | None


@dataclass(frozen=True, slots=True)
class WorkRequestCreationDecision:
    requester_id: str
    title: str
    description: str | None
    cc_member_ids: tuple[str, ...]
    active_member_checks: tuple[str, ...]
    assignee_validation: Literal["none", "active", "scoped"]


@dataclass(frozen=True, slots=True)
class WorkRequest:
    id: str
    assignee_id: str
    state: str
    version: int
    requester_id: str
    promoted_by_member_id: str | None
    title: str
    description: str | None
    due_date: date | None


@dataclass(frozen=True, slots=True)
class ReviseWorkRequest:
    actor_id: str
    expected_version: int
    mode: Literal["amend", "resubmit"]
    title: str | None
    description: str | None
    due_date: date | None
    clear_due_date: bool
    exact_replay: bool


@dataclass(frozen=True, slots=True)
class OpenWorkRequestRound:
    title: str
    description: str | None
    due_date: date | None
    assignee_id: str


@dataclass(frozen=True, slots=True)
class WorkRequestRevision:
    request: WorkRequest
    replay: bool
    open_round: OpenWorkRequestRound | None
    clear_conditions: bool


@dataclass(frozen=True, slots=True)
class WithdrawWorkRequest:
    actor_id: str
    expected_version: int


@dataclass(frozen=True, slots=True)
class WorkRequestWithdrawal:
    request: WorkRequest
    clear_conditions: bool


def decide_request_creation(context: RequestCreationContext) -> WorkRequestCreationDecision:
    """Normalize a request and describe the directory checks its origin requires."""
    clean_title = context.title.strip()
    if not clean_title:
        raise WorkRequestError("title is required")

    promoted = context.promoted_by_member_id is not None
    requester_id = SYSTEM_MEETING_REQUESTER if promoted else context.actor_id
    oneself = context.allow_self_assignment and context.assignee_id == context.actor_id
    assignee_validation: Literal["none", "active", "scoped"]
    if oneself:
        assignee_validation = "none"
    elif promoted:
        assignee_validation = "active"
    else:
        assignee_validation = "scoped"

    cc: list[str] = []
    for member_id in context.cc_member_ids:
        if member_id in {requester_id, context.actor_id, context.assignee_id} or member_id in cc:
            continue
        cc.append(member_id)
    active_member_checks = tuple(cc)
    # 회의 시스템이 보낸 요청도 누른 사람은 읽을 수 있어야 한다. 이 사람은 현재 행위자이므로 다시 조회하지 않는다.
    if promoted and context.promoted_by_member_id not in {context.assignee_id, *cc}:
        cc.append(str(context.promoted_by_member_id))

    return WorkRequestCreationDecision(
        requester_id=requester_id,
        title=clean_title,
        description=(context.description or "").strip() or None,
        cc_member_ids=tuple(cc),
        active_member_checks=active_member_checks,
        assignee_validation=assignee_validation,
    )


def revise_work_request(request: WorkRequest, command: ReviseWorkRequest) -> WorkRequestRevision:
    if command.actor_id not in {request.requester_id, request.promoted_by_member_id}:
        if command.mode == "amend":
            raise WorkRequestAccessDenied("only the requester may amend their own request")
        raise WorkRequestError("only the requester may resubmit")
    if command.mode == "amend":
        if request.state in {"accepted", "cancelled_by_agreement"}:
            # **수락 뒤에는 한쪽이 혼자 조건을 못 바꾼다** (정책 V-20 · `WORK_REQUEST_LOCKED_AFTER_ACCEPT`).
            # 이미 받아들인 조건이라 바꾸는 길은 제안–동의 하나다. 어디로 가야 하는지 함께 말한다.
            raise WorkRequestLockedAfterAccept(
                "이미 수락된 요청은 수정할 수 없습니다. 조건 변경 제안으로 담당자의 동의를 받으세요"
            )
        if request.state != "pending":
            raise WorkRequestError("담당자가 판단하고 있는 요청만 수정할 수 있습니다")
        if request.version != command.expected_version:
            if command.exact_replay:
                return WorkRequestRevision(
                    request=request,
                    replay=True,
                    open_round=None,
                    clear_conditions=False,
                )
            raise WorkRequestError("work request version is stale")
    else:
        if request.version != command.expected_version:
            raise WorkRequestError("work request version is stale")
        if request.state != "negotiating":
            raise WorkRequestError("only a negotiating work request can be resubmitted")
    if command.title is not None and not command.title.strip():
        raise WorkRequestError("title is required")
    revised_title = command.title.strip() if command.title is not None else request.title
    revised_description = (
        (command.description.strip() or None) if command.description is not None else request.description
    )
    revised_due_date = (
        None if command.clear_due_date else (command.due_date if command.due_date is not None else request.due_date)
    )
    if (revised_title, revised_description, revised_due_date) == (
        request.title,
        request.description,
        request.due_date,
    ):
        raise WorkRequestError("a revision must change something")
    changed = replace(
        request,
        title=revised_title,
        description=revised_description,
        due_date=revised_due_date,
        state="pending",
        version=request.version + 1,
    )
    return WorkRequestRevision(
        request=changed,
        replay=False,
        open_round=OpenWorkRequestRound(
            title=changed.title,
            description=changed.description,
            due_date=changed.due_date,
            assignee_id=changed.assignee_id,
        ),
        clear_conditions=command.mode == "resubmit",
    )


def withdraw_work_request(request: WorkRequest, command: WithdrawWorkRequest) -> WorkRequestWithdrawal:
    if command.actor_id not in {request.requester_id, request.promoted_by_member_id}:
        raise WorkRequestError("only the requester may withdraw")
    if request.version != command.expected_version:
        raise WorkRequestError("work request version is stale")
    if request.state not in {"pending", "negotiating"}:
        raise WorkRequestError("only an open work request can be withdrawn")
    return WorkRequestWithdrawal(
        request=replace(request, state="withdrawn", version=request.version + 1),
        clear_conditions=True,
    )
