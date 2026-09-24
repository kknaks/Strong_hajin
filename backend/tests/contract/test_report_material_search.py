"""Submitted Report revisions are searchable only through their current owner read policy."""
import asyncio
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from ax_workspace.bootstrap.material_worker import MaterialExtractionWorker
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from test_product_operations import ContractTestAiProvider

# 이 파일의 테스트는 **진짜 자식 프로세스**를 띄우고(자료·보고서 워커의 `IsolatedWork` spawn ·
# MCP `stdio_client` · `subprocess`) 그 진행을 초 단위 실시간 창으로 잰다 — 그래서 병렬 패스가 아니라
# `-n0` 직렬 패스에서 돈다. 기준과 걸개는 `tests/conftest.py`, 가르는 자리는 `Makefile` 의 `test-serial`.
pytestmark = pytest.mark.serial

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"), recordings_dir=str(tmp_path / "recordings"))
    app = create_app(settings, report_provider=ContractTestAiProvider())
    application = app.state.workflow_application
    worker = MaterialExtractionWorker(settings, queue_factory=lambda session: application.memory_job_queue)
    return TestClient(app), application, worker, settings


def _draft(application, body, report_date="2026-09-01"):
    principal = application.authenticated_principal("mina")
    generated = application.generate_daily_report_draft(principal, report_date)
    return application.edit_daily_report(principal, generated["report_id"], generated["draft_id"], generated["draft_version"], body, [], [])


def _submit(application, draft, reason=None):
    return application.submit_daily_report(application.authenticated_principal("mina"), draft["report_id"], draft["draft_id"], draft["draft_version"], reason)


def test_submitted_report_has_its_own_material_identity_exact_revision_and_owner_only_original(tmp_path):
    client, application, worker, _ = _stack(tmp_path)
    draft = _draft(application, "확정 보고서 reportsearchtoken")
    submitted = _submit(application, draft)
    assert asyncio.run(worker.run_once())
    found = application.search_materials(application.authenticated_principal("mina"), "reportsearchtoken", resource_types=["report"])
    assert len(found["results"]) == 1
    hit = found["results"][0]
    assert hit["source_resource_type"] == "report" and hit["source_resource_id"] == submitted["report_id"]
    assert hit["source_locator"]["source_revision_id"] == submitted["submission_id"]
    assert hit["source_locator"]["submission_version"] == 1
    opened = client.get(hit["origin"], headers=MINA)
    assert opened.status_code == 200 and opened.json()["body"] == submitted["body"]
    assert opened.json()["source_revision_id"] == submitted["submission_id"]
    assert client.get(hit["origin"], headers=JIHO).status_code == 404
    denied = application.search_materials(application.authenticated_principal("jiho"), "reportsearchtoken")
    assert denied["results"] == [] and denied["unavailable_materials_count"] == 0


def test_drafts_are_excluded_and_current_submission_survives_edits_with_explicit_history(tmp_path):
    client, application, worker, _ = _stack(tmp_path)
    principal = application.authenticated_principal("mina")
    draft = _draft(application, "oldreporttoken")
    assert application.search_materials(principal, "oldreporttoken")["unavailable_materials_count"] == 0
    _submit(application, draft)
    assert asyncio.run(worker.run_once())
    old = application.search_materials(principal, "oldreporttoken")["results"][0]
    edited = application.edit_daily_report(principal, draft["report_id"], draft["draft_id"], draft["draft_version"], "newreporttoken", [], [])
    assert application.search_materials(principal, "newreporttoken")["results"] == []
    assert application.search_materials(principal, "oldreporttoken")["results"][0]["source_contexts"][0]["is_current_revision"] is True
    _submit(application, edited, "내용 정정")
    assert asyncio.run(worker.run_once())
    assert application.search_materials(principal, "oldreporttoken")["results"] == []
    latest = application.search_materials(principal, "newreporttoken")["results"][0]
    assert latest["material_id"] != old["material_id"] and latest["source_locator"]["submission_version"] == 2
    pinned = application.search_materials(principal, "oldreporttoken", material_id=UUID(old["material_id"]))
    assert pinned["results"][0]["source_contexts"][0]["is_current_revision"] is False
    assert client.get(old["origin"], headers=MINA).json()["body"] == "oldreporttoken"


