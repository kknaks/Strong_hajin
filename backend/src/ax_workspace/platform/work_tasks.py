from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ax_workspace.modules.work.application import TaskNotFound, TaskState
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
    TaskAssignmentRecord,
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


def _content_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


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
    ) -> TaskRecord:
        if causation_key:
            existing = self.session.scalar(
                select(TaskRecord).where(TaskRecord.causation_key == causation_key)
            )
            if existing is not None:
                return existing
        now = datetime.now(UTC)
        task = TaskRecord(
            owner_id=owner_id,
            title=title,
            state=TaskState.OPEN,
            block_reason=None,
            description=description,
            start_date=start_date,
            due_date=due_date,
            organization_unit_id=_primary_unit(self.session, owner_id),
            origin_kind="direct",
            version=1,
            created_at=now,
            updated_at=now,
            causation_key=causation_key,
        )
        self.session.add(task)
        self.session.flush()
        self.session.add(
            TaskAssignmentRecord(
                task_id=task.id, assignee_id=owner_id, assigned_by=owner_id, assignment_kind="self", status="active", created_at=now, accepted_at=now
            )
        )
        self.session.add(TaskActivityRecord(task_id=task.id, task_version=task.version, state=task.state, occurred_at=now))
        ActivityLedger(self.session).record(
            target_type="task", target_id=str(task.id), event_kind="task.created", actor_id=owner_id,
            after_ref=f"task:{task.id}@1", safe_summary=f"업무 생성: {title}",
        )
        return task

    def record_activity(self, task: TaskRecord, actor_id: str, event_kind: str, summary: str, *, before_ref: str | None = None, reason: str | None = None) -> None:
        ActivityLedger(self.session).record(
            target_type="task", target_id=str(task.id), event_kind=event_kind, actor_id=actor_id,
            before_ref=before_ref, after_ref=f"task:{task.id}@{task.version}", reason=reason, safe_summary=summary,
            request_thread_id=task.request_thread_id,
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

    @staticmethod
    def _held_by(owner_id: str):
        return (
            select(TaskRecord)
            .join(TaskAssignmentRecord, TaskAssignmentRecord.task_id == TaskRecord.id)
            .where(TaskRecord.owner_id == owner_id, TaskAssignmentRecord.assignee_id == owner_id, TaskAssignmentRecord.status == "active")
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
            .where(TaskRecord.owner_id == str(principal.id))
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
            created_at=now,
            updated_at=now,
            causation_key=causation_key,
        )
        self._session.add(request)
        self._session.flush()
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
            after_ref=f"work_request:{request.id}@1", safe_summary=f"업무 요청 생성: {title}", request_thread_id=thread.id,
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
    ) -> ReviewDecisionRecord | None:
        submission = self.current_submission(request)
        if submission is None:
            return None
        assignment = self.active_assignment(submission)
        if assignment is None:
            return None
        now = datetime.now(UTC)
        assignment.status = "decided"
        record = ReviewDecisionRecord(
            review_assignment_id=assignment.id,
            submission_id=submission.id,
            actor_member_id=actor_id,
            decision=decision,
            reason=reason,
            conditions=conditions,
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
        """A revision is a new SubjectVersion + Submission with a diff; the prior decision stays untouched."""
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
        previous_assignment = self._session.scalar(
            select(ReviewAssignmentRecord).where(ReviewAssignmentRecord.submission_id == previous.id).order_by(ReviewAssignmentRecord.assigned_at.desc())
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
        ActivityLedger(self._session).record(
            target_type="work_request", target_id=str(request.id), event_kind="work_request.resubmitted", actor_id=actor_id,
            before_ref=f"submission:{previous.id}", after_ref=f"submission:{submission.id}",
            safe_summary=f"업무 요청 재상신 v{submission.submission_version}: {request.title}", request_thread_id=request.request_thread_id,
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
                }
                for s in submissions
            ],
            "review_assignments": [
                {"review_assignment_id": str(a.id), "submission_id": str(a.submission_id), "reviewer_member_id": a.reviewer_member_id, "status": a.status, "assigned_at": a.assigned_at.isoformat()}
                for a in assignments
            ],
            "review_decisions": [
                {"review_decision_id": str(d.id), "submission_id": str(d.submission_id), "actor_member_id": d.actor_member_id, "decision": d.decision, "reason": d.reason, "conditions": d.conditions, "decided_at": d.decided_at.isoformat()}
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
            owner_id=request.assignee_id,
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
                assigned_by=request.requester_id,
                assignment_kind="request_effect",
                status="active",
                source_work_request_id=request.id,
                source_decision_item_id=item.id if item else None,
                source_review_decision_id=decision.id if decision else None,
                created_at=now,
                accepted_at=now,
            )
        )
        return task

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

    def evidence_for(self, request: WorkRequestRecord) -> list[tuple[EvidenceRecord, AttachmentRecord, SubmissionRecord]]:
        item = self.open_decision_item(request)
        if item is None:
            return []
        rows = self._session.execute(
            select(EvidenceRecord, AttachmentRecord, SubmissionRecord)
            .join(AttachmentRecord, AttachmentRecord.id == EvidenceRecord.attachment_id)
            .join(SubmissionRecord, SubmissionRecord.id == EvidenceRecord.submission_id)
            .where(SubmissionRecord.decision_item_id == item.id)
            .order_by(EvidenceRecord.adopted_at)
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
    ) -> tuple[TaskRecord, TaskAssignmentRecord]:
        if causation_key:
            existing = self._session.scalar(select(TaskRecord).where(TaskRecord.causation_key == causation_key))
            if existing is not None:
                return existing, existing.assignments[-1]
        now = datetime.now(UTC)
        task = TaskRecord(
            owner_id=assignee_id,
            title=title,
            state=TaskState.OPEN,
            block_reason=None,
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
            after_ref=f"task_assignment:{assignment.id}", safe_summary=f"업무 배정: {title} → {assignee_id}",
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
