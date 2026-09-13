"""PostgreSQL proves that transport and extraction fences survive concurrent workers."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import time
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ax_workspace.bootstrap.material_worker import MaterialExtractionWorker
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.work.material_extraction import ExtractedBlock, ExtractedChunk, ExtractionOutcome
from ax_workspace.platform.persistence import DurableJobRecord, MaterialExtractionAttemptRecord, MaterialExtractionRecord, make_session_factory
from test_postgres_integration import _postgres_test_url


class SlowPostgresExtractor:
    def extract(self, *, name, content_type, data):
        time.sleep(3.2)
        text = data.decode()
        return ExtractionOutcome(
            status="completed", extractor="text", blocks=(ExtractedBlock(0, "paragraph", text),),
            chunks=(ExtractedChunk(0, text, 0, len(text), block_sequence=0),), char_count=len(text),
            coverage={"complete": True},
        )


@pytest.mark.integration
def test_heartbeat_keeps_one_postgres_owner_and_one_published_result(tmp_path):
    database_url = _postgres_test_url()
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, job_queue_backend="postgres", materials_dir=str(tmp_path / "materials"),
                        material_queue_visibility_timeout=2)
    client = TestClient(create_app(settings))
    headers = {"X-Demo-Persona": "mina"}
    task = client.post("/api/tasks", headers=headers, json={"title": "PG heartbeat"}).json()
    uploaded = client.post(f"/api/tasks/{task['task_id']}/materials", headers=headers, data={"kind": "input"},
                           files={"file": ("slow.txt", b"postgresheartbeattoken", "text/plain")}).json()
    first = MaterialExtractionWorker(settings, extractor=SlowPostgresExtractor())
    second = MaterialExtractionWorker(settings)
    sessions = make_session_factory(database_url)

    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(lambda: asyncio.run(first.run_once()))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with sessions() as session:
                extraction = session.get(MaterialExtractionRecord, UUID(uploaded["extraction"]["extraction_id"]))
                if extraction.status == "running":
                    initial_heartbeat = extraction.heartbeat_at
                    break
            time.sleep(0.05)
        else:
            raise AssertionError("first worker did not claim the extraction")
        time.sleep(2.25)
        with sessions() as session:
            extraction = session.get(MaterialExtractionRecord, UUID(uploaded["extraction"]["extraction_id"]))
            heartbeat = extraction.heartbeat_at
            if heartbeat.tzinfo is None:
                heartbeat = heartbeat.replace(tzinfo=UTC)
            assert heartbeat > initial_heartbeat and datetime.now(UTC).timestamp() - heartbeat.timestamp() < 2
        assert asyncio.run(second.run_once()) is False
        assert future.result(timeout=10)

    with sessions() as session:
        [extraction] = list(session.scalars(select(MaterialExtractionRecord)))
        [attempt] = list(session.scalars(select(MaterialExtractionAttemptRecord)))
        [job] = list(session.scalars(select(DurableJobRecord)))
        assert extraction.status == attempt.state == job.state == "completed"
        assert extraction.attempt_count == attempt.attempt_number == job.attempt_count == 1
        assert attempt.actor_id == "mina" and extraction.worker_token is None and job.lease_token is None
    found = client.get("/api/materials/search", headers=headers, params={"q": "postgresheartbeattoken"}).json()
    assert len(found["results"]) == 1
