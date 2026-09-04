import os
import asyncio
import tempfile
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock, Thread
import time
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text

from ax_workspace.modules.organization_access.domain import seeded_principal
from ax_workspace.platform.persistence import make_session_factory
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.bootstrap.application import create_workflow_application
from ax_workspace.bootstrap.meeting_worker import MeetingFinalizationWorker
from ax_workspace.entrypoints.conversation_worker import ConversationWorker
from ax_workspace.entrypoints.http import create_app
from ax_workspace.modules.ax_execution.ai import (
    AiConversationResult,
    AiGeneration,
    AiToolInvocation,
    ProviderRequestFailed,
)
from ax_workspace.modules.ax_execution.conversations import ConversationExecution
from ax_workspace.modules.jobs.domain import JOB_KIND_CONVERSATION_TURN, JOB_KIND_MEETING_FINALIZE, JOB_KIND_MATERIAL_EXTRACTION, JobEnvelope
from ax_workspace.modules.meetings.domain import MeetingVersionConflict
from ax_workspace.modules.meetings.transcription import FinalTranscriptSegment, FinalTranscriptionResult
from ax_workspace.platform.conversation_jobs import ConversationJobQueue
from ax_workspace.platform.durable_jobs import SqlAlchemyDurableJobQueue
from ax_workspace.platform.persistence import DurableJobRecord
from ax_workspace.platform.persistence import (
    ConversationTurnRecord,
    ConversationMessageRecord,
    ConversationAuditEventRecord,
    ActionItemRecord,
    EmploymentPeriodRecord,
    ToolInvocationRecord,
)


class ConversationProvider:
    def __init__(self, *, delay_seconds: float = 0, failures: int = 0) -> None:
        self.delay_seconds = delay_seconds
        self.failures = failures
        self.calls = 0
        self.started = Event()

    def converse(self, request, *, sink=None, cancel=None) -> AiConversationResult:
        self.calls += 1
        self.started.set()
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        if self.calls <= self.failures:
            raise ProviderRequestFailed("provider test failure")
        return AiConversationResult(
            provider_run_ref=f"run-{self.calls}",
            provider_session_ref="session-test",
            body=f"응답 {self.calls}",
            tool_invocations=[
                AiToolInvocation(
                    provider_call_id=f"tool-{self.calls}",
                    tool_name="work_request_list",
                    display_name="업무 요청 조회",
                    input_summary="안전한 요약",
                    state="completed",
                    result_summary="조회 완료",
                    error_summary=None,
                    latency_ms=1,
                )
            ],
        )


class ConcurrentReportProvider:
    """Makes a second report caller contend while the first holds its causal lock."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self.calls = 0
        self.started = Event()
        self._lock = Lock()
        self.idle_in_transaction_counts: list[int] = []

    def generate(self, request) -> AiGeneration:
        with self._lock:
            self.calls += 1
        with make_session_factory(self._database_url)() as session:
            self.idle_in_transaction_counts.append(
                int(
                    session.execute(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE datname = current_database() "
                            "AND state = 'idle in transaction'"
                        )
                    ).scalar_one()
                )
            )
        self.started.set()
        time.sleep(0.25)
        return AiGeneration(
            provider_run_ref="concurrent-report-run",
            provider_session_ref="concurrent-report-session",
            body="동시 요청에도 하나의 보고 초안을 생성했습니다.",
            requested_model="test-model",
            observed_model="test-model",
            requested_tier="test",
            observed_tier="test",
            latency_ms=1,
            usage=None,
        )

    def converse(self, request, *, sink=None, cancel=None):  # pragma: no cover - this double is report-only
        raise AssertionError("report provider must not service conversation execution")


class MeetingPipelineProvider:
    """Deterministic test composition only; the worker production default is Codex CLI."""

    def generate(self, request) -> AiGeneration:
        if "statements" in request.output_schema["properties"]:
            body = (
                '{"body":"## 요약\\n일정을 논의했습니다.","statements":['
                '{"kind":"summary","text":"일정을 논의했습니다.",'
                '"refinement_start_sequence":1,"refinement_end_sequence":1}]}'
            )
        else:
            body = (
                '{"segments":[{"raw_start_source_key":"segment-1",'
                '"raw_end_source_key":"segment-2","start_ms":0,"end_ms":3000,'
                '"text":"일정을 논의했습니다.","speaker_label":null,'
                '"correction_kind":"merge","confidence":0.9}]}'
            )
        return AiGeneration(
            provider_run_ref="meeting-pipeline-test",
            provider_session_ref=None,
            body=body,
            requested_model="test",
            observed_model="test",
            requested_tier="test",
            observed_tier=None,
            latency_ms=1,
            usage=None,
        )

    def converse(self, request, *, sink=None, cancel=None):  # pragma: no cover - meeting worker is structured only
        raise AssertionError("meeting worker must not use conversation generation")


class MeetingPipelineTranscriber:
    def transcribe(self, *, data, original_name, content_type, client_reference_id) -> FinalTranscriptionResult:
        del data, original_name, content_type, client_reference_id
        return FinalTranscriptionResult(
            provider_reference="soniox:recovery",
            provider_file_ref="soniox:file",
            segments=(
                FinalTranscriptSegment("segment-1", 0, 1_500, "일정을"),
                FinalTranscriptSegment("segment-2", 1_500, 3_000, "논의했습니다."),
            ),
        )


def _conversation_client(database_url: str) -> TestClient:
    return TestClient(
        create_app(
            Settings(
                RuntimeProfile.TEST,
                database_url,
                job_queue_backend="postgres",
            )
        )
    )


def _queue_count(session, kind: str = JOB_KIND_CONVERSATION_TURN) -> int:
    """Non-terminal (queued/running) durable jobs of one kind; completed/failed rows stay for audit."""
    return int(
        session.execute(
            text("SELECT count(*) FROM durable_jobs WHERE kind = :kind AND state IN ('queued', 'running')"),
            {"kind": kind},
        ).scalar_one()
    )


def _drain(worker, *, timeout: float = 4.0) -> bool:
    """Run a worker until it claims something or the deadline passes (lease/backoff windows are time based)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if asyncio.run(worker.run_once()):
            return True
        time.sleep(0.1)
    return False


