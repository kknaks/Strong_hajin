"""연결을 따라가는 것과 권한을 넘겨받는 것은 다르다.

A manager can follow how work came about — a request someone sent, the Task it became, the parts of that Task, the
materials attached to it. Every step is re-checked against what that person may already read: the graph shows how
things connect, it never hands out access, and it never counts or names what someone may not see.
"""
from fastapi.testclient import TestClient
import pytest

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
HYEON = {"X-Demo-Persona": "hyeon"}  # 인사 — 제품팀 밖
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


def test_a_person_is_a_place_to_start_from(tmp_path) -> None:
    """누군가의 일을 물으면 그 사람에서 걸어 나간다. 이름은 명부가 이미 모두에게 열어 둔 것이다."""
    client, _ = _stack(tmp_path)

    found = client.get("/api/graph/search", headers=JIHO, params={"q": "민아"}).json()

    people = [node for node in found["nodes"] if node["kind"] == "person"]
    assert people and all("민아" in node["title"] for node in people)
    # 그리고 그 자리에서 한 걸음 나갈 수 있다 — 나가는 연결은 거기서 다시 판정된다.
    around = client.get("/api/graph/neighbors", headers=JIHO, params={"node": f"person:{people[0]['id']}"})
    assert around.status_code == 200


def test_a_position_reference_is_resolved_by_the_server_not_the_display_name(tmp_path) -> None:
    """호칭과 직책은 provider 문구나 이름 장식이 아니라 조직 원장에서 해석한다."""
    from ax_workspace.entrypoints.mcp import McpReportsFacade
    from ax_workspace.platform.persistence import MemberRecord, make_session_factory

    client, application = _stack(tmp_path)
    with make_session_factory(application._settings.database_url)() as session:
        session.get(MemberRecord, "jiho").display_name = "지호"
        session.get(MemberRecord, "hyeon").display_name = "직책이 아닌 팀장 표기"
        session.commit()

    response = client.get("/api/graph/search", headers=MINA, params={"q": "우리 팀장님"})

    assert response.status_code == 200, response.text
    people = [node for node in response.json()["nodes"] if node["kind"] == "person"]
    assert people == [
        {
            "kind": "person",
            "id": "jiho",
            "title": "지호",
            "state": None,
            "date": None,
            "match": {
                "kind": "position",
                "label": "팀장",
                "organization_id": "product",
                "organization_name": "제품팀",
            },
        }
    ]
    settings = application._settings
    assert McpReportsFacade(settings, "mina").graph_search("우리 팀장님") == response.json()
    around = client.get("/api/graph/neighbors", headers=MINA, params={"node": "person:jiho"})
    assert any(
        edge["kind"] == "belongs_to" and edge["to"] == "team:product"
        for edge in around.json()["edges"]
    )


def test_a_name_honorific_uses_the_existing_person_identity(tmp_path) -> None:
    client, _ = _stack(tmp_path)

    found = client.get("/api/graph/search", headers=JIHO, params={"q": "민아님"}).json()

    assert [(node["kind"], node["id"]) for node in found["nodes"]] == [("person", "mina")]
    assert "match" not in found["nodes"][0]


def test_position_candidates_stay_current_scoped_and_ambiguous(tmp_path) -> None:
    from datetime import UTC, datetime, timedelta

    from ax_workspace.platform.persistence import AppointmentRecord, MemberRecord, make_session_factory

    client, application = _stack(tmp_path)
    now = datetime.now(UTC)
    with make_session_factory(application._settings.database_url)() as session:
        # 활성 보직이어도 Mina의 실제 소속 밖이면 `우리 팀장님` 후보가 아니다.
        session.add(
            AppointmentRecord(
                member_id="sora",
                organization_id="legal",
                role_id="role:team-lead",
                position_definition_id="team-lead",
            )
        )
        # 같은 제품팀 보직이어도 이미 끝났다면 현재 관계가 아니다.
        session.add(
            AppointmentRecord(
                member_id="hyeon",
                organization_id="product",
                role_id="role:team-lead",
                position_definition_id="team-lead",
                valid_from=now - timedelta(days=2),
                valid_until=now - timedelta(days=1),
            )
        )
        # member 원장이 비활성이면 유효한 appointment만으로 되살리지 않는다.
        session.get(MemberRecord, "minseok").record_status = "inactive"
        session.add(
            AppointmentRecord(
                member_id="minseok",
                organization_id="product",
                role_id="role:team-lead",
                position_definition_id="team-lead",
            )
        )
        session.commit()

    first = client.get("/api/graph/search", headers=MINA, params={"q": "우리 팀장님"}).json()
    assert [node["id"] for node in first["nodes"] if node["kind"] == "person"] == ["jiho"]

    with make_session_factory(application._settings.database_url)() as session:
        session.add(
            AppointmentRecord(
                member_id="yuna",
                organization_id="product",
                role_id="role:team-lead",
                position_definition_id="team-lead",
            )
        )
        session.commit()

    ambiguous = client.get("/api/graph/search", headers=MINA, params={"q": "우리 팀장님"}).json()
    assert [node["id"] for node in ambiguous["nodes"] if node["kind"] == "person"] == ["jiho", "yuna"]


