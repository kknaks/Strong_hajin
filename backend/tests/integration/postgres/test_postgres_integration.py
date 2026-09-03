import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
import time
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from ax_workspace.modules.organization_access.domain import seeded_principal
from ax_workspace.platform.persistence import make_session_factory
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.ax_execution.application import InvalidDecision
from ax_workspace.platform.workflow_runtime import LocalDemoToolDispatcher, SqlAlchemyUnitOfWork, workflow_service
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.bootstrap.application import create_workflow_application
from ax_workspace.entrypoints.conversation_worker import ConversationWorker
from ax_workspace.entrypoints.http import create_app
from ax_workspace.modules.ax_execution.ai import (
    AiConversationResult,
    AiGeneration,
    AiToolInvocation,
    ProviderRequestFailed,
)
from ax_workspace.modules.ax_execution.conversations import ConversationExecution
from ax_workspace.platform.conversation_queue import CONVERSATION_EXECUTION_QUEUE, PgmqConversationTurnQueue
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

    def converse(self, request) -> AiConversationResult:
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

    def __init__(self) -> None:
        self.calls = 0
        self.started = Event()
        self._lock = Lock()

    def generate(self, request) -> AiGeneration:
        with self._lock:
            self.calls += 1
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

    def converse(self, request):  # pragma: no cover - this double is report-only
        raise AssertionError("report provider must not service conversation execution")


def _conversation_client(database_url: str) -> TestClient:
    return TestClient(
        create_app(
            Settings(
                RuntimeProfile.TEST,
                database_url,
                conversation_queue_backend="pgmq",
            )
        )
    )


def _queue_count(session) -> int:
    return int(
        session.execute(
            text(f"SELECT count(*) FROM pgmq.q_{CONVERSATION_EXECUTION_QUEUE}")
        ).scalar_one()
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
    settings = Settings(RuntimeProfile.TEST, database_url, conversation_queue_backend="pgmq")
    provider = ConcurrentReportProvider()
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
    assert first_result["report_id"] == second_result["report_id"]
    assert first_result["draft_id"] == second_result["draft_id"]
    assert first_result["workflow_run_id"] == second_result["workflow_run_id"]


@pytest.mark.integration
def test_pgmq_enqueue_and_domain_turn_commit_atomically_then_parallel_groups_archive() -> None:
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
            conversation_queue_backend="pgmq",
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
def test_pgmq_enqueue_failure_rolls_back_the_domain_message_and_turn(
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

    def reject_enqueue(self, execution) -> None:
        raise RuntimeError("PGMQ enqueue unavailable")

    monkeypatch.setattr(PgmqConversationTurnQueue, "enqueue", reject_enqueue)
    with pytest.raises(RuntimeError, match="PGMQ enqueue unavailable"):
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
def test_pgmq_retries_visibility_timeout_and_ignores_a_duplicate_terminal_turn() -> None:
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
            conversation_queue_backend="pgmq",
        ),
        provider=retry_provider,
    )
    assert asyncio.run(retry_worker.run_once()) is True
    with session_factory() as session:
        assert _queue_count(session) == 1
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
        PgmqConversationTurnQueue(session).enqueue(
            ConversationExecution(turn.id, turn.conversation_id, turn.execution_id)
        )
        session.commit()
    assert asyncio.run(retry_worker.run_once()) is False
    assert retry_provider.calls == 2
    with session_factory() as session:
        assert len(list(session.scalars(select(ToolInvocationRecord)))) == 1


@pytest.mark.integration
def test_pgmq_heartbeat_keeps_a_slow_turn_invisible_to_a_second_worker() -> None:
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
        conversation_queue_backend="pgmq",
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
def test_pgmq_redelivery_does_not_invoke_a_second_live_worker_when_heartbeat_is_lost() -> None:
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
        conversation_queue_backend="pgmq",
    )
    first_provider = ConversationProvider(delay_seconds=2)
    second_provider = ConversationProvider()
    first_worker = ConversationWorker(settings, provider=first_provider)
    second_worker = ConversationWorker(settings, provider=second_provider)

    async def no_heartbeat(message_id: int) -> None:
        await asyncio.Event().wait()

    first_worker._heartbeat = no_heartbeat  # type: ignore[method-assign]
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(lambda: asyncio.run(first_worker.run_once()))
        assert first_provider.started.wait(timeout=2)
        time.sleep(1.15)
        assert asyncio.run(second_worker.run_once()) is False
        assert second_provider.calls == 0
        assert first.result(timeout=3) is True
    with make_session_factory(database_url)() as session:
        turn = session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"]))
        assert turn is not None
        assert turn.state == "completed"
        assert _queue_count(session) == 0


