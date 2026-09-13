from uuid import UUID

import pytest

from ax_workspace.modules.organization_access.domain import Principal, TASK_READ
from ax_workspace.modules.work.graph import GraphApplication, GraphError


TASK_ID = UUID("00000000-0000-0000-0000-000000000101")
CHILD_ID = UUID("00000000-0000-0000-0000-000000000102")
PROJECT_ID = UUID("00000000-0000-0000-0000-000000000201")
REQUEST_ID = UUID("00000000-0000-0000-0000-000000000301")
PARENT_ID = UUID("00000000-0000-0000-0000-000000000401")


def _principal() -> Principal:
    return Principal("mina", "민아", frozenset(), frozenset({TASK_READ}))


class FakeGraphSource:
    def __init__(self) -> None:
        self.people_rows: list[dict] = []
        self.unit_rows: list[dict] = []
        self.project_rows: list[dict] = []
        self.task_rows: list[dict] = []
        self.request_rows: list[dict] = []
        self.meeting_rows: list[dict] = []
        self.tasks_by_id: dict[UUID, dict | None] = {}
        self.people_by_id: dict[str, dict] = {}
        self.projects_by_id: dict[UUID, dict | None] = {}
        self.requests_by_id: dict[UUID, dict | None] = {}
        self.last_people_query: str | None = None

    def people(self, principal, *, query):
        self.last_people_query = query
        return self.people_rows

    def organization_units(self):
        return self.unit_rows

    def readable_projects(self, principal):
        return self.project_rows

    def readable_tasks(self, principal, *, query=None, assignee_id=None, limit=50):
        return self.task_rows

    def readable_requests(self, principal, *, query=None):
        return self.request_rows

    def readable_meetings(self, principal, *, query=None, member_id=None, limit=50):
        return self.meeting_rows

    def readable_task(self, principal, task_id):
        return self.tasks_by_id.get(task_id)

    def readable_project(self, principal, project_id):
        return self.projects_by_id.get(project_id)

    def readable_request(self, principal, request_id):
        return self.requests_by_id.get(request_id)

    def person(self, member_id):
        return self.people_by_id.get(member_id)

    def task_materials(self, principal, task_id):
        return []

    def own_reports(self, principal, *, limit=3):
        return []


def test_search_normalizes_the_question_and_bounds_one_ordered_answer() -> None:
    source = FakeGraphSource()
    source.people_rows = [{"member_id": "mina", "display_name": "민아"}]
    source.unit_rows = [{"id": "team-1", "name": "마케팅 계획팀"}]
    source.task_rows = [{"task_id": TASK_ID, "title": "마케팅 계획", "state": "open"}]

    answer = GraphApplication(source).search(_principal(), "  마케팅   계획  ", limit=2)

    assert source.last_people_query == "마케팅 계획"
    assert answer["query"] == "마케팅 계획"
    assert [(node["kind"], node["title"]) for node in answer["nodes"]] == [
        ("person", "민아"),
        ("team", "마케팅 계획팀"),
    ]
    assert answer["truncated"] is True
    with pytest.raises(GraphError, match="찾을 내용을 입력하세요"):
        GraphApplication(source).search(_principal(), "   ")


def test_task_neighbors_drop_each_link_the_source_cannot_reauthorize() -> None:
    source = FakeGraphSource()
    source.people_by_id["mina"] = {"member_id": "mina", "display_name": "민아"}
    source.tasks_by_id[TASK_ID] = {
        "task_id": TASK_ID,
        "title": "분기 보고",
        "state": "in_progress",
        "assignee": {"member_id": "mina"},
        "project_id": PROJECT_ID,
        "lineage": {"source_work_request_id": REQUEST_ID},
        "parent": {"task_id": PARENT_ID},
        "children": [{"task_id": CHILD_ID, "title": "자료 수집", "state": "open"}],
        "references": [],
    }
    source.projects_by_id[PROJECT_ID] = None
    source.requests_by_id[REQUEST_ID] = None
    source.tasks_by_id[PARENT_ID] = None

    answer = GraphApplication(source).neighbors(_principal(), f"task:{TASK_ID}")

    assert {f"{node['kind']}:{node['id']}" for node in answer["nodes"]} == {
        f"task:{TASK_ID}",
        "person:mina",
        f"task:{CHILD_ID}",
    }
    assert [(edge["kind"], edge["from"], edge["to"]) for edge in answer["edges"]] == [
        ("holds", "person:mina", f"task:{TASK_ID}"),
        ("parent_of", f"task:{TASK_ID}", f"task:{CHILD_ID}"),
    ]


def test_neighbors_accept_only_a_supported_kind_with_a_canonical_identity() -> None:
    application = GraphApplication(FakeGraphSource())

    for node_ref in ("conversation:123", "action:123", "draft:123", "task:", "project:2026", "2026"):
        with pytest.raises(GraphError):
            application.neighbors(_principal(), node_ref)
