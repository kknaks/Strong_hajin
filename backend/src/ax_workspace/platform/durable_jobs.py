"""DurableJobQueue adapters: standard PostgreSQL table (FOR UPDATE SKIP LOCKED + lease), in-memory fake, null."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import threading
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ax_workspace.modules.jobs.domain import ClaimedJob, JobEnvelope
from ax_workspace.platform.persistence import DurableJobRecord

_CLAIM_SQL = text(
    """
    WITH eligible AS (
        SELECT j.id
        FROM durable_jobs j
        WHERE j.kind = :kind
          AND j.state IN ('queued', 'running')
          AND NOT EXISTS (
              SELECT 1 FROM durable_jobs e
              WHERE e.kind = j.kind
                AND e.ordering_key = j.ordering_key
                AND e.state IN ('queued', 'running')
                AND e.sequence < j.sequence
          )
          AND (
              (j.state = 'queued' AND j.available_at <= now())
              OR (j.state = 'running' AND j.lease_expires_at <= now())
          )
        ORDER BY j.sequence
        LIMIT :limit
        FOR UPDATE SKIP LOCKED
    )
    UPDATE durable_jobs d
       SET state = 'running',
           attempt_count = d.attempt_count + 1,
           lease_token = CAST(:token_prefix || substr(md5(random()::text || d.id::text), 1, 12) AS text)::uuid,
           lease_expires_at = now() + make_interval(secs => :lease_seconds),
           leased_by = :worker_id,
           updated_at = now()
      FROM eligible
     WHERE d.id = eligible.id
    RETURNING d.id, d.lease_token, d.kind, d.ordering_key, d.idempotency_key, d.payload, d.attempt_count
    """
)


class SqlAlchemyDurableJobQueue:
    """Joins the caller's session transaction. Requires PostgreSQL for claim semantics (SKIP LOCKED, now())."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(self, job: JobEnvelope) -> UUID:
        existing = self._session.scalar(
            select(DurableJobRecord.id).where(
                DurableJobRecord.kind == job.kind,
                DurableJobRecord.idempotency_key == job.idempotency_key,
                DurableJobRecord.state.in_(("queued", "running")),
            )
        )
        if existing is not None:
            return existing
        # Conflict-safe insert: a concurrent enqueue with the same active key makes this statement wait for that
        # transaction, then insert nothing, so the caller's transaction is never poisoned by a unique violation.
        inserted = self._session.execute(
            text(
                "INSERT INTO durable_jobs (id, kind, ordering_key, idempotency_key, payload, state, attempt_count, available_at, created_at, updated_at) "
                "VALUES (:id, :kind, :ordering_key, :idempotency_key, CAST(:payload AS json), 'queued', 0, now(), now(), now()) "
                "ON CONFLICT (kind, idempotency_key) WHERE state IN ('queued', 'running') DO NOTHING "
                "RETURNING id"
            ),
            {"id": uuid4(), "kind": job.kind, "ordering_key": job.ordering_key, "idempotency_key": job.idempotency_key, "payload": json.dumps(job.payload)},
        ).scalar_one_or_none()
        if inserted is not None:
            return inserted if isinstance(inserted, UUID) else UUID(str(inserted))
        winner = self._session.scalar(
            select(DurableJobRecord.id).where(
                DurableJobRecord.kind == job.kind,
                DurableJobRecord.idempotency_key == job.idempotency_key,
                DurableJobRecord.state.in_(("queued", "running")),
            )
        )
        if winner is None:  # the concurrent job reached a terminal state in between; ours is now the only active one
            return self.enqueue(job)
        return winner

    def claim(self, kind: str, *, limit: int, lease_seconds: int, worker_id: str) -> list[ClaimedJob]:
        if self._session.bind is None or self._session.bind.dialect.name != "postgresql":
            raise RuntimeError("SqlAlchemyDurableJobQueue.claim requires PostgreSQL; use the memory backend for tests")
        # Token = uuid4 prefix (20 hex) + 12 random hex from the database; unique per claim, opaque to callers.
        token_prefix = uuid4().hex[:20]
        rows = self._session.execute(
            _CLAIM_SQL, {"kind": kind, "limit": max(1, limit), "lease_seconds": lease_seconds, "worker_id": worker_id[:200], "token_prefix": token_prefix}
        ).mappings().all()
        claimed: list[ClaimedJob] = []
        for row in rows:
            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            claimed.append(
                ClaimedJob(
                    job_id=row["id"] if isinstance(row["id"], UUID) else UUID(str(row["id"])),
                    lease_token=row["lease_token"] if isinstance(row["lease_token"], UUID) else UUID(str(row["lease_token"])),
                    kind=str(row["kind"]),
                    ordering_key=str(row["ordering_key"]),
                    idempotency_key=str(row["idempotency_key"]),
                    payload=dict(payload or {}),
                    attempt=int(row["attempt_count"]),
                )
            )
        # Deterministic ordering for callers regardless of UPDATE ... RETURNING order.
        claimed.sort(key=lambda item: item.attempt)
        return claimed

    def _fenced_update(self, job_id: UUID, token: UUID, /, **values: Any) -> bool:
        """UPDATE conditioned on id + current lease token + running state; 0 rows means the caller's lease is stale."""
        assignments = ", ".join(f"{key} = :set_{key}" for key in values)
        result = self._session.execute(
            text(
                f"UPDATE durable_jobs SET {assignments}, updated_at = now() "
                "WHERE id = :job_id AND lease_token = :fence_token AND state = 'running' AND lease_expires_at > now()"
            ),
            {"job_id": job_id, "fence_token": token, **{f"set_{key}": value for key, value in values.items()}},
        )
        return int(result.rowcount or 0) == 1

    def extend_lease(self, job_id: UUID, lease_token: UUID, lease_seconds: int) -> bool:
        result = self._session.execute(
            text(
                "UPDATE durable_jobs SET lease_expires_at = now() + make_interval(secs => :lease_seconds), updated_at = now() "
                "WHERE id = :job_id AND lease_token = :lease_token AND state = 'running' AND lease_expires_at > now()"
            ),
            {"job_id": job_id, "lease_token": lease_token, "lease_seconds": lease_seconds},
        )
        return int(result.rowcount or 0) == 1

    def complete(self, job_id: UUID, lease_token: UUID) -> bool:
        # Terminal rows carry no lease owner/token, so transport state is unambiguous.
        return self._fenced_update(job_id, lease_token, state="completed", completed_at=datetime.now(UTC), lease_token=None, lease_expires_at=None, leased_by=None)

    def release(self, job_id: UUID, lease_token: UUID, *, delay_seconds: int, error: str | None = None) -> bool:
        return self._fenced_update(
            job_id, lease_token, state="queued", available_at=datetime.now(UTC) + timedelta(seconds=max(0, delay_seconds)),
            lease_token=None, lease_expires_at=None, leased_by=None, last_error=(error or None) and str(error)[:500],
        )

    def fail(self, job_id: UUID, lease_token: UUID, *, error: str | None = None) -> bool:
        return self._fenced_update(job_id, lease_token, state="failed", completed_at=datetime.now(UTC), lease_token=None, lease_expires_at=None, leased_by=None, last_error=(error or None) and str(error)[:500])


