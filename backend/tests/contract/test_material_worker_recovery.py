"""Material workers reauthorize queued work and preserve bounded, fenced attempt outcomes.

여기 테스트는 한 건 안에서 `IsolatedWork` 로 **진짜 자식 인터프리터를 최대 둘** 띄우고, 그 위에서
**초 단위 lease 창을 실시간 시계로 잰다** — `-n auto`(= 코어 수) 아래서는 그 창이 스케줄 지터보다
작아져 흔들린다. 그래서 아래 `pytestmark = pytest.mark.serial` 이 이 파일을 **병렬 패스에서 빼고
`-n0` 직렬 패스로 보낸다**(`Makefile` 의 `test-serial`). 목록이 아니라 마커가 기준이므로 파일을
옮기거나 이름을 바꿔도 따라 고칠 것은 없다 — 새 테스트를 더할 때 마커만 함께 서면 된다.
"""
import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import time
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select

from ax_workspace.platform.persistence import MaterialExtractionAttemptRecord, MaterialExtractionRecord, RoleCapabilityRecord, make_session_factory
from ax_workspace.modules.jobs.domain import JOB_KIND_MATERIAL_EXTRACTION
from ax_workspace.modules.work.material_extraction import ExtractedBlock, ExtractedChunk, ExtractionOutcome
from test_material_search import MINA, _stack, _upload

# 위 docstring 의 이유로 직렬 패스에서 돈다. 부류의 기준과 걸개는 `tests/conftest.py`.
pytestmark = pytest.mark.serial


class AlwaysTransientExtractor:
    def extract(self, *, name, content_type, data):
        return ExtractionOutcome(status="failed", extractor="text", failure_reason="extractor_error", transient=True)


class SlowExtractor:
    def extract(self, *, name, content_type, data):
        time.sleep(3.6)
        text = data.decode()
        return ExtractionOutcome(
            status="completed", extractor="text", blocks=(ExtractedBlock(0, "paragraph", text),),
            chunks=(ExtractedChunk(0, text, 0, len(text), block_sequence=0),), char_count=len(text),
            coverage={"complete": True},
        )


class StageTimeoutExtractor:
    def extract(self, *, name, content_type, data):
        if name == "timeout.txt":
            time.sleep(30)
            raise AssertionError("the timed-out parser process must be terminated")
        text = data.decode()
        return ExtractionOutcome(
            status="completed", extractor="text", blocks=(ExtractedBlock(0, "paragraph", text),),
            chunks=(ExtractedChunk(0, text, 0, len(text), block_sequence=0),), char_count=len(text),
            coverage={"complete": True},
        )


def test_revoked_actor_is_rejected_before_reading_queued_file_bytes(tmp_path, monkeypatch):
    client, application, worker, settings = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "접수 후 권한 회수"}).json()
    uploaded = _upload(client, task["task_id"], "queued.txt", b"unreadtoken", "text/plain").json()
    observed = []
    original = worker._storage.get

    def read(reference):
        observed.append(reference)
        return original(reference)

    monkeypatch.setattr(worker._storage, "get", read)
    with make_session_factory(settings.database_url)() as session:
        session.execute(delete(RoleCapabilityRecord).where(RoleCapabilityRecord.capability_id == "task.read"))
        session.commit()
    assert asyncio.run(worker.run_once())
    assert observed == []
    with make_session_factory(settings.database_url)() as session:
        extraction = session.get(MaterialExtractionRecord, UUID(uploaded["extraction"]["extraction_id"]))
        assert extraction.status == "failed" and extraction.failure_reason == "access_denied"
        assert extraction.attempt_count == 1
        [attempt] = list(session.scalars(select(MaterialExtractionAttemptRecord)))
        assert attempt.actor_id == "mina" and attempt.state == "failed" and attempt.failure_reason == "access_denied"
    assert not asyncio.run(worker.run_once())
    assert observed == []


def test_transient_parser_failures_retry_after_two_and_four_seconds_then_stop(tmp_path):
    client, application, _, settings = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "제한 재시도"}).json()
    uploaded = _upload(client, task["task_id"], "retry.txt", b"retrytoken", "text/plain").json()
    from ax_workspace.bootstrap.material_worker import MaterialExtractionWorker
    worker = MaterialExtractionWorker(settings, extractor=AlwaysTransientExtractor(), queue_factory=lambda session: application.memory_job_queue)

    for attempt, expected_delay in ((1, 2), (2, 4)):
        assert asyncio.run(worker.run_once())
        observed_at = datetime.now(UTC)
        [job] = [row for row in application.memory_job_queue.snapshot() if row["kind"] == JOB_KIND_MATERIAL_EXTRACTION]
        assert job["state"] == "queued" and job["attempt_count"] == attempt
        assert expected_delay - 0.25 <= (job["available_at"] - observed_at).total_seconds() <= expected_delay + 0.25
        with application.memory_job_queue._lock:
            application.memory_job_queue._jobs[0]["available_at"] = datetime.now(UTC) - timedelta(seconds=1)

    assert asyncio.run(worker.run_once())
    [job] = [row for row in application.memory_job_queue.snapshot() if row["kind"] == JOB_KIND_MATERIAL_EXTRACTION]
    assert job["state"] == "completed" and job["attempt_count"] == 3
    with make_session_factory(settings.database_url)() as session:
        extraction = session.get(MaterialExtractionRecord, UUID(uploaded["extraction"]["extraction_id"]))
        assert extraction.status == "failed" and extraction.failure_reason == "extractor_error" and extraction.attempt_count == 3
        attempts = list(session.scalars(select(MaterialExtractionAttemptRecord).order_by(MaterialExtractionAttemptRecord.attempt_number)))
        assert [(row.actor_id, row.state, row.failure_reason) for row in attempts] == [
            ("mina", "retry", "extractor_error"),
            ("mina", "retry", "extractor_error"),
            ("mina", "failed", "extractor_error"),
        ]


