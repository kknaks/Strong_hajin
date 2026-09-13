"""Large synthetic ledgers exercise the real application/HTTP/MCP paths."""
from time import perf_counter

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.entrypoints.mcp import McpReportsFacade
from graph_scale_fixture import identity, populate
from test_mcp import ContractTestAiProvider
from test_relation_graph import _today


pytestmark = pytest.mark.scale


class _CountedCursor:
    """Count rows fetched, including scalar/tuple queries outside ORM hydration."""
    def __init__(self, cursor, counts):
        self.cursor, self.counts = cursor, counts

    def __getattr__(self, name):
        return getattr(self.cursor, name)

    def fetchone(self):
        row = self.cursor.fetchone()
        self.counts["fetched_db_rows"] += int(row is not None)
        return row

    def fetchmany(self, *args):
        rows = self.cursor.fetchmany(*args)
        self.counts["fetched_db_rows"] += len(rows)
        return rows

    def fetchall(self):
        rows = self.cursor.fetchall()
        self.counts["fetched_db_rows"] += len(rows)
        return rows


def _count_query(counts):
    def before_execute(connection, cursor, statement, parameters, context, executemany):
        counts["sql_queries"] += 1
        context.cursor = _CountedCursor(cursor, counts)
    return before_execute


def test_cost_counter_includes_each_cursor_fetch_mode():
    engine = create_engine("sqlite:///:memory:")
    counts = {"sql_queries": 0, "fetched_db_rows": 0}
    event.listen(engine, "before_cursor_execute", _count_query(counts))
    with engine.connect() as connection:
        rows = connection.execute(text("select 1 union all select 2 union all select 3"))
        assert rows.fetchone()[0] == 1
        assert rows.fetchmany(1)[0][0] == 2
        assert rows.fetchall()[0][0] == 3
    engine.dispose()
    assert counts == {"sql_queries": 1, "fetched_db_rows": 3}


@pytest.fixture(scope="module")
def large_graph(tmp_path_factory):
    directory = tmp_path_factory.mktemp("graph-scale")
    url = f"sqlite:///{directory / 'demo.db'}"
    reset_database(url)
    expected = populate(url)
    settings = Settings(RuntimeProfile.TEST, url, materials_dir=str(directory / "materials"))
    client = TestClient(create_app(settings, report_provider=ContractTestAiProvider()))
    mina = {"X-Demo-Persona": "mina"}
    # 회의에서 나온 일은 이제 안건의 「다음 할 일」에서 승격된다 — 그 표면은 SCAX-WP-004 다.
    # 여기서 필요한 것은 보고가 딛는 「오늘 움직인 업무」 하나뿐이므로 평범한 업무로 세운다.
    task = client.post("/api/tasks", headers=mina, json={"title": "오늘 움직인 업무"}).json()
    started = client.post(
        f"/api/tasks/{task['task_id']}/start", headers=mina, json={"expected_version": task["version"]}
    )
    assert started.status_code == 200, started.text
    application = client.app.state.workflow_application
    report = application.generate_daily_report_draft(
        application.authenticated_principal("mina"), _today()
    )
    expected["report_id"] = report["report_id"]
    expected["today_task"] = task["task_id"]
    return client, settings, expected


def test_scale_search_matches_ledger_partition_and_transport(large_graph, record_property):
    client, settings, expected = large_graph
    assert sum(expected["nodes"].values()) >= 5630
    assert len(expected["edges"]) >= 10000
    facade = McpReportsFacade(settings, "jiho")
    app = client.app.state.workflow_application
    measurements = []
    for query, wanted in [
        ("업무 1998", [("task", str(identity("task", 1998)))]),
        ("요청 0998", [("work_request", str(identity("request", 998)))]),
        ("회의 0498", [("meeting", str(identity("meeting", 498)))]),
        ("업무 1999", []),
        ("회의 0499", []),
    ]:
        counts = {"sql_queries": 0, "loaded_orm_rows": 0, "fetched_db_rows": 0}
        queried = _count_query(counts)
        def loaded(*args):
            counts["loaded_orm_rows"] += 1
        event.listen(Engine, "before_cursor_execute", queried)
        event.listen(Session, "loaded_as_persistent", loaded)
        try:
            started = perf_counter()
            body = app.graph_search(facade.principal, query)
            measurements.append({"query_class": query.split()[0], **counts, "elapsed_ms": round((perf_counter() - started) * 1000, 2)})
        finally:
            event.remove(Engine, "before_cursor_execute", queried)
            event.remove(Session, "loaded_as_persistent", loaded)
        assert [(row["kind"], row["id"]) for row in body["nodes"]] == wanted
        assert body["truncated"] is False
        assert client.get("/api/graph/search", headers={"X-Demo-Persona": "jiho"}, params={"q": query}).json() == body
        assert facade.graph_search(query) == body
    record_property("search_measurements", measurements)
    record_property("directed_relations", len(expected["edges"]))


