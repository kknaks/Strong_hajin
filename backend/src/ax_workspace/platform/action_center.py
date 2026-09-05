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
from ax_workspace.modules.work.requests import (
    DECISION_VERSION,
    EVIDENCE_HASH,
    REVISABLE_FIELDS,
    WorkRequestAccessDenied as ActionAccessDenied,
    WorkRequestError,
    evidence_manifest,
    evidence_manifest_entry,
    evidence_manifest_hash,
    decision_facts,
    normalize_proposed_changes,
)
from ax_workspace.platform.persistence import (
    ActionItemRecord,
    DecisionItemRecord,
    ResourceRelationshipRecord,
    ReviewAssignmentRecord,
    EvidenceRecord,
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

def _required_version(payload: dict[str, Any]) -> int:
    """Every command answers a version it was shown. Without one there is no stale-write check at all."""
    value = payload.get("expected_version")
    if value is None or isinstance(value, bool):
        raise ActionError("expected_version is required")
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ActionError("expected_version must be an integer") from error


def _proposed_changes(value: Any) -> dict[str, Any]:
    """An adjustment's optional structured ask. The work module owns the allow-list, so every writer agrees with it."""
    try:
        return normalize_proposed_changes(value)
    except WorkRequestError as error:
        raise ActionError(str(error)) from error


def _revision_changes(value: Any) -> dict[str, Any]:
    """What a revision may change. A field it does not own is refused, never dropped from a successful answer."""
    if not value:
        return {}
    if not isinstance(value, dict):
        raise ActionError("수정안은 필드별로 적어 주세요")
    unknown = sorted(set(value) - set(REVISABLE_FIELDS))
    if unknown:
        raise ActionError(f"수정안에서 바꿀 수 없는 항목입니다: {', '.join(unknown)}")
    changes: dict[str, Any] = {}
    if "title" in value:
        # A request always has a title, so emptying it is a mistake rather than a change.
        title = str(value["title"] or "").strip()
        if not title:
            raise ActionError("요청할 업무 제목은 비울 수 없습니다")
        changes["title"] = title
    if "description" in value:
        # Naming the description and leaving it empty is how a requester removes it: presence is the intent.
        changes["description"] = str(value["description"] or "").strip()
    if value.get("clear_due_date"):
        changes["clear_due_date"] = True
    elif "due_date" in value:
        # An empty date is refused rather than dropped: a revision that succeeded must have done everything it named.
        due_date = str(value["due_date"] or "").strip()
        if not due_date:
            raise ActionError("기한은 clear_due_date로만 지웁니다")
        _parse_date(due_date)
        changes["due_date"] = due_date
    return changes


def _suggested_changes(conditions: Any) -> dict[str, Any]:
    """The proposal a negotiate decision carries; a reason-only adjustment has none."""
    if not isinstance(conditions, dict):
        return {}
    return _proposed_changes(conditions.get("changes"))


WORK_REQUEST_ACCEPTANCE = "work_request.acceptance"
#: The result question: opened by a delivery report, answered by the person who asked for the work.
DELIVERY_REVIEW = "task.delivery.review"

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
                    "suggested_changes": _suggested_changes(decision.conditions),
                    # The basis this answer was actually made on, frozen when it was made.
                    "evidence_hash": decision_facts(decision.conditions).get(EVIDENCE_HASH),
                    "decided_at": decision.decided_at.isoformat(),
                }
            )
        bases = self._evidence([submission.id for submission in submissions])
        rows = []
        for submission in submissions:
            version = self._session.get(SubjectVersionRecord, submission.subject_version_id)
            basis = bases.get(submission.id, [])
            rows.append(
                {
                    "submission_id": str(submission.id),
                    "submission_version": int(submission.submission_version),
                    "submitted_by": submission.submitted_by,
                    "submitted_at": submission.submitted_at.isoformat(),
                    "content_hash": submission.payload_hash,
                    "snapshot": dict(version.snapshot) if version else {},
                    "diff": submission.diff,
                    "evidence": basis,
                    "evidence_hash": evidence_manifest_hash(basis),
                    "decisions": decisions.get(submission.id, []),
                }
            )
        return rows

    def _evidence(self, submission_ids: list[UUID]) -> dict[UUID, list[dict[str, str]]]:
        """What each round currently stands on, in the one canonical form every surface reads, in one query."""
        if not submission_ids:
            return {}
        grouped: dict[UUID, list[dict[str, str]]] = {submission_id: [] for submission_id in submission_ids}
        for row in self._session.scalars(select(EvidenceRecord).where(EvidenceRecord.submission_id.in_(submission_ids))):
            grouped[row.submission_id].append(evidence_manifest_entry(row.attachment_id, row.evidence_role, row.fixed_snapshot_ref))
        return {submission_id: evidence_manifest(entries) for submission_id, entries in grouped.items()}

    def normalize(self, item: tuple[Any, Any], command: str, payload: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {"expected_version": _required_version(payload)}
        reason = str(payload.get("reason") or "").strip()
        if reason:
            normalized["reason"] = reason
        if command == "adjust":
            proposed = _proposed_changes(payload.get("changes"))
            if proposed:
                normalized["changes"] = proposed
        elif command == "revise":
            revision = _revision_changes(payload.get("changes"))
            if revision:
                normalized["changes"] = revision
        elif payload.get("changes"):
            raise ActionError(f"'{command}'은(는) 변경 항목을 받지 않습니다")
        return normalized

    def execute(self, principal: Principal, item: tuple[Any, Any], command: str, payload: dict[str, Any]) -> None:
        _, request = item
        expected_version = _required_version(payload)
        reason = str(payload.get("reason") or "").strip()
        if command == "accept":
            self._work_requests.accept(principal, request.id, expected_version)
        elif command == "reject":
            self._work_requests.reject(principal, request.id, expected_version, reason)
        elif command == "adjust":
            proposed = _proposed_changes(payload.get("changes"))
            conditions = {"note": reason, **({"changes": proposed} if proposed else {})}
            self._work_requests.negotiate(principal, request.id, expected_version, conditions)
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

    def is_replay(self, item: tuple[Any, Any], principal: Principal, command: str, payload: dict[str, Any]) -> bool:
        """A re-send of the answer this principal already gave is a receipt, not a second effect.

        The bar is that this exact request produced the state the item is in now: it aimed at the version the command
        actually consumed, and its content is what the stored decision or submission records. An older round's command
        of the same shape, or the same version with different content, is a stale request and is refused — a lost
        response can never become a way to change one's mind or to replay a superseded round.
        """
        decision_item, request = item
        try:
            targeted = int(payload["expected_version"])
        except (KeyError, TypeError, ValueError):
            return False
        submission = self._current_submission(decision_item)
        if submission is None:
            return False
        if command == "revise":
            # The round this payload produced is the receipt, and the version it consumed is recoverable from the
            # adjustment it answered: nothing can move the request between that decision and this revision. So a later
            # bump — someone adopting evidence on the new round — leaves the receipt intact, while a version this
            # revision never consumed is still a different request.
            return (
                submission.submitted_by == str(principal.id)
                and self._revision_consumed(submission) == targeted
                and self._revision_produced(submission, payload)
            )
        if command == "withdraw":
            # Withdrawal is terminal: nothing can move the request afterwards, so the single step still pins it.
            return (
                request.state == "withdrawn"
                and request.requester_id == str(principal.id)
                and int(request.version) == targeted + 1
            )
        decided = self._session.scalar(
            select(ReviewDecisionRecord)
            .where(ReviewDecisionRecord.submission_id == submission.id)
            .order_by(ReviewDecisionRecord.decided_at.desc())
        )
        if decided is None or decided.actor_member_id != str(principal.id):
            return False
        if decided.decision != {"accept": "accept", "reject": "reject", "adjust": "negotiate"}.get(command):
            return False
        # The version this answer actually consumed, frozen when it was made rather than inferred from where the
        # request stands now.
        if decision_facts(decided.conditions).get(DECISION_VERSION) != targeted:
            return False
        if command == "accept":
            return True
        if (decided.reason or "") != str(payload.get("reason") or "").strip():
            return False
        return command != "adjust" or _suggested_changes(decided.conditions) == _proposed_changes(payload.get("changes"))

    def _revision_consumed(self, submission: SubmissionRecord) -> int | None:
        """The request version this revision answered, read from the adjustment that asked for it.

        A negotiating request is closed to everything but a revision or a withdrawal, so the version one step past
        that decision is exactly what the revision consumed.
        """
        if submission.revises_id is None:
            return None
        decided = self._session.scalar(
            select(ReviewDecisionRecord)
            .where(ReviewDecisionRecord.submission_id == submission.revises_id, ReviewDecisionRecord.decision == "negotiate")
            .order_by(ReviewDecisionRecord.decided_at.desc())
        )
        consumed = decision_facts(decided.conditions).get(DECISION_VERSION) if decided else None
        return int(consumed) + 1 if consumed is not None else None

    def _revision_produced(self, submission: SubmissionRecord, payload: dict[str, Any]) -> bool:
        """Would this payload, applied to the round it revised, have produced exactly the round that now stands?"""
        if submission.revises_id is None:
            return False
        previous = self._session.get(SubmissionRecord, submission.revises_id)
        current_version = self._session.get(SubjectVersionRecord, submission.subject_version_id)
        previous_version = self._session.get(SubjectVersionRecord, previous.subject_version_id) if previous else None
        if current_version is None or previous_version is None:
            return False
        changes = dict(payload.get("changes") or {})
        revised = dict(previous_version.snapshot)
        if changes.get("title") is not None:
            revised["title"] = str(changes["title"]).strip()
        if changes.get("description") is not None:
            revised["description"] = str(changes["description"]).strip() or None
        if changes.get("clear_due_date"):
            revised["due_date"] = None
        elif changes.get("due_date") is not None:
            parsed = _parse_date(changes["due_date"])
            revised["due_date"] = parsed.isoformat() if parsed else None
        return revised == dict(current_version.snapshot)

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
            extra={"suggested_changes": self._suggested_changes(submission)},
        )

    def discussion(self, item: tuple[Any, Any], principal: Principal) -> list[dict[str, Any]]:
        _, request = item
        self._require_participant(request, principal)
        return self._work_requests.discussion(principal, request.id)

    def _suggested_changes(self, submission: SubmissionRecord) -> dict[str, Any]:
        """What the last reviewer asked to be changed on the round still awaiting an answer."""
        decided = self._session.scalar(
            select(ReviewDecisionRecord)
            .where(ReviewDecisionRecord.submission_id == submission.id, ReviewDecisionRecord.decision == "negotiate")
            .order_by(ReviewDecisionRecord.decided_at.desc())
        )
        return _suggested_changes(decided.conditions if decided else None)

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
                # An AX proposal is judged on the payload the turn prepared; it adopts no Evidence of its own.
                "evidence": [],
                "evidence_hash": None,
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
                        "evidence_hash": None,
                        "decided_at": item.decided_at.isoformat(),
                    }
                ]
                if item.decided_at
                else [],
            }
        ]

    def normalize(self, item: Any, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        """An approval carries nothing but the version it answers: the effect is the payload the turn already prepared."""
        if payload.get("changes") or str(payload.get("reason") or "").strip():
            raise ActionError("AX 제안 판단은 사유나 변경 항목을 받지 않습니다")
        return {"expected_version": _required_version(payload)}

    def execute(self, principal: Principal, item: Any, command: str, payload: dict[str, Any]) -> None:
        self._actions.decide(principal, item.id, _required_version(payload), command)

    def discussion(self, item: Any, principal: Principal) -> list[dict[str, Any]]:
        """An AX proposal is judged on its preview and evidence; it carries no comment thread."""
        return []

    def is_replay(self, item: Any, principal: Principal, command: str, payload: dict[str, Any]) -> bool:
        """The persisted Action is the idempotency boundary; a repeat of the decision it already holds is a receipt."""
        if str(item.owner_id) != str(principal.id) or ACTION_DECIDE not in principal.capabilities:
            return False
        if item.state != {"approve": "approved", "reject": "rejected"}.get(command):
            return False
        # Deciding bumps the Action exactly once, so only the version this decision consumed is a receipt.
        try:
            return int(item.version) == _required_version(payload) + 1
        except ActionError:
            return False

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
                [
                    *([] if presented.get("obsolete") else [ActionCommand("approve", "승인", "primary")]),
                    ActionCommand("reject", "거절", "neutral"),
                ]
                if record.state == "pending" and ACTION_DECIDE in principal.capabilities and str(record.owner_id) == str(principal.id)
                else []
            ),
            submission_version=1,
            waiting_on=self._members.waiting_on(str(record.owner_id)),
            resource={"type": "action", "id": str(record.id)},
            expected_version=int(record.version),
            # Closing the round trip: the Task this proposal produced, if it produced one.
            extra={"derived_task_id": self._derived_task_id(record)},
        )

    def _derived_task_id(self, record: ActionItemRecord) -> str | None:
        task_id = self._session.scalar(select(TaskRecord.id).where(TaskRecord.source_action_item_id == record.id))
        return str(task_id) if task_id else None


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
                # A direct assignment is answered on the spot; it adopts no Evidence of its own.
                "evidence": [],
                "evidence_hash": None,
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
                        "evidence_hash": None,
                        "decided_at": decided_at.isoformat(),
                    }
                ]
                if decided_at
                else [],
            }
        ]

    def normalize(self, item: tuple[Any, Any], command: str, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("changes"):
            raise ActionError("배정 판단은 변경 항목을 받지 않습니다")
        normalized: dict[str, Any] = {"expected_version": _required_version(payload)}
        reason = str(payload.get("reason") or "").strip()
        if reason:
            normalized["reason"] = reason
        return normalized

    def execute(self, principal: Principal, item: tuple[Any, Any], command: str, payload: dict[str, Any]) -> None:
        assignment, task = item
        # The assignment carries no version of its own; the Task version the envelope showed is what is being answered.
        if int(task.version) != _required_version(payload):
            raise ActionError("task version is stale")
        if command == "accept":
            self._assignments.accept(principal, assignment.id)
        else:
            self._assignments.decline(principal, assignment.id, str(payload.get("reason") or ""))

    def discussion(self, item: tuple[Any, Any], principal: Principal) -> list[dict[str, Any]]:
        """A direct assignment is answered on the spot; it carries no comment thread."""
        return []

    def is_replay(self, item: tuple[Any, Any], principal: Principal, command: str, payload: dict[str, Any]) -> bool:
        """One assignment is answered once, so the stored status is the receipt — pinned to the Task version it moved.

        The assignment has no version column of its own, so the Task's is the contract the envelope hands out:
        accepting leaves it where it was, declining cancels the Task and moves it exactly one version on.
        """
        assignment, task = item
        if str(assignment.assignee_id) != str(principal.id):
            return False
        try:
            targeted = _required_version(payload)
        except ActionError:
            return False
        if command == "accept":
            return assignment.status == "active" and int(task.version) == targeted
        if command != "decline" or assignment.status != "declined":
            return False
        return int(task.version) == targeted + 1 and (assignment.decline_reason or "") == str(payload.get("reason") or "").strip()


class TaskDeliveryActionHandler:
    """A result handed over, and the person who asked for it deciding whether it is what they wanted.

    This is not the question that created the Task — that one was answered when the request was accepted. It has its
    own identity, its own words, and its own rounds: every report is another Submission on the same question.
    """

    def __init__(self, session: Session, tasks: Any) -> None:
        self._session = session
        self._members = MemberDirectory(session)
        self._tasks = tasks

    def pending(self, principal: Principal) -> list[ActionEnvelope]:
        if TASK_READ not in principal.capabilities:
            return []
        rows = self._session.execute(
            select(DecisionItemRecord, SubmissionRecord, ReviewAssignmentRecord)
            .join(SubmissionRecord, SubmissionRecord.decision_item_id == DecisionItemRecord.id)
            .join(ReviewAssignmentRecord, ReviewAssignmentRecord.submission_id == SubmissionRecord.id)
            .where(
                DecisionItemRecord.kind == DELIVERY_REVIEW,
                ReviewAssignmentRecord.reviewer_member_id == str(principal.id),
                ReviewAssignmentRecord.status == "pending",
            )
            .order_by(SubmissionRecord.submitted_at)
        ).all()
        envelopes = []
        for item, _submission, _assignment in rows:
            found = self.find(str(item.id))
            if found is not None:
                envelopes.append(self.envelope(found, principal))
        return envelopes

    def find(self, action_item_id: str) -> tuple[Any, Any] | None:
        try:
            item = self._session.get(DecisionItemRecord, UUID(action_item_id))
        except ValueError:
            return None
        if item is None or item.kind != DELIVERY_REVIEW:
            return None
        task = self._session.get(TaskRecord, UUID(str(item.context_id)))
        return (item, task) if task is not None else None

    def envelope(self, item: tuple[Any, Any], principal: Principal) -> ActionEnvelope:
        decision_item, task = item
        submissions = self._tasks.repository.delivery_submissions(decision_item)
        latest = submissions[-1]
        snapshot = self._tasks.repository.delivery_snapshot(latest)
        reviewer_id = self._reviewer(latest)
        answered = [row for row in self._tasks.repository.delivery_decisions([latest.id])]
        waiting = not answered
        mine = str(principal.id) == reviewer_id
        preview: list[dict[str, str]] = [
            {"id": "summary", "label": "결과 요약", "value": str(snapshot.get("summary") or ""), "kind": "text"},
            {"id": "reporter", "label": "보고자", "value": self._members.waiting_on(latest.submitted_by)["display_name"], "kind": "person"},
        ]
        outputs = snapshot.get("outputs") or []
        if outputs:
            preview.append({"id": "outputs", "label": "산출물", "value": ", ".join(str(row["name"]) for row in outputs), "kind": "text"})
        steps = snapshot.get("checklist") or []
        if steps:
            done = sum(1 for row in steps if row.get("done"))
            preview.append({"id": "checklist", "label": "체크리스트", "value": f"{done}/{len(steps)} 완료", "kind": "text"})
        if task.due_date:
            preview.append({"id": "due_date", "label": "기한", "value": task.due_date.isoformat(), "kind": "date"})
        # What the reviewer is looking at was frozen when it was reported; say so when the work has moved since.
        if int(snapshot.get("task_version") or 0) + 1 != int(task.version) and waiting:
            preview.append({"id": "stale", "label": "안내", "value": "보고 이후 업무가 변경되었습니다", "kind": "state"})
        return ActionEnvelope(
            action_item_id=str(decision_item.id),
            kind="task.delivery",
            status=AWAITING_REVIEW if waiting else RESOLVED,
            subject=str(task.title),
            operation_label="업무 결과 확인",
            current_question=(
                "요청한 결과가 충족됐는지 확인하세요" if waiting else "이 결과는 이미 판단이 끝났습니다"
            ),
            preview=preview,
            allowed_commands=(
                [
                    ActionCommand("accept", "완료 인정", "primary"),
                    ActionCommand("request_changes", "보완 요청", "neutral", requires_reason=True),
                ]
                if waiting and mine and TASK_READ in principal.capabilities
                else []
            ),
            submission_version=int(latest.submission_version),
            waiting_on=self._members.waiting_on(reviewer_id if waiting else None),
            resource={"type": "task", "id": str(task.id)},
            expected_version=int(task.version),
        )

    def rounds(self, item: tuple[Any, Any], principal: Principal) -> list[dict[str, Any]]:
        decision_item, task = item
        self._require_participant(decision_item, task, principal)
        submissions = self._tasks.repository.delivery_submissions(decision_item)
        decisions = self._tasks.repository.delivery_decisions([row.id for row in submissions])
        rows = []
        for submission in submissions:
            rows.append(
                {
                    "submission_id": str(submission.id),
                    "submission_version": int(submission.submission_version),
                    "submitted_by": submission.submitted_by,
                    "submitted_at": submission.submitted_at.isoformat(),
                    "content_hash": submission.payload_hash,
                    "snapshot": self._tasks.repository.delivery_snapshot(submission),
                    "diff": submission.diff,
                    # A delivery stands on the outputs named in its own snapshot, not on adopted Evidence rows.
                    "evidence": [],
                    "evidence_hash": None,
                    "decisions": [
                        {
                            "review_decision_id": str(row.id),
                            "actor_member_id": row.actor_member_id,
                            "decision": row.decision,
                            "reason": row.reason,
                            "evidence_hash": None,
                            "decided_at": row.decided_at.isoformat(),
                        }
                        for row in decisions
                        if row.submission_id == submission.id
                    ],
                }
            )
        return rows

    def normalize(self, item: tuple[Any, Any], command: str, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("changes"):
            raise ActionError("결과 확인은 변경 항목을 받지 않습니다")
        normalized: dict[str, Any] = {"expected_version": _required_version(payload)}
        reason = str(payload.get("reason") or "").strip()
        if command == "request_changes" and not reason:
            raise ActionError("보완 요청에는 사유가 필요합니다")
        if reason:
            normalized["reason"] = reason
        return normalized

    def execute(self, principal: Principal, item: tuple[Any, Any], command: str, payload: dict[str, Any]) -> None:
        decision_item, task = item
        if str(principal.id) != self._reviewer(self._latest(decision_item)):
            raise ActionAccessDenied("principal cannot decide this action item")
        expected = _required_version(payload)
        if int(task.version) != expected:
            raise ActionError("task version is stale")
        submission = self._latest(decision_item)
        if command == "accept":
            self._tasks.accept_delivery(principal, task, submission, expected)
            return
        self._tasks.request_delivery_changes(principal, task, submission, expected, str(payload.get("reason") or ""))

    def discussion(self, item: tuple[Any, Any], principal: Principal) -> list[dict[str, Any]]:
        """The discussion lives on the request thread this work came from, not on the result question."""
        return []

    def is_replay(self, item: tuple[Any, Any], principal: Principal, command: str, payload: dict[str, Any]) -> bool:
        """The answer that was given is the receipt, pinned to the Task version it actually consumed."""
        decision_item, task = item
        submission = self._latest(decision_item)
        try:
            targeted = _required_version(payload)
        except ActionError:
            return False
        decided = "accept" if command == "accept" else "negotiate"
        for row in self._tasks.repository.delivery_decisions([submission.id]):
            if row.actor_member_id != str(principal.id) or row.decision != decided:
                continue
            if int(decision_facts(row.conditions).get(DECISION_VERSION) or -1) != targeted:
                continue
            if decided == "negotiate" and (row.reason or "") != str(payload.get("reason") or "").strip():
                continue
            return True
        return False

    def _latest(self, decision_item: Any) -> Any:
        return self._tasks.repository.delivery_submissions(decision_item)[-1]

    def _reviewer(self, submission: Any) -> str | None:
        assignment = self._session.scalar(
            select(ReviewAssignmentRecord)
            .where(ReviewAssignmentRecord.submission_id == submission.id)
            .order_by(ReviewAssignmentRecord.assigned_at.desc())
        )
        return str(assignment.reviewer_member_id) if assignment is not None else None

    def _require_participant(self, decision_item: Any, task: Any, principal: Principal) -> None:
        submissions = self._tasks.repository.delivery_submissions(decision_item)
        people = {str(row.submitted_by) for row in submissions} | {
            reviewer for reviewer in (self._reviewer(row) for row in submissions) if reviewer
        }
        if str(principal.id) not in people:
            raise ActionAccessDenied("principal cannot read this action item")


def action_handlers(session: Session, *, work_requests: Any, actions: Any, assignments: Any, tasks: Any = None) -> list[Any]:
    """Every origin that can put a question to a person, in the order a person should meet them.

    The module applications are passed in rather than rebuilt here, so every command runs the same operation the rest of
    the product runs, with that module's own rules and audit.
    """
    handlers: list[Any] = [
        WorkRequestActionHandler(session, work_requests),
        TaskAssignmentActionHandler(session, assignments),
    ]
    if tasks is not None:
        handlers.append(TaskDeliveryActionHandler(session, tasks))
    handlers.append(AxProposalActionHandler(session, actions))
    return handlers


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
