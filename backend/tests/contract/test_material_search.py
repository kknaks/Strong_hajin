"""Material extraction lifecycle and authorized `material.search` through the public API, MCP facade, and conversation view.

Runs on SQLite with the in-memory job fake; queue correctness is covered by the PostgreSQL integration tests.
"""
import asyncio
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from ax_workspace.bootstrap.material_worker import MaterialExtractionWorker
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.mcp import McpReportsFacade
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.jobs.domain import JOB_KIND_MATERIAL_EXTRACTION
from ax_workspace.platform.persistence import ConversationTurnRecord, MaterialChunkRecord, make_session_factory

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
BRIEF = "# 견적 검토\n\n공급사는 한빛상사이고 납기일은 2026-09-30입니다.\n\n" + "총액은 1,200,000원이며 부가세 별도입니다. " * 40


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    app = create_app(settings)
    client = TestClient(app)
    application = app.state.workflow_application
    worker = MaterialExtractionWorker(settings, queue_factory=lambda session: application.memory_job_queue)
    return client, application, worker, settings


def _upload(client, task_id: str, name: str, body: bytes, content_type: str, headers=MINA):
    return client.post(f"/api/tasks/{task_id}/materials", headers=headers, data={"kind": "input"}, files={"file": (name, body, content_type)})


def test_upload_records_a_queued_extraction_and_the_worker_indexes_it_idempotently(tmp_path) -> None:
    client, application, worker, settings = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "견적 검토"}).json()
    uploaded = _upload(client, task["task_id"], "견적.md", BRIEF.encode(), "text/markdown")
    assert uploaded.status_code == 201, uploaded.text
    extraction = uploaded.json()["extraction"]
    assert extraction["status"] == "queued" and extraction["extractor"] == "markdown"
    # Exactly one job for the upload, delivered by the shared transport.
    assert application.memory_job_queue.pending_count(JOB_KIND_MATERIAL_EXTRACTION) == 1

    assert asyncio.run(worker.run_once()) is True
    listed = client.get(f"/api/tasks/{task['task_id']}/materials", headers=MINA).json()
    assert listed[0]["extraction"]["status"] == "completed" and listed[0]["extraction"]["chunk_count"] >= 1
    assert application.memory_job_queue.pending_count(JOB_KIND_MATERIAL_EXTRACTION) == 0

    # Re-processing the same attachment version is idempotent: no duplicate chunks, status unchanged.
    with make_session_factory(settings.database_url)() as session:
        before = len(session.scalars(select(MaterialChunkRecord)).all())
    status = worker.process(type("Job", (), {"extraction_id": UUID(extraction["extraction_id"]), "attachment_id": UUID(listed[0]["attachment_id"])})())
    assert status == "skipped"
    with make_session_factory(settings.database_url)() as session:
        assert len(session.scalars(select(MaterialChunkRecord)).all()) == before


def test_failures_are_explainable_states_not_silent_success(tmp_path) -> None:
    client, _, worker, _ = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "형식 확인"}).json()
    png = _upload(client, task["task_id"], "scan.png", b"\x89PNG\r\n", "image/png").json()
    latin = _upload(client, task["task_id"], "legacy.txt", "caf\xe9".encode("cp1252") + b"\xff\xfe", "text/plain").json()
    broken = _upload(client, task["task_id"], "broken.pdf", b"%PDF-1.4 not really", "application/pdf").json()
    assert {png["extraction"]["status"], latin["extraction"]["status"], broken["extraction"]["status"]} == {"queued"}
    while asyncio.run(worker.run_once()):  # the worker claims `material_worker_concurrency` jobs per pass
        pass
    by_name = {item["name"]: item["extraction"] for item in client.get(f"/api/tasks/{task['task_id']}/materials", headers=MINA).json()}
    assert (by_name["scan.png"]["status"], by_name["scan.png"]["failure_reason"]) == ("unsupported", "unsupported_format")
    assert (by_name["legacy.txt"]["status"], by_name["legacy.txt"]["failure_reason"]) == ("failed", "not_utf8_text")
    assert (by_name["broken.pdf"]["status"], by_name["broken.pdf"]["failure_reason"]) == ("failed", "corrupt_document")
    assert all(item["failure_text"] for item in by_name.values())
    # Nothing searchable, but the unavailable files are named so AX can say it could not read them.
    search = client.get(f"/api/tasks/{task['task_id']}/materials/search", headers=MINA, params={"q": "납기일"}).json()
    assert search["results"] == [] and search["searched_materials"] == 0
    assert sorted(item["name"] for item in search["unavailable_materials"]) == ["broken.pdf", "legacy.txt", "scan.png"]