def test_report_discovery_is_not_limited_to_recent_ui_reports_and_keeps_long_body_tail(tmp_path):
    _, application, worker, _ = _stack(tmp_path)
    principal = application.authenticated_principal("mina")
    body = "보고서의 일반 내용입니다.\n" * 18000 + "reportlongtailtoken"
    oldest = _submit(application, _draft(application, body, "2026-08-01"))
    for day in range(2, 6):
        _submit(application, _draft(application, "최근 보고 내용", f"2026-08-{day:02}"))
    assert asyncio.run(worker.run_once())
    assert oldest["report_id"] not in {row["report_id"] for row in application.daily_report_recent(principal)}
    hit = application.search_materials(principal, "reportlongtailtoken")["results"][0]
    assert hit["source_resource_id"] == oldest["report_id"]
    locator = hit["source_locator"]
    assert "reportlongtailtoken" in body[locator["char_start"]:locator["char_end"]]
    assert hit["extraction"]["coverage"]["complete"] is True


def test_report_source_is_read_only_after_authorization_and_rechecks_old_principal(tmp_path, monkeypatch):
    from dataclasses import replace
    import pytest
    from sqlalchemy import delete
    from ax_workspace.bootstrap.application import WorkflowApplication
    from ax_workspace.modules.work.materials import MaterialNotFound
    from ax_workspace.platform.native_materials import NativeMaterialRepository
    from ax_workspace.platform.persistence import RoleCapabilityRecord, make_session_factory

    client, application, worker, settings = _stack(tmp_path)
    with monkeypatch.context() as legacy:
        legacy.setattr(WorkflowApplication, "_register_native_material", lambda *args: None)
        _submit(application, _draft(application, "legacyreporttoken"))
    observed = []
    original = NativeMaterialRepository.payload
    def capture(self, kind, identifier):
        observed.append(str(identifier))
        return original(self, kind, identifier)
    monkeypatch.setattr(NativeMaterialRepository, "payload", capture)
    denied = application.search_materials(application.authenticated_principal("jiho"), "legacyreporttoken")
    assert denied["results"] == [] and denied["unavailable_materials_count"] == 0 and observed == []
    principal = application.authenticated_principal("mina")
    restricted = replace(principal, capabilities=principal.capabilities - {"daily_report.read"})
    assert application.search_materials(restricted, "legacyreporttoken")["unavailable_materials_count"] == 0 and observed == []
    pending = application.search_materials(principal, "legacyreporttoken")
    assert pending["results"] == [] and observed == []
    assert application.backfill_native_materials(restricted) == 0
    assert observed == []
    assert application.backfill_native_materials(principal) == 1
    assert len(observed) == 1
    assert application.backfill_native_materials(principal) == 0
    assert asyncio.run(worker.run_once())
    hit = application.search_materials(principal, "legacyreporttoken")["results"][0]
    with make_session_factory(settings.database_url)() as session:
        session.execute(delete(RoleCapabilityRecord).where(RoleCapabilityRecord.capability_id == "daily_report.read"))
        session.commit()
    revoked = application.search_materials(principal, "legacyreporttoken")
    assert revoked["results"] == [] and revoked["unavailable_materials_count"] == 0
    with pytest.raises(MaterialNotFound):
        application.search_materials(principal, "legacyreporttoken", resource_type="report", resource_id=hit["source_resource_id"])
    assert client.get(hit["origin"], headers=MINA).status_code == 404


def test_report_source_hash_drift_cannot_publish_or_open_changed_content(tmp_path):
    import pytest
    from ax_workspace.modules.work.materials import MaterialError
    from ax_workspace.platform.persistence import DailyReportSubmissionRecord, make_session_factory

    _, application, worker, settings = _stack(tmp_path)
    submitted = _submit(application, _draft(application, "originalreporttoken"))
    principal = application.authenticated_principal("mina")
    pending = application.search_materials(principal, "originalreporttoken")["unavailable_materials"][0]
    with make_session_factory(settings.database_url)() as session:
        session.get(DailyReportSubmissionRecord, UUID(submitted["submission_id"])).body = "tamperedreporttoken"
        session.commit()
    assert asyncio.run(worker.run_once())
    failed = application.search_materials(principal, "tamperedreporttoken")
    assert failed["results"] == []
    assert failed["unavailable_materials"][0]["extraction"]["failure_reason"] == "integrity_mismatch"
    with pytest.raises(MaterialError, match="integrity"):
        application.open_report_material(principal, UUID(submitted["report_id"]), UUID(pending["material_id"]))


def test_empty_legacy_submission_is_not_reported_as_complete_extraction(tmp_path):
    from ax_workspace.platform.persistence import ReportDraftRecord, make_session_factory

    _, application, worker, settings = _stack(tmp_path)
    draft = _draft(application, "pending body")
    with make_session_factory(settings.database_url)() as session:
        session.get(ReportDraftRecord, UUID(draft["draft_id"])).body = ""
        session.commit()
    _submit(application, draft)
    assert asyncio.run(worker.run_once())
    found = application.search_materials(application.authenticated_principal("mina"), "body")
    assert found["searched_materials"] == 0
    assert found["unavailable_materials"][0]["extraction"]["failure_reason"] == "empty_content"