@pytest.mark.integration
def test_postgres_meeting_finalization_reclaims_a_crashed_attempt_and_fences_the_late_result(tmp_path) -> None:
    database_url = _postgres_test_url()
    reset_database(database_url)
    settings = Settings(
        RuntimeProfile.TEST,
        database_url,
        job_queue_backend="postgres",
        recordings_dir=str(tmp_path / "recordings"),
        meeting_queue_visibility_timeout=1,
    )
    client = TestClient(create_app(settings, report_provider=MeetingPipelineProvider()))
    headers = {"X-Demo-Persona": "mina"}
    meeting = client.post(
        "/api/meetings",
        headers=headers,
        json={
            "organization_id": "scax",
            "title": "PG crash recovery",
            "starts_at": "2026-09-10T01:00:00Z",
            "ends_at": "2026-09-10T02:00:00Z",
            "visibility": "private",
            "attendee_ids": ["jiho"],
        },
    ).json()
    started = client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/start",
        headers=headers,
        json={"purpose": "fenced PostgreSQL recovery"},
    ).json()
    client.post(
        f"/api/meetings/{meeting['meeting_id']}/recordings/{started['recording_id']}/stop",
        headers=headers,
        data={"expected_version": str(started["version"])},
        files={"audio": ("recovery.webm", b"audio", "audio/webm")},
    ).raise_for_status()

    sessions = make_session_factory(database_url)
    with sessions() as session:
        [first_delivery] = SqlAlchemyDurableJobQueue(session).claim(
            JOB_KIND_MEETING_FINALIZE,
            limit=1,
            lease_seconds=1,
            worker_id="crashed-worker",
        )
        session.commit()
    application = create_workflow_application(settings, MeetingPipelineProvider())
    application.meeting_finalization_input(
        UUID(started["recording_id"]),
        lease_token=first_delivery.lease_token,
        stale_after_seconds=1,
    )

    time.sleep(1.1)
    recovery_worker = MeetingFinalizationWorker(
        settings,
        provider=MeetingPipelineProvider(),
        transcriber=MeetingPipelineTranscriber(),
    )
    assert asyncio.run(recovery_worker.run_once()) is True
    detail = client.get(f"/api/meetings/{meeting['meeting_id']}", headers=headers).json()
    assert detail["recordings"][0]["state"] == "transcribed"
    with pytest.raises(MeetingVersionConflict, match="lease is stale"):
        application.record_final_meeting_transcript(
            recording_id=UUID(started["recording_id"]),
            provider="soniox",
            provider_reference="soniox:late-crashed-worker",
            segments=[FinalTranscriptSegment("late", 0, 100, "늦은 결과")],
            finalization_lease_token=first_delivery.lease_token,
        )


def _postgres_test_url() -> str:
    database_url = os.getenv("AX_POSTGRES_TEST_URL")
    if not database_url:
        pytest.skip("Set AX_POSTGRES_TEST_URL to run against a disposable PostgreSQL database")
    return database_url


@pytest.mark.integration
def test_postgres_serializes_concurrent_daily_report_causation_before_workflow_execution() -> None:
    database_url = _postgres_test_url()
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, job_queue_backend="postgres")
    provider = ConcurrentReportProvider(database_url)
    first_application = create_workflow_application(settings, provider)
    second_application = create_workflow_application(settings, provider)
    first_principal = first_application.authenticated_principal("mina")
    second_principal = second_application.authenticated_principal("mina")

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            first_application.generate_daily_report_draft,
            first_principal,
            "2026-09-03",
            "concurrent-report-causation",
        )
        assert provider.started.wait(timeout=2), "first caller did not enter the provider"
        second = executor.submit(
            second_application.generate_daily_report_draft,
            second_principal,
            "2026-09-03",
            "concurrent-report-causation",
        )
        first_result = first.result(timeout=5)
        second_result = second.result(timeout=5)

    assert provider.calls == 1
    assert provider.idle_in_transaction_counts == [0]
    assert first_result["report_id"] == second_result["report_id"]
    assert first_result["draft_id"] == second_result["draft_id"]
    assert first_result["workflow_run_id"] == second_result["workflow_run_id"]


@pytest.mark.integration
def test_job_queue_enqueue_and_domain_turn_commit_atomically_then_parallel_groups_archive() -> None:
    database_url = _postgres_test_url()
    reset_database(database_url)
    client = _conversation_client(database_url)
    first_conversation = client.post(
        "/api/conversations",
        headers={"X-Demo-Persona": "mina"},
        json={"title": "첫 대화"},
    ).json()
    second_conversation = client.post(
        "/api/conversations",
        headers={"X-Demo-Persona": "mina"},
        json={"title": "둘째 대화"},
    ).json()

    first = client.post(
        f"/api/conversations/{first_conversation['conversation_id']}/messages",
        headers={"X-Demo-Persona": "mina", "Idempotency-Key": "first-message"},
        json={"body": "첫 발화", "context": []},
    )
    second = client.post(
        f"/api/conversations/{second_conversation['conversation_id']}/messages",
        headers={"X-Demo-Persona": "mina", "Idempotency-Key": "second-message"},
        json={"body": "동시 발화", "context": []},
    )
    assert first.status_code == second.status_code == 202

    session_factory = make_session_factory(database_url)
    with session_factory() as session:
        assert _queue_count(session) == 2
        assert len(list(session.scalars(select(ConversationTurnRecord)))) == 2

    provider = ConversationProvider(delay_seconds=0.2)
    worker = ConversationWorker(
        Settings(
            RuntimeProfile.TEST,
            database_url,
            conversation_queue_visibility_timeout=1,
            conversation_worker_concurrency=2,
            job_queue_backend="postgres",
        ),
        provider=provider,
    )
    started = time.monotonic()
    assert asyncio.run(worker.run_once()) is True
    assert time.monotonic() - started < 0.35
    assert provider.calls == 2
    with session_factory() as session:
        assert _queue_count(session) == 0
        assert [turn.state for turn in session.scalars(select(ConversationTurnRecord).order_by(ConversationTurnRecord.started_at))] == [
            "completed",
            "completed",
        ]

    reset_database(database_url)
    with session_factory() as session:
        assert _queue_count(session) == 0


@pytest.mark.integration
def test_job_queue_enqueue_failure_rolls_back_the_domain_message_and_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _postgres_test_url()
    reset_database(database_url)
    client = _conversation_client(database_url)
    conversation = client.post(
        "/api/conversations",
        headers={"X-Demo-Persona": "mina"},
        json={"title": "원자성 대화"},
    ).json()

    def reject_enqueue(self, job) -> None:
        raise RuntimeError("job transport unavailable")

    monkeypatch.setattr(SqlAlchemyDurableJobQueue, "enqueue", reject_enqueue)
    with pytest.raises(RuntimeError, match="job transport unavailable"):
        client.post(
            f"/api/conversations/{conversation['conversation_id']}/messages",
            headers={"X-Demo-Persona": "mina", "Idempotency-Key": "atomic-failure"},
            json={"body": "전달 실패", "context": []},
        )
    with make_session_factory(database_url)() as session:
        assert _queue_count(session) == 0
        assert list(
            session.scalars(
                select(ConversationMessageRecord).where(
                    ConversationMessageRecord.conversation_id
                    == UUID(conversation["conversation_id"])
                )
            )
        ) == []
        assert list(
            session.scalars(
                select(ConversationTurnRecord).where(
                    ConversationTurnRecord.conversation_id
                    == UUID(conversation["conversation_id"])
                )
            )
        ) == []


@pytest.mark.integration
def test_job_queue_retries_visibility_timeout_and_ignores_a_duplicate_terminal_turn() -> None:
    database_url = _postgres_test_url()
    reset_database(database_url)
    client = _conversation_client(database_url)
    conversation = client.post(
        "/api/conversations",
        headers={"X-Demo-Persona": "mina"},
        json={"title": "재시도 대화"},
    ).json()
    queued = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={"X-Demo-Persona": "mina", "Idempotency-Key": "queued-message"},
        json={"body": "재시도 발화", "context": []},
    )
    assert queued.status_code == 202
    session_factory = make_session_factory(database_url)
    retry_provider = ConversationProvider(failures=1)
    retry_worker = ConversationWorker(
        Settings(
            RuntimeProfile.TEST,
            database_url,
            conversation_queue_visibility_timeout=1,
            conversation_queue_max_attempts=2,
            job_queue_backend="postgres",
        ),
        provider=retry_provider,
    )
    assert asyncio.run(retry_worker.run_once()) is True
    with session_factory() as session:
        assert _queue_count(session) == 1  # released back to queued with a backoff, not terminal
        assert session.scalar(
            select(ConversationTurnRecord).order_by(ConversationTurnRecord.started_at.desc())
        ).state == "running"
    time.sleep(1.1)
    assert asyncio.run(retry_worker.run_once()) is True
    with session_factory() as session:
        assert _queue_count(session) == 0
        turn = session.scalar(
            select(ConversationTurnRecord).order_by(ConversationTurnRecord.started_at.desc())
        )
        assert turn is not None
        assert turn.state == "completed"
        assert len(list(session.scalars(select(ToolInvocationRecord)))) == 1
        # A duplicate delivery for a terminal turn (idempotency key freed by completion) is consumed without a provider call.
        ConversationJobQueue(SqlAlchemyDurableJobQueue(session)).enqueue(
            ConversationExecution(turn.id, turn.conversation_id, turn.execution_id)
        )
        session.commit()
    assert asyncio.run(retry_worker.run_once()) is False
    assert retry_provider.calls == 2
    with session_factory() as session:
        assert len(list(session.scalars(select(ToolInvocationRecord)))) == 1