def test_search_returns_bounded_excerpts_only_for_authorized_live_bindings(tmp_path) -> None:
    client, _, worker, _ = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "견적 검토"}).json()
    material = _upload(client, task["task_id"], "견적.md", BRIEF.encode(), "text/markdown").json()
    other = _upload(client, task["task_id"], "회의록.txt", ("회의록: 일정 조율. " * 50).encode(), "text/plain").json()
    assert asyncio.run(worker.run_once()) is True
    search = client.get(f"/api/tasks/{task['task_id']}/materials/search", headers=MINA, params={"q": "공급사와 납기일이 어떻게 되나요"}).json()
    assert search["searched_materials"] == 2 and search["unavailable_materials"] == []
    # Several excerpts may come from one file (different sections); the unrelated file never appears.
    assert {hit["name"] for hit in search["results"]} == {"견적.md"}
    hit = search["results"][0]
    assert "한빛상사" in hit["excerpt"] and len(hit["excerpt"]) <= 300 and hit["integrity_ref"].startswith("sha256:")
    assert hit["origin"] == f"/api/tasks/{task['task_id']}/materials/{material['material_id']}/content"
    assert client.get(hit["origin"], headers=MINA).status_code == 200
    # An empty query is a 422, not an empty success.
    assert client.get(f"/api/tasks/{task['task_id']}/materials/search", headers=MINA, params={"q": "  "}).status_code == 422
    # Another member holds no assignment for this task: not found, no counts leaked.
    assert client.get(f"/api/tasks/{task['task_id']}/materials/search", headers=JIHO, params={"q": "납기일"}).status_code == 404
    # A detached material disappears from results and counts.
    assert client.post(f"/api/tasks/{task['task_id']}/materials/{material['material_id']}/detach", headers=MINA).status_code == 200
    after = client.get(f"/api/tasks/{task['task_id']}/materials/search", headers=MINA, params={"q": "납기일"}).json()
    assert after["results"] == [] and after["searched_materials"] == 1 and [m["name"] for m in after["unavailable_materials"]] == []
    del other


def test_delegated_mcp_search_records_turn_evidence_that_the_conversation_view_exposes(tmp_path, monkeypatch) -> None:
    client, application, worker, settings = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "견적 검토"}).json()
    material = _upload(client, task["task_id"], "견적.md", BRIEF.encode(), "text/markdown").json()
    assert asyncio.run(worker.run_once()) is True
    conversation = client.post("/api/conversations", headers=MINA, json={"title": "근거 설명"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": "evidence-turn"},
        json={"body": "첨부자료까지 포함해 설명해줘", "context": []},
    )
    assert accepted.status_code == 202
    with make_session_factory(settings.database_url)() as session:
        turn = session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"]))
        execution_id = str(turn.execution_id)

    # The persona-bound MCP facade inside a delegated turn uses the same application query and records evidence.
    monkeypatch.setenv("AX_MCP_CAUSATION_ID", execution_id)
    facade = McpReportsFacade(settings, "mina")
    result = facade.search_task_materials(task["task_id"], "공급사 납기일", 3)
    assert {hit["name"] for hit in result["results"]} == {"견적.md"}
    # Calling again with the same execution does not duplicate the evidence rows.
    facade.search_task_materials(task["task_id"], "공급사 납기일", 3)

    view = client.get(f"/api/conversations/{conversation['conversation_id']}", headers=MINA).json()
    evidence = view["material_evidence"]
    assert len(evidence) == len(result["results"]) >= 1
    assert evidence[0]["turn_id"] == accepted.json()["turn_id"] and evidence[0]["name"] == "견적.md"
    assert evidence[0]["material_id"] == material["material_id"] and "한빛상사" in evidence[0]["excerpt"]
    assert evidence[0]["origin"].endswith(f"/materials/{material['material_id']}/content")

    # Jiho cannot use Mina's execution to search Mina's task, nor is Jiho's own facade call recorded on Mina's turn.
    monkeypatch.setenv("AX_MCP_CAUSATION_ID", execution_id)
    jiho = McpReportsFacade(settings, "jiho")
    try:
        jiho.search_task_materials(task["task_id"], "납기일", 3)
        raise AssertionError("jiho must not search mina's task")
    except Exception as error:  # noqa: BLE001 - the application raises TaskNotFound; the type is not the contract here
        assert "not found" in str(error)
    assert len(client.get(f"/api/conversations/{conversation['conversation_id']}", headers=MINA).json()["material_evidence"]) == len(evidence)


def test_contended_extraction_delivery_is_released_not_archived(tmp_path) -> None:
    from datetime import UTC, datetime

    from ax_workspace.platform.persistence import MaterialExtractionRecord

    client, application, worker, settings = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "경합"}).json()
    uploaded = _upload(client, task["task_id"], "견적.md", BRIEF.encode(), "text/markdown").json()
    # Simulate a live worker holding the extraction row (running, fresh started_at) while this worker gets the delivery.
    with make_session_factory(settings.database_url)() as session:
        extraction = session.get(MaterialExtractionRecord, UUID(uploaded["extraction"]["extraction_id"]))
        extraction.status, extraction.started_at, extraction.attempt_count = "running", datetime.now(UTC), 1
        session.commit()
    assert asyncio.run(worker.run_once()) is True
    with make_session_factory(settings.database_url)() as session:
        extraction = session.get(MaterialExtractionRecord, UUID(uploaded["extraction"]["extraction_id"]))
        assert extraction.status == "running" and extraction.attempt_count == 1  # untouched
    [job] = [row for row in application.memory_job_queue.snapshot() if row["kind"] == JOB_KIND_MATERIAL_EXTRACTION]
    assert job["state"] == "queued" and job["last_error"] == "extraction held by a live worker"  # released, not completed


