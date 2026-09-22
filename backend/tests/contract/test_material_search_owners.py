"""Canonical material search resolves permitted owner contexts before touching the content index."""
import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from ax_workspace.modules.organization_access.domain import WORK_REQUEST_READ
from ax_workspace.modules.work.materials import MaterialNotFound
from ax_workspace.modules.jobs.domain import JOB_KIND_MATERIAL_EXTRACTION
from ax_workspace.modules.work.requests import WorkRequestApplication
from ax_workspace.platform.material_extraction import PypdfTextExtractor, SqlAlchemyMaterialExtractionRepository
from ax_workspace.platform.persistence import AttachmentBindingRecord, EvidenceRecord, ResourceRelationshipRecord, make_session_factory
from ax_workspace.platform.work_tasks import SqlAlchemyAttachmentRepository
from legacy_acceptance import pending_request
from test_material_search import MINA, JIHO, _stack, _upload

# 이 파일의 테스트는 **진짜 자식 프로세스**를 띄우고(자료·보고서 워커의 `IsolatedWork` spawn ·
# MCP `stdio_client` · `subprocess`) 그 진행을 초 단위 실시간 창으로 잰다 — 그래서 병렬 패스가 아니라
# `-n0` 직렬 패스에서 돈다. 기준과 걸개는 `tests/conftest.py`, 가르는 자리는 `Makefile` 의 `test-serial`.
pytestmark = pytest.mark.serial


class OwnerPartialExtractor:
    def extract(self, **kwargs):
        return replace(
            PypdfTextExtractor().extract(**kwargs),
            status="partial",
            warnings=("page 2 needs OCR",),
            coverage={
                "complete": False,
                "missing_units": [{"page": 2, "reason": "needs_ocr"}],
            },
        )


def test_one_artifact_has_one_hit_with_only_its_currently_readable_task_contexts(tmp_path):
    client, application, worker, settings = _stack(tmp_path)
    first = client.post("/api/tasks", headers=MINA, json={"title": "공개 첫 연결"}).json()
    second = client.post("/api/tasks", headers=MINA, json={"title": "공개 둘째 연결"}).json()
    secret = client.post("/api/tasks", headers=JIHO, json={"title": "비공개 연결 이름"}).json()
    material = _upload(client, first["task_id"], "shared.txt", b"canonicalartifacttoken", "text/plain").json()
    with make_session_factory(settings.database_url)() as session:
        attachments = SqlAlchemyAttachmentRepository(session)
        for task in (second, secret):
            attachments.bind(attachment_id=UUID(material["attachment_id"]), context_type="task", context_id=task["task_id"], role="input", bound_by="mina")
        session.commit()
    assert asyncio.run(worker.run_once())
    found = application.search_materials(application.authenticated_principal("mina"), "canonicalartifacttoken")
    assert found["searched_materials"] == 1 and len(found["results"]) == 1
    hit = found["results"][0]
    assert hit["material_id"] == material["attachment_id"]
    assert {context["resource_id"] for context in hit["source_contexts"]} == {first["task_id"], second["task_id"]}
    assert all(context["resource_type"] == "task" for context in hit["source_contexts"])
    assert "비공개 연결 이름" not in str(found) and secret["task_id"] not in str(found)
    other = application.search_materials(application.authenticated_principal("jiho"), "canonicalartifacttoken")
    assert {context["resource_id"] for context in other["results"][0]["source_contexts"]} == {secret["task_id"]}
    scoped = application.search_materials(application.authenticated_principal("mina"), "canonicalartifacttoken",
                                          resource_type="task", resource_id=second["task_id"])
    assert len(scoped["results"]) == 1
    assert scoped["results"][0]["source_contexts"][0]["resource_id"] == second["task_id"]