@pytest.mark.integration
def test_job_queue_heartbeat_keeps_a_slow_turn_invisible_to_a_second_worker() -> None:
    database_url = _postgres_test_url()
    reset_database(database_url)
    client = _conversation_client(database_url)
    conversation = client.post(
        "/api/conversations",
        headers={"X-Demo-Persona": "mina"},
        json={"title": "긴 응답 대화"},
    ).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={"X-Demo-Persona": "mina", "Idempotency-Key": "slow-turn"},
        json={"body": "느린 응답", "context": []},
    )
    assert accepted.status_code == 202
    settings = Settings(
        RuntimeProfile.TEST,
        database_url,
        conversation_queue_visibility_timeout=2,
        job_queue_backend="postgres",
    )
    first_worker = ConversationWorker(
        settings,
        provider=ConversationProvider(delay_seconds=3),
    )
    second_worker = ConversationWorker(settings, provider=ConversationProvider())
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(lambda: asyncio.run(first_worker.run_once()))
        time.sleep(2.2)
        assert asyncio.run(second_worker.run_once()) is False
        assert first.result(timeout=3) is True
    with make_session_factory(database_url)() as session:
        assert _queue_count(session) == 0
        assert session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"])).state == "completed"


@pytest.mark.integration
def test_job_queue_redelivery_does_not_invoke_a_second_live_worker_when_heartbeat_is_lost() -> None:
    database_url = _postgres_test_url()
    reset_database(database_url)
    client = _conversation_client(database_url)
    conversation = client.post(
        "/api/conversations",
        headers={"X-Demo-Persona": "mina"},
        json={"title": "실행 guard 대화"},
    ).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={"X-Demo-Persona": "mina", "Idempotency-Key": "lost-heartbeat"},
        json={"body": "실행 중인 응답", "context": []},
    )
    assert accepted.status_code == 202
    settings = Settings(
        RuntimeProfile.TEST,
        database_url,
        conversation_queue_visibility_timeout=1,
        job_queue_backend="postgres",
    )
    first_provider = ConversationProvider(delay_seconds=2)
    second_provider = ConversationProvider()
    first_worker = ConversationWorker(settings, provider=first_provider)
    second_worker = ConversationWorker(settings, provider=second_provider)

    async def no_heartbeat(message_id: str, lease_token: str) -> None:
        await asyncio.Event().wait()

    first_worker._heartbeat = no_heartbeat  # type: ignore[method-assign]
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(lambda: asyncio.run(first_worker.run_once()))
        assert first_provider.started.wait(timeout=2)
        time.sleep(1.15)
        # The lease expired, so the second worker reclaims the job (new fencing token) but loses the execution
        # guard: it releases the job without calling the provider.
        assert asyncio.run(second_worker.run_once()) is False
        assert second_provider.calls == 0
        assert first.result(timeout=3) is True
    with make_session_factory(database_url)() as session:
        turn = session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"]))
        assert turn is not None
        assert turn.state == "completed"
        # The first worker's transport write was fenced out (stale token); the domain turn is complete anyway.
        job = session.scalar(select(DurableJobRecord).where(DurableJobRecord.kind == JOB_KIND_CONVERSATION_TURN))
        assert job is not None and job.state == "queued" and job.attempt_count == 2
    # The next claim (after the 1s guard-conflict release) sees the terminal turn and finalizes the transport
    # row without another provider call; run_once reports False because no provider work happened.
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        asyncio.run(second_worker.run_once())
        with make_session_factory(database_url)() as session:
            if _queue_count(session) == 0:
                break
        time.sleep(0.1)
    assert second_provider.calls == 0
    with make_session_factory(database_url)() as session:
        assert _queue_count(session) == 0
        assert len(list(session.scalars(select(ToolInvocationRecord)))) == 1


@pytest.mark.integration
def test_job_queue_guard_conflicts_do_not_spend_provider_attempt_budget() -> None:
    database_url = _postgres_test_url()
    reset_database(database_url)
    client = _conversation_client(database_url)
    conversation = client.post(
        "/api/conversations",
        headers={"X-Demo-Persona": "mina"},
        json={"title": "guard conflict retry budget"},
    ).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={"X-Demo-Persona": "mina", "Idempotency-Key": "guard-conflict-budget"},
        json={"body": "provider 실패를 재시도합니다.", "context": []},
    )
    assert accepted.status_code == 202
    settings = Settings(
        RuntimeProfile.TEST,
        database_url,
        conversation_queue_visibility_timeout=1,
        conversation_queue_max_attempts=2,
        job_queue_backend="postgres",
    )
    first_provider = ConversationProvider(delay_seconds=3.5, failures=1)
    conflict_provider = ConversationProvider()
    retry_provider = ConversationProvider(failures=1)
    first_worker = ConversationWorker(settings, provider=first_provider)
    conflicting_worker = ConversationWorker(settings, provider=conflict_provider)
    retry_worker = ConversationWorker(settings, provider=retry_provider)

    async def no_heartbeat(message_id: str, lease_token: str) -> None:
        await asyncio.Event().wait()

    first_worker._heartbeat = no_heartbeat  # type: ignore[method-assign]
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(lambda: asyncio.run(first_worker.run_once()))
        if not first_provider.started.wait(timeout=2):
            first.result(timeout=1)
            raise AssertionError("first worker did not invoke the provider")
        # Each expired lease is reclaimed, but both reclaims lose the execution guard and must not call
        # the provider or spend a domain attempt; they hand the job back with a short delay.
        time.sleep(1.15)
        assert asyncio.run(conflicting_worker.run_once()) is False
        time.sleep(1.15)
        assert asyncio.run(conflicting_worker.run_once()) is False
        assert conflict_provider.calls == 0
        assert first.result(timeout=5) is True

    # The first actual call failed (its transport release was fenced out). The next claim is only the second
    # actual attempt despite the preceding redeliveries, so it becomes terminal.
    assert _drain(retry_worker) is True
    assert first_provider.calls == retry_provider.calls == 1
    with make_session_factory(database_url)() as session:
        turn = session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"]))
        assert turn is not None
        assert turn.execution_attempt_count == 2
        assert turn.state == "failed"
        assert _queue_count(session) == 0


