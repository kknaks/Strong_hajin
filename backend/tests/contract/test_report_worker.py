from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import multiprocessing
import os
import time
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from ax_workspace.bootstrap.report_worker import DailyReportGenerationWorker
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.mcp import McpReportsFacade
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.ax_execution.ai import AiGeneration, ProviderRequestFailed, ProviderUnavailable
from ax_workspace.modules.reports.jobs import DailyReportGenerationJob, DailyReportGenerationQueue
from ax_workspace.platform.durable_jobs import MemoryDurableJobQueue
from ax_workspace.platform.persistence import (
    DailyReportGenerationAttemptRecord,
    DailyReportGenerationRecord,
    RoleCapabilityRecord,
    WorkflowNodeExecutionRecord,
    WorkflowRunRecord,
    make_session_factory,
)
from ax_workspace.platform.work_tasks import business_date

# 이 파일의 테스트는 **진짜 자식 프로세스**를 띄우고(자료·보고서 워커의 `IsolatedWork` spawn ·
# MCP `stdio_client` · `subprocess`) 그 진행을 초 단위 실시간 창으로 잰다 — 그래서 병렬 패스가 아니라
# `-n0` 직렬 패스에서 돈다. 기준과 걸개는 `tests/conftest.py`, 가르는 자리는 `Makefile` 의 `test-serial`.
pytestmark = pytest.mark.serial


REPORT_DATE = business_date(datetime.now(UTC))
HEADERS = {"X-Demo-Persona": "mina"}


class CountingReportProvider:
    def __init__(self) -> None:
        self._calls = multiprocessing.get_context("spawn").Value("i", 0)

    @property
    def calls(self) -> int:
        return self._calls.value

    def _record_call(self) -> int:
        with self._calls.get_lock():
            self._calls.value += 1
            return self._calls.value

    def generate(self, request) -> AiGeneration:
        calls = self._record_call()
        return AiGeneration(
            provider_run_ref=f"report-run-{calls}",
            provider_session_ref=f"report-session-{calls}",
            body="비동기로 만든 일일보고 초안입니다.",
            requested_model="gpt-5.6-terra",
            observed_model="gpt-5.6-terra",
            requested_tier="fast",
            observed_tier="fast",
            latency_ms=12,
            usage=None,
        )


class FailsTwiceReportProvider(CountingReportProvider):
    def generate(self, request) -> AiGeneration:
        calls = self._record_call()
        if calls < 3:
            raise ProviderUnavailable("temporary report provider failure")
        return AiGeneration(
            provider_run_ref="report-run-recovered",
            provider_session_ref="report-session-recovered",
            body="세 번째 시도에서 만든 보고입니다.",
            requested_model="gpt-5.6-terra",
            observed_model="gpt-5.6-terra",
            requested_tier="fast",
            observed_tier="fast",
            latency_ms=12,
            usage=None,
        )


class UnavailableReportProvider(CountingReportProvider):
    def generate(self, request) -> AiGeneration:
        self._record_call()
        raise ProviderUnavailable("temporary report provider failure")


class BlockingReportProvider(CountingReportProvider):
    def __init__(self, release: object) -> None:
        super().__init__()
        self.release = release

    def generate(self, request) -> AiGeneration:
        self.release.wait(timeout=30)
        return super().generate(request)


class AmbiguousReportProvider(CountingReportProvider):
    def generate(self, request) -> AiGeneration:
        self._record_call()
        raise ProviderRequestFailed("provider process ended after the request started")


class CrashingReportProvider(CountingReportProvider):
    """The isolated child is killed while the provider call is in flight.

    Nothing in the parent learns whether the request reached the provider — exactly the case the
    stage-timeout and ambiguous-result paths exist for, arrived at from the outside.
    """

    def generate(self, request) -> AiGeneration:
        self._record_call()
        os._exit(9)


class ImmediateRetryQueue(MemoryDurableJobQueue):
    def __init__(self) -> None:
        super().__init__()
        self.delays: list[int] = []

    def release(self, job_id, lease_token, *, delay_seconds, error=None):
        self.delays.append(delay_seconds)
        return super().release(job_id, lease_token, delay_seconds=0, error=error)


def _client(tmp_path, provider: CountingReportProvider):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url)
    client = TestClient(create_app(settings, report_provider=provider))
    return client, settings


def _worker(client: TestClient, settings: Settings, provider: CountingReportProvider) -> DailyReportGenerationWorker:
    application = client.app.state.workflow_application
    return DailyReportGenerationWorker(
        settings,
        provider=provider,
        queue_factory=lambda _session: application.memory_job_queue,
    )


