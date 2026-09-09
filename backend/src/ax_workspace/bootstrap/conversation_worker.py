"""Separate process that executes AX conversation turns delivered by the durable job transport."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import logging
import threading
from typing import Any, Callable
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

from ax_workspace.bootstrap.settings import Settings

from ax_workspace.modules.ax_execution.ai import AiProvider, ProviderFailure
from ax_workspace.modules.ax_execution.conversations import (
    ConversationExecution,
    ConversationExecutionQueue,
    ConversationQueueMessage,
)
from ax_workspace.modules.organization_access.application import OrganizationApplication
from ax_workspace.bootstrap.application import create_codex_cli_provider, create_workflow_application
from ax_workspace.platform.conversation_jobs import ConversationJobQueue
from ax_workspace.platform.durable_jobs import MemoryDurableJobQueue, build_job_queue
from ax_workspace.platform.conversations import (
    SqlAlchemyConversationExecutionGuard,
    SqlAlchemyConversationRepository,
)
from ax_workspace.platform.organization_access import SqlAlchemyOrganizationRepository
from ax_workspace.platform.persistence import make_session_factory


@dataclass(frozen=True, slots=True)
class ClaimedTurn:
    message_id: str
    lease_token: str
    read_count: int
    turn_id: UUID
    execution: ConversationExecution
    request: Any

GUARD_CONFLICT_RELEASE_SECONDS = 1
FAILURE_BACKOFF_SECONDS = 2.0
logger = logging.getLogger(__name__)


class ConversationWorker:
    """Claims at most one durable job per Conversation (the earliest non-terminal head) at a time."""

    def __init__(
        self,
        settings: Settings,
        *,
        provider: AiProvider | None = None,
        queue_factory: Callable[[Any], ConversationExecutionQueue] | None = None,
    ) -> None:
        self._settings = settings
        self._sessions = make_session_factory(settings.database_url)
        self._execution_guard = SqlAlchemyConversationExecutionGuard(self._sessions.kw["bind"])
        self._provider = provider or create_codex_cli_provider(settings)
        # Rebuilding the turn's context from the canonical conversation, not from the provider's memory.
        self._application = create_workflow_application(settings)
        self._worker_id = f"conversation-worker:{uuid4().hex[:12]}"
        if queue_factory is not None:
            self._queue_factory = queue_factory
        else:
            memory = MemoryDurableJobQueue() if settings.job_queue_backend == "memory" else None
            self._queue_factory = lambda session: ConversationJobQueue(build_job_queue(settings.job_queue_backend, session, memory), worker_id=self._worker_id)
        self._stopping = asyncio.Event()

    async def run(self) -> None:
        failures = 0
        while not self._stopping.is_set():
            try:
                processed = await self.run_once()
                failures = 0
            except SQLAlchemyError:
                # A database outage or schema reset must not kill the process; programming errors still propagate.
                failures += 1
                logger.exception("conversation worker poll failed (attempt %d); retrying after backoff", failures)
                processed = False
            delay = 0.25 if failures == 0 else min(30.0, FAILURE_BACKOFF_SECONDS * 2 ** min(failures - 1, 4))
            if not processed:
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=delay)
                except TimeoutError:
                    pass

    async def run_once(self) -> bool:
        messages = self._read_batch()
        if not messages:
            return False
        outcomes = await asyncio.gather(*(self._execute(message) for message in messages))
        return any(outcomes)

    def stop(self) -> None:
        self._stopping.set()

    def _read_batch(self) -> list[ConversationQueueMessage]:
        with self._sessions() as session:
            queue = self._queue_factory(session)
            messages = queue.read_group_heads(
                visibility_timeout_seconds=self._settings.conversation_queue_visibility_timeout,
                quantity=self._settings.conversation_worker_concurrency,
            )
            session.commit()
            return messages

    def _claim_message(
        self,
        message: ConversationQueueMessage,
    ) -> ClaimedTurn | None:
        with self._sessions() as session:
            queue = self._queue_factory(session)
            repository = SqlAlchemyConversationRepository(session, queue)
            owner_id = repository.owner_for_execution(message.execution)
            if owner_id is None:
                queue.archive(message.message_id, message.lease_token)
                session.commit()
                return None
            try:
                principal = OrganizationApplication(
                    SqlAlchemyOrganizationRepository(session)
                ).authenticated_principal(owner_id)
                claimed = repository.claim_execution(message.execution, principal)
            except LookupError:
                repository.fail_execution(
                    message.execution,
                    "Conversation owner is no longer authorized",
                )
                claimed = None
            if claimed is None:
                queue.archive(message.message_id, message.lease_token)
                session.commit()
                return None
            turn, request = claimed
            session.commit()
        # What this conversation already stands on, re-checked for this person right now. Provider memory is an
        # optimization; the canonical conversation is what a turn is actually built from.
        pack = self._application.conversation_context_pack(
            principal,
            message.execution.conversation_id,
            include_exchanges=request.provider_session_ref is None,
        )
        request = replace(
            request,
            # A provider checkpoint may contain prior material text whose permission has since changed.
            provider_session_ref=None if pack.get("reset_provider_session") else request.provider_session_ref,
            seed_references=tuple(pack["seeds"]),
            recent_exchanges=tuple(pack["exchanges"]),
        )
        with self._sessions() as session:
            return ClaimedTurn(
                message.message_id,
                message.lease_token,
                message.read_count,
                turn.id,
                message.execution,
                request,
            )

    async def _execute(self, message: ConversationQueueMessage) -> bool:
        with self._execution_guard.hold(message.execution) as acquired:
            if not acquired:
                # A live worker owns this turn (defense in depth beside the lease). Hand the job back so the
                # next claim, after the live worker finishes, can finalize it; never invoke the provider here.
                with self._sessions() as session:
                    self._queue_factory(session).release(message.message_id, message.lease_token, delay_seconds=GUARD_CONFLICT_RELEASE_SECONDS, error="execution guard held by a live worker")
                    session.commit()
                return False
            claim = self._claim_message(message)
            if claim is None:
                return False
            cancel = threading.Event()
            heartbeat = asyncio.create_task(self._heartbeat(claim, cancel))
            sink = _ProjectionSink(self, claim.execution)
            try:
                result = await asyncio.to_thread(self._provider.converse, claim.request, sink=sink, cancel=cancel)
            except ProviderFailure as error:
                self._handle_failure(claim, error)
            finally:
                heartbeat.cancel()
                try:
                    await heartbeat
                except asyncio.CancelledError:
                    pass
            if "result" in locals():
                self._complete(claim, result)
            return True

    async def _heartbeat(self, claim: ClaimedTurn, cancel: threading.Event) -> None:
        """Extend the transport lease and watch for a DB-side cancellation, which stops the provider process."""
        interval = max(1, self._settings.conversation_queue_visibility_timeout // 2)
        cancel_poll = 1.0
        elapsed = 0.0
        while True:
            await asyncio.sleep(cancel_poll)
            elapsed += cancel_poll
            with self._sessions() as session:
                queue = self._queue_factory(session)
                if SqlAlchemyConversationRepository(session, queue).is_cancelled(claim.execution):
                    cancel.set()
                if elapsed >= interval:
                    elapsed = 0.0
                    # Fenced: a lost lease is not silently re-acquired by heartbeating.
                    queue.extend_visibility(claim.message_id, claim.lease_token, self._settings.conversation_queue_visibility_timeout)
                session.commit()

    def apply_event(self, execution: ConversationExecution, event: Any) -> None:
        """Persist one observed provider event in its own short transaction (called from the provider thread)."""
        with self._sessions() as session:
            SqlAlchemyConversationRepository(session, self._queue_factory(session)).apply_event(execution, event)
            session.commit()

    def _complete(self, claim: ClaimedTurn, result: Any) -> None:
        with self._sessions() as session:
            queue = self._queue_factory(session)
            # Domain result first (idempotent on turn state); the transport write is fenced by our lease token and
            # is simply skipped when the lease was reclaimed - the next claimer sees the terminal turn and finalizes.
            SqlAlchemyConversationRepository(session, queue).complete_execution(
                claim.execution,
                result,
            )
            queue.archive(claim.message_id, claim.lease_token)
            session.commit()

    def _handle_failure(self, claim: ClaimedTurn, error: ProviderFailure) -> None:
        with self._sessions() as session:
            queue = self._queue_factory(session)
            terminal = SqlAlchemyConversationRepository(session, queue).fail_or_retry_execution(
                claim.execution,
                self._settings.conversation_queue_max_attempts,
                error,
            )
            if terminal:
                queue.archive(claim.message_id, claim.lease_token)
            else:
                # The user sees `retrying` (not a silent stall) while the transport backs off.
                SqlAlchemyConversationRepository(session, queue).mark_retrying(claim.execution)
                delay = min(self._settings.conversation_queue_visibility_timeout, 2 ** max(0, claim.read_count - 1))
                queue.release(claim.message_id, claim.lease_token, delay_seconds=delay, error=str(error)[:200])
            session.commit()


class _ProjectionSink:
    """AiEventSink that writes observed provider events into the Conversation projection."""

    def __init__(self, worker: ConversationWorker, execution: ConversationExecution) -> None:
        self._worker = worker
        self._execution = execution

    def accept(self, event: Any) -> None:
        self._worker.apply_event(self._execution, event)
