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


class TaskMutationResult(TypedDict):
    task_id: str
    title: str
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
    assignment: TaskAssignmentView | None
    lineage: TaskLineageView


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
    child_progress: TaskProgressView
    delivery: TaskDeliveryView | None
    access: Literal['owner', 'read_only']
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
    progress: TaskProgressView | None


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