def test_report_generation_is_durable_and_pollable_before_the_provider_runs(tmp_path) -> None:
    provider = CountingReportProvider()
    client, settings = _client(tmp_path, provider)

    accepted = client.post(
        "/api/daily-reports/generate-draft",
        headers=HEADERS,
        json={"report_date": REPORT_DATE},
    )

    assert accepted.status_code == 202
    receipt = accepted.json()
    assert receipt == {
        "report_date": REPORT_DATE,
        "status": "not_started",
        "report_id": None,
        "generation_id": receipt["generation_id"],
        "generation_status": "queued",
        "generation_error_code": None,
    }
    assert provider.calls == 0
    assert client.get(f"/api/daily-reports/status?report_date={REPORT_DATE}", headers=HEADERS).json() == receipt

    assert asyncio.run(_worker(client, settings, provider).run_once()) is True
    completed = client.get(f"/api/daily-reports/status?report_date={REPORT_DATE}", headers=HEADERS).json()
    assert completed["generation_id"] == receipt["generation_id"]
    assert completed["generation_status"] == "completed"
    assert completed["status"] == "draft" and completed["report_id"]
    history = client.get(f"/api/daily-reports/{completed['report_id']}/history", headers=HEADERS).json()
    assert history["drafts"][-1]["body"] == "비동기로 만든 일일보고 초안입니다."
    assert provider.calls == 1

    with make_session_factory(settings.database_url)() as session:
        generation = session.get(DailyReportGenerationRecord, UUID(receipt["generation_id"]))
        [attempt] = list(session.scalars(select(DailyReportGenerationAttemptRecord)))
        assert generation is not None and generation.state == "completed"
        assert generation.report_id == UUID(completed["report_id"])
        assert (attempt.actor_id, attempt.attempt_number, attempt.stage, attempt.state) == (
            "mina", 1, "workflow", "completed"
        )


def test_active_report_request_is_reused_and_worker_rechecks_current_actor_capability(tmp_path) -> None:
    provider = CountingReportProvider()
    client, settings = _client(tmp_path, provider)
    first = client.post(
        "/api/daily-reports/generate-draft", headers=HEADERS, json={"report_date": REPORT_DATE}
    ).json()
    second = client.post(
        "/api/daily-reports/generate-draft", headers=HEADERS, json={"report_date": REPORT_DATE}
    ).json()
    assert second["generation_id"] == first["generation_id"]
    assert len(client.app.state.workflow_application.memory_job_queue.snapshot()) == 1

    with make_session_factory(settings.database_url)() as session:
        session.execute(delete(RoleCapabilityRecord).where(RoleCapabilityRecord.capability_id == "daily_report.generate"))
        session.commit()

    assert asyncio.run(_worker(client, settings, provider).run_once()) is True
    status = client.get(f"/api/daily-reports/status?report_date={REPORT_DATE}", headers=HEADERS).json()
    assert status["generation_id"] == first["generation_id"]
    assert status["generation_status"] == "failed"
    assert status["generation_error_code"] == "access_denied"
    assert status["report_id"] is None and provider.calls == 0
    with make_session_factory(settings.database_url)() as session:
        [attempt] = list(session.scalars(select(DailyReportGenerationAttemptRecord)))
        assert attempt.actor_id == "mina" and attempt.state == "failed"
        assert attempt.failure_reason == "access_denied"


def test_report_provider_retry_is_bounded_and_records_two_and_four_second_backoff(tmp_path) -> None:
    provider = FailsTwiceReportProvider()
    client, settings = _client(tmp_path, provider)
    queue = ImmediateRetryQueue()
    client.app.state.workflow_application.memory_job_queue = queue
    accepted = client.post(
        "/api/daily-reports/generate-draft", headers=HEADERS, json={"report_date": REPORT_DATE}
    ).json()
    worker = DailyReportGenerationWorker(
        settings, provider=provider, queue_factory=lambda _session: queue
    )

    assert asyncio.run(worker.run_once()) is True
    assert asyncio.run(worker.run_once()) is True
    assert asyncio.run(worker.run_once()) is True

    status = client.get(f"/api/daily-reports/status?report_date={REPORT_DATE}", headers=HEADERS).json()
    assert status["generation_id"] == accepted["generation_id"]
    assert status["generation_status"] == "completed" and status["status"] == "draft"
    assert provider.calls == 3 and queue.delays == [2, 4]
    with make_session_factory(settings.database_url)() as session:
        attempts = list(session.scalars(select(DailyReportGenerationAttemptRecord).order_by(
            DailyReportGenerationAttemptRecord.attempt_number
        )))
        assert [(row.attempt_number, row.state, row.failure_reason) for row in attempts] == [
            (1, "retry", "report_provider_failed"),
            (2, "retry", "report_provider_failed"),
            (3, "completed", None),
        ]
        [run] = list(session.scalars(select(WorkflowRunRecord)))
        nodes = list(session.scalars(select(WorkflowNodeExecutionRecord).where(
            WorkflowNodeExecutionRecord.run_id == run.id
        )))
        assert (generation := session.get(DailyReportGenerationRecord, UUID(accepted["generation_id"])))
        assert generation.workflow_run_id == run.id
        assert {row.node_id: row.retry_count for row in nodes} == {
            "sources": 0,
            "render": 0,
            "generate": 2,
            "validate": 0,
        }


