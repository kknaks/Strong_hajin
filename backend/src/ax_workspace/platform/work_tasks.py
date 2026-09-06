from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo
from uuid import UUID

from sqlalchemy import Integer, func, or_, select
from sqlalchemy.orm import Session

from ax_workspace.modules.work.application import TaskNotFound, TaskState
from ax_workspace.modules.work.requests import (
    DECISION_FACTS,
    DECISION_VERSION,
    EVIDENCE_HASH,
    EVIDENCE_MANIFEST,
    decision_facts,
    evidence_manifest,
    evidence_manifest_entry,
    evidence_manifest_hash,
)
from ax_workspace.platform.persistence import (
    ActivityEventRecord,
    AttachmentBindingRecord,
    AttachmentRecord,
    CommentRecord,
    DecisionItemRecord,
    MembershipRecord,
    RequestThreadRecord,
    ReviewAssignmentRecord,
    ReviewDecisionRecord,
    SubjectRecord,
    SubjectVersionRecord,
    SubmissionRecord,
    TaskActivityRecord,
    TaskRecord,
    WorkRequestAuditEventRecord,
    WorkRequestRecord,
    MemberRecord,
    TaskAssignmentRecord,
    TaskChecklistItemRecord,
    TaskReferenceRecord,
    TaskVersionRecord,
    WorkRequestReferenceRecord,
    ResourceRelationshipRecord,
    EvidenceRecord,
)
import hashlib
import json


# Report dates are business dates in the organization's timezone, not UTC calendar days.
BUSINESS_TIMEZONE = ZoneInfo("Asia/Seoul")


def business_date(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(BUSINESS_TIMEZONE).date().isoformat()


def _primary_unit(session: Session, member_id: str) -> str | None:
    membership = session.scalar(
        select(MembershipRecord).where(MembershipRecord.member_id == member_id).order_by(MembershipRecord.is_primary.desc(), MembershipRecord.valid_from)
    )
    return membership.organization_id if membership else None


def _person(session: Session, member_id: str) -> str:
    """A ledger line reads as a sentence about people, so it names them rather than their ids.

    `민아 (구성원)` reads as `민아` inside a sentence; the role belongs to the org surface, not to every line.
    """
    member = session.get(MemberRecord, member_id)
    if member is None:
        return member_id
    return member.display_name.split(" (")[0].strip() or member.display_name


def _content_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


#: Set for the duration of one approved AX confirmation, so every ledger line that confirmation causes can say so.
#: The actor is always the person who approved; this names only the decision the change travelled through.
_CAUSATION: ContextVar[str | None] = ContextVar("activity_causation_ref", default=None)


@contextmanager
def caused_by(causation_ref: str | None):
    """Mark everything written inside this block as carried here by `causation_ref`."""
    token = _CAUSATION.set(causation_ref)
    try:
        yield
    finally:
        _CAUSATION.reset(token)


class SqlAlchemyGraphReceiptRepository:
    """Where a delegated turn actually walked, in the order it walked. Only observed steps are written."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(self, execution_id: UUID, principal_id: str, steps: list[dict[str, Any]]) -> int:
        from ax_workspace.platform.persistence import ConversationGraphReceiptRecord, ConversationRecord, ConversationTurnRecord

        turn = self._session.scalar(select(ConversationTurnRecord).where(ConversationTurnRecord.execution_id == execution_id))
        if turn is None:
            raise ValueError("delegated conversation execution was not found")
        conversation = self._session.get(ConversationRecord, turn.conversation_id)
        if conversation is None or str(conversation.owner_id) != principal_id:  # fail closed
            raise ValueError("delegated conversation belongs to another principal")
        highest = self._session.scalar(
            select(func.max(ConversationGraphReceiptRecord.sequence)).where(ConversationGraphReceiptRecord.turn_id == turn.id)
        )
        now = datetime.now(UTC)
        written = 0
        for offset, step in enumerate(steps, start=1):
            self._session.add(
                ConversationGraphReceiptRecord(
                    turn_id=turn.id,
                    conversation_id=turn.conversation_id,
                    execution_id=execution_id,
                    sequence=int(highest or 0) + offset,
                    kind=str(step["kind"]),
                    node_ref=step.get("node_ref"),
                    node_title=step.get("node_title"),
                    edge_kind=step.get("edge_kind"),
                    from_ref=step.get("from_ref"),
                    from_title=step.get("from_title"),
                    to_ref=step.get("to_ref"),
                    to_title=step.get("to_title"),
                    observed_at=now,
                )
            )
            written += 1
        self._session.flush()
        return written

    def for_conversation(self, conversation_id: UUID) -> list[Any]:
        from ax_workspace.platform.persistence import ConversationGraphReceiptRecord

        return list(
            self._session.scalars(
                select(ConversationGraphReceiptRecord)
                .where(ConversationGraphReceiptRecord.conversation_id == conversation_id)
                .order_by(ConversationGraphReceiptRecord.observed_at, ConversationGraphReceiptRecord.sequence)
            )
        )


class ActivityLedger:
    """Append-only ERD ActivityEvent writer shared by Work repositories."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        *,
        target_type: str,
        target_id: str,
        event_kind: str,
        actor_id: str,
        safe_summary: str,
        before_ref: str | None = None,
        after_ref: str | None = None,
        reason: str | None = None,
        request_thread_id: UUID | None = None,
        actor_kind: str = "member",
    ) -> None:
        self._session.add(
            ActivityEventRecord(
                request_thread_id=request_thread_id,
                target_type=target_type,
                target_id=target_id,
                event_kind=event_kind,
                actor_kind=actor_kind,
                actor_id=actor_id,
                before_ref=before_ref,
                after_ref=after_ref,
                reason=reason,
                causation_ref=_CAUSATION.get(),
                safe_summary=safe_summary[:300],
                occurred_at=datetime.now(UTC),
            )
        )

    def for_target(self, target_type: str, target_id: str) -> list[ActivityEventRecord]:
        return list(
            self._session.scalars(
                select(ActivityEventRecord)
                .where(ActivityEventRecord.target_type == target_type, ActivityEventRecord.target_id == target_id)
                .order_by(ActivityEventRecord.occurred_at)
            )
        )

    def for_thread(self, request_thread_id: UUID) -> list[ActivityEventRecord]:
        return list(
            self._session.scalars(
                select(ActivityEventRecord).where(ActivityEventRecord.request_thread_id == request_thread_id).order_by(ActivityEventRecord.occurred_at)
            )
        )


