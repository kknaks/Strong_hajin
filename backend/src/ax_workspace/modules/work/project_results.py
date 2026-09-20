"""Projects, current participation and closed participation rounds are separate views."""
from typing_extensions import TypedDict


class ProjectView(TypedDict):
    project_id: str
    name: str
    description: str | None
    state: str
    starts_on: str | None
    ends_on: str | None
    external_key: str | None
    version: int


class ProjectAssignmentView(TypedDict):
    assignment_id: str
    member_id: str
    assignment_kind: str
    valid_from: str | None
    valid_until: str | None


class ProjectMemberView(ProjectAssignmentView):
    display_name: str


class ProjectTaskView(TypedDict):
    task_id: str
    title: str
    state: str
    start_date: str | None
    due_date: str | None
    parent_task_id: str | None
    #: **간트 연결선의 유일한 원천** (SPEC-001 U-15 · §4). 이 줄이 함께 내므로 선행을 묻는 조회를
    #: 따로 만들지 않는다. 상위–하위는 다른 그림이라 `parent_task_id` 와 섞지 않는다.
    preceding_task_ids: list[str]


class ProjectDetailResult(ProjectView):
    may_manage: bool
    members: list[ProjectMemberView]
    tasks: list[ProjectTaskView]


class ProjectAssignmentHistoryView(ProjectAssignmentView):
    assigned_by_member_id: str | None
    created_at: str | None
    ended_at: str | None
    ended_by_member_id: str | None
    end_reason: str | None


class ProjectParticipationView(ProjectAssignmentHistoryView):
    display_name: str
    assigned_by_display_name: str | None
    ended_by_display_name: str | None
