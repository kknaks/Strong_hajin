"""Canonical content receipts retain observed owner contexts and reauthorize them on every read."""
import asyncio
from uuid import UUID

from ax_workspace.platform.persistence import ConversationTurnRecord, make_session_factory
from test_report_material_search import _stack, _draft, _submit, MINA


def _turn(client, settings):
    conversation = client.post("/api/conversations", headers=MINA, json={"title": "자료 근거"}).json()
    accepted = client.post(f"/api/conversations/{conversation['conversation_id']}/messages", headers={**MINA, "Idempotency-Key": "canonical-evidence"},
        json={"body": "자료에서 근거 찾아줘", "context": []})
    assert accepted.status_code == 202
    with make_session_factory(settings.database_url)() as session:
        turn = session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"]))
        return conversation["conversation_id"], str(turn.id), turn.execution_id


def test_generic_report_search_records_canonical_receipt_and_current_owner_context(tmp_path):
    client, application, worker, settings = _stack(tmp_path)
    submitted = _submit(application, _draft(application, "reportreceipttoken"))
    assert asyncio.run(worker.run_once())
    conversation_id, turn_id, execution_id = _turn(client, settings)
    principal = application.authenticated_principal("mina")
    found = application.search_materials(principal, "reportreceipttoken", execution_id=execution_id)
    application.search_materials(principal, "reportreceipttoken", execution_id=execution_id)
    evidence = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()["material_evidence"]
    assert len(evidence) == 1
    row = evidence[0]
    assert row["turn_id"] == turn_id and row["material_id"] == found["results"][0]["material_id"]
    assert row["attachment_id"] == row["material_id"] and "task_id" not in row
    assert row["source_contexts"][0]["resource_type"] == "report"
    assert row["source_locator"]["source_revision_id"] == submitted["submission_id"]
    assert row["origin"] == found["results"][0]["origin"] and row["excerpt"] == "reportreceipttoken"


def test_followup_pack_reauthorizes_material_seeds_and_omits_old_content_answers(tmp_path):
    from datetime import UTC, datetime
    from sqlalchemy import delete
    from ax_workspace.platform.persistence import ConversationMessageRecord, RoleCapabilityRecord

    client, application, worker, settings = _stack(tmp_path)
    _submit(application, _draft(application, "oldprivatecontenttoken"))
    assert asyncio.run(worker.run_once())
    conversation_id, turn_id, execution_id = _turn(client, settings)
    principal = application.authenticated_principal("mina")
    hit = application.search_materials(principal, "oldprivatecontenttoken", execution_id=execution_id)["results"][0]
    with make_session_factory(settings.database_url)() as session:
        session.add(ConversationMessageRecord(conversation_id=UUID(conversation_id), turn_id=UUID(turn_id), role="assistant",
            body="oldprivatecontenttoken 과거 답변", sequence=2, created_at=datetime.now(UTC), body_state="final"))
        session.commit()
    pack = application.conversation_context_pack(principal, UUID(conversation_id), include_exchanges=False)
    assert pack["reset_provider_session"] is True
    assert any(row["ref"] == f"material:{hit['material_id']}" for row in pack["seeds"])
    assert all("oldprivatecontenttoken" not in row["body"] for row in pack["exchanges"])
    with make_session_factory(settings.database_url)() as session:
        session.execute(delete(RoleCapabilityRecord).where(RoleCapabilityRecord.capability_id == "daily_report.read"))
        session.commit()
    revoked = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()
    assert revoked["material_evidence"] == []
    pack = application.conversation_context_pack(principal, UUID(conversation_id), include_exchanges=True)
    assert pack["reset_provider_session"] is True and pack["seeds"] == []
    assert all("oldprivatecontenttoken" not in row["body"] for row in pack["exchanges"])


