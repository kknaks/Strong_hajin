"""연결을 따라가는 것과 권한을 넘겨받는 것은 다르다.

A manager can follow how work came about — a request someone sent, the Task it became, the parts of that Task, the
materials attached to it. Every step is re-checked against what that person may already read: the graph shows how
things connect, it never hands out access, and it never counts or names what someone may not see.
"""
from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
SORA = {"X-Demo-Persona": "sora"}
ADMIN = {"X-Demo-Persona": "yuna"}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    app = create_app(settings)
    return TestClient(app), app.state.workflow_application


def _journey(client, title: str = "그래프가 따라갈 업무") -> dict:
    """The shape the graph is for: someone asked, someone accepted, and the work grew parts and materials."""
    request = client.post("/api/work-requests", headers=MINA, json={"title": title, "assignee_id": "jiho"}).json()
    [item] = [row for row in client.get("/api/action-items", headers=JIHO).json() if row["subject"] == title]
    client.post(
        f"/api/action-items/{item['action_item_id']}/commands/accept",
        headers=JIHO,
        json={"expected_version": item["expected_version"]},
    )
    [task] = [row for row in client.get("/api/my-work", headers=JIHO).json() if row["title"] == title]
    child = client.post(
        "/api/tasks", headers=JIHO, json={"title": f"{title} 하위", "parent_task_id": task["task_id"]}
    ).json()
    material = client.post(
        f"/api/tasks/{task['task_id']}/materials/links",
        headers=JIHO,
        json={"kind": "output", "url": "https://docs.example.com/graph", "label": "결과 문서"},
    ).json()
    return {"request": request, "task": task, "child": child, "material": material}