def test_a_position_reference_can_reach_the_heads_of_the_current_org_ancestors(tmp_path) -> None:
    from ax_workspace.platform.persistence import AppointmentRecord, make_session_factory

    client, application = _stack(tmp_path)
    with make_session_factory(application._settings.database_url)() as session:
        session.add(
            AppointmentRecord(
                member_id="yuna",
                organization_id="product-division",
                role_id="role:executive",
                position_definition_id="division-head",
            )
        )
        session.commit()

    found = client.get("/api/graph/search", headers=MINA, params={"q": "저희 본부장님"}).json()

    people = [node for node in found["nodes"] if node["kind"] == "person"]
    assert [node["id"] for node in people] == ["yuna"]
    assert people[0]["match"]["organization_id"] == "product-division"


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
    material_ref = f"material:{made['material']['material_id']}"
    assert any(row["edge_kind"] == "has_material" and row["to_ref"] == material_ref for row in steps)

    # The graph identity is a binding, even though canonical content search uses an artifact identity.
    detached = client.post(f'/api/tasks/{made['task']['task_id']}/material-bindings/{made['material']['binding_id']}/detach', headers=JIHO)
    assert detached.status_code == 200, detached.text
    after = client.get(f"/api/conversations/{conversation['conversation_id']}", headers=JIHO).json()["graph_receipts"]
    assert all(material_ref not in (row.get("node_ref"), row.get("from_ref"), row.get("to_ref")) for row in after)

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
            "title": "그래프에 보일 회의",
            "starts_at": "2026-09-10T01:00:00Z",
            "ends_at": "2026-09-10T02:00:00Z",
            "attendee_ids": ["mina"],
        },
    ).json()["meeting"]

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


def test_the_first_screen_connects_the_things_it_already_shows(tmp_path) -> None:
    """요청과 그것이 된 업무, 업무와 그 업무가 들고 있는 자료는 첫 화면에서도 이어져 있어야 한다.

    둘 다 이미 그려지는데 선만 없으면 같은 일이 흩어진 점으로 보인다. 이웃 조회에서만 이어지는 것은 이어져
    있다고 말하기 어렵다.
    """
    client, _ = _stack(tmp_path)
    made = _journey(client, "첫 화면이 이어야 할 업무")

    overview = client.get("/api/graph/overview", headers=JIHO).json()
    edges = {(edge["kind"], edge["from"], edge["to"]) for edge in overview["edges"]}
    kinds = {node["kind"] for node in overview["nodes"]}

    assert ("produced", f"work_request:{made['request']['request_id']}", f"task:{made['task']['task_id']}") in edges
    assert "material" in kinds
    assert any(kind == "has_material" for kind, _, _ in edges)


