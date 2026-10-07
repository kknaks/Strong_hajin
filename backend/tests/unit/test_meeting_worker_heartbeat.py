"""합성 워커가 도는 동안 lease 를 연장한다 (WORK-012 WP2 수정 1 F-1 — report·material 워커와 같은 결)."""
from __future__ import annotations

import asyncio
import time
from uuid import uuid4

from ax_workspace.bootstrap.meeting_worker import MeetingFinalizeWorker
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.modules.jobs.domain import JOB_KIND_MEETING_FINALIZE, ClaimedJob


class _Queue:
    def __init__(self, job: ClaimedJob) -> None:
        self.job = job
        self.extended: list[int] = []
        self.completed = False
        self._claimed = False

    def claim(self, kind, *, limit, lease_seconds, worker_id):
        if self._claimed:
            return []
        self._claimed = True
        self.claimed_lease = lease_seconds
        return [self.job]

    def extend_lease(self, job_id, lease_token, lease_seconds):
        self.extended.append(lease_seconds)
        return True

    def complete(self, job_id, lease_token):
        self.completed = True


class _SlowApplication:
    def finalize_meeting(self, meeting_id):
        time.sleep(0.4)  # 합성이 heartbeat 간격보다 오래 돈다 — 시각을 재는 시험이 아니다(연장이 «있었나» 만 본다)
        return True


def test_a_long_synthesis_keeps_extending_its_lease_and_then_completes(tmp_path) -> None:
    settings = Settings(RuntimeProfile.TEST, f"sqlite:///{tmp_path / 'x.db'}", job_queue_backend="memory")
    job = ClaimedJob(uuid4(), uuid4(), JOB_KIND_MEETING_FINALIZE, "m", "m", {"meeting_id": str(uuid4())}, 1)
    queue = _Queue(job)
    worker = MeetingFinalizeWorker(
        settings, application=_SlowApplication(), queue_factory=lambda session: queue, heartbeat_seconds=0.05
    )
    assert asyncio.run(worker.run_once()) is True
    assert queue.claimed_lease == settings.effective_meeting_finalize_lease_seconds
    assert queue.extended and set(queue.extended) == {settings.effective_meeting_finalize_lease_seconds}
    assert queue.completed


class _FlakyQueue(_Queue):
    """연장이 한 번은 `False`(lease 를 잃음), 한 번은 예외(DB 끊김) — 그래도 루프는 합성을 끝까지 기다린다 (W-r2-2)."""

    def __init__(self, job: ClaimedJob) -> None:
        super().__init__(job)
        self.outcomes = ["false", "raise"]

    def extend_lease(self, job_id, lease_token, lease_seconds):
        self.extended.append(lease_seconds)
        outcome = self.outcomes.pop(0) if self.outcomes else "ok"
        if outcome == "raise":
            raise RuntimeError("database went away")
        return outcome == "ok"


def test_a_failed_or_raising_heartbeat_neither_breaks_the_loop_nor_orphans_the_synthesis(tmp_path) -> None:
    settings = Settings(RuntimeProfile.TEST, f"sqlite:///{tmp_path / 'x.db'}", job_queue_backend="memory")
    job = ClaimedJob(uuid4(), uuid4(), JOB_KIND_MEETING_FINALIZE, "m", "m", {"meeting_id": str(uuid4())}, 1)
    queue = _FlakyQueue(job)
    worker = MeetingFinalizeWorker(
        settings, application=_SlowApplication(), queue_factory=lambda session: queue, heartbeat_seconds=0.05
    )
    assert asyncio.run(worker.run_once()) is True  # 예외로 끝나지 않는다 — 다음 잡을 집으러 가지 않는다
    assert len(queue.extended) >= 3  # 거짓 · 예외 뒤에도 다음 간격에 다시 연장을 시도했다
    assert queue.completed  # 합성이 끝난 뒤 그 잡을 정상으로 닫았다