@pytest.mark.integration
def test_job_queue_archives_after_the_configured_maximum_provider_attempts() -> None:
    database_url = _postgres_test_url()
    reset_database(database_url)
    client = _conversation_client(database_url)
    conversation = client.post(
        "/api/conversations",
        headers={"X-Demo-Persona": "mina"},
        json={"title": "실패 대화"},
    ).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={"X-Demo-Persona": "mina", "Idempotency-Key": "max-attempts"},
        json={"body": "실패합니다.", "context": []},
    )
    assert accepted.status_code == 202
    worker = ConversationWorker(
        Settings(
            RuntimeProfile.TEST,
            database_url,
            conversation_queue_visibility_timeout=1,
            conversation_queue_max_attempts=2,
            job_queue_backend="postgres",
        ),
        provider=ConversationProvider(failures=2),
    )
    assert asyncio.run(worker.run_once()) is True
    time.sleep(1.1)
    assert asyncio.run(worker.run_once()) is True
    with make_session_factory(database_url)() as session:
        assert _queue_count(session) == 0
        turn = session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"]))
        assert turn is not None
        assert turn.state == "failed"
        assert turn.execution_attempt_count == 2
        assert session.scalar(
            select(ConversationAuditEventRecord).where(
                ConversationAuditEventRecord.turn_id == turn.id
            )
        ) is not None


@pytest.mark.integration
def test_job_queue_fifo_drain_preserves_queued_fragments_for_one_conversation() -> None:
    database_url = _postgres_test_url()
    reset_database(database_url)
    client = _conversation_client(database_url)
    conversation = client.post(
        "/api/conversations",
        headers={"X-Demo-Persona": "mina"},
        json={"title": "FIFO 대화"},
    ).json()
    first = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={"X-Demo-Persona": "mina", "Idempotency-Key": "queued-message"},
        json={"body": "첫 발화", "context": []},
    )
    second = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={"X-Demo-Persona": "mina", "Idempotency-Key": "queued-message-2"},
        json={"body": "둘째 발화", "context": []},
    )
    assert first.status_code == second.status_code == 202
    assert second.json()["queued"] is True
    worker = ConversationWorker(
        Settings(RuntimeProfile.TEST, database_url, job_queue_backend="postgres"),
        provider=ConversationProvider(),
    )
    assert asyncio.run(worker.run_once()) is True
    session_factory = make_session_factory(database_url)
    with session_factory() as session:
        assert _queue_count(session) == 1
        messages = list(
            session.scalars(
                select(ConversationMessageRecord)
                .where(ConversationMessageRecord.conversation_id == UUID(conversation["conversation_id"]))
                .order_by(ConversationMessageRecord.sequence)
            )
        )
        assert [message.body for message in messages if message.role == "user"] == [
            "첫 발화",
            "둘째 발화",
        ]
    assert asyncio.run(worker.run_once()) is True
    with session_factory() as session:
        assert _queue_count(session) == 0
        assert [
            turn.state
            for turn in session.scalars(
                select(ConversationTurnRecord).order_by(ConversationTurnRecord.started_at)
            )
        ] == ["completed", "completed"]


@pytest.mark.integration
def test_job_queue_marks_inactive_owner_as_terminal_authorization_failure() -> None:
    database_url = _postgres_test_url()
    reset_database(database_url)
    client = _conversation_client(database_url)
    session_factory = make_session_factory(database_url)

    owner_lost = client.post(
        "/api/conversations",
        headers={"X-Demo-Persona": "mina"},
        json={"title": "비활성 owner"},
    ).json()
    owner_lost_message = client.post(
        f"/api/conversations/{owner_lost['conversation_id']}/messages",
        headers={"X-Demo-Persona": "mina", "Idempotency-Key": "owner-lost"},
        json={"body": "실행 전 owner가 비활성화됩니다.", "context": []},
    )
    assert owner_lost_message.status_code == 202
    with session_factory() as session:
        employment = session.scalar(
            select(EmploymentPeriodRecord).where(EmploymentPeriodRecord.member_id == "mina")
        )
        assert employment is not None
        employment.state = "inactive"
        session.commit()
    owner_provider = ConversationProvider()
    owner_worker = ConversationWorker(
        Settings(RuntimeProfile.TEST, database_url, job_queue_backend="postgres"),
        provider=owner_provider,
    )
    assert asyncio.run(owner_worker.run_once()) is False
    assert owner_provider.calls == 0
    with session_factory() as session:
        owner_turn = session.scalar(
            select(ConversationTurnRecord).where(
                ConversationTurnRecord.id == UUID(owner_lost_message.json()["turn_id"])
            )
        )
        assert owner_turn is not None
        assert owner_turn.state == "failed"
        assert session.scalar(
            select(ConversationAuditEventRecord).where(
                ConversationAuditEventRecord.turn_id == owner_turn.id
            )
        ) is not None
        assert _queue_count(session) == 0


@pytest.mark.integration
def test_job_queue_revalidates_a_stale_context_before_calling_the_provider() -> None:
    database_url = _postgres_test_url()
    reset_database(database_url)
    client = _conversation_client(database_url)
    task = client.post(
        "/api/tasks",
        headers={"X-Demo-Persona": "mina"},
        json={"title": "실행 전 변경되는 근거"},
    ).json()
    conversation = client.post(
        "/api/conversations",
        headers={"X-Demo-Persona": "mina"},
        json={"title": "stale context"},
    ).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={"X-Demo-Persona": "mina", "Idempotency-Key": "stale-context"},
        json={
            "body": "변경 전 근거를 사용합니다.",
            "context": [
                {
                    "resource_type": "task",
                    "resource_id": task["task_id"],
                    "resource_version": task["version"],
                    "included": True,
                }
            ],
        },
    )
    assert accepted.status_code == 202
    transitioned = client.post(
        f"/api/tasks/{task['task_id']}/start",
        headers={"X-Demo-Persona": "mina"},
        json={"expected_version": task["version"]},
    )
    assert transitioned.status_code == 200

    provider = ConversationProvider()
    worker = ConversationWorker(
        Settings(RuntimeProfile.TEST, database_url, job_queue_backend="postgres"),
        provider=provider,
    )
    assert asyncio.run(worker.run_once()) is False
    assert provider.calls == 0
    with make_session_factory(database_url)() as session:
        turn = session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"]))
        assert turn is not None
        assert turn.state == "failed"
        assert _queue_count(session) == 0


