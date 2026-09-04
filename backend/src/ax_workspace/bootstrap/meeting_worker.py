"""Separate Meeting finalization worker.

The API only persists an uploaded Recording plus a durable job. This process
claims the job, fences the recording attempt with the transport lease token,
does Soniox/Codex work outside database transactions, then persists the
immutable lineage in short transactions.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import threading
from typing import Any, Callable
from uuid import UUID, uuid4

from ax_workspace.bootstrap.application import WorkflowApplication
from ax_workspace.bootstrap.settings import Settings
from ax_workspace.modules.ax_execution.ai import AiProvider, ProviderFailure
from ax_workspace.modules.jobs.domain import ClaimedJob, DurableJobQueue
from ax_workspace.modules.meetings.jobs import JOB_KIND_MEETING_FINALIZE
from ax_workspace.modules.meetings.domain import MeetingVersionConflict
from ax_workspace.modules.meetings.transcription import FinalTranscriber, TranscriptionFailure
from ax_workspace.platform.durable_jobs import MemoryDurableJobQueue, build_job_queue
from ax_workspace.platform.recordings import LocalDirectoryRecordingStorage
from ax_workspace.platform.soniox import SonioxTranscriptionAdapter
from ax_workspace.platform.persistence import make_session_factory


class MeetingFinalizationWorker:
    def __init__(
        self,
        settings: Settings,
        *,
        transcriber: FinalTranscriber | None = None,
        provider: AiProvider | None = None,
        queue_factory: Callable[[Any], DurableJobQueue] | None = None,
    ) -> None:
        self._settings = settings
        self._application = WorkflowApplication(settings, provider)
        self._sessions = make_session_factory(settings.database_url)
        self._storage = LocalDirectoryRecordingStorage(Path(settings.recordings_dir))
        self._transcriber = transcriber or SonioxTranscriptionAdapter()
        self._worker_id = f"meeting-worker:{uuid4().hex[:12]}"
        memory = MemoryDurableJobQueue() if settings.job_queue_backend == "memory" else None
        self._queue_factory = queue_factory or (lambda session: build_job_queue(settings.job_queue_backend, session, memory))
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        while not self._stopping.is_set():
            processed = await self.run_once()
            if not processed:
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=0.25)
                except TimeoutError:
                    pass

    async def run_once(self) -> bool:
        jobs = self._claim()
        if not jobs:
            return False
        semaphore = asyncio.Semaphore(self._settings.meeting_worker_concurrency)

        async def handle(job: ClaimedJob) -> None:
            async with semaphore:
                await self._handle(job)

        await asyncio.gather(*(handle(job) for job in jobs))
        return True

    def _claim(self) -> list[ClaimedJob]:
        with self._sessions() as session:
            jobs = self._queue_factory(session).claim(
                JOB_KIND_MEETING_FINALIZE,
                limit=self._settings.meeting_worker_concurrency,
                lease_seconds=self._settings.meeting_queue_visibility_timeout,
                worker_id=self._worker_id,
            )
            session.commit()
            return jobs

    async def _handle(self, job: ClaimedJob) -> None:
        lease_lost = threading.Event()
        heartbeat = asyncio.create_task(self._heartbeat(job, lease_lost))
        try:
            status = await asyncio.to_thread(
                self.process,
                UUID(str(job.payload["recording_id"])),
                lease_token=job.lease_token,
                attempt=job.attempt,
                lease_is_active=lambda: not lease_lost.is_set(),
            )
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
        if lease_lost.is_set():
            # The database job lease is authoritative. A subsequent delivery will
            # re-fence the recording; this worker must not write a terminal transport state.
            return
        with self._sessions() as session:
            queue = self._queue_factory(session)
            if status == "retry":
                queue.release(job.job_id, job.lease_token, delay_seconds=min(30, 2 ** max(0, job.attempt - 1)), error="meeting transcription transient failure")
            elif status == "contended":
                queue.release(job.job_id, job.lease_token, delay_seconds=max(1, self._settings.meeting_queue_visibility_timeout // 2), error="meeting recording held by live worker")
            else:
                queue.complete(job.job_id, job.lease_token)
            session.commit()

    async def _heartbeat(self, job: ClaimedJob, lease_lost: threading.Event) -> None:
        """Keep a long Soniox/Codex operation visible to the transport.

        A failed fenced extension means another worker may now own the delivery.
        We deliberately do not reacquire it; ``process`` observes ``lease_lost``
        before each persistence boundary and refuses the result.
        """
        interval = max(1, self._settings.meeting_queue_visibility_timeout // 3)
        while True:
            await asyncio.sleep(interval)
            with self._sessions() as session:
                extended = self._queue_factory(session).extend_lease(
                    job.job_id,
                    job.lease_token,
                    self._settings.meeting_queue_visibility_timeout,
                )
                session.commit()
            if not extended:
                lease_lost.set()
                return

    def process(
        self,
        recording_id: UUID,
        *,
        lease_token: UUID,
        attempt: int,
        lease_is_active: Callable[[], bool] | None = None,
    ) -> str:
        active = lease_is_active or (lambda: True)
        try:
            plan = self._application.meeting_finalization_input(
                recording_id,
                lease_token=lease_token,
                stale_after_seconds=self._settings.meeting_queue_visibility_timeout,
            )
            if plan.get("completed"):
                return "completed"
            if plan.get("contended"):
                return "contended"
            if not active():
                return "stale"
            if plan["stage"] == "transcribe":
                result = self._transcriber.transcribe(
                    data=self._storage.get(plan["storage_key"]),
                    original_name=plan["original_name"],
                    content_type=plan["content_type"],
                    client_reference_id=plan["client_reference_id"],
                )
                if not active():
                    return "stale"
                self._application.record_meeting_finalization_cleanup_warning(
                    recording_id,
                    lease_token=lease_token,
                    warnings=result.cleanup_warnings,
                )
                raw = self._application.record_final_meeting_transcript(
                    recording_id=recording_id,
                    provider="soniox",
                    provider_reference=result.provider_reference,
                    segments=list(result.segments),
                    finalization_lease_token=lease_token,
                )
                transcript_id = UUID(raw["transcript_revision_id"])
                refinement_id: UUID | None = None
            elif plan["stage"] == "refinement":
                transcript_id = UUID(plan["transcript_revision_id"])
                refinement_id = None
            else:
                transcript_id = None
                refinement_id = UUID(plan["refinement_revision_id"])
            if not active():
                return "stale"
            if refinement_id is None:
                if transcript_id is None:  # defensive: every plan stage is explicit above
                    raise RuntimeError("meeting finalization plan is invalid")
                refinement = self._application.refine_meeting_transcript(
                    transcript_id,
                    finalization_lease_token=lease_token,
                )
                refinement_id = UUID(refinement["refinement_revision_id"])
            if not active():
                return "stale"
            self._application.summarize_meeting_transcript(
                refinement_id,
                finalization_lease_token=lease_token,
            )
            if not active():
                return "stale"
            self._application.complete_meeting_finalization(recording_id, lease_token=lease_token)
            return "completed"
        except TranscriptionFailure as error:
            try:
                self._application.record_meeting_finalization_cleanup_warning(
                    recording_id,
                    lease_token=lease_token,
                    warnings=error.cleanup_warnings,
                )
            except MeetingVersionConflict:
                return "stale"
            return self._record_failure(
                recording_id,
                lease_token=lease_token,
                attempt=attempt,
                code=error.code,
                retryable=error.retryable,
            )
        except MeetingVersionConflict:
            # Only an explicit recording fence conflict is stale. Provider and programming
            # failures become visible retryable/terminal domain state below.
            return "stale"
        except ProviderFailure:
            return self._record_failure(
                recording_id,
                lease_token=lease_token,
                attempt=attempt,
                code="meeting_ai_provider_failed",
                retryable=True,
            )
        except Exception:
            return self._record_failure(
                recording_id,
                lease_token=lease_token,
                attempt=attempt,
                code="meeting_finalization_error",
                retryable=True,
            )

    def _record_failure(
        self,
        recording_id: UUID,
        *,
        lease_token: UUID,
        attempt: int,
        code: str,
        retryable: bool,
    ) -> str:
        try:
            if retryable and attempt < self._settings.meeting_queue_max_attempts:
                self._application.retry_meeting_finalization(
                    recording_id,
                    lease_token=lease_token,
                    code=code,
                )
                return "retry"
            self._application.fail_meeting_finalization(
                recording_id,
                lease_token=lease_token,
                code=code,
            )
            return "failed"
        except MeetingVersionConflict:
            return "stale"