def test_request_comment_and_submission_files_are_searchable_without_a_task(tmp_path):
    client, application, worker, settings = _stack(tmp_path)
    request = pending_request(client, settings.database_url, MINA,
                              title="Task 없는 요청", assignee_id="jiho", cc_member_ids=["yuna"])
    rid = request["request_id"]
    comment = client.post(f"/api/work-requests/{rid}/comments", headers=MINA, json={"body": "첨부 참고"}).json()
    uploaded = client.post(f"/api/work-requests/{rid}/comments/{comment['comment_id']}/attachments", headers=MINA,
                           files={"file": ("discussion.txt", b"requestbodytoken discussion", "text/plain")})
    assert uploaded.status_code == 201
    evidence = client.post(f"/api/work-requests/{rid}/evidence", headers=JIHO,
                           files={"file": ("submission.txt", b"requestbodytoken submission", "text/plain")})
    assert evidence.status_code == 201
    assert asyncio.run(worker.run_once())
    expected = {uploaded.json()["attachments"][0]["attachment_id"], evidence.json()["attachment_id"]}
    for member in ("mina", "jiho", "yuna"):
        found = application.search_materials(application.authenticated_principal(member), "requestbodytoken", resource_types=["work_request"])
        assert {hit["material_id"] for hit in found["results"]} == expected
        assert found["searched_materials"] == 2
        assert all(hit["source_resource_id"] == rid and hit["source_resource_type"] == "work_request" for hit in found["results"])
    denied = application.search_materials(application.authenticated_principal("sora"), "requestbodytoken")
    assert denied["results"] == [] and denied["searched_materials"] == 0 and denied["unavailable_materials"] == []


def test_revoked_request_context_never_reaches_index_or_open_and_does_not_hide_a_readable_task(tmp_path, monkeypatch):
    client, application, worker, settings = _stack(tmp_path)
    # 근거는 **판단 회차**에 붙는다 — 신규 `assigned` 요청에는 그 회차가 없으므로 과거 모양 행에서 본다.
    request = pending_request(client, settings.database_url, MINA,
                              title="회수될 요청 이름", assignee_id="jiho", cc_member_ids=["yuna"])
    rid = request["request_id"]
    uploaded = client.post(f"/api/work-requests/{rid}/evidence", headers=JIHO,
                           files={"file": ("shared.txt", b"revocationtoken", "text/plain")}).json()
    artifact_id = UUID(uploaded["attachment_id"])
    task = client.post("/api/tasks", headers={"X-Demo-Persona": "yuna"}, json={"title": "남아 있는 연결"}).json()
    with make_session_factory(settings.database_url)() as session:
        SqlAlchemyAttachmentRepository(session).bind(attachment_id=artifact_id, context_type="task", context_id=task["task_id"], role="input", bound_by="yuna")
        session.commit()
    assert asyncio.run(worker.run_once())
    principal = application.authenticated_principal("yuna")
    initial = application.search_materials(principal, "revocationtoken")
    assert len(initial["results"]) == 1
    assert {context["resource_type"] for context in initial["results"][0]["source_contexts"]} == {"task", "work_request"}
    request_only = replace(principal, capabilities=frozenset({WORK_REQUEST_READ}))
    assert application.search_materials(request_only, "revocationtoken")["searched_materials"] == 1
    with make_session_factory(settings.database_url)() as session:
        cc = session.scalar(select(ResourceRelationshipRecord).where(ResourceRelationshipRecord.resource_id == rid,
                            ResourceRelationshipRecord.relationship_kind == "cc", ResourceRelationshipRecord.member_id == "yuna"))
        cc.valid_until = datetime.now(UTC)
        session.commit()
    observed = []
    original = SqlAlchemyMaterialExtractionRepository.for_attachments
    def record_candidates(self, attachment_ids):
        observed.append(set(attachment_ids))
        return original(self, attachment_ids)
    monkeypatch.setattr(SqlAlchemyMaterialExtractionRepository, "for_attachments", record_candidates)
    denied = application.search_materials(request_only, "revocationtoken")
    assert observed == [set()] and denied["searched_materials"] == 0
    assert denied["results"] == [] and denied["unavailable_materials_count"] == 0
    for hidden_id in (rid, str(uuid4())):
        with pytest.raises(MaterialNotFound, match="resource was not found"):
            application.search_materials(request_only, "revocationtoken", resource_type="work_request", resource_id=hidden_id)
    assert client.get(f"/api/work-requests/{rid}/attachments/{artifact_id}/content", headers={"X-Demo-Persona": "yuna"}).status_code == 404
    remaining = application.search_materials(principal, "revocationtoken")
    assert len(remaining["results"]) == 1
    assert "회수될 요청 이름" not in str(remaining) and rid not in str(remaining)
    assert remaining["results"][0]["source_contexts"][0]["resource_type"] == "task"