@pytest.mark.integration
def test_pgmq_guard_conflicts_do_not_spend_provider_attempt_budget() -> None:
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
        conversation_queue_backend="pgmq",
    )
    first_provider = ConversationProvider(delay_seconds=3.5, failures=1)
    conflict_provider = ConversationProvider()
    retry_provider = ConversationProvider(failures=1)
    first_worker = ConversationWorker(settings, provider=first_provider)
    conflicting_worker = ConversationWorker(settings, provider=conflict_provider)
    retry_worker = ConversationWorker(settings, provider=retry_provider)

    async def no_heartbeat(message_id: int) -> None:
        await asyncio.Event().wait()

    first_worker._heartbeat = no_heartbeat  # type: ignore[method-assign]
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(lambda: asyncio.run(first_worker.run_once()))
        if not first_provider.started.wait(timeout=2):
            first.result(timeout=1)
            raise AssertionError("first worker did not invoke the provider")
        # Each visible redelivery is consumed by PGMQ, but both lose the
        # execution guard and must not call the provider or use an attempt.
        time.sleep(1.15)
        assert asyncio.run(conflicting_worker.run_once()) is False
        time.sleep(1.15)
        assert asyncio.run(conflicting_worker.run_once()) is False
        assert conflict_provider.calls == 0
        assert first.result(timeout=5) is True

    # The first actual call failed. The next claim is only the second actual
    # attempt despite the preceding PGMQ redeliveries, so it becomes terminal.
    assert asyncio.run(retry_worker.run_once()) is True
    assert first_provider.calls == retry_provider.calls == 1
    with make_session_factory(database_url)() as session:
        turn = session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"]))
        assert turn is not None
        assert turn.execution_attempt_count == 2
        assert turn.state == "failed"
        assert _queue_count(session) == 0


@pytest.mark.integration
def test_pgmq_archives_after_the_configured_maximum_provider_attempts() -> None:
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
            conversation_queue_backend="pgmq",
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
def test_pgmq_fifo_drain_preserves_queued_fragments_for_one_conversation() -> None:
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
        Settings(RuntimeProfile.TEST, database_url, conversation_queue_backend="pgmq"),
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
def test_pgmq_marks_inactive_owner_as_terminal_authorization_failure() -> None:
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
        Settings(RuntimeProfile.TEST, database_url, conversation_queue_backend="pgmq"),
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
def test_pgmq_revalidates_a_stale_context_before_calling_the_provider() -> None:
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
        Settings(RuntimeProfile.TEST, database_url, conversation_queue_backend="pgmq"),
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
    settings = Settings(RuntimeProfile.TEST, database_url, conversation_queue_backend="pgmq")
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

    def propose() -> dict[str, object]:
        return application.propose_action(
            mina,
            execution_id,
            "task.create_self",
            "업무 생성 확인",
            {"title": "동시 제안 업무"},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = [future.result(timeout=3) for future in [executor.submit(propose), executor.submit(propose)]]
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



@pytest.mark.integration
def test_postgres_serializes_competing_human_decisions_before_effect_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.getenv("AX_POSTGRES_TEST_URL")
    if not database_url:
        pytest.skip("Set AX_POSTGRES_TEST_URL to run against a disposable PostgreSQL database")
    reset_database(database_url, technical_spike=True)
    session_factory = make_session_factory(database_url)
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert uow.workflows is not None
        started = workflow_service(uow.workflows).start("daily-report", seeded_principal("mina"), {})

    effect_started = Event()
    release_effect = Event()
    rejection_finished = Event()
    original_dispatch = LocalDemoToolDispatcher.dispatch

    def block_first_effect(self, run, node, execution):
        if run.id.hex == started["run_id"].replace("-", "") and node.id == "effect" and not effect_started.is_set():
            effect_started.set()
            assert release_effect.wait(timeout=3), "test did not release the pending effect"
        return original_dispatch(self, run, node, execution)

    monkeypatch.setattr(LocalDemoToolDispatcher, "dispatch", block_first_effect)

    def decide(decision: str) -> str:
        try:
            with SqlAlchemyUnitOfWork(session_factory) as uow:
                assert uow.workflows is not None
                result = workflow_service(uow.workflows).decide(
                    UUID(started["run_id"]), "confirm", seeded_principal("mina"), decision, None, {}
                )
                return str(result["state"])
        except InvalidDecision:
            return "conflict"
        finally:
            if decision == "reject":
                rejection_finished.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        accepting = executor.submit(decide, "accept")
        assert effect_started.wait(timeout=3), "accepting decision did not reach its effect"
        rejecting = executor.submit(decide, "reject")
        assert not rejection_finished.wait(timeout=0.25), "rejection bypassed the in-flight decision lock"
        release_effect.set()
        assert accepting.result(timeout=3) == "completed"
        assert rejecting.result(timeout=3) == "conflict"