@pytest.mark.integration
def test_postgres_action_proposals_are_owner_bound_and_serialized_per_execution() -> None:
    database_url = _postgres_test_url()
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, job_queue_backend="postgres")
    client = _conversation_client(database_url)
    conversation = client.post(
        "/api/conversations",
        headers={"X-Demo-Persona": "mina"},
        json={"title": "Action ownership"},
    ).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={"X-Demo-Persona": "mina", "Idempotency-Key": "action-owner"},
        json={"body": "업무를 제안해줘", "context": []},
    ).json()
    with make_session_factory(database_url)() as session:
        turn = session.get(ConversationTurnRecord, UUID(accepted["turn_id"]))
        assert turn is not None
        execution_id = turn.execution_id

    application = create_workflow_application(settings, ConversationProvider())
    mina = application.authenticated_principal("mina")
    jiho = application.authenticated_principal("jiho")

    def propose(title: str) -> dict[str, object]:
        return application.propose_action(
            mina,
            execution_id,
            "task.create_self",
            "업무 생성 확인",
            {"title": title},
        )

    turn_lock_acquired = Event()
    release_first_transaction = Event()
    second_started = Event()
    second_finished = Event()
    blocked_first_lock = False
    engine = application._session_factory.kw["bind"]

    def hold_after_first_turn_lock(conn, cursor, statement, parameters, context, executemany):
        nonlocal blocked_first_lock
        if (
            not blocked_first_lock
            and "conversation_turns" in statement
            and "JOIN conversations" in statement
            and "FOR UPDATE" in statement
        ):
            blocked_first_lock = True
            turn_lock_acquired.set()
            assert release_first_transaction.wait(timeout=3), "test did not release the first Action proposal"

    event.listen(engine, "after_cursor_execute", hold_after_first_turn_lock)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(propose, "첫 번째 제안 업무")
            assert turn_lock_acquired.wait(timeout=3), "first proposal did not hold the ConversationTurn lock"

            def propose_second() -> dict[str, object]:
                second_started.set()
                try:
                    return propose("재실행에서 달라진 업무")
                finally:
                    second_finished.set()

            second_future = executor.submit(propose_second)
            assert second_started.wait(timeout=1), "second proposal did not enter the competing transaction"
            assert not second_finished.wait(timeout=0.25), "second proposal bypassed the locked turn"
            release_first_transaction.set()
            first = first_future.result(timeout=3)
            second = second_future.result(timeout=3)
    finally:
        event.remove(engine, "after_cursor_execute", hold_after_first_turn_lock)

    assert first["action_id"] == second["action_id"]
    with pytest.raises(ValueError, match="execution was not found"):
        application.propose_action(
            jiho,
            execution_id,
            "task.create_self",
            "권한 없는 업무 생성",
            {"title": "권한 없는 업무"},
        )
    with make_session_factory(database_url)() as session:
        assert session.query(ActionItemRecord).count() == 1



# ---- shared durable job transport: correctness is only claimed here, against real PostgreSQL ----


def _job(kind: str, ordering_key: str, idem: str) -> JobEnvelope:
    return JobEnvelope(kind=kind, ordering_key=ordering_key, idempotency_key=idem, payload={"k": idem})


@pytest.mark.integration
def test_job_queue_fifo_head_blocks_later_jobs_of_the_same_key_even_while_locked() -> None:
    database_url = _postgres_test_url()
    reset_database(database_url)
    sessions = make_session_factory(database_url)
    kind = "test.fifo"
    with sessions() as session:
        queue = SqlAlchemyDurableJobQueue(session)
        first = queue.enqueue(_job(kind, "A", "a-1"))
        queue.enqueue(_job(kind, "A", "a-2"))
        other = queue.enqueue(_job(kind, "B", "b-1"))
        session.commit()
    # Hold a row lock on the head of key A in another transaction without claiming it.
    holder = sessions()
    holder.execute(text("SELECT id FROM durable_jobs WHERE id = :id FOR UPDATE"), {"id": first})
    try:
        with sessions() as session:
            claimed = SqlAlchemyDurableJobQueue(session).claim(kind, limit=10, lease_seconds=30, worker_id="w1")
            session.commit()
        # SKIP LOCKED skips the locked head, and the second job of key A must NOT be claimed in its place.
        assert [job.job_id for job in claimed] == [other]
    finally:
        holder.rollback()
        holder.close()
    with sessions() as session:
        claimed = SqlAlchemyDurableJobQueue(session).claim(kind, limit=10, lease_seconds=30, worker_id="w2")
        session.commit()
    assert [job.job_id for job in claimed] == [first]
    # While a-1 is running (leased), a-2 is still blocked.
    with sessions() as session:
        assert SqlAlchemyDurableJobQueue(session).claim(kind, limit=10, lease_seconds=30, worker_id="w3") == []
        session.commit()


@pytest.mark.integration
def test_job_queue_concurrent_claimers_never_share_a_job() -> None:
    database_url = _postgres_test_url()
    reset_database(database_url)
    sessions = make_session_factory(database_url)
    kind = "test.concurrency"
    with sessions() as session:
        queue = SqlAlchemyDurableJobQueue(session)
        for index in range(40):
            queue.enqueue(_job(kind, f"key-{index}", f"idem-{index}"))
        session.commit()

    def claim_all(worker_id: str) -> list[str]:
        seen: list[str] = []
        while True:
            with sessions() as session:
                jobs = SqlAlchemyDurableJobQueue(session).claim(kind, limit=5, lease_seconds=30, worker_id=worker_id)
                session.commit()
            if not jobs:
                return seen
            seen.extend(str(job.job_id) for job in jobs)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = [future.result(timeout=20) for future in [executor.submit(claim_all, f"w{n}") for n in range(4)]]
    flat = [job_id for result in results for job_id in result]
    assert len(flat) == 40 and len(set(flat)) == 40


@pytest.mark.integration
def test_job_queue_lease_expiry_reclaims_and_fencing_rejects_the_stale_owner() -> None:
    database_url = _postgres_test_url()
    reset_database(database_url)
    sessions = make_session_factory(database_url)
    kind = "test.fencing"
    with sessions() as session:
        SqlAlchemyDurableJobQueue(session).enqueue(_job(kind, "K", "k-1"))
        session.commit()
    with sessions() as session:
        [first] = SqlAlchemyDurableJobQueue(session).claim(kind, limit=1, lease_seconds=1, worker_id="slow")
        session.commit()
    with sessions() as session:
        assert SqlAlchemyDurableJobQueue(session).claim(kind, limit=1, lease_seconds=1, worker_id="eager") == []
        session.commit()
    time.sleep(1.1)
    with sessions() as session:
        [second] = SqlAlchemyDurableJobQueue(session).claim(kind, limit=1, lease_seconds=30, worker_id="eager")
        session.commit()
    assert second.job_id == first.job_id and second.attempt == 2 and second.lease_token != first.lease_token
    with sessions() as session:
        queue = SqlAlchemyDurableJobQueue(session)
        # The slow worker's writes are all rejected: it cannot heartbeat, complete, fail, or release someone else's lease.
        assert queue.extend_lease(first.job_id, first.lease_token, 30) is False
        assert queue.complete(first.job_id, first.lease_token) is False
        assert queue.fail(first.job_id, first.lease_token, error="stale") is False
        assert queue.release(first.job_id, first.lease_token, delay_seconds=0) is False
        assert queue.complete(second.job_id, second.lease_token) is True
        session.commit()
    with sessions() as session:
        job = session.scalar(select(DurableJobRecord).where(DurableJobRecord.id == first.job_id))
        assert job is not None and job.state == "completed" and job.attempt_count == 2
        # Terminal rows accept no further transport writes.
        assert SqlAlchemyDurableJobQueue(session).complete(second.job_id, second.lease_token) is False


