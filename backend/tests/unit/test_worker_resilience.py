"""Worker loops survive transient database failures (e.g. a schema reset) instead of exiting and leaving turns pending,
while programming errors still propagate so they cannot hide behind infinite backoff."""
import asyncio

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from ax_workspace.bootstrap import conversation_worker, material_worker
from ax_workspace.bootstrap.conversation_worker import ConversationWorker
from ax_workspace.bootstrap.material_worker import MaterialExtractionWorker
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings


class _FlakyQueue:
    """Raises a SQLAlchemy error on the first N claims (as a dropped table or lost connection would), then is empty."""

    def __init__(self, failures: int, error: Exception | None = None) -> None:
        self.failures = failures
        self.claims = 0
        self.error = error or ProgrammingError("SELECT ...", {}, Exception('relation "durable_jobs" does not exist'))

    def _maybe_fail(self) -> None:
        self.claims += 1
        if self.claims <= self.failures:
            raise self.error

    def read_group_heads(self, *, visibility_timeout_seconds, quantity):
        self._maybe_fail()
        return []

    def claim(self, kind, *, limit, lease_seconds, worker_id):
        self._maybe_fail()
        return []


class _NoProvider:
    def converse(self, request, *, sink=None, cancel=None):  # pragma: no cover - never reached without jobs
        raise AssertionError("no provider call expected")


def _settings(tmp_path) -> Settings:
    return Settings(RuntimeProfile.TEST, f"sqlite:///{tmp_path / 'demo.db'}", materials_dir=str(tmp_path / "materials"))


async def _run_until(worker, queue: _FlakyQueue, claims: int, timeout: float = 20.0) -> None:
    async def stop_when_ready() -> None:
        while queue.claims < claims:
            await asyncio.sleep(0.02)
        worker.stop()

    await asyncio.wait_for(asyncio.gather(worker.run(), stop_when_ready()), timeout=timeout)


def test_conversation_worker_keeps_polling_after_database_errors(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(conversation_worker, "FAILURE_BACKOFF_SECONDS", 0.01)
    queue = _FlakyQueue(failures=2, error=OperationalError("SELECT 1", {}, Exception("server closed the connection")))
    worker = ConversationWorker(_settings(tmp_path), provider=_NoProvider(), queue_factory=lambda session: queue)
    asyncio.run(_run_until(worker, queue, claims=3))
    assert queue.claims >= 3  # two failures were logged and the loop went on to a successful poll


def test_material_worker_keeps_polling_after_database_errors(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(material_worker, "FAILURE_BACKOFF_SECONDS", 0.01)
    queue = _FlakyQueue(failures=2)
    worker = MaterialExtractionWorker(_settings(tmp_path), queue_factory=lambda session: queue)
    asyncio.run(_run_until(worker, queue, claims=3))
    assert queue.claims >= 3


def test_programming_bugs_still_propagate_out_of_the_worker_loop(tmp_path) -> None:
    queue = _FlakyQueue(failures=1, error=TypeError("bug in claim"))
    worker = MaterialExtractionWorker(_settings(tmp_path), queue_factory=lambda session: queue)
    with pytest.raises(TypeError, match="bug in claim"):
        asyncio.run(asyncio.wait_for(worker.run(), timeout=5))
