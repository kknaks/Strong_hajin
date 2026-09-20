from __future__ import annotations


from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo
from uuid import UUID

from sqlalchemy import Integer, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ax_workspace.modules.work.application import TaskNotFound, TaskState
from ax_workspace.modules.work.request_errors import WorkRequestError
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
from ax_workspace.platform.notifications import SqlAlchemyNotificationRepository
from ax_workspace.platform.persistence import (
    ActivityEventRecord,
    AttachmentBindingRecord,
    AttachmentRecord,
    CommentRecord,
    DecisionItemRecord,
    MeetingRecord,
    MembershipRecord,
    RequestThreadRecord,
    ReviewAssignmentRecord,
    ReviewDecisionRecord,
    SubjectRecord,
    SubjectVersionRecord,
    SubmissionRecord,
    TaskActivityRecord,
    TaskCreationAttemptRecord,
    TaskPredecessorRecord,
    TaskProposalRecord,
    TaskRecord,
    WorkRequestAuditEventRecord,
    WorkRequestListEntryRecord,
    WorkRequestReadReceiptRecord,
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
from ax_workspace.platform.native_materials import TITLELESS_MEETING as UNTITLED_MEETING
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


def _meeting_title(session: Session, meeting_id: UUID | None) -> str:
    """진행 기록이 부르는 회의 이름. 제목이 아직 없으면 합성이 낸 후보를, 그것도 없으면 「제목 없는 회의」."""
    if meeting_id is None:
        return UNTITLED_MEETING
    meeting = session.get(MeetingRecord, meeting_id)
    if meeting is None:
        return UNTITLED_MEETING
    return (meeting.title or meeting.title_candidate or "").strip() or UNTITLED_MEETING


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


def _clean_locator(value: Any) -> dict[str, Any] | None:
    """도구가 말해 준 자리만 남긴다 — 쪽·절·구간처럼 그 자료가 스스로 부르는 이름.

    원문이나 발췌는 여기 오지 않는다. 근거는 자리를 가리키는 것이지 내용을 옮겨 적는 것이 아니며, 옮겨 적으면
    권한이 회수된 뒤에도 그 글이 남는다. 알아듣지 못하는 모양은 통째로 버리고 없는 것으로 둔다.
    """
    if not isinstance(value, dict):
        return None
    numeric = {"section", "page", "start", "end", "index", "slide", "row_start", "row_end", "column_start", "column_end", "char_start", "char_end", "start_ms", "end_ms", "segment_sequence", "submission_version", "note_version"}
    allowed = (*sorted(numeric), "anchor", "sheet", "cell", "kind", "cell_range", "container", "variant", "char_offset_basis", "source_revision_id", "recording_id", "segment_id", "raw_start_segment_id", "raw_end_segment_id", "report_id", "meeting_id", "note_id")
    cleaned = {
        key: (int(value[key]) if key in numeric and str(value[key]).lstrip("-").isdigit() else str(value[key])[:120])
        for key in allowed
        if value.get(key) not in (None, "")
    }
    return cleaned or None


class SqlAlchemyGraphReceiptRepository:
    """Where a delegated turn actually walked, in the order it walked. Only observed steps are written."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(self, execution_id: UUID, principal_id: str, steps: list[dict[str, Any]]) -> int:
        from ax_workspace.platform.persistence import ConversationGraphReceiptRecord, ConversationRecord, ConversationTurnRecord

        # A provider may fan out several graph tools for one Turn. Serialize their receipt appends on the owning
        # Turn row before reading max(sequence), otherwise concurrent transactions can choose the same next value.
        turn = self._session.scalar(
            select(ConversationTurnRecord)
            .where(ConversationTurnRecord.execution_id == execution_id)
            .with_for_update()
        )
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
                    source_contexts=step.get("source_contexts"),
                    integrity_ref=step.get("integrity_ref"),
                    observed_at=now,
                )
            )
            written += 1
        self._session.flush()
        return written

    def record_resources(self, execution_id: UUID, principal_id: str, references: list[dict[str, Any]]) -> int:
        """What this turn read and named. Recording the same thing twice in one turn is one reference, not two."""
        from ax_workspace.platform.persistence import (
            ConversationAnswerResourceRecord,
            ConversationRecord,
            ConversationTurnRecord,
        )

        turn = self._session.scalar(select(ConversationTurnRecord).where(ConversationTurnRecord.execution_id == execution_id).with_for_update())
        if turn is None:
            raise ValueError("delegated conversation execution was not found")
        conversation = self._session.get(ConversationRecord, turn.conversation_id)
        if conversation is None or str(conversation.owner_id) != principal_id:  # fail closed
            raise ValueError("delegated conversation belongs to another principal")
        existing = {
            (row.resource_type, row.resource_id): row
            for row in self._session.scalars(
                select(ConversationAnswerResourceRecord).where(ConversationAnswerResourceRecord.turn_id == turn.id)
            )
        }
        highest = self._session.scalar(
            select(func.max(ConversationAnswerResourceRecord.sequence)).where(
                ConversationAnswerResourceRecord.turn_id == turn.id
            )
        )
        now = datetime.now(UTC)
        written = 0
        for reference in references:
            key = (str(reference["resource_type"]), str(reference["resource_id"]))
            if key in existing:
                record = existing[key]
                if key[0] == "material" and record.integrity_ref == reference.get("integrity_ref"):
                    contexts = {(row["resource_type"], row["resource_id"], row["binding_id"]): row for row in record.source_contexts or []}
                    contexts.update({(row["resource_type"], row["resource_id"], row["binding_id"]): row for row in reference.get("source_contexts") or []})
                    record.source_contexts = list(contexts.values())
                    if reference.get("source_locator"):
                        record.source_locator = _clean_locator(reference["source_locator"])
                continue
            written += 1
            record = ConversationAnswerResourceRecord(
                    turn_id=turn.id,
                    conversation_id=turn.conversation_id,
                    execution_id=execution_id,
                    sequence=int(highest or 0) + written,
                    resource_type=key[0],
                    resource_id=key[1],
                    resource_version=reference.get("resource_version"),
                    source_contexts=reference.get("source_contexts"),
                    integrity_ref=reference.get("integrity_ref"),
                    source_locator=_clean_locator(reference.get("source_locator")),
                    observed_at=now,
                )
            self._session.add(record)
            existing[key] = record
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


class SqlAlchemyTaskCreationLedger:
    """(행위자 · 명령 종류 · 키) 하나에 생성 결과 하나 — 업무·담당과 **같은 transaction** 에 선다.

    동시 실행에서 둘째는 commit 의 unique 위반으로 막히고, 조립 층이 그 transaction 을 통째로 버린 뒤
    새 transaction 에서 이긴 쪽의 결과를 영수증으로 읽는다 — 영수증이 빈손으로 돌아가지 않는다.
    위반을 SAVEPOINT 로 받지 않는 이유는 `claim` 의 주석에 있다.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def find(self, actor_id: str, command_kind: str, request_key: str) -> TaskCreationAttemptRecord | None:
        return self._session.scalar(
            select(TaskCreationAttemptRecord).where(
                TaskCreationAttemptRecord.actor_id == actor_id,
                TaskCreationAttemptRecord.command_kind == command_kind,
                TaskCreationAttemptRecord.request_key == request_key,
            )
        )

    def claim(
        self, actor_id: str, command_kind: str, request_key: str, payload_fingerprint: str
    ) -> TaskCreationAttemptRecord | None:
        """이 의도를 내가 세운다고 원장에 먼저 적는다. **이미 보이는** 행이 있으면 `None`.

        동시 실행에서 두 transaction 이 여기를 함께 지나갈 수 있다 — 아직 commit 되지 않은 남의 행은
        보이지 않기 때문이다. 그때는 unique 제약이 commit 에서 둘째를 거절하고, 조립 층이 그 transaction 을
        통째로 버린 뒤 이긴 쪽의 결과를 영수증으로 다시 읽는다. **SAVEPOINT 로 받지 않는다** — pysqlite 에서
        savepoint 가 바깥 transaction 과 함께 되돌아가지 않아, 실패한 생성의 원장 한 줄이 남는다.
        """
        if self.find(actor_id, command_kind, request_key) is not None:
            return None
        attempt = TaskCreationAttemptRecord(
            actor_id=actor_id,
            command_kind=command_kind,
            request_key=request_key,
            payload_fingerprint=payload_fingerprint,
            created_at=datetime.now(UTC),
        )
        self._session.add(attempt)
        return attempt

    def bind(self, attempt: TaskCreationAttemptRecord, *, task_id: UUID | None = None, work_request_id: UUID | None = None) -> None:
        if task_id is not None:
            attempt.task_id = task_id
        if work_request_id is not None:
            attempt.work_request_id = work_request_id
        self._session.flush()


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
        source_decision_item_id: UUID | None = None,
        source_submission_id: UUID | None = None,
        source_review_decision_id: UUID | None = None,
        checklist: list[str] | None = None,
        references: list[UUID] | None = None,
        parent_task_id: UUID | None = None,
        project_id: UUID | None = None,
        cc_member_ids: list[str] | None = None,
        approver_id: str | None = None,
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
            project_id=project_id,
            description=description,
            start_date=start_date,
            due_date=due_date,
            organization_unit_id=_primary_unit(self.session, owner_id),
            # **WORK-001 이 만든 열에 이제 값이 들어온다** — `업무` 갈래만이다 (SPEC-001 §7 OQ-M).
            approver_id=approver_id,
            origin_kind="direct",
            version=1,
            created_at=now,
            updated_at=now,
            causation_key=causation_key,
            source_action_item_id=source_action_item_id,
            source_decision_item_id=source_decision_item_id,
            source_submission_id=source_submission_id,
            source_review_decision_id=source_review_decision_id,
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
        self.set_cc_members(task.id, cc_member_ids, created_at=now)
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

    def approval_rounds_for(self, task_ids: list[UUID]) -> dict[UUID, dict[str, Any]]:
        """여러 업무의 **완료 확인 회차**를 한 번에 — 목록 한 줄마다 따로 묻지 않기 위해서다.

        내는 것은 **원장의 사실**뿐이다: 마지막 회차의 번호와 그 회차에 달린 판단들. 「승인됐는가」의
        판정은 업무 모듈이 한다 (`_approval_state`) — 판정이 두 곳에 있으면 조용히 갈린다.
        """
        if not task_ids:
            return {}
        wanted = {str(task_id) for task_id in task_ids}
        items = list(
            self.session.scalars(
                select(DecisionItemRecord).where(
                    DecisionItemRecord.kind == self.DELIVERY_KIND,
                    DecisionItemRecord.context_type == "task",
                    DecisionItemRecord.context_id.in_(wanted),
                )
            )
        )
        if not items:
            return {}
        by_item = {item.id: UUID(str(item.context_id)) for item in items}
        submissions: dict[UUID, list[SubmissionRecord]] = {}
        for row in self.session.scalars(
            select(SubmissionRecord)
            .where(SubmissionRecord.decision_item_id.in_(list(by_item)))
            .order_by(SubmissionRecord.submission_version)
        ):
            submissions.setdefault(by_item[row.decision_item_id], []).append(row)
        latest = {task_id: rows[-1] for task_id, rows in submissions.items() if rows}
        decisions: dict[UUID, list[ReviewDecisionRecord]] = {}
        if latest:
            by_submission = {row.id: task_id for task_id, row in latest.items()}
            for row in self.session.scalars(
                select(ReviewDecisionRecord)
                .where(ReviewDecisionRecord.submission_id.in_(list(by_submission)))
                .order_by(ReviewDecisionRecord.decided_at)
            ):
                decisions.setdefault(by_submission[row.submission_id], []).append(row)
        return {
            task_id: {
                "rounds": len(submissions.get(task_id, ())),
                "decisions": tuple(
                    (row.decision, row.decided_at) for row in decisions.get(task_id, ())
                ),
            }
            for task_id in submissions
        }

    # ---- 제안: 수락 뒤에 조건을 바꾸거나 일을 접자는 말 (SPEC-003 §4 제안–동의) ----

    def pending_proposal_kinds(self, task_ids: list[UUID]) -> dict[UUID, str]:
        """업무마다 **응답을 기다리는** 제안의 종류. 한 업무에 같은 종류의 대기 제안은 하나다."""
        if not task_ids:
            return {}
        rows = self.session.scalars(
            select(TaskProposalRecord)
            .where(TaskProposalRecord.task_id.in_(task_ids), TaskProposalRecord.state == "pending")
            .order_by(TaskProposalRecord.created_at)
        )
        return {row.task_id: f"{row.kind}_pending" for row in rows}

    def proposals_for(self, task_id: UUID) -> list[TaskProposalRecord]:
        """대기 중인 것과 지난 것 전부 — 오래된 것부터. 응답한 제안도 지우지 않는다."""
        return list(
            self.session.scalars(
                select(TaskProposalRecord)
                .where(TaskProposalRecord.task_id == task_id)
                .order_by(TaskProposalRecord.created_at, TaskProposalRecord.id)
            )
        )

    def proposal(self, task_id: UUID, proposal_id: UUID, *, lock: bool = False) -> TaskProposalRecord | None:
        statement = select(TaskProposalRecord).where(
            TaskProposalRecord.id == proposal_id, TaskProposalRecord.task_id == task_id
        )
        return self.session.scalar(
            statement.with_for_update().execution_options(populate_existing=True) if lock else statement
        )

    def open_proposal(
        self, task: TaskRecord, kind: str, proposed_by: str, payload: dict[str, Any] | None, reason: str | None
    ) -> TaskProposalRecord:
        """제안을 연다. **제안만으로는 아무것도 바뀌지 않는다** — Task 의 상태도 조건도 그대로다.

        `task_version` 은 제안이 선 시점의 회차이고, 응답이 그 값을 다시 확인한다.
        """
        now = datetime.now(UTC)
        record = TaskProposalRecord(
            task_id=task.id,
            kind=kind,
            state="pending",
            proposed_by=proposed_by,
            payload=payload,
            reason=reason,
            task_version=int(task.version),
            created_at=now,
        )
        self.session.add(record)
        self.session.flush()
        ActivityLedger(self.session).record(
            target_type="task", target_id=str(task.id), event_kind=f"task.proposal_{kind}_opened", actor_id=proposed_by,
            after_ref=f"task_proposal:{record.id}", reason=reason,
            safe_summary=(
                f"취소 제안: {task.title}" if kind == "cancellation" else f"조건 변경 제안: {task.title}"
            ),
        )
        return record

    def settle_proposal(self, proposal: TaskProposalRecord, responder_id: str, state: str) -> None:
        """답이 왔다 — `agreed` · `declined` · `withdrawn`. 행은 남고 상태만 닫힌다."""
        now = datetime.now(UTC)
        proposal.state = state
        proposal.responder_id = responder_id
        proposal.responded_at = now
        task = self.session.get(TaskRecord, proposal.task_id)
        self.session.flush()
        if task is not None:
            ActivityLedger(self.session).record(
                target_type="task", target_id=str(task.id), event_kind=f"task.proposal_{state}", actor_id=responder_id,
                before_ref=f"task_proposal:{proposal.id}",
                safe_summary=f"{_person(self.session, responder_id)}가 제안에 답함({state}): {task.title}",
            )

    def children_map(self, task_ids: list[UUID]) -> dict[UUID, list[TaskRecord]]:
        """여러 업무의 **직속 하위**를 한 번에. 목록이 하위 진행을 세려면 줄마다 질의할 수 없다."""
        if not task_ids:
            return {}
        rows: dict[UUID, list[TaskRecord]] = {}
        for child in self.session.scalars(
            select(TaskRecord)
            .where(TaskRecord.parent_task_id.in_(task_ids))
            .order_by(TaskRecord.created_at, TaskRecord.id)
        ):
            rows.setdefault(child.parent_task_id, []).append(child)
        return rows

    def tasks_requested_by(self, requester_id: str, *, include_closed: bool = False) -> list[TaskRecord]:
        """**내가 요청한 업무** — 읽기의 세 번째 길이 시작하는 곳이다 (정책 V-21).

        승격 요청이면 **누른 사람**도 요청자 자리에 선다 (BASE-002 O-31) — 요청자 전용 조작이 이미
        그렇게 판정하므로 읽기도 같은 모양이어야 한다. 그렇지 않으면 회의에서 올린 사람이 자기가 올린
        일을 못 읽는다.
        """
        statement = (
            select(TaskRecord)
            .join(WorkRequestRecord, WorkRequestRecord.id == TaskRecord.source_work_request_id)
            .where(
                or_(
                    WorkRequestRecord.requester_id == requester_id,
                    WorkRequestRecord.promoted_by_member_id == requester_id,
                )
            )
        )
        if not include_closed:
            statement = statement.where(TaskRecord.state.not_in([TaskState.DONE, TaskState.CANCELLED]))
        return list(self.session.scalars(statement.order_by(TaskRecord.created_at)))

    def descendant_ids_of(self, task_ids: list[UUID]) -> set[UUID]:
        """그 업무들 **아래 전부** — 깊이 제한이 없다 (정책 V-21: 「필요하면 그 업무로 들어가 확인한다」).

        한 켜씩 내려가며 **본 것을 다시 보지 않는다**: 원장이 어긋나 고리가 생겨 있어도 여기서 멈춘다.
        """
        seen: set[UUID] = set()
        frontier = [task_id for task_id in task_ids if task_id is not None]
        while frontier:
            rows = list(
                self.session.scalars(select(TaskRecord.id).where(TaskRecord.parent_task_id.in_(frontier)))
            )
            frontier = [row for row in rows if row not in seen]
            seen.update(frontier)
        return seen

    def tasks_by_ids(self, task_ids: set[UUID], *, include_closed: bool = False) -> list[TaskRecord]:
        if not task_ids:
            return []
        statement = select(TaskRecord).where(TaskRecord.id.in_(list(task_ids)))
        if not include_closed:
            statement = statement.where(TaskRecord.state.not_in([TaskState.DONE, TaskState.CANCELLED]))
        return list(self.session.scalars(statement.order_by(TaskRecord.created_at)))

    def open_children_of(self, task_id: UUID) -> list[TaskRecord]:
        """아직 상위를 붙들고 있는 하위 — **`state` 만으로 세는 옛 판정이다.**

        완결 판정은 업무 모듈의 `is_child_settled()` 가 한다: 요청 하위는 요청자의 승인까지여야 끝난
        것이고, 그 사실은 판단 원장에 있지 이 행에 없다. **상위 완료 검사는 그쪽을 쓴다.**
        이 메서드는 그 판정이 닿지 않는 자리(`state` 만으로 충분한 곳)에만 남는다.
        """
        return [
            task
            for task in self.children_of(task_id)
            if task.state not in {TaskState.DONE, TaskState.CANCELLED, TaskState.COMPLETION_SUBMITTED}
        ]

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

    def task_by_id(self, task_id: UUID, *, lock: bool = False) -> TaskRecord | None:
        """The Task itself, with no holder scope. Callers must decide separately who may see it.

        `lock` 은 회차를 읽기 전에 그 행을 잡는다 — 두 명령이 같은 업무를 두고 경합할 때 둘 다
        「그때는 맞았던」 회차 검사를 통과하지 못하게 한다 (재개 ↔ 상위 완료, 제안 응답 ↔ 취소).
        """
        return self.session.get(TaskRecord, task_id, with_for_update=lock or None, populate_existing=lock)

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
        # **출처**는 첫 담당 행이 말하고(누가 이 일을 있게 했나), **지금 누가 드는가**는 `active` 행이,
        # **누가 답을 기다리나**는 `pending` 행이 말한다. 셋이 다른 질문이라 셋을 각각 담는다 (정책 P-3).
        # 하나로 뭉쳐 두면 담당 변경 대기(= `active` 1 + `pending` 1 공존)에서 어느 쪽이 나올지가 행
        # 순서에 달린다.
        origin_rows: dict[UUID, TaskAssignmentRecord] = {}
        current_rows: dict[UUID, TaskAssignmentRecord] = {}
        pending_rows: dict[UUID, TaskAssignmentRecord] = {}
        for assignment in self.session.scalars(
            select(TaskAssignmentRecord)
            .where(TaskAssignmentRecord.task_id.in_([task.id for task in tasks]))
            .order_by(TaskAssignmentRecord.created_at)
        ).all():
            origin_rows.setdefault(assignment.task_id, assignment)
            if assignment.status == "active":
                current_rows[assignment.task_id] = assignment
            elif assignment.status == "pending":
                pending_rows.setdefault(assignment.task_id, assignment)
        facts: dict[UUID, dict[str, Any]] = {}
        for task in tasks:
            origin = origin_rows.get(task.id)
            current = current_rows.get(task.id)
            waiting = pending_rows.get(task.id)
            request = requests.get(task.source_work_request_id) if task.source_work_request_id else None
            facts[task.id] = {
                "source_work_request_id": task.source_work_request_id,
                "request_requester_id": request.requester_id if request is not None else None,
                "request_title": request.title if request is not None else None,
                "request_state": request.state if request is not None else None,
                "assignment_kind": origin.assignment_kind if origin is not None else None,
                "assigned_by": origin.assigned_by if origin is not None else None,
                # **지금 드는 사람.** 아무도 들지 않으면 `None` 이고, 그것이 수락 대기의 모습이다.
                "assignee_id": current.assignee_id if current is not None else None,
                "current_assignment_id": current.id if current is not None else None,
                "accepted_at": current.accepted_at if current is not None else None,
                # **답을 기다리는 사람.** 요청 수락 대기 · 첫 지정 · 담당 교체 제안이 여기 선다.
                "pending_assignee_id": waiting.assignee_id if waiting is not None else None,
                "pending_assignment_id": waiting.id if waiting is not None else None,
                "pending_assignment_kind": waiting.assignment_kind if waiting is not None else None,
                "pending_supersedes_assignment_id": waiting.supersedes_assignment_id if waiting is not None else None,
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
                "material_id": str(attachment.id),
                "binding_id": str(binding.id),
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

    def tasks_in_projects(self, project_ids: frozenset[str], *, include_closed: bool = False) -> list[TaskRecord]:
        """이 프로젝트들의 업무 전부. 누가 들고 있는지는 묻지 않는다 — 프로젝트가 부서를 가로지르는 이유다."""
        if not project_ids:
            return []
        statement = select(TaskRecord).where(TaskRecord.project_id.in_([UUID(str(item)) for item in project_ids]))
        if not include_closed:
            statement = statement.where(TaskRecord.state.not_in([TaskState.DONE, TaskState.CANCELLED]))
        return list(self.session.scalars(statement.order_by(TaskRecord.created_at)))

    # ---- 선행업무: 상위·참고와 다른 세 번째 관계 ----------------------------------

    def predecessors_for(self, task_ids: list[UUID]) -> dict[UUID, list[UUID]]:
        """여러 업무의 **활성** 선행을 한 번에 — 목록·프로젝트 상세가 줄마다 다시 묻지 않는 자리다.

        뗀 행은 남아 있지만 여기 서지 않는다 (§4 「활성인 것만 실린다」).
        """
        if not task_ids:
            return {}
        found: dict[UUID, list[UUID]] = {}
        rows = self.session.execute(
            select(TaskPredecessorRecord.task_id, TaskPredecessorRecord.predecessor_task_id)
            .where(
                TaskPredecessorRecord.task_id.in_(list(task_ids)),
                TaskPredecessorRecord.released_at.is_(None),
            )
            .order_by(TaskPredecessorRecord.position, TaskPredecessorRecord.created_at)
        ).all()
        for task_id, predecessor_task_id in rows:
            found.setdefault(task_id, []).append(predecessor_task_id)
        return found

    def active_predecessor_ids(self, task_id: UUID) -> list[UUID]:
        return self.predecessors_for([task_id]).get(task_id, [])

    def predecessor_edges(self, task_ids: list[UUID]) -> dict[UUID, list[UUID]]:
        """순환 검사가 걷는 **활성 변**. `predecessors_for` 와 같은 사실이고 이름만 그래프 쪽이다."""
        return self.predecessors_for(task_ids)

    def replace_predecessors(self, task_id: UUID, wanted: list[UUID], actor_id: str) -> None:
        """활성 선행을 이 배열 **그대로** 만든다 — 전체 교체다 (SPEC-001 §4).

        **행을 지우지 않는다.** 빠진 것은 `released_at` 으로 닫고, 새로 들어온 것만 행을 더한다.
        이미 활성인 것은 **건드리지 않는다** — 다시 세우면 「언제부터 선행이었나」가 바뀐다.
        """
        now = datetime.now(UTC)
        keep = list(dict.fromkeys(wanted))
        active = {
            row.predecessor_task_id: row
            for row in self.session.scalars(
                select(TaskPredecessorRecord).where(
                    TaskPredecessorRecord.task_id == task_id,
                    TaskPredecessorRecord.released_at.is_(None),
                )
            )
        }
        for predecessor_task_id, row in active.items():
            if predecessor_task_id not in keep:
                row.released_at = now
                row.released_by = actor_id
        for position, predecessor_task_id in enumerate(keep):
            existing = active.get(predecessor_task_id)
            if existing is not None:
                # 이미 활성인 관계는 **다시 세우지 않는다** — 다시 세우면 「언제부터 선행이었나」가
                # 바뀐다. 순서만 이번에 고른 대로 맞춘다.
                existing.position = position
                continue
            self.session.add(
                TaskPredecessorRecord(
                    task_id=task_id,
                    predecessor_task_id=predecessor_task_id,
                    position=position,
                    created_by=actor_id,
                    created_at=now,
                )
            )
        self.session.flush()

    # ---- 참조자(cc): 읽기와 논의만 여는 한 겹 관계 --------------------------------

    def set_cc_members(self, task_id: UUID, member_ids: list[str] | None, *, created_at: datetime) -> None:
        """이 업무의 참조자를 세운다 — 요청이 쓰는 것과 **같은 표**다 (`resource_relationships`).

        따로 표를 파지 않는 이유는 하나다: 요청의 cc 가 이미 여기 살고, 업무 cc 를 다른 곳에 두면
        「참조로 받았다」가 두 모양으로 갈린다. `resource_type` 만 다르다.

        실제로 있는 구성원만 쓴다 — 없는 id 로 관계 행을 만들면 원장이 가리킬 곳 없는 자리를 갖는다.
        (「활동 중인 구성원인가」는 application 이 먼저 묻는다; 여기는 저장의 마지막 방어선이다.)
        """
        for member_id in dict.fromkeys(member_ids or []):
            if self.session.get(MemberRecord, member_id) is None:
                continue
            self.session.add(
                ResourceRelationshipRecord(
                    member_id=member_id,
                    resource_type="task",
                    resource_id=str(task_id),
                    relationship_kind="cc",
                    valid_from=created_at,
                )
            )

    def cc_member_ids(self, task_id: UUID) -> list[str]:
        return self.cc_members_for([task_id]).get(task_id, [])

    def cc_members_for(self, task_ids: list[UUID]) -> dict[UUID, list[str]]:
        """여러 업무의 참조자를 한 번에 — 목록이 줄마다 같은 질의를 반복하지 않는 자리다."""
        if not task_ids:
            return {}
        wanted = {str(task_id): task_id for task_id in task_ids}
        found: dict[UUID, list[str]] = {}
        rows = self.session.execute(
            select(ResourceRelationshipRecord.resource_id, ResourceRelationshipRecord.member_id)
            .where(
                ResourceRelationshipRecord.resource_type == "task",
                ResourceRelationshipRecord.resource_id.in_(list(wanted)),
                ResourceRelationshipRecord.relationship_kind == "cc",
                ResourceRelationshipRecord.valid_until.is_(None),
            )
            .order_by(ResourceRelationshipRecord.member_id)
        ).all()
        for resource_id, member_id in rows:
            found.setdefault(wanted[str(resource_id)], []).append(member_id)
        return found

    def tasks_cc_for(self, member_id: str, *, include_closed: bool = False) -> list[TaskRecord]:
        """**참조로 받은 업무.** 드는 것도 요청한 것도 아니고, 읽기와 논의만 열린다."""
        ids = [
            UUID(resource_id)
            for resource_id in self.session.scalars(
                select(ResourceRelationshipRecord.resource_id).where(
                    ResourceRelationshipRecord.member_id == member_id,
                    ResourceRelationshipRecord.resource_type == "task",
                    ResourceRelationshipRecord.relationship_kind == "cc",
                    ResourceRelationshipRecord.valid_until.is_(None),
                )
            )
        ]
        if not ids:
            return []
        statement = select(TaskRecord).where(TaskRecord.id.in_(ids))
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
        start_date: date | None = None,
        due_date: date | None = None,
        project_id: UUID | None = None,
        approver_id: str | None = None,
        cc_member_ids: list[str] | None = None,
        checklist: list[str] | None = None,
        reference_task_ids: list[UUID] | None = None,
        source_meeting_id: UUID | None = None,
        source_agenda_id: UUID | None = None,
        promoted_by_member_id: str | None = None,
        parent_task_id: UUID | None = None,
        supersedes_request_id: UUID | None = None,
    ) -> tuple[WorkRequestRecord, bool]:
        if causation_key:
            existing = self._session.scalar(
                select(WorkRequestRecord).where(WorkRequestRecord.causation_key == causation_key)
            )
            if existing is not None:
                return existing, False
        now = datetime.now(UTC)
        # 조직 맥락은 **사람**에게서 온다 — 요청자가 시스템이면 누른 사람의 소속이 그 요청이 선 자리다 (D40).
        context_unit = _primary_unit(self._session, promoted_by_member_id or requester_id)
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
            # 시작일·프로젝트는 **내 업무와 같은 공통 payload** 의 칸이다. 요청 행이 직접 들고 있어야
            # 발송이 세우는 Task 와 뒤이은 회차가 같은 값을 읽는다 — 호출 인자로만 흘려보내면 재상신에서 사라진다.
            start_date=start_date,
            due_date=due_date,
            project_id=project_id,
            # 결재자도 요청 행이 직접 들고 있어야 **발송이 세우는 업무와 뒤이은 회차가 같은 값을 읽는다** —
            # 호출 인자로만 흘려보내면 과거 행을 수락해 업무를 세울 때 그 값이 아무 데도 없다.
            approver_id=approver_id,
            parent_task_id=parent_task_id,
            supersedes_request_id=supersedes_request_id,
            # **`pending`(응답 대기)** — 업무는 발송이 세우고 **담당은 수락이 세운다** (SPEC-003 §4 발송).
            # W1 은 여기 `assigned` 를 썼다: 판단 없이 활성 담당이 섰다는 사실을 가리키던 값이다. v2 는
            # 그 한 단계를 되돌리므로 **더는 그 값을 발행하지 않는다** — 다만 과거 행에서는 계속 읽히고
            # 뜻이 바뀌지 않는다 (정책 P-8 · DEC-002 C-3). 되돌려 쓰지도, 재해석하지도 않는다.
            state="pending",
            version=1,
            conditions=None,
            initial_checklist=list(checklist) if checklist else None,
            # 회의에서 넘어왔으면 출처가 **열로** 남는다 — description 끝 문장은 사람이 읽는 용도다 (SCAX-SPEC-004 §9-5 D20).
            source_meeting_id=source_meeting_id,
            source_agenda_id=source_agenda_id,
            promoted_by_member_id=promoted_by_member_id,
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
        # 이 표는 **사람**의 관계를 담는다(`member_id` 가 members 를 가리킨다). 요청자가 시스템이면 그 자리에
        # 앉힐 사람이 없으므로 그 한 줄을 쓰지 않는다 — 누른 사람은 cc 로 들어가 같은 가시성을 갖는다 (D40).
        related = [(requester_id, "requester"), (assignee_id, "assignee"), *[(cc, "cc") for cc in (cc_member_ids or [])]]
        for member_id, kind in related:
            if self._session.get(MemberRecord, member_id) is None:
                continue
            self._session.add(
                ResourceRelationshipRecord(member_id=member_id, resource_type="work_request", resource_id=str(request.id), relationship_kind=kind, valid_from=now)
            )
        snapshot = {"title": title, "description": description, "due_date": due_date.isoformat() if due_date else None, "assignee_id": assignee_id}
        version = SubjectVersionRecord(subject_id=subject.id, version=1, content_hash=_content_hash(snapshot), snapshot=snapshot, captured_at=now)
        self._session.add(version)
        self._session.flush()
        # **수락 회차를 연다.** W1 은 이 자리를 비웠다 — 판단 없이 담당이 섰으니 물을 것이 없었다.
        # v2 는 받는 사람이 **답해야** 하므로 그 질문이 있어야 한다: 회차가 없으면 판단함에 아무것도
        # 서지 않고, 수락·거절·협의를 부를 자리가 화면에서 사라진다 (SPEC-002 「하나의 질문 = 하나의
        # ActionItem」 · SPEC-003 §4 발송).
        self._open_request_acceptance(request, version, now)
        # 진행 기록 첫 줄. 승격이면 **회의가 보낸 것**이고 누른 사람이 행위자다 (D40) — 「시스템이 보냄」으로
        # 끝내면 사람이 왜 이 요청을 받았는지 읽을 수 없다. 그래서 회의 이름과 누른 사람을 함께 적는다.
        if promoted_by_member_id:
            actor_id = promoted_by_member_id
            meeting_title = _meeting_title(self._session, source_meeting_id)
            summary = f"회의 {meeting_title}에서 {_person(self._session, promoted_by_member_id)}이 업무 요청을 만들었다: {title}"
        else:
            actor_id = requester_id
            summary = f"{_person(self._session, requester_id)}가 {_person(self._session, assignee_id)}에게 업무를 보냄: {title}"
        ActivityLedger(self._session).record(
            target_type="work_request", target_id=str(request.id), event_kind="work_request.created", actor_id=actor_id,
            after_ref=f"work_request:{request.id}@1",
            safe_summary=summary,
            request_thread_id=thread.id,
        )
        return request, True

    def request_decisions(self, request: WorkRequestRecord) -> list[ReviewDecisionRecord]:
        """그 요청에 실제로 내려진 판단들 — 오래된 것부터. 재전송인지 가르는 원장이다."""
        item = self.open_decision_item(request)
        if item is None:
            return []
        submissions = list(
            self._session.scalars(select(SubmissionRecord).where(SubmissionRecord.decision_item_id == item.id))
        )
        if not submissions:
            return []
        return list(
            self._session.scalars(
                select(ReviewDecisionRecord)
                .where(ReviewDecisionRecord.submission_id.in_([row.id for row in submissions]))
                .order_by(ReviewDecisionRecord.decided_at)
            )
        )

    def _open_request_acceptance(
        self, request: WorkRequestRecord, version: SubjectVersionRecord, now: datetime
    ) -> DecisionItemRecord:
        """이 요청 하나가 받는 사람에게 던지는 **질문 하나** (SPEC-002 판단 계약).

        `accept` · `negotiate` · `reject` 셋이 답이고, 거절과 협의에는 사유가 필요하다. 회차가 오르는 것은
        재상신·수정이며 그때도 **같은 판단 항목**이 identity 를 유지한다 — 새 질문이 생기지 않는다.
        """
        item = DecisionItemRecord(
            kind="work_request.acceptance",
            subject_id=request.subject_id,
            context_type="request_thread",
            context_id=str(request.request_thread_id),
            effect_identity=f"task.create_from_request:{request.id}",
            status="open",
            due_at=datetime.combine(request.due_date, datetime.min.time(), tzinfo=UTC) if request.due_date else None,
            created_at=now,
        )
        self._session.add(item)
        self._session.flush()
        submission = SubmissionRecord(
            decision_item_id=item.id,
            subject_version_id=version.id,
            submission_version=1,
            submitted_by=request.requester_id,
            payload_hash=version.content_hash,
            decision_policy_snapshot={
                "decisions": ["accept", "negotiate", "reject"],
                "reason_required_for": ["reject", "negotiate"],
            },
            submitted_at=now,
        )
        self._session.add(submission)
        self._session.flush()
        self._session.add(
            ReviewAssignmentRecord(
                submission_id=submission.id,
                reviewer_member_id=request.assignee_id,
                status="pending",
                assigned_at=now,
                due_at=item.due_at,
            )
        )
        self._session.flush()
        return item

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

    def settle_by_agreement(self, request_id: UUID, actor_id: str, *, reason: str | None = None) -> None:
        """합의 취소가 그 **요청**을 끝낸다 — `accepted → cancelled_by_agreement` (SPEC-003 §4 State).

        업무만 닫고 요청을 `accepted` 로 두면 「보낸 업무」는 담당 확정으로 읽고, 담당 관계는 `active` 로
        남고, 목록 정리는 「진행 중」이라며 그 항목을 거절한다. 세 자리가 같은 사실을 달리 말하게 된다.
        **행은 지우지 않는다** — 상태가 바뀌고 이력이 한 줄 더 선다.
        """
        request = self.request(request_id, lock=True)
        if request is None or request.state == "cancelled_by_agreement":
            return
        now = datetime.now(UTC)
        request.state = "cancelled_by_agreement"
        request.version += 1
        request.updated_at = now
        # 담당 관계도 함께 끝난다. 「거절」이 아니므로 판단 행으로 적지 않는다 — 둘이 합의해 접은 것이다.
        task = self.task_for_request(request)
        if task is not None:
            for assignment in self._session.scalars(
                select(TaskAssignmentRecord).where(
                    TaskAssignmentRecord.task_id == task.id,
                    TaskAssignmentRecord.status.in_(("active", "pending")),
                )
            ):
                assignment.status = "ended"
                assignment.superseded_at = now
        item = self.open_decision_item(request)
        if item is not None and item.status == "open":
            item.status = "resolved"
            item.resolved_at = now
        self._session.flush()
        ActivityLedger(self._session).record(
            target_type="work_request", target_id=str(request.id),
            event_kind="work_request.cancelled_by_agreement", actor_id=actor_id, reason=reason,
            safe_summary=f"합의로 요청 취소: {request.title}", request_thread_id=request.request_thread_id,
        )

    def hidden_request_ids(self, member_id: str) -> set[UUID]:
        """그 사람이 자기 목록에서 뺀 요청들. **한 사람의 정리가 다른 사람 목록을 바꾸지 않는다.**"""
        return set(
            self._session.scalars(
                select(WorkRequestListEntryRecord.work_request_id).where(
                    WorkRequestListEntryRecord.member_id == member_id
                )
            )
        )

    def remove_list_entry(self, request: WorkRequestRecord, member_id: str) -> None:
        """목록에서만 뺀다 — **요청 행도 업무도 로그도 지우지 않는다** (정책 P-12).

        「삭제」가 아니다: 거절·취소·재요청의 이력은 그대로 남고, 되돌리려면 이 행 하나를 지우면 된다.
        같은 사람이 두 번 눌러도 한 건이다.
        """
        existing = self._session.scalar(
            select(WorkRequestListEntryRecord).where(
                WorkRequestListEntryRecord.work_request_id == request.id,
                WorkRequestListEntryRecord.member_id == member_id,
            )
        )
        if existing is not None:
            return
        self._session.add(
            WorkRequestListEntryRecord(
                work_request_id=request.id, member_id=member_id, removed_at=datetime.now(UTC)
            )
        )
        self._session.flush()
        ActivityLedger(self._session).record(
            target_type="work_request", target_id=str(request.id), event_kind="work_request.list_entry_removed",
            actor_id=member_id, safe_summary=f"요청자 목록에서 정리: {request.title}",
            request_thread_id=request.request_thread_id,
        )

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

    def source_meeting_title(self, request: WorkRequestRecord) -> str | None:
        """이 요청이 나온 회의의 이름. 회의에서 오지 않았으면 `None` — 지어내지 않는다 (D40)."""
        if request.source_meeting_id is None:
            return None
        return _meeting_title(self._session, request.source_meeting_id)

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

    def task_for_request(self, request: WorkRequestRecord, *, lock: bool = False) -> TaskRecord | None:
        """그 요청이 세운 업무. 연결은 업무 자신의 `source_work_request_id` 한 곳에만 산다."""
        statement = select(TaskRecord).where(TaskRecord.source_work_request_id == request.id)
        return self._session.scalar(
            statement.with_for_update().execution_options(populate_existing=True) if lock else statement
        )

    def accept_request_assignment(self, request: WorkRequestRecord, actor_id: str) -> TaskRecord:
        """수락 — **같은 업무의 담당을 확정한다. 새 업무를 만들지 않는다** (SPEC-003 §4 수락).

        W1 에서는 이 자리가 `create_accepted_task()` 였다: 요청 하나에 업무 하나였지만 그 업무는 발송이
        아니라 **수락**이 만들었다. v2 는 발송이 업무를 세우므로 여기서 만들 것이 없다 — `task_id` 도
        `parent_task_id` 도 발송 때 그대로이고, 바뀌는 것은 담당 행 하나다.

        `state` 는 `open` **그대로**이고 `started_at` 은 **비워 둔다** — 받아들인 것과 시작한 것은 다른
        사실이고, 그 둘이 각각 읽혀야 한다 (인수조건 · 정책 V-10·V-13).
        """
        now = datetime.now(UTC)
        task = self.task_for_request(request, lock=True)
        if task is None:
            # **업무 없이 답을 기다리는 요청 행의 자리다.** W1 **이전**의 모양이다 — 그때는 수락이
            # 업무를 세웠다. (W1 신규 생성은 즉시 배정이었으므로 W1 이 만든 행은 여기 오지 않는다.)
            # 그런 행이 실제 운영 DB 에 남아 있는지는 **확인되지 않았다**: Phase 0 에서 실행 DB 가 비어
            # 있어 세어 보지 못했고, 있다고 단정하지 않는다. 다만 **없다고 단정할 근거도 없으므로**
            # 만나면 그 시절 방식대로 세운다 — 기존 행을 일괄 변환하지 않기로 한 이상(정책 P-8)
            # 그 행을 만나는 자리에서 처리하는 것이 남는 선택이다. 행위자는 수락한 담당자다.
            # **v2 로 발송된 요청은 이 갈래로 오지 않는다**: 발송이 업무를 세웠고 위에서 찾힌다.
            return self.create_task_for_request(request, actor_id=request.assignee_id, accepted=True)
        assignment = self._session.scalar(
            select(TaskAssignmentRecord).where(
                TaskAssignmentRecord.task_id == task.id,
                TaskAssignmentRecord.source_work_request_id == request.id,
                TaskAssignmentRecord.status == "pending",
            )
        )
        if assignment is None:
            # 재전송이면 이미 `active` 다 — 두 번째 effect 없이 그 업무를 그대로 돌려준다 (영수증).
            return task
        item = self.open_decision_item(request)
        decision = None
        submission = self.current_submission(request)
        if submission is not None:
            decision = self._session.scalar(
                select(ReviewDecisionRecord)
                .where(ReviewDecisionRecord.submission_id == submission.id)
                .order_by(ReviewDecisionRecord.decided_at.desc())
            )
        assignment.status = "active"
        assignment.accepted_at = now
        if item is not None:
            assignment.source_decision_item_id = item.id
            task.source_decision_item_id = item.id
        if decision is not None:
            assignment.source_review_decision_id = decision.id
            task.source_review_decision_id = decision.id
        task.version += 1
        task.updated_at = now
        self._session.add(TaskActivityRecord(task_id=task.id, task_version=task.version, state=task.state, occurred_at=now))
        ActivityLedger(self._session).record(
            target_type="task", target_id=str(task.id), event_kind="task.request_accepted", actor_id=actor_id,
            after_ref=f"task_assignment:{assignment.id}",
            safe_summary=f"{_person(self._session, actor_id)}가 업무 요청을 수락함: {task.title}",
        )
        SqlAlchemyTaskRepository(self._session).capture_version(task, actor_id, "task.request_accepted")
        self._session.flush()
        self._session.refresh(task)
        return task

    def close_request_task(self, request: WorkRequestRecord, actor_id: str, *, cancel_reason: str, summary: str) -> TaskRecord | None:
        """거절·철회·합의 취소가 그 업무를 닫는다 — **상위 연결과 로그는 남긴다** (SPEC-003 §4 거절).

        행을 지우지 않는다. 요청자의 상위에서 「취소됨 — 요청 거절」로 읽혀야 하고, 재요청이 같은 상위
        아래에 새로 설 때 이전 시도가 거기 그대로 있어야 한다.
        """
        now = datetime.now(UTC)
        task = self.task_for_request(request, lock=True)
        if task is None:
            return None
        if task.state == TaskState.CANCELLED:
            return task
        for assignment in self._session.scalars(
            select(TaskAssignmentRecord).where(
                TaskAssignmentRecord.task_id == task.id, TaskAssignmentRecord.status.in_(("active", "pending"))
            )
        ):
            # 수락 전이면 아무도 들지 않았고, 수락 뒤면 들던 사람이 있다. 어느 쪽도 「거절」이 아니므로
            # 판단 행으로 적지 않고 담당 관계만 끝난 것으로 닫는다.
            assignment.status = "ended"
            assignment.superseded_at = now
        task.state = TaskState.CANCELLED
        task.cancel_reason = cancel_reason
        task.version += 1
        task.updated_at = now
        self._session.add(TaskActivityRecord(task_id=task.id, task_version=task.version, state=task.state, occurred_at=now))
        ActivityLedger(self._session).record(
            target_type="task", target_id=str(task.id), event_kind="task.state_changed", actor_id=actor_id,
            before_ref=f"task:{task.id}@{task.version - 1}", safe_summary=summary,
        )
        SqlAlchemyTaskRepository(self._session).capture_version(task, actor_id, "task.state_changed")
        self._session.flush()
        self._session.refresh(task)
        return task

    def create_task_for_request(
        self,
        request: WorkRequestRecord,
        *,
        actor_id: str,
        accepted: bool = False,
        preceding_task_ids: list[UUID] | None = None,
        source_action_item_id: UUID | None = None,
        source_decision_item_id: UUID | None = None,
        source_submission_id: UUID | None = None,
        source_review_decision_id: UUID | None = None,
    ) -> TaskRecord:
        """요청 하나에 업무 하나 + **활성 담당 하나**. 신규 경로는 판단 없이 여기로 바로 온다.

        `actor_id` 는 **이 업무를 있게 한 명령을 실제로 부른 사람**이다. 신규 경로에서는 보낸 사람이고,
        회의 승격에서는 누른 사람이며, 과거 pending 행의 수락에서는 수락한 담당자다. 받는 사람을 일률로
        적으면 아무 행위도 하지 않은 사람이 `tasks.created_by_actor_id` 와 최초 회차의 actor 로 남는다.

        요청 자신의 판단 회차가 있으면 그것이 출처다(과거 행). 회차가 없는 신규 경로에서는 호출자가
        준 계보를 그대로 싣는다 — AX 확인으로 만들어진 업무는 자기를 있게 한 action 과 그 확인 회차를
        가리켜야 한다. 어느 쪽도 없으면 비는 것이 정상이고, 사람이 하지 않은 판단을 가리키지 않는다.
        """
        now = datetime.now(UTC)
        item = self.open_decision_item(request)
        submission = self.current_submission(request)
        decision = self._session.scalar(
            select(ReviewDecisionRecord).where(ReviewDecisionRecord.submission_id == submission.id).order_by(ReviewDecisionRecord.decided_at.desc())
        ) if submission else None
        # 요청 자신의 회차가 있으면 그것이 출처고(과거 행), 없으면 호출자가 준 계보를 싣는다(AX 확인).
        round_decision_item_id = item.id if item else source_decision_item_id
        round_submission_id = submission.id if submission else source_submission_id
        round_review_decision_id = decision.id if decision else source_review_decision_id
        task = TaskRecord(
            created_by_actor_id=actor_id,
            title=request.title,
            description=request.description,
            # 요청에 실린 공통 payload 가 그대로 업무의 첫 회차가 된다 — 시작일·프로젝트도 예외가 아니다.
            start_date=getattr(request, "start_date", None),
            due_date=request.due_date,
            project_id=getattr(request, "project_id", None),
            # **요청의 결재자가 그 요청이 세우는 업무의 결재자다.** 발송이 세우는 신규 경로에서도,
            # 수락이 세우는 과거 행에서도 같은 값이라 두 길의 답이 갈리지 않는다.
            approver_id=getattr(request, "approver_id", None),
            # 하위 요청이면 **발송 단계부터** 상위 아래에 선다 (정책 V-9). 수락 전에도, 거절된 뒤에도
            # 이 연결은 유지된다 — 요청자의 상위에서 「취소됨 — 요청 거절」로 읽혀야 하기 때문이다.
            parent_task_id=getattr(request, "parent_task_id", None),
            organization_unit_id=_primary_unit(self._session, request.assignee_id),
            # 회의에서 온 요청이면 업무도 회의에서 왔다고 말한다 — 받는 사람이 왜 이 일이 생겼는지를 좇는다 (§9-7).
            origin_kind="meeting" if request.source_meeting_id is not None else "request_effect",
            request_thread_id=request.request_thread_id,
            source_work_request_id=request.id,
            # 업무가 설 때 요청의 출처가 업무로 옮겨진다.
            source_meeting_id=request.source_meeting_id,
            source_agenda_id=request.source_agenda_id,
            source_action_item_id=source_action_item_id,
            source_decision_item_id=round_decision_item_id,
            source_submission_id=round_submission_id,
            source_review_decision_id=round_review_decision_id,
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
                # **수락 대기다.** W1 은 여기서 바로 `active` 를 세웠고, 그래서 받는 사람이 답하기도 전에
                # 그 사람의 「내 업무」에 일이 섰다. v2 는 그 한 단계만 되돌린다 (SPEC-003 §4 발송 · 정책 V-10):
                # 행은 지금 서고 **책임은 수락에서** 선다. `accepted_at` 은 사람이 수락한 그 순간에만 찍힌다 —
                # 하지 않은 수락을 시각으로 남기지 않는다.
                #
                # `accepted=True` 는 **과거 행을 수락하는 자리 하나뿐**이다: 업무 없이 답을 기다리던 요청은
                # 수락이 업무를 세우므로 그 순간 이미 담당이 확정돼 있다 (`accept_request_assignment`).
                status="active" if accepted else "pending",
                source_work_request_id=request.id,
                source_decision_item_id=item.id if item else None,
                source_review_decision_id=decision.id if decision else None,
                created_at=now,
                accepted_at=now if accepted else None,
            )
        )
        self._session.flush()
        tasks = SqlAlchemyTaskRepository(self._session)
        # 요청의 참조자는 **그 요청이 세운 업무의 참조자이기도 하다.** 업무 쪽에 같은 행을 세우지 않으면
        # 참조로 받은 사람이 요청은 읽는데 그 요청이 만든 업무는 못 읽는 경계 불일치가 생긴다.
        tasks.set_cc_members(task.id, self.cc_member_ids(request), created_at=now)
        # 요청에 실린 선행이 그대로 이 업무의 선행이 된다 — 판정은 이미 업무 모듈이 끝냈다.
        if preceding_task_ids:
            tasks.replace_predecessors(task.id, list(preceding_task_ids), actor_id)
        # The steps came with the request, so they were written by the person who asked, not by the one receiving it.
        tasks.seed_checklist(task, list(request.initial_checklist or []), request.requester_id)
        # So did the earlier work they pointed at: the pointer travels, the permission to open it does not.
        for reference in self.request_references(request.id):
            tasks.add_reference(task.id, reference.referenced_task_id, request.requester_id)
        tasks.capture_version(task, actor_id, "task.created")
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
        event = WorkRequestAuditEventRecord(
            request_id=request_id,
            actor_id=actor_id,
            event_type=event_type,
            payload=payload,
            occurred_at=datetime.now(UTC),
        )
        self._session.add(event)
        request = self._session.get(WorkRequestRecord, request_id)
        if request is None or event_type not in {"work_request.created", "work_request.accepted"}:
            return
        self._session.flush()
        recipient = request.assignee_id if event_type == "work_request.created" else request.requester_id
        actor_name = _person(self._session, actor_id)
        summary = (
            f"{actor_name}님이 ‘{request.title}’ 업무를 요청했습니다."
            if event_type == "work_request.created"
            else f"{actor_name}님이 ‘{request.title}’ 업무 요청을 수락했습니다."
        )
        SqlAlchemyNotificationRepository(self._session).emit(
            recipient_member_id=recipient,
            source_kind="work_request_audit_event",
            source_id=str(event.id),
            kind="work_request.received" if event_type == "work_request.created" else event_type,
            resource_type="work_request",
            resource_id=str(request.id),
            resource_version=int(request.version),
            resource_title=request.title,
            actor_member_id=actor_id,
            safe_summary=summary,
            created_at=event.occurred_at,
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

    def predecessor_task_ids(self, task_id: UUID | None) -> list[UUID]:
        """그 업무의 **활성 선행**, 고른 순서 그대로.

        요청 행은 선행을 따로 갖지 않는다 — 발송이 세운 업무가 그 관계의 자리다 (SPEC-001 §4). 그래서
        요청 조회가 「무엇 다음인가」를 답하려면 파생 업무에서 읽어 와야 한다. 업무가 아직 없는 요청
        행(W1 이전 모양)은 선행도 없다.
        """
        if task_id is None:
            return []
        return list(
            self._session.scalars(
                select(TaskPredecessorRecord.predecessor_task_id)
                .where(
                    TaskPredecessorRecord.task_id == task_id,
                    TaskPredecessorRecord.released_at.is_(None),
                )
                .order_by(TaskPredecessorRecord.position, TaskPredecessorRecord.created_at)
            )
        )

    # ---- 참조 읽음 영수증 — 사람마다 따로 움직이는 사실 --------------------------

    def read_receipt(self, request_id: UUID, member_id: str) -> WorkRequestReadReceiptRecord | None:
        return self._session.scalar(
            select(WorkRequestReadReceiptRecord).where(
                WorkRequestReadReceiptRecord.work_request_id == request_id,
                WorkRequestReadReceiptRecord.member_id == member_id,
            )
        )

    def mark_read(self, request_id: UUID, member_id: str) -> WorkRequestReadReceiptRecord:
        """이 사람이 그 참조를 읽었다. **두 번째 호출은 처음 행을 그대로 돌려준다.**

        같은 사람의 **동시 두 번**이 행 하나로 수렴해야 한다 (§5 동시성). application 검사만으로는
        두 transaction 이 동시에 「없다」를 보고 둘 다 넣는 틈이 남으므로, 실제로 넣어 보고
        **유일성 제약이 거절하면 이미 선 행을 읽는다.** SAVEPOINT 안에서 시도하므로 진 쪽의 제약
        위반이 바깥 transaction 을 깨지 않는다 — 읽음은 다른 것을 함께 바꾸지 않지만, 이 저장소의
        명령은 한 session 에 실려 오므로 바깥을 살려 두는 것이 이 자리의 계약이다.
        """
        existing = self.read_receipt(request_id, member_id)
        if existing is not None:
            return existing
        record = WorkRequestReadReceiptRecord(
            work_request_id=request_id, member_id=member_id, read_at=datetime.now(UTC)
        )
        try:
            with self._session.begin_nested():
                self._session.add(record)
                self._session.flush()
        except IntegrityError:
            # 진 쪽이다. 이긴 쪽이 이미 세운 행을 읽어 **같은 `read_at`** 을 돌려준다.
            won = self.read_receipt(request_id, member_id)
            if won is None:  # pragma: no cover - 유일성 말고 깨질 제약이 이 표에 없다
                raise
            return won
        return record

    def read_request_ids(self, member_id: str) -> set[UUID]:
        """이 사람이 이미 읽은 참조 요청 전부 — 수신함 한 표면이 쓰는 필터의 원천이다."""
        return set(
            self._session.scalars(
                select(WorkRequestReadReceiptRecord.work_request_id).where(
                    WorkRequestReadReceiptRecord.member_id == member_id
                )
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

    def create_unheld_task(
        self,
        creator_id: str,
        title: str,
        *,
        project_id: UUID,
        description: str | None = None,
        start_date: date | None = None,
        due_date: date | None = None,
        organization_unit_id: str | None = None,
    ) -> TaskRecord:
        """아직 누구의 것도 아닌 일. 프로젝트 계획에는 있으나 사람이 정해지지 않은 상태다.

        배정 행이 하나도 없다는 것이 곧 `담당자 미정`이다. `미정`이라는 이름의 가짜 담당자를 만들지 않는다.
        """
        now = datetime.now(UTC)
        task = TaskRecord(
            created_by_actor_id=creator_id,
            title=title,
            state=TaskState.OPEN,
            block_reason=None,
            project_id=project_id,
            description=description,
            start_date=start_date,
            due_date=due_date,
            organization_unit_id=organization_unit_id,
            origin_kind="project_plan",
            version=1,
            created_at=now,
            updated_at=now,
        )
        self._session.add(task)
        self._session.flush()
        self._session.add(TaskActivityRecord(task_id=task.id, task_version=task.version, state=task.state, occurred_at=now))
        ActivityLedger(self._session).record(
            target_type="task", target_id=str(task.id), event_kind="task.created", actor_id=creator_id,
            after_ref=f"task:{task.id}@1", safe_summary=f"프로젝트 업무 생성: {title}",
        )
        SqlAlchemyTaskRepository(self._session).capture_version(task, creator_id, "task.created")
        return task

    def hand_to(self, task: TaskRecord, assigner_id: str, assignee_id: str) -> TaskAssignmentRecord:
        """아직 아무도 들지 않은 일에 첫 사람을 붙인다. 그 사람이 수락해야 자기 업무가 된다.

        배정과 똑같이 판단 항목을 함께 만든다. 만들지 않으면 붙였다는 사실이 그 사람에게 닿지 않고, 자기
        판단함에 없는 일을 수락할 방법이 없다.
        """
        now = datetime.now(UTC)
        appended = TaskAssignmentRecord(
            task_id=task.id,
            assignee_id=assignee_id,
            assigned_by=assigner_id,
            assignment_kind="direct",
            status="pending",
            created_at=now,
        )
        self._session.add(appended)
        self._session.flush()
        task.version += 1
        task.updated_at = now
        self._open_assignment_acceptance(task, appended, now)
        ActivityLedger(self._session).record(
            target_type="task", target_id=str(task.id), event_kind="task.assignment.offered", actor_id=assigner_id,
            after_ref=f"task_assignment:{appended.id}",
            safe_summary=f"{_person(self._session, assigner_id)}가 {_person(self._session, assignee_id)}에게 담당을 맡김: {task.title}",
        )
        self._session.add(TaskActivityRecord(task_id=task.id, task_version=task.version, state=task.state, occurred_at=now))
        SqlAlchemyTaskRepository(self._session).capture_version(task, assigner_id, "task.assignment.offered")
        self._session.flush()
        self._session.refresh(task)
        return appended

    def _open_assignment_acceptance(self, task: TaskRecord, appended: TaskAssignmentRecord, now: datetime, *, legacy: bool = False) -> None:
        """Every offered assignment has one immutable question for its new assignee."""
        assigner_id, assignee_id = appended.assigned_by, appended.assignee_id
        subject = self._session.scalar(
            select(SubjectRecord).where(
                SubjectRecord.owning_resource_type == "task", SubjectRecord.owning_resource_id == str(task.id)
            )
        )
        if subject is None:
            subject = SubjectRecord(
                subject_type="task", owning_resource_type="task", owning_resource_id=str(task.id), created_at=now
            )
            self._session.add(subject)
            self._session.flush()
        item = DecisionItemRecord(
            kind="task.assignment.acceptance",
            subject_id=subject.id,
            context_type="task",
            context_id=str(task.id),
            effect_identity=f"task_assignment.activate:{appended.id}",
            status="open",
            due_at=datetime.combine(task.due_date, datetime.min.time(), tzinfo=UTC) if task.due_date else None,
            created_at=now,
        )
        self._session.add(item)
        self._session.flush()
        appended.source_decision_item_id = item.id
        # 판단은 무엇에 대한 판단인지를 가리켜야 한다: 지금 이 회차의 Task가 그 대상이다.
        snapshot = {
            "title": task.title,
            "description": task.description,
            "start_date": task.start_date.isoformat() if task.start_date else None,
            "due_date": task.due_date.isoformat() if task.due_date else None,
            "assignee_id": assignee_id,
            "assigned_by": assigner_id,
            **({"task_version": int(task.version)} if legacy else {}),
        }
        # A legacy assignment had no immutable submission. Capture the content
        # being answered now, without backdating it or replacing another round.
        latest = self._session.scalar(select(func.max(SubjectVersionRecord.version)).where(SubjectVersionRecord.subject_id == subject.id)) if legacy else None
        version = SubjectVersionRecord(
            subject_id=subject.id,
            version=max(int(task.version), int(latest or 0) + 1) if legacy else task.version,
            content_hash=_content_hash(snapshot),
            snapshot=snapshot,
            captured_at=now,
        )
        self._session.add(version)
        self._session.flush()
        submission = SubmissionRecord(
            decision_item_id=item.id,
            subject_version_id=version.id,
            submission_version=1,
            submitted_by=assigner_id,
            payload_hash=version.content_hash,
            decision_policy_snapshot={"decisions": ["accept", "reject"], "reason_required_for": ["reject"], **({"legacy_assignment_id": str(appended.id), "captured_on_command": True} if legacy else {})},
            submitted_at=now,
        )
        self._session.add(submission)
        self._session.flush()
        self._session.add(
            ReviewAssignmentRecord(
                submission_id=submission.id, reviewer_member_id=assignee_id, status="pending", assigned_at=now, due_at=item.due_at
            )
        )

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
        references: list[UUID] | None = None,
        parent_task_id: UUID | None = None,
        source_action_item_id: UUID | None = None,
        source_decision_item_id: UUID | None = None,
        source_submission_id: UUID | None = None,
        source_review_decision_id: UUID | None = None,
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
            source_action_item_id=source_action_item_id,
            source_decision_item_id=source_decision_item_id,
            source_submission_id=source_submission_id,
            source_review_decision_id=source_review_decision_id,
        )
        self._session.add(task)
        self._session.flush()
        # 관리자 배정도 **수락을 기다리지 않는다** — 명령이 성공하면 상대의 업무 목록에 바로 선다
        # (WORK-001 Phase 4). `accepted_at` 은 본인 생성과 같은 뜻으로만 쓴다: 담당이 선 시각이다.
        assignment = TaskAssignmentRecord(
            task_id=task.id, assignee_id=assignee_id, assigned_by=assigner_id, assignment_kind="direct",
            status="active", created_at=now, accepted_at=now,
        )
        self._session.add(assignment)
        self._session.flush()
        tasks = SqlAlchemyTaskRepository(self._session)
        tasks.seed_checklist(task, checklist, assigner_id)
        for referenced_task_id in references or []:
            tasks.add_reference(task.id, referenced_task_id, assigner_id)
        tasks.capture_version(task, assigner_id, "task.created")
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
        return self._session.get(TaskRecord, task_id, with_for_update=lock or None, populate_existing=lock)

    def current_assignment_for(self, task_id: UUID, *, lock: bool = False) -> TaskAssignmentRecord | None:
        """**지금 이 업무를 든 사람.** `active` 한 행이고, 없으면 아무도 들지 않았다.

        `pending` 을 여기 섞지 않는다 — 담당 변경 대기 중에는 `active` 1 + `pending` 1 이 **공존하므로**
        (SPEC-003 §4 담당 관계 · 정책 V-18) 한 조회가 둘을 함께 받으면 어느 쪽이 나올지가 행 순서에 달린다.
        「누가 들고 있나」와 「누가 답을 기다리나」는 다른 질문이고, 그래서 조회도 둘이다 (정책 P-3).
        """
        statement = select(TaskAssignmentRecord).where(
            TaskAssignmentRecord.task_id == task_id, TaskAssignmentRecord.status == "active"
        )
        return self._session.scalar(
            statement.with_for_update().execution_options(populate_existing=True) if lock else statement
        )

    def pending_assignment_for(self, task_id: UUID, *, lock: bool = False) -> TaskAssignmentRecord | None:
        """**답을 기다리는 담당 제안.** 요청 발송의 수락 대기 · 첫 지정 · 담당 변경 제안이 여기 선다.

        한 업무에 대기 제안은 하나다 (SPEC-003 §4 Validation).
        """
        statement = select(TaskAssignmentRecord).where(
            TaskAssignmentRecord.task_id == task_id, TaskAssignmentRecord.status == "pending"
        )
        return self._session.scalar(
            statement.with_for_update().execution_options(populate_existing=True) if lock else statement
        )

    def assignment_rows_for(self, task_id: UUID) -> list[TaskAssignmentRecord]:
        """이 업무의 담당 이력 전부 — 오래된 것부터. 현재/대기를 각각 내는 조회가 여기서 나온다."""
        return list(
            self._session.scalars(
                select(TaskAssignmentRecord)
                .where(TaskAssignmentRecord.task_id == task_id)
                .order_by(TaskAssignmentRecord.created_at, TaskAssignmentRecord.id)
            )
        )

    def reassign(self, task: TaskRecord, current: TaskAssignmentRecord, assigner_id: str, assignee_id: str, reason: str | None) -> TaskAssignmentRecord:
        """담당을 바꾸자고 **제안한다.** 기존 담당은 이 시점에 닫히지 않는다 (SPEC-003 §4 · 정책 V-18).

        예전에는 여기서 기존 행을 `superseded` 로 닫았다. 그러면 제안과 수락 사이에 **아무도 책임지지 않는
        구간**이 생기고, 새 담당이 거절하면 그 일은 담당자 없이 남는다. v2 는 그 구간을 없앤다 —
        기존 `active` 는 그대로 두고 새 행을 `pending` 으로 **덧붙이기만** 한다. 실제 교체는
        `decide(..., "accept")` 한 덩어리에서만 일어난다.

        `supersedes_assignment_id` 가 **이 행이 교체 제안이라는 표식**이다. 그 값이 없는 `pending` 은
        「담당 없는 업무의 첫 지정」(`hand_to`)이고 거절의 뜻이 다르다 — 그쪽은 현행대로 Task 가 취소된다.
        """
        now = datetime.now(UTC)
        previous_assignee = current.assignee_id
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
        self._open_assignment_acceptance(task, appended, now)
        self._session.add(TaskActivityRecord(task_id=task.id, task_version=task.version, state=task.state, occurred_at=now))
        ActivityLedger(self._session).record(
            # 제안·수락·거절·실제 교체를 **각각** 남긴다 (정책 L-9). 이 줄은 그중 「제안」이다.
            target_type="task", target_id=str(task.id), event_kind="task.assignment.change_proposed", actor_id=assigner_id,
            before_ref=f"task_assignment:{current.id}", after_ref=f"task_assignment:{appended.id}", reason=reason,
            safe_summary=(
                f"{_person(self._session, assigner_id)}가 담당자를 {_person(self._session, previous_assignee)}에서 "
                f"{_person(self._session, assignee_id)}로 바꾸자고 제안함: {task.title}"
            ),
        )
        SqlAlchemyTaskRepository(self._session).capture_version(task, assigner_id, "task.assignment.change_proposed", reason)
        self._session.flush()
        self._session.refresh(task)
        return appended

    def decide(self, assignment: TaskAssignmentRecord, actor_id: str, decision: str, *, reason: str | None = None) -> ReviewDecisionRecord:
        """Acceptance activates the assignment; rejection closes it and cancels the never-entered Task."""
        now = datetime.now(UTC)
        task = self.task_for(assignment)
        if assignment.source_decision_item_id is None:
            self._open_assignment_acceptance(task, assignment, now, legacy=True)
            self._session.flush()
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
            review_assignment_id=review.id, submission_id=submission.id, actor_member_id=actor_id, decision=decision, reason=reason,
            conditions={DECISION_FACTS: {DECISION_VERSION: int(task.version)}}, decided_at=now
        )
        self._session.add(record)
        item.status = "resolved"
        item.resolved_at = now
        self._session.flush()
        assignment.source_review_decision_id = record.id
        # **교체 제안인가, 첫 지정인가.** `supersedes_assignment_id` 가 그 표식이다 — `reassign()` 만
        # 그 값을 싣는다. 두 경우는 **거절의 뜻이 다르다**: 첫 지정의 거절은 「아무도 안 받았다」라
        # 현행대로 Task 를 취소하고, 교체 제안의 거절은 **제안만 닫는다** — 기존 담당이 그대로 있으므로
        # 취소할 이유가 없다 (정책 V-18 · BASE-002 O-13).
        replaced = (
            self._session.get(TaskAssignmentRecord, assignment.supersedes_assignment_id)
            if assignment.supersedes_assignment_id is not None
            else None
        )
        if decision == "accept":
            if replaced is not None and replaced.status == "active":
                # 종료와 활성화가 **한 transaction** 이다 — 활성 담당이 0명이거나 2명인 중간 상태는
                # 관찰되지 않는다 (정책 V-18 · K-3). 부분 유일 인덱스가 둘째 `active` 를 거절한다.
                replaced.status = "superseded"
                replaced.superseded_at = now
                # **닫는 UPDATE 를 먼저 내보낸다.** 두 행을 한 flush 에 맡기면 순서를 ORM 이 정하고
                # (같은 표에서는 대개 기본키 순), 새 행이 먼저 `active` 가 되는 순간 부분 유일 인덱스가
                # 둘째 `active` 를 거절해 500 이 된다 — uuid 값에 따라 **간헐적으로만** 터진다.
                # 트랜잭션은 하나이므로 밖에서 보이는 중간 상태는 여전히 없다.
                self._session.flush()
            assignment.status = "active"
            assignment.accepted_at = now
            task.source_decision_item_id = item.id
            task.source_review_decision_id = record.id
            summary = f"담당 교체 수락: {task.title}" if replaced is not None else f"배정 수락: {task.title}"
        else:
            assignment.status = "declined"
            assignment.declined_at = now
            assignment.decline_reason = reason
            if replaced is None:
                task.state = TaskState.CANCELLED
                task.cancel_reason = "direct"
                task.version += 1
                task.updated_at = now
                self._session.add(TaskActivityRecord(task_id=task.id, task_version=task.version, state=task.state, occurred_at=now))
                summary = f"배정 거절: {task.title}"
            else:
                summary = f"담당 교체 거절: {task.title}"
        ActivityLedger(self._session).record(
            target_type="task", target_id=str(task.id), event_kind=f"task.assignment_{decision}ed", actor_id=actor_id,
            before_ref=f"task_assignment:{assignment.id}", after_ref=f"review_decision:{record.id}", reason=reason, safe_summary=summary,
        )
        if decision == "accept" and replaced is not None:
            # 「제안」과 「실제 교체」가 각각이다 (정책 L-9) — 수락 줄 다음에 교체가 일어난 줄이 선다.
            ActivityLedger(self._session).record(
                target_type="task", target_id=str(task.id), event_kind="task.assignment.changed", actor_id=actor_id,
                before_ref=f"task_assignment:{replaced.id}", after_ref=f"task_assignment:{assignment.id}",
                safe_summary=(
                    f"담당자가 {_person(self._session, replaced.assignee_id)}에서 "
                    f"{_person(self._session, assignment.assignee_id)}로 바뀜: {task.title}"
                ),
            )
        self._session.flush()
        self._session.refresh(task)
        return record

    def cancel(self, assignment: TaskAssignmentRecord, actor_id: str) -> None:
        """Close an unanswered direct assignment without pretending the assignee judged it."""
        now = datetime.now(UTC)
        task = self.task_for(assignment)
        if assignment.source_decision_item_id is None:
            self._open_assignment_acceptance(task, assignment, now, legacy=True)
            self._session.flush()
        item = self._session.get(DecisionItemRecord, assignment.source_decision_item_id) if assignment.source_decision_item_id else None
        submission = (
            self._session.scalar(
                select(SubmissionRecord)
                .where(SubmissionRecord.decision_item_id == item.id)
                .order_by(SubmissionRecord.submission_version.desc())
            )
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
        review.status = "cancelled"
        item.status = "resolved"
        item.resolved_at = now
        assignment.status = "cancelled"
        task.state = TaskState.CANCELLED
        task.version += 1
        task.updated_at = now
        self._session.add(TaskActivityRecord(task_id=task.id, task_version=task.version, state=task.state, occurred_at=now))
        ActivityLedger(self._session).record(
            target_type="task",
            target_id=str(task.id),
            event_kind="task.assignment_cancelled",
            actor_id=actor_id,
            before_ref=f"task_assignment:{assignment.id}",
            safe_summary=f"업무 요청 취소: {task.title}",
        )
        self._session.flush()
        self._session.refresh(task)


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
        attachment = self._session.get(AttachmentRecord, attachment_id)
        if attachment is not None and attachment.source_kind in {"native_revision", "native_recording"}:
            raise ValueError("native revisions retain their owning resource; they are not shareable file bindings")
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
            .where(AttachmentBindingRecord.context_type == context_type, AttachmentBindingRecord.context_id == context_id,
                   AttachmentRecord.source_kind.not_in(("native_revision", "native_recording")))
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
                AttachmentRecord.source_kind.not_in(("native_revision", "native_recording")),
            )
        ).first()
        return (row[0], row[1]) if row else None

    def unbind(self, binding: AttachmentBindingRecord) -> None:
        binding.unbound_at = datetime.now(UTC)

    def attachment(self, attachment_id: UUID) -> AttachmentRecord | None:
        return self._session.get(AttachmentRecord, attachment_id)

    def active_task_contexts(self, material_id: UUID) -> list[str]:
        """Candidate owners, not an authorization decision; callers must read each Task."""
        return list(self._session.scalars(
            select(AttachmentBindingRecord.context_id)
            .join(AttachmentRecord, AttachmentRecord.id == AttachmentBindingRecord.attachment_id)
            .where(
                AttachmentBindingRecord.attachment_id == material_id,
                AttachmentBindingRecord.context_type == "task",
                AttachmentBindingRecord.unbound_at.is_(None),
                AttachmentRecord.source_kind.not_in(("native_revision", "native_recording")),
            )
            .distinct()
            .order_by(AttachmentBindingRecord.context_id)
        ))

    def bindings_for_many(self, context_type: str, context_ids: list[str]) -> list[tuple[AttachmentBindingRecord, AttachmentRecord]]:
        if not context_ids:
            return []
        rows = self._session.execute(
            select(AttachmentBindingRecord, AttachmentRecord)
            .join(AttachmentRecord, AttachmentRecord.id == AttachmentBindingRecord.attachment_id)
            .where(AttachmentBindingRecord.context_type == context_type, AttachmentBindingRecord.context_id.in_(context_ids),
                   AttachmentBindingRecord.unbound_at.is_(None), AttachmentRecord.source_kind.not_in(("native_revision", "native_recording")))
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
