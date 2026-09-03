"""Separate PGMQ-backed process for AX conversation execution."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import select

from ax_workspace.bootstrap.settings import Settings
from ax_workspace.modules.ax_execution.ai import AiProvider, ProviderFailure, ProviderRequestFailed
from ax_workspace.modules.ax_execution.conversations import (
    ConversationError,
    ConversationExecutionQueue,
    ConversationQueueMessage,
)
from ax_workspace.modules.organization_access.application import OrganizationApplication
from ax_workspace.bootstrap.application import create_codex_cli_provider
from ax_workspace.platform.conversation_queue import (
    NullConversationExecutionQueue,
    PgmqConversationTurnQueue,
)
from ax_workspace.platform.conversations import SqlAlchemyConversationRepository
from ax_workspace.platform.organization_access import SqlAlchemyOrganizationRepository
from ax_workspace.platform.persistence import ConversationRecord, ConversationTurnRecord, make_session_factory


@dataclass(frozen=True, slots=True)
class ClaimedTurn:
    message_id: int
    read_count: int
    turn_id: UUID
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
        claims = self._claim_batch()
        if not claims:
            return False
        await asyncio.gather(*(self._execute(claim) for claim in claims))
        return True

    def stop(self) -> None:
        self._stopping.set()

    def _claim_batch(self) -> list[ClaimedTurn]:
        with self._sessions() as session:
            queue = self._queue_factory(session)
            messages = queue.read_group_heads(
                visibility_timeout_seconds=self._settings.conversation_queue_visibility_timeout,
                quantity=self._settings.conversation_worker_concurrency,
            )
            claims: list[ClaimedTurn] = []
            for message in messages:
                claim = self._claim_message(session, queue, message)
                if claim is not None:
                    claims.append(claim)
            session.commit()
            return claims

    def _claim_message(
        self,
        session: Any,
        queue: ConversationExecutionQueue,
        message: ConversationQueueMessage,
    ) -> ClaimedTurn | None:
        turn = session.scalar(
            select(ConversationTurnRecord)
            .where(ConversationTurnRecord.id == message.execution.turn_id)
            .with_for_update()
        )
        if turn is None or (
            turn.id != message.execution.turn_id
            or turn.conversation_id != message.execution.conversation_id
            or turn.execution_id != message.execution.execution_id
        ):
            queue.archive(message.message_id)
            return None
        if turn.state in {"completed", "failed", "cancelled"}:
            queue.archive(message.message_id)
            return None
        if turn.state == "pending":
            turn.state = "running"
        try:
            conversation = session.get(ConversationRecord, turn.conversation_id)
            assert conversation is not None
            principal = OrganizationApplication(
                SqlAlchemyOrganizationRepository(session)
            ).authenticated_principal(conversation.owner_id)
            request = SqlAlchemyConversationRepository(session, queue).request_for(turn, principal)
        except (ConversationError, LookupError):
            SqlAlchemyConversationRepository(session, queue).fail(
                turn,
                ProviderRequestFailed("Conversation context is no longer authorized"),
            )
            queue.archive(message.message_id)
            return None
        return ClaimedTurn(message.message_id, message.read_count, turn.id, request)

    async def _execute(self, claim: ClaimedTurn) -> None:
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
            turn = session.scalar(
                select(ConversationTurnRecord)
                .where(ConversationTurnRecord.id == claim.turn_id)
                .with_for_update()
            )
            if turn is not None and turn.state == "running":
                SqlAlchemyConversationRepository(session, queue).complete(turn, result)
            queue.archive(claim.message_id)
            session.commit()

    def _handle_failure(self, claim: ClaimedTurn, error: ProviderFailure) -> None:
        with self._sessions() as session:
            queue = self._queue_factory(session)
            turn = session.scalar(
                select(ConversationTurnRecord)
                .where(ConversationTurnRecord.id == claim.turn_id)
                .with_for_update()
            )
            if turn is None or turn.state != "running":
                queue.archive(claim.message_id)
            elif claim.read_count >= self._settings.conversation_queue_max_attempts:
                SqlAlchemyConversationRepository(session, queue).fail(turn, error)
                queue.archive(claim.message_id)
            # Else PGMQ's VT makes the same message visible for another attempt.
            session.commit()