def test_long_parse_heartbeats_its_lease_so_a_second_worker_cannot_reclaim_it(tmp_path):
    client, application, _, settings = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "heartbeat"}).json()
    uploaded = _upload(client, task["task_id"], "slow.txt", b"slowheartbeattoken", "text/plain").json()
    settings = replace(settings, material_queue_visibility_timeout=3)
    from ax_workspace.bootstrap.material_worker import MaterialExtractionWorker
    first = MaterialExtractionWorker(settings, extractor=SlowExtractor(), queue_factory=lambda session: application.memory_job_queue)
    second = MaterialExtractionWorker(settings, queue_factory=lambda session: application.memory_job_queue)

    async def run_both():
        running = asyncio.create_task(first.run_once())
        await asyncio.sleep(3.15)
        [job] = application.memory_job_queue.snapshot()
        assert job["state"] == "running" and job["lease_expires_at"] > datetime.now(UTC)
        with make_session_factory(settings.database_url)() as session:
            extraction = session.get(MaterialExtractionRecord, UUID(uploaded["extraction"]["extraction_id"]))
            assert extraction.status == "running" and extraction.heartbeat_at is not None and extraction.worker_token == job["lease_token"]
        assert await second.run_once() is False
        assert await running

    asyncio.run(run_both())
    [job] = application.memory_job_queue.snapshot()
    assert job["state"] == "completed" and job["attempt_count"] == 1
    assert application.search_materials(application.authenticated_principal("mina"), "slowheartbeattoken")["results"]


def test_stage_timeout_fences_the_late_parser_result(tmp_path):
    client, application, _, settings = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "시간 제한"}).json()
    uploaded = _upload(client, task["task_id"], "timeout.txt", b"latetimeouttoken", "text/plain").json()
    next_task = client.post("/api/tasks", headers=MINA, json={"title": "다음 처리"}).json()
    _upload(client, next_task["task_id"], "next.txt", b"nextjobtoken", "text/plain")
    settings = replace(settings, material_queue_visibility_timeout=3, material_stage_timeout_seconds=1)
    from ax_workspace.bootstrap.material_worker import MaterialExtractionWorker
    worker = MaterialExtractionWorker(settings, extractor=StageTimeoutExtractor(), queue_factory=lambda session: application.memory_job_queue)

    async def run_and_observe():
        started_at = time.monotonic()
        assert await worker.run_once()
        with make_session_factory(settings.database_url)() as session:
            extraction = session.get(MaterialExtractionRecord, UUID(uploaded["extraction"]["extraction_id"]))
            assert extraction.status == "failed" and extraction.failure_reason == "time_limit_exceeded"
        assert [job["state"] for job in application.memory_job_queue.snapshot()] == ["failed", "completed"]
        assert time.monotonic() - started_at < 12.0

    asyncio.run(run_and_observe())
    assert application.search_materials(application.authenticated_principal("mina"), "latetimeouttoken")["results"] == []
    assert application.search_materials(application.authenticated_principal("mina"), "nextjobtoken")["results"]


def test_expired_owner_cannot_publish_after_a_new_attempt_takes_over(tmp_path):
    from ax_workspace.modules.work.material_extraction import MaterialExtractionJob, MaterialExtractionService
    from ax_workspace.platform.material_extraction import SqlAlchemyMaterialExtractionRepository

    client, _, _, settings = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "늦은 결과 fence"}).json()
    uploaded = _upload(client, task["task_id"], "late.txt", b"lateownertoken", "text/plain").json()
    extraction_id = UUID(uploaded["extraction"]["extraction_id"])
    attachment_id = UUID(uploaded["attachment_id"])
    old_token, new_token = uuid4(), uuid4()
    with make_session_factory(settings.database_url)() as session:
        old_service = MaterialExtractionService(SqlAlchemyMaterialExtractionRepository(session), stale_after_seconds=1)
        old_claim = old_service.claim(MaterialExtractionJob(extraction_id, attachment_id, "mina", old_token))
        extraction = session.get(MaterialExtractionRecord, extraction_id)
        extraction.heartbeat_at = datetime.now(UTC) - timedelta(seconds=2)
        session.commit()
    with make_session_factory(settings.database_url)() as session:
        new_service = MaterialExtractionService(SqlAlchemyMaterialExtractionRepository(session), stale_after_seconds=1)
        new_claim = new_service.claim(MaterialExtractionJob(extraction_id, attachment_id, "mina", new_token))
        session.commit()
    outcome = ExtractionOutcome(status="completed", extractor="text", coverage={"complete": True})
    with make_session_factory(settings.database_url)() as session:
        assert MaterialExtractionService(SqlAlchemyMaterialExtractionRepository(session), stale_after_seconds=1).finish(old_claim, outcome) == "stale"
        session.commit()
    with make_session_factory(settings.database_url)() as session:
        service = MaterialExtractionService(SqlAlchemyMaterialExtractionRepository(session), stale_after_seconds=1)
        assert service.finish(new_claim, outcome) == "completed"
        session.commit()
        attempts = list(session.scalars(select(MaterialExtractionAttemptRecord).order_by(MaterialExtractionAttemptRecord.attempt_number)))
        assert [(row.state, row.failure_reason) for row in attempts] == [("abandoned", "lease_expired"), ("completed", None)]
