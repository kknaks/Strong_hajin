"""Reading every kind of pending judgement out of PostgreSQL as one canonical ActionItem projection.

Each handler knows one origin's tables and its policy; none of them re-implements another module's business rules. The
WorkRequest handler reads the canonical `Subject → ActionItem → Submission → ReviewAssignment` chain that the request
module already writes, so an adjustment round is the same ActionItem with a later Submission, never a new question.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ax_workspace.modules.actions.domain import (
    AWAITING_REVIEW,
    AWAITING_REVISION,
    ActionCommand,
    ActionEnvelope,
)
from ax_workspace.modules.organization_access.domain import ACTION_DECIDE, WORK_REQUEST_DECIDE, Principal
from ax_workspace.platform.actions import ActionPresenter
from ax_workspace.platform.organization_access import SqlAlchemyOrganizationRepository
from ax_workspace.platform.persistence import (
    ActionItemRecord,
    DecisionItemRecord,
    ReviewAssignmentRecord,
    SubjectVersionRecord,
    SubmissionRecord,
    WorkRequestRecord,
)

WORK_REQUEST_ACCEPTANCE = "work_request.acceptance"

_QUESTIONS = {
    (WORK_REQUEST_ACCEPTANCE, AWAITING_REVIEW): "이 업무 요청을 수락할지 결정하세요",
    (WORK_REQUEST_ACCEPTANCE, AWAITING_REVISION): "조정 요청에 답해 수정안을 다시 보낼지 결정하세요",
}


class MemberDirectory:
    """Display names for the person a judgement is waiting on, resolved once per session."""

    def __init__(self, session: Session) -> None:
        self._repository = SqlAlchemyOrganizationRepository(session)
        self._cache: dict[str, str | None] = {}

    def waiting_on(self, member_id: str | None) -> dict[str, str] | None:
        if not member_id:
            return None
        if member_id not in self._cache:
            principal = self._repository.principal_for(member_id)
            self._cache[member_id] = principal.display_name if principal else None
        return {"member_id": member_id, "display_name": self._cache[member_id] or member_id}


class WorkRequestActionHandler:
    """WorkRequest acceptance: the reviewer answers a Submission, or the requester answers an adjustment."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._members = MemberDirectory(session)

    def pending(self, principal: Principal) -> list[ActionEnvelope]:
        rows = self._session.execute(
            select(DecisionItemRecord, WorkRequestRecord)
            .join(WorkRequestRecord, WorkRequestRecord.subject_id == DecisionItemRecord.subject_id)
            .where(
                DecisionItemRecord.kind == WORK_REQUEST_ACCEPTANCE,
                DecisionItemRecord.status.in_(("open", AWAITING_REVISION)),
            )
            .order_by(DecisionItemRecord.created_at)
        ).all()
        envelopes = []
        for item, request in rows:
            envelope = self._envelope(item, request, principal)
            if envelope is not None:
                envelopes.append(envelope)
        return envelopes

    def _envelope(self, item: DecisionItemRecord, request: WorkRequestRecord, principal: Principal) -> ActionEnvelope | None:
        submission = self._current_submission(item)
        if submission is None:
            return None
        status = AWAITING_REVISION if item.status == AWAITING_REVISION else AWAITING_REVIEW
        if status == AWAITING_REVIEW:
            assignment = self._active_assignment(submission)
            actor = assignment.reviewer_member_id if assignment else None
            commands = [
                ActionCommand("accept", "수락", "primary"),
                ActionCommand("adjust", "조정 요청", "neutral", requires_reason=True),
                ActionCommand("reject", "거절", "danger", requires_reason=True),
            ]
            allowed = commands if WORK_REQUEST_DECIDE in principal.capabilities else []
        else:
            actor = request.requester_id
            allowed = [ActionCommand("revise", "수정안 재상신", "primary"), ActionCommand("withdraw", "요청 철회", "neutral")]
        if actor != str(principal.id):
            return None
        return ActionEnvelope(
            action_item_id=str(item.id),
            kind=WORK_REQUEST_ACCEPTANCE,
            status=status,
            subject=str(request.title),
            operation_label="업무 요청",
            current_question=_QUESTIONS[(WORK_REQUEST_ACCEPTANCE, status)],
            preview=self._preview(submission, request),
            allowed_commands=allowed,
            submission_version=int(submission.submission_version),
            waiting_on=self._members.waiting_on(actor),
            resource={"type": "work_request", "id": str(request.id)},
            expected_version=int(request.version),
        )

    def _preview(self, submission: SubmissionRecord, request: WorkRequestRecord) -> list[dict[str, str]]:
        """Read the frozen Submission, not the mutable request row: this is what is actually being judged."""
        version = self._session.get(SubjectVersionRecord, submission.subject_version_id)
        snapshot: dict[str, Any] = dict(version.snapshot) if version else {}
        rows: list[dict[str, str]] = []
        if snapshot.get("description"):
            rows.append({"id": "description", "label": "설명", "value": str(snapshot["description"]), "kind": "text"})
        requester = self._members.waiting_on(request.requester_id)
        if requester:
            rows.append({"id": "requester", "label": "요청자", "value": requester["display_name"], "kind": "person"})
        assignee = self._members.waiting_on(str(snapshot.get("assignee_id") or request.assignee_id))
        if assignee:
            rows.append({"id": "assignee", "label": "담당", "value": assignee["display_name"], "kind": "person"})
        if snapshot.get("due_date"):
            rows.append({"id": "due_date", "label": "기한", "value": str(snapshot["due_date"]), "kind": "date"})
        return rows

    def _current_submission(self, item: DecisionItemRecord) -> SubmissionRecord | None:
        return self._session.scalar(
            select(SubmissionRecord)
            .where(SubmissionRecord.decision_item_id == item.id)
            .order_by(SubmissionRecord.submission_version.desc())
        )

    def _active_assignment(self, submission: SubmissionRecord) -> ReviewAssignmentRecord | None:
        return self._session.scalar(
            select(ReviewAssignmentRecord)
            .where(ReviewAssignmentRecord.submission_id == submission.id, ReviewAssignmentRecord.status == "pending")
            .order_by(ReviewAssignmentRecord.assigned_at.desc())
        )


