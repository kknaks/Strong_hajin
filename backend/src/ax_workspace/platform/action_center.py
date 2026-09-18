"""Reading every kind of pending judgement out of PostgreSQL as one canonical ActionItem projection.

Each handler knows one origin's tables and its policy; none of them re-implements another module's business rules. The
WorkRequest handler reads the canonical `Subject → ActionItem → Submission → ReviewAssignment` chain that the request
module already writes, so an adjustment round is the same ActionItem with a later Submission, never a new question.
"""
from __future__ import annotations

from ax_workspace.modules.actions.results import ActionRoundView, ActionDiscussionView

from ax_workspace.modules.ax_execution.command_contracts import COMMAND_CONTRACTS

from datetime import UTC, datetime
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
    ActionNotFound,
)
from ax_workspace.modules.actions.confirmation import (
    ATTACHABLE_ACTION_TYPES,
    SUPPORTED_ACTION_TYPES,
    AxConfirmationContext,
    AxReplayContext,
    decide_ax_confirmation,
    is_ax_replay,
    normalize_ax_draft as _normalize_ax_draft,
    required_base_submission_version as _required_base_submission_version,
    required_version as _required_version,
)
from ax_workspace.modules.actions.payloads import (
    attachment_draft_ids,
    proposed_changes,
    revision_changes,
    suggested_changes,
)
from ax_workspace.modules.actions.policy import (
    RETIRED_ACTION_TYPES,
    AssignmentActionContext,
    AxProposalActionContext,
    DeliveryActionContext,
    WorkRequestActionContext,
    available_assignment_commands,
    available_ax_proposal_commands,
    available_delivery_commands,
    available_work_request_commands,
)
from ax_workspace.modules.actions.replay import (
    AssignmentReplayContext,
    RecordedDeliveryDecision,
    WorkRequestReplayContext,
    is_assignment_replay,
    is_delivery_replay,
    is_work_request_replay,
)
from ax_workspace.modules.organization_access.domain import (
    ACTION_DECIDE,
    TASK_READ,
    Principal,
)
from ax_workspace.platform.actions import (
    ActionEvidenceReader,
    ActionPresenter,
    ActionServices,
    action_result_view,
)
from ax_workspace.modules.ax_execution.actions import action_payload_hash
from ax_workspace.platform.organization_access import SqlAlchemyOrganizationRepository
from ax_workspace.modules.work.requests import (
    DECISION_VERSION,
    EVIDENCE_HASH,
    WorkRequestAccessDenied as ActionAccessDenied,
    evidence_manifest,
    evidence_manifest_entry,
    evidence_manifest_hash,
    decision_facts,
)
from ax_workspace.platform.persistence import (
    ActionItemRecord,
    DecisionItemRecord,
    ResourceRelationshipRecord,
    ReviewAssignmentRecord,
    EvidenceRecord,
    MeetingRoomCreationAttemptRecord,
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

    def rounds(self, item: tuple[Any, Any], principal: Principal) -> list[ActionRoundView]:
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
                    "suggested_changes": suggested_changes(decision.conditions),
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
            proposed = proposed_changes(payload.get("changes"))
            if proposed:
                normalized["changes"] = proposed
        elif command == "revise":
            revision = revision_changes(payload.get("changes"))
            if revision:
                normalized["changes"] = revision
        elif payload.get("changes"):
            raise ActionError(f"'{command}'은(는) 변경 항목을 받지 않습니다")
        return normalized

    def execute(self, principal: Principal, item: tuple[Any, Any], command: str, payload: dict[str, Any]) -> None:
        decision_item, request = item
        # The offered envelope can predate another tab's commit. Serialize on
        # the owning request, then recognize that exact judgement's receipt
        # before invoking the version-checked domain command again.
        request = self._session.scalar(
            select(WorkRequestRecord)
            .where(WorkRequestRecord.id == request.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if request is None:
            raise ActionNotFound("action item was not found")
        self._session.refresh(decision_item)
        if self.is_replay((decision_item, request), principal, command, payload):
            return
        expected_version = _required_version(payload)
        reason = str(payload.get("reason") or "").strip()
        if command == "accept":
            self._work_requests.accept(principal, request.id, expected_version)
        elif command == "reject":
            self._work_requests.reject(principal, request.id, expected_version, reason)
        elif command == "adjust":
            proposed = proposed_changes(payload.get("changes"))
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
        submission = self._current_submission(decision_item)
        if submission is None:
            return False
        decided = self._session.scalar(
            select(ReviewDecisionRecord)
            .where(ReviewDecisionRecord.submission_id == submission.id)
            .order_by(ReviewDecisionRecord.decided_at.desc())
        )
        current_version = self._session.get(SubjectVersionRecord, submission.subject_version_id)
        previous = self._session.get(SubmissionRecord, submission.revises_id) if submission.revises_id else None
        previous_version = (
            self._session.get(SubjectVersionRecord, previous.subject_version_id) if previous is not None else None
        )
        stored = decision_facts(decided.conditions) if decided is not None else {}
        return is_work_request_replay(
            WorkRequestReplayContext(
                request_state=str(request.state),
                request_version=int(request.version),
                requester_id=str(request.requester_id),
                submission_actor_id=str(submission.submitted_by),
                revision_consumed_version=self._revision_consumed(submission),
                current_snapshot=dict(current_version.snapshot) if current_version is not None else None,
                previous_snapshot=dict(previous_version.snapshot) if previous_version is not None else None,
                decision_actor_id=str(decided.actor_member_id) if decided is not None else None,
                decision=str(decided.decision) if decided is not None else None,
                decision_expected_version=(
                    int(stored[DECISION_VERSION]) if stored.get(DECISION_VERSION) is not None else None
                ),
                decision_reason=str(decided.reason) if decided is not None and decided.reason is not None else None,
                decision_suggested_changes=suggested_changes(decided.conditions) if decided is not None else {},
            ),
            actor_id=str(principal.id),
            command=command,
            payload=payload,
        )

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
            raise ActionNotFound("action item was not found")

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
            actor = None
        elif status == AWAITING_REVIEW:
            assignment = self._active_assignment(submission)
            actor = assignment.reviewer_member_id if assignment else None
        else:
            actor = request.requester_id
        context = WorkRequestActionContext(status=status, actor_id=actor)
        if pending_only and not available_work_request_commands(context, principal):
            return None
        return ActionEnvelope(
            action_item_id=str(item.id),
            kind=WORK_REQUEST_ACCEPTANCE,
            status=status,
            subject=str(request.title),
            operation_label="업무 요청",
            current_question=_QUESTIONS.get((WORK_REQUEST_ACCEPTANCE, status), "이 요청은 이미 판단이 끝났습니다"),
            preview=self._preview(submission, request),
            allowed_commands=available_work_request_commands(context, principal),
            submission_version=int(submission.submission_version),
            waiting_on=self._members.waiting_on(actor),
            resource={"type": "work_request", "id": str(request.id)},
            expected_version=int(request.version),
            extra={"suggested_changes": self._suggested_changes(submission)},
        )

    def discussion(self, item: tuple[Any, Any], principal: Principal) -> list[ActionDiscussionView]:
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
        return suggested_changes(decided.conditions if decided else None)

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

    def __init__(
        self,
        session: Session,
        actions: Any,
        assignments: Any,
        material_drafts: Any = None,
        *,
        services: ActionServices,
        evidence_reader: ActionEvidenceReader | None = None,
    ) -> None:
        self._session = session
        self._services = services
        self._presenter = ActionPresenter(
            session,
            services=services,
            evidence_reader=evidence_reader,
        )
        self._members = MemberDirectory(session)
        self._actions = actions
        self._assignments = assignments
        self._material_drafts = material_drafts

    def find(self, action_item_id: str) -> Any | None:
        try:
            return self._session.get(ActionItemRecord, UUID(action_item_id))
        except ValueError:
            return None

    def rounds(self, item: Any, principal: Principal) -> list[ActionRoundView]:
        """Read the immutable canonical submissions; legacy rows remain a read-only compatibility fallback."""
        if str(item.owner_id) != str(principal.id):
            raise ActionNotFound("action item was not found")
        decision_item = self._decision(item)
        if decision_item is not None:
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
                        "evidence_hash": decision_facts(decision.conditions).get(EVIDENCE_HASH),
                        "decided_at": decision.decided_at.isoformat(),
                    }
                )
            rows = []
            for submission in submissions:
                version = self._session.get(SubjectVersionRecord, submission.subject_version_id)
                evidence = self._canonical_evidence(submission.id)
                rows.append(
                    {
                        "submission_id": str(submission.id),
                        "submission_version": int(submission.submission_version),
                        "evidence": evidence,
                        "evidence_hash": evidence_manifest_hash(evidence),
                        "submitted_by": submission.submitted_by,
                        "submitted_at": submission.submitted_at.isoformat(),
                        "content_hash": submission.payload_hash,
                        "snapshot": dict(version.snapshot) if version else {},
                        "diff": submission.diff,
                        "decisions": decisions.get(submission.id, []),
                    }
                )
            return rows
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

    def _decision(self, item: ActionItemRecord) -> DecisionItemRecord | None:
        decision = self._session.get(DecisionItemRecord, item.id)
        return decision if decision is not None and decision.kind == f"ax.{item.action_type}" else None

    def _current_submission(self, item: ActionItemRecord) -> SubmissionRecord | None:
        decision = self._decision(item)
        if decision is None:
            return None
        return self._session.scalar(
            select(SubmissionRecord)
            .where(SubmissionRecord.decision_item_id == decision.id)
            .order_by(SubmissionRecord.submission_version.desc())
        )

    def _canonical_evidence(self, submission_id: UUID) -> list[dict[str, str]]:
        rows = self._session.scalars(select(EvidenceRecord).where(EvidenceRecord.submission_id == submission_id)).all()
        return evidence_manifest(
            [evidence_manifest_entry(row.attachment_id, row.evidence_role, row.fixed_snapshot_ref) for row in rows]
        )

    def normalize(self, item: Any, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Freeze the exact canonical draft a confirmation will compare and execute."""
        if command == "cancel_assignment":
            if payload.get("draft") is not None or payload.get("changes") or str(payload.get("reason") or "").strip():
                raise ActionError("업무 요청 취소는 수정안이나 사유를 받지 않습니다")
            return {}
        normalized: dict[str, Any] = {"expected_version": _required_version(payload)}
        if command == "confirm":
            normalized["base_submission_version"] = _required_base_submission_version(payload)
            current = self._current_submission(item)
            if current is None:
                raise ActionError("canonical AX submission was not found")
            version = self._session.get(SubjectVersionRecord, current.subject_version_id)
            recovery_payload = self._unresolved_room_creation_payload(item)
            draft = (
                payload.get("draft")
                if payload.get("draft") is not None
                else recovery_payload or (dict(version.snapshot) if version else {})
            )
            if item.action_type in SUPPORTED_ACTION_TYPES:
                normalized_draft = _normalize_ax_draft(
                    item.action_type,
                    draft,
                    requester_id=str(item.owner_id),
                )
                if item.action_type == "task.create_self" and not normalized_draft.get("due_date"):
                    raise ActionError("업무 기한을 입력해 주세요")
                normalized["draft"] = normalized_draft
                if item.action_type in ATTACHABLE_ACTION_TYPES:
                    attachment_source = (
                        payload.get("attachment_draft_ids")
                        if "attachment_draft_ids" in payload
                        else (recovery_payload or {}).get("attachment_draft_ids")
                    )
                    normalized["attachment_draft_ids"] = attachment_draft_ids(attachment_source)
            elif payload.get("draft") is not None:
                raise ActionError("이 AX 제안은 수정 가능한 초안을 받지 않습니다")
        elif (
            payload.get("draft") is not None
            or payload.get("base_submission_version") is not None
            or payload.get("attachment_draft_ids") is not None
        ):
            raise ActionError(f"'{command}'은(는) 업무 초안을 받지 않습니다")
        if payload.get("changes") or str(payload.get("reason") or "").strip():
            raise ActionError("AX 제안 판단은 사유나 변경 항목을 받지 않습니다")
        return normalized

    def execute(self, principal: Principal, item: Any, command: str, payload: dict[str, Any]) -> None:
        if command == "cancel_assignment":
            assignment = self._assignment(item)
            if assignment is None:
                raise ActionError("task assignment was not found")
            self._assignments.cancel(principal, assignment.id)
            return
        if command in {"approve", "reject"}:
            if command == "reject":
                self._services.validate_action_rejection(
                    principal, str(item.id), item.action_type
                )
            if command == "reject" and self._material_drafts is not None:
                for draft in self._material_drafts.list(principal, item.id):
                    if draft["state"] == "staged":
                        self._material_drafts.discard(principal, item.id, UUID(draft["material_draft_id"]))
            self._actions.decide(principal, item.id, _required_version(payload), command)
            return
        if command != "confirm":  # pragma: no cover - the envelope already refused anything else
            raise ActionError(f"unsupported command {command}")
        self._confirm(principal, item, payload)

    def discussion(self, item: Any, principal: Principal) -> list[ActionDiscussionView]:
        """An AX proposal is judged on its preview and evidence; it carries no comment thread."""
        return []

    def is_replay(self, item: Any, principal: Principal, command: str, payload: dict[str, Any]) -> bool:
        """A repeat is a receipt only when version, base round and normalized final content all match."""
        if command == "cancel_assignment":
            assignment = self._assignment(item)
            return (
                assignment is not None
                and assignment.status == "cancelled"
                and assignment.assigned_by == str(principal.id)
            )
        if str(item.owner_id) != str(principal.id) or ACTION_DECIDE not in principal.capabilities:
            return False
        decision_item = self._decision(item)
        stored = (
            self._session.scalar(
                select(ReviewDecisionRecord)
                .join(SubmissionRecord, SubmissionRecord.id == ReviewDecisionRecord.submission_id)
                .where(SubmissionRecord.decision_item_id == decision_item.id)
                .order_by(ReviewDecisionRecord.decided_at.desc())
            )
            if decision_item is not None
            else None
        )
        normalized = payload
        if command == "confirm":
            try:
                normalized = self.normalize(item, command, payload)
            except ActionError:
                return False
        return is_ax_replay(
            AxReplayContext(
                state=str(item.state),
                version=int(item.version),
                has_decision_item=decision_item is not None,
                stored_actor_id=str(stored.actor_member_id) if stored is not None else None,
                stored_decision=str(stored.decision) if stored is not None else None,
                stored_conditions=dict(stored.conditions or {}) if stored is not None else {},
            ),
            actor_id=str(principal.id),
            command=command,
            normalized_payload=normalized,
        )

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
        if str(record.owner_id) != str(principal.id):
            raise ActionNotFound("action item was not found")
        decision = self._decision(record)
        submission = self._current_submission(record)
        version = self._session.get(SubjectVersionRecord, submission.subject_version_id) if submission is not None else None
        recovery_payload = self._unresolved_room_creation_payload(record)
        presented = self._presenter.present(
            record,
            principal,
            payload_override=recovery_payload or (dict(version.snapshot) if version is not None else None),
        )
        if recovery_payload is not None:
            presented["preview"] = [
                *presented["preview"],
                {
                    "id": "room_reservation_recovery",
                    "label": "회의실 예약 상태",
                    "value": "이전에 시도한 예약 결과 확인 필요",
                    "kind": "status",
                },
            ]
        resolved = decision is not None and decision.status == "resolved"
        pending = record.state == "pending" and not resolved
        assignment = self._assignment(record)
        visible_result = action_result_view(record.result)
        return ActionEnvelope(
            action_item_id=str(record.id),
            kind=f"ax.{record.action_type}",
            status=AWAITING_REVIEW if pending else RESOLVED,
            subject=str(presented["subject"]),
            operation_label=str(presented["operation_label"]),
            current_question=(
                RETIRED_ACTION_TYPES[record.action_type]
                if pending and record.action_type in RETIRED_ACTION_TYPES
                else "이전에 시도한 회의실 예약을 확인 필요 회의로 기록하세요"
                if pending and recovery_payload is not None
                else "AX가 준비한 변경을 확정할지 결정하세요"
                if pending
                else "이 제안은 이미 판단이 끝났습니다"
            ),
            preview=list(presented["preview"]),
            allowed_commands=available_ax_proposal_commands(
                AxProposalActionContext(
                    action_type=str(record.action_type),
                    pending=pending,
                    resolved=resolved,
                    has_submission=submission is not None,
                    obsolete=bool(presented.get("obsolete")),
                    assignment_status=str(assignment.status) if assignment is not None else None,
                    assigned_by=str(assignment.assigned_by) if assignment is not None else None,
                    allow_reject=recovery_payload is None,
                ),
                principal,
            ),
            submission_version=int(submission.submission_version) if submission is not None else 1,
            waiting_on=self._members.waiting_on(str(record.owner_id)) if pending else None,
            resource={"type": "action", "id": str(record.id)},
            expected_version=int(record.version),
            compatibility_commands=[ActionCommand('approve', '승인', 'primary')]
            if pending and ACTION_DECIDE in principal.capabilities and not presented.get('obsolete') and record.action_type in COMMAND_CONTRACTS else [],
            # Closing the round trip and carrying the same server-authored editor contract as the chat projection.
            extra={
                "derived_task_id": self._derived_task_id(record),
                "derived_meeting_id": self._derived_meeting_id(record),
                "material_drafts": self._material_drafts.list(principal, record.id) if self._material_drafts else [],
                "material_results": list((record.result or {}).get("material_results") or []),
                **({"execution_result": visible_result} if visible_result is not None else {}),
                **({"result_summary": presented["result_summary"]} if presented.get("result_summary") else {}),
                **({"edit_contract": presented["edit_contract"]} if presented.get("edit_contract") is not None else {}),
            },
        )

    def _unresolved_room_creation_payload(
        self, record: ActionItemRecord
    ) -> dict[str, Any] | None:
        if record.action_type != "meeting.reservation.create" or record.state != "pending":
            return None
        attempt = self._session.scalar(
            select(MeetingRoomCreationAttemptRecord)
            .where(
                MeetingRoomCreationAttemptRecord.owner_id == str(record.owner_id),
                MeetingRoomCreationAttemptRecord.request_key.like(f"action:{record.id}:%"),
                MeetingRoomCreationAttemptRecord.meeting_id.is_(None),
                MeetingRoomCreationAttemptRecord.status.in_(
                    ("pending", "booked", "needs_verification")
                ),
            )
            .order_by(MeetingRoomCreationAttemptRecord.updated_at.desc())
        )
        return dict(attempt.request_payload) if attempt is not None and attempt.request_payload else None

    def _assignment(self, record: ActionItemRecord) -> TaskAssignmentRecord | None:
        if record.action_type != "task.assign" or not isinstance(record.result, dict):
            return None
        try:
            assignment_id = UUID(str(record.result.get("assignment_id")))
        except (TypeError, ValueError):
            return None
        return self._session.get(TaskAssignmentRecord, assignment_id)

    def _confirm(self, principal: Principal, item: ActionItemRecord, payload: dict[str, Any]) -> None:
        locked = self._session.scalar(
            select(ActionItemRecord)
            .where(ActionItemRecord.id == item.id, ActionItemRecord.owner_id == str(principal.id))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked is None:
            raise ActionNotFound("action item was not found")
        if locked.state != "pending":
            if self.is_replay(locked, principal, "confirm", payload):
                return
        decision_item = self._decision(locked)
        submission = self._current_submission(locked)
        base_version = (
            self._session.get(SubjectVersionRecord, submission.subject_version_id)
            if submission is not None
            else None
        )
        base_snapshot = dict(base_version.snapshot) if base_version else {}
        assignment = self._active_assignment(submission) if submission is not None else None
        decision = decide_ax_confirmation(
            AxConfirmationContext(
                action_type=str(locked.action_type),
                state=str(locked.state),
                version=int(locked.version),
                owner_id=str(locked.owner_id),
                decision_open=decision_item is not None and decision_item.status == "open" and submission is not None,
                submission_version=int(submission.submission_version) if submission is not None else 0,
                base_snapshot=base_snapshot,
                has_active_assignment=assignment is not None,
            ),
            payload,
        )
        if decision_item is None or submission is None:  # pragma: no cover - rejected by the pure plan above
            raise ActionError("action is no longer pending")
        attachment_ids = list(decision.attachment_draft_ids)
        if attachment_ids:
            if self._material_drafts is None:
                raise ActionError("action material staging is not available")
            owner_type = "meeting" if locked.action_type == "meeting.reservation.create" else "task"
            self._material_drafts.validate_claim(
                principal, locked.id, [UUID(value) for value in attachment_ids], owner_type
            )
        prepared_final = self._services.prepare_action(
            principal,
            str(locked.id),
            locked.action_type,
            {
                **decision.final_draft,
                **(
                    {"_attachment_draft_ids": attachment_ids}
                    if locked.action_type in ATTACHABLE_ACTION_TYPES
                    else {}
                ),
            },
        )
        selected = submission
        now = datetime.now(UTC)
        superseded_assignment_id: UUID | None = None
        if decision.open_round is not None:
            round_effect = decision.open_round
            if assignment is not None:
                assignment.status = "superseded"
                assignment.resolution_kind = "revised"
                superseded_assignment_id = assignment.id
            next_version = SubjectVersionRecord(
                subject_id=decision_item.subject_id,
                version=round_effect.submission_version,
                content_hash=action_payload_hash(round_effect.snapshot),
                snapshot=round_effect.snapshot,
                captured_at=now,
            )
            self._session.add(next_version)
            self._session.flush()
            selected = SubmissionRecord(
                decision_item_id=decision_item.id,
                subject_version_id=next_version.id,
                submission_version=round_effect.submission_version,
                revises_id=submission.id,
                submitted_by=str(principal.id),
                payload_hash=next_version.content_hash,
                decision_policy_snapshot=dict(submission.decision_policy_snapshot or {}),
                diff=round_effect.diff,
                submitted_at=now,
            )
            self._session.add(selected)
            self._session.flush()
            # Editing the proposal does not detach the basis the proposing Turn actually used. Each Submission owns
            # its own immutable adoption rows, so later history never has to follow a mutable pointer backwards.
            for evidence in self._session.scalars(
                select(EvidenceRecord).where(EvidenceRecord.submission_id == submission.id)
            ).all():
                self._session.add(
                    EvidenceRecord(
                        submission_id=selected.id,
                        attachment_id=evidence.attachment_id,
                        evidence_role=evidence.evidence_role,
                        fixed_snapshot_ref=evidence.fixed_snapshot_ref,
                        mutable_source=evidence.mutable_source,
                        adopted_by=str(principal.id),
                        adopted_at=now,
                    )
                )
            assignment = ReviewAssignmentRecord(
                submission_id=selected.id,
                reviewer_member_id=str(principal.id),
                supersedes_assignment_id=superseded_assignment_id,
                status="pending",
                assigned_at=now,
            )
            self._session.add(assignment)
            self._session.flush()
        if assignment is None:
            raise ActionError("active AX review assignment was not found")
        basis = self._canonical_evidence(selected.id)
        review = ReviewDecisionRecord(
            review_assignment_id=assignment.id,
            submission_id=selected.id,
            actor_member_id=str(principal.id),
            decision="confirm",
            reason=None,
            conditions={
                "expected_version": decision.expected_version,
                "base_submission_version": decision.base_submission_version,
                "payload_hash": action_payload_hash(decision.final_snapshot),
                "attachment_draft_ids": attachment_ids,
                "_decision": {
                    "expected_version": decision.expected_version,
                    "evidence_hash": evidence_manifest_hash(basis),
                    "evidence_manifest": basis,
                },
            },
            decided_at=now,
        )
        self._session.add(review)
        self._session.flush()
        assignment.status = "decided"
        assignment.resolution_kind = "confirmed"
        assignment.resolution_ref = str(review.id)
        decision_item.status = "resolved"
        decision_item.resolved_at = now
        self._actions.execute_confirmed(
            principal,
            locked,
            {
                **prepared_final,
                **(
                    {"_attachment_draft_ids": attachment_ids}
                    if locked.action_type in ATTACHABLE_ACTION_TYPES
                    else {}
                ),
            },
            source_decision_item_id=decision_item.id,
            source_submission_id=selected.id,
            source_review_decision_id=review.id,
        )

    def _active_assignment(self, submission: SubmissionRecord) -> ReviewAssignmentRecord | None:
        return self._session.scalar(
            select(ReviewAssignmentRecord)
            .where(ReviewAssignmentRecord.submission_id == submission.id, ReviewAssignmentRecord.status == "pending")
            .order_by(ReviewAssignmentRecord.assigned_at.desc())
        )

    def _derived_task_id(self, record: ActionItemRecord) -> str | None:
        task_id = self._session.scalar(select(TaskRecord.id).where(TaskRecord.source_action_item_id == record.id))
        return str(task_id) if task_id else None

    def _derived_meeting_id(self, record: ActionItemRecord) -> str | None:
        """이 확인에서 나온 회의 — **지금은 언제나 없다.**

        회의가 확인에서 나오려면 회의 행이 「어느 확인에서 나왔는가」를 들고 있어야 하는데, SCAX-SPEC-004
        의 회의에는 그 계보 열이 없다(채팅이 회의를 만드는 경로가 멈춰 있다). 열을 되살리는 대신 **없다고
        답한다** — 화면은 이미 `None` 을 다루고, 그 경로가 새 모델로 돌아오면 여기부터 다시 잇는다.
        """
        del record
        return None


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
            .where(
                TaskAssignmentRecord.assignee_id == str(principal.id),
                TaskAssignmentRecord.status == "pending",
                # **요청에서 난 대기 담당은 여기 서지 않는다.** 그 질문은 요청 자신이 갖는다
                # (`work_request.acceptance`) — 같은 일을 두 항목으로 물으면 한쪽을 답해도 다른 쪽이
                # 남고, 배정 수락 경로로 답하면 있지도 않은 배정 회차를 찾다 깨진다.
                TaskAssignmentRecord.assignment_kind != "request_effect",
            )
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
        version = self._frozen_version(assignment) if not pending else None
        if pending:
            snapshot = {
                "title": task.title,
                "description": task.description,
                "due_date": task.due_date.isoformat() if task.due_date else None,
            }
            expected_version = int(task.version)
        elif version is not None:
            snapshot = dict(version.snapshot)
            expected_version = int(snapshot.get("task_version", version.version)) + int(
                assignment.status in {"declined", "cancelled"}
            )
        else:
            snapshot = {"title": "과거 업무 배정", "description": None, "due_date": None}
            expected_version = None
        preview: list[dict[str, str]] = []
        if snapshot.get('description'):
            preview.append({"id": "description", "label": "설명", "value": str(snapshot['description']), "kind": "text"})
        assigner = self._members.waiting_on(assignment.assigned_by)
        if assigner:
            preview.append({"id": "assigner", "label": "배정자", "value": assigner["display_name"], "kind": "person"})
        if snapshot.get('due_date'):
            preview.append({"id": "due_date", "label": "기한", "value": str(snapshot['due_date']), "kind": "date"})
        return ActionEnvelope(
            action_item_id=str(assignment.id),
            kind="task.assignment",
            status=AWAITING_REVIEW if pending else RESOLVED,
            subject=str(snapshot['title']),
            operation_label="업무 배정",
            current_question="이 업무 배정을 수락할지 결정하세요" if pending else "이 배정은 이미 판단이 끝났습니다",
            preview=preview,
            allowed_commands=available_assignment_commands(
                AssignmentActionContext(pending=pending, assignee_id=str(assignment.assignee_id)), principal
            ),
            submission_version=1,
            waiting_on=self._members.waiting_on(assignment.assignee_id if pending else None),
            resource={"type": "task", "id": str(task.id)},
            expected_version=expected_version,
        )

    def _frozen_version(self, assignment: Any) -> SubjectVersionRecord | None:
        if assignment.source_decision_item_id is None:
            return None
        return self._session.scalar(select(SubjectVersionRecord).join(
            SubmissionRecord, SubmissionRecord.subject_version_id == SubjectVersionRecord.id
        ).where(SubmissionRecord.decision_item_id == assignment.source_decision_item_id).order_by(SubmissionRecord.submission_version.desc()).limit(1))

    def rounds(self, item: tuple[Any, Any], principal: Principal) -> list[ActionRoundView]:
        assignment, task = item
        if str(assignment.assignee_id) != str(principal.id) and str(assignment.assigned_by) != str(principal.id):
            raise ActionNotFound("action item was not found")
        if assignment.source_decision_item_id is not None:
            submissions = self._session.scalars(select(SubmissionRecord).where(
                SubmissionRecord.decision_item_id == assignment.source_decision_item_id
            ).order_by(SubmissionRecord.submission_version)).all()
            rounds = []
            for submission in submissions:
                version = self._session.get(SubjectVersionRecord, submission.subject_version_id)
                decisions = self._session.scalars(select(ReviewDecisionRecord).where(
                    ReviewDecisionRecord.submission_id == submission.id
                ).order_by(ReviewDecisionRecord.decided_at)).all()
                rounds.append({
                    'submission_id': str(submission.id),
                    'submission_version': int(submission.submission_version),
                    'evidence': [], 'evidence_hash': None,
                    'submitted_by': submission.submitted_by,
                    'submitted_at': submission.submitted_at.isoformat(),
                    'content_hash': submission.payload_hash,
                    'snapshot': dict(version.snapshot), 'diff': submission.diff,
                    'decisions': [{
                        'review_decision_id': str(decision.id), 'actor_member_id': decision.actor_member_id,
                        'decision': 'decline' if decision.decision == 'reject' else decision.decision,
                        'reason': decision.reason, 'evidence_hash': None,
                        'decided_at': decision.decided_at.isoformat(),
                    } for decision in decisions],
                    **({'capture_kind': 'legacy_assignment_on_command'} if submission.decision_policy_snapshot.get('legacy_assignment_id') else {}),
                })
            return rounds
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
                "snapshot": ({"title": task.title, "description": task.description, "due_date": task.due_date.isoformat() if task.due_date else None}
                             if assignment.status == 'pending' else {'title': '과거 업무 배정', 'description': None, 'due_date': None}),
                **({'capture_kind': 'legacy_unavailable'} if assignment.status != 'pending' else {}),
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
        # Match the owning command's Task -> assignment lock order. Another tab
        # may have completed this exact decision after both controls were offered.
        task = self._session.get(TaskRecord, task.id, with_for_update=True, populate_existing=True)
        assignment = self._session.get(TaskAssignmentRecord, assignment.id, with_for_update=True, populate_existing=True)
        if task is None or assignment is None:
            raise ActionNotFound('action item was not found')
        if self.is_replay((assignment, task), principal, command, payload):
            return
        # The assignment carries no version of its own; the Task version the envelope showed is what is being answered.
        if int(task.version) != _required_version(payload):
            raise ActionError("task version is stale")
        if command == "accept":
            self._assignments.accept(principal, assignment.id, expected_task_version=_required_version(payload))
        else:
            self._assignments.decline(principal, assignment.id, str(payload.get("reason") or ""), expected_task_version=_required_version(payload))

    def discussion(self, item: tuple[Any, Any], principal: Principal) -> list[ActionDiscussionView]:
        """A direct assignment is answered on the spot; it carries no comment thread."""
        return []

    def is_replay(self, item: tuple[Any, Any], principal: Principal, command: str, payload: dict[str, Any]) -> bool:
        """The saved decision answers its consumed version even after the Task moves on."""
        assignment, task = item
        try:
            targeted = _required_version(payload)
        except ActionError:
            return False
        decision = None
        consumed_version = None
        if assignment.source_review_decision_id is not None:
            decision = self._session.get(ReviewDecisionRecord, assignment.source_review_decision_id)
            if decision is not None:
                consumed_version = decision_facts(decision.conditions).get(DECISION_VERSION)
                if consumed_version is None:
                    version = self._frozen_version(assignment)
                    if version is not None:
                        consumed_version = version.snapshot.get("task_version", version.version)
        return is_assignment_replay(
            AssignmentReplayContext(
                status=str(assignment.status),
                assignee_id=str(assignment.assignee_id),
                task_version=int(task.version),
                decline_reason=assignment.decline_reason,
                has_recorded_decision=assignment.source_review_decision_id is not None,
                decision_actor_id=str(decision.actor_member_id) if decision is not None else None,
                decision=str(decision.decision) if decision is not None else None,
                decision_consumed_version=int(consumed_version) if consumed_version is not None else None,
                decision_reason=decision.reason if decision is not None else None,
            ),
            actor_id=str(principal.id),
            command=command,
            expected_version=targeted,
            reason=payload.get("reason"),
        )


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
            allowed_commands=available_delivery_commands(
                DeliveryActionContext(waiting=waiting, reviewer_id=reviewer_id), principal
            ),
            submission_version=int(latest.submission_version),
            waiting_on=self._members.waiting_on(reviewer_id if waiting else None),
            resource={"type": "task", "id": str(task.id)},
            expected_version=int(task.version),
        )

    def rounds(self, item: tuple[Any, Any], principal: Principal) -> list[ActionRoundView]:
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

    def discussion(self, item: tuple[Any, Any], principal: Principal) -> list[ActionDiscussionView]:
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
        decisions = tuple(
            RecordedDeliveryDecision(
                actor_id=str(row.actor_member_id),
                decision=str(row.decision),
                expected_version=int(decision_facts(row.conditions).get(DECISION_VERSION) or -1),
                reason=row.reason,
            )
            for row in self._tasks.repository.delivery_decisions([submission.id])
        )
        return is_delivery_replay(
            decisions,
            actor_id=str(principal.id),
            command=command,
            expected_version=targeted,
            reason=payload.get("reason"),
        )

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
            raise ActionNotFound("action item was not found")


def action_handlers(
    session: Session,
    *,
    services: ActionServices,
    work_requests: Any,
    actions: Any,
    assignments: Any,
    tasks: Any = None,
    material_drafts: Any = None,
    evidence_reader: ActionEvidenceReader | None = None,
) -> list[Any]:
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
    handlers.append(
        AxProposalActionHandler(
            session,
            actions,
            assignments,
            material_drafts,
            services=services,
            evidence_reader=evidence_reader,
        )
    )
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
