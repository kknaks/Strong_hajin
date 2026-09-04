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
    assert (by_name["broken.pdf"]["status"], by_name["broken.pdf"]["failure_reason"]) == ("failed", "corrupt_pdf")
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