def test_each_view_answers_one_question_and_not_the_next_one(tmp_path) -> None:
    """구성원 보기는 내 주변을, 팀으로 묶기는 조직을 답한다.

    일이 하나도 없어도 어디에 속해 있고 누구와 나란히 있는지는 내 주변이다. 하지만 팀 위에 무엇이 있는지는
    조직도의 질문이고, 그것까지 첫 화면에 세우면 모두의 조상인 회사 node가 화면의 모든 것과 이어져 그림이
    아니라 바퀴가 된다.
    """
    client, _ = _stack(tmp_path)

    overview = client.get("/api/graph/overview", headers=JIHO).json()
    edges = {(edge["kind"], edge["from"], edge["to"]) for edge in overview["edges"]}

    # 내 주변: 내 자리와 나란히 선 사람들.
    assert ("belongs_to", "person:jiho", "team:product") in edges
    colleagues = {
        edge[1] for edge in edges if edge[0] == "belongs_to" and edge[2] == "team:product" and edge[1] != "person:jiho"
    }
    assert colleagues, "같은 팀 사람들이 팀에 붙어 있지 않습니다"
    # 조직 계층은 여기 없다 — 그 질문에는 다음 보기가 답한다.
    assert not any(kind == "under" for kind, _, _ in edges)

    # 프로젝트는 조직 단위와 나란한 두 번째 축이므로 소속과 같은 자격으로 내 옆에 선다.
    project = client.post(
        "/api/projects", headers=JIHO, json={"name": "한빛 통합 마케팅"}
    ).json()
    inside = client.post(
        "/api/tasks", headers=JIHO, json={"title": "프로젝트에 매달린 일", "project_id": project["project_id"]}
    ).json()
    # 만들면 담당자로 함께 기록되므로, 만든 사람 옆에는 곧바로 선다.
    mine = client.get("/api/graph/overview", headers=JIHO).json()
    with_project = {(edge["kind"], edge["from"], edge["to"]) for edge in mine["edges"]}
    assert ("assigned_to", "person:jiho", f"project:{project['project_id']}") in with_project
    # 그 프로젝트의 일이라는 것도 잇는다 — 접는 것이 아니라 잇는 것이므로 업무는 낱개로 남는다.
    assert ("part_of", f"task:{inside['task_id']}", f"project:{project['project_id']}") in with_project
    assert any(node["id"] == inside["task_id"] for node in mine["nodes"] if node["kind"] == "task")

    grouped = client.get("/api/graph/overview", headers=JIHO, params={"view": "team"}).json()
    upward = {edge["from"]: edge["to"] for edge in grouped["edges"] if edge["kind"] == "under"}
    current, walked = "team:product", []
    while current in upward:
        current = upward[current]
        walked.append(current)
    assert walked[-1] == "team:scax" and len(walked) > 1, walked
    # 접힌 팀은 자기가 몇 사람을 담고 있는지 말한다.
    product = next(node for node in grouped["nodes"] if node["kind"] == "team" and node["id"] == "product")
    assert product["folded"] >= 1


