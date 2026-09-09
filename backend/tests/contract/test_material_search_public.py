"""Canonical REST/MCP content search uses artifact identity across owners."""
import asyncio
from uuid import UUID

import pytest

from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from test_report_material_search import _stack, _draft, _submit, MINA


def test_public_report_search_has_rest_mcp_parity_and_only_one_provider_tool(tmp_path):
    client, application, worker, settings = _stack(tmp_path)
    submitted = _submit(application, _draft(application, "publicreporttoken"))
    assert asyncio.run(worker.run_once())
    response = client.get("/api/materials/search", headers=MINA, params={"q": "publicreporttoken", "resource_types": "report"})
    assert response.status_code == 200, response.text
    assert len(response.json()["results"]) == 1
    server = _create_bound_persona_server(McpReportsFacade(settings, "mina"))
    async def call():
        tools = await server.list_tools()
        names = {tool.name for tool in tools}
        assert "material_search" in names and "task_material_search" not in names
        tool = next(tool for tool in tools if tool.name == "material_search")
        assert {"resource_types", "resource_type", "resource_id", "material_id"} <= tool.input_schema["properties"].keys()
        return await server.call_tool("material_search", {"query": "publicreporttoken", "resource_types": ["report"]})
    result = asyncio.run(call())
    assert result.structured_content == response.json()
    assert result.structured_content["results"][0]["source_resource_id"] == submitted["report_id"]


def test_delegated_canonical_search_links_answer_resource_to_current_material_metadata(tmp_path, monkeypatch):
    from test_material_evidence_owners import _turn
    from sqlalchemy import delete
    from ax_workspace.platform.persistence import RoleCapabilityRecord, make_session_factory

    client, application, worker, settings = _stack(tmp_path)
    submitted = _submit(application, _draft(application, "publicanswertoken"))
    assert asyncio.run(worker.run_once())
    conversation_id, _, execution_id = _turn(client, settings)
    monkeypatch.setenv("AX_MCP_CAUSATION_ID", str(execution_id))
    found = McpReportsFacade(settings, "mina").search_materials("publicanswertoken")
    material_id = found["results"][0]["material_id"]
    view = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()
    resource = next(row for row in view["answer_resources"] if row["resource_type"] == "material")
    assert resource["resource_id"] == material_id and resource["source_contexts"]
    assert resource["source_locator"]["source_revision_id"] == submitted["submission_id"]
    assert resource["origin"] == found["results"][0]["origin"]
    metadata = client.get(f"/api/materials/{material_id}", headers=MINA)
    assert metadata.status_code == 200 and metadata.json()["material_id"] == material_id
    assert metadata.json()["origin"] == resource["origin"]
    with make_session_factory(settings.database_url)() as session:
        session.execute(delete(RoleCapabilityRecord).where(RoleCapabilityRecord.capability_id == "daily_report.read"))
        session.commit()
    assert client.get(f"/api/materials/{material_id}", headers=MINA).status_code == 404
    view = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()
    assert view["answer_resources"] == [] and view["material_evidence"] == []