@pytest.mark.integration
def test_job_queue_idempotency_key_allows_one_active_job_and_release_delays_visibility() -> None:
    database_url = _postgres_test_url()
    reset_database(database_url)
    sessions = make_session_factory(database_url)
    kind = "test.idempotency"
    with sessions() as session:
        queue = SqlAlchemyDurableJobQueue(session)
        first = queue.enqueue(_job(kind, "K", "same"))
        assert queue.enqueue(_job(kind, "K", "same")) == first
        session.commit()
        assert session.scalar(text("SELECT count(*) FROM durable_jobs WHERE kind = :kind"), {"kind": kind}) == 1
    with sessions() as session:
        [claimed] = SqlAlchemyDurableJobQueue(session).claim(kind, limit=1, lease_seconds=30, worker_id="w")
        assert SqlAlchemyDurableJobQueue(session).release(claimed.job_id, claimed.lease_token, delay_seconds=2, error="try later") is True
        session.commit()
    with sessions() as session:
        assert SqlAlchemyDurableJobQueue(session).claim(kind, limit=1, lease_seconds=30, worker_id="w") == []
        # Still active (queued with a future available_at): the same idempotency key is not duplicated.
        assert SqlAlchemyDurableJobQueue(session).enqueue(_job(kind, "K", "same")) == first
        session.commit()
    time.sleep(2.1)
    with sessions() as session:
        [again] = SqlAlchemyDurableJobQueue(session).claim(kind, limit=1, lease_seconds=30, worker_id="w")
        assert again.attempt == 2
        assert SqlAlchemyDurableJobQueue(session).complete(again.job_id, again.lease_token) is True
        session.commit()
    with sessions() as session:
        # Once terminal, the key may be used again by a new job.
        assert SqlAlchemyDurableJobQueue(session).enqueue(_job(kind, "K", "same")) != first
        session.commit()


@pytest.mark.integration
def test_job_queue_concurrent_enqueues_with_one_active_key_return_the_same_job_without_failing() -> None:
    database_url = _postgres_test_url()
    reset_database(database_url)
    sessions = make_session_factory(database_url)
    kind = "test.enqueue-race"
    gate = Event()

    def enqueue(worker: int) -> str:
        with sessions() as session:
            queue = SqlAlchemyDurableJobQueue(session)
            gate.wait(timeout=5)
            job_id = queue.enqueue(_job(kind, "K", "same-key"))
            # Something else in the caller's transaction must still succeed: the transaction is not poisoned.
            session.execute(text("SELECT 1"))
            session.commit()
            return str(job_id)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(enqueue, n) for n in range(8)]
        time.sleep(0.2)
        gate.set()
        ids = {future.result(timeout=20) for future in futures}
    assert len(ids) == 1
    with sessions() as session:
        assert session.scalar(text("SELECT count(*) FROM durable_jobs WHERE kind = :kind"), {"kind": kind}) == 1
        [claimed] = SqlAlchemyDurableJobQueue(session).claim(kind, limit=5, lease_seconds=30, worker_id="w")
        assert SqlAlchemyDurableJobQueue(session).complete(claimed.job_id, claimed.lease_token) is True
        session.commit()
    with sessions() as session:
        row = session.scalar(select(DurableJobRecord).where(DurableJobRecord.id == claimed.job_id))
        assert row is not None and row.state == "completed" and row.lease_token is None and row.leased_by is None


@pytest.mark.integration
def test_material_upload_enqueues_in_the_same_transaction_and_the_worker_indexes_once(tmp_path) -> None:
    from ax_workspace.bootstrap.material_worker import MaterialExtractionWorker
    from ax_workspace.platform.persistence import MaterialChunkRecord, MaterialExtractionRecord

    database_url = _postgres_test_url()
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, job_queue_backend="postgres", materials_dir=str(tmp_path / "materials"), material_queue_visibility_timeout=1)
    client = TestClient(create_app(settings))
    task = client.post("/api/tasks", headers={"X-Demo-Persona": "mina"}, json={"title": "PG 자료"}).json()
    body = ("납기일은 2026-09-30입니다. 공급사는 한빛상사입니다. " * 30).encode()
    uploaded = client.post(
        f"/api/tasks/{task['task_id']}/materials", headers={"X-Demo-Persona": "mina"}, data={"kind": "input"}, files={"file": ("견적.md", body, "text/markdown")}
    )
    assert uploaded.status_code == 201, uploaded.text
    sessions = make_session_factory(database_url)
    with sessions() as session:
        assert _queue_count(session, JOB_KIND_MATERIAL_EXTRACTION) == 1
        extraction = session.scalar(select(MaterialExtractionRecord))
        assert extraction is not None and extraction.status == "queued"

    worker = MaterialExtractionWorker(settings)
    assert asyncio.run(worker.run_once()) is True
    with sessions() as session:
        extraction = session.scalar(select(MaterialExtractionRecord))
        assert extraction.status == "completed" and extraction.chunk_count >= 1
        chunk_count = len(session.scalars(select(MaterialChunkRecord)).all())
        assert _queue_count(session, JOB_KIND_MATERIAL_EXTRACTION) == 0
        # A duplicate delivery of the same extraction (at-least-once) is consumed without re-indexing.
        SqlAlchemyDurableJobQueue(session).enqueue(
            JobEnvelope(JOB_KIND_MATERIAL_EXTRACTION, str(extraction.id), f"{JOB_KIND_MATERIAL_EXTRACTION}:{extraction.id}", {"extraction_id": str(extraction.id), "attachment_id": str(extraction.attachment_id)})
        )
        session.commit()
    assert asyncio.run(worker.run_once()) is True
    with sessions() as session:
        assert len(session.scalars(select(MaterialChunkRecord)).all()) == chunk_count
        assert _queue_count(session, JOB_KIND_MATERIAL_EXTRACTION) == 0
    search = client.get(f"/api/tasks/{task['task_id']}/materials/search", headers={"X-Demo-Persona": "mina"}, params={"q": "공급사"}).json()
    assert {hit["name"] for hit in search["results"]} == {"견적.md"}


@pytest.mark.integration
def test_job_queue_expired_lease_is_fenced_out_even_before_reclaim() -> None:
    database_url = _postgres_test_url()
    reset_database(database_url)
    sessions = make_session_factory(database_url)
    kind = "test.expired-fence"
    with sessions() as session:
        SqlAlchemyDurableJobQueue(session).enqueue(_job(kind, "K", "k-1"))
        session.commit()
    with sessions() as session:
        [claimed] = SqlAlchemyDurableJobQueue(session).claim(kind, limit=1, lease_seconds=1, worker_id="slow")
        session.commit()
    time.sleep(1.1)
    with sessions() as session:
        queue = SqlAlchemyDurableJobQueue(session)
        assert queue.extend_lease(claimed.job_id, claimed.lease_token, 30) is False
        assert queue.complete(claimed.job_id, claimed.lease_token) is False
        assert queue.fail(claimed.job_id, claimed.lease_token, error="late") is False
        assert queue.release(claimed.job_id, claimed.lease_token, delay_seconds=0) is False
        session.commit()
    with sessions() as session:
        job = session.scalar(select(DurableJobRecord).where(DurableJobRecord.id == claimed.job_id))
        assert job.state == "running" and job.attempt_count == 1  # left for a fresh claim
        [again] = SqlAlchemyDurableJobQueue(session).claim(kind, limit=1, lease_seconds=30, worker_id="eager")
        assert again.attempt == 2 and SqlAlchemyDurableJobQueue(session).complete(again.job_id, again.lease_token) is True
        session.commit()