def test_evidence_recording_fails_closed_for_a_turn_the_principal_does_not_own(tmp_path) -> None:
    import pytest

    from ax_workspace.platform.material_extraction import SqlAlchemyMaterialEvidenceRepository

    client, _, _, settings = _stack(tmp_path)
    conversation = client.post("/api/conversations", headers=MINA, json={"title": "민아의 대화"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages", headers={**MINA, "Idempotency-Key": "owner-check"}, json={"body": "안내", "context": []}
    )
    with make_session_factory(settings.database_url)() as session:
        turn = session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"]))
        with pytest.raises(ValueError, match="another principal"):
            SqlAlchemyMaterialEvidenceRepository(session).record(turn.execution_id, "jiho", UUID(int=1), "q", [])


def test_action_preview_links_the_attachments_the_turn_read_and_hides_them_from_other_principals(tmp_path, monkeypatch) -> None:
    """The approver sees which attachments grounded the proposal, re-checked against their own Task access."""
    client, application, worker, settings = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "견적 검토"}).json()
    _upload(client, task["task_id"], "견적.md", BRIEF.encode(), "text/markdown")
    assert asyncio.run(worker.run_once()) is True
    conversation = client.post("/api/conversations", headers=MINA, json={"title": "근거 설명"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": "evidence-action"},
        json={"body": "첨부자료를 근거로 후속 업무를 제안해줘", "context": []},
    )
    with make_session_factory(settings.database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"])).execution_id
    monkeypatch.setenv("AX_MCP_CAUSATION_ID", str(execution_id))
    McpReportsFacade(settings, "mina").search_task_materials(task["task_id"], "공급사 납기일", 3)

    mina = application.authenticated_principal("mina")
    jiho = application.authenticated_principal("jiho")
    proposed = application.propose_action(mina, execution_id, "task.create_self", "업무 생성 확인", {"title": "후속 업무"})
    assert {row["id"]: row["value"] for row in proposed["preview"]}["evidence"] == "견적.md"
    # The same row reaches both the chat projection and the decision inbox.
    assert {row["id"]: row["value"] for row in client.get("/api/actions", headers=MINA).json()[0]["preview"]}["evidence"] == "견적.md"
    projected = client.get(f"/api/conversations/{conversation['conversation_id']}", headers=MINA).json()["actions"][0]
    assert {row["id"] for row in projected["preview"]} >= {"evidence"}

    from ax_workspace.platform.actions import ActionPresenter
    from ax_workspace.platform.persistence import ActionItemRecord

    with make_session_factory(settings.database_url)() as session:
        record = session.get(ActionItemRecord, UUID(proposed["action_id"]))
        presenter = ActionPresenter(session)
        assert any(row["id"] == "evidence" for row in presenter.present(record, mina)["preview"])
        # Jiho holds no assignment on Mina's Task, so the linked evidence leaves the preview entirely.
        rows = presenter.present(record, jiho)["preview"]
        assert not any(row["id"] == "evidence" for row in rows)
        assert "견적.md" not in str(rows)


def test_action_preview_has_no_evidence_row_when_the_turn_read_nothing(tmp_path) -> None:
    client, application, worker, settings = _stack(tmp_path)
    conversation = client.post("/api/conversations", headers=MINA, json={"title": "근거 없음"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": "no-evidence"},
        json={"body": "그냥 업무 하나 만들어줘", "context": []},
    )
    with make_session_factory(settings.database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"])).execution_id
    proposed = application.propose_action(application.authenticated_principal("mina"), execution_id, "task.create_self", "업무 생성 확인", {"title": "후속 업무"})
    assert [row["id"] for row in proposed["preview"]] == ["assignee"]


def test_searching_without_naming_the_work_finds_what_this_person_may_read(tmp_path) -> None:
    """어느 자료에 있는지 모르는 채로 묻는 것이 자료 검색의 보통이다.

    시작점을 대라고 요구하면 이미 아는 사람만 찾을 수 있고, 그것은 검색이 아니라 조회다. 시작점이 넓어져도
    권한은 넓어지지 않는다 — 읽을 수 있는 업무에 지금 살아 있는 binding만 본다.
    """
    client, application, worker, settings = _stack(tmp_path)
    mine = client.post("/api/tasks", headers=MINA, json={"title": "내 견적 검토"}).json()
    theirs = client.post("/api/tasks", headers=JIHO, json={"title": "남의 견적 검토"}).json()
    _upload(client, mine["task_id"], "내견적.md", BRIEF.encode(), "text/markdown")
    _upload(client, theirs["task_id"], "남견적.md", BRIEF.encode(), "text/markdown", headers=JIHO)
    while asyncio.run(worker.run_once()):
        pass

    found = client.get("/api/materials/search", headers=MINA, params={"q": "한빛상사"})
    assert found.status_code == 200, found.text
    body = found.json()
    # 시작점을 대지 않았으므로 답에도 시작점이 없다. 각 줄이 자기 업무를 말한다.
    assert body["task_id"] is None and body["task_title"] is None
    assert {row["name"] for row in body["results"]} == {"내견적.md"}
    assert {row["task_id"] for row in body["results"]} == {mine["task_id"]}
    # 남의 자료는 이름도 건수도 나오지 않는다.
    assert "남견적" not in found.text

    # 지호에게는 정확히 반대로 보인다.
    theirs_found = client.get("/api/materials/search", headers=JIHO, params={"q": "한빛상사"}).json()
    assert {row["name"] for row in theirs_found["results"]} == {"남견적.md"}


def test_naming_the_work_still_scopes_the_search_to_it(tmp_path) -> None:
    """시작점을 알면 거기서만 찾는다. 넓게 찾는 길이 생겼다고 좁게 찾는 길이 사라지지 않는다."""
    client, application, worker, settings = _stack(tmp_path)
    one = client.post("/api/tasks", headers=MINA, json={"title": "첫 업무"}).json()
    two = client.post("/api/tasks", headers=MINA, json={"title": "두 번째 업무"}).json()
    _upload(client, one["task_id"], "첫견적.md", BRIEF.encode(), "text/markdown")
    _upload(client, two["task_id"], "둘째견적.md", BRIEF.encode(), "text/markdown")
    while asyncio.run(worker.run_once()):
        pass

    scoped = client.get(f"/api/tasks/{one['task_id']}/materials/search", headers=MINA, params={"q": "한빛상사"}).json()
    assert scoped["task_id"] == one["task_id"]
    assert {row["name"] for row in scoped["results"]} == {"첫견적.md"}

    wide = client.get("/api/materials/search", headers=MINA, params={"q": "한빛상사"}).json()
    assert {row["name"] for row in wide["results"]} == {"첫견적.md", "둘째견적.md"}


def test_an_index_made_with_older_rules_is_rebuilt_and_only_once(tmp_path) -> None:
    """분석 규칙이 바뀌면 그 규칙으로 만든 색인은 더 이상 질문과 만나지 못한다. 다시 만들어야 한다.

    다시 만드는 일은 여러 번 불러도 한 번 부른 것과 같아야 하고, 원문은 건드리지 않아야 한다 — 찾기 위한
    형태만 바뀐다.
    """
    from sqlalchemy import select

    from ax_workspace.platform.persistence import MaterialChunkRecord, make_session_factory

    client, application, worker, settings = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "견적 검토"}).json()
    _upload(client, task["task_id"], "견적.md", BRIEF.encode(), "text/markdown")
    assert asyncio.run(worker.run_once()) is True

    database_url = settings.database_url
    with make_session_factory(database_url)() as session:
        chunks = list(session.scalars(select(MaterialChunkRecord)))
        original = {chunk.id: chunk.text for chunk in chunks}
        # 옛 규칙으로 만들어진 것처럼 되돌린다.
        for chunk in chunks:
            chunk.search_text = "옛 규칙"
            chunk.analyzer_version = "kiwi-0.0.0-r0"
        session.commit()

    assert client.get(f"/api/tasks/{task['task_id']}/materials/search", headers=MINA, params={"q": "한빛상사"}).json()["results"] == []

    rebuilt = application.reindex_material_search()
    assert rebuilt == len(original)
    # 두 번째는 할 일이 없다.
    assert application.reindex_material_search() == 0

    found = client.get(f"/api/tasks/{task['task_id']}/materials/search", headers=MINA, params={"q": "한빛상사"}).json()
    assert found["results"], "다시 만든 색인으로도 찾지 못했습니다"
    with make_session_factory(database_url)() as session:
        # 원문은 그대로다. 바뀐 것은 찾기 위한 형태뿐이다.
        assert {chunk.id: chunk.text for chunk in session.scalars(select(MaterialChunkRecord))} == original
