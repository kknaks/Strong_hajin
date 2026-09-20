"""Task mutation results; relationship-rich query views add their own fields."""
from typing import Literal
from pydantic import JsonValue
from typing_extensions import NotRequired, TypedDict


class TaskAssignmentView(TypedDict):
    assignment_id: str
    kind: str
    status: str
    assigned_by: str | None
    accepted_at: str | None


class TaskLineageView(TypedDict):
    request_thread_id: str | None
    source_work_request_id: str | None
    source_decision_item_id: str | None
    source_submission_id: str | None
    source_review_decision_id: str | None
    source_action_item_id: str | None
    source_task_id: str | None


class TaskBlockingChildView(TypedDict):
    """상위 완료를 막는 하위 하나 — 이름을 내야 사람이 다음 걸음을 고른다."""

    task_id: str
    title: str
    #: `unfinished`(아직 안 끝남) | `awaiting_approval`(끝났지만 요청자 승인 전)
    why: str


class TaskDerivedView(TypedDict):
    """**서버가 만드는 파생 표시** (SPEC-003 §4 Data). 화면이 `state` 로 되짚지 않는다.

    `reply`·`status_note` 는 **키만 있고 값은 `null`** 이다 — SPEC-001 에서 이어받은 이름이지만 그
    원장(문의·회신 대기·상태 메모)이 아직 구현되지 않았다. 없는 것을 있다고 내지 않는다.
    """

    assignment: str | None
    approval: str | None
    proposal: str | None
    blocking_children: list[TaskBlockingChildView]
    reply: str | None
    status_note: str | None
    overdue_days: int | None


class TaskMutationResult(TypedDict):
    task_id: str
    title: str
    #: **넷뿐이다** — `open` · `in_progress` · `done` · `cancelled`.
    state: str
    version: int
    block_reason: str | None
    description: str | None
    start_date: str | None
    due_date: str | None
    created_at: str | None
    updated_at: str | None
    organization_unit_id: str | None
    project_id: str | None
    origin_kind: str
    visibility: str
    #: `direct` | `request_rejected` | `request_withdrawn` | `cancellation_agreed`
    cancel_reason: str | None
    #: **실제** 시작 시각. `start_date`(계획)와 다른 사실이다.
    started_at: str | None
    completed_at: str | None
    reopened_at: str | None
    assignment: TaskAssignmentView | None
    #: 참조자 — **읽기와 논의만** 열린다. 업무 요청의 같은 이름과 같은 뜻이다 (`modules/work/parties.py`).
    cc_member_ids: list[str]
    #: **선행업무 — 활성인 것만** (SPEC-001 §4 Data Contract). 상위·참고와 다른 세 번째 관계이고
    #: **간트 연결선의 유일한 원천**이다. 뗀 관계는 행으로 남지만 여기 서지 않는다.
    preceding_task_ids: list[str]
    #: 승인자 0..1 — 화면 라벨은 「결재자」고 같은 값이다 (SPEC-001 §7 OQ-N).
    approver_id: str | None
    derived: TaskDerivedView | None
    lineage: TaskLineageView


class TaskPredecessorView(TypedDict):
    """선행 하나의 요약. **볼 수 없는 선행은 `title`·`state` 가 비고 자리만 남는다** (SPEC-001 §4).

    자료 구획과 다르다: 자료는 건수도 내지 않지만 선행은 **시작을 막는 이유**라, 이유를 숨기면
    사람이 다음 걸음을 고를 수 없다. **제목은 감추고 건수는 낸다** — 배열 길이가 그 건수다.
    """

    task_id: str
    title: str | None
    state: str | None


class ChecklistItemView(TypedDict):
    item_id: str
    text: str
    position: int
    done: bool
    state: str
    version: int
    created_by: str
    completed_by: str | None
    completed_at: str | None


class ChecklistMutationResult(ChecklistItemView):
    task_version: int


class ChecklistOrderResult(TypedDict):
    task_version: int
    checklist: list[ChecklistItemView]


class TaskAssignmentResult(TypedDict):
    assignment_id: str
    assignment_kind: str
    status: str
    assignee_id: str
    assigned_by: str | None
    decline_reason: str | None
    created_at: str | None
    accepted_at: str | None
    declined_at: str | None
    task: TaskMutationResult