class AxProposalActionHandler:
    """A gated AX proposal: the owner approves or rejects the effect the turn prepared."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._presenter = ActionPresenter(session)
        self._members = MemberDirectory(session)

    def pending(self, principal: Principal) -> list[ActionEnvelope]:
        if ACTION_DECIDE not in principal.capabilities:
            return []
        records = self._session.scalars(
            select(ActionItemRecord)
            .where(ActionItemRecord.owner_id == str(principal.id), ActionItemRecord.state == "pending")
            .order_by(ActionItemRecord.created_at)
        ).all()
        return [self._envelope(record, principal) for record in records]

    def _envelope(self, record: ActionItemRecord, principal: Principal) -> ActionEnvelope:
        presented = self._presenter.present(record, principal)
        return ActionEnvelope(
            action_item_id=str(record.id),
            kind=f"ax.{record.action_type}",
            status=AWAITING_REVIEW,
            subject=str(presented["subject"]),
            operation_label=str(presented["operation_label"]),
            current_question="AX가 준비한 변경을 승인할지 결정하세요",
            preview=list(presented["preview"]),
            allowed_commands=[ActionCommand("approve", "승인", "primary"), ActionCommand("reject", "거절", "neutral")],
            submission_version=1,
            waiting_on=self._members.waiting_on(str(record.owner_id)),
            resource={"type": "action", "id": str(record.id)},
            expected_version=int(record.version),
        )


def action_handlers(session: Session) -> list[Any]:
    """Every origin that can put a question to a person, in the order a person should meet them."""
    return [WorkRequestActionHandler(session), AxProposalActionHandler(session)]


def decision_item_id(session: Session, request_id: UUID) -> UUID | None:
    """The canonical ActionItem behind a WorkRequest, for callers that still start from the request."""
    request = session.get(WorkRequestRecord, request_id)
    if request is None or request.subject_id is None:
        return None
    item = session.scalar(
        select(DecisionItemRecord)
        .where(DecisionItemRecord.subject_id == request.subject_id, DecisionItemRecord.kind == WORK_REQUEST_ACCEPTANCE)
        .order_by(DecisionItemRecord.created_at.desc())
    )
    return item.id if item else None