class MemoryDurableJobQueue:
    """In-process fake with the same visible semantics (FIFO head per key, lease, fencing) for unit/contract tests.

    It cannot join a database transaction, so correctness claims about transactions, SKIP LOCKED, and concurrent
    workers belong to the PostgreSQL integration tests, not here.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: list[dict[str, Any]] = []
        self._sequence = 0

    def enqueue(self, job: JobEnvelope) -> UUID:
        with self._lock:
            for row in self._jobs:
                if row["kind"] == job.kind and row["idempotency_key"] == job.idempotency_key and row["state"] in ("queued", "running"):
                    return row["id"]
            self._sequence += 1
            now = datetime.now(UTC)
            row = {
                "id": uuid4(), "sequence": self._sequence, "kind": job.kind, "ordering_key": job.ordering_key,
                "idempotency_key": job.idempotency_key, "payload": dict(job.payload), "state": "queued", "attempt_count": 0,
                "available_at": now, "lease_token": None, "lease_expires_at": None, "leased_by": None, "last_error": None,
                "created_at": now, "completed_at": None,
            }
            self._jobs.append(row)
            return row["id"]

    def claim(self, kind: str, *, limit: int, lease_seconds: int, worker_id: str) -> list[ClaimedJob]:
        now = datetime.now(UTC)
        claimed: list[ClaimedJob] = []
        with self._lock:
            active = [row for row in self._jobs if row["kind"] == kind and row["state"] in ("queued", "running")]
            heads: dict[str, dict[str, Any]] = {}
            for row in sorted(active, key=lambda item: item["sequence"]):
                heads.setdefault(row["ordering_key"], row)
            for row in sorted(heads.values(), key=lambda item: item["sequence"]):
                if len(claimed) >= max(1, limit):
                    break
                eligible = (row["state"] == "queued" and row["available_at"] <= now) or (
                    row["state"] == "running" and row["lease_expires_at"] is not None and row["lease_expires_at"] <= now
                )
                if not eligible:
                    continue
                row["state"] = "running"
                row["attempt_count"] += 1
                row["lease_token"] = uuid4()
                row["lease_expires_at"] = now + timedelta(seconds=lease_seconds)
                row["leased_by"] = worker_id
                claimed.append(ClaimedJob(row["id"], row["lease_token"], row["kind"], row["ordering_key"], row["idempotency_key"], dict(row["payload"]), row["attempt_count"]))
        return claimed

    def _fenced(self, job_id: UUID, lease_token: UUID) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        for row in self._jobs:
            if (
                row["id"] == job_id
                and row["lease_token"] == lease_token
                and row["state"] == "running"
                and row["lease_expires_at"] is not None
                and row["lease_expires_at"] > now  # an expired lease is fenced out even before anyone reclaims it
            ):
                return row
        return None

    def extend_lease(self, job_id: UUID, lease_token: UUID, lease_seconds: int) -> bool:
        with self._lock:
            row = self._fenced(job_id, lease_token)
            if row is None:
                return False
            row["lease_expires_at"] = datetime.now(UTC) + timedelta(seconds=lease_seconds)
            return True

    def complete(self, job_id: UUID, lease_token: UUID) -> bool:
        with self._lock:
            row = self._fenced(job_id, lease_token)
            if row is None:
                return False
            row.update(state="completed", completed_at=datetime.now(UTC), lease_token=None, lease_expires_at=None, leased_by=None)
            return True

    def release(self, job_id: UUID, lease_token: UUID, *, delay_seconds: int, error: str | None = None) -> bool:
        with self._lock:
            row = self._fenced(job_id, lease_token)
            if row is None:
                return False
            row.update(state="queued", available_at=datetime.now(UTC) + timedelta(seconds=max(0, delay_seconds)), lease_token=None, lease_expires_at=None, leased_by=None, last_error=error)
            return True

    def fail(self, job_id: UUID, lease_token: UUID, *, error: str | None = None) -> bool:
        with self._lock:
            row = self._fenced(job_id, lease_token)
            if row is None:
                return False
            row.update(state="failed", completed_at=datetime.now(UTC), lease_token=None, lease_expires_at=None, leased_by=None, last_error=error)
            return True

    # test helpers
    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._jobs]

    def pending_count(self, kind: str | None = None) -> int:
        with self._lock:
            return sum(1 for row in self._jobs if row["state"] in ("queued", "running") and (kind is None or row["kind"] == kind))


class NullDurableJobQueue:
    """Explicit test-only transport: records nothing and delivers nothing."""

    def enqueue(self, job: JobEnvelope) -> UUID:
        del job
        return uuid4()

    def claim(self, kind: str, *, limit: int, lease_seconds: int, worker_id: str) -> list[ClaimedJob]:
        del kind, limit, lease_seconds, worker_id
        return []

    def extend_lease(self, job_id: UUID, lease_token: UUID, lease_seconds: int) -> bool:
        del job_id, lease_token, lease_seconds
        return False

    def complete(self, job_id: UUID, lease_token: UUID) -> bool:
        del job_id, lease_token
        return False

    def release(self, job_id: UUID, lease_token: UUID, *, delay_seconds: int, error: str | None = None) -> bool:
        del job_id, lease_token, delay_seconds, error
        return False

    def fail(self, job_id: UUID, lease_token: UUID, *, error: str | None = None) -> bool:
        del job_id, lease_token, error
        return False


def build_job_queue(backend: str, session: Session | None, memory: MemoryDurableJobQueue | None):
    if backend == "postgres":
        if session is None:
            raise RuntimeError("postgres job queue requires a session")
        return SqlAlchemyDurableJobQueue(session)
    if backend == "memory":
        if memory is None:
            raise RuntimeError("memory job queue requires a shared MemoryDurableJobQueue instance")
        return memory
    if backend == "null":
        return NullDurableJobQueue()
    raise RuntimeError(f"Unknown AX_JOB_QUEUE_BACKEND {backend!r}; use postgres or memory")
