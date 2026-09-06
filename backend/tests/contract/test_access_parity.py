"""한 사람이 한 자원에 대해 받는 답은 창구가 달라도 같다.

The same person asking about the same thing must get the same answer through the browser's HTTP API, through the
delegated MCP tools, and through the relation graph. Where the answer is no, none of those channels may leak the
thing's title, its file names, its node, or even a count.
"""
import asyncio
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from ax_workspace.bootstrap.material_worker import MaterialExtractionWorker
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.mcp import McpReportsFacade
from ax_workspace.entrypoints.reset_demo import reset_database


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    return TestClient(create_app(settings)), settings


def _http_task(client: TestClient, member: str, task_id: str):
    return client.get(f"/api/tasks/{task_id}", headers={"X-Demo-Persona": member})


def test_every_channel_gives_the_same_answer_about_the_same_work(tmp_path) -> None:
    client, settings = _stack(tmp_path)
    secret = "재무 정산 대사 자료"
    task = client.post("/api/tasks", headers={"X-Demo-Persona": "minseok"}, json={"title": secret}).json()

    # 대표 may read the organization's work: every channel opens it, and calls it read-only.
    assert _http_task(client, "yuna", task["task_id"]).json()["access"] == "read_only"
    executive = McpReportsFacade(settings, "yuna")
    assert executive.get_task(task["task_id"])["title"] == secret
    assert task["task_id"] in {row["task_id"] for row in executive.list_tasks()}
    found = executive.graph_search(secret[:4])
    assert task["task_id"] in {node["id"] for node in found["nodes"] if node["kind"] == "task"}

    # 팀장 leads another team: every channel refuses, and none of them says the title, the node, or a count.
    lead = McpReportsFacade(settings, "jiho")
    assert _http_task(client, "jiho", task["task_id"]).status_code == 404
    with pytest.raises(Exception):
        lead.get_task(task["task_id"])
    assert task["task_id"] not in {row["task_id"] for row in lead.list_tasks()}
    lead_graph = lead.graph_search(secret[:4])
    assert secret not in str(lead_graph)
    assert lead_graph["nodes"] == [] and lead_graph.get("truncated") in (False, None)
    with pytest.raises(Exception):
        lead.graph_neighbors(f"task:{task['task_id']}")
    assert secret not in str(client.get("/api/graph/search", headers={"X-Demo-Persona": "jiho"}, params={"q": secret[:4]}).json())


def test_every_channel_gives_the_same_answer_about_the_same_file(tmp_path) -> None:
    """자료도 업무와 같다 — 창구가 달라도 답이 같고, 아니오일 때는 어디서도 이름도 건수도 나오지 않는다.

    본문 검색은 파일 이름에 없는 말로 파일을 찾아 준다. 그래서 여기서 새는 것은 제목이 아니라 문서 안이다.
    """
    client, settings = _stack(tmp_path)
    secret = "공급사는 한빛상사이고 납기일은 2026-09-30입니다. " * 20
    task = client.post("/api/tasks", headers={"X-Demo-Persona": "minseok"}, json={"title": "재무 정산 대사"}).json()
    uploaded = client.post(
        f"/api/tasks/{task['task_id']}/materials",
        headers={"X-Demo-Persona": "minseok"},
        data={"kind": "input"},
        files={"file": ("정산.md", secret.encode(), "text/markdown")},
    )
    assert uploaded.status_code == 201, uploaded.text
    application = client.app.state.workflow_application
    worker = MaterialExtractionWorker(settings, queue_factory=lambda session: application.memory_job_queue)
    while asyncio.run(worker.run_once()):
        pass

    # 대표는 조직의 일을 읽는다: 세 창구가 같은 발췌를 준다.
    executive = McpReportsFacade(settings, "yuna")
    over_http = client.get("/api/materials/search", headers={"X-Demo-Persona": "yuna"}, params={"q": "한빛상사"}).json()
    over_mcp = executive.search_task_materials(None, "한빛상사")
    assert {row["name"] for row in over_http["results"]} == {row["name"] for row in over_mcp["results"]} == {"정산.md"}
    # 그리고 찾은 파일을 목록에서도 본다 — 본문만 보이고 파일은 없는 자리를 만들지 않는다.
    listed = client.get(f"/api/tasks/{task['task_id']}/materials", headers={"X-Demo-Persona": "yuna"})
    assert [row["name"] for row in listed.json()] == ["정산.md"]

    # 다른 팀의 팀장에게는 어느 창구에서도 아무것도 없다.
    lead = McpReportsFacade(settings, "jiho")
    refused = client.get("/api/materials/search", headers={"X-Demo-Persona": "jiho"}, params={"q": "한빛상사"}).json()
    assert refused["results"] == [] and refused["searched_materials"] == 0
    assert "정산" not in str(refused)
    denied = lead.search_task_materials(None, "한빛상사")
    assert denied["results"] == [] and denied["searched_materials"] == 0
    assert "정산" not in str(denied)
    assert client.get(f"/api/tasks/{task['task_id']}/materials", headers={"X-Demo-Persona": "jiho"}).status_code == 404


def test_a_capability_taken_away_is_taken_away_everywhere_at_once(tmp_path) -> None:
    client, settings = _stack(tmp_path)
    task = client.post("/api/tasks", headers={"X-Demo-Persona": "minseok"}, json={"title": "회수 확인용 업무"}).json()
    application = client.app.state.workflow_application
    yuna = application.authenticated_principal("yuna")

    assert _http_task(client, "yuna", task["task_id"]).status_code == 200
    assert "task_get" in {tool.name for tool in _tool_names(settings, "yuna")}

    role = application.set_role_capabilities(
        yuna,
        "role:executive",
        [capability for capability in sorted(yuna.capabilities) if capability not in {"task.read", "work.read.all"}],
        expected_version=1,
        reason="조회 권한 축소",
    )
    assert role["version"] == 2

    # The same second: the HTTP route, the tool list, and the graph all stop offering it.
    assert _http_task(client, "yuna", task["task_id"]).status_code == 403
    assert "task_get" not in {tool.name for tool in _tool_names(settings, "yuna")}
    assert client.get("/api/graph/search", headers={"X-Demo-Persona": "yuna"}, params={"q": "회수"}).status_code == 403


def _tool_names(settings: Settings, persona: str):
    import asyncio
    import os

    from ax_workspace.entrypoints.mcp import create_mcp_server

    previous = os.environ.get("AX_MCP_PERSONA")
    os.environ["AX_MCP_PERSONA"] = persona
    try:
        return asyncio.run(create_mcp_server(settings).list_tools())
    finally:
        if previous is None:
            os.environ.pop("AX_MCP_PERSONA", None)
        else:
            os.environ["AX_MCP_PERSONA"] = previous