class SqlAlchemyTaskRepository:
    def __init__(self, session: Session) -> None: self.session = session

    def create_self_task(
        self,
        owner_id: str,
        title: str,
        causation_key: str | None = None,
        *,
        description: str | None = None,
        start_date: date | None = None,
        due_date: date | None = None,
        source_action_item_id: UUID | None = None,
        checklist: list[str] | None = None,
        references: list[UUID] | None = None,
        parent_task_id: UUID | None = None,
    ) -> TaskRecord:
        if causation_key:
            existing = self.session.scalar(
                select(TaskRecord).where(TaskRecord.causation_key == causation_key)
            )
            if existing is not None:
                return existing
        now = datetime.now(UTC)
        task = TaskRecord(
            created_by_actor_id=owner_id,
            title=title,
            state=TaskState.OPEN,
            block_reason=None,
            parent_task_id=parent_task_id,
            description=description,
            start_date=start_date,
            due_date=due_date,
            organization_unit_id=_primary_unit(self.session, owner_id),
            origin_kind="direct",
            version=1,
            created_at=now,
            updated_at=now,
            causation_key=causation_key,
            source_action_item_id=source_action_item_id,
        )
        self.session.add(task)
        self.session.flush()
        self.session.add(
            TaskAssignmentRecord(
                task_id=task.id, assignee_id=owner_id, assigned_by=None, assignment_kind="self", status="active", created_at=now, accepted_at=now
            )
        )
        self.session.add(TaskActivityRecord(task_id=task.id, task_version=task.version, state=task.state, occurred_at=now))
        ActivityLedger(self.session).record(
            target_type="task", target_id=str(task.id), event_kind="task.created", actor_id=owner_id,
            after_ref=f"task:{task.id}@1", safe_summary=f"업무 생성: {title}",
        )
        self.seed_checklist(task, checklist, owner_id)
        for referenced_task_id in references or []:
            self.add_reference(task.id, referenced_task_id, owner_id)
        # Everything written with the work belongs to version 1, so the first snapshot already holds it.
        self.capture_version(task, owner_id, "task.created")
        return task

    # ---- checklist: steps inside one Task ----

    def checklist_for(self, task_id: UUID, *, include_archived: bool = False) -> list[TaskChecklistItemRecord]:
        """The steps on the list. Archived ones are off it, and only history asks for them."""
        statement = select(TaskChecklistItemRecord).where(TaskChecklistItemRecord.task_id == task_id)
        if not include_archived:
            statement = statement.where(TaskChecklistItemRecord.state == "active")
        return list(self.session.scalars(statement.order_by(TaskChecklistItemRecord.position, TaskChecklistItemRecord.created_at)))

    def checklist_progress_for(self, task_ids: list[UUID]) -> dict[UUID, tuple[int, int]]:
        """Done/total per Task in one query, so a list projection never fans out per row."""
        if not task_ids:
            return {}
        rows = self.session.execute(
            select(
                TaskChecklistItemRecord.task_id,
                func.count(TaskChecklistItemRecord.id),
                func.sum(func.cast(TaskChecklistItemRecord.done, Integer)),
            )
            .where(TaskChecklistItemRecord.task_id.in_(task_ids), TaskChecklistItemRecord.state == "active")
            .group_by(TaskChecklistItemRecord.task_id)
        ).all()
        return {task_id: (int(done or 0), int(total or 0)) for task_id, total, done in rows}

    def add_checklist_item(self, task_id: UUID, text: str, created_by: str) -> TaskChecklistItemRecord:
        """A new step always lands last; positions are never reused so archiving one cannot reorder the rest."""
        now = datetime.now(UTC)
        highest = self.session.scalar(
            select(func.max(TaskChecklistItemRecord.position)).where(TaskChecklistItemRecord.task_id == task_id)
        )
        record = TaskChecklistItemRecord(
            task_id=task_id, text=text, position=int(highest or 0) + 1, done=False, state="active", version=1,
            created_by=created_by, created_at=now, updated_at=now,
        )
        self.session.add(record)
        self.session.flush()
        return record

    # ---- subtasks: work inside work, one level deep ----

    def children_of(self, task_id: UUID) -> list[TaskRecord]:
        return list(
            self.session.scalars(
                select(TaskRecord)
                .where(TaskRecord.parent_task_id == task_id)
                .order_by(TaskRecord.created_at, TaskRecord.id)
            )
        )

    def open_children_of(self, task_id: UUID) -> list[TaskRecord]:
        """Children that are neither done nor cancelled: the ones that still hold their parent open."""
        return [task for task in self.children_of(task_id) if task.state not in {TaskState.DONE, TaskState.CANCELLED}]

    # ---- references: earlier work this Task points at ----

    def references_for(self, task_id: UUID, *, include_released: bool = False) -> list[TaskReferenceRecord]:
        statement = select(TaskReferenceRecord).where(TaskReferenceRecord.task_id == task_id)
        if not include_released:
            statement = statement.where(TaskReferenceRecord.released_at.is_(None))
        return list(self.session.scalars(statement.order_by(TaskReferenceRecord.created_at)))

    def reference(self, task_id: UUID, reference_id: UUID) -> TaskReferenceRecord | None:
        """An open pointer. A released one answers as one that is not there."""
        return self.session.scalar(
            select(TaskReferenceRecord).where(
                TaskReferenceRecord.id == reference_id,
                TaskReferenceRecord.task_id == task_id,
                TaskReferenceRecord.released_at.is_(None),
            )
        )

    def add_reference(self, task_id: UUID, referenced_task_id: UUID, created_by: str) -> TaskReferenceRecord:
        record = TaskReferenceRecord(
            task_id=task_id, referenced_task_id=referenced_task_id, created_by=created_by, created_at=datetime.now(UTC)
        )
        self.session.add(record)
        self.session.flush()
        return record

    def release_reference(self, reference: TaskReferenceRecord, actor_id: str) -> None:
        reference.released_at = datetime.now(UTC)
        reference.released_by = actor_id
        self.session.flush()

    # ---- delivery: what was handed over, and the question it puts to the person who asked ----

    #: The one question a delivery report opens, kept apart from the acceptance question that created the Task.
    DELIVERY_KIND = "task.delivery.review"

    def delivery_item(self, task: TaskRecord) -> DecisionItemRecord | None:
        return self.session.scalar(
            select(DecisionItemRecord)
            .where(
                DecisionItemRecord.kind == self.DELIVERY_KIND,
                DecisionItemRecord.context_type == "task",
                DecisionItemRecord.context_id == str(task.id),
            )
            .order_by(DecisionItemRecord.created_at)
        )

    def delivery_submissions(self, item: DecisionItemRecord) -> list[SubmissionRecord]:
        return list(
            self.session.scalars(
                select(SubmissionRecord)
                .where(SubmissionRecord.decision_item_id == item.id)
                .order_by(SubmissionRecord.submission_version)
            )
        )

    def delivery_snapshot(self, submission: SubmissionRecord) -> dict:
        version = self.session.get(SubjectVersionRecord, submission.subject_version_id)
        return dict(version.snapshot) if version is not None else {}

    def delivery_decisions(self, submission_ids: list[UUID]) -> list[ReviewDecisionRecord]:
        if not submission_ids:
            return []
        return list(
            self.session.scalars(
                select(ReviewDecisionRecord)
                .where(ReviewDecisionRecord.submission_id.in_(submission_ids))
                .order_by(ReviewDecisionRecord.decided_at)
            )
        )

    def open_delivery_round(self, task: TaskRecord, reporter_id: str, reviewer_id: str, snapshot: dict) -> SubmissionRecord:
        """Freeze this report and put it in front of the reviewer, as another round of the same question."""
        now = datetime.now(UTC)
        item = self.delivery_item(task)
        if item is None:
            subject = SubjectRecord(
                subject_type="task_delivery", owning_resource_type="task", owning_resource_id=str(task.id), created_at=now
            )
            self.session.add(subject)
            self.session.flush()
            item = DecisionItemRecord(
                kind=self.DELIVERY_KIND,
                subject_id=subject.id,
                context_type="task",
                context_id=str(task.id),
                effect_identity=f"task.delivery.accept:{task.id}",
                status="open",
                due_at=datetime.combine(task.due_date, datetime.min.time(), tzinfo=UTC) if task.due_date else None,
                created_at=now,
            )
            self.session.add(item)
            self.session.flush()
        previous = self.delivery_submissions(item)
        previous_snapshot = self.delivery_snapshot(previous[-1]) if previous else {}
        version = SubjectVersionRecord(
            subject_id=item.subject_id,
            version=len(previous) + 1,
            content_hash=_content_hash(snapshot),
            snapshot=snapshot,
            captured_at=now,
        )
        self.session.add(version)
        self.session.flush()
        diff = {
            key: {"before": previous_snapshot.get(key), "after": snapshot.get(key)}
            for key in snapshot
            if previous and previous_snapshot.get(key) != snapshot.get(key)
        }
        submission = SubmissionRecord(
            decision_item_id=item.id,
            subject_version_id=version.id,
            submission_version=len(previous) + 1,
            revises_id=previous[-1].id if previous else None,
            submitted_by=reporter_id,
            payload_hash=version.content_hash,
            decision_policy_snapshot={"decisions": ["accept", "negotiate"], "reason_required_for": ["negotiate"]},
            diff=diff or None,
            submitted_at=now,
        )
        self.session.add(submission)
        self.session.flush()
        if previous:
            open_assignment = self.session.scalar(
                select(ReviewAssignmentRecord)
                .where(ReviewAssignmentRecord.submission_id == previous[-1].id, ReviewAssignmentRecord.status == "pending")
            )
            if open_assignment is not None:
                open_assignment.status = "superseded"
        self.session.add(
            ReviewAssignmentRecord(
                submission_id=submission.id, reviewer_member_id=reviewer_id, status="pending", assigned_at=now, due_at=item.due_at
            )
        )
        item.status = "open"
        self.session.flush()
        return submission

    def record_delivery_decision(
        self, submission: SubmissionRecord, actor_id: str, decision: str, *, reason: str | None = None, expected_version: int
    ) -> ReviewDecisionRecord:
        now = datetime.now(UTC)
        assignment = self.session.scalar(
            select(ReviewAssignmentRecord)
            .where(ReviewAssignmentRecord.submission_id == submission.id, ReviewAssignmentRecord.status == "pending")
        )
        if assignment is not None:
            assignment.status = "answered"
        record = ReviewDecisionRecord(
            submission_id=submission.id,
            review_assignment_id=assignment.id if assignment is not None else None,
            actor_member_id=actor_id,
            decision=decision,
            reason=reason,
            conditions={DECISION_FACTS: {DECISION_VERSION: expected_version}},
            decided_at=now,
        )
        self.session.add(record)
        item = self.session.get(DecisionItemRecord, submission.decision_item_id)
        if item is not None:
            item.status = "resolved" if decision == "accept" else "open"
        self.session.flush()
        return record

    def seed_checklist(self, task: TaskRecord, texts: list[str] | None, author_id: str) -> None:
        """Steps written with the work itself. They belong to version 1, so no version moves for them."""
        for text in texts or []:
            self.add_checklist_item(task.id, text, author_id)

    def checklist_item(self, task_id: UUID, item_id: UUID, *, lock: bool = False) -> TaskChecklistItemRecord | None:
        """A step still on the list. An archived one answers as one that is not there."""
        statement = select(TaskChecklistItemRecord).where(
            TaskChecklistItemRecord.id == item_id,
            TaskChecklistItemRecord.task_id == task_id,
            TaskChecklistItemRecord.state == "active",
        )
        return self.session.scalar(
            statement.with_for_update().execution_options(populate_existing=True) if lock else statement
        )

    def archive_checklist_item(self, item: TaskChecklistItemRecord, actor_id: str) -> None:
        """Off the list, still in the record: what someone wrote and checked is not erased by tidying up."""
        now = datetime.now(UTC)
        item.state = "archived"
        item.archived_by = actor_id
        item.archived_at = now
        item.version += 1
        item.updated_at = now
        self.session.flush()

    def reorder_checklist(self, items: list[TaskChecklistItemRecord], ordered_ids: list[UUID]) -> list[TaskChecklistItemRecord]:
        """Rewrite the whole order in one go, so no two steps can end up claiming the same place."""
        now = datetime.now(UTC)
        by_id = {item.id: item for item in items}
        for position, item_id in enumerate(ordered_ids, start=1):
            item = by_id[item_id]
            if item.position != position:
                item.position = position
                item.version += 1
                item.updated_at = now
        self.session.flush()
        return [by_id[item_id] for item_id in ordered_ids]

    def member_display_name(self, member_id: str) -> str | None:
        member = self.session.get(MemberRecord, member_id)
        return member.display_name if member is not None else None

    def task_by_id(self, task_id: UUID) -> TaskRecord | None:
        """The Task itself, with no holder scope. Callers must decide separately who may see it."""
        return self.session.get(TaskRecord, task_id)

    def origin_facts(self, tasks: list[TaskRecord]) -> dict[UUID, dict[str, Any]]:
        """Raw origin facts per Task, straight from the canonical columns; the application decides what may be shown."""
        if not tasks:
            return {}
        request_ids = [task.source_work_request_id for task in tasks if task.source_work_request_id is not None]
        requests = {
            row.id: row
            for row in (
                self.session.scalars(select(WorkRequestRecord).where(WorkRequestRecord.id.in_(request_ids))).all() if request_ids else []
            )
        }
        assignments: dict[UUID, TaskAssignmentRecord] = {}
        for assignment in self.session.scalars(
            select(TaskAssignmentRecord)
            .where(TaskAssignmentRecord.task_id.in_([task.id for task in tasks]))
            .order_by(TaskAssignmentRecord.created_at)
        ).all():
            assignments.setdefault(assignment.task_id, assignment)
        facts: dict[UUID, dict[str, Any]] = {}
        for task in tasks:
            assignment = assignments.get(task.id)
            request = requests.get(task.source_work_request_id) if task.source_work_request_id else None
            facts[task.id] = {
                "source_work_request_id": task.source_work_request_id,
                "request_requester_id": request.requester_id if request is not None else None,
                "request_title": request.title if request is not None else None,
                "assignment_kind": assignment.assignment_kind if assignment is not None else None,
                "assigned_by": assignment.assigned_by if assignment is not None else None,
                "assignee_id": assignment.assignee_id if assignment is not None else None,
            }
        return facts

    def derived_task_ids(self, request_ids: list[UUID]) -> dict[UUID, UUID]:
        """WorkRequest → Task, read back through the Task's own source FK rather than a column on the request."""
        if not request_ids:
            return {}
        rows = self.session.execute(
            select(TaskRecord.source_work_request_id, TaskRecord.id).where(TaskRecord.source_work_request_id.in_(request_ids))
        ).all()
        return {request_id: task_id for request_id, task_id in rows}

    def record_activity(self, task: TaskRecord, actor_id: str, event_kind: str, summary: str, *, before_ref: str | None = None, reason: str | None = None) -> None:
        """One seam for every meaningful change: the ledger line a person reads, and the snapshot they can go back to."""
        ActivityLedger(self.session).record(
            target_type="task", target_id=str(task.id), event_kind=event_kind, actor_id=actor_id,
            before_ref=before_ref, after_ref=f"task:{task.id}@{task.version}", reason=reason, safe_summary=summary,
            request_thread_id=task.request_thread_id,
        )
        self.capture_version(task, actor_id, event_kind, reason)

    def capture_version(self, task: TaskRecord, actor_id: str, change_kind: str, reason: str | None = None) -> None:
        """Freeze the Task as it now is, in the transaction that made it so.

        One row per version: a change that did not move the version has nothing new to freeze.
        """
        existing = self.session.scalar(
            select(TaskVersionRecord).where(TaskVersionRecord.task_id == task.id, TaskVersionRecord.version == task.version)
        )
        if existing is not None:
            return
        self.session.add(
            TaskVersionRecord(
                task_id=task.id,
                version=task.version,
                change_kind=change_kind,
                actor_id=actor_id,
                reason=reason,
                snapshot=self.task_snapshot(task),
                captured_at=datetime.now(UTC),
            )
        )
        self.session.flush()

    def task_snapshot(self, task: TaskRecord) -> dict:
        """What the Task is right now, including what a current screen may hide later.

        Materials are referenced, never copied: the Attachment identity and its integrity hash are enough to say what
        was attached, and the bytes keep exactly one home.
        """
        checklist = [
            {
                "item_id": str(item.id),
                "text": item.text,
                "position": item.position,
                "done": bool(item.done),
                "state": item.state,
                "version": int(item.version),
                "created_by": item.created_by,
                "completed_by": item.completed_by,
            }
            # History keeps the steps a current screen hides, so an archived one is part of what the Task then was.
            for item in self.checklist_for(task.id, include_archived=True)
        ]
        materials = [
            {
                "material_id": str(binding.id),
                "attachment_id": str(attachment.id),
                "kind": binding.role,
                "name": attachment.name,
                "source_kind": attachment.source_kind,
                "integrity_ref": attachment.integrity_ref,
            }
            for binding, attachment in SqlAlchemyAttachmentRepository(self.session).bindings_for("task", str(task.id))
            if binding.unbound_at is None
        ]
        references = [
            {"reference_id": str(row.id), "referenced_task_id": str(row.referenced_task_id), "created_by": row.created_by}
            for row in self.references_for(task.id)
        ]
        assignment = self.session.scalar(
            select(TaskAssignmentRecord).where(
                TaskAssignmentRecord.task_id == task.id, TaskAssignmentRecord.status.in_(("active", "pending"))
            )
        )
        return {
            "title": task.title,
            "description": task.description,
            "state": task.state,
            "block_reason": task.block_reason,
            "start_date": task.start_date.isoformat() if task.start_date else None,
            "due_date": task.due_date.isoformat() if task.due_date else None,
            "created_by_actor_id": task.created_by_actor_id,
            "source_work_request_id": str(task.source_work_request_id) if task.source_work_request_id else None,
            "assignment": (
                {
                    "assignment_id": str(assignment.id),
                    "assignee_id": assignment.assignee_id,
                    "assigned_by": assignment.assigned_by,
                    "kind": assignment.assignment_kind,
                    "status": assignment.status,
                }
                if assignment
                else None
            ),
            "checklist": checklist,
            "materials": materials,
            "references": references,
            "children": [str(child.id) for child in self.children_of(task.id)],
        }

    def versions_for(self, task_id: UUID) -> list[TaskVersionRecord]:
        return list(
            self.session.scalars(
                select(TaskVersionRecord).where(TaskVersionRecord.task_id == task_id).order_by(TaskVersionRecord.version)
            )
        )

    def activity_for(self, task_id: UUID) -> list[ActivityEventRecord]:
        return ActivityLedger(self.session).for_target("task", str(task_id))

    def task(self, task_id: UUID, owner_id: str, *, lock: bool = False) -> TaskRecord:
        """A task is the owner's to read or drive only while they hold an active TaskAssignment for it."""
        statement = self._held_by(owner_id).where(TaskRecord.id == task_id)
        task = self.session.scalar(statement.with_for_update(of=TaskRecord).execution_options(populate_existing=True) if lock else statement)
        if task is None:
            raise TaskNotFound("task was not found")
        return task

    def tasks_for(self, owner_id: str, *, include_closed: bool = False) -> list[TaskRecord]:
        statement = self._held_by(owner_id)
        if not include_closed:
            statement = statement.where(TaskRecord.state.not_in([TaskState.DONE, TaskState.CANCELLED]))
        return list(self.session.scalars(statement.order_by(TaskRecord.created_at)))

    def tasks_held_by_members(self, member_ids: frozenset[str], *, include_closed: bool = False) -> list[TaskRecord]:
        """Every task those people currently hold. Used only for an organization-wide read, never to act on them."""
        if not member_ids:
            return []
        statement = (
            select(TaskRecord)
            .join(TaskAssignmentRecord, TaskAssignmentRecord.task_id == TaskRecord.id)
            .where(TaskAssignmentRecord.assignee_id.in_(member_ids), TaskAssignmentRecord.status == "active")
        )
        if not include_closed:
            statement = statement.where(TaskRecord.state.not_in([TaskState.DONE, TaskState.CANCELLED]))
        return list(self.session.scalars(statement.order_by(TaskRecord.created_at)))

    @staticmethod
    def _held_by(owner_id: str):
        return (
            select(TaskRecord)
            .join(TaskAssignmentRecord, TaskAssignmentRecord.task_id == TaskRecord.id)
            .where(TaskAssignmentRecord.assignee_id == owner_id, TaskAssignmentRecord.status == "active")
        )

    def touch(self, task: TaskRecord) -> None:
        task.updated_at = datetime.now(UTC)
        self.session.add(TaskActivityRecord(task_id=task.id, task_version=task.version, state=task.state, occurred_at=task.updated_at))