@pytest.mark.parametrize("kind", ["task", "work_request", "personal_folder", "team_folder", "meeting"])
def test_public_owner_search_and_metadata_share_current_permissions(tmp_path, kind):
    from test_material_search import _upload
    from test_meeting_material_search import _recorded
    from ax_workspace.modules.meetings.transcription import FinalTranscriptSegment

    client, application, worker, settings = _stack(tmp_path)
    token = "publicownerparitytoken"
    if kind == "task":
        owner_id = client.post("/api/tasks", headers=MINA, json={"title": "검색 업무"}).json()["task_id"]
        _upload(client, owner_id, "public.txt", token.encode(), "text/plain")
    elif kind == "work_request":
        owner_id = client.post("/api/work-requests", headers=MINA, json={"title": "독립 요청", "assignee_id": "jiho"}).json()["request_id"]
        response = client.post(f"/api/work-requests/{owner_id}/evidence", headers=MINA, files={"file": ("public.txt", token.encode(), "text/plain")})
        assert response.status_code == 201, response.text
    elif kind.endswith("folder"):
        owner_id = client.post("/api/material-folders", headers=MINA, json={"kind": kind.removesuffix("_folder"), "title": "독립 자료", **({"organization_id": "product"} if kind == "team_folder" else {})}).json()["folder_id"]
        response = client.post(f"/api/material-folders/{owner_id}/materials", headers=MINA, files={"file": ("public.txt", token.encode(), "text/plain")})
        assert response.status_code == 201, response.text
    else:
        meeting, recording = _recorded(client)
        owner_id = meeting["meeting_id"]
        application.record_final_meeting_transcript(recording_id=UUID(recording["recording_id"]), provider="fixture", provider_reference="public:parity",
            segments=[FinalTranscriptSegment("public-a", 1200, 3400, token)])
    assert asyncio.run(worker.run_once())
    filters = {"resource_type": kind, "resource_id": owner_id}
    response = client.get("/api/materials/search", headers=MINA, params={"q": token, **filters})
    assert response.status_code == 200, response.text
    server = _create_bound_persona_server(McpReportsFacade(settings, "mina"))
    found = asyncio.run(server.call_tool("material_search", {"query": token, **filters})).structured_content
    assert found == response.json() and len(found["results"]) == 1
    hit = found["results"][0]
    assert hit["source_resource_type"] == kind and hit["source_resource_id"] == owner_id
    assert client.get(f"/api/materials/{hit['material_id']}", headers=MINA).json()["origin"] == hit["origin"]
    assert client.get(hit["origin"], headers=MINA).status_code == 200
    denied_headers = {"X-Demo-Persona": "sora"}
    assert client.get(f"/api/materials/{hit['material_id']}", headers=denied_headers).status_code == 404
    denied_server = _create_bound_persona_server(McpReportsFacade(settings, "sora"))
    denied = asyncio.run(denied_server.call_tool("material_search", {"query": token})).structured_content
    assert denied["results"] == [] and denied["searched_materials"] == denied["unavailable_materials_count"] == 0


def test_public_selected_partial_uses_artifact_id_and_retains_no_hit_coverage(tmp_path):
    from dataclasses import replace
    from test_material_search import _upload
    from ax_workspace.platform.material_extraction import PypdfTextExtractor

    client, _, worker, settings = _stack(tmp_path)
    task_id = client.post("/api/tasks", headers=MINA, json={"title": "부분 자료"}).json()["task_id"]
    uploaded = _upload(client, task_id, "partial.txt", b"publicpartialtoken", "text/plain").json()
    class PartialExtractor:
        def extract(self, **kwargs):
            return replace(PypdfTextExtractor().extract(**kwargs), status="partial", warnings=("page 2 needs OCR",),
                coverage={"complete": False, "unit": "page", "total_units": 2, "processed_units": 1, "missing_units": [{"page": 2, "reason": "needs_ocr"}]})
    worker._extractor = PartialExtractor()
    assert asyncio.run(worker.run_once())
    assert client.get("/api/materials/search", headers=MINA, params={"q": "publicpartialtoken"}).json()["results"] == []
    server = _create_bound_persona_server(McpReportsFacade(settings, "mina"))
    for query in ("publicpartialtoken", "missingphrase"):
        response = client.get("/api/materials/search", headers=MINA, params={"q": query, "material_id": uploaded["attachment_id"]})
        found = asyncio.run(server.call_tool("material_search", {"query": query, "material_id": uploaded["attachment_id"]})).structured_content
        assert response.status_code == 200 and found == response.json()
        assert found["searched_materials"] == 1
        assert found["selected_material"]["extraction"]["coverage"]["complete"] is False
        assert bool(found["results"]) == (query == "publicpartialtoken")
    assert client.get("/api/materials/search", headers=MINA, params={"q": "publicpartialtoken", "material_id": uploaded["binding_id"]}).status_code == 404


def test_registered_tool_is_available_without_task_capability_and_sanitizes_failures(tmp_path, monkeypatch):
    from sqlalchemy import delete
    from ax_workspace.platform.persistence import RoleCapabilityRecord, make_session_factory

    client, _, worker, settings = _stack(tmp_path)
    folder = client.post("/api/material-folders", headers=MINA, json={"kind": "personal", "title": "개인 자료"}).json()
    client.post(f"/api/material-folders/{folder['folder_id']}/materials", headers=MINA, files={"file": ("personal.txt", b"personalcapabilitytoken", "text/plain")})
    assert asyncio.run(worker.run_once())
    with make_session_factory(settings.database_url)() as session:
        session.execute(delete(RoleCapabilityRecord))
        session.commit()
    facade = McpReportsFacade(settings, "mina")
    server = _create_bound_persona_server(facade)
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert "material_search" in names and "task_list" not in names
    found = asyncio.run(server.call_tool("material_search", {"query": "personalcapabilitytoken"})).structured_content
    assert len(found["results"]) == 1
    invalid = asyncio.run(server.call_tool("material_search", {"query": "x", "material_id": "invalid-uuid"})).structured_content
    assert invalid["error_code"] == "invalid_query"
    def fail(*args, **kwargs):
        raise RuntimeError("secret SQL /private/storage/path")
    monkeypatch.setattr(facade, "search_materials", fail)
    failed = asyncio.run(server.call_tool("material_search", {"query": "x"})).structured_content
    assert failed["error_code"] == "internal_error" and "secret" not in str(failed) and "/private" not in str(failed)