def test_grouping_by_team_reads_the_same_answer_one_level_up(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    _journey(client, "팀으로 묶어 볼 업무")
    grouped = client.get("/api/graph/overview", headers=JIHO, params={"view": "team"}).json()

    assert grouped["view"] == "team"
    # 사람은 자기 팀으로 접힌다. 팀 node는 조직 원장의 canonical unit이다. 회사 뿌리에만 있는 사람은
    # 붙일 팀이 없어 그대로 남는다 — 그 규칙은 아래 test가 따로 고정한다.
    assert "jiho" not in {node["id"] for node in grouped["nodes"] if node["kind"] == "person"}
    assert "team:product" in {f"{node['kind']}:{node['id']}" for node in grouped["nodes"]}
    # 같은 팀 안에서만 이어지는 연결은 그 팀의 내부 사정이므로 감춘다.
    assert all(edge["from"] != edge["to"] for edge in grouped["edges"])
    # 같은 방향·종류의 연결은 개수로 접힌다.
    assert all(edge.get("count", 1) >= 1 for edge in grouped["edges"])


def test_someone_who_sits_only_at_the_top_stays_where_they_are(tmp_path) -> None:
    """접는 것은 팀이 있는 사람까지다. 회사 뿌리에만 있는 사람에게 없는 팀을 만들어 붙이지 않는다."""
    client, _ = _stack(tmp_path)
    _journey(client, "대표가 들어오는 업무")
    starts = "2026-09-20T01:00:00+00:00"
    client.post(
        "/api/meetings",
        headers=JIHO,
        json={
            "title": "대표가 참석하는 회의",
            "starts_at": starts, "ends_at": "2026-09-20T02:00:00+00:00",
            "attendee_ids": ["yuna"],
        },
    )

    member = client.get("/api/graph/overview", headers=JIHO).json()
    grouped = client.get("/api/graph/overview", headers=JIHO, params={"view": "team"}).json()

    people = lambda answer: {node["id"] for node in answer["nodes"] if node["kind"] == "person"}
    # 팀에 앉은 사람은 사라지고 그 팀이 대신 선다.
    assert "jiho" in people(member) and "jiho" not in people(grouped)
    assert "product" in {node["id"] for node in grouped["nodes"] if node["kind"] == "team"}
    # 대표는 회사 아래 팀에 앉아 있지 않으므로 자기 자리에 그대로 남는다.
    assert "yuna" in people(member) and "yuna" in people(grouped)


def test_grouping_by_project_folds_work_and_leaves_the_rest_alone(tmp_path) -> None:
    """프로젝트로 묶으면 그 프로젝트의 일이 하나로 접힌다. 프로젝트 없는 일은 접히지 않고 그대로 남는다.

    팀 보기가 사람을 접는 것과 같은 동작이되 접히는 것이 다르다 — 프로젝트는 사람이 아니라 일을 묶는다.
    """
    client, _ = _stack(tmp_path)
    # 프로젝트가 하나도 없으면 프로젝트로 묶는 것을 제안하지도 않는다.
    assert client.get("/api/graph/overview", headers=JIHO).json()["available_views"] == ["member", "team"]

    project = client.post(
        "/api/projects", headers=JIHO, json={"name": "한빛 통합 마케팅"}
    ).json()
    inside = client.post(
        "/api/tasks", headers=JIHO, json={"title": "홈페이지 디자인 기획", "project_id": project["project_id"]}
    )
    assert inside.status_code == 201, inside.text
    client.post("/api/tasks", headers=JIHO, json={"title": "프로젝트 없는 실무"})

    assert "project" in client.get("/api/graph/overview", headers=JIHO).json()["available_views"]
    grouped = client.get("/api/graph/overview", headers=JIHO, params={"view": "project"}).json()
    assert grouped["view"] == "project"
    refs = {f"{node['kind']}:{node['id']}" for node in grouped["nodes"]}
    assert f"project:{project['project_id']}" in refs
    titles = {node["title"] for node in grouped["nodes"]}
    assert "홈페이지 디자인 기획" not in titles, "프로젝트의 일이 접히지 않았습니다"
    assert "프로젝트 없는 실무" in titles, "프로젝트 없는 일까지 사라졌습니다"


def test_a_project_someone_may_not_read_never_folds_their_view(tmp_path) -> None:
    """읽을 수 없는 프로젝트로는 접지 않는다. 접었다면 그 프로젝트의 이름이 드러났을 것이다."""
    client, _ = _stack(tmp_path)
    project = client.post(
        "/api/projects", headers=JIHO, json={"name": "이름이 새면 안 되는 프로젝트"}
    ).json()
    client.post("/api/tasks", headers=JIHO, json={"title": "그 안의 일", "project_id": project["project_id"]})

    outsider = client.get("/api/graph/overview", headers=HYEON, params={"view": "project"}).json()
    assert "이름이 새면 안 되는 프로젝트" not in {node["title"] for node in outsider["nodes"]}
    assert "project" not in outsider["available_views"]


def test_a_meeting_says_who_was_there_and_what_came_out_of_it(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    meeting = client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "title": "연결을 볼 회의",
            "starts_at": "2026-09-10T01:00:00Z",
            "ends_at": "2026-09-10T02:00:00Z",
            "attendee_ids": ["jiho"],
        },
    ).json()["meeting"]

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


