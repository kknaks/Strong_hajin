"""PostgreSQL adapter for AX confirmation Actions and their canonical effects."""
from __future__ import annotations

from ax_workspace.modules.ax_execution.result_contracts import ActionProposalResult, ActionMaterialDraftView

from ax_workspace.modules.ax_execution.command_contracts import COMMAND_CONTRACTS

from datetime import UTC, datetime
from dataclasses import dataclass, fields
from typing import Any, Callable
from uuid import UUID
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ax_workspace.modules.actions.domain import ActionCenterApplication
from ax_workspace.modules.errors import ResourceNotFound
from ax_workspace.modules.actions.payloads import normalize_task_progress_batch as _normalize_task_progress_batch
from ax_workspace.modules.actions.confirmation import SUPPORTED_ACTION_TYPES
from ax_workspace.modules.actions.policy import CONFIRM_LABELS, RETIRED_ACTION_TYPES
from ax_workspace.modules.organization_access.application import OrganizationApplication
from ax_workspace.modules.organization_access.domain import ACTION_DECIDE, DAILY_REPORT_READ, DAILY_REPORT_SUBMIT, PROJECT_READ, TASK_ASSIGN, Principal
from ax_workspace.modules.meetings.application import MeetingApplication
from ax_workspace.modules.meetings.domain import MeetingError
from ax_workspace.modules.meetings.commands import (
    MeetingFollowupCommand,
    MeetingMaterialDetachCommand,
    MeetingMaterialLinkCommand,
    MeetingReservationInput,
    MeetingSpeakerCommand,
    MeetingSummaryAdoptCommand,
)
from ax_workspace.modules.reports.application import DailyReportApplication
from ax_workspace.modules.reports.commands import ReportEditCommand, ReportSubmitCommand
from ax_workspace.modules.ax_execution.actions import (
    ACTION_ITEM_COMMAND,
    ACTION_ITEM_COMMAND_TITLE,
    TURN_PROPOSAL_SLOT_TAKEN_MESSAGE,
    ActionError,
    TurnProposalSlotTaken,
    action_commands,
    action_payload_hash,
)
from ax_workspace.modules.work.task_creation import TaskCreateInput, TaskAssignmentInput
from ax_workspace.modules.work.requests import (
    DECISION_FACTS,
    EVIDENCE_HASH,
    EVIDENCE_MANIFEST,
    WorkRequestApplication,
    WorkRequestError,
    WorkRequestAccessDenied,
    evidence_manifest,
    evidence_manifest_entry,
    evidence_manifest_hash,
)
from ax_workspace.modules.work.application import TaskAccessDenied, TaskApplication, TaskError, TaskNotFound, TaskState
from ax_workspace.modules.work.drafts import (
    normalize_assigned_task_draft,
    normalize_task_draft,
    normalize_work_request_draft,
)
from ax_workspace.modules.work.projects import ProjectApplication
from ax_workspace.modules.work.checklist_commands import (
    ChecklistAddCommand,
    ChecklistArchiveCommand,
    ChecklistOrderCommand,
    ChecklistUpdateCommand,
)
from ax_workspace.modules.work.task_commands import TaskTransitionCommand, TaskUpdateCommand, TaskCompletionCommand, TaskReferenceCommand, TaskReferenceReleaseCommand, TaskReassignCommand
from ax_workspace.modules.work.material_commands import (TaskMaterialLinkCommand, TaskMaterialReferenceCommand, TaskMaterialDetachCommand)
from ax_workspace.modules.work.material_folders import MaterialFolderApplication
from ax_workspace.modules.work.materials import TaskMaterialApplication
from ax_workspace.modules.work.folder_commands import FolderCreateInput, FolderArchiveCommand, FolderMaterialDetachCommand
from ax_workspace.modules.work.project_commands import (
    ProjectCreateInput, ProjectMemberCommand, ProjectReleaseCommand, ProjectWorkCommand,
)
from ax_workspace.platform.organization_access import SqlAlchemyOrganizationRepository
from ax_workspace.platform.meetings import SqlAlchemyMeetingRepository
from ax_workspace.platform.persistence import (
    ActionItemAuditEventRecord,
    ActionItemRecord,
    ActionMaterialDraftRecord,
    ConversationRecord,
    ConversationTurnRecord,
    DecisionItemRecord,
    EvidenceRecord,
    MeetingRecord,
    ReviewAssignmentRecord,
    ReviewDecisionRecord,
    SubjectRecord,
    SubjectVersionRecord,
    SubmissionRecord,
    TaskAssignmentRecord,
    TaskRecord,
    TaskVersionRecord,
)
from ax_workspace.modules.work.assignments import TaskAssignmentApplication
from ax_workspace.platform.work_tasks import caused_by, SqlAlchemyAttachmentRepository, SqlAlchemyTaskRepository


from ax_workspace.modules.meetings.followups import MeetingFollowupApplication
from ax_workspace.modules.work.creation_commands import TaskCreationApplication
from ax_workspace.modules.notifications import NotificationApplication, NotificationReadCommand
from ax_workspace.modules.organization_access.commands import AssistantCharacterInput, ASSISTANT_CHARACTER_LABELS
from ax_workspace.modules.work.assignment_commands import AssignmentAcceptCommand, AssignmentDeclineCommand
from ax_workspace.modules.work.request_commands import WorkRequestAcceptCommand, WorkRequestRejectCommand, WorkRequestNegotiationCommand, WorkRequestAmendCommand, WorkRequestCreateInput, WorkRequestCommentCommand
from ax_workspace.modules.ax_execution.conversation_commands import (ConversationCreateInput, ConversationMessageCommand, ConversationCancelCommand, ConversationRetryCommand)
from ax_workspace.modules.ax_execution.conversations import ConversationApplication
from ax_workspace.modules.work.action_materials import ActionMaterialDraftApplication
from ax_workspace.modules.work.material_commands import ActionMaterialLinkCommand, ActionMaterialDiscardCommand

POST_COMMIT_RECEIPT_PENDING = "_scax_post_commit_receipt_pending"


