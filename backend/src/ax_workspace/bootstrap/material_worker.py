"""Separate process that extracts and indexes Task material content through the shared durable job transport."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

from ax_workspace.bootstrap.settings import Settings
from ax_workspace.modules.jobs.domain import JOB_KIND_MATERIAL_EXTRACTION, ClaimedJob, DurableJobQueue
from ax_workspace.modules.work.material_extraction import (
    MaterialExtractionJob,
    MaterialExtractionService,
    MaterialTextExtractor,
    PARSER_VERSION,
    UPGRADABLE_PARSER_VERSIONS,
    ParserVersionConflict,
)
from ax_workspace.platform.durable_jobs import MemoryDurableJobQueue, build_job_queue
from ax_workspace.platform.material_extraction import (
    PypdfTextExtractor,
    MaterialJobQueue,
    SqlAlchemyMaterialExtractionRepository,
    reindex_stale_chunks,
)
from ax_workspace.platform.materials import LocalDirectoryMaterialStorage
from ax_workspace.platform.native_materials import NativeRevisionExtractor, NativeRevisionStorage
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
            # 할 일이 없을 때 뒤처진 색인을 따라잡는다. 분석 규칙이 바뀌었다는 것을 사람이 기억했다가 명령을
            # 부르는 것은 잊기 위한 설계다 — 규칙은 chunk마다 적혀 있으므로 이 자리가 알아서 안다.
            return await asyncio.to_thread(self._catch_up_index)
        for job in jobs:
            await asyncio.to_thread(self._handle, job)
        return True

    def _catch_up_index(self) -> bool:
        """지난 규칙으로 만들어진 색인을 조금씩 다시 만든다. 원문은 건드리지 않는다.

        한 번에 다 하지 않는 이유는 추출이 먼저이기 때문이다 — 한 묶음만 하고 돌아와 새 job을 먼저 본다.
        """
        try:
            with self._sessions() as session:
                upgrades = SqlAlchemyMaterialExtractionRepository(session).request_upgrades(limit=self._settings.material_worker_concurrency)
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

    def _handle(self, job: ClaimedJob) -> None:
        status = self.process(MaterialExtractionJob(UUID(str(job.payload["extraction_id"])), UUID(str(job.payload["attachment_id"]))))
        with self._sessions() as session:
            queue = self._queue_factory(session)
            if status == "retry":
                delay = min(self._settings.material_queue_visibility_timeout, 2 ** max(0, job.attempt - 1))
                queue.release(job.job_id, job.lease_token, delay_seconds=delay, error="transient extraction failure")
            elif status == "contended":
                # A live worker holds the extraction row: hand the delivery back instead of archiving it.
                queue.release(job.job_id, job.lease_token, delay_seconds=max(1, self._settings.material_queue_visibility_timeout // 2), error="extraction held by a live worker")
            else:
                # completed / failed / unsupported / skipped / stale are terminal for this delivery.
                queue.complete(job.job_id, job.lease_token)
            session.commit()

    def process(self, job: MaterialExtractionJob) -> str:
        """Exposed so tests and the API-side inline path share the exact use case. Never holds a transaction while parsing."""
        with self._sessions() as session:
            repository = SqlAlchemyMaterialExtractionRepository(session)
            extraction = repository.get(job.extraction_id)
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
                    MaterialJobQueue(self._queue_factory(session)).enqueue(MaterialExtractionJob(current.id, attachment.id))
                    session.commit()
                    return "skipped"
            claimed = self._service(session).claim(job)
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