def test_team_and_project_are_searchable_authorized_start_nodes(tmp_path) -> None:
    from ax_workspace.entrypoints.mcp import McpReportsFacade

    client, application = _stack(tmp_path)
    project = client.post("/api/projects", headers=JIHO, json={"name": "탐색 2026 프로젝트"}).json()
    facade = McpReportsFacade(Settings(RuntimeProfile.TEST, f"sqlite:///{tmp_path / 'demo.db'}"), "jiho")
    for query, kind, identifier in [("제품", "team", "product"), ("탐색 2026", "project", project["project_id"])]:
        response = client.get("/api/graph/search", headers=JIHO, params={"q": query})
        assert response.status_code == 200
        answer = response.json()
        assert any(node["kind"] == kind and node["id"] == identifier for node in answer["nodes"])
        assert answer == facade.graph_search(query)
        assert answer == application.graph_search(facade.principal, query)
    hidden = client.get("/api/graph/search", headers=HYEON, params={"q": "탐색 2026"}).json()
    assert hidden == {"query": "탐색 2026", "nodes": [], "truncated": False}


def test_project_walks_live_assignments_and_authorized_work_one_hop(tmp_path) -> None:
    from ax_workspace.entrypoints.mcp import McpReportsFacade

    client, application = _stack(tmp_path)
    project = client.post("/api/projects", headers=JIHO, json={"name": "프로젝트 한 단계"}).json()
    project_id = project["project_id"]
    task = client.post("/api/tasks", headers=JIHO, json={"title": "프로젝트 업무", "project_id": project_id}).json()
    client.post(f"/api/projects/{project_id}/members", headers=JIHO, json={"member_id": "mina"})
    facade = McpReportsFacade(Settings(RuntimeProfile.TEST, f"sqlite:///{tmp_path / 'demo.db'}"), "mina")
    ref = f"project:{project_id}"
    response = client.get("/api/graph/neighbors", headers=MINA, params={"node": ref})
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer == facade.graph_neighbors(ref) == application.graph_neighbors(facade.principal, ref)
    edges = {(edge["kind"], edge["from"], edge["to"]) for edge in answer["edges"]}
    assert edges == {("assigned_to", "person:jiho", ref), ("assigned_to", "person:mina", ref), ("part_of", f"task:{task['task_id']}", ref)}
    from dataclasses import replace
    from ax_workspace.modules.organization_access.domain import TASK_READ

    project_only = replace(facade.principal, capabilities=facade.principal.capabilities - {TASK_READ})
    limited = application.graph_neighbors(project_only, ref, limit=2)
    assert limited["truncated"] is False
    assert {edge["kind"] for edge in limited["edges"]} == {"assigned_to"}
    assert task["task_id"] not in str(limited) and task["title"] not in str(limited)
    assert client.get("/api/graph/neighbors", headers=HYEON, params={"node": ref}).status_code == 404
    client.delete(f"/api/projects/{project_id}/members/mina", headers=JIHO)
    assert client.get("/api/graph/neighbors", headers=MINA, params={"node": ref}).status_code == 404
    assert facade.graph_search("프로젝트 한 단계")["nodes"] == []


def test_project_connections_respect_assignment_time_in_seoul(tmp_path, monkeypatch) -> None:
    import time
    from datetime import UTC, datetime, timedelta

    with monkeypatch.context() as environment:
        environment.setenv("TZ", "Asia/Seoul")
        time.tzset()
        try:
            client, _ = _stack(tmp_path)
            project = client.post("/api/projects", headers=JIHO, json={"name": "참여 유효기간"}).json()
            pid = project["project_id"]
            future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
            expired = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
            for member, fields in [("mina", {"valid_from": future}), ("hyeon", {"valid_until": expired}), ("minseok", {"valid_until": future})]:
                response = client.post(f"/api/projects/{pid}/members", headers=JIHO, json={"member_id": member, **fields})
                assert response.status_code == 201, response.text
            answer = client.get("/api/graph/neighbors", headers=JIHO, params={"node": f"project:{pid}"}).json()
            assert {edge["from"] for edge in answer["edges"]} == {"person:jiho", "person:minseok"}
        finally:
            environment.undo()
            time.tzset()


