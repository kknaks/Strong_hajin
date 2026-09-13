"""Daily-report generation envelopes for the shared durable transport."""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from ax_workspace.modules.jobs.domain import JOB_KIND_DAILY_REPORT_GENERATE, DurableJobQueue, JobEnvelope


@dataclass(frozen=True, slots=True)
class DailyReportGenerationJob:
    generation_id: UUID
    actor_id: str


class DailyReportGenerationQueue:
    def __init__(self, jobs: DurableJobQueue) -> None:
        self._jobs = jobs

    def enqueue(self, job: DailyReportGenerationJob) -> UUID:
        return self._jobs.enqueue(
            JobEnvelope(
                kind=JOB_KIND_DAILY_REPORT_GENERATE,
                ordering_key=f"{job.actor_id}:{job.generation_id}",
                idempotency_key=f"{JOB_KIND_DAILY_REPORT_GENERATE}:{job.generation_id}",
                payload={"generation_id": str(job.generation_id), "actor_id": job.actor_id},
            )
        )