def test_uncertain_report_provider_result_stops_new_execution_until_verified(tmp_path) -> None:
    provider = AmbiguousReportProvider()
    client, settings = _client(tmp_path, provider)
    first = client.post(
        "/api/daily-reports/generate-draft", headers=HEADERS, json={"report_date": REPORT_DATE}
    ).json()

    assert asyncio.run(_worker(client, settings, provider).run_once()) is True
    status = client.get(
        f"/api/daily-reports/status?report_date={REPORT_DATE}", headers=HEADERS
    ).json()
    assert status["generation_status"] == "needs_verification"
    assert status["generation_error_code"] == "provider_result_uncertain"

    replay = client.post(
        "/api/daily-reports/generate-draft", headers=HEADERS, json={"report_date": REPORT_DATE}
    ).json()
    assert replay["generation_id"] == first["generation_id"]
    assert replay["generation_status"] == "needs_verification"
    assert provider.calls == 1
    with make_session_factory(settings.database_url)() as session:
        [attempt] = list(session.scalars(select(DailyReportGenerationAttemptRecord)))
        assert attempt.state == "needs_verification"
        assert attempt.failure_reason == "provider_result_uncertain"


def test_user_retry_after_terminal_failure_is_linked_to_the_failed_generation(tmp_path) -> None:
    provider = UnavailableReportProvider()
    client, settings = _client(tmp_path, provider)
    queue = ImmediateRetryQueue()
    client.app.state.workflow_application.memory_job_queue = queue
    first = client.post(
        "/api/daily-reports/generate-draft", headers=HEADERS, json={"report_date": REPORT_DATE}
    ).json()
    worker = DailyReportGenerationWorker(
        settings, provider=provider, queue_factory=lambda _session: queue
    )

    assert asyncio.run(worker.run_once()) is True
    assert asyncio.run(worker.run_once()) is True
    assert asyncio.run(worker.run_once()) is True
    assert provider.calls == 3
    failed = client.get(
        f"/api/daily-reports/status?report_date={REPORT_DATE}", headers=HEADERS
    ).json()
    assert failed["generation_status"] == "failed"

    retry = client.post(
        "/api/daily-reports/generate-draft", headers=HEADERS, json={"report_date": REPORT_DATE}
    ).json()
    assert retry["generation_id"] != first["generation_id"]
    assert retry["generation_status"] == "queued"
    with make_session_factory(settings.database_url)() as session:
        generation = session.get(DailyReportGenerationRecord, UUID(retry["generation_id"]))
        assert generation is not None
        assert generation.retry_of_generation_id == UUID(first["generation_id"])


def test_report_stage_timeout_fences_the_late_provider_result(tmp_path) -> None:
    release = multiprocessing.get_context("spawn").Event()
    provider = BlockingReportProvider(release)
    client, base_settings = _client(tmp_path, provider)
    settings = Settings(
        RuntimeProfile.TEST,
        base_settings.database_url,
        report_queue_visibility_timeout=1,
        report_stage_timeout_seconds=1,
        report_total_timeout_seconds=3,
    )
    accepted = client.post(
        "/api/daily-reports/generate-draft", headers=HEADERS, json={"report_date": REPORT_DATE}
    ).json()
    worker = _worker(client, settings, provider)

    async def run_without_releasing_provider() -> None:
        started_at = time.monotonic()
        assert await worker.run_once() is True
        assert time.monotonic() - started_at < 12.0

    asyncio.run(run_without_releasing_provider())
    status = client.get(f"/api/daily-reports/status?report_date={REPORT_DATE}", headers=HEADERS).json()
    assert status["generation_id"] == accepted["generation_id"]
    assert status["generation_status"] == "needs_verification"
    assert status["generation_error_code"] == "provider_time_limit_uncertain"
    assert status["report_id"] is None
    with make_session_factory(settings.database_url)() as session:
        generation = session.get(DailyReportGenerationRecord, UUID(accepted["generation_id"]))
        [attempt] = list(session.scalars(select(DailyReportGenerationAttemptRecord)))
        assert generation is not None and generation.draft_id is None
        assert attempt.state == "needs_verification"
        assert attempt.failure_reason == "provider_time_limit_uncertain"


