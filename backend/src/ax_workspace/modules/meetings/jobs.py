"""Meeting durable-job envelopes. Transport remains shared with the other product modules."""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from ax_workspace.modules.jobs.domain import JOB_KIND_MEETING_FINALIZE, DurableJobQueue, JobEnvelope


@dataclass(frozen=True, slots=True)
class MeetingFinalizationJob:
    recording_id: UUID


class MeetingFinalizationQueue:
    def __init__(self, jobs: DurableJobQueue) -> None:
        self._jobs = jobs

    def enqueue(self, job: MeetingFinalizationJob) -> None:
        self._jobs.enqueue(
            JobEnvelope(
                kind=JOB_KIND_MEETING_FINALIZE,
                ordering_key=str(job.recording_id),
                idempotency_key=f"{JOB_KIND_MEETING_FINALIZE}:{job.recording_id}",
                payload={"recording_id": str(job.recording_id)},
            )
        )