def test_empty_team_is_a_real_node_and_unknown_team_is_not(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    from ax_workspace.platform.persistence import OrganizationUnitRecord, make_session_factory

    with make_session_factory(f"sqlite:///{tmp_path / 'demo.db'}")() as session:
        session.add(OrganizationUnitRecord(id="empty-team", name="아직 빈 팀", parent_id="scax"))
        session.commit()
    answer = client.get("/api/graph/neighbors", headers=JIHO, params={"node": "team:empty-team"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["center"]["title"] == "아직 빈 팀"
    assert client.get("/api/graph/neighbors", headers=JIHO, params={"node": "team:missing"}).status_code == 404


@pytest.mark.parametrize("node", ["conversation:123", "action:123", "draft:123", "task:", "project:2026", "2026"])
def test_graph_rejects_unsupported_nodes_and_title_numbers(tmp_path, node) -> None:
    client, _ = _stack(tmp_path)
    assert client.get("/api/graph/neighbors", headers=JIHO, params={"node": node}).status_code == 422
    assert client.get("/api/graph/neighbors", headers=JIHO, params={"node_ref": "person:jiho"}).status_code == 422


def test_unknown_person_is_not_a_graph_node(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    assert client.get("/api/graph/neighbors", headers=JIHO, params={"node": "person:missing"}).status_code == 404


def test_request_read_without_task_read_can_use_the_discovered_graph(tmp_path) -> None:
    from dataclasses import replace
    from ax_workspace.entrypoints.mcp import McpReportsFacade
    from ax_workspace.modules.organization_access.domain import TASK_READ

    client, application = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "요청만 조회", "assignee_id": "jiho"}).json()
    principal = McpReportsFacade(Settings(RuntimeProfile.TEST, f"sqlite:///{tmp_path / 'demo.db'}"), "mina").principal
    request_reader = replace(principal, capabilities=principal.capabilities - {TASK_READ})
    result = application.graph_search(request_reader, "요청만 조회")
    assert [(row["kind"], row["id"]) for row in result["nodes"]] == [("work_request", request["request_id"])]


def test_project_receipts_are_visible_then_redacted_after_membership_release(tmp_path, monkeypatch) -> None:
    from uuid import UUID
    from ax_workspace.entrypoints.mcp import McpReportsFacade
    from ax_workspace.platform.persistence import ConversationTurnRecord, make_session_factory

    client, _ = _stack(tmp_path)
    project = client.post("/api/projects", headers=JIHO, json={"name": "회수되는 탐색 근거"}).json()
    pid = project["project_id"]
    client.post(f"/api/projects/{pid}/members", headers=JIHO, json={"member_id": "mina"})
    conversation = client.post("/api/conversations", headers=MINA, json={"title": "조회"}).json()
    cid = conversation["conversation_id"]
    accepted = client.post(f"/api/conversations/{cid}/messages", headers={**MINA, "Idempotency-Key": "project-receipt"}, json={"body": "프로젝트 관계", "context": []}).json()
    url = f"sqlite:///{tmp_path / 'demo.db'}"
    with make_session_factory(url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id
    monkeypatch.setenv("AX_MCP_CAUSATION_ID", str(execution_id))
    facade = McpReportsFacade(Settings(RuntimeProfile.TEST, url), "mina")
    facade.graph_search(project["name"])
    facade.graph_neighbors(f"project:{pid}")
    before = client.get(f"/api/conversations/{cid}", headers=MINA).json()["graph_receipts"]
    assert any(row.get("node_ref") == f"project:{pid}" for row in before)
    assert any(row.get("edge_kind") == "assigned_to" for row in before)
    client.delete(f"/api/projects/{pid}/members/mina", headers=JIHO)
    assert client.get(f"/api/conversations/{cid}", headers=MINA).json()["graph_receipts"] == []


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
            "title": "시간이 정해진 회의",
            "starts_at": "2026-09-10T01:00:00Z", "ends_at": "2026-09-10T02:00:00Z",
            "attendee_ids": [],
        },
    ).json()["meeting"]

    nodes = {f"{node['kind']}:{node['id']}": node for node in client.get("/api/graph/overview", headers=MINA).json()["nodes"]}
    assert nodes[f"task:{dated['task_id']}"]["date"] == "2026-09-30"
    assert nodes[f"meeting:{meeting['meeting_id']}"]["date"] == "2026-09-10"
    # 계획한 날짜가 없으면 없는 채로 둔다 — 만든 날짜를 계획인 척하지 않는다.
    undated = [node for node in nodes.values() if node["kind"] == "task" and node["title"] == "날짜가 없는 업무"]
    assert undated and undated[0]["date"] is None
    assert nodes["person:mina"]["date"] is None