class SqlAlchemyWorkRecordSource:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list(self, principal, report_date: str) -> list[dict[str, object]]:
        activities = self._session.scalars(
            select(TaskActivityRecord)
            .join(TaskRecord, TaskRecord.id == TaskActivityRecord.task_id)
            .join(TaskAssignmentRecord, TaskAssignmentRecord.task_id == TaskRecord.id)
            .where(TaskAssignmentRecord.assignee_id == str(principal.id), TaskAssignmentRecord.status == "active")
            .order_by(TaskActivityRecord.occurred_at)
        )
        return [
            {
                "task_id": str(item.task_id),
                "task_version": item.task_version,
                "state": item.state,
                "occurred_at": item.occurred_at.isoformat(),
            }
            for item in activities
            if business_date(item.occurred_at) == report_date
        ]


class SqlAlchemyWorkRequestRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_request(
        self,
        requester_id: str,
        assignee_id: str,
        title: str,
        causation_key: str | None = None,
        *,
        description: str | None = None,
        due_date: date | None = None,
        cc_member_ids: list[str] | None = None,
        checklist: list[str] | None = None,
        reference_task_ids: list[UUID] | None = None,
    ) -> tuple[WorkRequestRecord, bool]:
        if causation_key:
            existing = self._session.scalar(
                select(WorkRequestRecord).where(WorkRequestRecord.causation_key == causation_key)
            )
            if existing is not None:
                return existing, False
        now = datetime.now(UTC)
        context_unit = _primary_unit(self._session, requester_id)
        thread = RequestThreadRecord(initiated_by=requester_id, organization_context_id=context_unit, purpose=title, created_at=now)
        self._session.add(thread)
        self._session.flush()
        request = WorkRequestRecord(
            request_thread_id=thread.id,
            organization_context_id=context_unit,
            requester_id=requester_id,
            assignee_id=assignee_id,
            title=title,
            description=description,
            due_date=due_date,
            state="pending",
            version=1,
            conditions=None,
            initial_checklist=list(checklist) if checklist else None,
            created_at=now,
            updated_at=now,
            causation_key=causation_key,
        )
        self._session.add(request)
        self._session.flush()
        for referenced_task_id in reference_task_ids or []:
            self._session.add(
                WorkRequestReferenceRecord(
                    work_request_id=request.id, referenced_task_id=referenced_task_id,
                    created_by=requester_id, created_at=now,
                )
            )
        subject = SubjectRecord(subject_type="payload", owning_resource_type="work_request", owning_resource_id=str(request.id), created_at=now)
        self._session.add(subject)
        self._session.flush()
        request.subject_id = subject.id
        # ERD RESOURCE_RELATIONSHIP: requester, assignee, and cc members hold period-bound relationships to the request.
        for member_id, kind in [(requester_id, "requester"), (assignee_id, "assignee"), *[(cc, "cc") for cc in (cc_member_ids or [])]]:
            self._session.add(
                ResourceRelationshipRecord(member_id=member_id, resource_type="work_request", resource_id=str(request.id), relationship_kind=kind, valid_from=now)
            )
        snapshot = {"title": title, "description": description, "due_date": due_date.isoformat() if due_date else None, "assignee_id": assignee_id}
        version = SubjectVersionRecord(subject_id=subject.id, version=1, content_hash=_content_hash(snapshot), snapshot=snapshot, captured_at=now)
        self._session.add(version)
        item = DecisionItemRecord(
            kind="work_request.acceptance",
            subject_id=subject.id,
            context_type="request_thread",
            context_id=str(thread.id),
            effect_identity=f"task.create_from_request:{request.id}",
            status="open",
            due_at=datetime.combine(due_date, datetime.min.time(), tzinfo=UTC) if due_date else None,
            created_at=now,
        )
        self._session.add(item)
        self._session.flush()
        submission = SubmissionRecord(
            decision_item_id=item.id,
            subject_version_id=version.id,
            submission_version=1,
            submitted_by=requester_id,
            payload_hash=version.content_hash,
            decision_policy_snapshot={"decisions": ["accept", "negotiate", "reject"], "reason_required_for": ["reject", "negotiate"]},
            submitted_at=now,
        )
        self._session.add(submission)
        self._session.flush()
        self._session.add(
            ReviewAssignmentRecord(submission_id=submission.id, reviewer_member_id=assignee_id, status="pending", assigned_at=now, due_at=item.due_at)
        )
        ActivityLedger(self._session).record(
            target_type="work_request", target_id=str(request.id), event_kind="work_request.created", actor_id=requester_id,
            after_ref=f"work_request:{request.id}@1",
            safe_summary=f"{_person(self._session, requester_id)}가 {_person(self._session, assignee_id)}에게 업무를 보냄: {title}",
            request_thread_id=thread.id,
        )
        return request, True

    # ---- ERD decision continuity ----

    def open_decision_item(self, request: WorkRequestRecord) -> DecisionItemRecord | None:
        if request.subject_id is None:
            return None
        return self._session.scalar(
            select(DecisionItemRecord)
            .where(DecisionItemRecord.subject_id == request.subject_id, DecisionItemRecord.kind == "work_request.acceptance")
            .order_by(DecisionItemRecord.created_at.desc())
        )

    def current_submission(self, request: WorkRequestRecord) -> SubmissionRecord | None:
        item = self.open_decision_item(request)
        if item is None:
            return None
        return self._session.scalar(
            select(SubmissionRecord).where(SubmissionRecord.decision_item_id == item.id).order_by(SubmissionRecord.submission_version.desc())
        )

    def active_assignment(self, submission: SubmissionRecord) -> ReviewAssignmentRecord | None:
        return self._session.scalar(
            select(ReviewAssignmentRecord)
            .where(ReviewAssignmentRecord.submission_id == submission.id, ReviewAssignmentRecord.status == "pending")
            .order_by(ReviewAssignmentRecord.assigned_at.desc())
        )

    def record_decision(
        self,
        request: WorkRequestRecord,
        actor_id: str,
        decision: str,
        *,
        reason: str | None = None,
        conditions: dict | None = None,
        expected_version: int | None = None,
    ) -> ReviewDecisionRecord | None:
        submission = self.current_submission(request)
        if submission is None:
            return None
        assignment = self.active_assignment(submission)
        if assignment is None:
            return None
        now = datetime.now(UTC)
        assignment.status = "decided"
        manifest = self.evidence_manifest_for(submission)
        record = ReviewDecisionRecord(
            review_assignment_id=assignment.id,
            submission_id=submission.id,
            actor_member_id=actor_id,
            decision=decision,
            reason=reason,
            # What the server froze sits under its own key, beside whatever conditions the decision itself carried.
            conditions={
                **(conditions or {}),
                DECISION_FACTS: {
                    DECISION_VERSION: int(expected_version) if expected_version is not None else int(request.version),
                    EVIDENCE_HASH: evidence_manifest_hash(manifest),
                    EVIDENCE_MANIFEST: manifest,
                },
            },
            decided_at=now,
        )
        self._session.add(record)
        item = self.open_decision_item(request)
        if item is not None:
            if decision == "negotiate":
                item.status = "awaiting_revision"
            else:
                item.status = "resolved"
                item.resolved_at = now
        self._session.flush()
        ActivityLedger(self._session).record(
            target_type="work_request", target_id=str(request.id), event_kind=f"work_request.{decision}", actor_id=actor_id,
            before_ref=f"submission:{submission.id}", after_ref=f"review_decision:{record.id}", reason=reason,
            safe_summary=f"업무 요청 {decision}: {request.title}", request_thread_id=request.request_thread_id,
        )
        return record

    def withdraw(self, request: WorkRequestRecord, actor_id: str) -> None:
        """The submitter retracts the question. Not a ReviewDecision: nobody judged it, so the round stays open-ended."""
        now = datetime.now(UTC)
        item = self.open_decision_item(request)
        submission = self.current_submission(request)
        if submission is not None:
            assignment = self.active_assignment(submission)
            if assignment is not None:
                assignment.status = "cancelled"
        if item is not None:
            item.status = "resolved"
            item.resolved_at = now
        self._session.flush()
        ActivityLedger(self._session).record(
            target_type="work_request", target_id=str(request.id), event_kind="work_request.withdrawn", actor_id=actor_id,
            before_ref=f"submission:{submission.id}" if submission else None,
            safe_summary=f"업무 요청 철회: {request.title}", request_thread_id=request.request_thread_id,
        )

    def resubmit(self, request: WorkRequestRecord, actor_id: str, snapshot: dict) -> SubmissionRecord:
        """A revision answers an adjustment: a new SubjectVersion + Submission with a diff; the prior decision stays."""
        return self._open_round(request, actor_id, snapshot, event_kind="work_request.resubmitted", summary="재상신")

    def amend(self, request: WorkRequestRecord, actor_id: str, snapshot: dict) -> SubmissionRecord:
        """A requester improving their own open request: the same round trip, minus anyone having asked for it."""
        return self._open_round(request, actor_id, snapshot, event_kind="work_request.amended", summary="수정")

    def _open_round(
        self, request: WorkRequestRecord, actor_id: str, snapshot: dict, *, event_kind: str, summary: str
    ) -> SubmissionRecord:
        """One new round on the same question: a frozen Submission, the basis carried forward, one open assignment.

        Whatever the assignee was looking at stops being the question. If they had not answered yet that assignment is
        superseded rather than left open, so at most one review is ever pending on this request.
        """
        now = datetime.now(UTC)
        item = self.open_decision_item(request)
        previous = self.current_submission(request)
        assert item is not None and previous is not None and request.subject_id is not None
        previous_version = self._session.get(SubjectVersionRecord, previous.subject_version_id)
        assert previous_version is not None
        version = SubjectVersionRecord(
            subject_id=request.subject_id,
            version=previous_version.version + 1,
            content_hash=_content_hash(snapshot),
            snapshot=snapshot,
            captured_at=now,
        )
        self._session.add(version)
        self._session.flush()
        diff = {key: {"before": previous_version.snapshot.get(key), "after": snapshot.get(key)} for key in snapshot if previous_version.snapshot.get(key) != snapshot.get(key)}
        submission = SubmissionRecord(
            decision_item_id=item.id,
            subject_version_id=version.id,
            submission_version=previous.submission_version + 1,
            revises_id=previous.id,
            submitted_by=actor_id,
            payload_hash=version.content_hash,
            decision_policy_snapshot=previous.decision_policy_snapshot,
            diff=diff,
            submitted_at=now,
        )
        self._session.add(submission)
        self._session.flush()
        inherited = self._inherit_evidence(previous, submission)
        previous_assignment = self._session.scalar(
            select(ReviewAssignmentRecord).where(ReviewAssignmentRecord.submission_id == previous.id).order_by(ReviewAssignmentRecord.assigned_at.desc())
        )
        if previous_assignment is not None and previous_assignment.status == "pending":
            previous_assignment.status = "superseded"
        # The question is judged against this round, so the deadline shown on it is this round's, not the first one's.
        item.due_at = (
            datetime.combine(request.due_date, datetime.min.time(), tzinfo=UTC) if request.due_date else None
        )
        self._session.add(
            ReviewAssignmentRecord(
                submission_id=submission.id,
                reviewer_member_id=request.assignee_id,
                supersedes_assignment_id=previous_assignment.id if previous_assignment else None,
                status="pending",
                assigned_at=now,
                due_at=item.due_at,
            )
        )
        item.status = "open"
        ledger = ActivityLedger(self._session)
        ledger.record(
            target_type="work_request", target_id=str(request.id), event_kind=event_kind, actor_id=actor_id,
            before_ref=f"submission:{previous.id}", after_ref=f"submission:{submission.id}",
            safe_summary=f"업무 요청 {summary} v{submission.submission_version}: {request.title}",
            request_thread_id=request.request_thread_id,
        )
        if inherited:
            ledger.record(
                target_type="work_request", target_id=str(request.id), event_kind="work_request.evidence_inherited",
                actor_id=actor_id, before_ref=f"submission:{previous.id}", after_ref=f"submission:{submission.id}",
                safe_summary=f"이전 회차 근거 {inherited}건을 새 회차로 이어받음: {request.title}",
                request_thread_id=request.request_thread_id,
            )
        return submission

    def timeline(self, request: WorkRequestRecord) -> dict:
        item = self.open_decision_item(request)
        submissions = (
            list(self._session.scalars(select(SubmissionRecord).where(SubmissionRecord.decision_item_id == item.id).order_by(SubmissionRecord.submission_version)))
            if item
            else []
        )
        versions = {v.id: v for v in self._session.scalars(select(SubjectVersionRecord).where(SubjectVersionRecord.subject_id == request.subject_id))} if request.subject_id else {}
        submission_ids = [s.id for s in submissions]
        assignments = list(self._session.scalars(select(ReviewAssignmentRecord).where(ReviewAssignmentRecord.submission_id.in_(submission_ids)).order_by(ReviewAssignmentRecord.assigned_at))) if submission_ids else []
        decisions = list(self._session.scalars(select(ReviewDecisionRecord).where(ReviewDecisionRecord.submission_id.in_(submission_ids)).order_by(ReviewDecisionRecord.decided_at))) if submission_ids else []
        events = ActivityLedger(self._session).for_thread(request.request_thread_id) if request.request_thread_id else []
        bases = self.evidence_manifests_for(submission_ids)
        return {
            "request_thread_id": str(request.request_thread_id) if request.request_thread_id else None,
            "decision_item": {"decision_item_id": str(item.id), "kind": item.kind, "status": item.status, "due_at": item.due_at.isoformat() if item.due_at else None} if item else None,
            "submissions": [
                {
                    "submission_id": str(s.id),
                    "submission_version": s.submission_version,
                    "revises_id": str(s.revises_id) if s.revises_id else None,
                    "submitted_by": s.submitted_by,
                    "submitted_at": s.submitted_at.isoformat(),
                    "snapshot": versions[s.subject_version_id].snapshot if s.subject_version_id in versions else {},
                    "subject_version": versions[s.subject_version_id].version if s.subject_version_id in versions else None,
                    "diff": s.diff,
                    "evidence": bases[s.id],
                    "evidence_hash": evidence_manifest_hash(bases[s.id]),
                }
                for s in submissions
            ],
            "review_assignments": [
                {"review_assignment_id": str(a.id), "submission_id": str(a.submission_id), "reviewer_member_id": a.reviewer_member_id, "status": a.status, "assigned_at": a.assigned_at.isoformat()}
                for a in assignments
            ],
            "review_decisions": [
                {
                    "review_decision_id": str(d.id), "submission_id": str(d.submission_id), "actor_member_id": d.actor_member_id,
                    "decision": d.decision, "reason": d.reason, "conditions": d.conditions,
                    "evidence_hash": decision_facts(d.conditions).get(EVIDENCE_HASH),
                    "decided_at": d.decided_at.isoformat(),
                }
                for d in decisions
            ],
            "activity": [
                {"event_kind": e.event_kind, "actor_id": e.actor_id, "safe_summary": e.safe_summary, "reason": e.reason, "occurred_at": e.occurred_at.isoformat()}
                for e in events
            ],
        }

    def request(self, request_id: UUID, *, lock: bool = False) -> WorkRequestRecord | None:
        statement = select(WorkRequestRecord).where(WorkRequestRecord.id == request_id)
        return self._session.scalar(statement.with_for_update().execution_options(populate_existing=True) if lock else statement)

    def create_accepted_task(self, request: WorkRequestRecord) -> TaskRecord:
        now = datetime.now(UTC)
        item = self.open_decision_item(request)
        submission = self.current_submission(request)
        decision = self._session.scalar(
            select(ReviewDecisionRecord).where(ReviewDecisionRecord.submission_id == submission.id).order_by(ReviewDecisionRecord.decided_at.desc())
        ) if submission else None
        task = TaskRecord(
            created_by_actor_id=request.assignee_id,
            title=request.title,
            description=request.description,
            due_date=request.due_date,
            organization_unit_id=_primary_unit(self._session, request.assignee_id),
            origin_kind="request_effect",
            request_thread_id=request.request_thread_id,
            source_work_request_id=request.id,
            source_decision_item_id=item.id if item else None,
            source_submission_id=submission.id if submission else None,
            source_review_decision_id=decision.id if decision else None,
            state=TaskState.OPEN,
            block_reason=None,
            version=1,
            created_at=now,
            updated_at=now,
        )
        self._session.add(task)
        self._session.flush()
        self._session.add(
            TaskActivityRecord(task_id=task.id, task_version=task.version, state=task.state, occurred_at=now)
        )
        self._session.add(
            TaskAssignmentRecord(
                task_id=task.id,
                assignee_id=request.assignee_id,
                assigned_by=None,
                assignment_kind="request_effect",
                status="active",
                source_work_request_id=request.id,
                source_decision_item_id=item.id if item else None,
                source_review_decision_id=decision.id if decision else None,
                created_at=now,
                accepted_at=now,
            )
        )
        self._session.flush()
        tasks = SqlAlchemyTaskRepository(self._session)
        # The steps came with the request, so they were written by the person who asked, not by the one accepting.
        tasks.seed_checklist(task, list(request.initial_checklist or []), request.requester_id)
        # So did the earlier work they pointed at: the pointer travels, the permission to open it does not.
        for reference in self.request_references(request.id):
            tasks.add_reference(task.id, reference.referenced_task_id, request.requester_id)
        tasks.capture_version(task, request.assignee_id, "task.created")
        return task

    def request_references(self, request_id: UUID) -> list[WorkRequestReferenceRecord]:
        return list(
            self._session.scalars(
                select(WorkRequestReferenceRecord)
                .where(WorkRequestReferenceRecord.work_request_id == request_id)
                .order_by(WorkRequestReferenceRecord.created_at)
            )
        )

    def append_audit(self, request_id: UUID, actor_id: str, event_type: str, payload: dict) -> None:
        self._session.add(
            WorkRequestAuditEventRecord(
                request_id=request_id,
                actor_id=actor_id,
                event_type=event_type,
                payload=payload,
                occurred_at=datetime.now(UTC),
            )
        )

    def rebuild_relationships(self) -> int:
        """Rebuild the access projection from the canonical rows it is derived from.

        `ResourceRelationship` says who may reach a resource; it never decides who sent or holds the work. Because it
        is derived, it must be reconstructible — this is that reconstruction, and the contract test proves the rebuilt
        set equals the one the writes produced.
        """
        existing = {
            (row.member_id, row.resource_type, row.resource_id, row.relationship_kind)
            for row in self._session.scalars(select(ResourceRelationshipRecord))
            if row.valid_until is None
        }
        added = 0
        for request in self._session.scalars(select(WorkRequestRecord)):
            wanted = [(request.requester_id, "requester"), (request.assignee_id, "assignee")]
            wanted += [(member_id, "cc") for member_id in self.cc_member_ids(request)]
            for member_id, kind in wanted:
                key = (member_id, "work_request", str(request.id), kind)
                if key in existing:
                    continue
                self._session.add(
                    ResourceRelationshipRecord(
                        member_id=member_id, resource_type="work_request", resource_id=str(request.id),
                        relationship_kind=kind, valid_from=request.created_at,
                    )
                )
                existing.add(key)
                added += 1
        self._session.flush()
        return added

    def audit_payloads(self, request_id: UUID, event_type: str) -> list[dict]:
        """What was recorded for one kind of command on this request, oldest first."""
        return [
            dict(row.payload or {})
            for row in self._session.scalars(
                select(WorkRequestAuditEventRecord)
                .where(WorkRequestAuditEventRecord.request_id == request_id, WorkRequestAuditEventRecord.event_type == event_type)
                .order_by(WorkRequestAuditEventRecord.occurred_at, WorkRequestAuditEventRecord.id)
            )
        ]

    def inbox_for(self, assignee_id: str) -> list[WorkRequestRecord]:
        return list(
            self._session.scalars(
                select(WorkRequestRecord)
                .where(
                    WorkRequestRecord.assignee_id == assignee_id,
                    WorkRequestRecord.state.in_(("pending", "negotiating")),
                )
                .order_by(WorkRequestRecord.created_at)
            )
        )

    def list_for(self, principal_id: str) -> list[WorkRequestRecord]:
        cc_ids = [
            UUID(resource_id)
            for resource_id in self._session.scalars(
                select(ResourceRelationshipRecord.resource_id).where(
                    ResourceRelationshipRecord.member_id == principal_id,
                    ResourceRelationshipRecord.resource_type == "work_request",
                    ResourceRelationshipRecord.relationship_kind == "cc",
                    ResourceRelationshipRecord.valid_until.is_(None),
                )
            )
        ]
        return list(
            self._session.scalars(
                select(WorkRequestRecord)
                .where(
                    or_(
                        WorkRequestRecord.requester_id == principal_id,
                        WorkRequestRecord.assignee_id == principal_id,
                        WorkRequestRecord.id.in_(cc_ids) if cc_ids else False,
                    )
                )
                .order_by(WorkRequestRecord.created_at)
            )
        )

    def derived_task_ids(self, requests: list[WorkRequestRecord]) -> dict[UUID, UUID]:
        """The Task a request produced, read back through the Task's own source FK.

        The link lives in one place. Nothing is mirrored onto the request row, so a reload or a new session resolves
        the same edge instead of depending on whatever a list response happened to carry.
        """
        if not requests:
            return {}
        rows = self._session.execute(
            select(TaskRecord.source_work_request_id, TaskRecord.id).where(
                TaskRecord.source_work_request_id.in_([request.id for request in requests])
            )
        ).all()
        return {request_id: task_id for request_id, task_id in rows}

    def cc_member_ids(self, request: WorkRequestRecord) -> list[str]:
        return list(
            self._session.scalars(
                select(ResourceRelationshipRecord.member_id)
                .where(
                    ResourceRelationshipRecord.resource_type == "work_request",
                    ResourceRelationshipRecord.resource_id == str(request.id),
                    ResourceRelationshipRecord.relationship_kind == "cc",
                    ResourceRelationshipRecord.valid_until.is_(None),
                )
                .order_by(ResourceRelationshipRecord.member_id)
            )
        )

    def adopt_evidence(self, submission: SubmissionRecord, attachment: AttachmentRecord, *, role: str, adopted_by: str) -> EvidenceRecord:
        """ERD EVIDENCE: an attachment explicitly adopted as basis for one Submission, pinned by its integrity hash."""
        record = EvidenceRecord(
            submission_id=submission.id,
            attachment_id=attachment.id,
            evidence_role=role,
            fixed_snapshot_ref=attachment.integrity_ref,
            mutable_source=False,
            adopted_by=adopted_by,
            adopted_at=datetime.now(UTC),
        )
        self._session.add(record)
        self._session.flush()
        return record

    def _inherit_evidence(self, previous: SubmissionRecord, submission: SubmissionRecord) -> int:
        """Carry the previous round's basis into the new one as its own rows, and say how much was carried.

        Append-only: no attachment or byte is copied, only the adoption. Each copy keeps the author and moment of the
        adoption it repeats, so no row ever claims that someone adopted something at a time they did nothing. The copy
        itself is the revising actor's doing and is recorded as that by the caller, after the revision it follows from.
        """
        sources = list(
            self._session.scalars(
                select(EvidenceRecord).where(EvidenceRecord.submission_id == previous.id).order_by(EvidenceRecord.adopted_at, EvidenceRecord.id)
            )
        )
        for source in sources:
            self._session.add(
                EvidenceRecord(
                    submission_id=submission.id,
                    attachment_id=source.attachment_id,
                    evidence_role=source.evidence_role,
                    fixed_snapshot_ref=source.fixed_snapshot_ref,
                    mutable_source=source.mutable_source,
                    adopted_by=source.adopted_by,
                    adopted_at=source.adopted_at,
                )
            )
        self._session.flush()
        return len(sources)

    def evidence_count_for(self, submission: SubmissionRecord) -> int:
        """How many adoptions this round holds. Right after a revision that is exactly what it inherited."""
        return len(list(self._session.scalars(select(EvidenceRecord).where(EvidenceRecord.submission_id == submission.id))))

    def evidence_manifest_for(self, submission: SubmissionRecord) -> list[dict[str, str]]:
        """The basis this Submission currently stands on, in canonical order."""
        return self.evidence_manifests_for([submission.id]).get(submission.id, [])

    def evidence_manifests_for(self, submission_ids: list[UUID]) -> dict[UUID, list[dict[str, str]]]:
        """Every round's basis in one read, so a timeline does not fan out per round."""
        if not submission_ids:
            return {}
        grouped: dict[UUID, list[dict[str, str]]] = {submission_id: [] for submission_id in submission_ids}
        for row in self._session.scalars(select(EvidenceRecord).where(EvidenceRecord.submission_id.in_(submission_ids))):
            grouped[row.submission_id].append(
                evidence_manifest_entry(row.attachment_id, row.evidence_role, row.fixed_snapshot_ref)
            )
        return {submission_id: evidence_manifest(entries) for submission_id, entries in grouped.items()}

    def evidence_for(self, request: WorkRequestRecord) -> list[tuple[EvidenceRecord, AttachmentRecord, SubmissionRecord]]:
        item = self.open_decision_item(request)
        if item is None:
            return []
        rows = self._session.execute(
            select(EvidenceRecord, AttachmentRecord, SubmissionRecord)
            .join(AttachmentRecord, AttachmentRecord.id == EvidenceRecord.attachment_id)
            .join(SubmissionRecord, SubmissionRecord.id == EvidenceRecord.submission_id)
            .where(SubmissionRecord.decision_item_id == item.id)
            .order_by(SubmissionRecord.submission_version, EvidenceRecord.adopted_at, EvidenceRecord.id)
        ).all()
        return [(evidence, attachment, submission) for evidence, attachment, submission in rows]


