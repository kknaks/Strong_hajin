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


def test_the_first_screen_is_already_a_graph_of_what_this_person_is_connected_to(tmp_path) -> None:
    """빈 검색 상자가 아니라, 지금 연결되어 있는 것들이 먼저 보인다."""
    client, _ = _stack(tmp_path)
    title = "첫 화면에 보일 업무"
    _journey(client, title)
    meeting = client.post(
        "/api/meetings",
        headers=JIHO,
        json={
            "organization_id": "scax",
            "title": "그래프에 보일 회의",
            "starts_at": "2026-09-10T01:00:00Z",
            "ends_at": "2026-09-10T02:00:00Z",
            "visibility": "private",
            "attendee_ids": ["mina"],
        },
    ).json()

    overview = client.get("/api/graph/overview", headers=JIHO).json()
    kinds = {node["kind"] for node in overview["nodes"]}
    assert {"person", "team", "task", "work_request", "meeting"} <= kinds
    assert overview["center"] == {"kind": "person", "id": "jiho", "title": "지호 (팀장)", "state": None, "date": None}
    assert meeting["meeting_id"] in {node["id"] for node in overview["nodes"] if node["kind"] == "meeting"}
    # 자기가 속한 팀은 원장에 있는 소속에서 나온다.
    assert "product" in {node["id"] for node in overview["nodes"] if node["kind"] == "team"}

    # 모든 edge는 어느 쪽에서 읽느냐만 다르고, 어느 원장이 말하는 사실인지 함께 온다.
    holds = next(edge for edge in overview["edges"] if edge["kind"] == "holds")
    assert (holds["label"], holds["inverse_label"], holds["provenance"]) == ("담당함", "담당자", "task_assignment")
    assert all(edge.get("provenance") for edge in overview["edges"])
    assert overview["available_views"] == ["member", "team"]


def test_grouping_by_team_reads_the_same_answer_one_level_up(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    _journey(client, "팀으로 묶어 볼 업무")
    grouped = client.get("/api/graph/overview", headers=JIHO, params={"view": "team"}).json()

    assert grouped["view"] == "team"
    # 사람은 자기 팀으로 접힌다. 팀 node는 조직 원장의 canonical unit이다.
    assert not any(node["kind"] == "person" for node in grouped["nodes"])
    assert "team:product" in {f"{node['kind']}:{node['id']}" for node in grouped["nodes"]}
    # 같은 팀 안에서만 이어지는 연결은 그 팀의 내부 사정이므로 감춘다.
    assert all(edge["from"] != edge["to"] for edge in grouped["edges"])
    # 같은 방향·종류의 연결은 개수로 접힌다.
    assert all(edge.get("count", 1) >= 1 for edge in grouped["edges"])


def test_a_meeting_says_who_was_there_and_what_came_out_of_it(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    meeting = client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "organization_id": "scax",
            "title": "연결을 볼 회의",
            "starts_at": "2026-09-10T01:00:00Z",
            "ends_at": "2026-09-10T02:00:00Z",
            "visibility": "private",
            "attendee_ids": ["jiho"],
        },
    ).json()

    neighbors = client.get("/api/graph/neighbors", headers=MINA, params={"node": f"meeting:{meeting['meeting_id']}"}).json()
    assert neighbors["center"]["kind"] == "meeting"
    kinds = {(edge["kind"], edge["from"], edge["to"]) for edge in neighbors["edges"]}
    assert ("owns_meeting", "person:mina", f"meeting:{meeting['meeting_id']}") in kinds
    assert ("attended", "person:jiho", f"meeting:{meeting['meeting_id']}") in kinds

    # 볼 수 없는 회의는 이웃 조회에서도 존재를 말하지 않는다.
    assert client.get("/api/graph/neighbors", headers=SORA, params={"node": f"meeting:{meeting['meeting_id']}"}).status_code in {403, 404}


