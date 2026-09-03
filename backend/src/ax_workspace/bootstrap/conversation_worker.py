"""Separate PGMQ-backed process for AX conversation execution."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable
from uuid import UUID

from ax_workspace.bootstrap.settings import Settings

from ax_workspace.modules.ax_execution.ai import AiProvider, ProviderFailure
from ax_workspace.modules.ax_execution.conversations import (
    ConversationExecution,
    ConversationExecutionQueue,
    ConversationQueueMessage,
)
from ax_workspace.modules.organization_access.application import OrganizationApplication
from ax_workspace.bootstrap.application import create_codex_cli_provider
from ax_workspace.platform.conversation_queue import (
    NullConversationExecutionQueue,
    PgmqConversationTurnQueue,
)
from ax_workspace.platform.conversations import (
    SqlAlchemyConversationExecutionGuard,
    SqlAlchemyConversationRepository,
)
from ax_workspace.platform.organization_access import SqlAlchemyOrganizationRepository
from ax_workspace.platform.persistence import make_session_factory


@dataclass(frozen=True, slots=True)
class ClaimedTurn:
    message_id: int
    read_count: int
    turn_id: UUID
    execution: ConversationExecution
    request: Any


class ConversationWorker:
    """Consumes one visible PGMQ head per Conversation group at a time."""

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
        if queue_factory is not None:
            self._queue_factory = queue_factory
        elif settings.conversation_queue_backend == "pgmq":
            self._queue_factory = lambda session: PgmqConversationTurnQueue(session)
        elif settings.conversation_queue_backend == "null":
            self._queue_factory = lambda session: NullConversationExecutionQueue()
        else:
            raise RuntimeError("Unknown AX conversation queue backend")
        self._stopping = asyncio.Event()

    async def run(self) -> None:
        while not self._stopping.is_set():
            processed = await self.run_once()
            if not processed:
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=0.25)
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
                queue.archive(message.message_id)
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
                queue.archive(message.message_id)
                session.commit()
                return None
            turn, request = claimed
            session.commit()
            return ClaimedTurn(
                message.message_id,
                message.read_count,
                turn.id,
                message.execution,
                request,
            )

    async def _execute(self, message: ConversationQueueMessage) -> bool:
        with self._execution_guard.hold(message.execution) as acquired:
            if not acquired:
                # A live worker owns this turn.  Do not consume the redelivery;
                # its original queue receipt remains responsible for archival.
                return False
            claim = self._claim_message(message)
            if claim is None:
                return False
            heartbeat = asyncio.create_task(self._heartbeat(claim.message_id))
            try:
                result = await asyncio.to_thread(self._provider.converse, claim.request)
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

    async def _heartbeat(self, message_id: int) -> None:
        interval = max(1, self._settings.conversation_queue_visibility_timeout // 2)
        while True:
            await asyncio.sleep(interval)
            with self._sessions() as session:
                self._queue_factory(session).extend_visibility(
                    message_id,
                    self._settings.conversation_queue_visibility_timeout,
                )
                session.commit()

    def _complete(self, claim: ClaimedTurn, result: Any) -> None:
        with self._sessions() as session:
            queue = self._queue_factory(session)
            SqlAlchemyConversationRepository(session, queue).complete_execution(
                claim.execution,
                result,
            )
            queue.archive(claim.message_id)
            session.commit()

    def _handle_failure(self, claim: ClaimedTurn, error: ProviderFailure) -> None:
        with self._sessions() as session:
            queue = self._queue_factory(session)
            terminal = SqlAlchemyConversationRepository(session, queue).fail_or_retry_execution(
                claim.execution,
                claim.read_count,
                self._settings.conversation_queue_max_attempts,
                error,
            )
            if terminal:
                queue.archive(claim.message_id)
            session.commit()
