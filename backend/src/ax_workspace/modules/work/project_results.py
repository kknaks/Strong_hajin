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