class TaskMemberView(TypedDict):
    member_id: str
    display_name: str


class TaskOriginSource(TypedDict):
    type: str
    id: str
    title: str | None


class TaskOriginView(TypedDict):
    kind: str
    actor_role: str | None
    actor: TaskMemberView | None
    source: TaskOriginSource | None


class TaskProgressView(TypedDict):
    done: int
    total: int


class TaskChildProgressView(TypedDict):
    """하위 진행 — `blocking` 이 0이어야 상위를 끝낼 수 있다.

    **`blocking=0` 이 완료를 보장하지는 않는다**: 이 숫자는 읽을 수 있는 하위만 센 투영이고, 완료를
    막는 검사는 읽을 수 없는 하위까지 본다. 화면은 이 값으로 단추를 열되 409 를 정상 응답으로 받는다.
    """

    done: int
    blocking: int
    cancelled: int
    total: int


class TaskListEntry(TaskMutationResult):
    checklist_progress: TaskProgressView
    origin: TaskOriginView | None
    assignee: TaskMemberView | None


class TaskParentView(TypedDict):
    task_id: str
    title: str
    state: str


class TaskSummaryView(TaskParentView):
    due_date: str | None
    assignee: TaskMemberView | None
    #: 하위 한 줄도 자기 파생 표시를 갖는다 — 승인 대기인지 아닌지가 그 줄에서 읽혀야 한다.
    derived: NotRequired[TaskDerivedView | None]


class TaskReferenceView(TypedDict):
    reference_id: str
    created_by: str
    created_at: str | None
    task: TaskSummaryView | None


class TaskDeliveryView(TypedDict):
    action_item_id: str
    status: str
    rounds: int
    reported_by: str
    reported_at: str | None
    summary: str | None
    last_reason: str | None


class TaskDetailResult(TaskMutationResult):
    origin: TaskOriginView | None
    assignee: TaskMemberView | None
    parent: TaskParentView | None
    children: list[TaskSummaryView]
    child_progress: TaskChildProgressView
    delivery: TaskDeliveryView | None
    access: Literal['owner', 'read_only']
    #: 각 선행의 제목·상태. `preceding_task_ids` 와 **같은 순서·같은 길이**다.
    predecessors: list[TaskPredecessorView]
    checklist: NotRequired[list[ChecklistItemView]]
    checklist_progress: NotRequired[TaskProgressView]
    references: NotRequired[list[TaskReferenceView]]


class TaskVersionView(TypedDict):
    version: int
    change_kind: str
    actor_id: str
    reason: str | None
    captured_at: str
    snapshot: dict[str, JsonValue]


class TaskCausationView(TypedDict):
    kind: str
    id: str


class TaskActivityView(TypedDict):
    event_kind: str
    actor: TaskMemberView | None
    actor_kind: str
    summary: str
    reason: str | None
    version: int | None
    causation: TaskCausationView | None
    occurred_at: str


class TaskHistoryResult(TypedDict):
    task_id: str
    versions: list[TaskVersionView]
    activity: list[TaskActivityView]


class TaskValueChange(TypedDict):
    before: JsonValue
    after: JsonValue


class TaskCollectionChange(TypedDict):
    added: list[str]
    removed: list[str]


class TaskChangesView(TypedDict, total=False):
    title: TaskValueChange
    description: TaskValueChange
    state: TaskValueChange
    block_reason: TaskValueChange
    start_date: TaskValueChange
    due_date: TaskValueChange
    assignee: TaskValueChange
    checklist: TaskCollectionChange
    materials: TaskCollectionChange


TaskHistoryDiffResult = TypedDict('TaskHistoryDiffResult', {
    'task_id': str, 'from': int, 'to': int, 'changes': TaskChangesView,
})


class TaskSubtasksResult(TypedDict):
    task_id: str
    parent: TaskParentView | None
    children: list[TaskSummaryView]
    progress: TaskChildProgressView | None


class TaskChecklistResult(TypedDict):
    task_id: str
    checklist: list[ChecklistItemView]
    progress: TaskProgressView | None


class TaskReferenceResult(TaskReferenceView):
    task_version: int


class TaskReferenceReleaseResult(TypedDict):
    reference_id: str
    task_version: int


class TaskCompletionResult(TaskMutationResult):
    delivery: TaskDeliveryView | None
