"""Owner uploads and lazy legacy discovery publish artifact projections with durable jobs atomically."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ax_workspace.bootstrap.application import WorkflowApplication
from ax_workspace.bootstrap.material_worker import MaterialExtractionWorker
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.native_materials import NativeMaterialRepository
from ax_workspace.platform.durable_jobs import SqlAlchemyDurableJobQueue
from ax_workspace.platform.persistence import AttachmentRecord, AttachmentBindingRecord, DurableJobRecord, EvidenceRecord, MaterialExtractionRecord, make_session_factory
from test_postgres_integration import _postgres_test_url

MINA = {"X-Demo-Persona": "mina"}


def _stack(tmp_path, *, report_provider=None):
    database_url = _postgres_test_url()
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, job_queue_backend="postgres", materials_dir=str(tmp_path / "materials"), recordings_dir=str(tmp_path / "recordings"))
    app = create_app(settings, report_provider=report_provider)
    return TestClient(app), app.state.workflow_application, MaterialExtractionWorker(settings), make_session_factory(database_url)


@pytest.mark.integration
@pytest.mark.parametrize("owner", ["folder", "comment", "evidence"])
def test_owner_upload_rolls_back_artifact_binding_projection_and_job_together(tmp_path, monkeypatch, owner):
    client, application, worker, sessions = _stack(tmp_path)
    if owner == "folder":
        folder = client.post("/api/material-folders", headers=MINA, json={"kind": "personal", "title": "PG 개인 자료"}).json()
        route = f"/api/material-folders/{folder['folder_id']}/materials"
    else:
        request = client.post("/api/work-requests", headers=MINA, json={"title": "PG 요청", "assignee_id": "jiho"}).json()
        route = f"/api/work-requests/{request['request_id']}/evidence"
        if owner == "comment":
            comment = client.post(f"/api/work-requests/{request['request_id']}/comments", headers=MINA, json={"body": "근거 파일"}).json()
            route = f"/api/work-requests/{request['request_id']}/comments/{comment['comment_id']}/attachments"
    original = SqlAlchemyDurableJobQueue.enqueue
    def fail_after_insert(self, job):
        original(self, job)
        raise RuntimeError("injected failure after durable enqueue")
    with monkeypatch.context() as failed:
        failed.setattr(SqlAlchemyDurableJobQueue, "enqueue", fail_after_insert)
        with pytest.raises(RuntimeError, match="injected failure"):
            client.post(route, headers=MINA, files={"file": ("rolled-back.txt", b"postgresownertoken", "text/plain")})
    with sessions() as session:
        for record in (AttachmentRecord, AttachmentBindingRecord, MaterialExtractionRecord, DurableJobRecord, EvidenceRecord):
            assert list(session.scalars(select(record))) == []
    # Retry is a new upload transaction; the orphan storage object from a failed attempt grants no read access.
    retry = client.post(route, headers=MINA, files={"file": ("committed.txt", b"postgresownertoken", "text/plain")})
    assert retry.status_code == 201, retry.text
    with sessions() as session:
        assert len(list(session.scalars(select(MaterialExtractionRecord)))) == 1
        assert len(list(session.scalars(select(DurableJobRecord).where(DurableJobRecord.state == "queued")))) == 1
    assert asyncio.run(worker.run_once())
    found = application.search_materials(application.authenticated_principal("mina"), "postgresownertoken")
    assert found["searched_materials"] == 1 and len(found["results"]) == 1


# main 이 이 파일에 둔 회의 시험 다섯과 헬퍼 둘(`_recording`·`_final`)을 걷었다 — 전부 **옛 회의 모델**의
# 표면(`/recordings/start`·`/stop` · `record_final_meeting_transcript` · MeetingNote·MeetingRawTranscript·
# MeetingRecording 테이블)을 지나는데 SCAX-SPEC-004 가 그 모델을 대체했다.
# 이 파일이 지키는 것(자료 소유자별 업로드가 원장·투영·잡을 **한 트랜잭션**으로 묶는가, 동시 검색이
# 투영을 하나만 만드는가)은 남은 폴더·댓글·증빙·보고 시험이 그대로 지킨다. 회의 자료의 같은 보장을
# 새 모델 위에서 다시 세우는 것은 별도 작업이다.


class _ReportProvider:
    def generate(self, request):
        from ax_workspace.modules.ax_execution.ai import AiGeneration

        return AiGeneration(
            provider_run_ref="pg-report",
            provider_session_ref="pg-report-session",
            body="postgresreporttoken",
            requested_model="fixture",
            observed_model="fixture",
            requested_tier="fast",
            observed_tier="fast",
            latency_ms=1,
            usage={},
        )


def _report_draft(application):
    return application.generate_daily_report_draft(
        application.authenticated_principal("mina"),
        "2026-09-01",
    )


def _report_submit(application, draft):
    return application.submit_daily_report(
        application.authenticated_principal("mina"),
        draft["report_id"],
        draft["draft_id"],
        draft["draft_version"],
        None,
    )


@pytest.mark.integration
def test_report_submission_rolls_back_with_projection_and_durable_enqueue(tmp_path, monkeypatch):
    from ax_workspace.platform.persistence import DailyReportRecord, DailyReportSubmissionRecord

    client, application, worker, sessions = _stack(tmp_path, report_provider=_ReportProvider())
    draft = _report_draft(application)
    original = SqlAlchemyDurableJobQueue.enqueue
    def fail_after_insert(self, job):
        original(self, job)
        raise RuntimeError("injected report enqueue failure")
    with monkeypatch.context() as failed:
        failed.setattr(SqlAlchemyDurableJobQueue, "enqueue", fail_after_insert)
        with pytest.raises(RuntimeError, match="injected report enqueue"):
            _report_submit(application, draft)
    with sessions() as session:
        assert session.get(DailyReportRecord, UUID(draft["report_id"])).status == "draft"
        for record in (DailyReportSubmissionRecord, AttachmentRecord, AttachmentBindingRecord, MaterialExtractionRecord, DurableJobRecord):
            assert list(session.scalars(select(record))) == []
    submitted = _report_submit(application, draft)
    assert asyncio.run(worker.run_once())
    hit = application.search_materials(application.authenticated_principal("mina"), "postgresreporttoken")["results"][0]
    assert client.get(hit["origin"], headers=MINA).json()["source_revision_id"] == submitted["submission_id"]


@pytest.mark.integration
def test_concurrent_legacy_report_discovery_creates_one_material_and_job(tmp_path, monkeypatch):
    _, application, worker, sessions = _stack(tmp_path, report_provider=_ReportProvider())
    draft = _report_draft(application)
    with monkeypatch.context() as legacy:
        legacy.setattr(WorkflowApplication, "_register_native_material", lambda *args: None)
        _report_submit(application, draft)
    barrier = Barrier(2)
    original = NativeMaterialRepository.ensure
    def concurrent_ensure(self, kind, identifier):
        assert kind == "report_submission"
        barrier.wait(timeout=10)
        return original(self, kind, identifier)
    principal = application.authenticated_principal("mina")
    with monkeypatch.context() as concurrent:
        concurrent.setattr(NativeMaterialRepository, "ensure", concurrent_ensure)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(application.search_materials, principal, "postgresreporttoken") for _ in range(2)]
            for future in futures:
                assert future.result(timeout=15)["unavailable_materials"][0]["extraction"]["status"] == "queued"
    with sessions() as session:
        for record in (AttachmentRecord, AttachmentBindingRecord, MaterialExtractionRecord, DurableJobRecord):
            assert len(list(session.scalars(select(record)))) == 1
    assert asyncio.run(worker.run_once())
    assert len(application.search_materials(principal, "postgresreporttoken")["results"]) == 1


@pytest.mark.integration
def test_generic_receipt_is_idempotent_under_concurrent_search_and_survives_projection_purge(tmp_path, monkeypatch):
    from ax_workspace.platform.material_extraction import SqlAlchemyMaterialEvidenceRepository
    from ax_workspace.platform.persistence import ConversationContentEvidenceRecord, ConversationTurnRecord

    client, application, worker, sessions = _stack(tmp_path)
    folder = client.post("/api/material-folders", headers=MINA, json={"kind": "personal", "title": "PG 근거"}).json()
    uploaded = client.post(f"/api/material-folders/{folder['folder_id']}/materials", headers=MINA,
        files={"file": ("receipt.txt", b"postgresreceipttoken", "text/plain")}).json()
    assert asyncio.run(worker.run_once())
    conversation = client.post("/api/conversations", headers=MINA, json={"title": "PG 근거 대화"}).json()
    accepted = client.post(f"/api/conversations/{conversation['conversation_id']}/messages", headers={**MINA, "Idempotency-Key": "pg-evidence"},
        json={"body": "근거 찾아줘", "context": []}).json()
    with sessions() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id
    barrier = Barrier(2)
    original = SqlAlchemyMaterialEvidenceRepository.record
    def concurrent_record(self, *args):
        barrier.wait(timeout=10)
        return original(self, *args)
    principal = application.authenticated_principal("mina")
    with monkeypatch.context() as concurrent:
        concurrent.setattr(SqlAlchemyMaterialEvidenceRepository, "record", concurrent_record)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(application.search_materials, principal, "postgresreceipttoken", execution_id=execution_id) for _ in range(2)]
            for future in futures:
                assert len(future.result(timeout=15)["results"]) == 1
    with sessions() as session:
        rows = list(session.scalars(select(ConversationContentEvidenceRecord)))
        assert len(rows) == 1 and rows[0].excerpt == "postgresreceipttoken"
    view = client.get(f"/api/conversations/{conversation['conversation_id']}", headers=MINA).json()
    assert len(view["material_evidence"]) == 1
    application.purge_attachment(application.authenticated_principal("yuna"), UUID(uploaded["material_id"]), reason="PG 검증")
    with sessions() as session:
        row = session.scalar(select(ConversationContentEvidenceRecord))
        assert row is not None and row.excerpt == "" and row.header_context is None
    assert client.get(f"/api/conversations/{conversation['conversation_id']}", headers=MINA).json()["material_evidence"] == []


@pytest.mark.integration
def test_purge_completed_after_search_cannot_be_undone_by_receipt_publication(tmp_path, monkeypatch):
    from ax_workspace.platform.material_extraction import SqlAlchemyMaterialEvidenceRepository
    from ax_workspace.platform.persistence import ConversationContentEvidenceRecord, ConversationTurnRecord

    client, application, worker, sessions = _stack(tmp_path)
    folder = client.post("/api/material-folders", headers=MINA, json={"kind": "personal", "title": "PG 삭제 경합"}).json()
    uploaded = client.post(f"/api/material-folders/{folder['folder_id']}/materials", headers=MINA,
        files={"file": ("race.txt", b"postgrespurgeracetoken", "text/plain")}).json()
    assert asyncio.run(worker.run_once())
    conversation = client.post("/api/conversations", headers=MINA, json={"title": "경합 근거"}).json()
    accepted = client.post(f"/api/conversations/{conversation['conversation_id']}/messages", headers={**MINA, "Idempotency-Key": "pg-race"},
        json={"body": "근거 확인", "context": []}).json()
    with sessions() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id
    original = SqlAlchemyMaterialEvidenceRepository.record
    def purge_first(self, *args):
        application.purge_attachment(application.authenticated_principal("yuna"), UUID(uploaded["material_id"]), reason="race")
        return original(self, *args)
    monkeypatch.setattr(SqlAlchemyMaterialEvidenceRepository, "record", purge_first)
    application.search_materials(application.authenticated_principal("mina"), "postgrespurgeracetoken", execution_id=execution_id)
    with sessions() as session:
        assert list(session.scalars(select(ConversationContentEvidenceRecord))) == []


@pytest.mark.integration
def test_concurrent_material_metadata_observations_union_contexts_in_one_answer_resource(tmp_path):
    from ax_workspace.platform.persistence import ConversationTurnRecord, ConversationAnswerResourceRecord
    from ax_workspace.platform.work_tasks import SqlAlchemyAttachmentRepository

    client, application, _, sessions = _stack(tmp_path)
    tasks = [client.post('/api/tasks', headers=MINA, json={'title': title}).json()['task_id'] for title in ('첫 관측', '둘째 관측')]
    uploaded = client.post(f'/api/tasks/{tasks[0]}/materials', headers=MINA, data={'kind': 'input'},
        files={'file': ('observed.txt', b'metadata', 'text/plain')}).json()
    with sessions() as session:
        other = SqlAlchemyAttachmentRepository(session).bind(attachment_id=UUID(uploaded['material_id']), context_type='task', context_id=tasks[1], role='input', bound_by='mina')
        session.commit()
        bindings = [uploaded['binding_id'], str(other.id)]
    conversation = client.post('/api/conversations', headers=MINA, json={'title': '동시 목록 관측'}).json()
    accepted = client.post(f"/api/conversations/{conversation['conversation_id']}/messages", headers={**MINA, 'Idempotency-Key': 'metadata-union'}, json={'body': '자료 목록', 'context': []}).json()
    with sessions() as session:
        execution = session.get(ConversationTurnRecord, UUID(accepted['turn_id'])).execution_id
    principal = application.authenticated_principal('mina')
    barrier = Barrier(2)
    def observe(index):
        barrier.wait(timeout=10)
        return application.record_answer_resources(principal, execution, [{
            'resource_type': 'material', 'resource_id': uploaded['material_id'], 'integrity_ref': uploaded['integrity_ref'],
            'source_contexts': [{'resource_type': 'task', 'resource_id': tasks[index], 'binding_id': bindings[index]}],
        }])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(observe, index) for index in range(2)]
        assert sorted(future.result(timeout=15) for future in futures) == [0, 1]
    with sessions() as session:
        rows = list(session.scalars(select(ConversationAnswerResourceRecord)))
        assert len(rows) == 1
        assert {context['binding_id'] for context in rows[0].source_contexts} == set(bindings)
    client.post(f"/api/tasks/{tasks[0]}/material-bindings/{bindings[0]}/detach", headers=MINA)
    view = client.get(f"/api/conversations/{conversation['conversation_id']}", headers=MINA).json()
    assert len(view['answer_resources']) == 1
    assert [context['binding_id'] for context in view['answer_resources'][0]['source_contexts']] == [bindings[1]]