@pytest.mark.integration
def test_conversation_cancel_stops_the_provider_and_late_events_are_ignored() -> None:
    """DB-first cancel: the API marks the turn cancelled, the worker's watcher sets the cancel token, the provider stops,
    partial text stays with body_state=cancelled, and a late provider event cannot reopen or overwrite the turn."""
    from datetime import UTC, datetime

    from ax_workspace.modules.ax_execution.ai import AiProviderEvent, ProviderCancelled
    from ax_workspace.platform.conversations import SqlAlchemyConversationRepository
    from ax_workspace.platform.persistence import ConversationTurnRecord

    database_url = _postgres_test_url()
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, job_queue_backend="postgres", conversation_queue_visibility_timeout=4)
    client = TestClient(create_app(settings))
    release = Event()
    observed_cancel = Event()

    class BlockingProvider:
        def converse(self, request, *, sink=None, cancel=None):
            now = datetime.now(UTC)
            sink.accept(AiProviderEvent("turn_started", now, provider_run_ref="run-c"))
            sink.accept(AiProviderEvent("item_completed", now, item_id="m1", item_type="agent_message", text="부분 답변입니다."))
            # Behave like the real adapter: keep running until the cancel token is set (or the test releases us).
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not release.is_set():
                if cancel is not None and cancel.is_set():
                    observed_cancel.set()
                    raise ProviderCancelled("cancelled by user")
                time.sleep(0.1)
            raise AssertionError("provider was never cancelled")

    conversation = client.post("/api/conversations", headers={"X-Demo-Persona": "mina"}, json={"title": "취소 대화"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={"X-Demo-Persona": "mina", "Idempotency-Key": "cancel-me"},
        json={"body": "오래 걸리는 질문", "context": []},
    )
    assert accepted.status_code == 202
    worker = ConversationWorker(settings, provider=BlockingProvider())
    with ThreadPoolExecutor(max_workers=1) as executor:
        running = executor.submit(lambda: asyncio.run(worker.run_once()))
        # Partial text and the live progress are visible through the same hydrate query while the turn runs.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            view = client.get(f"/api/conversations/{conversation['conversation_id']}", headers={"X-Demo-Persona": "mina"}).json()
            assistant = [m for m in view["messages"] if m["role"] == "assistant"]
            if assistant and assistant[0]["body"] == "부분 답변입니다." and assistant[0]["body_state"] == "streaming":
                break
            time.sleep(0.1)
        else:
            release.set()
            raise AssertionError("partial assistant text was not persisted while running")
        assert view["turns"][0]["progress_state"] in {"preparing", "composing"} and view["turns"][0]["execution_started_at"]
        cancelled = client.post(
            f"/api/conversations/{conversation['conversation_id']}/cancel", headers={"X-Demo-Persona": "mina"}, json={"expected_version": view["version"]}
        )
        assert cancelled.status_code == 200, cancelled.text
        assert observed_cancel.wait(timeout=8), "the worker did not propagate the cancellation to the provider"
        assert running.result(timeout=10) is True
    view = client.get(f"/api/conversations/{conversation['conversation_id']}", headers={"X-Demo-Persona": "mina"}).json()
    [turn] = view["turns"]
    assert turn["state"] == "cancelled" and turn["progress_state"] == "cancelled" and turn["error"] is None
    assistant = [m for m in view["messages"] if m["role"] == "assistant"][0]
    assert assistant["body"] == "부분 답변입니다." and assistant["body_state"] == "cancelled"
    # A late event for the terminal turn is ignored.
    with make_session_factory(database_url)() as session:
        record = session.get(ConversationTurnRecord, UUID(turn["turn_id"]))
        execution = ConversationExecution(record.id, record.conversation_id, record.execution_id)
        late = AiProviderEvent("item_completed", datetime.now(UTC), item_id="late", item_type="agent_message", text="늦게 도착한 본문")
        assert SqlAlchemyConversationRepository(session, SqlAlchemyDurableJobQueue(session)).apply_event(execution, late) is False
        session.commit()
    view = client.get(f"/api/conversations/{conversation['conversation_id']}", headers={"X-Demo-Persona": "mina"}).json()
    assert [m["body"] for m in view["messages"] if m["role"] == "assistant"] == ["부분 답변입니다."]
    assert view["turns"][0]["state"] == "cancelled"
    with make_session_factory(database_url)() as session:
        assert _queue_count(session) == 0


@pytest.mark.integration
def test_postgres_serializes_two_simultaneous_comment_posts_of_the_same_key_into_one_row() -> None:
    """The real double-submit: two requests in flight at once, which SQLite's no-op row lock cannot exercise."""
    database_url = _postgres_test_url()
    reset_database(database_url)
    client = _conversation_client(database_url)
    request = client.post(
        "/api/work-requests",
        headers={"X-Demo-Persona": "mina"},
        json={"title": "동시 논의", "assignee_id": "jiho"},
    ).json()
    url = f"/api/work-requests/{request['request_id']}/comments"
    headers = {"X-Demo-Persona": "mina", "Idempotency-Key": "double-submit"}
    barrier = Barrier(2)
    results: list[tuple[int, str | None]] = []
    lock = Lock()

    def post() -> None:
        barrier.wait(timeout=10)
        response = client.post(url, headers=headers, json={"body": "논의 추가"})
        with lock:
            results.append((response.status_code, response.json().get("comment_id") if response.status_code == 201 else None))

    threads = [Thread(target=post) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert [status for status, _ in results] == [201, 201], results
    assert len({comment_id for _, comment_id in results}) == 1, results
    with make_session_factory(database_url)() as session:
        stored = session.execute(text("SELECT count(*) FROM comments WHERE body = '논의 추가'")).scalar_one()
    assert int(stored) == 1
    timeline = client.get(f"/api/work-requests/{request['request_id']}/timeline", headers={"X-Demo-Persona": "mina"}).json()
    assert [item["body"] for item in timeline["comments"]] == ["논의 추가"]


@pytest.mark.integration
def test_postgres_serializes_two_simultaneous_judgements_into_one_effect() -> None:
    """Two people (or two tabs) answering the same question at once must produce one Task and one decision."""
    database_url = _postgres_test_url()
    reset_database(database_url)
    client = _conversation_client(database_url)
    client.post("/api/work-requests", headers={"X-Demo-Persona": "mina"}, json={"title": "동시 판단 요청", "assignee_id": "jiho"})
    [item] = client.get("/api/action-items", headers={"X-Demo-Persona": "jiho"}).json()
    url = f"/api/action-items/{item['action_item_id']}/commands/accept"
    body = {"expected_version": item["expected_version"]}
    barrier = Barrier(2)
    results: list[tuple[int, str]] = []
    lock = Lock()

    def accept() -> None:
        barrier.wait(timeout=10)
        response = client.post(url, headers={"X-Demo-Persona": "jiho"}, json=body)
        with lock:
            results.append((response.status_code, response.text[:200]))

    threads = [Thread(target=accept) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert sorted(status for status, _ in results) == [200, 422], results  # one answer, one stale-version refusal
    with make_session_factory(database_url)() as session:
        assert int(session.execute(text("SELECT count(*) FROM tasks WHERE title = '동시 판단 요청'")).scalar_one()) == 1
        assert int(session.execute(text("SELECT count(*) FROM review_decisions")).scalar_one()) == 1
    assert client.get("/api/action-items", headers={"X-Demo-Persona": "jiho"}).json() == []


@pytest.mark.integration
def test_postgres_keeps_every_earlier_round_byte_identical_after_a_revision() -> None:
    """A revision adds a round. It never edits the content, the hash, or the decision of an earlier one."""
    database_url = _postgres_test_url()
    reset_database(database_url)
    client = _conversation_client(database_url)
    mina = {"X-Demo-Persona": "mina"}
    jiho = {"X-Demo-Persona": "jiho"}
    client.post("/api/work-requests", headers=mina, json={"title": "원래 제목", "assignee_id": "jiho", "description": "원래 설명"})
    [item] = client.get("/api/action-items", headers=jiho).json()
    action_item_id = item["action_item_id"]

    client.post(
        f"/api/action-items/{action_item_id}/commands/adjust",
        headers=jiho,
        json={"expected_version": item["expected_version"], "reason": "기한을 늦춰 주세요"},
    )
    first_round = client.get(f"/api/action-items/{action_item_id}", headers=mina).json()["rounds"][0]

    [waiting] = client.get("/api/action-items", headers=mina).json()
    revised = client.post(
        f"/api/action-items/{action_item_id}/commands/revise",
        headers=mina,
        json={"expected_version": waiting["expected_version"], "changes": {"title": "고친 제목", "description": "고친 설명"}},
    )
    assert revised.status_code == 200, revised.text

    rounds = client.get(f"/api/action-items/{action_item_id}", headers=mina).json()["rounds"]
    assert [row["submission_version"] for row in rounds] == [1, 2]
    # The stored first round is exactly what it was before the revision existed.
    assert rounds[0] == first_round
    assert rounds[0]["snapshot"]["title"] == "원래 제목" and rounds[0]["content_hash"] != rounds[1]["content_hash"]
    assert rounds[1]["diff"]["description"] == {"before": "원래 설명", "after": "고친 설명"}

    # One ActionItem, two immutable submissions, one decision so far; the reviewer owes the new round.
    with make_session_factory(database_url)() as session:
        assert int(session.execute(text("SELECT count(*) FROM decision_items")).scalar_one()) == 1
        assert int(session.execute(text("SELECT count(*) FROM submissions")).scalar_one()) == 2
        assert int(session.execute(text("SELECT count(*) FROM review_decisions")).scalar_one()) == 1
        active = session.execute(text("SELECT count(*) FROM review_assignments WHERE status = 'pending'")).scalar_one()
        assert int(active) == 1
    [current] = client.get("/api/action-items", headers=jiho).json()
    assert current["action_item_id"] == action_item_id and current["submission_version"] == 2


@pytest.mark.integration
def test_postgres_serializes_two_simultaneous_ax_approvals_into_one_effect() -> None:
    """The same lost-update guard on the AX path: two approvals in flight produce one Task and one approval."""
    database_url = _postgres_test_url()
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, job_queue_backend="postgres")
    client = _conversation_client(database_url)
    jiho = {"X-Demo-Persona": "jiho"}
    conversation = client.post("/api/conversations", headers=jiho, json={"title": "동시 승인"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**jiho, "Idempotency-Key": "concurrent-approve"},
        json={"body": "제안해줘", "context": []},
    ).json()
    with make_session_factory(database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id
    application = create_workflow_application(settings, ConversationProvider())
    application.propose_action(
        application.authenticated_principal("jiho"), execution_id, "task.create_self", "업무 생성 확인", {"title": "동시 승인 업무"}
    )
    [item] = [row for row in client.get("/api/action-items", headers=jiho).json() if row["kind"] == "ax.task.create_self"]
    url = f"/api/action-items/{item['action_item_id']}/commands/approve"
    body = {"expected_version": item["expected_version"]}
    barrier = Barrier(2)
    results: list[int] = []
    lock = Lock()

    def approve() -> None:
        barrier.wait(timeout=10)
        response = client.post(url, headers=jiho, json=body)
        with lock:
            results.append(response.status_code)

    threads = [Thread(target=approve) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    # Both callers get an answer; only one effect exists. The persisted Action is the idempotency boundary.
    assert results == [200, 200], results
    with make_session_factory(database_url)() as session:
        assert int(session.execute(text("SELECT count(*) FROM tasks WHERE title = '동시 승인 업무'")).scalar_one()) == 1
    assert client.get("/api/actions", headers=jiho).json()[0]["state"] == "approved"
    assert [row for row in client.get("/api/action-items", headers=jiho).json() if row["kind"] == "ax.task.create_self"] == []


@pytest.mark.integration
def test_postgres_rejects_a_task_whose_source_does_not_exist() -> None:
    """A source reference is a foreign key, not a loose id. The database refuses a link that points nowhere."""
    database_url = _postgres_test_url()
    reset_database(database_url)
    factory = make_session_factory(database_url)
    columns = {
        "source_work_request_id": "work_requests",
        "source_decision_item_id": "decision_items",
        "source_submission_id": "submissions",
        "source_review_decision_id": "review_decisions",
        "source_action_item_id": "action_items",
        "source_task_id": "tasks",
    }
    for column, target in columns.items():
        with factory() as session:
            with pytest.raises(Exception) as raised:
                session.execute(
                    text(
                        "INSERT INTO tasks (id, owner_id, title, state, origin_kind, visibility, version, created_at, updated_at, "
                        f"{column}) VALUES (gen_random_uuid(), 'mina', 'dangling', 'open', 'direct', 'scope_default', 1, now(), now(), "
                        "gen_random_uuid())"
                    )
                )
                session.commit()
            assert "foreign key" in str(raised.value).lower(), f"{column} does not reference {target}"

    # The same guard on the assignment ledger.
    with factory() as session:
        with pytest.raises(Exception) as raised:
            session.execute(
                text(
                    "INSERT INTO task_assignments (id, task_id, assignee_id, assigned_by, assignment_kind, status, created_at, "
                    "source_work_request_id) VALUES (gen_random_uuid(), gen_random_uuid(), 'mina', 'jiho', 'direct', 'pending', now(), gen_random_uuid())"
                )
            )
            session.commit()
        assert "foreign key" in str(raised.value).lower()


@pytest.mark.integration
def test_postgres_serializes_adopting_evidence_against_deciding_on_it() -> None:
    """Whoever wins the row, the basis a decision froze is exactly the basis that round was standing on."""
    database_url = _postgres_test_url()
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=tempfile.mkdtemp())
    client = TestClient(create_app(settings))
    mina = {"X-Demo-Persona": "mina"}
    jiho = {"X-Demo-Persona": "jiho"}

    for attempt, head_start in enumerate((0.0, 0.05)):
        request = client.post("/api/work-requests", headers=mina, json={"title": f"경합 {attempt}", "assignee_id": "jiho"}).json()
        item = [row for row in client.get("/api/action-items", headers=jiho).json() if row["resource"]["id"] == request["request_id"]][0]
        gate = Barrier(2)

        def adopt() -> Any:
            gate.wait()
            time.sleep(head_start)
            return client.post(
                f"/api/work-requests/{request['request_id']}/evidence",
                headers=mina,
                files={"file": (f"근거{attempt}.txt", b"race", "text/plain")},
            )

        def decide() -> Any:
            gate.wait()
            return client.post(
                f"/api/action-items/{item['action_item_id']}/commands/accept",
                headers=jiho,
                json={"expected_version": item["expected_version"]},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            adopted, decided = executor.submit(adopt), executor.submit(decide)
            adoption, decision = adopted.result(), decided.result()

        timeline = client.get(f"/api/work-requests/{request['request_id']}/timeline", headers=jiho).json()
        [round_one] = timeline["submissions"]
        if adoption.status_code == 201:
            # The adoption landed first, so the reviewer's version was stale and nothing was decided on the old basis.
            assert decision.status_code == 422, decision.text
            assert len(round_one["evidence"]) == 1 and timeline["review_decisions"] == []
        else:
            # The decision landed first, so the round was closed to new evidence.
            assert adoption.status_code == 422 and decision.status_code == 200
            assert len(round_one["evidence"]) == 0
            [frozen] = timeline["review_decisions"]
            assert frozen["evidence_hash"] == round_one["evidence_hash"]
