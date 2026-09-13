"""Public read operations return the same authorized answer over HTTP and MCP."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from ax_workspace.entrypoints.reset_demo import reset_database


@pytest.mark.parametrize(
    "persona,tool,arguments,path",
    [
        ("mina", "member_directory", {}, "/api/organization/members"),
        ("mina", "organization_tree", {}, "/api/organization/tree"),
        (
            "mina",
            "organization_unit_members",
            {"unit_id": "product"},
            "/api/organization/units/product/members",
        ),
        (
            "mina",
            "organization_member_detail",
            {"member_id": "jiho"},
            "/api/organization/members/jiho",
        ),
        (
            "mina",
            "organization_member_history",
            {"member_id": "mina", "axis": "membership"},
            "/api/organization/members/mina/history?axis=membership",
        ),
        ("yuna", "organization_activity", {}, "/api/organization/activity"),
        ("mina", "my_organization_profile", {}, "/api/organization/me"),
        ("yuna", "installed_access_roles", {}, "/api/access/roles"),
        ("yuna", "member_access", {"member_id": "mina"}, "/api/access/members/mina"),
        (
            "mina",
            "daily_report_status",
            {"report_date": "2026-09-11"},
            "/api/daily-reports/status?report_date=2026-09-11",
        ),
        ("mina", "list_material_folders", {}, "/api/material-folders"),
        ("mina", "list_notifications", {}, "/api/notifications"),
        ("jiho", "list_projects", {}, "/api/projects"),
        ("jiho", "sent_task_assignments", {}, "/api/task-assignments/sent"),
        ("mina", "work_request_cc_candidates", {}, "/api/work-request-cc-candidates"),
        ("mina", "conversations", {}, "/api/conversations"),
    ],
)
def test_registered_query_matches_http(tmp_path, persona, tool, arguments, path):
    url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(url)
    settings = Settings(
        RuntimeProfile.TEST, url, materials_dir=str(tmp_path / "materials")
    )
    client = TestClient(create_app(settings))
    response = client.get(path, headers={"X-Demo-Persona": persona})
    assert response.status_code == 200, response.text
    server = _create_bound_persona_server(McpReportsFacade(settings, persona))
    discovered = {item.name for item in asyncio.run(server.list_tools())}
    assert tool in discovered
    result = asyncio.run(server.call_tool(tool, arguments))
    assert not result.is_error
    payload = result.structured_content
    if isinstance(response.json(), list):
        payload = payload["result"]
    assert payload == response.json()


def test_open_tools_return_authorized_browser_links_and_never_file_bytes(tmp_path):
    url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(url)
    settings = Settings(
        RuntimeProfile.TEST, url, materials_dir=str(tmp_path / "materials")
    )
    client = TestClient(create_app(settings))
    headers = {"X-Demo-Persona": "mina"}
    task = client.post(
        "/api/tasks", headers=headers, json={"title": "첨부 조회"}
    ).json()
    folder = client.post(
        "/api/material-folders",
        headers=headers,
        json={"kind": "personal", "title": "자료"},
    ).json()
    for tool, owner, target in (
        (
            "open_task_material",
            {"task_id": task["task_id"]},
            f"/api/tasks/{task['task_id']}/materials",
        ),
        (
            "open_folder_material",
            {"folder_id": folder["folder_id"]},
            f"/api/material-folders/{folder['folder_id']}/materials",
        ),
    ):
        uploaded = client.post(
            target,
            headers=headers,
            data={"kind": "input"},
            files={"file": ("source.txt", b"private-original-bytes", "text/plain")},
        )
        assert uploaded.status_code == 201, uploaded.text
        args = {**owner, "material_id": uploaded.json()["material_id"]}
        server = _create_bound_persona_server(McpReportsFacade(settings, "mina"))
        result = asyncio.run(server.call_tool(tool, args))
        assert not result.is_error
        opened = result.structured_content
        assert "private-original-bytes" not in str(result)
        response = client.get(opened["open_url"], headers=headers)
        assert (
            response.status_code == 200
            and response.content == b"private-original-bytes"
        )
        outsider = _create_bound_persona_server(McpReportsFacade(settings, "sora"))
        from mcp.server.mcpserver.exceptions import ToolError

        with pytest.raises(ToolError):
            asyncio.run(outsider.call_tool(tool, args))


def test_missing_and_unreadable_queries_have_the_same_not_found_response(tmp_path):
    from uuid import uuid4
    from mcp.server.mcpserver.exceptions import ToolError

    url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(url)
    settings = Settings(RuntimeProfile.TEST, url)
    client = TestClient(create_app(settings))
    mina = {"X-Demo-Persona": "mina"}
    request = client.post(
        "/api/work-requests",
        headers=mina,
        json={"title": "요청 비밀", "assignee_id": "jiho"},
    ).json()
    conversation = client.post(
        "/api/conversations", headers=mina, json={"title": "대화 비밀"}
    ).json()
    server = _create_bound_persona_server(McpReportsFacade(settings, "minseok"))
    for tool, key, path, identifier in (
        ("work_request_get", "request_id", "/api/work-requests", request["request_id"]),
        (
            "conversation",
            "conversation_id",
            "/api/conversations",
            conversation["conversation_id"],
        ),
    ):
        responses = []
        for target in (identifier, str(uuid4())):
            response = client.get(
                f"{path}/{target}", headers={"X-Demo-Persona": "minseok"}
            )
            assert response.status_code == 404, response.text
            responses.append(response.json())
            with pytest.raises(ToolError, match="대상을 찾을 수 없습니다"):
                asyncio.run(server.call_tool(tool, {key: target}))
        assert responses == [{"detail": "대상을 찾을 수 없습니다"}] * 2


def test_removed_scope_switches_are_rejected_instead_of_silently_ignored(tmp_path):
    from mcp.server.mcpserver.exceptions import ToolError

    url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(url)
    server = _create_bound_persona_server(
        McpReportsFacade(Settings(RuntimeProfile.TEST, url), "mina")
    )
    for tool, arguments in [
        ("task_list", {"mine": True}),
        ("meeting_list", {"include_visible": True}),
    ]:
        with pytest.raises(ToolError, match="Unknown arguments"):
            asyncio.run(server.call_tool(tool, arguments))


def test_query_capability_denial_is_not_an_empty_success(tmp_path):
    from dataclasses import replace
    from ax_workspace.modules.reports.application import DailyReportAccessDenied
    from ax_workspace.modules.work.projects import ProjectAccessDenied

    url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(url)
    settings = Settings(RuntimeProfile.TEST, url)
    application = create_app(settings).state.workflow_application
    principal = replace(
        application.authenticated_principal("mina"), capabilities=frozenset(), grants=()
    )
    with pytest.raises(DailyReportAccessDenied):
        application.daily_report_recent(principal)
    with pytest.raises(ProjectAccessDenied):
        application.list_projects(principal)


def test_selected_queries_and_native_downloads_use_the_same_owning_reads(tmp_path):
    from uuid import UUID
    from test_mcp import ContractTestAiProvider
    from ax_workspace.platform.native_materials import material_id_for

    url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(url)
    settings = Settings(
        RuntimeProfile.TEST, url, materials_dir=str(tmp_path / "materials")
    )
    client = TestClient(create_app(settings, report_provider=ContractTestAiProvider()))
    app = client.app.state.workflow_application
    principal = app.authenticated_principal("mina")
    headers = {"X-Demo-Persona": "mina"}
    task = client.post(
        "/api/tasks", headers=headers, json={"title": "처음 업무"}
    ).json()
    edited = client.patch(
        f"/api/tasks/{task['task_id']}",
        headers=headers,
        json={"expected_version": task["version"], "title": "바뀐 업무"},
    )
    assert edited.status_code == 200, edited.text
    project = client.post(
        "/api/projects", headers={"X-Demo-Persona": "jiho"}, json={"name": "참여 범위"}
    ).json()
    joined = client.post(
        f"/api/projects/{project['project_id']}/members",
        headers={"X-Demo-Persona": "jiho"},
        json={"member_id": "mina"},
    )
    assert joined.status_code == 201, joined.text
    folder = client.post(
        "/api/material-folders",
        headers=headers,
        json={"kind": "personal", "title": "원본"},
    ).json()
    uploaded = client.post(
        f"/api/material-folders/{folder['folder_id']}/materials",
        headers=headers,
        files={"file": ("selected.txt", b"selectedcontent", "text/plain")},
    ).json()
    conversation = client.post(
        "/api/conversations", headers=headers, json={"title": "저장 대화"}
    ).json()
    meeting = client.post(
        "/api/meetings",
        headers=headers,
        json={
            "title": "회의",
            "starts_at": "2026-09-11T01:00:00Z",
            "ends_at": "2026-09-11T02:00:00Z",
        },
    ).json()["meeting"]
    attached = client.post(
        f"/api/meetings/{meeting['meeting_id']}/materials",
        headers=headers,
        files=[("files", ("meeting.md", b"# meeting material", "text/markdown"))],
    )
    assert attached.status_code == 201, attached.text
    note_material = attached.json()["attached"][0]["material_id"]
    draft = app.generate_daily_report_draft(principal, "2026-09-11")
    submission = app.submit_daily_report(
        principal, draft["report_id"], draft["draft_id"], draft["draft_version"], None
    )
    report_material = str(
        material_id_for("report_submission", UUID(submission["submission_id"]))
    )
    request = client.post(
        "/api/work-requests",
        headers=headers,
        json={"title": "근거 요청", "assignee_id": "jiho"},
    ).json()
    evidence = client.post(
        f"/api/work-requests/{request['request_id']}/evidence",
        headers=headers,
        files={"file": ("evidence.txt", b"requestevidence", "text/plain")},
    ).json()
    server = _create_bound_persona_server(
        McpReportsFacade(settings, "mina", ContractTestAiProvider())
    )
    cases = [
        (
            "task_history_diff",
            {"task_id": task["task_id"], "before": 1, "after": 2},
            f"/api/tasks/{task['task_id']}/history/diff?from=1&to=2",
        ),
        (
            "get_project",
            {"project_id": project["project_id"]},
            f"/api/projects/{project['project_id']}",
        ),
        (
            "list_folder_materials",
            {"folder_id": folder["folder_id"]},
            f"/api/material-folders/{folder['folder_id']}/materials",
        ),
        (
            "material_metadata",
            {"material_id": uploaded["material_id"]},
            f"/api/materials/{uploaded['material_id']}",
        ),
        (
            "conversation",
            {"conversation_id": conversation["conversation_id"]},
            f"/api/conversations/{conversation['conversation_id']}",
        ),
    ]
    for tool, args, path in cases:
        result = asyncio.run(server.call_tool(tool, args))
        expected = client.get(path, headers=headers)
        assert expected.status_code == 200, expected.text
        assert not result.is_error
        assert (
            result.structured_content["result"]
            if isinstance(expected.json(), list)
            else result.structured_content
        ) == expected.json()
    recent = asyncio.run(
        server.call_tool("daily_report_recent", {"limit": 1})
    ).structured_content["result"]
    assert recent == app.daily_report_recent(principal, limit=1)
    for tool, args in [
        (
            "open_meeting_material",
            {"meeting_id": meeting["meeting_id"], "material_id": note_material},
        ),
        (
            "open_report_material",
            {"report_id": draft["report_id"], "material_id": report_material},
        ),
        (
            "open_work_request_attachment",
            {
                "request_id": request["request_id"],
                "attachment_id": evidence["attachment_id"],
            },
        ),
    ]:
        result = asyncio.run(server.call_tool(tool, args))
        assert not result.is_error
        downloaded = client.get(result.structured_content["open_url"], headers=headers)
        assert downloaded.status_code == 200 and downloaded.content
        assert result.structured_content["size_bytes"] == len(downloaded.content)


def test_scoped_member_history_and_access_queries_hide_unreadable_targets(tmp_path):
    url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(url)
    settings = Settings(RuntimeProfile.TEST, url)
    client = TestClient(create_app(settings))
    for member in ("jiho", "missing-member"):
        response = client.get(
            f"/api/organization/members/{member}/history?axis=membership",
            headers={"X-Demo-Persona": "mina"},
        )
        assert response.status_code == 404
        assert response.json() == {"detail": "대상을 찾을 수 없습니다"}
    missing_access = client.get(
        "/api/access/members/missing-member", headers={"X-Demo-Persona": "yuna"}
    )
    assert missing_access.status_code == 404
    missing_unit = client.get(
        "/api/organization/units/missing-unit/members",
        headers={"X-Demo-Persona": "mina"},
    )
    assert missing_unit.status_code == 404


@pytest.mark.parametrize(
    "owner,method",
    [
        (
            "ax_workspace.modules.meetings.application.MeetingApplication",
            "list",
        ),
        ("ax_workspace.modules.work.requests.WorkRequestApplication", "list"),
        ("ax_workspace.modules.work.projects.ProjectApplication", "list"),
    ],
)
def test_graph_does_not_turn_an_owning_read_failure_into_empty_success(
    tmp_path, monkeypatch, owner, method
):
    url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(url)
    settings = Settings(RuntimeProfile.TEST, url)

    def fail(*args, **kwargs):
        raise RuntimeError("read store unavailable")

    monkeypatch.setattr(f"{owner}.{method}", fail)
    facade = McpReportsFacade(settings, "mina")
    with pytest.raises(RuntimeError, match="read store unavailable"):
        facade.graph_search("없는 검색어")
