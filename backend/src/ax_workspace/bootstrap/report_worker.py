"""Separate worker for durable daily-report draft generation."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

from ax_workspace.bootstrap.application import WorkflowApplication
from ax_workspace.bootstrap.isolated_work import IsolatedWork
from ax_workspace.bootstrap.settings import Settings
from ax_workspace.modules.ax_execution.ai import AiProvider, ProviderFailure, ProviderUnavailable
from ax_workspace.modules.jobs.domain import JOB_KIND_DAILY_REPORT_GENERATE, ClaimedJob, DurableJobQueue
from ax_workspace.modules.reports.application import DailyReportGenerationConflict, DailyReportNotFound
from ax_workspace.platform.durable_jobs import MemoryDurableJobQueue, build_job_queue
from ax_workspace.platform.persistence import DailyReportGenerationRecord, make_session_factory


logger = logging.getLogger(__name__)


class DailyReportGenerationWorker:
    def __init__(
        self,
        settings: Settings,
        *,
        provider: AiProvider | None = None,
        queue_factory: Callable[[Any], DurableJobQueue] | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._application = WorkflowApplication(settings, provider)
        self._sessions = make_session_factory(settings.database_url)
        self._worker_id = f"report-worker:{uuid4().hex[:12]}"
        memory = MemoryDurableJobQueue() if settings.job_queue_backend == "memory" else None
        self._queue_factory = queue_factory or (
            lambda session: build_job_queue(settings.job_queue_backend, session, memory)
        )
        self._stopping = asyncio.Event()

    async def run(self) -> None:
        failures = 0
        while not self._stopping.is_set():
            try:
                processed = await self.run_once()
                failures = 0
            except SQLAlchemyError:
                failures += 1
                logger.exception("daily report worker poll failed (attempt %d)", failures)
                processed = False
            delay = 0.25 if failures == 0 else min(30.0, 2.0 * 2 ** min(failures - 1, 4))
            if not processed:
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=delay)
                except TimeoutError:
                    pass

    def stop(self) -> None:
        self._stopping.set()

    async def run_once(self) -> bool:
        with self._sessions() as session:
            jobs = self._queue_factory(session).claim(
                JOB_KIND_DAILY_REPORT_GENERATE,
                limit=self._settings.report_worker_concurrency,
                lease_seconds=self._settings.report_queue_visibility_timeout,
                worker_id=self._worker_id,
            )
            session.commit()
        if not jobs:
            return False
        await asyncio.gather(*(self._deliver(job) for job in jobs))
        return True

    async def _deliver(self, job: ClaimedJob) -> None:
        try:
            await self._handle_with_heartbeat(job)
        except Exception:  # noqa: BLE001 — 이 배달의 실패다. 워커 프로세스는 죽지 않는다
            logger.exception("daily report delivery ended without a result (job %s)", job.job_id)
            await asyncio.to_thread(self._abandon, job)

    async def _handle_with_heartbeat(self, job: ClaimedJob) -> None:
        if await asyncio.to_thread(self._generation_is_completed, job):
            with self._sessions() as session:
                self._queue_factory(session).complete(job.job_id, job.lease_token)
                session.commit()
            return
        work = IsolatedWork(_process_report_job, self._settings, self._provider, job)
        interval = max(0.1, self._settings.report_queue_visibility_timeout / 3)
        next_heartbeat = time.monotonic() + interval
        while True:
            await asyncio.sleep(max(0, min(0.05, next_heartbeat - time.monotonic())))
            if work.done:
                status = work.result()
                break
            if time.monotonic() >= next_heartbeat:
                if not await asyncio.to_thread(self._heartbeat, job):
                    work.terminate()
                    await asyncio.to_thread(self._time_out, job)
                    return
                next_heartbeat = time.monotonic() + interval

        if not await asyncio.to_thread(self._extend_transport, job):
            return
        with self._sessions() as session:
            queue = self._queue_factory(session)
            if status == "retry":
                queue.release(
                    job.job_id,
                    job.lease_token,
                    delay_seconds=min(self._settings.report_queue_visibility_timeout, 2 ** max(1, job.attempt)),
                    error="daily report provider transient failure",
                )
            elif status in {"contended", "stale"}:
                queue.release(
                    job.job_id,
                    job.lease_token,
                    delay_seconds=0 if status == "stale" else max(1, self._settings.report_queue_visibility_timeout // 2),
                    error=f"daily report generation {status}",
                )
            elif status in {"failed", "needs_verification"}:
                queue.fail(job.job_id, job.lease_token, error="daily report generation failed")
            else:
                queue.complete(job.job_id, job.lease_token)
            session.commit()

    def _extend_transport(self, job: ClaimedJob) -> bool:
        with self._sessions() as session:
            extended = self._queue_factory(session).extend_lease(
                job.job_id,
                job.lease_token,
                self._settings.report_queue_visibility_timeout,
            )
            session.commit()
            return extended

    def _generation_is_completed(self, job: ClaimedJob) -> bool:
        with self._sessions() as session:
            generation = session.get(
                DailyReportGenerationRecord,
                UUID(str(job.payload["generation_id"])),
            )
            return generation is not None and generation.state == "completed"

    def _heartbeat(self, job: ClaimedJob) -> bool:
        with self._sessions() as session:
            queue = self._queue_factory(session)
            if not queue.extend_lease(
                job.job_id, job.lease_token, self._settings.report_queue_visibility_timeout
            ):
                session.rollback()
                return False
            session.commit()
        with self._sessions() as session:
            generation = session.get(
                DailyReportGenerationRecord, UUID(str(job.payload["generation_id"]))
            )
            if generation is not None and generation.state == "queued" and generation.lease_token is None:
                return True
        try:
            active = self._application.heartbeat_daily_report_generation(
                UUID(str(job.payload["generation_id"])),
                lease_token=job.lease_token,
                stage_timeout_seconds=self._settings.report_stage_timeout_seconds,
                total_timeout_seconds=self._settings.report_total_timeout_seconds,
            )
        except DailyReportGenerationConflict:
            return self._generation_is_completed(job)
        return active or self._generation_is_completed(job)

    def _abandon(self, job: ClaimedJob) -> None:
        """자식이 결과를 남기지 못했다. 외부 호출을 이미 시작했다면 다시 부르지 않는다."""
        try:
            outcome = self._application.abandon_daily_report_generation(
                UUID(str(job.payload["generation_id"])), lease_token=job.lease_token
            )
        except (DailyReportGenerationConflict, DailyReportNotFound):
            outcome = "stale"
        with self._sessions() as session:
            queue = self._queue_factory(session)
            if outcome == "retry":
                queue.release(
                    job.job_id,
                    job.lease_token,
                    delay_seconds=min(
                        self._settings.report_queue_visibility_timeout, 2 ** max(1, job.attempt)
                    ),
                    error="daily report worker attempt was lost",
                )
            else:
                queue.fail(job.job_id, job.lease_token, error="daily report attempt ended without a result")
            session.commit()

    def _time_out(self, job: ClaimedJob) -> None:
        generation_id = UUID(str(job.payload["generation_id"]))
        try:
            self._application.mark_daily_report_generation_needs_verification(
                generation_id, lease_token=job.lease_token, code="provider_time_limit_uncertain"
            )
        except DailyReportGenerationConflict:
            pass
        with self._sessions() as session:
            self._queue_factory(session).fail(
                job.job_id, job.lease_token, error="daily report provider result uncertain after time limit"
            )
            session.commit()

    def _process(self, job: ClaimedJob) -> str:
        generation_id = UUID(str(job.payload["generation_id"]))
        try:
            plan = self._application.daily_report_generation_input(
                generation_id,
                attempt=job.attempt,
                lease_token=job.lease_token,
                stale_after_seconds=self._settings.report_queue_visibility_timeout,
            )
            if plan.get("completed"):
                return "completed"
            if plan.get("needs_verification"):
                return "needs_verification"
            if plan.get("denied") or plan.get("terminal"):
                return "failed"
            if plan.get("contended"):
                return "contended"
            if job.attempt > self._settings.report_queue_max_attempts:
                return self._record_failure(
                    generation_id,
                    lease_token=job.lease_token,
                    attempt=job.attempt,
                    code="max_attempts_exceeded",
                    retryable=False,
                )
            if not self._application.heartbeat_daily_report_generation(
                generation_id,
                lease_token=job.lease_token,
                stage_timeout_seconds=self._settings.report_stage_timeout_seconds,
                total_timeout_seconds=self._settings.report_total_timeout_seconds,
            ):
                return "failed"
            self._application.execute_daily_report_generation(
                generation_id, lease_token=job.lease_token
            )
            return "completed"
        except DailyReportGenerationConflict:
            return "stale"
        except ProviderUnavailable:
            return self._record_failure(
                generation_id,
                lease_token=job.lease_token,
                attempt=job.attempt,
                code="report_provider_failed",
                retryable=True,
            )
        except ProviderFailure:
            try:
                self._application.mark_daily_report_generation_needs_verification(
                    generation_id,
                    lease_token=job.lease_token,
                    code="provider_result_uncertain",
                )
                return "needs_verification"
            except DailyReportGenerationConflict:
                return "stale"
        except Exception:
            logger.exception("daily report generation attempt failed")
            return self._record_failure(
                generation_id,
                lease_token=job.lease_token,
                attempt=job.attempt,
                code="report_generation_error",
                retryable=False,
            )

    def _record_failure(
        self,
        generation_id: UUID,
        *,
        lease_token: UUID,
        attempt: int,
        code: str,
        retryable: bool,
    ) -> str:
        try:
            if retryable and attempt < self._settings.report_queue_max_attempts:
                self._application.retry_daily_report_generation(
                    generation_id, lease_token=lease_token, code=code
                )
                return "retry"
            self._application.fail_daily_report_generation(
                generation_id, lease_token=lease_token, code=code
            )
            return "failed"
        except DailyReportGenerationConflict:
            return "stale"


def _process_report_job(
    settings: Settings,
    provider: AiProvider | None,
    job: ClaimedJob,
) -> str:
    return DailyReportGenerationWorker(settings, provider=provider)._process(job)
