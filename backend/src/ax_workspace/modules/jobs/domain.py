"""DurableJobQueue port.

Transport state (lease, attempts, availability) is separate from each feature's domain state. Delivery is
at-least-once; handlers must be idempotent. Every claim hands out a fencing lease token, and every later
transport write (heartbeat, release, complete, fail) is conditioned on job id + token + running state so an
expired or reclaimed worker can never persist a stale terminal transport result.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

JOB_KIND_CONVERSATION_TURN = "conversation.turn"
JOB_KIND_MATERIAL_EXTRACTION = "material.extraction"
JOB_KIND_MEETING_FINALIZE = "meeting.recording.finalize"

TERMINAL_JOB_STATES = frozenset({"completed", "failed"})


@dataclass(frozen=True, slots=True)
class JobEnvelope:
    kind: str
    ordering_key: str
    idempotency_key: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    job_id: UUID
    lease_token: UUID
    kind: str
    ordering_key: str
    idempotency_key: str
    payload: dict[str, Any]
    attempt: int


class DurableJobQueue(Protocol):
    def enqueue(self, job: JobEnvelope) -> UUID:
        """Record the job in the caller's transaction. An active (queued/running) job with the same kind +
        idempotency key is returned instead of duplicated."""
        ...

    def claim(self, kind: str, *, limit: int, lease_seconds: int, worker_id: str) -> list[ClaimedJob]:
        """Lease the absolute earliest non-terminal job of each ordering key, when it is queued and available or
        running with an expired lease. A locked or leased head blocks every later job of that key."""
        ...

    def extend_lease(self, job_id: UUID, lease_token: UUID, lease_seconds: int) -> bool: ...
    def complete(self, job_id: UUID, lease_token: UUID) -> bool: ...
    def release(self, job_id: UUID, lease_token: UUID, *, delay_seconds: int, error: str | None = None) -> bool: ...
    def fail(self, job_id: UUID, lease_token: UUID, *, error: str | None = None) -> bool: ...
