"""E2E-12 — AX 가 업무를 만들 때 사람이 「새 업무 추가」 창에서 고르듯 프로젝트·관련 업무를 찾아 채운다.

문구가 곧 동작이다(모델은 이것만 읽는다). 그래서 무엇이 사라지면 안 되는지를 여기서 잡는다.
"""
from ax_workspace.modules.ax_execution.tool_catalog import TOOL_CATALOG
from ax_workspace.platform.claude_cli import ClaudeCliProviderAdapter
from ax_workspace.platform.codex_cli import CodexCliProviderAdapter


def test_drafting_work_looks_up_project_and_related_tasks_and_never_invents_them() -> None:
    policy = CodexCliProviderAdapter.WORK_AND_REPORT_ROUTING_POLICY
    for tool in ("task_create_self", "work_request_create", "list_projects", "graph_search", "graph_neighbors", "task_list"):
        assert f"`{tool}" in policy, tool
    for field in ("project_id", "parent_task_id", "preceding_task_ids", "reference_task_ids"):
        assert field in policy, field
    assert "연결할 프로젝트를 찾지 못했습니다" in policy
    assert "지어내지 않는다" in policy


def test_the_no_graph_first_rule_is_about_finding_people_not_about_drafting_work() -> None:
    policy = CodexCliProviderAdapter.WORK_AND_REPORT_ROUTING_POLICY
    assert "사람을 찾으려고** `graph_search`를 먼저 호출하지 않는다" in policy
    assert "관계 자체를 묻지 않은 한 `graph_search`를 먼저 호출하지 않는다" not in policy


def test_creation_tool_descriptions_ask_for_the_lookup_and_forbid_invention() -> None:
    for name in ("task_create_self", "work_request_create"):
        description = TOOL_CATALOG[name].description
        assert "Before drafting, look up" in description, name
        assert "never invent an ID" in description, name
        assert "list_projects" in description and "graph_neighbors" in description, name


def test_project_candidates_are_not_limited_to_meetings() -> None:
    project_list = TOOL_CATALOG["project_list"]
    assert "회의용" not in project_list.title
    assert "Task or WorkRequest" in project_list.description
    assert "project:<project_id>" in TOOL_CATALOG["list_projects"].description


def test_graph_search_says_its_only_arguments_and_that_there_is_no_kind_filter() -> None:
    description = TOOL_CATALOG["graph_search"].description
    assert "Arguments are exactly `query`" in description and "no kind filter" in description
    relationship = CodexCliProviderAdapter.RELATIONSHIP_POLICY
    assert "`graph_search`의 인자는 `query`(찾을 이름)와 `limit` 둘뿐이다" in relationship
    assert "graph_search 시작 종류는" not in relationship


def test_answers_may_point_at_projects_by_their_own_kind() -> None:
    policy = CodexCliProviderAdapter.ANSWER_PRESENTATION_POLICY
    assert "project:<project_id>" in policy
    assert "프로젝트를 task:로 가리키지 않는다" in policy


def test_the_claude_adapter_reads_the_same_policies() -> None:
    for name in ("RELATIONSHIP_POLICY", "WORK_AND_REPORT_ROUTING_POLICY", "ANSWER_PRESENTATION_POLICY"):
        assert getattr(ClaudeCliProviderAdapter, name) is getattr(CodexCliProviderAdapter, name), name