def action_result_view(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep the durable finalization fence out of public approval receipts."""
    if not isinstance(result, dict):
        return result
    if result.get(POST_COMMIT_RECEIPT_PENDING):
        return None
    return {key: value for key, value in result.items() if key != POST_COMMIT_RECEIPT_PENDING}


ActionEvidenceReader = Callable[[Principal, UUID], list[dict[str, Any]]]

CURRENT_MEETING_ACTION_TYPES = frozenset({
    "meeting.reservation.create",
    "meeting.quick_start",
    "meeting.info.update",
    "meeting.cancel",
    "meeting.note.delete",
    "meeting.start",
    "meeting.end",
    "meeting.finalize.retry",
    "meeting.todo.promote",
    "meeting.todo.remove",
    "meeting.agenda.add",
    "meeting.agenda.update",
    "meeting.agenda.remove",
    "meeting.memo.write",
    "meeting.material.remove",
    "meeting.share_many",
    "meeting.share.revoke",
})
LEGACY_MEETING_ACTION_TYPES = frozenset({
    "meeting.update",
    "meeting.share",
    "meeting.revoke_share",
    "meeting.note.create",
    "meeting.note.save",
    "meeting.note.finalize",
    "meeting.material.attach_link",
    "meeting.material.detach",
    "meeting.summary.adopt",
    "meeting.speaker.assign",
    "meeting.followup.task",
    "meeting.followup.request",
})
MEETING_ACTION_TYPES = CURRENT_MEETING_ACTION_TYPES | LEGACY_MEETING_ACTION_TYPES
MEETING_CALLBACK_ACTION_TYPES = CURRENT_MEETING_ACTION_TYPES | frozenset({
    "meeting.update",
    "meeting.share",
    "meeting.revoke_share",
    "meeting.note.create",
    "meeting.note.save",
    "meeting.note.finalize",
})


@dataclass(frozen=True)
class ActionServices:
    """Named, session-bound factories supplied by bootstrap, never resolved by string.

    Lazy edges break the Task-origin / Action-preview read cycle. Each factory
    retains the approval's session and the composition root's complete dependencies.
    """

    tasks: Callable[[], TaskApplication]
    assignments: Callable[[], TaskAssignmentApplication]
    #: 생성 세 경로와 그 멱등 계약이 만나는 자리. 확인된 action 하나가 **생성 의도 하나**다.
    task_creation: Callable[[], TaskCreationApplication]
    meetings: Callable[[], MeetingApplication]
    prepare_action: Callable[[Principal, str, str, dict[str, Any]], dict[str, Any]]
    validate_action_rejection: Callable[[Principal, str, str], None]
    meeting_action: Callable[[Principal, str, dict[str, Any]], Any]
    reports: Callable[[], DailyReportApplication]
    projects: Callable[[], ProjectApplication]
    organization: Callable[[], OrganizationApplication]
    action_center: Callable[[], ActionCenterApplication]
    material_folders: Callable[[], MaterialFolderApplication]
    materials: Callable[[], TaskMaterialApplication]
    meeting_followups: Callable[[], MeetingFollowupApplication]
    notifications: Callable[[], NotificationApplication]
    work_requests: Callable[[], WorkRequestApplication]
    conversations: Callable[[], ConversationApplication]
    action_materials: Callable[[], ActionMaterialDraftApplication]

    def __post_init__(self) -> None:
        for dependency in fields(self):
            if not callable(getattr(self, dependency.name)):
                raise TypeError(f"Action service {dependency.name} must be supplied by bootstrap")


class SqlAlchemyActionRepository:
    def __init__(
        self,
        session: Session,
        *,
        services: ActionServices,
        evidence_reader: ActionEvidenceReader | None = None,
    ) -> None:
        self._session = session
        self._services = services
        self._evidence_reader = evidence_reader
        self._presenter = ActionPresenter(
            session,
            services=services,
            evidence_reader=evidence_reader,
        )

    def propose(
        self,
        owner_id: str,
        execution_id: UUID,
        action_type: str,
        title: str,
        payload: dict[str, Any],
    ) -> ActionItemRecord:
        """Return one recoverable effect slot per action type and turn execution.

        A provider redelivery of the same judgement reuses the first proposal rather than
        creating a second effect. A genuinely different payload is a different judgement and is
        refused, so it waits for the next turn instead of being answered with someone else's
        confirmation. Intentional repeated effects belong in a later turn either way.
        """
        if action_type in RETIRED_ACTION_TYPES:
            # 걷은 계약은 정책이 막는다 — tool registry 에 없다는 사실에 기대지 않는다.
            raise ActionError(RETIRED_ACTION_TYPES[action_type])
        turn = self._session.scalar(
            select(ConversationTurnRecord)
            .join(ConversationRecord, ConversationRecord.id == ConversationTurnRecord.conversation_id)
            .where(
                ConversationTurnRecord.execution_id == execution_id,
                ConversationRecord.owner_id == owner_id,
            )
            .with_for_update()
        )
        if turn is None:
            raise ValueError("delegated action execution was not found")
        existing = self._session.scalar(
            select(ActionItemRecord).where(
                ActionItemRecord.execution_id == execution_id,
                ActionItemRecord.action_type == action_type,
            )
        )
        payload = self._canonical_proposal(owner_id, turn, action_type, payload)
        payload_hash = action_payload_hash(payload)
        if existing is not None:
            # A redelivery of the same judgement gets the same receipt. Anything else is a different
            # judgement: answering it with this one would let AX report a change nobody will ever see.
            if existing.payload_hash != payload_hash:
                raise TurnProposalSlotTaken(TURN_PROPOSAL_SLOT_TAKEN_MESSAGE)
            return existing
        now = datetime.now(UTC)
        action = ActionItemRecord(
            owner_id=owner_id,
            conversation_id=turn.conversation_id,
            turn_id=turn.id,
            execution_id=execution_id,
            action_type=action_type,
            title=title,
            payload=payload,
            payload_hash=payload_hash,
            state="pending",
            version=1,
            result=None,
            audit_ref=None,
            created_at=now,
            decided_at=None,
        )
        self._session.add(action)
        self._session.flush()
        self._open_canonical_submission(action, turn, payload_hash, now)
        self._audit(action, owner_id, "action.proposed", {})
        return action

    def _canonical_proposal(
        self,
        owner_id: str,
        turn: ConversationTurnRecord,
        action_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """The stored form of a proposal, so the same judgement always hashes the same way."""
        if action_type == "meeting.reservation.create":
            return self._freeze_meeting_proposal(owner_id, turn, payload)
        if action_type == "task.progress.batch":
            return _normalize_task_progress_batch(payload)
        return payload

    def _freeze_meeting_proposal(
        self,
        owner_id: str,
        turn: ConversationTurnRecord,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Freeze the current reservation contract before a person reviews it."""
        del owner_id, turn
        try:
            return MeetingReservationInput.model_validate(payload).model_dump(mode="json")
        except (TypeError, ValueError) as error:
            raise MeetingError(str(error)) from error

    def _meeting_source_label(self, owner_id: str, source_type: str, source_id: str) -> str | None:
        """Re-authorize a previously observed resource before freezing it into a MeetingNote."""
        principal = SqlAlchemyOrganizationRepository(self._session).principal_for(owner_id)
        if principal is None:
            return None
        try:
            if source_type == "task":
                return str(self._tasks_for_source().get(principal, UUID(source_id))["title"])
            if source_type == "meeting":
                # 새 회의 모델의 상세는 `{"meeting": {...}, "agendas": [...]}` 이고 제목은 그 안에 있다.
                meeting = self._services.meetings().get(principal, UUID(source_id))["meeting"]
                return str(meeting.get("title") or meeting.get("title_candidate") or "")
        except (MeetingError, TaskError, ValueError):
            return None
        return None

    def _tasks_for_source(self) -> TaskApplication:
        return self._services.tasks()

    def _open_canonical_submission(
        self,
        action: ActionItemRecord,
        turn: ConversationTurnRecord,
        payload_hash: str,
        now: datetime,
    ) -> None:
        """Write the AX proposal into the shared immutable judgement ledger.

        `action_items` remains the transitional compatibility/effect row. The Subject and Submission are the
        canonical question and content: the Subject points at the proposing Turn, while the DecisionItem keeps the
        Conversation context and reuses the public ActionItem identity.
        """
        subject = SubjectRecord(
            subject_type="ax_proposal",
            owning_resource_type="conversation_turn",
            owning_resource_id=str(turn.id),
            created_at=now,
        )
        self._session.add(subject)
        self._session.flush()
        version = SubjectVersionRecord(
            subject_id=subject.id,
            version=1,
            content_hash=payload_hash,
            snapshot=dict(action.payload),
            captured_at=now,
        )
        decision = DecisionItemRecord(
            id=action.id,
            kind=f"ax.{action.action_type}",
            subject_id=subject.id,
            context_type="conversation",
            context_id=str(action.conversation_id),
            effect_identity=f"{action.execution_id}:{action.action_type}",
            status="open",
            created_at=now,
        )
        self._session.add_all([version, decision])
        self._session.flush()
        submission = SubmissionRecord(
            decision_item_id=decision.id,
            subject_version_id=version.id,
            submission_version=1,
            submitted_by="ax",
            payload_hash=payload_hash,
            decision_policy_snapshot={
                "proposer": "ax",
                "reviewer_member_id": action.owner_id,
                "action_type": action.action_type,
            },
            diff=None,
            submitted_at=now,
        )
        self._session.add(submission)
        self._session.flush()
        self._session.add(
            ReviewAssignmentRecord(
                submission_id=submission.id,
                reviewer_member_id=action.owner_id,
                status="pending",
                assigned_at=now,
            )
        )
        # A material retrieved by the proposing Turn is the proposal's frozen basis. The excerpt stays in the
        # conversation evidence table; the judgement ledger records only the attachment and immutable locator.
        for evidence in self._readable_turn_materials(action.owner_id, turn.id):
            self._session.add(
                EvidenceRecord(
                    submission_id=submission.id,
                    attachment_id=UUID(str(evidence["attachment_id"])),
                    evidence_role="supporting",
                    fixed_snapshot_ref=(
                        f"conversation_content_evidence:{evidence.get('evidence_id') or evidence.get('chunk_id')}"
                        f"@{evidence['integrity_ref']}"
                    ),
                    mutable_source=False,
                    adopted_by="ax",
                    adopted_at=now,
                )
            )

    def _readable_turn_materials(self, owner_id: str, turn_id: UUID) -> list[dict[str, Any]]:
        if self._evidence_reader is None:
            return []
        principal = SqlAlchemyOrganizationRepository(self._session).principal_for(owner_id)
        if principal is None:
            return []
        return self._evidence_reader(principal, turn_id)

    def action(self, action_id: UUID, owner_id: str, *, lock: bool = False) -> ActionItemRecord | None:
        statement = select(ActionItemRecord).where(
            ActionItemRecord.id == action_id,
            ActionItemRecord.owner_id == owner_id,
        )
        return self._session.scalar(statement.with_for_update().execution_options(populate_existing=True) if lock else statement)

    def subject_label(self, action: ActionItemRecord) -> str:
        return action_subject_label(action, self._canonical_payload(action))

    def list_for(self, owner_id: str) -> list[ActionItemRecord]:
        return list(
            self._session.scalars(
                select(ActionItemRecord)
                .where(ActionItemRecord.owner_id == owner_id)
                .order_by(ActionItemRecord.created_at.desc(), ActionItemRecord.id.desc())
            )
        )

    def for_conversation(self, conversation_id: UUID) -> list[ActionItemRecord]:
        return list(
            self._session.scalars(
                select(ActionItemRecord)
                .where(ActionItemRecord.conversation_id == conversation_id)
                .order_by(ActionItemRecord.created_at, ActionItemRecord.id)
            )
        )

    def resolve(
        self,
        action: ActionItemRecord,
        actor_id: str,
        decision: str,
        result: dict[str, Any] | None,
    ) -> None:
        action.state = "approved" if decision == "approve" else "rejected"
        action.version += 1
        action.result = result
        action.audit_ref = str(action.id)
        action.decided_at = datetime.now(UTC)
        self._resolve_canonical_projection(action, actor_id, decision)
        self._audit(action, actor_id, f"action.{action.state}", {"result": result or {}})

    def _resolve_canonical_projection(self, action: ActionItemRecord, actor_id: str, decision: str) -> None:
        """Keep compatibility approvals/rejections inside the canonical immutable judgement history."""
        item = self._session.get(DecisionItemRecord, action.id)
        if item is None or item.status == "resolved":
            return
        submission = self._session.scalar(
            select(SubmissionRecord)
            .where(SubmissionRecord.decision_item_id == item.id)
            .order_by(SubmissionRecord.submission_version.desc())
        )
        if submission is None:
            return
        assignment = self._session.scalar(
            select(ReviewAssignmentRecord)
            .where(ReviewAssignmentRecord.submission_id == submission.id, ReviewAssignmentRecord.status == "pending")
            .order_by(ReviewAssignmentRecord.assigned_at.desc())
        )
        if assignment is None:
            return
        now = action.decided_at or datetime.now(UTC)
        basis = evidence_manifest(
            [
                evidence_manifest_entry(row.attachment_id, row.evidence_role, row.fixed_snapshot_ref)
                for row in self._session.scalars(
                    select(EvidenceRecord).where(EvidenceRecord.submission_id == submission.id)
                ).all()
            ]
        )
        review = ReviewDecisionRecord(
            review_assignment_id=assignment.id,
            submission_id=submission.id,
            actor_member_id=actor_id,
            decision=decision,
            reason=None,
            conditions={
                "expected_version": int(action.version) - 1,
                DECISION_FACTS: {
                    "expected_version": int(action.version) - 1,
                    EVIDENCE_HASH: evidence_manifest_hash(basis),
                    EVIDENCE_MANIFEST: basis,
                },
            },
            decided_at=now,
        )
        self._session.add(review)
        self._session.flush()
        assignment.status = "decided"
        assignment.resolution_kind = "confirmed" if decision == "approve" else "rejected"
        assignment.resolution_ref = str(review.id)
        item.status = "resolved"
        item.resolved_at = now

    def view(self, action: ActionItemRecord, principal: Principal | None = None) -> ActionProposalResult:
        """Canonical Action row plus its structured, permission-safe presentation for `principal`."""
        presented = self._presenter.present(action, principal, payload_override=self._canonical_payload(action))
        can_decide = principal is not None and ACTION_DECIDE in principal.capabilities
        commands = action_commands(action.state, can_decide, obsolete=bool(presented.get("obsolete")))
        if presented.get("edit_contract") is not None and action.state == "pending" and can_decide and not presented.get("obsolete"):
            commands = [
                {"id": "confirm", "label": CONFIRM_LABELS[action.action_type], "tone": "primary"},
                {"id": "reject", "label": "거절", "tone": "neutral"},
            ]
        assignment = self._assignment(action)
        if (
            action.state == "approved"
            and action.action_type == "task.assign"
            and assignment is not None
            and assignment.status == "pending"
            and principal is not None
            and assignment.assigned_by == str(principal.id)
            and TASK_ASSIGN in principal.capabilities
        ):
            commands = [{"id": "cancel_assignment", "label": "취소", "tone": "danger"}]
        result = action_result_view(action.result)
        if isinstance(result, dict) and assignment is not None:
            result["status"] = assignment.status
        return {
            "action_id": str(action.id),
            "conversation_id": str(action.conversation_id),
            "turn_id": str(action.turn_id),
            "action_type": action.action_type,
            "title": action.title,
            "state": action.state,
            "version": action.version,
            "payload_hash": action.payload_hash,
            "payload_summary": self._summary(action),
            "result": result,
            "material_drafts": self._material_drafts(action, principal),
            "material_results": list((action.result or {}).get("material_results") or []),
            "audit_ref": action.audit_ref,
            **presented,
            "commands": commands,
        }

    def _assignment(self, action: ActionItemRecord) -> TaskAssignmentRecord | None:
        if action.action_type != "task.assign" or not isinstance(action.result, dict):
            return None
        try:
            assignment_id = UUID(str(action.result.get("assignment_id")))
        except (TypeError, ValueError):
            return None
        return self._session.get(TaskAssignmentRecord, assignment_id)

    def _material_drafts(self, action: ActionItemRecord, principal: Principal | None) -> list[ActionMaterialDraftView]:
        if principal is None or str(principal.id) != str(action.owner_id):
            return []
        rows = self._session.scalars(
            select(ActionMaterialDraftRecord)
            .where(
                ActionMaterialDraftRecord.action_id == action.id,
                ActionMaterialDraftRecord.owner_id == str(principal.id),
                ActionMaterialDraftRecord.state.in_(("staged", "claimed")),
            )
            .order_by(ActionMaterialDraftRecord.created_at, ActionMaterialDraftRecord.id)
        ).all()
        return [
            {
                "material_draft_id": str(row.id),
                "action_item_id": str(row.action_id),
                "source_kind": row.source_kind,
                "name": row.name,
                "content_type": row.content_type,
                "size_bytes": int(row.size_bytes),
                "url": row.source_ref if row.source_kind == "external_link" else None,
                "integrity_ref": row.integrity_ref,
                "state": row.state,
                "expires_at": (row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)).isoformat(),
                "claimed_task_id": str(row.claimed_task_id) if row.claimed_task_id else None,
                "claimed_meeting_id": str(row.claimed_meeting_id) if row.claimed_meeting_id else None,
            }
            for row in rows
        ]

    def _canonical_payload(self, action: ActionItemRecord) -> dict[str, Any]:
        item = self._session.get(DecisionItemRecord, action.id)
        if item is None:
            return dict(action.payload)
        submission = self._session.scalar(
            select(SubmissionRecord)
            .where(SubmissionRecord.decision_item_id == item.id)
            .order_by(SubmissionRecord.submission_version.desc())
        )
        version = self._session.get(SubjectVersionRecord, submission.subject_version_id) if submission else None
        return dict(version.snapshot) if version is not None else dict(action.payload)

    def _audit(self, action: ActionItemRecord, actor_id: str, event_type: str, payload: dict[str, Any]) -> None:
        self._session.add(
            ActionItemAuditEventRecord(
                action_id=action.id,
                actor_id=actor_id,
                event_type=event_type,
                payload=payload,
                occurred_at=datetime.now(UTC),
            )
        )

    @staticmethod
    def _summary(action: ActionItemRecord) -> str:
        if action.action_type == "meeting.reservation.create":
            return f"회의 생성: {action.payload['title']}"
        if action.action_type == "meeting.share":
            return "회의 공유"
        if action.action_type == "work_request.create":
            return f"업무 요청: {action.payload['title']}"
        if action.action_type == "daily_report.edit":
            return "일일보고 초안 수정"
        if action.action_type == "task.progress.batch":
            return f"업무 진행 {len(action.payload.get('operations') or [])}건"
        if action.action_type.startswith("task.checklist."):
            return _OPERATION_LABELS.get(action.action_type, action.action_type)
        if action.action_type == "task.assign":
            return f"업무 배정: {action.payload['title']} → {action.payload['assignee_id']}"
        if action.action_type == ACTION_ITEM_COMMAND:
            # Deliberately says nothing about the target: the row outlives the reader's access to the work it names.
            return ACTION_ITEM_COMMAND_TITLE
        return action.action_type


