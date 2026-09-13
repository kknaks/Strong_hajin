"""Publish complete projections atomically; retain incomplete-source metadata on every material surface."""
import asyncio
import pytest
from dataclasses import replace
from uuid import UUID

from test_material_search import MINA, _stack, _upload
from ax_workspace.entrypoints.mcp import McpReportsFacade
from ax_workspace.modules.work.material_extraction import MaterialExtractionJob
from ax_workspace.platform.material_extraction import PypdfTextExtractor


class PartialCoverageExtractor:
    def extract(self, **kwargs):
        return replace(
            PypdfTextExtractor().extract(**kwargs),
            status="partial",
            warnings=("page 2 needs OCR",),
            coverage={
                "complete": False,
                "unit": "page",
                "total_units": 2,
                "processed_units": 1,
                "missing_units": [{"page": 2, "reason": "needs_ocr"}],
            },
        )


def test_partial_search_requires_a_file_anchor_and_preserves_coverage_in_receipt(tmp_path, monkeypatch):
    from ax_workspace.platform.persistence import ConversationTurnRecord, make_session_factory

    client, _, worker, settings = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "선택한 부분 자료"}).json()
    selected = _upload(client, task["task_id"], "selected.txt", b"Readable partialtailtoken", "text/plain").json()
    _upload(client, task["task_id"], "other.txt", b"Other partialtailtoken", "text/plain")

    worker._extractor = PartialCoverageExtractor()
    assert asyncio.run(worker.run_once())
    scoped_url = "/api/materials/search"
    scope = {"resource_type": "task", "resource_id": task["task_id"]}
    for filters in (scope, {}):
        general = client.get(scoped_url, headers=MINA, params={**filters, "q": "partialtailtoken"}).json()
        assert general["results"] == [] and general["searched_materials"] == 0
    params = {"q": "partialtailtoken", "material_id": selected["material_id"]}
    found = client.get(scoped_url, headers=MINA, params={**params, **scope}).json()
    assert found["searched_materials"] == 1 and found["unavailable_materials"] == []
    assert {hit["material_id"] for hit in found["results"]} == {selected["material_id"]}
    extraction = found["results"][0]["extraction"]
    assert extraction["status"] == "partial" and extraction["coverage"]["complete"] is False
    assert extraction["coverage"]["missing_units"] == [{"page": 2, "reason": "needs_ocr"}]
    assert extraction["warnings"] == ["page 2 needs OCR"]
    global_found = client.get('/api/materials/search', headers=MINA, params=params).json()
    assert global_found["results"] == found["results"] and global_found["searched_materials"] == 1

    conversation = client.post("/api/conversations", headers=MINA, json={"title": "부분 근거"}).json()
    accepted = client.post(f"/api/conversations/{conversation['conversation_id']}/messages", headers={**MINA, "Idempotency-Key": "partial-turn"},
                           json={"body": "선택한 파일에서 확인 가능한 부분을 찾아줘", "context": []}).json()
    with make_session_factory(settings.database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id
    monkeypatch.setenv("AX_MCP_CAUSATION_ID", str(execution_id))
    mcp = McpReportsFacade(settings, 'mina').search_materials('partialtailtoken', material_id=selected['material_id'], resource_type='task', resource_id=task['task_id'])
    assert mcp == found
    receipt = client.get(f"/api/conversations/{conversation['conversation_id']}", headers=MINA).json()["material_evidence"]
    assert len(receipt) == 1 and receipt[0]["extraction"] == extraction
    for filters in (scope, {}):
        no_hit = client.get(scoped_url, headers=MINA, params={**params, **filters, "q": "unmatchedsyntheticphrase"}).json()
        assert no_hit["results"] == [] and no_hit["searched_materials"] == 1
        assert no_hit["selected_material"]["extraction"] == extraction
    no_hit_mcp = McpReportsFacade(settings, 'mina').search_materials('unmatchedsyntheticphrase', material_id=selected['material_id'])
    assert no_hit_mcp == no_hit


@pytest.mark.parametrize("revocation", ["detach", "purge", "integrity"])
def test_file_search_and_stored_evidence_recheck_the_live_material(tmp_path, monkeypatch, revocation):
    from uuid import uuid4
    from test_material_search import JIHO
    from ax_workspace.platform.persistence import AttachmentRecord, ConversationTurnRecord, make_session_factory

    client, _, worker, settings = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "근거 회수"}).json()
    material = _upload(client, task["task_id"], "revocable.txt", b"revocableevidencetoken", "text/plain").json()
    assert asyncio.run(worker.run_once())
    conversation = client.post("/api/conversations", headers=MINA, json={"title": "읽었던 근거"}).json()
    turn = client.post(f"/api/conversations/{conversation['conversation_id']}/messages", headers={**MINA, "Idempotency-Key": "revoke-evidence"},
                      json={"body": "선택 자료를 찾아줘", "context": []}).json()
    with make_session_factory(settings.database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(turn["turn_id"])).execution_id
    monkeypatch.setenv("AX_MCP_CAUSATION_ID", str(execution_id))
    facade = McpReportsFacade(settings, "mina")
    assert facade.search_materials('revocableevidencetoken', material_id=material['material_id'])["results"]
    params = {"q": "revocableevidencetoken", "material_id": material["material_id"]}
    missing = client.get('/api/materials/search', headers=MINA, params={**params, 'material_id': str(uuid4())})
    forbidden = client.get('/api/materials/search', headers=JIHO, params=params)
    assert missing.status_code == forbidden.status_code == 404
    assert missing.json() == forbidden.json()
    if revocation == "detach":
        assert client.post(f'/api/tasks/{task['task_id']}/material-bindings/{material['binding_id']}/detach', headers=MINA).status_code == 200
    else:
        with make_session_factory(settings.database_url)() as session:
            attachment = session.get(AttachmentRecord, UUID(material["attachment_id"]))
            if revocation == "purge":
                attachment.lifecycle = "purged"
            else:
                attachment.integrity_ref = "sha256:changed"
            session.commit()
    after = client.get('/api/materials/search', headers=MINA, params=params)
    if revocation == "integrity":
        assert after.status_code == 200 and after.json()["results"] == [] and after.json()["searched_materials"] == 0
    else:
        assert after.status_code == 404 and after.json() == missing.json()
    view = client.get(f"/api/conversations/{conversation['conversation_id']}", headers=MINA).json()
    assert view["material_evidence"] == []
    assert "revocableevidencetoken" not in str(view)