def test_scale_duplicate_names_cross_kind_clashes_and_bounded_results(large_graph):
    client, settings, _ = large_graph
    facade = McpReportsFacade(settings, "jiho")
    people = facade.graph_search("합성 동명이인 1")
    assert len(people["nodes"]) == 10
    assert len({node["id"] for node in people["nodes"]}) == 10
    assert people["truncated"] is False
    answer = facade.graph_search("대량 충돌 2026", 500)
    assert len(answer["nodes"]) == 50 and answer["truncated"] is True
    assert {node["kind"] for node in answer["nodes"]} == {"project", "task"}
    denied = {str(identity(kind, i)) for kind, count in [("project", 10), ("task", 2000), ("request", 1000), ("meeting", 500)] for i in range(1, count, 2)}
    assert not denied.intersection(node["id"] for node in answer["nodes"])
    assert client.get("/api/graph/search", headers={"X-Demo-Persona": "jiho"}, params={"q": "대량 충돌 2026", "limit": 500}).json() == answer


def test_scale_material_beyond_first_fifty_tasks_still_has_live_owners(large_graph):
    client, _, expected = large_graph
    # Task lists are oldest first. A late task is deliberately beyond their first page.
    material = f"material:{identity('material', 1998)}"
    answer = client.get("/api/graph/neighbors", headers={"X-Demo-Persona": "jiho"}, params={"node": material})
    assert answer.status_code == 200, answer.text
    actual = {(edge["kind"], edge["from"], edge["to"]) for edge in answer.json()["edges"]}
    assert actual == {edge for edge in expected["edges"] if material in edge[1:]}


def test_scale_person_finds_work_beyond_the_general_task_page(large_graph):
    _, settings, _ = large_graph
    answer = McpReportsFacade(settings, "jiho").graph_neighbors("person:mina", 50)
    assert ("holds", "person:mina", f"task:{identity('task', 1998)}") in {(e["kind"], e["from"], e["to"]) for e in answer["edges"]}


def test_scale_person_finds_attendance_beyond_the_general_meeting_page(large_graph):
    _, settings, _ = large_graph
    answer = McpReportsFacade(settings, "jiho").graph_neighbors("person:hyeon", 50)
    assert ("attended", "person:hyeon", f"meeting:{identity('meeting', 498)}") in {(e["kind"], e["from"], e["to"]) for e in answer["edges"]}


def test_scale_meeting_search_reports_truncation_at_maximum_limit(large_graph):
    client, _, _ = large_graph
    body = client.get("/api/graph/search", headers={"X-Demo-Persona": "jiho"}, params={"q": "2026 회의", "limit": 50}).json()
    assert len(body["nodes"]) == 50
    assert body["truncated"] is True


def test_scale_task_relations_preserve_directions_and_project_context(large_graph):
    client, settings, expected = large_graph
    ref = f"task:{identity('task', 500)}"
    answer = McpReportsFacade(settings, "jiho").graph_neighbors(ref, 50)
    actual = {(edge["kind"], edge["from"], edge["to"]) for edge in answer["edges"]}
    wanted = {edge for edge in expected["edges"] if edge[1] == ref or (edge[2] == ref and edge[0] in {"holds", "produced", "parent_of"})}
    assert wanted <= actual
    assert ("refers_to", ref, f"task:{identity('task', 504)}") not in actual
    assert all(edge["provenance"] and edge["inverse_label"] for edge in answer["edges"])
    assert client.get("/api/graph/neighbors", headers={"X-Demo-Persona": "jiho"}, params={"node": ref, "limit": 50}).json() == answer
    assert client.get("/api/graph/neighbors", headers={"X-Demo-Persona": "minseok"}, params={"node": ref}).status_code == 404