class SqlAlchemyActionExecutor:
    """Invokes existing public application commands inside the Action transaction."""

    def __init__(
        self,
        session: Session,
        action_materials: Any = None,
        *,
        services: ActionServices,
        work_requests: WorkRequestApplication,
    ) -> None:
        self._session = session
        self._services = services
        self._action_materials = action_materials
        self._work_requests = work_requests

    def execute(
        self,
        principal: Principal,
        action: ActionItemRecord,
        *,
        payload: dict[str, Any] | None = None,
        source_decision_item_id: UUID | None = None,
        source_submission_id: UUID | None = None,
        source_review_decision_id: UUID | None = None,
    ) -> dict[str, Any]:
        if action.action_type in RETIRED_ACTION_TYPES:
            raise ActionError(RETIRED_ACTION_TYPES[action.action_type])
        # Legacy decisions select the stored payload here; canonical confirmations
        # pass their final Submission. No branch may read the proposal again.
        confirmed_payload = dict(action.payload if payload is None else payload)
        confirmed_payload = self._services.prepare_action(
            principal, str(action.id), action.action_type, confirmed_payload
        )
        # Everything this approval causes says which confirmation carried it; the actor stays the approver.
        with caused_by(f"action_item:{action.id}"):
            return self._execute(
                principal,
                action,
                payload=confirmed_payload,
                source_decision_item_id=source_decision_item_id,
                source_submission_id=source_submission_id,
                source_review_decision_id=source_review_decision_id,
            )

    def _execute(
        self,
        principal: Principal,
        action: ActionItemRecord,
        *,
        payload: dict[str, Any],
        source_decision_item_id: UUID | None = None,
        source_submission_id: UUID | None = None,
        source_review_decision_id: UUID | None = None,
    ) -> dict[str, Any]:
        if action.action_type in MEETING_CALLBACK_ACTION_TYPES:
            result = self._services.meeting_action(principal, action.action_type, payload)
            if action.action_type == "meeting.reservation.create":
                result = self._claim_action_materials(principal, action, payload, result)
                return self._attach_meeting_references(principal, payload, result)
            return result
        if action.action_type == "work_request.create":
            command = WorkRequestCreateInput.model_validate(payload).for_requester(str(principal.id))
            # 확정된 action 하나가 의도 하나다 — `action.id` 를 재실행 식별(`causation_key`)과
            # **새 멱등 키 자리**에 함께 싣는다 (WORK-001 § 멱등 키).
            return self._services.task_creation().create_work_request(
                principal, **command.model_dump(), causation_key=str(action.id), idempotency_key=str(action.id),
                source_action_item_id=action.id,
                source_decision_item_id=source_decision_item_id,
                source_submission_id=source_submission_id,
                source_review_decision_id=source_review_decision_id,
            )
        if action.action_type == "action.material.link.stage":
            command = ActionMaterialLinkCommand.model_validate(payload)
            return self._services.action_materials().stage_link(principal, command.action_item_id, url=command.url, label=command.label)
        if action.action_type == "action.material.draft.discard":
            command = ActionMaterialDiscardCommand.model_validate(payload)
            return self._services.action_materials().discard(principal, command.action_item_id, command.material_draft_id)
        if action.action_type == "conversation.create":
            command = ConversationCreateInput.model_validate(payload)
            return self._services.conversations().create(principal, command.title)
        if action.action_type == "conversation.message.send":
            command = ConversationMessageCommand.model_validate(payload)
            return self._services.conversations().accept_message(principal, **command.model_dump(exclude={'context'}), context=command.references())
        if action.action_type == "conversation.turn.cancel":
            command = ConversationCancelCommand.model_validate(payload)
            return self._services.conversations().cancel(principal, **command.model_dump())
        if action.action_type == "conversation.turn.retry":
            command = ConversationRetryCommand.model_validate(payload)
            return self._services.conversations().retry(principal, **command.model_dump())
        if action.action_type == "notification.mark_read":
            command = NotificationReadCommand.model_validate(payload)
            return self._services.notifications().mark_read(principal, command.notification_id)
        if action.action_type == "assistant.character.set":
            command = AssistantCharacterInput.model_validate(payload)
            return self._services.organization().set_assistant_character(principal, **command.model_dump())
        if action.action_type == "work_request.comment.add":
            command = WorkRequestCommentCommand.model_validate(payload)
            return self._work_requests.add_comment(principal, **command.model_dump())
        if action.action_type == "meeting.material.attach_link":
            command = MeetingMaterialLinkCommand.model_validate(payload)
            return self._services.meetings().attach_material_link(principal, **command.model_dump())
        if action.action_type == "meeting.material.detach":
            command = MeetingMaterialDetachCommand.model_validate(payload)
            return self._services.meetings().detach_material(principal, **command.model_dump())
        if action.action_type == "meeting.summary.adopt":
            command = MeetingSummaryAdoptCommand.model_validate(payload)
            return self._services.meetings().adopt_summary(principal, **command.model_dump())
        if action.action_type == "meeting.speaker.assign":
            command = MeetingSpeakerCommand.model_validate(payload)
            return self._services.meetings().assign_speaker_identity(principal, **command.model_dump())
        if action.action_type in {"meeting.followup.task", "meeting.followup.request"}:
            command = MeetingFollowupCommand.model_validate(payload)
            wanted = "task" if action.action_type == "meeting.followup.task" else "work_request"
            if command.kind != wanted:
                raise ActionError("followup kind does not match the approved operation")
            return self._services.meeting_followups().promote(principal, **command.model_dump())
        if action.action_type == "task.material.attach_link":
            command = TaskMaterialLinkCommand.model_validate(payload)
            return self._services.materials().attach_link(principal, **command.model_dump())
        if action.action_type == "task.material.attach_reference":
            command = TaskMaterialReferenceCommand.model_validate(payload)
            return self._services.materials().attach_reference(principal, **command.model_dump())
        if action.action_type == "task.material.detach":
            command = TaskMaterialDetachCommand.model_validate(payload)
            return self._services.materials().detach(principal, **command.model_dump())
        if action.action_type == "task.reassign":
            command = TaskReassignCommand.model_validate(payload)
            return self._services.assignments().reassign(principal, **command.model_dump())
        if action.action_type == "task.completion.submit":
            command = TaskCompletionCommand.model_validate(payload)
            return self._services.tasks().submit_completion(principal, **command.model_dump())
        if action.action_type == "task.reference.add":
            command = TaskReferenceCommand.model_validate(payload)
            return self._services.tasks().add_reference(principal, command.task_id, command.referenced_task_id)
        if action.action_type == "task.reference.release":
            command = TaskReferenceReleaseCommand.model_validate(payload)
            return self._services.tasks().release_reference(principal, command.task_id, command.reference_id)
        if action.action_type == "project.create":
            command = ProjectCreateInput.model_validate(payload)
            return self._services.projects().create(principal, **command.model_dump())
        if action.action_type == "material_folder.create":
            command = FolderCreateInput.model_validate(payload)
            return self._services.material_folders().create(principal, **command.model_dump())
        if action.action_type == "material_folder.archive":
            command = FolderArchiveCommand.model_validate(payload)
            return self._services.material_folders().archive(principal, command.folder_id)
        if action.action_type == "material_folder.detach":
            command = FolderMaterialDetachCommand.model_validate(payload)
            return self._services.material_folders().detach(principal, command.folder_id, command.material_id)
        if action.action_type == "project.assign_member":
            command = ProjectMemberCommand.model_validate(payload)
            return self._services.projects().assign(principal, **command.model_dump())
        if action.action_type == "project.release_member":
            command = ProjectReleaseCommand.model_validate(payload)
            return self._services.projects().release(principal, **command.model_dump())
        if action.action_type == "project.plan_work":
            command = ProjectWorkCommand.model_validate(payload)
            return self._services.assignments().plan_project_work(principal, **command.model_dump())
        if action.action_type == "daily_report.edit":
            command = ReportEditCommand.model_validate(payload)
            return self._services.reports().edit(
                principal,
                str(command.report_id),
                str(command.draft_id),
                command.expected_version,
                command.body,
                [item.model_dump(mode="json", exclude_unset=True) for item in command.include_source_refs],
                [item.model_dump(mode="json", exclude_unset=True) for item in command.exclude_source_refs],
            )
        if action.action_type == "daily_report.submit":
            command = ReportSubmitCommand.model_validate(payload)
            return self._services.reports().submit(
                principal,
                str(command.report_id),
                str(command.draft_id),
                command.expected_version,
                command.reason,
            )
        if action.action_type == "task.create_self":
            command = TaskCreateInput.model_validate({key: value for key, value in payload.items() if key != '_attachment_draft_ids'})
            result = self._services.task_creation().create_task(
                principal, **command.model_dump(exclude={'title'}), title=command.title,
                idempotency_key=str(action.id), causation_key=str(action.id),
                source_action_item_id=action.id,
                source_decision_item_id=source_decision_item_id,
                source_submission_id=source_submission_id,
                source_review_decision_id=source_review_decision_id,
            )
            return self._claim_action_materials(principal, action, payload, result)
        if action.action_type == "task.update":
            command = TaskUpdateCommand.model_validate(payload)
            return self._services.tasks().update(
                command.task_id,
                principal,
                command.expected_version,
                command.changes(),
            )
        if action.action_type == "task.progress.batch":
            return self._run_task_progress_batch(principal, payload)
        if action.action_type.startswith("task.checklist."):
            return self._run_checklist_command(principal, action.action_type, payload)
        if action.action_type == "task.assign":
            command = TaskAssignmentInput.model_validate({key: value for key, value in payload.items() if key != '_attachment_draft_ids'})
            result = self._services.task_creation().assign_task(
                principal, **command.model_dump(exclude={'title', 'assignee_id'}),
                title=command.title, assignee_id=command.assignee_id,
                idempotency_key=str(action.id), causation_key=str(action.id),
                source_action_item_id=action.id,
                source_decision_item_id=source_decision_item_id,
                source_submission_id=source_submission_id,
                source_review_decision_id=source_review_decision_id,
            )
            return self._claim_action_materials(principal, action, payload, result)
        if action.action_type == "task.assignment.accept":
            # Old accept envelopes carried a reason that the operation ignored.
            command = AssignmentAcceptCommand.model_validate({key: value for key, value in payload.items() if key != 'reason'})
            return self._assignments().accept(principal, **command.model_dump())
        if action.action_type == "task.assignment.decline":
            command = AssignmentDeclineCommand.model_validate(payload)
            return self._assignments().decline(principal, **command.model_dump())
        if action.action_type == "task.transition":
            command = TaskTransitionCommand.model_validate(payload)
            return self._services.tasks().transition(
                command.task_id,
                principal,
                TaskState(command.target),
                command.reason,
                command.expected_version,
            )
        if action.action_type == "work_request.accept":
            command = WorkRequestAcceptCommand.model_validate(payload)
            return self._work_requests.accept(principal, **command.model_dump())
        if action.action_type == "work_request.reject":
            command = WorkRequestRejectCommand.model_validate(payload)
            return self._work_requests.reject(principal, **command.model_dump())
        if action.action_type == ACTION_ITEM_COMMAND:
            return self._run_action_item_command(principal, payload)
        if action.action_type == "work_request.amend":
            command = WorkRequestAmendCommand.model_validate(payload)
            return self._work_requests.amend(principal, **command.model_dump())
        if action.action_type == "work_request.negotiate":
            command = WorkRequestNegotiationCommand.model_validate(payload)
            return self._work_requests.negotiate(principal, **command.model_dump())
        raise ValueError("unsupported action type")

    def _tasks(self) -> TaskApplication:
        """The same dependency-complete Task application used by direct creation and authorization."""
        return self._services.tasks()

    def _run_checklist_command(self, principal: Principal, action_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        """A checklist change a delegated turn prepared, applied once by the person who approved it."""
        tasks = self._services.tasks()
        if action_type == "task.checklist.add":
            command = ChecklistAddCommand.model_validate(payload)
            return tasks.add_checklist_item(
                principal,
                command.task_id,
                command.text,
                expected_task_version=command.expected_task_version,
            )
        if action_type == "task.checklist.reorder":
            command = ChecklistOrderCommand.model_validate(payload)
            return tasks.reorder_checklist(
                principal,
                command.task_id,
                command.item_ids,
                expected_task_version=command.expected_task_version,
            )
        if action_type == "task.checklist.archive":
            command = ChecklistArchiveCommand.model_validate(payload)
            return tasks.archive_checklist_item(
                principal,
                command.task_id,
                command.item_id,
                expected_version=command.expected_version,
                expected_task_version=command.expected_task_version,
            )
        command = ChecklistUpdateCommand.model_validate(payload)
        return tasks.update_checklist_item(
            principal,
            command.task_id,
            command.item_id,
            **command.model_dump(exclude={"task_id", "item_id"}, exclude_none=True),
        )

    def _run_task_progress_batch(self, principal: Principal, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply each distinct Task effect behind one approval, preserving an outcome for every item."""
        tasks = self._tasks()
        results: list[dict[str, Any]] = []
        for operation in payload.get("operations") or []:
            try:
                with self._session.begin_nested():
                    if operation["kind"] == "progress.note":
                        applied = tasks.add_progress_note(
                            principal,
                            UUID(str(operation["task_id"])),
                            str(operation["summary"]),
                            expected_task_version=int(operation["expected_version"]),
                        )
                    else:
                        applied = tasks.update_checklist_item(
                            principal,
                            UUID(str(operation["task_id"])),
                            UUID(str(operation["item_id"])),
                            expected_version=int(operation["expected_version"]),
                            text=operation.get("text"),
                            done=operation.get("done"),
                        )
                    self._session.flush()
                results.append({
                    "effect_id": operation["effect_id"],
                    "kind": operation["kind"],
                    "task_id": operation["task_id"],
                    "status": "applied",
                    "task_version": applied["task_version"],
                })
            except TaskError as error:
                message = str(error)
                status = (
                    "stale"
                    if "stale" in message
                    else "denied"
                    if isinstance(error, (TaskAccessDenied, TaskNotFound))
                    else "failed"
                )
                results.append({
                    "effect_id": operation["effect_id"],
                    "kind": operation["kind"],
                    "task_id": operation["task_id"],
                    "status": status,
                    "message": {
                        "stale": "대상이 변경되었습니다",
                        "denied": "이 업무를 변경할 수 없습니다",
                        "failed": "현재 상태에서는 이 변경을 반영할 수 없습니다",
                    }[status],
                })
        applied_count = sum(item["status"] == "applied" for item in results)
        return {
            "batch_state": "completed" if applied_count == len(results) else "partial" if applied_count else "failed",
            "applied_count": applied_count,
            "total_count": len(results),
            "items": results,
        }

    def _run_action_item_command(self, principal: Principal, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply the judgement a delegated turn prepared, through the one canonical command path.

        The approving person's own authority is what runs it: the ActionCenter re-checks that the command is still
        offered to them on that item and that the version they are answering is still the current one.
        """
        center = self._services.action_center()
        target = str(payload["action_item_id"])
        envelope = center.detail(principal, target)
        if envelope.get("expected_version") != payload.get("expected_version"):
            raise ActionError("대상이 바뀌어 이 확인은 더 이상 쓸 수 없습니다. 판단을 다시 준비하세요")
        if str(envelope.get("kind", "")).startswith("ax."):
            # The gate exists to put a person between AX and the effect; approving one gate must not open another.
            raise ActionError("an AX proposal cannot be decided by another proposal")
        return center.execute(
            principal,
            target,
            str(payload["command"]),
            {key: payload[key] for key in ("expected_version", "reason", "changes") if key in payload},
        )

    def _assignments(self) -> TaskAssignmentApplication:
        return self._services.assignments()

    def _claim_action_materials(
        self, principal: Principal, action: ActionItemRecord, payload: dict[str, Any], result: dict[str, Any]
    ) -> dict[str, Any]:
        ids = [UUID(str(item)) for item in payload.get("_attachment_draft_ids") or []]
        if not ids:
            return result
        if self._action_materials is None:
            raise ActionError("action material staging is not available")
        owner_type = "meeting" if action.action_type == "meeting.reservation.create" else "task"
        nested = result.get(owner_type)
        owner_id = result.get(f"{owner_type}_id") or (
            nested.get(f"{owner_type}_id") if isinstance(nested, dict) else None
        )
        if not owner_id:
            raise ActionError(f"created {owner_type.title()} receipt did not include its identity")
        materials = self._action_materials.claim(
            principal,
            action.id,
            ids,
            owner_type,
            UUID(str(owner_id)),
        )
        if owner_type == "meeting":
            meeting = self._session.get(MeetingRecord, UUID(str(owner_id)))
            if meeting is not None:
                SqlAlchemyMeetingRepository(self._session).append_audit(
                    meeting,
                    str(principal.id),
                    "meeting.materials_attached",
                    f"회의 첨부 {len(materials)}건 추가",
                )
            return {**result, "material_results": materials}
        task_id = owner_id
        task = self._session.get(TaskRecord, UUID(str(task_id)))
        creation = self._session.scalar(
            select(TaskVersionRecord).where(TaskVersionRecord.task_id == UUID(str(task_id)), TaskVersionRecord.version == 1)
        )
        if task is not None and creation is not None:
            # Task v1 is frozen at the transaction boundary, after every create-time relation has been written.
            creation.snapshot = SqlAlchemyTaskRepository(self._session).task_snapshot(task)
        return {**result, "material_results": materials}

    def _attach_meeting_references(
        self,
        principal: Principal,
        payload: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        reference_ids = [UUID(str(item)) for item in payload.get("reference_task_ids") or []]
        if not reference_ids:
            return result
        nested = result.get("meeting")
        meeting_id = result.get("meeting_id") or (
            nested.get("meeting_id") if isinstance(nested, dict) else None
        )
        if not meeting_id:
            raise ActionError("created Meeting receipt did not include its identity")
        attachments = SqlAlchemyAttachmentRepository(self._session)
        materials = list(result.get("material_results") or [])
        for task_id in reference_ids:
            task = self._tasks().get(principal, task_id)
            attachment = attachments.add_reference(
                resource_type="task",
                resource_id=str(task_id),
                name=str(task["title"]),
                provenance=f"selected by {principal.id} on meeting {meeting_id}",
                uploaded_by=str(principal.id),
            )
            binding = attachments.bind(
                attachment_id=attachment.id,
                context_type="meeting",
                context_id=str(meeting_id),
                role="input",
                bound_by=str(principal.id),
            )
            materials.append({
                "material_id": str(binding.id),
                "attachment_id": str(attachment.id),
                "meeting_id": str(meeting_id),
                "kind": "input",
                "name": attachment.name,
                "content_type": attachment.content_type,
                "size_bytes": int(attachment.size_bytes),
                "source_kind": attachment.source_kind,
                "url": None,
                "integrity_ref": attachment.integrity_ref,
            })
        meeting = self._session.get(MeetingRecord, UUID(str(meeting_id)))
        if meeting is not None:
            SqlAlchemyMeetingRepository(self._session).append_audit(
                meeting,
                str(principal.id),
                "meeting.materials_attached",
                f"회의 관련 업무 {len(reference_ids)}건 추가",
            )
        return {**result, "material_results": materials}


def _parse_date(value: Any):
    if value in (None, ""):
        return None
    from datetime import date

    return date.fromisoformat(str(value))


def _parse_datetime(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("meeting times must include a timezone")
    return parsed


def _uuid_or_none(value: Any) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


# ---- structured, permission-safe Action presentation -------------------------------------------------------------

_OPERATION_LABELS: dict[str, str] = {
    "action.material.link.stage": "승인 항목의 링크 자료 준비",
    "action.material.draft.discard": "승인 항목의 자료 초안 버리기",
    "conversation.create": "새 대화 생성",
    "conversation.message.send": "대화 메시지 접수",
    "conversation.turn.cancel": "대화 응답 취소",
    "conversation.turn.retry": "대화 응답 다시 시도",
    "notification.mark_read": "알림 읽음 처리",
    "assistant.character.set": "AX 캐릭터 변경",
    "work_request.comment.add": "업무 요청 댓글 등록",
    "meeting.material.attach_link": "회의 자료 링크 연결",
    "meeting.material.detach": "회의 자료 분리",
    "meeting.material.remove": "회의 자료 분리",
    "meeting.summary.adopt": "회의 요약 채택",
    "meeting.speaker.assign": "회의 화자 확인",
    "meeting.followup.task": "회의 후속 내 업무 생성",
    "meeting.followup.request": "회의 후속 업무 요청",
    "task.material.attach_link": "업무 자료 링크 연결",
    "task.material.attach_reference": "업무 회의 자료 연결",
    "task.material.detach": "업무 자료 분리",
    "task.reassign": "업무 담당자 변경",
    "task.completion.submit": "업무 완료 보고",
    "task.reference.add": "참고 업무 연결",
    "task.reference.release": "참고 업무 해제",
    "material_folder.create": "자료함 생성",
    "material_folder.archive": "자료함 보관",
    "material_folder.detach": "자료함에서 자료 분리",
    "project.create": "프로젝트 생성",
    "project.assign_member": "프로젝트 참여 배정",
    "project.release_member": "프로젝트 참여 해제",
    "project.plan_work": "프로젝트 업무 계획",
    "meeting.info.update": "회의 수정",
    "meeting.share.revoke": "회의 공유 해제",
    "meeting.note.create": "회의록 작성",
    "meeting.note.save": "회의록 수정",
    "meeting.note.finalize": "회의록 확정",
    # `RETIRED_ACTION_TYPES` 의 옛 계약. 실행 경로는 없고, 남은 행의 `operation_label` 만 한국어로 선다.
    "meeting.create": "회의 생성",
    "meeting.reservation.create": "회의 생성",
    "meeting.share": "회의 공유",
    "task.create_self": "업무 생성",
    "work_request.create": "업무 요청",
    "task.assign": "업무 배정",
    "task.update": "업무 수정",
    "task.transition": "업무 상태 변경",
    "task.assignment.accept": "배정 수락",
    "task.assignment.decline": "배정 거절",
    "work_request.accept": "요청 수락",
    "work_request.reject": "요청 거절",
    "work_request.negotiate": "요청 조건 협의",
    "work_request.amend": "요청 수정",
    "daily_report.edit": "일일보고 수정",
    "daily_report.submit": "일일보고 제출",
    "task.checklist.add": "체크리스트 단계 추가",
    "task.checklist.update": "체크리스트 단계 수정",
    "task.checklist.archive": "체크리스트 단계 정리",
    "task.checklist.reorder": "체크리스트 순서 변경",
    "task.progress.batch": "업무 진행 일괄 반영",
}
_TASK_STATE_LABELS: dict[str, str] = {"open": "대기", "in_progress": "진행 중", "blocked": "막힘", "done": "완료", "cancelled": "취소"}
_UNKNOWN_MEMBER = "확인할 수 없는 구성원"
_CREATION_KINDS = {
    "task.create_self",
    "work_request.create",
    "task.assign",
    "meeting.reservation.create",
}
_CURRENT_MEETING_COMMAND_KINDS = CURRENT_MEETING_ACTION_TYPES


def action_subject_label(action: ActionItemRecord, payload: dict[str, Any] | None = None) -> str:
    """What the proposal is about, in the words of the work itself rather than the confirmation wording."""
    if action.action_type in _CREATION_KINDS:
        title = (payload if payload is not None else action.payload or {}).get("title")
        if title:
            return str(title)
    return str(action.title)


class ActionPresenter:
    """Server-side preview of what approving an Action will actually do.

    The client renders `subject`, `operation_label`, and `preview` label/value rows verbatim and never derives fields or
    controls from `action_type` or the raw payload. Only fields present in the actual command are emitted; member names
    are resolved through the principal's organization visibility and referenced Tasks only when the principal can read
    them, so the preview never widens what the approver may see.
    """

    def __init__(
        self,
        session: Session,
        *,
        services: ActionServices,
        evidence_reader: ActionEvidenceReader | None = None,
    ) -> None:
        self._session = session
        self._services = services
        self._name_cache: dict[str, dict[str, str]] = {}
        self._evidence_reader = evidence_reader

    def present(
        self,
        action: ActionItemRecord,
        principal: Principal | None,
        *,
        payload_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(action.payload or {}) if payload_override is None else dict(payload_override)
        kind = action.action_type
        fields: list[dict[str, str]] = []
        subject = action.title
        # Only a confirmation of another judgement can go out of date; every other proposal carries its own effect.
        obsolete = False

        if kind == "material_folder.create":
            subject = str(payload["title"])
            self._text(fields, "title", "자료함 명", subject)
            self._text(fields, "kind", "자료함 종류", "개인" if payload["kind"] == "personal" else "팀")
            if payload.get("organization_id"):
                self._text(fields, "organization", "소유 조직", payload["organization_id"])
        elif kind in {"material_folder.archive", "material_folder.detach"}:
            folders = self._services.material_folders()
            folder = next((row for row in folders.list_for(principal) if row["folder_id"] == payload["folder_id"]), None)
            subject = folder["title"] if folder is not None else "볼 수 없는 자료함"
            self._text(fields, "folder", "자료함", subject)
            obsolete = folder is None and action.state == "pending"
            if kind == "material_folder.detach" and folder is not None:
                material = next((row for row in folders.materials(principal, UUID(folder["folder_id"])) if row["material_id"] == payload["material_id"]), None)
                self._text(fields, "material", "분리할 자료", material["name"] if material else "볼 수 없는 자료")
                obsolete = material is None and action.state == "pending"
        elif kind in {"project.create", "project.assign_member", "project.release_member", "project.plan_work"}:
            if kind == "project.create":
                subject = str(payload["name"])
                self._text(fields, "name", "프로젝트 명", payload["name"])
                self._text(fields, "description", "설명", payload.get("description"))
                self._date(fields, "starts_on", "시작일", payload.get("starts_on"))
                self._date(fields, "ends_on", "종료일", payload.get("ends_on"))
                self._text(fields, "external_key", "외부 식별자", payload.get("external_key"))
            else:
                try:
                    project = self._services.projects().get(principal, UUID(str(payload["project_id"])))
                except ResourceNotFound:
                    project = None
                subject = project["name"] if project is not None else "볼 수 없는 프로젝트"
                obsolete = project is None and action.state == "pending"
                self._text(fields, "project", "프로젝트", subject)
                if kind == "project.plan_work":
                    self._text(fields, "title", "업무 명", payload.get("title"))
                    self._text(fields, "description", "설명", payload.get("description"))
                    self._date(fields, "start_date", "시작일", payload.get("start_date"))
                    self._date(fields, "due_date", "기한", payload.get("due_date"))
                else:
                    self._person(fields, "member", "구성원", payload.get("member_id"), principal)
                    if kind == "project.release_member":
                        self._text(fields, "reason", "종료 사유", payload.get("reason"))
                    if kind == "project.assign_member":
                        self._text(fields, "kind", "참여 종류", "담당" if payload.get("kind") == "lead" else "참여")
                        self._date_time(fields, "valid_from", "참여 시작", payload.get("valid_from"))
                        self._date_time(fields, "valid_until", "참여 종료", payload.get("valid_until"))
        elif kind == "meeting.reservation.create":
            subject = action_subject_label(action, payload)
            self._text(fields, "purpose", "목적", payload.get("purpose"))
            self._date_time(fields, "starts_at", "시작", payload.get("starts_at"))
            self._date_time(fields, "ends_at", "종료", payload.get("ends_at"))
            self._text(fields, "location", "장소", payload.get("location"))
            self._person(fields, "host", "주최자", action.owner_id, principal)
            attendee_ids = [str(item) for item in payload.get("attendee_ids") or []]
            if attendee_ids:
                names = self._names(principal)
                fields.append({
                    "id": "attendees",
                    "label": "참석자",
                    "value": ", ".join(names.get(member, _UNKNOWN_MEMBER) for member in attendee_ids),
                    "kind": "people",
                })
            external = [str(item) for item in payload.get("external_attendees") or []]
            if external:
                self._text(fields, "external_attendees", "외부 참석자", ", ".join(external))
        elif kind in _CURRENT_MEETING_COMMAND_KINDS:
            if kind == "meeting.quick_start":
                subject = "바로 시작할 회의"
                self._text(fields, "operation", "변경", "제목 없는 회의를 만들고 바로 시작")
            else:
                try:
                    detail = self._services.meetings().get(principal, UUID(str(payload["meeting_id"])))
                except (MeetingError, ValueError):
                    detail = None
                subject = (
                    str(detail["meeting"].get("title") or detail["meeting"].get("title_candidate") or "제목 없는 회의")
                    if detail is not None
                    else "볼 수 없는 회의"
                )
                obsolete = detail is None and action.state == "pending"
                self._text(fields, "meeting", "대상 회의", subject)
                changes = payload.get("changes") if isinstance(payload.get("changes"), dict) else {}
                for key, label in (
                    ("title", "회의 명"),
                    ("purpose", "목적"),
                    ("starts_at", "시작"),
                    ("ends_at", "종료"),
                    ("location", "장소"),
                ):
                    if key in changes:
                        if key in {"starts_at", "ends_at"} and changes[key]:
                            self._date_time(fields, key, label, changes[key])
                        else:
                            self._text(fields, key, label, changes[key] or "삭제")
                if payload.get("title"):
                    self._text(fields, "title", "안건", payload.get("title"))
                if payload.get("text"):
                    self._text(fields, "text", "메모", payload.get("text"))
                if payload.get("assignee_id"):
                    self._person(fields, "assignee", "요청 대상", payload.get("assignee_id"), principal)
                member_ids = [str(item) for item in payload.get("member_ids") or []]
                if member_ids:
                    names = self._names(principal)
                    self._text(
                        fields,
                        "members",
                        "공유 대상",
                        ", ".join(names.get(member_id, _UNKNOWN_MEMBER) for member_id in member_ids),
                    )
                if payload.get("member_id"):
                    self._person(fields, "member", "공유 해제 대상", payload.get("member_id"), principal)
        elif kind == "meeting.share":
            meeting = self._services.meetings().get(principal, UUID(str(payload["meeting_id"])))["meeting"]
            subject = str(meeting.get("title") or meeting.get("title_candidate") or "")
            fields.append({"id": "meeting", "label": "회의", "value": subject, "kind": "text"})
            target_name = self._names(principal).get(str(payload.get("member_id")), _UNKNOWN_MEMBER)
            fields.append({
                "id": "member",
                "label": "공유 대상",
                "value": target_name.split(" (")[0].strip() or target_name,
                "kind": "person",
            })
            fields.append({"id": "scope", "label": "공유 범위", "value": "회의 열람", "kind": "state"})
        elif kind in _CREATION_KINDS:
            subject = action_subject_label(action, payload)
            self._text(fields, "description", "설명", payload.get("description"))
            if kind == "work_request.create":
                self._person(fields, "requester", "요청자", action.owner_id, principal)
                self._person(fields, "assignee", "요청 대상", payload.get("assignee_id"), principal)
            elif kind == "task.assign":
                self._person(fields, "assignee", "담당자", payload.get("assignee_id"), principal)
                self._person(fields, "requester", "요청자", action.owner_id, principal)
            else:
                self._person(fields, "assignee", "담당", action.owner_id, principal)
            self._date(fields, "start_date", "시작일", payload.get("start_date"))
            self._date(fields, "due_date", "기한", payload.get("due_date"))
            pointers = [str(item) for item in payload.get("reference_task_ids") or []]
            if pointers:
                titles = [self._readable_task_title(item, principal) for item in pointers]
                named = [title for title in titles if title]
                hidden = len(titles) - len(named)
                value = " · ".join(named) if named else ""
                if hidden:
                    value = f"{value} · 볼 수 없는 업무 {hidden}건".strip(" ·")
                fields.append({"id": "references", "label": "참고 업무", "value": value, "kind": "text"})
            if payload.get("parent_task_id"):
                parent_title = self._readable_task_title(payload.get("parent_task_id"), principal)
                fields.append({
                    "id": "parent", "label": "상위 업무", "value": parent_title or "볼 수 없는 업무", "kind": "text",
                })
            steps = [str(step) for step in payload.get("checklist") or []]
            if steps:
                fields.append({
                    "id": "checklist", "label": "체크리스트",
                    "value": f"{len(steps)}단계 · " + " → ".join(steps), "kind": "text",
                })
            cc = [str(member) for member in payload.get("cc_member_ids") or []]
            if cc:
                names = self._names(principal)
                fields.append({"id": "cc", "label": "참조자", "value": ", ".join(names.get(member, _UNKNOWN_MEMBER) for member in cc), "kind": "people"})
        elif kind in {"task.completion.submit", "task.reference.add", "task.reference.release", "task.reassign", "task.material.attach_link", "task.material.attach_reference", "task.material.detach"}:
            try:
                task = self._services.tasks().get(principal, UUID(str(payload["task_id"])))
            except (TaskNotFound, TaskAccessDenied):
                task = None
            subject = task["title"] if task is not None else "볼 수 없는 업무"
            obsolete = task is None and action.state == "pending"
            self._text(fields, "task", "대상 업무", subject)
            if kind == "task.material.attach_link":
                self._text(fields, "material", "자료 이름", payload.get("label"))
                self._text(fields, "url", "링크 주소", payload.get("url"))
                self._text(fields, "role", "자료 역할", "산출물" if payload.get("kind") == "output" else "참고 자료")
            elif kind == "task.material.attach_reference":
                try:
                    target = self._services.meetings().get(principal, UUID(str(payload["resource_id"]))) if payload.get("resource_type") == "meeting" else None
                except (MeetingError, ResourceNotFound):
                    target = None
                self._text(
                    fields,
                    "material",
                    "연결할 회의",
                    target["meeting"]["title"] if target else "볼 수 없는 회의",
                )
                obsolete = obsolete or (target is None and action.state == "pending")
            elif kind == "task.material.detach":
                materials = self._services.materials().list(principal, UUID(str(payload["task_id"]))) if task else []
                material = next((row for row in materials if row["binding_id"] == str(payload["binding_id"])), None)
                self._text(fields, "material", "분리할 자료", material["name"] if material else "분리되었거나 볼 수 없는 자료")
                self._text(fields, "original", "원본", "원본 파일은 보존됩니다")
            elif kind == "task.reassign":
                self._person(fields, "assignee", "새 담당자", payload.get("assignee_id"), principal)
                self._text(fields, "reason", "변경 사유", payload.get("reason"))
                self._text(fields, "acceptance", "다음 판단", "새 담당자가 수락해야 내 업무에 들어갑니다")
            elif kind == "task.completion.submit":
                self._text(fields, "summary", "결과 요약", payload.get("summary"))
                if task is not None:
                    wanted = {str(identifier) for identifier in payload.get("output_material_ids") or []}
                    materials = self._services.materials().list(principal, UUID(str(payload["task_id"])))
                    selected_names = [row["name"] for row in materials if row["material_id"] in wanted]
                    self._text(fields, "output_materials", "결과 자료", " · ".join(selected_names) or "선택한 결과 자료 없음")
                self._text(fields, "review", "다음 판단", "요청자가 결과를 확인한 뒤 완료로 인정합니다")
            elif kind == "task.reference.add":
                referenced = self._readable_task_title(payload.get("referenced_task_id"), principal)
                self._text(fields, "reference", "참고할 업무", referenced or "볼 수 없는 업무")
            else:
                reference = next((row for row in (task or {}).get("references", []) if row["reference_id"] == payload["reference_id"]), None)
                self._text(fields, "reference", "해제할 참고 업무", (reference.get("task") or {}).get("title") if reference else "해제된 참고 연결")
        elif kind in {"task.update", "task.transition"}:
            task_title = self._readable_task_title(payload.get("task_id"), principal)
            if task_title is not None:
                subject = task_title
                fields.append({"id": "task", "label": "대상 업무", "value": task_title, "kind": "text"})
            if kind == "task.update":
                changes = TaskUpdateCommand.model_validate(payload).model_dump(mode='json', exclude_unset=True, exclude={'task_id', 'expected_version'})
                obsolete = task_title is None and action.state == 'pending'
                for key, label in [('title', '제목'), ('description', '설명'), ('start_date', '시작일'), ('due_date', '기한')]:
                    if key in changes:
                        if key in {'start_date', 'due_date'} and changes[key]:
                            self._date(fields, key, label, changes[key])
                        else:
                            self._text(fields, key, label, changes[key] or '삭제')
                if 'project_id' in changes:
                    name = '연결 해제'
                    if changes['project_id']:
                        try:
                            name = self._services.projects().get(principal, UUID(changes['project_id']))['name']
                        except ResourceNotFound:
                            name = '볼 수 없는 프로젝트'
                            obsolete = action.state == 'pending'
                    self._text(fields, 'project', '프로젝트', name)
            else:
                target = str(payload.get("target") or "")
                if target:
                    fields.append({"id": "target", "label": "변경 상태", "value": _TASK_STATE_LABELS.get(target, target), "kind": "state"})
                self._text(fields, "reason", "사유", payload.get("reason"))
        elif kind == "task.progress.batch":
            operations = list(payload.get("operations") or [])
            subject = f"업무 진행 {len(operations)}건"
            outcomes = {
                str(item.get("effect_id")): str(item.get("status"))
                for item in (action.result or {}).get("items", [])
                if isinstance(item, dict)
            }
            for index, operation in enumerate(operations, start=1):
                task_title = self._readable_task_title(operation.get("task_id"), principal)
                if task_title is None:
                    task_title = "볼 수 없는 업무"
                if operation.get("kind") == "progress.note":
                    change = f"진행 메모 · {operation['summary']}"
                else:
                    step = self._readable_checklist_item_text(
                        operation.get("task_id"), operation.get("item_id"), principal
                    )
                    state = "완료" if operation.get("done") is True else "완료 해제"
                    change = f"{step} · {state}" if step else f"체크리스트 {state}"
                    if operation.get("text"):
                        change = f"{change} · {operation['text']}" if operation.get("done") is not None else str(operation["text"])
                outcome = outcomes.get(str(operation.get("effect_id")))
                if outcome:
                    change = f"{change} · " + {
                        "applied": "반영됨",
                        "stale": "대상 변경",
                        "denied": "권한 없음",
                    }.get(outcome, "반영 실패")
                fields.append({
                    "id": f"operation_{index}",
                    "label": task_title,
                    "value": change,
                    "kind": "state",
                })
        elif kind.startswith("task.checklist."):
            task_title = self._readable_task_title(payload.get("task_id"), principal)
            if task_title is not None:
                subject = task_title
                fields.append({"id": "task", "label": "대상 업무", "value": task_title, "kind": "text"})
            if kind == "task.checklist.add":
                self._text(fields, "text", "추가할 단계", payload.get("text"))
            elif kind == "task.checklist.update":
                self._text(fields, "text", "고칠 내용", payload.get("text"))
                if payload.get("done") is not None:
                    fields.append({
                        "id": "done", "label": "완료 여부",
                        "value": "완료로 표시" if payload.get("done") else "완료 해제", "kind": "state",
                    })
            elif kind == "task.checklist.reorder":
                fields.append({
                    "id": "order", "label": "새 순서",
                    "value": f"{len(payload.get('item_ids') or [])}단계를 다시 정렬", "kind": "text",
                })
            self._checklist_step(fields, payload, principal)
        elif kind == 'daily_report.submit':
            self._text(fields, 'reason', '제출 사유', payload.get('reason'))
            if action.state == 'pending':
                obsolete = principal is None or DAILY_REPORT_SUBMIT not in principal.capabilities
                if not obsolete:
                    try:
                        report = self._services.reports().submission_preview(principal, str(payload['report_id']), str(payload['draft_id']))
                    except ResourceNotFound:
                        obsolete = True
                    else:
                        subject = f"{report['report_date']} 일일보고"
                        self._text(fields, 'body', '제출할 보고 본문', report['body'])
                        self._text(fields, 'sources', '보고 근거', f"{len(report['source_refs'])}건")
                        obsolete = report['draft_version'] != payload['expected_version']
        elif kind in {"task.assignment.decline", "work_request.reject"}:
            self._text(fields, "reason", "사유", payload.get("reason"))
        elif kind in {"action.material.link.stage", "action.material.draft.discard"}:
            try:
                subject = self._services.action_materials().target_title(principal, UUID(str(payload['action_item_id'])))
            except ResourceNotFound:
                subject = '닫혔거나 볼 수 없는 승인 항목'
                obsolete = action.state == 'pending'
            self._text(fields, 'target', '대상 승인 항목', subject)
            if kind == "action.material.link.stage":
                self._text(fields, 'name', '자료 이름', payload.get('label'))
                self._text(fields, 'url', '링크 주소', payload.get('url'))
                self._text(fields, 'claim', '최종 연결', '생성 승인에서 자료를 선택해야 업무·회의에 연결됩니다')
            else:
                drafts = self._services.action_materials().list(principal, UUID(str(payload['action_item_id']))) if not obsolete else []
                draft = next((row for row in drafts if row['material_draft_id'] == str(payload['material_draft_id'])), None)
                self._text(fields, 'material', '버릴 자료 초안', draft['name'] if draft else '버렸거나 볼 수 없는 자료 초안')
        elif kind == "conversation.create":
            subject = str(payload.get('title') or '새 대화')
            self._text(fields, 'title', '대화 제목', subject)
        elif kind in {"conversation.message.send", "conversation.turn.cancel", "conversation.turn.retry"}:
            try:
                subject = self._services.conversations().target_title(principal, UUID(str(payload['conversation_id'])))
            except ResourceNotFound:
                subject = '볼 수 없는 대화'
                obsolete = action.state == 'pending'
            self._text(fields, 'conversation', '대상 대화', subject)
            if kind == "conversation.message.send":
                self._text(fields, 'body', '메시지 본문', payload.get('body'))
                self._text(fields, 'accepted', '접수 이후', '대화 작업에 접수되며 응답이 생성되는 동안 상태를 확인할 수 있습니다')
                names = []
                for reference in payload.get('context') or []:
                    if reference['resource_type'] == 'task':
                        name = self._readable_task_title(reference['resource_id'], principal)
                    else:
                        try:
                            name = self._services.work_requests().get(principal, UUID(reference['resource_id']))['title']
                        except (ResourceNotFound, WorkRequestAccessDenied):
                            name = None
                    names.append(f"{name or '볼 수 없는 맥락'} ({'내용 포함' if reference['included'] else '내용 제외'})")
                self._text(fields, 'context', '선택한 맥락', ' · '.join(names) or '선택한 맥락 없음')
            elif kind == "conversation.turn.retry":
                self._text(fields, 'turn', '재시도 대상', payload.get('turn_id'))
            else:
                self._text(fields, 'cancel', '취소 대상', '현재 처리 중인 응답을 취소합니다')
        elif kind == "notification.mark_read":
            notification = next((row for row in self._services.notifications().list(principal) if row['notification_id'] == payload['notification_id']), None)
            subject = notification['resource']['title'] if notification else '볼 수 없는 알림'
            self._text(fields, 'notification', '대상 알림', subject)
            obsolete = notification is None and action.state == 'pending'
        elif kind == "assistant.character.set":
            self._text(fields, 'character', '새 캐릭터', ASSISTANT_CHARACTER_LABELS.get(payload.get('character_key'), '지원하지 않는 캐릭터'))
        elif kind == "work_request.comment.add":
            try:
                request = self._services.work_requests().get(principal, UUID(str(payload['request_id'])))
            except ResourceNotFound:
                request = None
            subject = request['title'] if request else '볼 수 없는 업무 요청'
            self._text(fields, 'request', '대상 요청', subject)
            obsolete = request is None and action.state == 'pending'
            self._text(fields, 'comment', '댓글 본문', payload.get('body'))
            self._text(fields, 'decision', '요청 판단', '댓글은 수락·거절 판단을 바꾸지 않습니다')
        elif kind == "work_request.amend":
            self._text(fields, "title", "제목", payload.get("title"))
            self._text(fields, "description", "설명", payload.get("description"))
            if payload.get("clear_due_date"):
                fields.append({"id": "due_date", "label": "기한", "value": "없앰", "kind": "state"})
            else:
                self._date(fields, "due_date", "기한", payload.get("due_date"))
        elif kind == "work_request.negotiate":
            conditions = dict(payload.get("conditions") or {})
            self._date(fields, "due_date", "제안 기한", conditions.get("due_date"))
            self._text(fields, "note", "메모", conditions.get("note") or conditions.get("reason"))
        elif kind == ACTION_ITEM_COMMAND:
            return self._action_item_command(action, payload, principal, fields)

        if kind in RETIRED_ACTION_TYPES:
            # No branch above claims a retired kind, so this sets `obsolete` rather than overriding one:
            # a withdrawn contract stays readable, but nothing offers to run it again.
            obsolete = action.state == "pending"
        self._evidence(fields, action, principal)
        result: dict[str, Any] = {
            "subject": subject,
            "operation_label": _OPERATION_LABELS.get(kind, kind),
            "preview": fields,
            "obsolete": obsolete,
        }
        if kind == "task.progress.batch" and isinstance(action.result, dict):
            applied = int(action.result.get("applied_count") or 0)
            total = int(action.result.get("total_count") or 0)
            result["result_summary"] = (
                f"{total}건 반영됨"
                if action.result.get("batch_state") == "completed"
                else f"{applied}/{total}건 반영됨 · 나머지 항목 확인 필요"
                if applied
                else "반영된 항목 없음 · 항목 확인 필요"
            )
        # 대상이 사라진 제안은 거절만 남는다. 편집기를 함께 내려보내면 제출할 수 없는 화면이 선다.
        edit_contract = None if obsolete else self._edit_contract(action, principal, payload)
        if edit_contract is not None:
            result["edit_contract"] = edit_contract
        return result

    def _edit_contract(
        self,
        action: ActionItemRecord,
        principal: Principal | None,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """The closed, permission-safe editor contract for a canonical AX creation proposal."""
        # The same set `decide_ax_confirmation` accepts: an editor is only offered where a confirm can land.
        if principal is None or action.action_type not in SUPPORTED_ACTION_TYPES:
            return None
        decision = self._session.get(DecisionItemRecord, action.id)
        if decision is None:
            return None
        submission = self._session.scalar(
            select(SubmissionRecord)
            .where(SubmissionRecord.decision_item_id == decision.id)
            .order_by(SubmissionRecord.submission_version.desc())
        )
        if submission is None:
            return None
        if action.action_type in COMMAND_CONTRACTS:
            return self._command_edit_contract(action, principal, payload, submission)
        if action.action_type == "task.progress.batch":
            try:
                values = _normalize_task_progress_batch(payload)
            except (TaskError, TypeError, ValueError) as error:
                raise ActionError(str(error)) from error
            return {
                "editor": "task_progress_batch",
                "base_submission_version": int(submission.submission_version),
                "values": values,
                "fields": [],
            }
        if action.action_type == "meeting.reservation.create":
            return self._meeting_edit_contract(principal, payload, submission)
        if action.action_type == "work_request.create":
            return self._work_request_edit_contract(principal, payload, submission)
        try:
            task_values = {key: value for key, value in payload.items() if key != "attachment_draft_ids"}
            values = normalize_assigned_task_draft(task_values) if action.action_type == "task.assign" else normalize_task_draft(task_values)
        except (TaskError, TypeError, ValueError) as error:
            raise ActionError(str(error)) from error
        organization = SqlAlchemyOrganizationRepository(self._session)
        projects = self._services.projects()
        tasks = self._services.tasks()
        reference_options = []
        project_options = []
        if action.action_type in {"task.create_self", "task.assign"}:
            reference_options = [
                {"value": row["task_id"], "label": row["title"]}
                for row in tasks.readable_tasks(principal, include_closed=True)
            ]
        cc_options: list[dict[str, str]] = []
        if action.action_type == "task.create_self":
            project_options = [
                {"value": row["project_id"], "label": row["name"]}
                for row in projects.list(principal)
            ]
            # 참조자 후보는 **요청 초안이 쓰는 것과 같은 명부**다. 나 자신은 담당 자리에 이미 서 있다.
            cc_options = [
                {"value": row["id"], "label": row["display_name"]}
                for row in self._services.organization().member_candidates(principal)
                if str(row["id"]) != str(principal.id)
            ]
        assignee = organization.principal_for(str(principal.id))
        assignee_label = assignee.display_name if assignee is not None else str(principal.id)
        assignment_options: list[dict[str, str]] = []
        if action.action_type == "task.assign":
            assignment_options = [
                {"value": row["id"], "label": row["display_name"]}
                for row in self._services.assignments().candidates(principal)
            ]
        fields: list[dict[str, Any]] = [
            {"id": "title", "label": "업무 명", "type": "text", "required": True, "editable": True},
            {"id": "description", "label": "내용", "type": "textarea", "required": False, "editable": True},
            (
                {
                    "id": "assignee_id",
                    "label": "담당자",
                    "type": "select",
                    "required": True,
                    "editable": True,
                    "options": assignment_options,
                }
                if action.action_type == "task.assign"
                else {
                    "id": "assignee_id",
                    "label": "담당자",
                    "type": "person",
                    "required": True,
                    "editable": False,
                    "value": str(principal.id),
                    "label_value": assignee_label,
                }
            ),
            {"id": "start_date", "label": "시작일", "type": "date", "required": False, "editable": True},
            {
                "id": "due_date",
                "label": "기한",
                "type": "date",
                "required": action.action_type == "task.create_self",
                "editable": True,
            },
        ]
        if action.action_type == "task.create_self":
            fields.extend([
                {
                    "id": "project_id",
                    "label": "프로젝트",
                    "type": "select",
                    "required": False,
                    "editable": True,
                    "options": project_options,
                },
                {
                    # 참조자는 **읽기와 논의만** 연다 — 담당을 옮기지 않으므로 확인 화면에서 고칠 수 있다.
                    "id": "cc_member_ids",
                    "label": "참조자",
                    "type": "multi_select",
                    "required": False,
                    "editable": True,
                    "options": cc_options,
                },
                {
                    # **선행 배열은 생성 계약의 일부라 모든 생성 표면에 함께 선다** (SPEC-001 §5 표면 일치).
                    # 후보는 「읽을 수 있는 업무」이고, 같은 프로젝트인지는 서버가 실행 때 다시 가른다 —
                    # 확인 화면이 프로젝트를 함께 고치는 자리라 여기서 미리 좁히면 고른 프로젝트와 어긋난다.
                    "id": "preceding_task_ids",
                    "label": "선행업무",
                    "type": "multi_select",
                    "required": False,
                    "editable": True,
                    "options": reference_options,
                },
                {
                    # 결재자 — **`업무` 갈래만이다.** 요청 초안에는 이 칸이 없다 (SPEC-001 §7 OQ-M).
                    "id": "approver_id",
                    "label": "결재자",
                    "type": "select",
                    "required": False,
                    "editable": True,
                    "options": cc_options,
                },
                {
                    "id": "checklist",
                    "label": "체크리스트",
                    "type": "string_list",
                    "required": False,
                    "editable": True,
                },
                {
                    "id": "reference_task_ids",
                    "label": "참고 업무",
                    "type": "multi_select",
                    "required": False,
                    "editable": True,
                    "options": reference_options,
                },
            ])
        else:
            fields.extend([
                {
                    "id": "checklist",
                    "label": "체크리스트",
                    "type": "string_list",
                    "required": False,
                    "editable": True,
                },
                {
                    "id": "reference_task_ids",
                    "label": "참고 업무",
                    "type": "multi_select",
                    "required": False,
                    "editable": True,
                    "options": reference_options,
                },
            ])
        return {
            "editor": "task",
            "base_submission_version": int(submission.submission_version),
            "values": values,
            "fields": fields,
        }

    def _command_edit_contract(self, action: ActionItemRecord, principal: Principal, payload: dict[str, Any], submission: SubmissionRecord) -> dict[str, Any] | None:
        if action.state != 'pending':
            return None
        contract = COMMAND_CONTRACTS[action.action_type]
        values = contract.normalize(payload)
        schema = contract.model.model_json_schema()
        fields = []
        labels = {'public': '공개', 'private': '비공개', 'input': '참고 자료', 'output': '산출물', 'lead': '담당', 'member': '참여', **ASSISTANT_CHARACTER_LABELS}
        for key, definition in schema['properties'].items():
            # 상태 변경의 사유는 **차단과 취소**에서만 사람이 고쳐 쓴다 — 시작·완료는 사유를 묻지 않으므로
            # 그 칸을 확인 화면에 띄우면 채울 수 없는 자리가 하나 생긴다 (SPEC-003 §4 Validation).
            if key in contract.fixed_fields or (
                action.action_type == 'task.transition'
                and key == 'reason'
                and values['target'] not in {'blocked', 'cancelled'}
            ):
                continue
            shape = definition
            if 'anyOf' in shape:
                shape = next((item for item in shape['anyOf'] if item.get('type') != 'null'), {})
            if '$ref' in shape:
                shape = schema['$defs'][shape['$ref'].split('/')[-1]]
            field = {'id': key, 'label': definition.get('title', key), 'required': key in schema.get('required', []), 'editable': True, 'empty_policy': contract.empty_policy(key, definition)}
            if key in {'include_source_refs', 'exclude_source_refs'} and action.action_type == 'daily_report.edit':
                field['type'] = 'source_select'
                field['options'] = self._report_source_options(principal, values)
            elif key == 'item_ids' and action.action_type == 'task.checklist.reorder':
                field['type'] = 'ordered_select'
                field['options'] = self._command_options(action.action_type, key, principal, values)
            elif key in {'assignee_id', 'member_id', 'referenced_task_id', 'resource_id', 'output_material_ids', 'project_id'}:
                field['type'] = 'multi_select' if shape.get('type') == 'array' else 'select'
                field['options'] = self._command_options(action.action_type, key, principal, values)
            elif 'enum' in shape:
                field['type'] = 'select'
                field['options'] = [{'value': str(value), 'label': labels.get(value, str(value))} for value in shape['enum']]
            elif shape.get('type') == 'boolean':
                field['type'] = 'boolean'
            elif shape.get('format') in {'date', 'date-time'}:
                field['type'] = 'date' if shape['format'] == 'date' else 'datetime'
            elif shape.get('type') == 'array':
                field['type'] = 'string_list'
            else:
                field['type'] = 'textarea' if key in {'body', 'description', 'reason', 'summary'} else 'text'
            fields.append(field)
        return {'editor': 'command', 'base_submission_version': int(submission.submission_version), 'values': values, 'fields': fields}

    def _report_source_options(self, principal: Principal, values: dict[str, Any]) -> list[dict[str, str]]:
        sources = [*values['include_source_refs'], *values['exclude_source_refs']]
        if DAILY_REPORT_READ in principal.capabilities:
            history = self._services.reports().history(principal, str(values['report_id']))
            for draft in history['drafts']:
                sources.extend(draft['source_refs'])
        options = {}
        for source in sources:
            key = (source['task_id'], source['task_version'], source['occurred_at'])
            title = self._readable_task_title(source['task_id'], principal) or '기존 보고 근거'
            options[key] = {'value': json.dumps(source, ensure_ascii=False, sort_keys=True), 'label': f"{title} · v{source['task_version']} · {source['occurred_at']}"}
        return list(options.values())

    def _command_options(self, action_type: str, key: str, principal: Principal, values: dict[str, Any]) -> list[dict[str, str]]:
        try:
            if key == 'item_ids':
                return [{'value': row['item_id'], 'label': row['text']} for row in self._services.tasks().get(principal, UUID(values['task_id'])).get('checklist', [])]
            if key == 'project_id':
                if 'project.read' not in principal.capabilities:
                    return []
                return [{'value': row['project_id'], 'label': row['name']} for row in self._services.projects().list(principal)]
            if key == 'assignee_id':
                if action_type == 'task.reassign':
                    rows = self._services.assignments().candidates(principal)
                    rows = [*rows, {'id': str(principal.id), 'display_name': principal.display_name}]
                else:
                    rows = self._services.organization().work_request_assignee_candidates(principal)
                return [{'value': str(row['id']), 'label': row['display_name']} for row in rows]
            if key == 'member_id':
                return [{'value': str(row['id']), 'label': row['display_name']} for row in self._services.organization().member_candidates(principal)]
            if key == 'referenced_task_id':
                return [{'value': row['task_id'], 'label': row['title']} for row in self._services.tasks().readable_tasks(principal, include_closed=True) if row['task_id'] != values.get('task_id')]
            if key == 'resource_id':
                return [
                    {'value': row['meeting_id'], 'label': row['title']}
                    for row in self._services.meetings().list(principal)
                    if row.get('kind') == 'meeting'
                ]
            if key == 'output_material_ids':
                return [{'value': row['material_id'], 'label': row['name']} for row in self._services.materials().list(principal, UUID(values['task_id'])) if row['kind'] == 'output']
        except (ResourceNotFound, TaskAccessDenied):
            return []
        return []

    def _work_request_edit_contract(
        self,
        principal: Principal,
        payload: dict[str, Any],
        submission: SubmissionRecord,
    ) -> dict[str, Any]:
        try:
            values = normalize_work_request_draft(payload, requester_id=str(principal.id))
        except (WorkRequestError, TypeError, ValueError) as error:
            raise ActionError(str(error)) from error
        organization = self._services.organization()
        reference_options = [
            {"value": row["task_id"], "label": row["title"]}
            for row in self._tasks_for_principal(principal).readable_tasks(principal, include_closed=True)
        ]
        assignee_options = [
            {"value": row["id"], "label": row["display_name"]}
            for row in organization.work_request_assignee_candidates(principal)
        ]
        cc_options = [
            {"value": row["id"], "label": row["display_name"]}
            for row in organization.member_candidates(principal)
            if str(row["id"]) != str(principal.id)
        ]
        # 프로젝트는 **읽을 수 있는 사람에게만** 고르게 한다 — 없으면 빈 목록이고 칸은 남는다.
        request_project_options = (
            [
                {"value": row["project_id"], "label": row["name"]}
                for row in self._services.projects().list(principal)
            ]
            if PROJECT_READ in principal.capabilities
            else []
        )
        return {
            # Work requests intentionally reuse the Task card shell; the server field list keeps the operation distinct.
            "editor": "task",
            "base_submission_version": int(submission.submission_version),
            "values": values,
            "fields": [
                {"id": "title", "label": "업무 명", "type": "text", "required": True, "editable": True},
                {"id": "description", "label": "내용", "type": "textarea", "required": False, "editable": True},
                {
                    "id": "assignee_id",
                    "label": "요청 대상",
                    "type": "select",
                    "required": True,
                    "editable": True,
                    "options": assignee_options,
                },
                {"id": "start_date", "label": "시작일", "type": "date", "required": False, "editable": True},
                {"id": "due_date", "label": "기한", "type": "date", "required": False, "editable": True},
                {
                    "id": "project_id",
                    "label": "프로젝트",
                    "type": "select",
                    "required": False,
                    "editable": True,
                    "options": request_project_options,
                },
                {
                    "id": "cc_member_ids",
                    "label": "참조자",
                    "type": "multi_select",
                    "required": False,
                    "editable": True,
                    "options": cc_options,
                },
                {
                    # 선행도 결재자도 **두 갈래가 같은 칸**을 받는다 — 확인 화면이 표면마다 다른
                    # 필드를 내면 사람이 고른 값이 어느 길에서만 저장된다.
                    "id": "preceding_task_ids",
                    "label": "선행업무",
                    "type": "multi_select",
                    "required": False,
                    "editable": True,
                    "options": reference_options,
                },
                {
                    # 결재자 — 요청 갈래도 이제 값을 받아 저장하고, 그 값이 이 요청이 세우는 업무로 간다.
                    "id": "approver_id",
                    "label": "결재자",
                    "type": "select",
                    "required": False,
                    "editable": True,
                    "options": cc_options,
                },
                {
                    "id": "checklist",
                    "label": "체크리스트",
                    "type": "string_list",
                    "required": False,
                    "editable": True,
                },
                {
                    "id": "reference_task_ids",
                    "label": "참고 업무",
                    "type": "multi_select",
                    "required": False,
                    "editable": True,
                    "options": reference_options,
                },
            ],
        }

    def _meeting_edit_contract(
        self,
        principal: Principal,
        payload: dict[str, Any],
        submission: SubmissionRecord,
    ) -> dict[str, Any]:
        try:
            values = MeetingReservationInput.model_validate(
                {key: value for key, value in payload.items() if key != "attachment_draft_ids"}
            ).model_dump(mode="json")
        except (TypeError, ValueError) as error:
            raise ActionError(str(error)) from error
        organizations = SqlAlchemyOrganizationRepository(self._session)
        attendee_options = [
            {"value": str(row["id"]), "label": str(row["display_name"])}
            for row in organizations.member_directory()
            if organizations.principal_for(str(row["id"])) is not None
        ]
        fields: list[dict[str, Any]] = [
            {"id": "title", "label": "회의 명", "type": "text", "required": False, "editable": True},
            {"id": "purpose", "label": "목적", "type": "textarea", "required": False, "editable": True},
            {"id": "starts_at", "label": "시작", "type": "datetime", "required": True, "editable": True},
            {"id": "ends_at", "label": "종료", "type": "datetime", "required": True, "editable": True},
            {"id": "location", "label": "장소", "type": "text", "required": False, "editable": True},
            {"id": "attendee_ids", "label": "참석자", "type": "multi_select", "required": False,
             "editable": True, "options": attendee_options},
            {"id": "external_attendees", "label": "외부 참석자", "type": "string_list",
             "required": False, "editable": True},
            {"id": "agendas", "label": "안건", "type": "object_list", "required": False, "editable": True},
            {"id": "carried_from_meeting_id", "label": "이어온 회의", "type": "text",
             "required": False, "editable": True},
            {"id": "room_id", "label": "회의실 번호", "type": "number", "required": False, "editable": True},
        ]
        return {
            "editor": "meeting",
            "base_submission_version": int(submission.submission_version),
            "values": values,
            "fields": fields,
            "warnings": [],
        }

    def _tasks_for_principal(self, principal: Principal) -> TaskApplication:
        return self._services.tasks()

    def _checklist_step(self, fields: list[dict[str, str]], payload: dict[str, Any], principal: Principal | None) -> None:
        """Which step is being changed, named by its own words rather than by an id nobody can read."""
        text = self._readable_checklist_item_text(payload.get("task_id"), payload.get("item_id"), principal)
        if text:
            fields.append({"id": "step", "label": "대상 단계", "value": text, "kind": "text"})

    def _readable_checklist_item_text(
        self, task_id: Any, item_id: Any, principal: Principal | None
    ) -> str | None:
        """Resolve a frozen checklist identity without widening the current reader's Task access."""
        if not item_id or not task_id or principal is None:
            return None
        from ax_workspace.platform.work_tasks import SqlAlchemyTaskRepository

        repository = SqlAlchemyTaskRepository(self._session)
        try:
            identifier = UUID(str(task_id))
        except (TypeError, ValueError):
            return None
        try:
            task = self._services.tasks().get(principal, identifier)
        except (ResourceNotFound, TaskAccessDenied):
            return None
        if task.get("access") != "owner":
            return None
        for item in repository.checklist_for(identifier, include_archived=True):
            if str(item.id) == str(item_id):
                return str(item.text)
        return None

    def _action_item_command(
        self,
        action: ActionItemRecord,
        payload: dict[str, Any],
        principal: Principal | None,
        fields: list[dict[str, str]],
    ) -> dict[str, Any]:
        """A judgement a delegated turn prepared, shown as the target's own permission-safe card.

        The approver sees the work being judged and the answer that would be given — never the raw wire payload, and
        never more of the target than that person may already read.
        """
        target = self._target_envelope(payload.get("action_item_id"), principal)
        if target is None:
            # Not readable now, whatever was readable when the turn proposed it. Nobody can confirm a judgement they
            # cannot see, so the card offers only the way out rather than an approval that would fail on use.
            return {"subject": ACTION_ITEM_COMMAND_TITLE, "operation_label": "판단 확인", "preview": [], "obsolete": True}
        # A confirmation answers one moment. If the target has moved since, approving it would apply an answer to
        # something else, so the card says so rather than waiting to fail.
        obsolete = target.get("expected_version") != payload.get("expected_version")
        if obsolete:
            fields.append({"id": "obsolete", "label": "상태", "value": "대상이 바뀌어 이 확인은 더 이상 쓸 수 없습니다", "kind": "state"})
        command = str(payload.get("command") or "")
        label = next((entry["label"] for entry in target.get("allowed_commands", []) if entry["id"] == command), command)
        fields.append({"id": "command", "label": "판단", "value": label, "kind": "state"})
        self._text(fields, "question", "질문", target.get("current_question"))
        self._text(fields, "reason", "사유", payload.get("reason"))
        changes = dict(payload.get("changes") or {})
        self._text(fields, "changes_title", "제안 제목", changes.get("title"))
        self._text(fields, "changes_description", "제안 설명", changes.get("description"))
        self._date(fields, "changes_due_date", "제안 기한", changes.get("due_date"))
        # The target's own preview rows are already permission-safe for this principal.
        fields.extend(target.get("preview", []))
        self._evidence(fields, action, principal)
        return {
            "subject": str(target.get("subject") or action.title),
            "operation_label": f"{target.get('operation_label', '판단')} 판단",
            "preview": fields,
            "obsolete": obsolete,
        }

    def _target_envelope(self, action_item_id: Any, principal: Principal | None) -> dict[str, Any] | None:
        if principal is None or not action_item_id:
            return None
        try:
            return self._services.action_center().detail(principal, str(action_item_id))
        except Exception:
            # The approver cannot read the target: say nothing about it rather than widen what they may see.
            return None

    def _evidence(self, fields: list[dict[str, str]], action: ActionItemRecord, principal: Principal | None) -> None:
        """Name actual turn observations after current owner, binding and hash checks."""
        if principal is None or self._evidence_reader is None:
            return
        seen: set[str] = set()
        names: list[str] = []
        for item in self._evidence_reader(principal, action.turn_id):
            identifier = str(item["attachment_id"])
            if identifier not in seen:
                seen.add(identifier)
                names.append(item["name"])
        if names:
            fields.append({"id": "evidence", "label": "근거 자료", "value": ", ".join(names), "kind": "evidence"})

    @staticmethod
    def _text(fields: list[dict[str, str]], field_id: str, label: str, value: Any) -> None:
        if value in (None, ""):
            return
        fields.append({"id": field_id, "label": label, "value": str(value), "kind": "text"})

    @staticmethod
    def _date(fields: list[dict[str, str]], field_id: str, label: str, value: Any) -> None:
        if value in (None, ""):
            return
        fields.append({"id": field_id, "label": label, "value": str(value), "kind": "date"})

    @staticmethod
    def _date_time(fields: list[dict[str, str]], field_id: str, label: str, value: Any) -> None:
        if value in (None, ""):
            return
        fields.append({"id": field_id, "label": label, "value": str(value), "kind": "datetime"})

    def _person(self, fields: list[dict[str, str]], field_id: str, label: str, member_id: Any, principal: Principal | None) -> None:
        if member_id in (None, ""):
            return
        fields.append({"id": field_id, "label": label, "value": self._names(principal).get(str(member_id), _UNKNOWN_MEMBER), "kind": "person"})

    def _names(self, principal: Principal | None) -> dict[str, str]:
        """Names visible to THIS principal.

        The cache is keyed by principal id: one presenter instance is reused for every Action in a view, and two
        principals may ask the same instance (a worker loop, a shared session). An unkeyed cache would hand the first
        principal's candidate list to the second and widen what they can see.
        """
        if principal is None:
            return {}
        key = str(principal.id)
        cached = self._name_cache.get(key)
        if cached is not None:
            return cached
        organization = self._services.organization()
        names = {str(member["id"]): str(member["display_name"]) for member in organization.member_candidates(principal)}
        names[key] = principal.display_name
        self._name_cache[key] = names
        return names

    def _readable_task_title(self, task_id: Any, principal: Principal | None) -> str | None:
        if principal is None or task_id in (None, ""):
            return None
        try:
            return str(self._services.tasks().get(principal, UUID(str(task_id)))["title"])
        except (TaskError, ValueError):
            return None