def test_project_access_revocation_removes_file_search_and_prior_receipt(tmp_path, monkeypatch):
    from test_material_search import JIHO
    from ax_workspace.platform.persistence import ConversationTurnRecord, ToolInvocationRecord, make_session_factory

    client, _, worker, settings = _stack(tmp_path)
    project = client.post("/api/projects", headers=JIHO, json={"name": "근거 읽기 프로젝트"}).json()
    task = client.post("/api/tasks", headers=JIHO, json={"title": "공유 업무", "project_id": project["project_id"]}).json()
    material = _upload(client, task["task_id"], "shared.txt", b"sharedrevocationtoken", "text/plain", headers=JIHO).json()
    assert asyncio.run(worker.run_once())
    assert client.post(f"/api/projects/{project['project_id']}/members", headers=JIHO, json={"member_id": "mina"}).status_code == 201
    conversation = client.post("/api/conversations", headers=MINA, json={"title": "공유 자료 조회"}).json()
    turn = client.post(f"/api/conversations/{conversation['conversation_id']}/messages", headers={**MINA, "Idempotency-Key": "shared-turn"},
                      json={"body": "공유 파일을 찾아줘", "context": []}).json()
    with make_session_factory(settings.database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(turn["turn_id"])).execution_id
    monkeypatch.setenv("AX_MCP_CAUSATION_ID", str(execution_id))
    facade = McpReportsFacade(settings, "mina")
    assert facade.search_materials('sharedrevocationtoken', material_id=material['material_id'])["results"]
    with make_session_factory(settings.database_url)() as session:
        session.add(ToolInvocationRecord(
            turn_id=UUID(turn["turn_id"]), sequence=1, provider_call_id="historical-material-call",
            tool_name="material_search", display_name="자료 검색", input_summary="입력: 비공개", state="succeeded",
            result_summary="결과: 자료 1개 검색, 관련 구간 1건 (shared.txt)",
        ))
        session.commit()
    assert client.get(f"/api/conversations/{conversation['conversation_id']}", headers=MINA).json()["material_evidence"]
    assert client.delete(f"/api/projects/{project['project_id']}/members/mina", headers=JIHO).status_code == 204
    assert client.get('/api/materials/search', headers=MINA, params={'q': 'sharedrevocationtoken', 'material_id': material['material_id']}).status_code == 404
    general = facade.search_materials('sharedrevocationtoken')
    assert general["results"] == [] and general["searched_materials"] == 0 and general["unavailable_materials"] == []
    view = client.get(f"/api/conversations/{conversation['conversation_id']}", headers=MINA).json()
    assert view["material_evidence"] == [] and view["answer_resources"] == []
    assert "sharedrevocationtoken" not in str(view) and "shared.txt" not in str(view)