def test_a_manager_can_follow_how_the_work_came_about(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    made = _journey(client)

    found = client.get("/api/graph/search", headers=JIHO, params={"q": "그래프가 따라갈"})
    assert found.status_code == 200, found.text
    kinds = {row["kind"] for row in found.json()["nodes"]}
    assert {"work_request", "task"} <= kinds
    center = next(row for row in found.json()["nodes"] if row["kind"] == "task" and row["id"] == made["task"]["task_id"])
    assert center["title"] == "그래프가 따라갈 업무"

    around = client.get("/api/graph/neighbors", headers=JIHO, params={"node": f"task:{made['task']['task_id']}"})
    assert around.status_code == 200, around.text
    body = around.json()
    edges = {(row["kind"], row["from"], row["to"]) for row in body["edges"]}
    assert ("produced", f"work_request:{made['request']['request_id']}", f"task:{made['task']['task_id']}") in edges
    assert ("holds", "person:jiho", f"task:{made['task']['task_id']}") in edges
    assert ("parent_of", f"task:{made['task']['task_id']}", f"task:{made['child']['task_id']}") in edges
    assert any(kind == "has_material" for kind, _, _ in edges)
    # Every node named in an edge is described, so nothing is a bare id on screen.
    described = {f"{row['kind']}:{row['id']}" for row in body["nodes"]}
    for _kind, source, target in edges:
        assert source in described and target in described

    # One step at a time: the parts of a part are not pulled in unasked.
    assert all(row["id"] != made["child"]["task_id"] or row["kind"] == "task" for row in body["nodes"])


def test_the_graph_never_hands_out_what_a_person_may_not_read(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    made = _journey(client, "볼 수 없는 업무")

    # Someone who may read work in general, but has no relationship to this work, finds none of it.
    assert client.get("/api/graph/search", headers=MINA, params={"q": "볼 수 없는 업무 하위"}).json()["nodes"] == []
    walked = client.get("/api/graph/neighbors", headers=MINA, params={"node": f"task:{made['child']['task_id']}"})
    assert walked.status_code in {403, 404}
    assert made["child"]["title"] not in walked.text

    # The requester may follow their own request to the work it became, but not into its parts.
    theirs = client.get("/api/graph/neighbors", headers=MINA, params={"node": f"work_request:{made['request']['request_id']}"})
    assert theirs.status_code == 200, theirs.text
    assert any(row["kind"] == "task" and row["id"] == made["task"]["task_id"] for row in theirs.json()["nodes"])
    into = client.get("/api/graph/neighbors", headers=MINA, params={"node": f"task:{made['task']['task_id']}"})
    assert into.status_code == 200
    assert made["child"]["title"] not in into.text


def test_following_connections_needs_the_capability_to_do_it(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    made = _journey(client, "권한을 확인할 업무")
    # 소라 may read neither work nor requests, so the surface is not hers at all — not even empty.
    refused = client.get("/api/graph/search", headers=SORA, params={"q": "권한"})
    assert refused.status_code == 403
    assert client.get("/api/graph/neighbors", headers=SORA, params={"node": f"task:{made['task']['task_id']}"}).status_code == 403
    assert client.get("/api/graph/search", headers=ADMIN, params={"q": "권한을 확인할"}).status_code == 200


def test_a_search_answers_with_bounded_results(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    for index in range(8):
        client.post("/api/tasks", headers=JIHO, json={"title": f"많이 있는 업무 {index}"})

    bounded = client.get("/api/graph/search", headers=JIHO, params={"q": "많이 있는", "limit": 3}).json()
    assert len(bounded["nodes"]) == 3
    assert bounded["truncated"] is True
    # An empty question is not a search for everything.
    assert client.get("/api/graph/search", headers=JIHO, params={"q": "  "}).status_code == 422


def test_a_delegated_turn_walks_the_same_authorized_graph(tmp_path, monkeypatch) -> None:
    from ax_workspace.entrypoints.mcp import McpReportsFacade

    monkeypatch.delenv("AX_MCP_CAUSATION_ID", raising=False)
    client, _ = _stack(tmp_path)
    made = _journey(client, "AX가 따라갈 업무")
    database_url = client.app.state.workflow_application._settings.database_url
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))

    jiho = McpReportsFacade(settings, "jiho")
    found = jiho.graph_search("AX가 따라갈")
    assert any(row["id"] == made["task"]["task_id"] for row in found["nodes"])
    walked = jiho.graph_neighbors(f"task:{made['task']['task_id']}")
    assert any(row["kind"] == "parent_of" for row in walked["edges"])

    # A persona who may not read work at all does not get the tools' answers either.
    sora = McpReportsFacade(settings, "sora")
    try:
        sora.graph_search("AX가 따라갈")
    except Exception as error:
        assert "capability" in str(error)
    else:
        raise AssertionError("a persona without work access walked the graph")


def test_a_turn_keeps_a_record_of_where_it_actually_walked(tmp_path, monkeypatch) -> None:
    """The path shown in a chat is what the tools really returned, and it survives coming back later."""
    from uuid import UUID

    from ax_workspace.entrypoints.mcp import McpReportsFacade
    from ax_workspace.platform.persistence import ConversationTurnRecord, make_session_factory

    client, _ = _stack(tmp_path)
    made = _journey(client, "발자국이 남는 업무")
    database_url = client.app.state.workflow_application._settings.database_url
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))

    conversation = client.post("/api/conversations", headers=JIHO, json={"title": "관계 질문"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**JIHO, "Idempotency-Key": "graph-turn"},
        json={"body": "이 업무가 어디서 왔는지 알려줘", "context": []},
    )
    with make_session_factory(database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"])).execution_id
    monkeypatch.setenv("AX_MCP_CAUSATION_ID", str(execution_id))

    facade = McpReportsFacade(settings, "jiho")
    facade.graph_search("발자국이 남는")
    facade.graph_neighbors(f"task:{made['task']['task_id']}")
    monkeypatch.delenv("AX_MCP_CAUSATION_ID", raising=False)

    detail = client.get(f"/api/conversations/{conversation['conversation_id']}", headers=JIHO).json()
    steps = detail["graph_receipts"]
    assert [row["kind"] for row in steps][0] == "node"
    assert any(row["kind"] == "edge" and row["edge_kind"] == "produced" for row in steps)
    assert all(row["turn_id"] == accepted.json()["turn_id"] for row in steps)
    # Every step names something readable, and the order is the order it was walked.
    assert [row["sequence"] for row in steps] == sorted(row["sequence"] for row in steps)
    assert any(row["node_title"] == "발자국이 남는 업무" for row in steps if row["kind"] == "node")

    # Nothing is written for a walk that was not taken.
    other = client.post("/api/conversations", headers=JIHO, json={"title": "다른 대화"}).json()
    assert client.get(f"/api/conversations/{other['conversation_id']}", headers=JIHO).json()["graph_receipts"] == []
