"""PostgreSQL adapter for AX confirmation Actions and their canonical effects."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ax_workspace.modules.organization_access.application import OrganizationApplication
from ax_workspace.modules.organization_access.domain import ACTION_DECIDE, TASK_ASSIGN, Principal
from ax_workspace.modules.meetings.application import MeetingApplication
from ax_workspace.modules.meetings.domain import MeetingError
from ax_workspace.modules.meetings.drafts import normalize_meeting_draft
from ax_workspace.modules.reports.application import DailyReportApplication
from ax_workspace.modules.ax_execution.actions import (
    ACTION_ITEM_COMMAND,
    ACTION_ITEM_COMMAND_TITLE,
    ActionError,
    action_commands,
    action_payload_hash,
)
from ax_workspace.modules.work.requests import (
    DECISION_FACTS,
    EVIDENCE_HASH,
    EVIDENCE_MANIFEST,
    WorkRequestApplication,
    WorkRequestError,
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
from ax_workspace.platform.organization_access import SqlAlchemyOrganizationRepository
from ax_workspace.platform.meetings import SqlAlchemyMeetingRepository
from ax_workspace.platform.projects import SqlAlchemyProjectRepository
from ax_workspace.platform.persistence import (
    ActionItemAuditEventRecord,
    ActionItemRecord,
    ActionMaterialDraftRecord,
    ConversationAnswerResourceRecord,
    ConversationGraphReceiptRecord,
    ConversationMessageRecord,
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
from ax_workspace.platform.reports import SqlAlchemyDailyReportDraftWorkflow, SqlAlchemyDailyReportRepository
from ax_workspace.modules.work.assignments import TaskAssignmentApplication
from ax_workspace.platform.work_tasks import (
    caused_by,
    SqlAlchemyAttachmentRepository,
    SqlAlchemyTaskAssignmentRepository,
    SqlAlchemyTaskRepository,
    SqlAlchemyWorkRecordSource,
    SqlAlchemyWorkRequestRepository,
)


ActionEvidenceReader = Callable[[Principal, UUID], list[dict[str, Any]]]


class SqlAlchemyActionRepository:
    def __init__(
        self,
        session: Session,
        *,
        evidence_reader: ActionEvidenceReader | None = None,
        work_requests: WorkRequestApplication | None = None,
    ) -> None:
        self._session = session
        self._evidence_reader = evidence_reader
        self._presenter = ActionPresenter(
            session,
            evidence_reader=evidence_reader,
            work_requests=work_requests,
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

        A provider redelivery may produce a different payload for the same
        delegated turn. It must reuse the first proposal rather than create a
        second effect. Intentional repeated effects belong in a later turn.
        """
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
        if existing is not None:
            return existing
        if action_type == "meeting.create":
            payload = self._freeze_meeting_proposal(owner_id, turn, payload)
        elif action_type == "task.progress.batch":
            payload = _normalize_task_progress_batch(payload)
        payload_hash = action_payload_hash(payload)
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

    def _freeze_meeting_proposal(
        self,
        owner_id: str,
        turn: ConversationTurnRecord,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Adopt only source facts this delegated Turn actually carried before the human confirmation."""
        draft = dict(payload)
        prior_discussion_requested = bool(draft.pop("prior_discussion_requested", False))
        source_turn_values = draft.pop("source_turn_ids", []) or []
        if not isinstance(source_turn_values, list):
            raise MeetingError("과거 대화 source는 Turn id 목록이어야 합니다")
        include_note = bool(draft.get("include_initial_note") or draft.get("initial_note_body"))
        if not include_note:
            draft["include_initial_note"] = False
            draft["initial_note_body"] = None
            draft["initial_note_source_status"] = "not_requested"
            draft["initial_note_source_evidence"] = []
            return normalize_meeting_draft(draft)
        messages = list(
            self._session.scalars(
                select(ConversationMessageRecord)
                .where(ConversationMessageRecord.turn_id == turn.id, ConversationMessageRecord.role == "user")
                .order_by(ConversationMessageRecord.sequence)
            )
        )
        excerpt = "\n".join(message.body.strip() for message in messages if message.body.strip())[:400]
        evidence: list[dict[str, Any]] = [{
            "source_type": "conversation_turn",
            "source_id": str(turn.id),
            "label": "현재 대화",
            "excerpt": excerpt,
            "locator": {"conversation_id": str(turn.conversation_id), "turn_id": str(turn.id)},
        }]
        past_turn_ids: list[UUID] = []
        for value in source_turn_values:
            try:
                identifier = UUID(str(value))
            except ValueError:
                continue
            if identifier != turn.id and identifier not in past_turn_ids:
                past_turn_ids.append(identifier)
        if past_turn_ids:
            observed_cross_conversation_turns = {
                parsed
                for resource in self._session.scalars(
                    select(ConversationAnswerResourceRecord).where(
                        ConversationAnswerResourceRecord.turn_id == turn.id,
                        ConversationAnswerResourceRecord.resource_type == "conversation_turn",
                    )
                )
                if (parsed := _uuid_or_none(resource.resource_id)) is not None
            }
            past_turns = list(
                self._session.scalars(
                    select(ConversationTurnRecord)
                    .join(ConversationRecord, ConversationRecord.id == ConversationTurnRecord.conversation_id)
                    .where(
                        ConversationTurnRecord.id.in_(past_turn_ids),
                        ConversationRecord.owner_id == owner_id,
                    )
                )
            )
            by_id = {item.id: item for item in past_turns}
            for identifier in past_turn_ids:
                source_turn = by_id.get(identifier)
                if source_turn is None or (
                    source_turn.conversation_id != turn.conversation_id
                    and identifier not in observed_cross_conversation_turns
                ):
                    continue
                conversation = self._session.get(ConversationRecord, source_turn.conversation_id)
                source_messages = list(
                    self._session.scalars(
                        select(ConversationMessageRecord)
                        .where(ConversationMessageRecord.turn_id == source_turn.id)
                        .order_by(ConversationMessageRecord.sequence)
                    )
                )
                source_excerpt = "\n".join(
                    message.body.strip() for message in source_messages if message.body.strip()
                )[:400]
                evidence.append({
                    "source_type": "conversation_turn",
                    "source_id": str(source_turn.id),
                    "label": f"과거 대화 · {conversation.title if conversation else '대화'}",
                    "excerpt": source_excerpt,
                    "locator": {
                        "conversation_id": str(source_turn.conversation_id),
                        "turn_id": str(source_turn.id),
                    },
                })
        for material in self._readable_turn_materials(owner_id, turn.id):
            contexts = list(material.get("source_contexts") or [])
            primary = contexts[0] if contexts else {}
            evidence.append({
                "source_type": "material",
                "source_id": str(material["material_id"]),
                "label": str(material["name"]),
                "excerpt": str(material.get("excerpt") or "")[:400],
                "locator": {
                    "resource_type": primary.get("resource_type"),
                    "resource_id": primary.get("resource_id"),
                    "binding_id": primary.get("binding_id"),
                    "material_id": str(material["material_id"]),
                    "chunk_id": material.get("chunk_id"),
                    "page": material.get("page"),
                    "integrity_ref": material.get("integrity_ref"),
                },
            })
        for resource in self._session.scalars(
            select(ConversationAnswerResourceRecord)
            .where(ConversationAnswerResourceRecord.turn_id == turn.id)
            .order_by(ConversationAnswerResourceRecord.sequence)
        ):
            if resource.resource_type not in {"task", "meeting"}:
                continue
            label = self._meeting_source_label(owner_id, resource.resource_type, resource.resource_id)
            if label is None:
                continue
            evidence.append({
                "source_type": resource.resource_type,
                "source_id": resource.resource_id,
                "label": label,
                "excerpt": None,
                "locator": {
                    "resource_version": resource.resource_version,
                    "source_contexts": list(resource.source_contexts or []),
                    "source_locator": dict(resource.source_locator) if resource.source_locator else None,
                },
            })
        for step in self._session.scalars(
            select(ConversationGraphReceiptRecord)
            .where(ConversationGraphReceiptRecord.turn_id == turn.id)
            .order_by(ConversationGraphReceiptRecord.sequence)
        ):
            candidates = (
                [(step.node_ref, step.node_title)]
                if step.kind == "node"
                else [(step.from_ref, step.from_title), (step.to_ref, step.to_title)]
            )
            for node_ref, node_title in candidates:
                if not node_ref or ":" not in node_ref:
                    continue
                source_type, _, source_id = node_ref.partition(":")
                if source_type not in {"task", "meeting"}:
                    continue
                label = self._meeting_source_label(owner_id, source_type, source_id)
                if label is None:
                    continue
                evidence.append({
                    "source_type": source_type,
                    "source_id": source_id,
                    "label": node_title or label,
                    "excerpt": None,
                    "locator": {"graph_receipt_id": str(step.id)},
                })
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for source in evidence:
            key = (str(source["source_type"]), str(source["source_id"]))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(source)
        draft["include_initial_note"] = True
        draft["initial_note_source_status"] = (
            "resolved" if len(deduped) > 1
            else "not_found" if prior_discussion_requested
            else "current_turn"
        )
        draft["initial_note_source_evidence"] = deduped
        return normalize_meeting_draft(draft)

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
                meeting = MeetingApplication(SqlAlchemyMeetingRepository(self._session)).get(
                    principal, UUID(source_id)
                )["meeting"]
                return str(meeting.get("title") or meeting.get("title_candidate") or "")
        except (MeetingError, TaskError, ValueError):
            return None
        return None

    def _tasks_for_source(self) -> TaskApplication:
        return TaskApplication(
            SqlAlchemyTaskRepository(self._session),
            SqlAlchemyWorkRequestRepository(self._session),
            SqlAlchemyActionRepository(self._session),
            SqlAlchemyAttachmentRepository(self._session),
            SqlAlchemyOrganizationRepository(self._session),
            ProjectApplication(SqlAlchemyProjectRepository(self._session)),
        )

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

    def view(self, action: ActionItemRecord, principal: Principal | None = None) -> dict[str, Any]:
        """Canonical Action row plus its structured, permission-safe presentation for `principal`."""
        presented = self._presenter.present(action, principal, payload_override=self._canonical_payload(action))
        can_decide = principal is not None and ACTION_DECIDE in principal.capabilities
        commands = action_commands(action.state, can_decide, obsolete=bool(presented.get("obsolete")))
        if presented.get("edit_contract") is not None and action.state == "pending" and can_decide:
            commands = [
                {
                    "id": "confirm",
                    "label": (
                        "이 내용으로 업무 요청"
                        if action.action_type in {"task.assign", "work_request.create"}
                        else "이 내용으로 회의 생성"
                        if action.action_type == "meeting.create"
                        else "이 내용으로 반영"
                        if action.action_type == "task.progress.batch"
                        else "이 내용으로 업무 생성"
                    ),
                    "tone": "primary",
                },
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
        result = dict(action.result) if isinstance(action.result, dict) else action.result
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

    def _material_drafts(self, action: ActionItemRecord, principal: Principal | None) -> list[dict[str, Any]]:
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
        if action.action_type == "meeting.create":
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


def action_center_application(
    session: Session,
    executor: Any,
    *,
    work_requests: WorkRequestApplication,
    evidence_reader: ActionEvidenceReader | None = None,
) -> Any:
    """The one judgement application, built here so the wrapper runs exactly what HTTP and the UI run."""
    # Imported late: the ActionCenter presents AX proposals through this module.
    from ax_workspace.modules.ax_execution.actions import ActionApplication
    from ax_workspace.modules.actions.domain import ActionCenterApplication
    from ax_workspace.platform.action_center import action_handlers

    return ActionCenterApplication(
        action_handlers(
            session,
            evidence_reader=evidence_reader,
            work_requests=work_requests,
            actions=ActionApplication(
                SqlAlchemyActionRepository(
                    session,
                    evidence_reader=evidence_reader,
                    work_requests=work_requests,
                ),
                executor,
            ),
            assignments=TaskAssignmentApplication(
                SqlAlchemyTaskAssignmentRepository(session),
                OrganizationApplication(SqlAlchemyOrganizationRepository(session)),
                TaskApplication(
                    SqlAlchemyTaskRepository(session),
                    SqlAlchemyWorkRequestRepository(session),
                    SqlAlchemyActionRepository(session),
                    SqlAlchemyAttachmentRepository(session),
                ),
            ),
            tasks=TaskApplication(
                SqlAlchemyTaskRepository(session),
                SqlAlchemyWorkRequestRepository(session),
                SqlAlchemyActionRepository(session),
                SqlAlchemyAttachmentRepository(session),
            ),
            material_drafts=getattr(executor, "_action_materials", None),
        )
    )


class SqlAlchemyActionExecutor:
    """Invokes existing public application commands inside the Action transaction."""

    def __init__(
        self,
        session: Session,
        report_provider: Any,
        action_materials: Any = None,
        *,
        work_requests: WorkRequestApplication,
        evidence_reader: ActionEvidenceReader | None = None,
    ) -> None:
        self._session = session
        self._report_provider = report_provider
        self._action_materials = action_materials
        self._work_requests = work_requests
        self._evidence_reader = evidence_reader

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
        # Everything this approval causes says which confirmation carried it; the actor stays the approver.
        with caused_by(f"action_item:{action.id}"):
            return self._execute(
                principal,
                action,
                payload=payload,
                source_decision_item_id=source_decision_item_id,
                source_submission_id=source_submission_id,
                source_review_decision_id=source_review_decision_id,
            )

    def _execute(
        self,
        principal: Principal,
        action: ActionItemRecord,
        *,
        payload: dict[str, Any] | None = None,
        source_decision_item_id: UUID | None = None,
        source_submission_id: UUID | None = None,
        source_review_decision_id: UUID | None = None,
    ) -> dict[str, Any]:
        payload = dict(action.payload if payload is None else payload)
        if action.action_type == "work_request.create":
            return self._work_requests.create(
                principal,
                str(payload["title"]),
                str(payload["assignee_id"]),
                causation_key=str(action.id),
                description=payload.get("description"),
                due_date=_parse_date(payload.get("due_date")),
                cc_member_ids=list(payload.get("cc_member_ids") or []),
                checklist=list(payload.get("checklist") or []),
                reference_task_ids=[UUID(str(item)) for item in payload.get("reference_task_ids") or []],
            )
        if action.action_type == "daily_report.edit":
            return DailyReportApplication(
                SqlAlchemyDailyReportRepository(self._session),
                SqlAlchemyDailyReportDraftWorkflow(
                    self._session,
                    SqlAlchemyWorkRecordSource(self._session),
                    self._report_provider,
                ),
            ).edit(
                principal,
                str(action.payload["report_id"]),
                str(action.payload["draft_id"]),
                int(action.payload["expected_version"]),
                str(action.payload["body"]),
                list(action.payload.get("include_source_refs", [])),
                list(action.payload.get("exclude_source_refs", [])),
            )
        if action.action_type == "daily_report.submit":
            return DailyReportApplication(
                SqlAlchemyDailyReportRepository(self._session),
                SqlAlchemyDailyReportDraftWorkflow(self._session, SqlAlchemyWorkRecordSource(self._session), self._report_provider),
            ).submit(principal, str(action.payload["report_id"]), str(action.payload["draft_id"]), int(action.payload["expected_version"]), action.payload.get("reason"))
        if action.action_type == "task.create_self":
            result = self._tasks().create_self(
                principal,
                str(payload["title"]),
                causation_key=str(action.id),
                description=payload.get("description"),
                start_date=_parse_date(payload.get("start_date")),
                due_date=_parse_date(payload.get("due_date")),
                source_action_item_id=action.id,
                source_decision_item_id=source_decision_item_id,
                source_submission_id=source_submission_id,
                source_review_decision_id=source_review_decision_id,
                checklist=list(payload.get("checklist") or []),
                reference_task_ids=[UUID(str(item)) for item in payload.get("reference_task_ids") or []],
                parent_task_id=UUID(str(payload["parent_task_id"])) if payload.get("parent_task_id") else None,
                project_id=UUID(str(payload["project_id"])) if payload.get("project_id") else None,
            )
            return self._claim_action_materials(principal, action, payload, result)
        if action.action_type in {"meeting.create", "meeting.share"}:
            # main 의 채팅 확인 경로는 **옛 회의 모델**(description·visibility·판 있는 회의록·expected_version)
            # 위에 서 있었고, SCAX-SPEC-004 가 그 모델을 대체하면서 여기서 부르던 표면이 사라졌다.
            # 조용히 터지게 두지 않는다 — 사람이 [확인] 을 누르는 자리이므로 무엇이 안 되는지 말하고 멈춘다.
            # 새 모델 위에 이 두 확인을 다시 세우는 것은 별도 작업이다(회의 생성·공유는 지금 회의 화면에 있다).
            raise ActionError("이 확인은 아직 새 회의 모델로 옮겨지지 않았습니다 — 회의 화면에서 직접 해 주세요")

        if action.action_type == "task.update":
            changes = dict(action.payload.get("changes", {}))
            for field in ("start_date", "due_date"):
                if field in changes:
                    changes[field] = _parse_date(changes[field])
            return TaskApplication(SqlAlchemyTaskRepository(self._session), SqlAlchemyWorkRequestRepository(self._session), SqlAlchemyActionRepository(self._session)).update(
                UUID(str(action.payload["task_id"])), principal, int(action.payload["expected_version"]), changes
            )
        if action.action_type == "task.progress.batch":
            return self._run_task_progress_batch(principal, payload)
        if action.action_type.startswith("task.checklist."):
            return self._run_checklist_command(principal, action)
        if action.action_type == "task.assign":
            result = self._assignments().assign(
                principal, str(payload["title"]), str(payload["assignee_id"]),
                description=payload.get("description"),
                start_date=_parse_date(payload.get("start_date")),
                due_date=_parse_date(payload.get("due_date")),
                causation_key=str(action.id),
                checklist=list(payload.get("checklist") or []),
                reference_task_ids=[UUID(str(item)) for item in payload.get("reference_task_ids") or []],
                parent_task_id=UUID(str(payload["parent_task_id"])) if payload.get("parent_task_id") else None,
                source_action_item_id=action.id,
                source_decision_item_id=source_decision_item_id,
                source_submission_id=source_submission_id,
                source_review_decision_id=source_review_decision_id,
            )
            return self._claim_action_materials(principal, action, payload, result)
        if action.action_type == "task.assignment.accept":
            return self._assignments().accept(principal, UUID(str(action.payload["assignment_id"])))
        if action.action_type == "task.assignment.decline":
            return self._assignments().decline(principal, UUID(str(action.payload["assignment_id"])), str(action.payload.get("reason") or ""))
        if action.action_type == "task.transition":
            return TaskApplication(SqlAlchemyTaskRepository(self._session), SqlAlchemyWorkRequestRepository(self._session), SqlAlchemyActionRepository(self._session)).transition(
                UUID(str(action.payload["task_id"])), principal, TaskState(str(action.payload["target"])),
                action.payload.get("reason"), int(action.payload["expected_version"])
            )
        if action.action_type == "work_request.accept":
            return self._work_requests.accept(principal, UUID(str(action.payload["request_id"])), int(action.payload["expected_version"]))
        if action.action_type == "work_request.reject":
            return self._work_requests.reject(principal, UUID(str(action.payload["request_id"])), int(action.payload["expected_version"]), str(action.payload["reason"]))
        if action.action_type == ACTION_ITEM_COMMAND:
            return self._run_action_item_command(principal, action)
        if action.action_type == "work_request.amend":
            return self._work_requests.amend(
                principal,
                UUID(str(action.payload["request_id"])),
                int(action.payload["expected_version"]),
                title=action.payload.get("title"),
                description=action.payload.get("description"),
                due_date=_parse_date(action.payload.get("due_date")),
                clear_due_date=bool(action.payload.get("clear_due_date")),
            )
        if action.action_type == "work_request.negotiate":
            return self._work_requests.negotiate(principal, UUID(str(action.payload["request_id"])), int(action.payload["expected_version"]), dict(action.payload["conditions"]))
        raise ValueError("unsupported action type")

    def _tasks(self) -> TaskApplication:
        """The same dependency-complete Task application used by direct creation and authorization."""
        return TaskApplication(
            SqlAlchemyTaskRepository(self._session),
            SqlAlchemyWorkRequestRepository(self._session),
            SqlAlchemyActionRepository(self._session),
            SqlAlchemyAttachmentRepository(self._session),
            SqlAlchemyOrganizationRepository(self._session),
            ProjectApplication(SqlAlchemyProjectRepository(self._session)),
        )

    def _run_checklist_command(self, principal: Principal, action: ActionItemRecord) -> dict[str, Any]:
        """A checklist change a delegated turn prepared, applied once by the person who approved it."""
        tasks = TaskApplication(
            SqlAlchemyTaskRepository(self._session),
            SqlAlchemyWorkRequestRepository(self._session),
            SqlAlchemyActionRepository(self._session),
        )
        payload = dict(action.payload)
        task_id = UUID(str(payload["task_id"]))
        if action.action_type == "task.checklist.add":
            return tasks.add_checklist_item(principal, task_id, str(payload["text"]))
        if action.action_type == "task.checklist.reorder":
            return tasks.reorder_checklist(principal, task_id, [UUID(str(item)) for item in payload["item_ids"]])
        item_id = UUID(str(payload["item_id"]))
        expected_version = int(payload["expected_version"])
        if action.action_type == "task.checklist.archive":
            return tasks.archive_checklist_item(principal, task_id, item_id, expected_version=expected_version)
        changes: dict[str, Any] = {"expected_version": expected_version}
        if payload.get("text") is not None:
            changes["text"] = str(payload["text"])
        if payload.get("done") is not None:
            changes["done"] = bool(payload["done"])
        return tasks.update_checklist_item(principal, task_id, item_id, **changes)

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

    def _run_action_item_command(self, principal: Principal, action: ActionItemRecord) -> dict[str, Any]:
        """Apply the judgement a delegated turn prepared, through the one canonical command path.

        The approving person's own authority is what runs it: the ActionCenter re-checks that the command is still
        offered to them on that item and that the version they are answering is still the current one.
        """
        payload = action.payload or {}
        center = action_center_application(
            self._session,
            self,
            work_requests=self._work_requests,
            evidence_reader=self._evidence_reader,
        )
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
        return TaskAssignmentApplication(
            SqlAlchemyTaskAssignmentRepository(self._session),
            OrganizationApplication(SqlAlchemyOrganizationRepository(self._session)),
            TaskApplication(
                SqlAlchemyTaskRepository(self._session),
                SqlAlchemyWorkRequestRepository(self._session),
                SqlAlchemyActionRepository(self._session),
                SqlAlchemyAttachmentRepository(self._session),
            ),
        )

    def _claim_action_materials(
        self, principal: Principal, action: ActionItemRecord, payload: dict[str, Any], result: dict[str, Any]
    ) -> dict[str, Any]:
        ids = [UUID(str(item)) for item in payload.get("_attachment_draft_ids") or []]
        if not ids:
            return result
        if self._action_materials is None:
            raise ActionError("action material staging is not available")
        owner_type = "meeting" if action.action_type == "meeting.create" else "task"
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
        meeting_id = result.get("meeting_id")
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


def _normalize_task_progress_batch(payload: dict[str, Any]) -> dict[str, Any]:
    operations = payload.get("operations")
    if not isinstance(operations, list) or not 1 <= len(operations) <= 20:
        raise TaskError("a task progress batch must contain between 1 and 20 operations")
    normalized: list[dict[str, Any]] = []
    seen_tasks: set[str] = set()
    for raw in operations:
        if not isinstance(raw, dict) or raw.get("kind") not in {"checklist.update", "progress.note"}:
            raise TaskError("unsupported task progress operation")
        task_id = str(UUID(str(raw.get("task_id"))))
        if task_id in seen_tasks:
            raise TaskError("a task progress batch may change each task only once")
        seen_tasks.add(task_id)
        expected_version = int(raw.get("expected_version"))
        if expected_version < 1:
            raise TaskError("task progress expected_version must be positive")
        if raw["kind"] == "progress.note":
            summary = " ".join(str(raw.get("summary") or "").split())
            if not summary:
                raise TaskError("a progress note must say what changed")
            operation = {
                "kind": "progress.note",
                "task_id": task_id,
                "expected_version": expected_version,
                "summary": summary[:300],
            }
        else:
            item_id = str(UUID(str(raw.get("item_id"))))
            text = " ".join(str(raw["text"]).split()) if raw.get("text") is not None else None
            done = raw.get("done") if isinstance(raw.get("done"), bool) else None
            if text is None and done is None:
                raise TaskError("a checklist update must change text or done")
            operation = {
                "kind": "checklist.update",
                "task_id": task_id,
                "item_id": item_id,
                "expected_version": expected_version,
                "text": text,
                "done": done,
            }
        operation["effect_id"] = action_payload_hash(operation)
        normalized.append(operation)
    return {"operations": normalized}



# ---- structured, permission-safe Action presentation -------------------------------------------------------------

_OPERATION_LABELS: dict[str, str] = {
    "meeting.create": "회의 생성",
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
_CREATION_KINDS = {"task.create_self", "work_request.create", "task.assign", "meeting.create"}


def action_subject_label(action: ActionItemRecord, payload: dict[str, Any] | None = None) -> str:
    """What the proposal is about, in the words of the work itself rather than the confirmation wording."""
    if action.action_type in _CREATION_KINDS:
        title = (payload if payload is not None else action.payload or {}).get("title")
        if title:
            return str(title)
    return str(action.title)


class _NoEffectExecutor:
    """Reading a judgement never runs one; the presenter builds the ledger with an executor that refuses to act."""

    def execute(self, principal: Principal, action: ActionItemRecord, **_: Any) -> dict[str, Any]:
        raise ValueError("the presenter never executes an action")


_NO_EFFECT_EXECUTOR = _NoEffectExecutor()


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
        evidence_reader: ActionEvidenceReader | None = None,
        work_requests: WorkRequestApplication | None = None,
    ) -> None:
        self._session = session
        self._name_cache: dict[str, dict[str, str]] = {}
        self._evidence_reader = evidence_reader
        self._work_requests = work_requests

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

        if kind == "meeting.create":
            subject = action_subject_label(action, payload)
            self._text(fields, "description", "내용", payload.get("description"))
            self._date_time(fields, "starts_at", "시작", payload.get("starts_at"))
            self._date_time(fields, "ends_at", "종료", payload.get("ends_at"))
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
            fields.append({
                "id": "visibility",
                "label": "공개 범위",
                "value": "공개" if payload.get("visibility") == "public" else "비공개",
                "kind": "state",
            })
            if payload.get("include_initial_note"):
                self._text(fields, "initial_note_body", "회의록 초안", payload.get("initial_note_body"))
        elif kind == "meeting.share":
            meeting = MeetingApplication(SqlAlchemyMeetingRepository(self._session)).get(
                principal, UUID(str(payload["meeting_id"]))
            )["meeting"]
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
        elif kind in {"task.update", "task.transition"}:
            task_title = self._readable_task_title(payload.get("task_id"), principal)
            if task_title is not None:
                subject = task_title
                fields.append({"id": "task", "label": "대상 업무", "value": task_title, "kind": "text"})
            if kind == "task.update":
                changes = dict(payload.get("changes") or {})
                self._text(fields, "title", "제목", changes.get("title"))
                self._text(fields, "description", "설명", changes.get("description"))
                self._date(fields, "start_date", "시작일", changes.get("start_date"))
                self._date(fields, "due_date", "기한", changes.get("due_date"))
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
        elif kind in {"task.assignment.decline", "work_request.reject", "daily_report.submit"}:
            self._text(fields, "reason", "사유", payload.get("reason"))
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
        edit_contract = self._edit_contract(action, principal, payload)
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
        if principal is None or action.action_type not in {
            "task.create_self",
            "task.assign",
            "work_request.create",
            "meeting.create",
            "task.progress.batch",
        }:
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
        if action.action_type == "meeting.create":
            return self._meeting_edit_contract(action, principal, payload, submission)
        if action.action_type == "work_request.create":
            return self._work_request_edit_contract(principal, payload, submission)
        try:
            task_values = {key: value for key, value in payload.items() if key != "attachment_draft_ids"}
            values = normalize_assigned_task_draft(task_values) if action.action_type == "task.assign" else normalize_task_draft(task_values)
        except (TaskError, TypeError, ValueError) as error:
            raise ActionError(str(error)) from error
        organization = SqlAlchemyOrganizationRepository(self._session)
        projects = ProjectApplication(SqlAlchemyProjectRepository(self._session))
        tasks = TaskApplication(
            SqlAlchemyTaskRepository(self._session),
            SqlAlchemyWorkRequestRepository(self._session),
            SqlAlchemyActionRepository(self._session),
            SqlAlchemyAttachmentRepository(self._session),
            organization,
            projects,
        )
        reference_options = []
        project_options = []
        if action.action_type in {"task.create_self", "task.assign"}:
            reference_options = [
                {"value": row["task_id"], "label": row["title"]}
                for row in tasks.list_for(principal, include_closed=True, include_organization=True)
            ]
        if action.action_type == "task.create_self":
            project_options = [
                {"value": row["project_id"], "label": row["name"]}
                for row in projects.list(principal)
            ]
        assignee = organization.principal_for(str(principal.id))
        assignee_label = assignee.display_name if assignee is not None else str(principal.id)
        assignment_options: list[dict[str, str]] = []
        if action.action_type == "task.assign":
            assignment_options = [
                {"value": row["id"], "label": row["display_name"]}
                for row in TaskAssignmentApplication(
                    SqlAlchemyTaskAssignmentRepository(self._session),
                    OrganizationApplication(organization),
                    tasks,
                    projects,
                ).candidates(principal)
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
        organization = OrganizationApplication(SqlAlchemyOrganizationRepository(self._session))
        reference_options = [
            {"value": row["task_id"], "label": row["title"]}
            for row in self._tasks_for_principal(principal).list_for(
                principal,
                include_closed=True,
                include_organization=True,
            )
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
                {"id": "due_date", "label": "기한", "type": "date", "required": False, "editable": True},
                {
                    "id": "cc_member_ids",
                    "label": "참조자",
                    "type": "multi_select",
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
        action: ActionItemRecord,
        principal: Principal,
        payload: dict[str, Any],
        submission: SubmissionRecord,
    ) -> dict[str, Any]:
        try:
            values = normalize_meeting_draft({key: value for key, value in payload.items() if key != "attachment_draft_ids"})
        except (MeetingError, TypeError, ValueError) as error:
            raise ActionError(str(error)) from error
        organizations = SqlAlchemyOrganizationRepository(self._session)
        profile = organizations.profile_for(str(principal.id)) or {"organizations": []}
        organization_options = [
            {"value": str(row["id"]), "label": str(row["name"])}
            for row in profile.get("organizations", [])
            if str(row["id"]) in principal.organization_scope
        ]
        meeting_repository = SqlAlchemyMeetingRepository(self._session)
        organization_ids = [str(option["value"]) for option in organization_options]
        attendee_options = []
        for row in organizations.member_directory():
            memberships = [
                organization_id
                for organization_id in organization_ids
                if meeting_repository.is_active_member_in_organization(str(row["id"]), organization_id)
            ]
            if memberships:
                attendee_options.append({
                    "value": row["id"],
                    "label": row["display_name"],
                    "organization_ids": memberships,
                })
        reference_options = [
            {"value": row["task_id"], "label": row["title"]}
            for row in self._tasks_for_principal(principal).list_for(
                principal,
                include_closed=True,
                include_organization=True,
            )
        ]
        fields: list[dict[str, Any]] = [
            {
                "id": "organization_id", "label": "조직", "type": "select", "required": True,
                "editable": True, "options": organization_options,
            },
            {"id": "title", "label": "회의 명", "type": "text", "required": True, "editable": True},
            {"id": "description", "label": "내용", "type": "textarea", "required": False, "editable": True},
            {"id": "starts_at", "label": "시작", "type": "datetime", "required": True, "editable": True},
            {"id": "ends_at", "label": "종료", "type": "datetime", "required": True, "editable": True},
            {
                "id": "visibility", "label": "공개 범위", "type": "select", "required": True,
                "editable": True,
                "options": [{"value": "private", "label": "비공개"}, {"value": "public", "label": "공개"}],
            },
            {
                "id": "host_id", "label": "주최자", "type": "person", "required": True,
                "editable": False, "value": str(principal.id), "label_value": principal.display_name,
            },
            {
                "id": "attendee_ids", "label": "참석자", "type": "multi_select", "required": False,
                "editable": True, "options": attendee_options,
            },
            {
                "id": "reference_task_ids", "label": "참고 업무", "type": "multi_select", "required": False,
                "editable": True, "options": reference_options,
            },
            {
                "id": "include_initial_note", "label": "회의록 초안도 만들기", "type": "boolean",
                "required": False, "editable": True,
            },
            {
                "id": "initial_note_body", "label": "회의록 초안", "type": "textarea",
                "required": False, "editable": True,
            },
        ]
        overlap_count = 0
        if values["organization_id"] in principal.organization_scope:
            overlap_count = int(
                self._session.scalar(
                    select(func.count())
                    .select_from(MeetingRecord)
                    .where(
                        MeetingRecord.organization_id == values["organization_id"],
                        MeetingRecord.starts_at < _parse_datetime(values["ends_at"]),
                        MeetingRecord.ends_at > _parse_datetime(values["starts_at"]),
                        # 새 모델의 상태 열은 `status` 이고 취소는 그 여섯 값 중 하나다 (SCAX-SPEC-004 §5.1).
                        MeetingRecord.status != "cancelled",
                    )
                )
                or 0
            )
        return {
            "editor": "meeting",
            "base_submission_version": int(submission.submission_version),
            "values": values,
            "fields": fields,
            "warnings": (
                [f"같은 조직에 시간이 겹치는 일정이 {overlap_count}건 있습니다."]
                if overlap_count
                else []
            ),
        }

    def _tasks_for_principal(self, principal: Principal) -> TaskApplication:
        return TaskApplication(
            SqlAlchemyTaskRepository(self._session),
            SqlAlchemyWorkRequestRepository(self._session),
            SqlAlchemyActionRepository(self._session),
            SqlAlchemyAttachmentRepository(self._session),
            SqlAlchemyOrganizationRepository(self._session),
            ProjectApplication(SqlAlchemyProjectRepository(self._session)),
        )

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
            task = repository.task(UUID(str(task_id)), str(principal.id))
        except Exception:  # not this person's task: the card says nothing about it
            return None
        for item in repository.checklist_for(task.id, include_archived=True):
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
        if principal is None or not action_item_id or self._work_requests is None:
            return None
        try:
            return action_center_application(
                self._session,
                _NO_EFFECT_EXECUTOR,
                work_requests=self._work_requests,
                evidence_reader=self._evidence_reader,
            ).detail(principal, str(action_item_id))
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
        organization = OrganizationApplication(SqlAlchemyOrganizationRepository(self._session))
        names = {str(member["id"]): str(member["display_name"]) for member in organization.member_candidates(principal)}
        names[key] = principal.display_name
        self._name_cache[key] = names
        return names

    def _readable_task_title(self, task_id: Any, principal: Principal | None) -> str | None:
        if principal is None or task_id in (None, ""):
            return None
        try:
            return str(TaskApplication(SqlAlchemyTaskRepository(self._session), SqlAlchemyWorkRequestRepository(self._session), SqlAlchemyActionRepository(self._session)).get(principal, UUID(str(task_id)))["title"])
        except (TaskError, ValueError):
            return None
