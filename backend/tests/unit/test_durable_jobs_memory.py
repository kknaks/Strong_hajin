"""The in-memory DurableJobQueue fake mirrors the port's visible semantics for unit/contract tests.

Transaction, SKIP LOCKED, and multi-worker correctness are asserted only in the PostgreSQL integration tests.
"""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from ax_workspace.modules.jobs.domain import JobEnvelope
from ax_workspace.platform.durable_jobs import MemoryDurableJobQueue


def _job(key: str, idem: str, kind: str = "test.kind") -> JobEnvelope:
    return JobEnvelope(kind=kind, ordering_key=key, idempotency_key=idem, payload={"idem": idem})


def test_only_the_earliest_non_terminal_job_per_ordering_key_is_claimable() -> None:
    queue = MemoryDurableJobQueue()
    a1 = queue.enqueue(_job("A", "a-1"))
    queue.enqueue(_job("A", "a-2"))
    b1 = queue.enqueue(_job("B", "b-1"))
    claimed = queue.claim("test.kind", limit=10, lease_seconds=30, worker_id="w")
    assert [job.job_id for job in claimed] == [a1, b1]
    # a-2 stays blocked while a-1 is running, and becomes the head only after a-1 is terminal.
    assert queue.claim("test.kind", limit=10, lease_seconds=30, worker_id="w") == []
    assert queue.complete(a1, claimed[0].lease_token) is True
    [a2] = queue.claim("test.kind", limit=10, lease_seconds=30, worker_id="w")
    assert a2.idempotency_key == "a-2" and a2.attempt == 1


def test_lease_expiry_reclaims_with_a_new_token_and_fences_the_stale_owner() -> None:
    queue = MemoryDurableJobQueue()
    job_id = queue.enqueue(_job("K", "k-1"))
    [first] = queue.claim("test.kind", limit=1, lease_seconds=30, worker_id="slow")
    assert queue.claim("test.kind", limit=1, lease_seconds=30, worker_id="eager") == []
    # Expire the lease without sleeping.
    queue._jobs[0]["lease_expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
    [second] = queue.claim("test.kind", limit=1, lease_seconds=30, worker_id="eager")
    assert second.job_id == job_id and second.attempt == 2 and second.lease_token != first.lease_token
    assert queue.extend_lease(job_id, first.lease_token, 30) is False
    assert queue.complete(job_id, first.lease_token) is False
    assert queue.release(job_id, first.lease_token, delay_seconds=0) is False
    assert queue.fail(job_id, first.lease_token) is False
    assert queue.complete(job_id, second.lease_token) is True
    assert queue.complete(job_id, second.lease_token) is False  # terminal rows take no further writes
    assert queue.pending_count() == 0


def test_idempotency_key_dedupes_active_jobs_and_release_delays_visibility() -> None:
    queue = MemoryDurableJobQueue()
    first = queue.enqueue(_job("K", "same"))
    assert queue.enqueue(_job("K", "same")) == first
    [claimed] = queue.claim("test.kind", limit=1, lease_seconds=30, worker_id="w")
    assert queue.release(claimed.job_id, claimed.lease_token, delay_seconds=60, error="later") is True
    assert queue.claim("test.kind", limit=1, lease_seconds=30, worker_id="w") == []
    assert queue.enqueue(_job("K", "same")) == first  # still active while queued
    queue._jobs[0]["available_at"] = datetime.now(UTC)
    [again] = queue.claim("test.kind", limit=1, lease_seconds=30, worker_id="w")
    assert again.attempt == 2 and queue.fail(again.job_id, again.lease_token, error="boom") is True
    assert queue.enqueue(_job("K", "same")) != first  # a terminal job frees the key
    assert queue.snapshot()[0]["last_error"] == "boom"


def test_unknown_token_and_kind_isolation() -> None:
    queue = MemoryDurableJobQueue()
    queue.enqueue(_job("K", "k", kind="kind.a"))
    assert queue.claim("kind.b", limit=5, lease_seconds=30, worker_id="w") == []
    [job] = queue.claim("kind.a", limit=5, lease_seconds=30, worker_id="w")
    assert queue.complete(job.job_id, uuid4()) is False


def test_expired_lease_is_fenced_out_before_anyone_reclaims_it() -> None:
    queue = MemoryDurableJobQueue()
    job_id = queue.enqueue(_job("K", "k-expired"))
    [claimed] = queue.claim("test.kind", limit=1, lease_seconds=30, worker_id="slow")
    queue._jobs[0]["lease_expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
    # Nobody reclaimed the job yet; the original owner still may not heartbeat itself back to life or finish it.
    assert queue.extend_lease(job_id, claimed.lease_token, 30) is False
    assert queue.complete(job_id, claimed.lease_token) is False
    assert queue.fail(job_id, claimed.lease_token, error="late") is False
    assert queue.release(job_id, claimed.lease_token, delay_seconds=0) is False
    assert queue.snapshot()[0]["state"] == "running"  # left for reclaim