class SqlAlchemyTaskAssignmentRepository:
    """ERD TASK_ASSIGNMENT + assignment-acceptance ActionItem for manager-assigned Tasks."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_assigned_task(
        self,
        assigner_id: str,
        assignee_id: str,
        title: str,
        *,
        description: str | None = None,
        start_date: date | None = None,
        due_date: date | None = None,
        causation_key: str | None = None,
        checklist: list[str] | None = None,
        parent_task_id: UUID | None = None,
    ) -> tuple[TaskRecord, TaskAssignmentRecord]:
        if causation_key:
            existing = self._session.scalar(select(TaskRecord).where(TaskRecord.causation_key == causation_key))
            if existing is not None:
                return existing, existing.assignments[-1]
        now = datetime.now(UTC)
        task = TaskRecord(
            created_by_actor_id=assigner_id,
            title=title,
            state=TaskState.OPEN,
            block_reason=None,
            parent_task_id=parent_task_id,
            description=description,
            start_date=start_date,
            due_date=due_date,
            organization_unit_id=_primary_unit(self._session, assignee_id),
            origin_kind="assignment",
            version=1,
            created_at=now,
            updated_at=now,
            causation_key=causation_key,
        )
        self._session.add(task)
        self._session.flush()
        subject = SubjectRecord(subject_type="task", owning_resource_type="task", owning_resource_id=str(task.id), created_at=now)
        self._session.add(subject)
        self._session.flush()
        snapshot = {
            "title": title,
            "description": description,
            "start_date": start_date.isoformat() if start_date else None,
            "due_date": due_date.isoformat() if due_date else None,
            "assignee_id": assignee_id,
            "assigned_by": assigner_id,
        }
        version = SubjectVersionRecord(subject_id=subject.id, version=1, content_hash=_content_hash(snapshot), snapshot=snapshot, captured_at=now)
        self._session.add(version)
        assignment = TaskAssignmentRecord(
            task_id=task.id, assignee_id=assignee_id, assigned_by=assigner_id, assignment_kind="direct", status="pending", created_at=now
        )
        self._session.add(assignment)
        self._session.flush()
        tasks = SqlAlchemyTaskRepository(self._session)
        tasks.seed_checklist(task, checklist, assigner_id)
        tasks.capture_version(task, assigner_id, "task.created")
        item = DecisionItemRecord(
            kind="task.assignment.acceptance",
            subject_id=subject.id,
            context_type="task",
            context_id=str(task.id),
            effect_identity=f"task_assignment.activate:{assignment.id}",
            status="open",
            due_at=datetime.combine(due_date, datetime.min.time(), tzinfo=UTC) if due_date else None,
            created_at=now,
        )
        self._session.add(item)
        self._session.flush()
        assignment.source_decision_item_id = item.id
        submission = SubmissionRecord(
            decision_item_id=item.id,
            subject_version_id=version.id,
            submission_version=1,
            submitted_by=assigner_id,
            payload_hash=version.content_hash,
            decision_policy_snapshot={"decisions": ["accept", "reject"], "reason_required_for": ["reject"]},
            submitted_at=now,
        )
        self._session.add(submission)
        self._session.flush()
        self._session.add(ReviewAssignmentRecord(submission_id=submission.id, reviewer_member_id=assignee_id, status="pending", assigned_at=now, due_at=item.due_at))
        self._session.add(TaskActivityRecord(task_id=task.id, task_version=task.version, state=task.state, occurred_at=now))
        ActivityLedger(self._session).record(
            target_type="task", target_id=str(task.id), event_kind="task.assigned", actor_id=assigner_id,
            after_ref=f"task_assignment:{assignment.id}",
            safe_summary=f"{_person(self._session, assigner_id)}가 담당자를 {_person(self._session, assignee_id)}로 지정함: {title}",
        )
        self._session.flush()
        self._session.refresh(task)
        return task, assignment

    def assignment(self, assignment_id: UUID, *, lock: bool = False) -> TaskAssignmentRecord | None:
        statement = select(TaskAssignmentRecord).where(TaskAssignmentRecord.id == assignment_id)
        return self._session.scalar(statement.with_for_update().execution_options(populate_existing=True) if lock else statement)

    def task_for(self, assignment: TaskAssignmentRecord) -> TaskRecord:
        task = self._session.get(TaskRecord, assignment.task_id)
        assert task is not None
        return task

    def pending_for(self, assignee_id: str) -> list[tuple[TaskAssignmentRecord, TaskRecord]]:
        rows = self._session.execute(
            select(TaskAssignmentRecord, TaskRecord)
            .join(TaskRecord, TaskRecord.id == TaskAssignmentRecord.task_id)
            .where(TaskAssignmentRecord.assignee_id == assignee_id, TaskAssignmentRecord.status == "pending")
            .order_by(TaskAssignmentRecord.created_at)
        ).all()
        return [(assignment, task) for assignment, task in rows]

    def assigned_by(self, assigner_id: str) -> list[tuple[TaskAssignmentRecord, TaskRecord]]:
        rows = self._session.execute(
            select(TaskAssignmentRecord, TaskRecord)
            .join(TaskRecord, TaskRecord.id == TaskAssignmentRecord.task_id)
            .where(TaskAssignmentRecord.assigned_by == assigner_id, TaskAssignmentRecord.assignment_kind == "direct")
            .order_by(TaskAssignmentRecord.created_at.desc())
        ).all()
        return [(assignment, task) for assignment, task in rows]

    def task_by_id(self, task_id: UUID, *, lock: bool = False) -> TaskRecord | None:
        """The Task itself, with no holder scope. Callers decide separately who may act on it.

        `lock` takes the Task row before anything is read off it, so two commands racing for the same Task cannot
        both pass a version check that was true only before the other one committed.
        """
        return self._session.get(TaskRecord, task_id, with_for_update=lock or None)

    def active_assignment_for(self, task_id: UUID, *, lock: bool = False) -> TaskAssignmentRecord | None:
        """Who holds this Task right now. At most one assignment is ever open on it."""
        statement = select(TaskAssignmentRecord).where(
            TaskAssignmentRecord.task_id == task_id, TaskAssignmentRecord.status.in_(("active", "pending"))
        )
        return self._session.scalar(
            statement.with_for_update().execution_options(populate_existing=True) if lock else statement
        )

    def reassign(self, task: TaskRecord, current: TaskAssignmentRecord, assigner_id: str, assignee_id: str, reason: str | None) -> TaskAssignmentRecord:
        """Move the work to someone else: the assignment that was open is closed and a new one is appended.

        Nothing is written over. The row that was there keeps saying who held it and until when, and the new row says
        who put this person on it and which assignment it replaced.
        """
        now = datetime.now(UTC)
        previous_assignee = current.assignee_id
        current.status = "superseded"
        current.superseded_at = now
        appended = TaskAssignmentRecord(
            task_id=task.id,
            assignee_id=assignee_id,
            assigned_by=assigner_id,
            assignment_kind="direct",
            status="pending",
            supersedes_assignment_id=current.id,
            created_at=now,
        )
        self._session.add(appended)
        # Flush before the ledger line so the appended assignment has an id to point at.
        self._session.flush()
        task.version += 1
        task.updated_at = now
        self._session.add(TaskActivityRecord(task_id=task.id, task_version=task.version, state=task.state, occurred_at=now))
        ActivityLedger(self._session).record(
            target_type="task", target_id=str(task.id), event_kind="task.reassigned", actor_id=assigner_id,
            before_ref=f"task_assignment:{current.id}", after_ref=f"task_assignment:{appended.id}", reason=reason,
            safe_summary=(
                f"{_person(self._session, assigner_id)}가 담당자를 {_person(self._session, previous_assignee)}에서 "
                f"{_person(self._session, assignee_id)}로 바꿈: {task.title}"
            ),
        )
        SqlAlchemyTaskRepository(self._session).capture_version(task, assigner_id, "task.reassigned", reason)
        self._session.flush()
        return appended

    def decide(self, assignment: TaskAssignmentRecord, actor_id: str, decision: str, *, reason: str | None = None) -> ReviewDecisionRecord:
        """Acceptance activates the assignment; rejection closes it and cancels the never-entered Task."""
        now = datetime.now(UTC)
        task = self.task_for(assignment)
        item = self._session.get(DecisionItemRecord, assignment.source_decision_item_id) if assignment.source_decision_item_id else None
        submission = (
            self._session.scalar(select(SubmissionRecord).where(SubmissionRecord.decision_item_id == item.id).order_by(SubmissionRecord.submission_version.desc()))
            if item
            else None
        )
        review = (
            self._session.scalar(
                select(ReviewAssignmentRecord)
                .where(ReviewAssignmentRecord.submission_id == submission.id, ReviewAssignmentRecord.status == "pending")
                .order_by(ReviewAssignmentRecord.assigned_at.desc())
            )
            if submission
            else None
        )
        assert item is not None and submission is not None and review is not None, "assignment acceptance item is missing"
        review.status = "decided"
        record = ReviewDecisionRecord(
            review_assignment_id=review.id, submission_id=submission.id, actor_member_id=actor_id, decision=decision, reason=reason, decided_at=now
        )
        self._session.add(record)
        item.status = "resolved"
        item.resolved_at = now
        self._session.flush()
        assignment.source_review_decision_id = record.id
        if decision == "accept":
            assignment.status = "active"
            assignment.accepted_at = now
            task.source_decision_item_id = item.id
            task.source_review_decision_id = record.id
            summary = f"배정 수락: {task.title}"
        else:
            assignment.status = "declined"
            assignment.declined_at = now
            assignment.decline_reason = reason
            task.state = TaskState.CANCELLED
            task.version += 1
            task.updated_at = now
            self._session.add(TaskActivityRecord(task_id=task.id, task_version=task.version, state=task.state, occurred_at=now))
            summary = f"배정 거절: {task.title}"
        ActivityLedger(self._session).record(
            target_type="task", target_id=str(task.id), event_kind=f"task.assignment_{decision}ed", actor_id=actor_id,
            before_ref=f"task_assignment:{assignment.id}", after_ref=f"review_decision:{record.id}", reason=reason, safe_summary=summary,
        )
        self._session.flush()
        self._session.refresh(task)
        return record


class SqlAlchemyAttachmentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_file(self, *, storage_key: str, name: str, content_type: str, size_bytes: int, integrity_ref: str, provenance: str, uploaded_by: str) -> AttachmentRecord:
        record = AttachmentRecord(
            source_kind="file",
            source_ref=storage_key,
            name=name,
            content_type=content_type,
            size_bytes=size_bytes,
            provenance=provenance,
            integrity_ref=integrity_ref,
            uploaded_by=uploaded_by,
            created_at=datetime.now(UTC),
        )
        self._session.add(record)
        self._session.flush()
        return record

    def add_link(self, *, url: str, name: str, provenance: str, uploaded_by: str) -> AttachmentRecord:
        """ERD ATTACHMENT with `source_kind=external_link`: the URL is the artifact, and SCAX holds none of its bytes.

        One URL is one artifact identity, so the same link used on two Tasks is two bindings on one Attachment. The
        integrity ref records only when it was observed — never a content hash, because nothing was read.
        """
        existing = self._session.scalar(select(AttachmentRecord).where(AttachmentRecord.source_ref == url))
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        record = AttachmentRecord(
            source_kind="external_link",
            source_ref=url,
            name=name,
            content_type="text/uri-list",
            size_bytes=0,
            provenance=provenance,
            integrity_ref=f"observed:{now.isoformat()}",
            uploaded_by=uploaded_by,
            created_at=now,
        )
        self._session.add(record)
        self._session.flush()
        return record

    def add_reference(self, *, resource_type: str, resource_id: str, name: str, provenance: str, uploaded_by: str) -> AttachmentRecord:
        """ERD ATTACHMENT with `source_kind=resource_ref`: another thing inside SCAX, named by what it is.

        The stored name is a fallback only. Every read resolves the reference through the owning module's own
        authorization, so a reader who may not open it is never handed its title.
        """
        source_ref = f"{resource_type}:{resource_id}"
        existing = self._session.scalar(select(AttachmentRecord).where(AttachmentRecord.source_ref == source_ref))
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        record = AttachmentRecord(
            source_kind="resource_ref",
            source_ref=source_ref,
            name=name,
            content_type="application/vnd.scax.resource-ref",
            size_bytes=0,
            provenance=provenance,
            integrity_ref=f"observed:{now.isoformat()}",
            uploaded_by=uploaded_by,
            created_at=now,
        )
        self._session.add(record)
        self._session.flush()
        return record

    def bind(self, *, attachment_id: UUID, context_type: str, context_id: str, role: str, bound_by: str) -> AttachmentBindingRecord:
        binding = AttachmentBindingRecord(
            attachment_id=attachment_id, context_type=context_type, context_id=context_id, role=role, bound_by=bound_by, bound_at=datetime.now(UTC)
        )
        self._session.add(binding)
        self._session.flush()
        return binding

    def bindings_for(self, context_type: str, context_id: str) -> list[tuple[AttachmentBindingRecord, AttachmentRecord]]:
        rows = self._session.execute(
            select(AttachmentBindingRecord, AttachmentRecord)
            .join(AttachmentRecord, AttachmentRecord.id == AttachmentBindingRecord.attachment_id)
            .where(AttachmentBindingRecord.context_type == context_type, AttachmentBindingRecord.context_id == context_id)
            .order_by(AttachmentBindingRecord.bound_at)
        ).all()
        return [(binding, attachment) for binding, attachment in rows]

    def binding(self, context_type: str, context_id: str, binding_id: UUID) -> tuple[AttachmentBindingRecord, AttachmentRecord] | None:
        row = self._session.execute(
            select(AttachmentBindingRecord, AttachmentRecord)
            .join(AttachmentRecord, AttachmentRecord.id == AttachmentBindingRecord.attachment_id)
            .where(
                AttachmentBindingRecord.id == binding_id,
                AttachmentBindingRecord.context_type == context_type,
                AttachmentBindingRecord.context_id == context_id,
            )
        ).first()
        return (row[0], row[1]) if row else None

    def unbind(self, binding: AttachmentBindingRecord) -> None:
        binding.unbound_at = datetime.now(UTC)

    def attachment(self, attachment_id: UUID) -> AttachmentRecord | None:
        return self._session.get(AttachmentRecord, attachment_id)

    def bindings_for_many(self, context_type: str, context_ids: list[str]) -> list[tuple[AttachmentBindingRecord, AttachmentRecord]]:
        if not context_ids:
            return []
        rows = self._session.execute(
            select(AttachmentBindingRecord, AttachmentRecord)
            .join(AttachmentRecord, AttachmentRecord.id == AttachmentBindingRecord.attachment_id)
            .where(AttachmentBindingRecord.context_type == context_type, AttachmentBindingRecord.context_id.in_(context_ids), AttachmentBindingRecord.unbound_at.is_(None))
            .order_by(AttachmentBindingRecord.bound_at)
        ).all()
        return [(binding, attachment) for binding, attachment in rows]


class SqlAlchemyCommentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, request_thread_id: UUID, author_id: str, body: str, *, comment_id: UUID | None = None) -> CommentRecord:
        record = CommentRecord(request_thread_id=request_thread_id, author_member_id=author_id, body=body, created_at=datetime.now(UTC))
        if comment_id is not None:
            record.id = comment_id
        self._session.add(record)
        self._session.flush()
        return record

    def lock_thread(self, request_thread_id: UUID) -> None:
        """Serialize same-thread comment writes so two simultaneous posts of one key cannot both insert."""
        self._session.execute(select(RequestThreadRecord.id).where(RequestThreadRecord.id == request_thread_id).with_for_update())

    def list_for(self, request_thread_id: UUID) -> list[CommentRecord]:
        return list(
            self._session.scalars(select(CommentRecord).where(CommentRecord.request_thread_id == request_thread_id).order_by(CommentRecord.created_at))
        )

    def comment(self, request_thread_id: UUID, comment_id: UUID) -> CommentRecord | None:
        return self._session.scalar(select(CommentRecord).where(CommentRecord.id == comment_id, CommentRecord.request_thread_id == request_thread_id))