def test_receipt_keeps_only_live_observed_contexts_and_records_later_observations(tmp_path):
    from datetime import UTC, datetime
    from sqlalchemy import select
    from ax_workspace.platform.persistence import AttachmentBindingRecord
    from ax_workspace.platform.work_tasks import SqlAlchemyAttachmentRepository

    client, application, worker, settings = _stack(tmp_path)
    first = client.post("/api/tasks", headers=MINA, json={"title": "처음 관측한 연결"}).json()
    second = client.post("/api/tasks", headers=MINA, json={"title": "유지할 연결"}).json()
    uploaded = client.post(f"/api/tasks/{first['task_id']}/materials", headers=MINA,
        files={"file": ("contexts.txt", b"contextreceipttoken", "text/plain")}, data={"kind": "input"}).json()
    with make_session_factory(settings.database_url)() as session:
        artifact_id = UUID(uploaded["material_id"])
        SqlAlchemyAttachmentRepository(session).bind(attachment_id=artifact_id, context_type="task", context_id=second["task_id"], role="input", bound_by="mina")
        session.commit()
    assert asyncio.run(worker.run_once())
    conversation_id, _, execution_id = _turn(client, settings)
    principal = application.authenticated_principal("mina")
    application.search_materials(principal, "contextreceipttoken", execution_id=execution_id)
    third = client.post("/api/tasks", headers=MINA, json={"title": "아직 관측하지 않은 연결"}).json()
    with make_session_factory(settings.database_url)() as session:
        session.get(AttachmentBindingRecord, UUID(uploaded["binding_id"])).unbound_at = datetime.now(UTC)
        SqlAlchemyAttachmentRepository(session).bind(attachment_id=artifact_id, context_type="task", context_id=third["task_id"], role="input", bound_by="mina")
        session.commit()
    evidence = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()["material_evidence"]
    assert [row["resource_id"] for row in evidence[0]["source_contexts"]] == [second["task_id"]]
    assert "처음 관측한 연결" not in str(evidence) and "아직 관측하지 않은 연결" not in str(evidence)
    application.search_materials(principal, "contextreceipttoken", execution_id=execution_id)
    evidence = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()["material_evidence"]
    assert len(evidence) == 1
    assert {row["resource_id"] for row in evidence[0]["source_contexts"]} == {second["task_id"], third["task_id"]}


def test_worker_followup_does_not_resume_a_provider_checkpoint_with_revoked_material(tmp_path):
    from sqlalchemy import delete
    from ax_workspace.bootstrap.conversation_worker import ConversationWorker
    from ax_workspace.modules.ax_execution.ai import AiConversationResult
    from ax_workspace.platform.conversation_jobs import ConversationJobQueue
    from ax_workspace.platform.persistence import RoleCapabilityRecord

    client, application, worker, settings = _stack(tmp_path)
    _submit(application, _draft(application, "providerprivatecontenttoken"))
    assert asyncio.run(worker.run_once())
    conversation_id, _, execution_id = _turn(client, settings)
    application.search_materials(application.authenticated_principal("mina"), "providerprivatecontenttoken", execution_id=execution_id)
    requests = []
    class Provider:
        def converse(self, request, *, sink=None, cancel=None):
            requests.append(request)
            return AiConversationResult("fixture-run", "old-provider-checkpoint", "providerprivatecontenttoken 답변", [])
    conversation_worker = ConversationWorker(settings, provider=Provider(), queue_factory=lambda session: ConversationJobQueue(application.memory_job_queue, worker_id="receipt-test"))
    assert asyncio.run(conversation_worker.run_once())
    with make_session_factory(settings.database_url)() as session:
        session.execute(delete(RoleCapabilityRecord).where(RoleCapabilityRecord.capability_id == "daily_report.read"))
        session.commit()
    accepted = client.post(f"/api/conversations/{conversation_id}/messages", headers={**MINA, "Idempotency-Key": "next-evidence-turn"},
        json={"body": "그 자료 다시 확인해줘", "context": []})
    assert accepted.status_code == 202
    assert asyncio.run(conversation_worker.run_once())
    assert len(requests) == 2 and requests[-1].provider_session_ref is None
    assert requests[-1].seed_references == ()
    assert all("providerprivatecontenttoken" not in row["body"] for row in requests[-1].recent_exchanges)


def test_no_hit_material_tool_observation_also_resets_provider_memory(tmp_path):
    from datetime import UTC, datetime
    from ax_workspace.platform.persistence import ConversationMessageRecord, ToolInvocationRecord

    client, application, _, settings = _stack(tmp_path)
    conversation_id, turn_id, _ = _turn(client, settings)
    with make_session_factory(settings.database_url)() as session:
        session.add(ToolInvocationRecord(turn_id=UUID(turn_id), sequence=1, tool_name="material_search", display_name="자료 검색",
            input_summary="검색", state="completed", result_summary="unavailableprivatenametoken 자료 처리 중"))
        session.add(ConversationMessageRecord(conversation_id=UUID(conversation_id), turn_id=UUID(turn_id), sequence=2,
            role="assistant", body="unavailableprivatenametoken", body_state="final", created_at=datetime.now(UTC)))
        session.commit()
    pack = application.conversation_context_pack(application.authenticated_principal("mina"), UUID(conversation_id), include_exchanges=False)
    assert pack["reset_provider_session"] is True and pack["seeds"] == []
    assert "unavailableprivatenametoken" not in str(pack)
    view = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()
    assert "unavailableprivatenametoken" not in str(view["tool_invocations"])