@pytest.mark.parametrize("invalidity", ["unbound", "unadopted", "snapshot_mismatch", "mutable"])
def test_submission_file_needs_a_live_binding_and_immutable_adoption(tmp_path, invalidity):
    client, application, worker, settings = _stack(tmp_path)
    request = pending_request(client, settings.database_url, MINA, title="참여 요청", assignee_id="jiho")
    rid = request["request_id"]
    uploaded = client.post(f"/api/work-requests/{rid}/evidence", headers=MINA,
                           files={"file": ("excluded.txt", b"exclusiontoken", "text/plain")}).json()
    artifact_id = UUID(uploaded["attachment_id"])
    assert asyncio.run(worker.run_once())
    principal = application.authenticated_principal("mina")
    assert application.search_materials(principal, "exclusiontoken")["searched_materials"] == 1
    with make_session_factory(settings.database_url)() as session:
        adoption = session.get(EvidenceRecord, UUID(uploaded["evidence_id"]))
        if invalidity == "unbound":
            binding = session.scalar(select(AttachmentBindingRecord).where(AttachmentBindingRecord.attachment_id == artifact_id))
            binding.unbound_at = datetime.now(UTC)
        elif invalidity == "unadopted":
            session.delete(adoption)
        elif invalidity == "snapshot_mismatch":
            adoption.fixed_snapshot_ref = "sha256:different"
        else:
            adoption.mutable_source = True
        session.commit()
    result = application.search_materials(principal, "exclusiontoken")
    assert result["results"] == [] and result["searched_materials"] == 0 and result["unavailable_materials"] == []
    with pytest.raises(MaterialNotFound, match="material was not found"):
        application.search_materials(principal, "exclusiontoken", material_id=artifact_id)
    assert client.get(f"/api/work-requests/{rid}/attachments/{artifact_id}/content", headers=MINA).status_code == 404


def test_reading_legacy_files_never_enqueues_and_the_worker_backfills_missing_projections(tmp_path, monkeypatch):
    client, application, worker, settings = _stack(tmp_path)
    with monkeypatch.context() as old_deployment:
        old_deployment.setattr(WorkRequestApplication, "_request_extraction", lambda *args: None)
        request = pending_request(client, settings.database_url, MINA, title="이전 배포 첨부", assignee_id="jiho")
        uploaded = client.post(f"/api/work-requests/{request['request_id']}/evidence", headers=MINA,
                               files={"file": ("legacy.txt", b"legacyrequesttoken", "text/plain")}).json()
    assert application.memory_job_queue.pending_count(JOB_KIND_MATERIAL_EXTRACTION) == 0
    assert application.search_materials(application.authenticated_principal("sora"), "legacyrequesttoken")["results"] == []
    assert application.memory_job_queue.pending_count(JOB_KIND_MATERIAL_EXTRACTION) == 0
    principal = application.authenticated_principal("mina")
    for _ in range(2):
        pending = application.search_materials(principal, "legacyrequesttoken")
        assert pending["results"] == [] and pending["unavailable_materials"][0]["extraction"] is None
        assert application.memory_job_queue.pending_count(JOB_KIND_MATERIAL_EXTRACTION) == 0
    assert asyncio.run(worker.run_once())  # Explicit worker maintenance registers the missing projection.
    assert application.memory_job_queue.pending_count(JOB_KIND_MATERIAL_EXTRACTION) == 1
    assert asyncio.run(worker.run_once())
    assert application.search_materials(principal, "legacyrequesttoken")["results"][0]["material_id"] == uploaded["attachment_id"]


