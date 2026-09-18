"""Request mutation receipts; detailed timelines and discussions are separate queries."""
from typing_extensions import TypedDict
from pydantic import JsonValue
from ax_workspace.modules.actions.results import ActionDiscussionView, ActionEvidenceView
from ax_workspace.modules.work.task_results import TaskSummaryView


class WorkRequestMutationResult(TypedDict):
    request_id: str
    request_thread_id: str | None
    submission_version: int | None
    title: str
    description: str | None
    due_date: str | None
    checklist: list[str]
    requester_id: str
    requester_kind: str
    source_meeting_id: str | None
    promoted_by_member_id: str | None
    assignee_id: str
    cc_member_ids: list[str]
    state: str
    version: int
    task_id: str | None
    assignment_state: str | None
    parent_task_id: str | None
    supersedes_request_id: str | None
    #: 내가 이 항목을 목록에서 정리했는가. **서버가 답한다** — 화면의 기억은 새로 열면 사라진다.
    list_entry_hidden: bool
    conditions: dict[str, JsonValue] | None


class WorkRequestReferenceView(TypedDict):
    reference_id: str
    created_by: str
    task: TaskSummaryView | None


class WorkRequestDetailResult(WorkRequestMutationResult):
    references: list[WorkRequestReferenceView]
    source_meeting_title: str | None


class WorkRequestEvidenceView(TypedDict):
    evidence_id: str
    submission_id: str
    submission_version: int
    attachment_id: str
    name: str
    content_type: str
    size_bytes: int
    evidence_role: str
    fixed_snapshot_ref: str
    adopted_by: str
    adopted_at: str


class WorkRequestEvidenceResult(WorkRequestEvidenceView):
    request_version: int


class WorkRequestDecisionItemView(TypedDict):
    decision_item_id: str
    kind: str
    status: str
    due_at: str | None


class WorkRequestSubmissionView(TypedDict):
    submission_id: str
    submission_version: int
    revises_id: str | None
    submitted_by: str
    submitted_at: str
    snapshot: dict[str, JsonValue]
    subject_version: int | None
    diff: dict[str, JsonValue] | None
    evidence: list[ActionEvidenceView]
    evidence_hash: str


class WorkRequestReviewAssignmentView(TypedDict):
    review_assignment_id: str
    submission_id: str
    reviewer_member_id: str
    status: str
    assigned_at: str


class WorkRequestReviewDecisionView(TypedDict):
    review_decision_id: str
    submission_id: str
    actor_member_id: str
    decision: str
    reason: str | None
    conditions: dict[str, JsonValue] | None
    evidence_hash: str | None
    decided_at: str


class WorkRequestActivityView(TypedDict):
    event_kind: str
    actor_id: str
    safe_summary: str
    reason: str | None
    occurred_at: str


class WorkRequestTimelineRecords(TypedDict):
    request_thread_id: str | None
    decision_item: WorkRequestDecisionItemView | None
    submissions: list[WorkRequestSubmissionView]
    review_assignments: list[WorkRequestReviewAssignmentView]
    review_decisions: list[WorkRequestReviewDecisionView]
    activity: list[WorkRequestActivityView]


class WorkRequestHistoryResult(WorkRequestTimelineRecords):
    request: WorkRequestMutationResult
    comments: list[ActionDiscussionView]
    evidence: list[WorkRequestEvidenceView]
