"""Request mutation receipts; detailed timelines and discussions are separate queries."""
from typing import Literal
from typing_extensions import TypedDict
from pydantic import JsonValue
from ax_workspace.modules.actions.results import ActionDiscussionView, ActionEvidenceView
from ax_workspace.modules.work.material_results import TaskMaterialView
from ax_workspace.modules.work.task_results import TaskSummaryView


class WorkRequestMaterialView(TaskMaterialView):
    """요청에 붙은 자료 한 건 — **업무 자료와 같은 모양**이고 자리만 하나 더 붙는다.

    `request_id` 는 그 자료가 붙어 있는 요청이고, `task_id` 는 그 요청이 세운 업무다. 수락하면 같은
    Attachment 가 두 자리에 함께 서므로(요청 binding 보존 + 업무 binding 추가) 두 값이 모두 뜻을 갖는다.
    """

    request_id: str
    #: 아직 업무가 서지 않은 요청 행(W1 이전 모양)에서는 비어 있다 — 업무 자료와 달리 **없을 수 있다.**
    task_id: str | None


class WorkRequestMaterialResult(WorkRequestMaterialView):
    """붙이고 뗀 결과 — 요청이 옮겨 간 회차를 함께 낸다(업무 자료의 `task_version` 과 같은 자리)."""

    request_version: int


class WorkRequestMutationResult(TypedDict):
    request_id: str
    request_thread_id: str | None
    submission_version: int | None
    title: str
    description: str | None
    #: **계획 시작일** — 내 업무와 같은 공통 payload 의 칸이다 (`WorkPayloadFields`).
    start_date: str | None
    due_date: str | None
    #: 어느 프로젝트의 일로 보냈는가. 비어 있는 것이 정상이다.
    project_id: str | None
    #: 결재자 0..1 — 이 요청이 세우는 업무의 `approver_id` 와 **같은 값**이다.
    approver_id: str | None
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
    #: **이것이 끝나야 시작한다** — 요청이 세운 업무의 선행이다 (SPEC-001 §4). 생성 입력으로 받던
    #: 값이 조회로 돌아오지 않아 상세에서 「무엇 다음인가」를 그릴 수 없었다.
    preceding_task_ids: list[str]
    #: 함께 보낸 참고 업무. `references` 는 그 업무를 **지금 읽을 수 있을 때만** 내용까지 내지만,
    #: 이 목록은 무엇을 가리켰는지 자체다.
    reference_task_ids: list[str]
    #: 요청에 붙인 자료. 요청이 업무가 될 때 함께 넘어가므로 보낸 쪽·받는 쪽 모두 여기서 확인한다.
    materials: list[WorkRequestMaterialView]


class WorkRequestReadReceiptResult(TypedDict):
    """참고 항목 읽음의 영수증 (SPEC-001 §4). **요청 투영이 아니다** — 회차도 상태도 싣지 않는다.

    읽음은 요청 행을 바꾸지 않으므로 여기에 `version` 을 실으면 바뀌지 않은 값을 바뀐 것처럼 보이게 한다.
    """

    request_id: str
    read: bool
    #: **처음 읽은 시각.** 두 번째 호출도 같은 값이다.
    read_at: str


class WorkRequestInboxEntry(WorkRequestMutationResult):
    category: Literal["work", "reference"]


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