def test_mcp_report_generation_returns_the_same_pollable_acceptance_contract(tmp_path) -> None:
    provider = CountingReportProvider()
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    facade = McpReportsFacade(Settings(RuntimeProfile.TEST, database_url), "mina", provider)

    first = facade.generate_daily_report_draft(REPORT_DATE)
    replay = facade.generate_daily_report_draft(REPORT_DATE)

    assert first["generation_status"] == "queued" and first["report_id"] is None
    assert replay["generation_id"] == first["generation_id"]
    assert len(facade._application.memory_job_queue.snapshot()) == 1
    assert provider.calls == 0


def test_a_worker_child_killed_mid_call_neither_kills_the_loop_nor_calls_the_provider_again(tmp_path) -> None:
    """결과 없이 끝난 시도는 배달 하나의 실패다 — 워커는 살아 있고, provider 를 다시 부르지 않는다."""
    provider = CrashingReportProvider()
    client, settings = _client(tmp_path, provider)
    queue = ImmediateRetryQueue()
    client.app.state.workflow_application.memory_job_queue = queue
    accepted = client.post(
        "/api/daily-reports/generate-draft", headers=HEADERS, json={"report_date": REPORT_DATE}
    ).json()
    worker = DailyReportGenerationWorker(
        settings, provider=provider, queue_factory=lambda _session: queue
    )

    # 자식의 죽음이 run_once 밖으로 새어 나오면 run() 루프가 통째로 끝난다.
    assert asyncio.run(worker.run_once()) is True

    status = client.get(f"/api/daily-reports/status?report_date={REPORT_DATE}", headers=HEADERS).json()
    assert status["generation_id"] == accepted["generation_id"]
    assert status["generation_status"] == "needs_verification"
    assert status["generation_error_code"] == "provider_result_uncertain"
    assert status["report_id"] is None

    # 확인 필요로 고정된 뒤에는 어떤 후속 배달도 외부 호출을 되풀이하지 않는다.
    asyncio.run(worker.run_once())
    assert provider.calls == 1


def test_a_takeover_of_a_lost_host_does_not_repeat_the_external_call(tmp_path) -> None:
    """호스트가 통째로 사라져 부모가 정리하지 못한 실행도, 다음 worker 가 provider 를 다시 부르지 않는다."""
    provider = CrashingReportProvider()
    client, settings = _client(tmp_path, provider)
    queue = ImmediateRetryQueue()
    client.app.state.workflow_application.memory_job_queue = queue
    accepted = client.post(
        "/api/daily-reports/generate-draft", headers=HEADERS, json={"report_date": REPORT_DATE}
    ).json()
    worker = DailyReportGenerationWorker(
        settings, provider=provider, queue_factory=lambda _session: queue
    )
    assert asyncio.run(worker.run_once()) is True

    # 부모가 남긴 정리를 지우고, 호스트가 통째로 사라진 채 lease 만 만료된 상태로 되돌린다.
    with make_session_factory(settings.database_url)() as session:
        generation = session.get(DailyReportGenerationRecord, UUID(accepted["generation_id"]))
        generation.state = "running"
        generation.error_code = None
        generation.completed_at = None
        generation.heartbeat_at = datetime(2026, 1, 1, tzinfo=UTC)
        attempt = session.scalars(select(DailyReportGenerationAttemptRecord)).one()
        attempt.state = "running"
        attempt.failure_reason = None
        [run] = list(session.scalars(select(WorkflowRunRecord)))
        assert [row.state for row in session.scalars(
            select(WorkflowNodeExecutionRecord).where(WorkflowNodeExecutionRecord.run_id == run.id)
        )].count("running") == 1
        session.commit()
    DailyReportGenerationQueue(queue).enqueue(
        DailyReportGenerationJob(UUID(accepted["generation_id"]), "mina")
    )

    takeover = DailyReportGenerationWorker(
        settings, provider=provider, queue_factory=lambda _session: queue
    )
    assert asyncio.run(takeover.run_once()) is True

    status = client.get(f"/api/daily-reports/status?report_date={REPORT_DATE}", headers=HEADERS).json()
    assert status["generation_status"] == "needs_verification"
    assert status["generation_error_code"] == "provider_result_uncertain"
    assert provider.calls == 1