def test_another_principal_cannot_record_on_the_execution_even_with_no_hits(tmp_path):
    import pytest

    client, application, _, settings = _stack(tmp_path)
    conversation_id, _, execution_id = _turn(client, settings)
    with pytest.raises(ValueError, match="another principal"):
        application.search_materials(application.authenticated_principal("jiho"), "nothing", execution_id=execution_id)
    assert client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()["material_evidence"] == []


def test_purge_removes_generic_excerpt_and_header_and_hides_the_receipt(tmp_path):
    from sqlalchemy import select
    from ax_workspace.platform.persistence import ConversationContentEvidenceRecord

    client, application, worker, settings = _stack(tmp_path)
    folder = client.post("/api/material-folders", headers=MINA, json={"kind": "personal", "title": "자료"}).json()
    uploaded = client.post(f"/api/material-folders/{folder['folder_id']}/materials", headers=MINA,
        files={"file": ("purge.txt", b"purgecontentreceipttoken", "text/plain")}).json()
    assert asyncio.run(worker.run_once())
    conversation_id, _, execution_id = _turn(client, settings)
    application.search_materials(application.authenticated_principal("mina"), "purgecontentreceipttoken", execution_id=execution_id)
    with make_session_factory(settings.database_url)() as session:
        row = session.scalar(select(ConversationContentEvidenceRecord))
        row.header_context = {"preview": "copied header"}
        session.commit()
    application.purge_attachment(application.authenticated_principal("yuna"), UUID(uploaded["material_id"]), reason="검증")
    with make_session_factory(settings.database_url)() as session:
        row = session.scalar(select(ConversationContentEvidenceRecord))
        assert row.excerpt == "" and row.header_context is None
    assert client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()["material_evidence"] == []


def test_purge_between_search_and_receipt_does_not_resurrect_copied_text(tmp_path, monkeypatch):
    from sqlalchemy import select
    from ax_workspace.platform.material_extraction import SqlAlchemyMaterialEvidenceRepository
    from ax_workspace.platform.persistence import ConversationContentEvidenceRecord

    client, application, worker, settings = _stack(tmp_path)
    folder = client.post("/api/material-folders", headers=MINA, json={"kind": "personal", "title": "삭제 경합"}).json()
    uploaded = client.post(f"/api/material-folders/{folder['folder_id']}/materials", headers=MINA,
        files={"file": ("race.txt", b"purgeraceresurrectiontoken", "text/plain")}).json()
    assert asyncio.run(worker.run_once())
    _, _, execution_id = _turn(client, settings)
    original = SqlAlchemyMaterialEvidenceRepository.record
    def purge_before_record(self, *args):
        application.purge_attachment(application.authenticated_principal("yuna"), UUID(uploaded["material_id"]), reason="race")
        return original(self, *args)
    monkeypatch.setattr(SqlAlchemyMaterialEvidenceRepository, "record", purge_before_record)
    application.search_materials(application.authenticated_principal("mina"), "purgeraceresurrectiontoken", execution_id=execution_id)
    with make_session_factory(settings.database_url)() as session:
        assert list(session.scalars(select(ConversationContentEvidenceRecord))) == []


def test_recorded_historical_native_revision_remains_a_followup_seed(tmp_path):
    client, application, worker, settings = _stack(tmp_path)
    principal = application.authenticated_principal("mina")
    draft = _draft(application, "oldsubmissionreceipttoken")
    submitted = _submit(application, draft)
    assert asyncio.run(worker.run_once())
    conversation_id, _, execution_id = _turn(client, settings)
    hit = application.search_materials(principal, "oldsubmissionreceipttoken", execution_id=execution_id)["results"][0]
    edited = application.edit_daily_report(principal, draft["report_id"], draft["draft_id"], draft["draft_version"], "newsubmissionreceipttoken", [], [])
    _submit(application, edited, "정정")
    assert asyncio.run(worker.run_once())
    assert application.search_materials(principal, "oldsubmissionreceipttoken")["results"] == []
    evidence = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()["material_evidence"]
    assert evidence[0]["source_locator"]["source_revision_id"] == submitted["submission_id"]
    assert evidence[0]["source_contexts"][0]["is_current_revision"] is False
    pack = application.conversation_context_pack(principal, UUID(conversation_id), include_exchanges=False)
    assert any(row["ref"] == f"material:{hit['material_id']}" for row in pack["seeds"])