def test_scale_unbound_material_and_private_material_do_not_leak(large_graph):
    client, _, _ = large_graph
    for index in (1996, 1999):
        answer = client.get("/api/graph/neighbors", headers={"X-Demo-Persona": "jiho"}, params={"node": f"material:{identity('material', index)}"})
        assert answer.status_code == 404
        assert answer.json() == {"detail": "대상을 찾을 수 없습니다"}


def test_scale_destroyed_source_preserves_the_same_live_binding_fact(large_graph):
    from ax_workspace.platform.persistence import AttachmentRecord, make_session_factory

    client, settings, _ = large_graph
    with make_session_factory(settings.database_url)() as session:
        session.get(AttachmentRecord, identity("material", 1994)).lifecycle = "purged"
        session.commit()
    task_ref = f"task:{identity('task', 1994)}"
    material_ref = f"material:{identity('material', 1994)}"
    facade = McpReportsFacade(settings, "jiho")
    task = facade.graph_neighbors(task_ref)
    assert any(edge["to"] == material_ref for edge in task["edges"])
    material = facade.graph_neighbors(material_ref)
    assert [(edge["kind"], edge["from"], edge["to"]) for edge in material["edges"]] == [("has_material", task_ref, material_ref)]
    listed = client.get(f"/api/tasks/{identity('task', 1994)}/materials", headers={"X-Demo-Persona": "jiho"}).json()
    assert listed[0]["purged"] is True


def test_scale_task_read_does_not_grant_project_read(large_graph):
    from dataclasses import replace
    from ax_workspace.modules.organization_access.domain import PROJECT_READ

    client, settings, _ = large_graph
    principal = McpReportsFacade(settings, "jiho").principal
    task_reader = replace(principal, capabilities=principal.capabilities - {PROJECT_READ})
    task_ref = f"task:{identity('task', 500)}"
    answer = client.app.state.workflow_application.graph_neighbors(task_reader, task_ref, 50)
    assert answer["center"]["id"] == str(identity("task", 500))
    assert not any(node["kind"] == "project" for node in answer["nodes"])
    assert not any(edge["kind"] == "part_of" for edge in answer["edges"])
    assert "대량 충돌 2026 프로젝트" not in str(answer)
    assert answer["truncated"] is False


def test_scale_project_expansion_is_bounded_and_matches_live_ledger(large_graph):
    client, settings, expected = large_graph
    ref = f"project:{identity('project', 0)}"
    facade = McpReportsFacade(settings, "jiho")
    answer = facade.graph_neighbors(ref, 50)
    actual = {(edge["kind"], edge["from"], edge["to"]) for edge in answer["edges"]}
    assert len(actual) == 50 and answer["truncated"] is True
    assert actual <= {edge for edge in expected["edges"] if ref in edge[1:]}
    assert answer == client.app.state.workflow_application.graph_neighbors(facade.principal, ref, 50)
    assert answer == client.get("/api/graph/neighbors", headers={"X-Demo-Persona": "jiho"}, params={"node": ref, "limit": 50}).json()
    assert client.get("/api/graph/neighbors", headers={"X-Demo-Persona": "minseok"}, params={"node": ref}).status_code == 404


def test_scale_report_reaches_only_actual_evidence(large_graph):
    """보고–업무 간선은 초안이 실제로 담은 `source_refs` 에서만 나온다.

    회의–업무 간선은 승격이 안건의 「다음 할 일」로 옮겨 가면서 잠시 없다 — SCAX-WP-004 가 다시 세운다.
    """
    client, settings, expected = large_graph
    task = expected["today_task"]
    report = McpReportsFacade(settings, "mina").graph_neighbors(f"report:{expected['report_id']}", 50)
    cited = [edge for edge in report["edges"] if edge["kind"] == "cites"]
    assert cited and all(edge["to"] in {f"task:{identity('task', 1998)}", f"task:{task}"} for edge in cited)
    assert report["truncated"] is False
    assert client.get("/api/graph/neighbors", headers={"X-Demo-Persona": "minseok"}, params={"node": f"report:{expected['report_id']}"}).status_code == 404