def test_worker_legacy_backfill_and_unavailable_metadata_are_bounded_without_hiding_the_total(tmp_path, monkeypatch):
    client, application, worker, settings = _stack(tmp_path)
    with monkeypatch.context() as old_deployment:
        old_deployment.setattr(WorkRequestApplication, "_request_extraction", lambda *args: None)
        request = pending_request(client, settings.database_url, MINA, title="이전 첨부 묶음", assignee_id="jiho")
        for sequence in range(22):
            response = client.post(f"/api/work-requests/{request['request_id']}/evidence", headers=MINA,
                                   files={"file": (f"legacy-{sequence}.txt", b"batchtoken", "text/plain")})
            assert response.status_code == 201
    principal = application.authenticated_principal("mina")
    pending = application.search_materials(principal, "batchtoken")
    assert pending["searched_materials"] == 0 and pending["results"] == []
    assert pending["unavailable_materials_count"] == 22 and pending["unavailable_truncated"]
    assert len(pending["unavailable_materials"]) == 20
    assert application.memory_job_queue.pending_count(JOB_KIND_MATERIAL_EXTRACTION) == 0
    assert asyncio.run(worker.run_once())
    assert application.memory_job_queue.pending_count(JOB_KIND_MATERIAL_EXTRACTION) == settings.material_worker_concurrency
    application.search_materials(principal, "batchtoken")
    assert application.memory_job_queue.pending_count(JOB_KIND_MATERIAL_EXTRACTION) == settings.material_worker_concurrency


def test_inherited_evidence_keeps_its_original_binding_and_one_search_hit(tmp_path):
    client, application, worker, settings = _stack(tmp_path)
    request = pending_request(client, settings.database_url, MINA, title="이전 제목", assignee_id="jiho")
    rid = request["request_id"]
    uploaded = client.post(f"/api/work-requests/{rid}/evidence", headers=MINA,
                           files={"file": ("inherited.txt", b"inheritedtoken", "text/plain")}).json()
    amended = client.post(f"/api/work-requests/{rid}/amend", headers=MINA,
                          json={"expected_version": uploaded["request_version"], "title": "현재 제목"})
    assert amended.status_code == 200
    assert asyncio.run(worker.run_once())
    found = application.search_materials(application.authenticated_principal("mina"), "inheritedtoken")
    assert found["searched_materials"] == 1 and len(found["results"]) == 1
    [context] = found["results"][0]["source_contexts"]
    assert context["title"] == "현재 제목" and context["binding_context_id"] == uploaded["submission_id"]
    opened = client.get(context["origin"], headers=MINA)
    assert opened.status_code == 200 and opened.content == b"inheritedtoken"


def test_generic_partial_search_requires_the_canonical_artifact_anchor_and_retains_no_hit_coverage(tmp_path):
    client, application, worker, settings = _stack(tmp_path)
    request = pending_request(client, settings.database_url, MINA, title="부분 자료 요청", assignee_id="jiho")
    uploaded = client.post(f"/api/work-requests/{request['request_id']}/evidence", headers=MINA,
                           files={"file": ("partial.txt", b"partialrequesttoken", "text/plain")}).json()
    worker._extractor = OwnerPartialExtractor()
    assert asyncio.run(worker.run_once())
    principal = application.authenticated_principal("mina")
    for filters in ({}, {"resource_type": "work_request", "resource_id": request["request_id"]}):
        found = application.search_materials(principal, "partialrequesttoken", **filters)
        assert found["results"] == [] and found["searched_materials"] == 0
        assert found["unavailable_materials"][0]["extraction"]["status"] == "partial"
    selected = application.search_materials(principal, "partialrequesttoken", material_id=UUID(uploaded["attachment_id"]))
    hit = selected["results"][0]
    assert hit["material_id"] == uploaded["attachment_id"] and hit["extraction"]["status"] == "partial"
    assert hit["extraction"]["coverage"]["missing_units"] == [{"page": 2, "reason": "needs_ocr"}]
    no_hit = application.search_materials(principal, "absenttoken", material_id=UUID(uploaded["attachment_id"]))
    assert no_hit["results"] == [] and no_hit["selected_material"]["extraction"] == hit["extraction"]
    with pytest.raises(MaterialNotFound, match="material was not found"):
        application.search_materials(principal, "partialrequesttoken", material_id=UUID(hit["source_contexts"][0]["binding_id"]))
