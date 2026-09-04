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
    RESOLVED,
    ActionCommand,
    ActionEnvelope,
    ActionError,
)
from ax_workspace.modules.organization_access.domain import (
    ACTION_DECIDE,
    TASK_READ,
    TASK_SELF_MANAGE,
    WORK_REQUEST_DECIDE,
    Principal,
)
from ax_workspace.platform.actions import ActionPresenter
from ax_workspace.platform.organization_access import SqlAlchemyOrganizationRepository
from ax_workspace.modules.work.requests import WorkRequestAccessDenied as ActionAccessDenied
from ax_workspace.platform.persistence import (
    ActionItemRecord,
    DecisionItemRecord,
    ResourceRelationshipRecord,
    ReviewAssignmentRecord,
    ReviewDecisionRecord,
    SubjectVersionRecord,
    SubmissionRecord,
    TaskAssignmentRecord,
    TaskRecord,
    WorkRequestRecord,
)


def _parse_date(value: Any):
    from datetime import date

    return date.fromisoformat(str(value)) if value else None

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
    """WorkRequest acceptance: the reviewer answers a Submission, or the requester answers an adjustment.

    Commands delegate to `WorkRequestApplication`; the acceptance rules, the version check and the Task effect all stay
    in the work module. This class only decides which command the current principal may reach.
    """

    def __init__(self, session: Session, work_requests: Any) -> None:
        self._session = session
        self._members = MemberDirectory(session)
        self._work_requests = work_requests

    def find(self, action_item_id: str) -> tuple[Any, Any] | None:
        try:
            item = self._session.get(DecisionItemRecord, UUID(action_item_id))
        except ValueError:
            return None
        if item is None or item.kind != WORK_REQUEST_ACCEPTANCE:
            return None
        request = self._session.scalar(select(WorkRequestRecord).where(WorkRequestRecord.subject_id == item.subject_id))
        return (item, request) if request is not None else None

    def envelope(self, item: tuple[Any, Any], principal: Principal) -> ActionEnvelope:
        decision_item, request = item
        return self._build(decision_item, request, principal, pending_only=False)

    def rounds(self, item: tuple[Any, Any], principal: Principal) -> list[dict[str, Any]]:
        decision_item, request = item
        self._require_participant(request, principal)
        submissions = self._session.scalars(
            select(SubmissionRecord)
            .where(SubmissionRecord.decision_item_id == decision_item.id)
            .order_by(SubmissionRecord.submission_version)
        ).all()
        decisions: dict[Any, list[dict[str, Any]]] = {}
        for decision in self._session.scalars(
            select(ReviewDecisionRecord)
            .where(ReviewDecisionRecord.submission_id.in_([submission.id for submission in submissions]))
            .order_by(ReviewDecisionRecord.decided_at)
        ).all():
            decisions.setdefault(decision.submission_id, []).append(
                {
                    "review_decision_id": str(decision.id),
                    "actor_member_id": decision.actor_member_id,
                    "decision": decision.decision,
                    "reason": decision.reason,
                    "decided_at": decision.decided_at.isoformat(),
                }
            )
        rows = []
        for submission in submissions:
            version = self._session.get(SubjectVersionRecord, submission.subject_version_id)
            rows.append(
                {
                    "submission_id": str(submission.id),
                    "submission_version": int(submission.submission_version),
                    "submitted_by": submission.submitted_by,
                    "submitted_at": submission.submitted_at.isoformat(),
                    "content_hash": submission.payload_hash,
                    "snapshot": dict(version.snapshot) if version else {},
                    "diff": submission.diff,
                    "decisions": decisions.get(submission.id, []),
                }
            )
        return rows

    def execute(self, principal: Principal, item: tuple[Any, Any], command: str, payload: dict[str, Any]) -> None:
        _, request = item
        expected_version = int(payload.get("expected_version") or request.version)
        reason = str(payload.get("reason") or "").strip()
        if command == "accept":
            self._work_requests.accept(principal, request.id, expected_version)
        elif command == "reject":
            self._work_requests.reject(principal, request.id, expected_version, reason)
        elif command == "adjust":
            self._work_requests.negotiate(principal, request.id, expected_version, {"note": reason})
        elif command == "withdraw":
            self._work_requests.withdraw(principal, request.id, expected_version)
        elif command == "revise":
            changes = dict(payload.get("changes") or {})
            if not changes:
                raise ActionError("수정안에는 바뀐 내용이 있어야 합니다")
            self._work_requests.resubmit(
                principal,
                request.id,
                expected_version,
                title=changes.get("title"),
                description=changes.get("description"),
                due_date=_parse_date(changes.get("due_date")),
                clear_due_date=bool(changes.get("clear_due_date")),
            )
        else:  # pragma: no cover - the envelope already refused anything else
            raise ActionError(f"unsupported command {command}")

    def is_replay(self, item: tuple[Any, Any], principal: Principal, command: str) -> bool:
        """An answered request cannot be answered again; the caller reads the resolved item instead."""
        return False

    def _require_participant(self, request: Any, principal: Principal) -> None:
        member_id = str(principal.id)
        if member_id in {request.requester_id, request.assignee_id}:
            return
        related = self._session.scalars(
            select(ResourceRelationshipRecord.member_id).where(
                ResourceRelationshipRecord.resource_type == "work_request",
                ResourceRelationshipRecord.resource_id == str(request.id),
                ResourceRelationshipRecord.valid_until.is_(None),
            )
        ).all()
        if member_id not in set(related):
            raise ActionAccessDenied("principal cannot read this action item")

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
            envelope = self._build(item, request, principal, pending_only=True)
            if envelope is not None:
                envelopes.append(envelope)
        return envelopes

    def _build(self, item: DecisionItemRecord, request: WorkRequestRecord, principal: Principal, *, pending_only: bool) -> Any:
        submission = self._current_submission(item)
        if submission is None:
            return None
        resolved = item.status not in {"open", AWAITING_REVISION}
        status = RESOLVED if resolved else AWAITING_REVISION if item.status == AWAITING_REVISION else AWAITING_REVIEW
        if status == RESOLVED:
            actor, allowed = None, []
        elif status == AWAITING_REVIEW:
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
            if pending_only:
                return None
            allowed = []
        if pending_only and not allowed:
            return None
        return ActionEnvelope(
            action_item_id=str(item.id),
            kind=WORK_REQUEST_ACCEPTANCE,
            status=status,
            subject=str(request.title),
            operation_label="업무 요청",
            current_question=_QUESTIONS.get((WORK_REQUEST_ACCEPTANCE, status), "이 요청은 이미 판단이 끝났습니다"),
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

    def __init__(self, session: Session, actions: Any) -> None:
        self._session = session
        self._presenter = ActionPresenter(session)
        self._members = MemberDirectory(session)
        self._actions = actions

    def find(self, action_item_id: str) -> Any | None:
        try:
            return self._session.get(ActionItemRecord, UUID(action_item_id))
        except ValueError:
            return None

    def rounds(self, item: Any, principal: Principal) -> list[dict[str, Any]]:
        """A gated proposal is prepared once; its single round is the payload the turn produced."""
        if str(item.owner_id) != str(principal.id):
            raise ActionAccessDenied("principal cannot read this action item")
        return [
            {
                "submission_id": str(item.id),
                "submission_version": 1,
                "submitted_by": "ax",
                "submitted_at": item.created_at.isoformat(),
                "content_hash": item.payload_hash,
                "snapshot": dict(item.payload),
                "diff": None,
                "decisions": [
                    {
                        "review_decision_id": str(item.id),
                        "actor_member_id": str(item.owner_id),
                        "decision": item.state,
                        "reason": None,
                        "decided_at": item.decided_at.isoformat(),
                    }
                ]
                if item.decided_at
                else [],
            }
        ]

    def execute(self, principal: Principal, item: Any, command: str, payload: dict[str, Any]) -> None:
        self._actions.decide(principal, item.id, int(payload.get("expected_version") or item.version), command)

    def is_replay(self, item: Any, principal: Principal, command: str) -> bool:
        """The persisted Action is the idempotency boundary; a repeat of the decision it already holds is a receipt."""
        if str(item.owner_id) != str(principal.id) or ACTION_DECIDE not in principal.capabilities:
            return False
        return item.state == {"approve": "approved", "reject": "rejected"}.get(command)

    def pending(self, principal: Principal) -> list[ActionEnvelope]:
        if ACTION_DECIDE not in principal.capabilities:
            return []
        records = self._session.scalars(
            select(ActionItemRecord)
            .where(ActionItemRecord.owner_id == str(principal.id), ActionItemRecord.state == "pending")
            .order_by(ActionItemRecord.created_at)
        ).all()
        return [self.envelope(record, principal) for record in records]

    def envelope(self, record: ActionItemRecord, principal: Principal) -> ActionEnvelope:
        presented = self._presenter.present(record, principal)
        return ActionEnvelope(
            action_item_id=str(record.id),
            kind=f"ax.{record.action_type}",
            status=AWAITING_REVIEW if record.state == "pending" else RESOLVED,
            subject=str(presented["subject"]),
            operation_label=str(presented["operation_label"]),
            current_question="AX가 준비한 변경을 승인할지 결정하세요" if record.state == "pending" else "이 제안은 이미 판단이 끝났습니다",
            preview=list(presented["preview"]),
            allowed_commands=(
                [ActionCommand("approve", "승인", "primary"), ActionCommand("reject", "거절", "neutral")]
                if record.state == "pending" and ACTION_DECIDE in principal.capabilities and str(record.owner_id) == str(principal.id)
                else []
            ),
            submission_version=1,
            waiting_on=self._members.waiting_on(str(record.owner_id)),
            resource={"type": "action", "id": str(record.id)},
            expected_version=int(record.version),
        )


class TaskAssignmentActionHandler:
    """A direct assignment: the assignee decides whether to take the work on.

    There is one round by construction — the assigner proposes once — so the assignment row is its own submission.
    """

    def __init__(self, session: Session, assignments: Any) -> None:
        self._session = session
        self._members = MemberDirectory(session)
        self._assignments = assignments

    def pending(self, principal: Principal) -> list[ActionEnvelope]:
        if TASK_READ not in principal.capabilities:
            return []
        rows = self._session.execute(
            select(TaskAssignmentRecord, TaskRecord)
            .join(TaskRecord, TaskRecord.id == TaskAssignmentRecord.task_id)
            .where(TaskAssignmentRecord.assignee_id == str(principal.id), TaskAssignmentRecord.status == "pending")
            .order_by(TaskAssignmentRecord.created_at)
        ).all()
        return [self.envelope((assignment, task), principal) for assignment, task in rows]

    def find(self, action_item_id: str) -> tuple[Any, Any] | None:
        try:
            assignment = self._session.get(TaskAssignmentRecord, UUID(action_item_id))
        except ValueError:
            return None
        if assignment is None:
            return None
        task = self._session.get(TaskRecord, assignment.task_id)
        return (assignment, task) if task is not None else None

    def envelope(self, item: tuple[Any, Any], principal: Principal) -> ActionEnvelope:
        assignment, task = item
        pending = assignment.status == "pending"
        mine = str(assignment.assignee_id) == str(principal.id)
        preview: list[dict[str, str]] = []
        if task.description:
            preview.append({"id": "description", "label": "설명", "value": str(task.description), "kind": "text"})
        assigner = self._members.waiting_on(assignment.assigned_by)
        if assigner:
            preview.append({"id": "assigner", "label": "배정자", "value": assigner["display_name"], "kind": "person"})
        if task.due_date:
            preview.append({"id": "due_date", "label": "기한", "value": task.due_date.isoformat(), "kind": "date"})
        return ActionEnvelope(
            action_item_id=str(assignment.id),
            kind="task.assignment",
            status=AWAITING_REVIEW if pending else RESOLVED,
            subject=str(task.title),
            operation_label="업무 배정",
            current_question="이 업무 배정을 수락할지 결정하세요" if pending else "이 배정은 이미 판단이 끝났습니다",
            preview=preview,
            allowed_commands=(
                [ActionCommand("accept", "수락", "primary"), ActionCommand("decline", "거절", "danger", requires_reason=True)]
                if pending and mine and TASK_SELF_MANAGE in principal.capabilities
                else []
            ),
            submission_version=1,
            waiting_on=self._members.waiting_on(assignment.assignee_id if pending else None),
            resource={"type": "task", "id": str(task.id)},
            expected_version=int(task.version),
        )

    def rounds(self, item: tuple[Any, Any], principal: Principal) -> list[dict[str, Any]]:
        assignment, task = item
        if str(assignment.assignee_id) != str(principal.id) and str(assignment.assigned_by) != str(principal.id):
            raise ActionAccessDenied("principal cannot read this action item")
        decided_at = assignment.accepted_at or assignment.declined_at
        return [
            {
                "submission_id": str(assignment.id),
                "submission_version": 1,
                "submitted_by": assignment.assigned_by,
                "submitted_at": assignment.created_at.isoformat(),
                "content_hash": "",
                "snapshot": {"title": task.title, "description": task.description, "due_date": task.due_date.isoformat() if task.due_date else None},
                "diff": None,
                "decisions": [
                    {
                        "review_decision_id": str(assignment.id),
                        "actor_member_id": str(assignment.assignee_id),
                        "decision": "accept" if assignment.accepted_at else "decline",
                        "reason": assignment.decline_reason,
                        "decided_at": decided_at.isoformat(),
                    }
                ]
                if decided_at
                else [],
            }
        ]

    def execute(self, principal: Principal, item: tuple[Any, Any], command: str, payload: dict[str, Any]) -> None:
        assignment, _ = item
        if command == "accept":
            self._assignments.accept(principal, assignment.id)
        else:
            self._assignments.decline(principal, assignment.id, str(payload.get("reason") or ""))

    def is_replay(self, item: tuple[Any, Any], principal: Principal, command: str) -> bool:
        assignment, _ = item
        if str(assignment.assignee_id) != str(principal.id):
            return False
        return (command == "accept" and assignment.status == "active") or (command == "decline" and assignment.status == "declined")


def action_handlers(session: Session, *, work_requests: Any, actions: Any, assignments: Any) -> list[Any]:
    """Every origin that can put a question to a person, in the order a person should meet them.

    The module applications are passed in rather than rebuilt here, so every command runs the same operation the rest of
    the product runs, with that module's own rules and audit.
    """
    return [
        WorkRequestActionHandler(session, work_requests),
        TaskAssignmentActionHandler(session, assignments),
        AxProposalActionHandler(session, actions),
    ]


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
