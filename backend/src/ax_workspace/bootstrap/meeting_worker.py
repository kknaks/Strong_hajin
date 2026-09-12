"""회의록 합성을 도는 별도 프로세스 — 공용 durable job transport 위에 선다.

「회의 종료」는 전이만 하고 즉시 답한다. 합성은 여기서 돈다 (SCAX-SPEC-004 §8-1) — 사람이 기다릴 일이 아니다.
lease 와 fencing 은 transport 가 소유하고, 이 워커는 **한 회차가 곧 한 배달**이라는 것만 지킨다:
합성이 실패해도 회의 데이터는 그대로 남고 상태만 「실패」가 된다.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

from ax_workspace.bootstrap.application import WorkflowApplication, create_workflow_application
from ax_workspace.bootstrap.settings import Settings
from ax_workspace.modules.jobs.domain import JOB_KIND_MEETING_FINALIZE, ClaimedJob, DurableJobQueue
from ax_workspace.modules.meetings.finalize import FINAL_ATTEMPTS
from ax_workspace.platform.durable_jobs import MemoryDurableJobQueue, build_job_queue
from ax_workspace.platform.persistence import make_session_factory

FAILURE_BACKOFF_SECONDS = 2.0
#: 예외로 끝난 배달을 몇 번까지 되돌리는가. 합성 자신의 시도 상한과 같은 수다 (SCAX-SPEC-004 §8-3).
MAX_DELIVERIES = FINAL_ATTEMPTS
logger = logging.getLogger(__name__)


class MeetingFinalizeWorker:
    """claim job (tx) → 합성 (tx 밖) → 상태 적재 (tx) → finish job (tx, fenced)."""

    def __init__(
        self,
        settings: Settings,
        *,
        application: WorkflowApplication | None = None,
        queue_factory: Callable[[Any], DurableJobQueue] | None = None,
    ) -> None:
        self._settings = settings
        self._sessions = make_session_factory(settings.database_url)
        self._application = application or create_workflow_application(settings)
        self._worker_id = f"meeting-worker:{uuid4().hex[:12]}"
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
                # 데이터베이스가 잠깐 없어도 프로세스가 죽지 않는다. 프로그래밍 오류는 그대로 올라간다.
                failures += 1
                logger.exception("meeting worker poll failed (attempt %d); retrying after backoff", failures)
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
            return False
        for job in jobs:
            await asyncio.to_thread(self._handle, job)
        return True

    def _claim_jobs(self) -> list[ClaimedJob]:
        with self._sessions() as session:
            jobs = self._queue_factory(session).claim(
                JOB_KIND_MEETING_FINALIZE,
                limit=1,
                lease_seconds=self._settings.meeting_finalize_lease_seconds,
                worker_id=self._worker_id,
            )
            session.commit()
            return jobs

    def _handle(self, job: ClaimedJob) -> None:
        """한 배달 = 한 회차. 합성이 제 실패를 다뤘으면(「실패」로 옮겼으면) **배달은 끝난다**.

        합성이 **예외로** 끝난 것은 다르다 — 그때는 회의가 아직 「정리 중」이므로 배달을 끝내면 안 된다.
        끝내 버리면 잡은 `completed` 인데 회의는 「정리 중」에 갇혀, 화면이 오지 않을 결과를 영원히 폴링한다.
        상한(`MAX_DELIVERIES`)까지 배달을 되돌리고, 그래도 안 되면 잡을 `failed` 로 닫으면서
        회의도 「실패」로 보낸다 — 조용히 삼키지 않는다 (SPEC-004 §5.1 · §8-8).
        """
        meeting_id = UUID(str(job.payload["meeting_id"]))
        try:
            self._application.finalize_meeting(meeting_id)
        except Exception as error:  # noqa: BLE001 — 이 배달의 실패다. 워커 프로세스는 죽지 않는다
            self._deliver_failed(job, meeting_id, error)
            return
        with self._sessions() as session:
            self._queue_factory(session).complete(job.job_id, job.lease_token)
            session.commit()

    def _deliver_failed(self, job: ClaimedJob, meeting_id: UUID, error: Exception) -> None:
        """예외로 끝난 배달. 남은 시도가 있으면 되돌리고, 없으면 회의와 잡을 함께 「실패」로 닫는다."""
        last = job.attempt >= MAX_DELIVERIES
        # 사유는 **예외 종류 한 줄**이다 — 스택도, SQL 도, 내부 식별자도 사람 화면에 내지 않는다.
        reason = f"합성이 끝나지 못했습니다 ({type(error).__name__})"
        logger.exception("회의 %s 합성 배달 %d/%d 실패", meeting_id, job.attempt, MAX_DELIVERIES)
        if last:
            # 회의를 먼저 「실패」로 보낸다 — 잡이 닫히고 회의만 「정리 중」에 남는 창을 두지 않는다.
            self._application.fail_meeting_finalize(meeting_id, reason)
        with self._sessions() as session:
            queue = self._queue_factory(session)
            if last:
                queue.fail(job.job_id, job.lease_token, error=reason)
            else:
                delay = min(30.0, FAILURE_BACKOFF_SECONDS * 2 ** max(0, job.attempt - 1))
                queue.release(job.job_id, job.lease_token, delay_seconds=int(delay), error=reason)
            session.commit()