def test_worker_persists_partial_coverage_and_does_not_offer_it_in_general_search(tmp_path):
    client, application, worker, settings = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "부분 처리"}).json()
    material = _upload(client, task["task_id"], "mixed.txt", b"Readable tailtoken", "text/plain").json()
    missing = {"page": 2, "reason": "needs_ocr"}

    worker._extractor = PartialCoverageExtractor()
    assert asyncio.run(worker.run_once())
    listed = client.get(f"/api/tasks/{task['task_id']}/materials", headers=MINA).json()
    extraction = listed[0]["extraction"]
    assert extraction["status"] == "partial"
    assert extraction["coverage"]["missing_units"] == [missing]
    assert extraction["warnings"] == ["page 2 needs OCR"]
    result = McpReportsFacade(settings, 'mina').search_materials('tailtoken', limit=3)
    assert result["results"] == [] and result["searched_materials"] == 0
    assert result["unavailable_materials"][0]["extraction"] == extraction
    assert worker.process(MaterialExtractionJob(UUID(extraction["extraction_id"]), UUID(material["attachment_id"]))) == "skipped"


def test_interrupted_batch_publish_rolls_back_and_retry_publishes_once(tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta
    import pytest
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session
    from ax_workspace.platform.persistence import MaterialBlockRecord, MaterialChunkRecord, MaterialExtractionRecord, make_session_factory

    client, _, worker, settings = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "중단 재시도"}).json()
    body = ("synthetic evidence context " * 10000 + " uniquefinaltoken").encode()
    uploaded = _upload(client, task["task_id"], "long.txt", body, "text/plain").json()
    job = MaterialExtractionJob(UUID(uploaded["extraction"]["extraction_id"]), UUID(uploaded["attachment_id"]))
    execute = Session.execute
    batches = []

    def interrupt(self, statement, params=None, *args, **kwargs):
        if getattr(statement, "is_insert", False) and statement.table.name == "material_chunks":
            batches.append(len(params))
            if len(batches) == 2:
                raise RuntimeError("synthetic interruption")
        return execute(self, statement, params, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Session, "execute", interrupt)
        with pytest.raises(RuntimeError, match="synthetic interruption"):
            worker.process(job)
    assert batches == [128, 128]
    with make_session_factory(settings.database_url)() as session:
        assert session.scalar(select(func.count()).select_from(MaterialChunkRecord)) == 0
        assert session.scalar(select(func.count()).select_from(MaterialBlockRecord)) == 0
        extraction = session.get(MaterialExtractionRecord, job.extraction_id)
        assert extraction.status == "running" and extraction.completed_at is None
        extraction.started_at = datetime.now(UTC) - timedelta(days=1)
        extraction.heartbeat_at = extraction.started_at
        session.commit()
    assert worker.process(job) == "completed"
    assert worker.process(job) == "skipped"
    with make_session_factory(settings.database_url)() as session:
        extraction = session.get(MaterialExtractionRecord, job.extraction_id)
        assert extraction.attempt_count == 2
        assert session.scalar(select(func.count()).select_from(MaterialChunkRecord)) == extraction.chunk_count
        assert extraction.char_count == len(body.decode()) and extraction.coverage["complete"]
        assert session.scalar(select(func.count()).select_from(MaterialBlockRecord)) == 1
    result = McpReportsFacade(settings, 'mina').search_materials('uniquefinaltoken', resource_type='task', resource_id=task['task_id'], limit=3)
    assert result["results"] and "uniquefinaltoken" in result["results"][0]["excerpt"]