@pytest.mark.parametrize("change", ["replace_context", "integrity"])
def test_answer_resource_cannot_restore_a_revoked_observation_through_current_metadata(tmp_path, monkeypatch, change):
    from test_material_search import _upload
    from test_material_evidence_owners import _turn
    from ax_workspace.platform.persistence import AttachmentRecord, make_session_factory
    from ax_workspace.platform.work_tasks import SqlAlchemyAttachmentRepository

    client, application, worker, settings = _stack(tmp_path)
    first = client.post("/api/tasks", headers=MINA, json={"title": "관측 업무"}).json()["task_id"]
    second = client.post("/api/tasks", headers=MINA, json={"title": "미관측 업무"}).json()["task_id"]
    uploaded = _upload(client, first, "observed.txt", b"observedcontexttoken", "text/plain").json()
    assert asyncio.run(worker.run_once())
    conversation_id, _, execution_id = _turn(client, settings)
    monkeypatch.setenv("AX_MCP_CAUSATION_ID", str(execution_id))
    McpReportsFacade(settings, "mina").search_materials("observedcontexttoken", resource_type="task", resource_id=first)
    if change == "replace_context":
        assert client.post(f'/api/tasks/{first}/material-bindings/{uploaded['binding_id']}/detach', headers=MINA).status_code == 200
    with make_session_factory(settings.database_url)() as session:
        if change == "replace_context":
            SqlAlchemyAttachmentRepository(session).bind(attachment_id=UUID(uploaded["attachment_id"]), context_type="task", context_id=second, role="input", bound_by="mina")
        else:
            session.get(AttachmentRecord, UUID(uploaded["attachment_id"])).integrity_ref = "sha256:changed"
        session.commit()
    assert client.get(f"/api/materials/{uploaded['attachment_id']}", headers=MINA).status_code == 200
    view = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()
    assert view["material_evidence"] == [] and view["answer_resources"] == []
    pack = application.conversation_context_pack(application.authenticated_principal("mina"), UUID(conversation_id), include_exchanges=False)
    assert all(row["ref"] != f"material:{uploaded['attachment_id']}" for row in pack["seeds"])


@pytest.mark.parametrize("status", ["queued", "failed"])
def test_pending_or_failed_independent_material_has_application_rest_mcp_parity(tmp_path, status):
    client, application, worker, settings = _stack(tmp_path)
    folder = client.post("/api/material-folders", headers=MINA, json={"kind": "personal", "title": "미완료 자료"}).json()
    uploaded = client.post(f"/api/material-folders/{folder['folder_id']}/materials", headers=MINA,
        files={"file": ("unavailable.txt", b"\xff" if status == "failed" else b"pendingtoken", "text/plain")}).json()
    if status == "failed":
        assert asyncio.run(worker.run_once())
    expected = application.search_materials(application.authenticated_principal("mina"), "pendingtoken")
    response = client.get("/api/materials/search", headers=MINA, params={"q": "pendingtoken"})
    server = _create_bound_persona_server(McpReportsFacade(settings, "mina"))
    found = asyncio.run(server.call_tool("material_search", {"query": "pendingtoken"})).structured_content
    assert response.status_code == 200 and response.json() == found == expected
    assert found["results"] == [] and found["searched_materials"] == 0
    assert found["unavailable_materials_count"] == 1
    assert found["unavailable_materials"][0]["material_id"] == uploaded["material_id"]
    assert found["unavailable_materials"][0]["extraction"]["status"] == status
    denied = client.get("/api/materials/search", headers={"X-Demo-Persona": "sora"}, params={"q": "pendingtoken"}).json()
    assert denied["results"] == [] and denied["unavailable_materials_count"] == denied["searched_materials"] == 0
    assert "unavailable.txt" not in str(denied)