def test_a_person_node_shows_only_the_connections_the_asker_may_already_read(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    title = "민아만 아는 업무"
    client.post("/api/tasks", headers=MINA, json={"title": title})

    mine = client.get("/api/graph/neighbors", headers=MINA, params={"node": "person:mina"}).json()
    assert title in {node["title"] for node in mine["nodes"]}
    assert "team:product" in {f"{node['kind']}:{node['id']}" for node in mine["nodes"]}

    theirs = client.get("/api/graph/neighbors", headers=JIHO, params={"node": "person:mina"}).json()
    assert title not in str(theirs)


def test_a_report_stands_on_the_work_it_was_written_from(tmp_path) -> None:
    """보고–출처는 초안이 실제로 담은 업무에서만 나온다. 남의 보고는 그래프에 없다."""
    client, application = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "보고에 담길 업무"}).json()
    client.post(f"/api/tasks/{task['task_id']}/start", headers=MINA, json={"expected_version": task["version"]})
    generated = client.post("/api/daily-reports/generate-draft", headers=MINA, json={"report_date": _today()})
    assert generated.status_code in {200, 201}, generated.text
    report_id = generated.json()["report_id"]

    around = client.get("/api/graph/neighbors", headers=MINA, params={"node": f"report:{report_id}"}).json()
    assert around["center"]["kind"] == "report"
    cited = [edge for edge in around["edges"] if edge["kind"] == "cites"]
    assert cited and cited[0]["to"] == f"task:{task['task_id']}"
    assert (cited[0]["label"], cited[0]["inverse_label"], cited[0]["provenance"]) == (
        "근거로 삼은 업무",
        "이 업무를 담은 보고",
        "daily_report_draft",
    )
    # The same fact reads from the work's side too.
    from_task = client.get("/api/graph/neighbors", headers=MINA, params={"node": f"task:{task['task_id']}"}).json()
    assert any(edge["kind"] == "cites" and edge["from"] == f"report:{report_id}" for edge in from_task["edges"])

    # A report is its writer's own. Nobody else can walk into it, and it never names them.
    denied = client.get("/api/graph/neighbors", headers=JIHO, params={"node": f"report:{report_id}"})
    assert denied.status_code in {403, 404}
    assert "보고" not in str(client.get("/api/graph/overview", headers=JIHO).json())


def test_a_file_says_which_work_carries_it(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "자료가 붙은 업무"}).json()
    material = client.post(
        f"/api/tasks/{task['task_id']}/materials/links",
        headers=MINA,
        json={"kind": "output", "url": "https://docs.example.com/spec", "label": "설계 문서"},
    ).json()

    around = client.get("/api/graph/neighbors", headers=MINA, params={"node": f"material:{material['material_id']}"}).json()
    assert around["center"]["kind"] == "material" and around["center"]["title"] == "설계 문서"
    assert any(edge["kind"] == "has_material" and edge["from"] == f"task:{task['task_id']}" for edge in around["edges"])
    # Someone who cannot read the work cannot reach the file through it either.
    assert client.get("/api/graph/neighbors", headers=JIHO, params={"node": f"material:{material['material_id']}"}).status_code in {403, 404}


def _today() -> str:
    from datetime import UTC, datetime

    from ax_workspace.platform.work_tasks import business_date

    return business_date(datetime.now(UTC))


def test_each_node_says_where_it_sits_in_time_when_the_ledger_plans_one(tmp_path) -> None:
    """기간으로 좁혀 보려면 날짜가 필요하다. 그 날짜는 원장이 계획한 것이지 만들어 낸 것이 아니다."""
    client, _ = _stack(tmp_path)
    dated = client.post(
        "/api/tasks", headers=MINA, json={"title": "기한이 있는 업무", "due_date": "2026-09-30"}
    ).json()
    client.post("/api/tasks", headers=MINA, json={"title": "날짜가 없는 업무"})
    meeting = client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "organization_id": "scax", "title": "시간이 정해진 회의",
            "starts_at": "2026-09-10T01:00:00Z", "ends_at": "2026-09-10T02:00:00Z",
            "visibility": "private", "attendee_ids": [],
        },
    ).json()

    nodes = {f"{node['kind']}:{node['id']}": node for node in client.get("/api/graph/overview", headers=MINA).json()["nodes"]}
    assert nodes[f"task:{dated['task_id']}"]["date"] == "2026-09-30"
    assert nodes[f"meeting:{meeting['meeting_id']}"]["date"] == "2026-09-10"
    # 계획한 날짜가 없으면 없는 채로 둔다 — 만든 날짜를 계획인 척하지 않는다.
    undated = [node for node in nodes.values() if node["kind"] == "task" and node["title"] == "날짜가 없는 업무"]
    assert undated and undated[0]["date"] is None
    assert nodes["person:mina"]["date"] is None
