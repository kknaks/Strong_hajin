"""The parser transition uses PostgreSQL locks and the same transaction as its durable job."""
from legacy_acceptance import pending_request
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ax_workspace.bootstrap.material_worker import MaterialExtractionWorker
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.work.material_extraction import PARSER_VERSION
from ax_workspace.platform.persistence import DurableJobRecord, MaterialExtractionRecord, make_session_factory
from test_postgres_integration import _postgres_test_url


@pytest.mark.integration
def test_concurrent_workers_schedule_one_parser_upgrade_and_publish_one_current_projection(tmp_path):
    database_url = _postgres_test_url()
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, job_queue_backend="postgres", materials_dir=str(tmp_path / "materials"))
    client = TestClient(create_app(settings))
    headers = {"X-Demo-Persona": "mina"}
    task = client.post("/api/tasks", headers=headers, json={"title": "동시 재추출"}).json()
    material = client.post(f"/api/tasks/{task['task_id']}/materials", headers=headers, data={"kind": "input"},
                           files={"file": ("upgrade.txt", b"postgresupgradetailtoken", "text/plain")}).json()
    worker = MaterialExtractionWorker(settings)
    assert asyncio.run(worker.run_once())
    with make_session_factory(database_url)() as session:
        old = session.get(MaterialExtractionRecord, UUID(material["extraction"]["extraction_id"]))
        old.parser_version, old.coverage = "1", None
        session.commit()
    barrier = Barrier(2)
    def catch_up():
        barrier.wait(timeout=5)
        return MaterialExtractionWorker(settings)._catch_up_index()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(catch_up) for _ in range(2)]
        assert any([future.result(timeout=10) for future in futures])
    with make_session_factory(database_url)() as session:
        rows = list(session.scalars(select(MaterialExtractionRecord)))
        assert len(rows) == 2
        current = next(row for row in rows if row.superseded_at is None)
        assert current.parser_version == PARSER_VERSION and current.status == "queued"
        jobs = list(session.scalars(select(DurableJobRecord).where(DurableJobRecord.state == "queued")))
        assert len(jobs) == 1 and jobs[0].payload["extraction_id"] == str(current.id)
    assert asyncio.run(worker.run_once())
    found = client.get('/api/materials/search', headers=headers, params={'q': 'postgresupgradetailtoken'}).json()
    assert found["searched_materials"] == 1 and len(found["results"]) == 1
    assert found["results"][0]["extraction"]["parser_version"] == PARSER_VERSION


@pytest.mark.integration
def test_two_workers_backfill_one_missing_projection_without_search_writes(tmp_path, monkeypatch):
    from ax_workspace.modules.work.requests import WorkRequestApplication

    database_url = _postgres_test_url()
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, job_queue_backend="postgres", materials_dir=str(tmp_path / "materials"))
    client = TestClient(create_app(settings))
    headers = {"X-Demo-Persona": "mina"}
    with monkeypatch.context() as legacy:
        legacy.setattr(WorkRequestApplication, "_request_extraction", lambda *args: None)
        # 근거는 판단 회차에 붙는다 — 신규 요청에는 그 회차가 없으므로 과거 모양 행에서 본다.
        request = pending_request(client, database_url, headers, title="이전 자료 보강", assignee_id="jiho")
        response = client.post(f"/api/work-requests/{request['request_id']}/evidence", headers=headers,
                               files={"file": ("legacy.txt", b"postgresbackfilltoken", "text/plain")})
        assert response.status_code == 201, response.text
    for _ in range(2):
        assert client.get("/api/materials/search", headers=headers, params={"q": "postgresbackfilltoken"}).json()["results"] == []
    with make_session_factory(database_url)() as session:
        assert list(session.scalars(select(MaterialExtractionRecord))) == []
        assert list(session.scalars(select(DurableJobRecord))) == []
    barrier = Barrier(2)

    def catch_up():
        barrier.wait(timeout=5)
        return MaterialExtractionWorker(settings)._catch_up_index()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(catch_up) for _ in range(2)]
        assert any([future.result(timeout=10) for future in futures])
    with make_session_factory(database_url)() as session:
        extractions = list(session.scalars(select(MaterialExtractionRecord)))
        jobs = list(session.scalars(select(DurableJobRecord)))
        assert len(extractions) == len(jobs) == 1
        assert jobs[0].payload["extraction_id"] == str(extractions[0].id)
    assert asyncio.run(MaterialExtractionWorker(settings).run_once())
    found = client.get("/api/materials/search", headers=headers, params={"q": "postgresbackfilltoken"}).json()
    assert len(found["results"]) == 1
