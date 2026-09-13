"""Separate process that extracts and indexes Task material content through the shared durable job transport."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import logging
from pathlib import Path
import time
from typing import Any, Callable
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

from ax_workspace.bootstrap.settings import Settings
from ax_workspace.bootstrap.isolated_work import IsolatedWork
from ax_workspace.bootstrap.material_sources import SessionMaterialOwners
from ax_workspace.modules.jobs.domain import JOB_KIND_MATERIAL_EXTRACTION, ClaimedJob, DurableJobQueue
from ax_workspace.modules.work.material_extraction import (
    MaterialExtractionJob,
    MaterialExtractionService,
    MaterialTextExtractor,
    PARSER_VERSION,
    UPGRADABLE_PARSER_VERSIONS,
    ParserVersionConflict,
)
from ax_workspace.modules.work.material_search import RESOURCE_TYPES
from ax_workspace.modules.work.action_materials import ActionMaterialDraftApplication
from ax_workspace.platform.action_materials import SqlAlchemyActionMaterialDraftRepository
from ax_workspace.platform.durable_jobs import MemoryDurableJobQueue, build_job_queue
from ax_workspace.platform.material_extraction import (
    PypdfTextExtractor,
    MaterialJobQueue,
    SqlAlchemyMaterialExtractionRepository,
    reindex_stale_chunks,
)
from ax_workspace.platform.materials import LocalDirectoryMaterialStorage
from ax_workspace.platform.native_materials import NativeRevisionExtractor, NativeRevisionStorage
from ax_workspace.platform.organization_access import SqlAlchemyOrganizationRepository
from ax_workspace.platform.persistence import AttachmentRecord, make_session_factory

FAILURE_BACKOFF_SECONDS = 2.0
logger = logging.getLogger(__name__)


class MaterialExtractionWorker:
    """claim job (tx) -> claim extraction (tx) -> parse (no tx) -> finish extraction (tx, fenced) -> finish job (tx, fenced)."""

    def __init__(
        self,
        settings: Settings,
        *,
        extractor: MaterialTextExtractor | None = None,
        queue_factory: Callable[[Any], DurableJobQueue] | None = None,
    ) -> None:
        self._settings = settings
        self._sessions = make_session_factory(settings.database_url)
        self._storage = LocalDirectoryMaterialStorage(Path(settings.materials_dir))
        self._extractor = extractor or PypdfTextExtractor()
        # Owner policies stay in the composition root; this instance only lends them the worker's short session.
        from ax_workspace.bootstrap.application import WorkflowApplication
        self._application = WorkflowApplication(settings)
        self._worker_id = f"material-worker:{uuid4().hex[:12]}"
        if queue_factory is not None:
            self._queue_factory = queue_factory
        else:
            memory = MemoryDurableJobQueue() if settings.job_queue_backend == "memory" else None
            self._queue_factory = lambda session: build_job_queue(settings.job_queue_backend, session, memory)
        self._stopping = asyncio.Event()

    async def run(self) -> None:
        failures = 0
        while not self._stopping.is_set():
            try:
                processed = await self.run_once()
                failures = 0
            except SQLAlchemyError:
                # A database outage or schema reset must not kill the process; programming errors still propagate.
                failures += 1
                logger.exception("material worker poll failed (attempt %d); retrying after backoff", failures)
                processed = False
            delay = 0.25 if failures == 0 else min(30.0, FAILURE_BACKOFF_SECONDS * 2 ** min(failures - 1, 4))
            if not processed:
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=delay)
                except TimeoutError:
                    pass

    def stop(self) -> None:
        self._stopping.set()

    async def run_once(self) -> bool:
        jobs = self._claim_jobs()
        if not jobs:
            if await asyncio.to_thread(self._reconcile_action_drafts):
                return True
            # 할 일이 없을 때 뒤처진 색인을 따라잡는다. 분석 규칙이 바뀌었다는 것을 사람이 기억했다가 명령을
            # 부르는 것은 잊기 위한 설계다 — 규칙은 chunk마다 적혀 있으므로 이 자리가 알아서 안다.
            return await asyncio.to_thread(self._catch_up_index)
        await asyncio.gather(*(self._deliver(job) for job in jobs))
        return True

    async def _deliver(self, job: ClaimedJob) -> None:
        try:
            await self._handle_with_heartbeat(job)
        except Exception:  # noqa: BLE001 — 이 배달의 실패다. 워커 프로세스는 죽지 않는다
            logger.exception("material extraction delivery ended without a result (job %s)", job.job_id)
            await asyncio.to_thread(self._abandon, job)

    def _abandon(self, job: ClaimedJob) -> None:
        """자식이 결과를 남기지 못했다. 이 배달만 실패로 닫고 다음 배달이 이어받게 한다."""
        with self._sessions() as session:
            repository = SqlAlchemyMaterialExtractionRepository(session)
            extraction = repository.running(
                UUID(str(job.payload["extraction_id"])), job.attempt, job.lease_token
            )
            if extraction is not None:
                repository.fail(extraction, "worker_attempt_lost")
            self._queue_factory(session).fail(
                job.job_id, job.lease_token, error="material extraction attempt ended without a result"
            )
            session.commit()

    async def _handle_with_heartbeat(self, job: ClaimedJob) -> None:
        work = IsolatedWork(_process_material_job, self._settings, self._extractor, job)
        interval = max(0.1, self._settings.material_queue_visibility_timeout / 3)
        deadline: float | None = None
        next_heartbeat = time.monotonic() + interval
        while True:
            await asyncio.sleep(max(0, min(0.05, next_heartbeat - time.monotonic())))
            if work.done:
                status = work.result()
                extended = await asyncio.to_thread(self._extend_transport, job)
                if extended:
                    self._finish_transport(job, status)
                return
            if deadline is not None and time.monotonic() >= deadline:
                work.terminate()
                await asyncio.to_thread(self._time_out, job)
                return
            if time.monotonic() >= next_heartbeat:
                heartbeat = await asyncio.to_thread(self._heartbeat, job)
                if heartbeat == "lost":
                    work.terminate()
                    return
                if heartbeat == "terminal":
                    work.terminate()
                    self._finish_transport(job, "completed")
                    return
                if heartbeat == "active" and deadline is None:
                    deadline = time.monotonic() + self._settings.material_stage_timeout_seconds
                next_heartbeat = time.monotonic() + interval

    def _extend_transport(self, job: ClaimedJob) -> bool:
        with self._sessions() as session:
            extended = self._queue_factory(session).extend_lease(
                job.job_id,
                job.lease_token,
                self._settings.material_queue_visibility_timeout,
            )
            session.commit()
            return extended

    def _time_out(self, job: ClaimedJob) -> None:
        with self._sessions() as session:
            repository = SqlAlchemyMaterialExtractionRepository(session)
            extraction = repository.running(UUID(str(job.payload["extraction_id"])), job.attempt, job.lease_token)
            if extraction is not None:
                repository.fail(extraction, "time_limit_exceeded")
            self._queue_factory(session).fail(job.job_id, job.lease_token, error="material extraction stage time limit exceeded")
            session.commit()

    def _heartbeat(self, job: ClaimedJob) -> str:
        with self._sessions() as session:
            queue = self._queue_factory(session)
            if not queue.extend_lease(job.job_id, job.lease_token, self._settings.material_queue_visibility_timeout):
                session.rollback()
                return "lost"
            repository = SqlAlchemyMaterialExtractionRepository(session)
            extraction_id = UUID(str(job.payload["extraction_id"]))
            extraction = repository.get(extraction_id)
            # The transport claim precedes the domain claim. Keep the lease while authorization/setup is still
            # running; once the domain attempt exists, both fences must belong to this delivery.
            if extraction is None or extraction.status == "queued":
                session.commit()
                return "waiting"
            if repository.is_terminal(extraction_id):
                session.commit()
                return "terminal"
            if extraction.status != "running":
                session.rollback()
                return "lost"
            if not repository.heartbeat(extraction_id, job.attempt, job.lease_token):
                # The child may have committed its terminal result between our read and fenced UPDATE.
                session.refresh(extraction)
                if repository.is_terminal(extraction_id):
                    session.commit()
                    return "terminal"
                session.rollback()
                return "lost"
            session.commit()
            return "active"

    def _reconcile_action_drafts(self) -> bool:
        """Delete bytes for discarded or expired pre-create drafts; delete is idempotent across retry/commit gaps."""
        try:
            with self._sessions() as session:
                cleaned = ActionMaterialDraftApplication(
                    SqlAlchemyActionMaterialDraftRepository(session), self._storage
                ).reconcile(limit=self._settings.material_worker_concurrency * 25)
                session.commit()
        except SQLAlchemyError:
            logger.exception("material worker could not reconcile Action material drafts; retrying later")
            return False
        if cleaned:
            logger.info("material worker reclaimed %d Action material draft(s)", cleaned)
        return bool(cleaned)

    def _catch_up_index(self) -> bool:
        """지난 규칙으로 만들어진 색인을 조금씩 다시 만든다. 원문은 건드리지 않는다.

        한 번에 다 하지 않는 이유는 추출이 먼저이기 때문이다 — 한 묶음만 하고 돌아와 새 job을 먼저 본다.
        """
        try:
            with self._sessions() as session:
                repository = SqlAlchemyMaterialExtractionRepository(session)
                upgrades = repository.request_upgrades(limit=self._settings.material_worker_concurrency)
                upgrades += repository.request_missing(limit=self._settings.material_worker_concurrency)
                queue = MaterialJobQueue(self._queue_factory(session))
                for job in upgrades:
                    queue.enqueue(job)
                written = reindex_stale_chunks(session, limit=self._settings.material_worker_concurrency * 50)
                session.commit()
        except SQLAlchemyError:
            logger.exception("material worker could not catch the search index up; retrying later")
            return False
        if written:
            logger.info("material worker reindexed %d chunk(s) made with older analysis rules", written)
        return bool(written or upgrades)

    def _claim_jobs(self) -> list[ClaimedJob]:
        with self._sessions() as session:
            jobs = self._queue_factory(session).claim(
                JOB_KIND_MATERIAL_EXTRACTION,
                limit=self._settings.material_worker_concurrency,
                lease_seconds=self._settings.material_queue_visibility_timeout,
                worker_id=self._worker_id,
            )
            session.commit()
            return jobs

    def _service(self, session: Any) -> MaterialExtractionService:
        return MaterialExtractionService(
            SqlAlchemyMaterialExtractionRepository(session),
            max_attempts=self._settings.material_queue_max_attempts,
            # Strictly below the transport lease: a job reclaimed after lease expiry always sees a stale extraction row.
            stale_after_seconds=max(1, self._settings.material_queue_visibility_timeout // 2),
        )

    def _finish_transport(self, job: ClaimedJob, status: str) -> None:
        with self._sessions() as session:
            queue = self._queue_factory(session)
            if status == "upgraded":
                attachment_id = UUID(str(job.payload["attachment_id"]))
                current = SqlAlchemyMaterialExtractionRepository(session).for_attachments(
                    [attachment_id]
                ).get(attachment_id)
                if current is not None:
                    MaterialJobQueue(queue).enqueue(
                        MaterialExtractionJob(
                            current.id,
                            attachment_id,
                            job.payload.get("actor_id"),
                        )
                    )
                queue.complete(job.job_id, job.lease_token)
            elif status == "retry":
                delay = min(self._settings.material_queue_visibility_timeout, 2 ** max(1, job.attempt))
                queue.release(job.job_id, job.lease_token, delay_seconds=delay, error="transient extraction failure")
            elif status == "contended":
                # A live worker holds the extraction row: hand the delivery back instead of archiving it.
                queue.release(job.job_id, job.lease_token, delay_seconds=max(1, self._settings.material_queue_visibility_timeout // 2), error="extraction held by a live worker")
            elif status == "stale":
                queue.release(job.job_id, job.lease_token, delay_seconds=0, error="extraction attempt lost its owner fence")
            else:
                # completed / failed / unsupported / skipped are terminal for this delivery.
                queue.complete(job.job_id, job.lease_token)
            session.commit()

    def process(self, job: MaterialExtractionJob) -> str:
        """Exposed so tests and the API-side inline path share the exact use case. Never holds a transaction while parsing."""
        with self._sessions() as session:
            repository = SqlAlchemyMaterialExtractionRepository(session)
            extraction = repository.get(job.extraction_id)
            attachment = session.get(AttachmentRecord, job.attachment_id)
            if getattr(job, "actor_id", None) is None and attachment is not None:
                job = MaterialExtractionJob(
                    job.extraction_id, job.attachment_id, attachment.uploaded_by, getattr(job, "owner_token", None)
                )
            if not repository.is_terminal(job.extraction_id) and extraction.parser_version != PARSER_VERSION:
                if extraction.parser_version not in UPGRADABLE_PARSER_VERSIONS:
                    # A worker from an older deployment must not downgrade or consume a future parser's job.
                    return "contended"
                attachment = session.get(AttachmentRecord, extraction.attachment_id)
                if attachment is not None and attachment.lifecycle != "purged":
                    try:
                        current = repository.request(attachment)
                    except ParserVersionConflict:
                        return "contended"
                    MaterialJobQueue(self._queue_factory(session)).enqueue(MaterialExtractionJob(current.id, attachment.id, attachment.uploaded_by))
                    session.commit()
                    return "upgraded"
            claimed = self._service(session).claim(job)
            if not isinstance(claimed, str):
                actor_id = job.actor_id or (attachment.uploaded_by if attachment is not None else None)
                principal = SqlAlchemyOrganizationRepository(session).principal_for(actor_id) if actor_id else None
                readable = [] if principal is None else SessionMaterialOwners(self._application, session).sources(
                    principal, set(RESOURCE_TYPES), material_id=job.attachment_id
                )
                if not readable:
                    running = repository.running(claimed.extraction_id, claimed.attempt, claimed.owner_token)
                    if running is not None:
                        repository.fail(running, "access_denied")
                    session.commit()
                    return "failed"
                requested_at = extraction.requested_at
                if requested_at.tzinfo is None:
                    requested_at = requested_at.replace(tzinfo=UTC)
                if datetime.now(UTC) - requested_at >= timedelta(seconds=self._settings.material_total_timeout_seconds):
                    running = repository.running(claimed.extraction_id, claimed.attempt, claimed.owner_token)
                    if running is not None:
                        repository.fail(running, "time_limit_exceeded")
                    session.commit()
                    return "failed"
            session.commit()
        if isinstance(claimed, str):
            return claimed  # "skipped" (terminal) or "contended" (held by a live worker)
        if claimed.source_kind == "native_revision":
            outcome = MaterialExtractionService.extract(claimed, NativeRevisionStorage(self._sessions), NativeRevisionExtractor())
        else:
            outcome = MaterialExtractionService.extract(claimed, self._storage, self._extractor)
        try:
            with self._sessions() as session:
                status = self._service(session).finish(claimed, outcome)
                session.commit()
                return status
        finally:
            outcome.close()


def _process_material_job(
    settings: Settings,
    extractor: MaterialTextExtractor,
    job: ClaimedJob,
) -> str:
    worker = MaterialExtractionWorker(settings, extractor=extractor)
    return worker.process(
        MaterialExtractionJob(
            UUID(str(job.payload["extraction_id"])),
            UUID(str(job.payload["attachment_id"])),
            job.payload.get("actor_id"),
            job.lease_token,
        )
    )
