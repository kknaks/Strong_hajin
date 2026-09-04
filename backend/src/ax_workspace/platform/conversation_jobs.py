"""Conversation Turn delivery over the shared DurableJobQueue (ordering key = conversation, FIFO head only)."""
from __future__ import annotations

from uuid import UUID

from ax_workspace.modules.ax_execution.conversations import ConversationExecution, ConversationQueueMessage
from ax_workspace.modules.jobs.domain import JOB_KIND_CONVERSATION_TURN, DurableJobQueue, JobEnvelope


class ConversationJobQueue:
    """Adapts the durable job port to the Conversation transport port. Every write carries the claim's lease token."""

    def __init__(self, jobs: DurableJobQueue, *, worker_id: str = "api") -> None:
        self._jobs = jobs
        self._worker_id = worker_id

    def enqueue(self, execution: ConversationExecution) -> None:
        self._jobs.enqueue(
            JobEnvelope(
                kind=JOB_KIND_CONVERSATION_TURN,
                ordering_key=str(execution.conversation_id),
                idempotency_key=f"{JOB_KIND_CONVERSATION_TURN}:{execution.execution_id}",
                payload={
                    "turn_id": str(execution.turn_id),
                    "conversation_id": str(execution.conversation_id),
                    "execution_id": str(execution.execution_id),
                },
            )
        )

    def read_group_heads(self, *, visibility_timeout_seconds: int, quantity: int) -> list[ConversationQueueMessage]:
        claimed = self._jobs.claim(JOB_KIND_CONVERSATION_TURN, limit=quantity, lease_seconds=visibility_timeout_seconds, worker_id=self._worker_id)
        return [
            ConversationQueueMessage(
                message_id=str(job.job_id),
                lease_token=str(job.lease_token),
                read_count=job.attempt,
                execution=ConversationExecution(
                    turn_id=UUID(str(job.payload["turn_id"])),
                    conversation_id=UUID(str(job.payload["conversation_id"])),
                    execution_id=UUID(str(job.payload["execution_id"])),
                ),
            )
            for job in claimed
        ]

    def archive(self, message_id: str, lease_token: str) -> bool:
        return self._jobs.complete(UUID(message_id), UUID(lease_token))

    def release(self, message_id: str, lease_token: str, *, delay_seconds: int, error: str | None = None) -> bool:
        return self._jobs.release(UUID(message_id), UUID(lease_token), delay_seconds=delay_seconds, error=error)

    def extend_visibility(self, message_id: str, lease_token: str, visibility_timeout_seconds: int) -> bool:
        return self._jobs.extend_lease(UUID(message_id), UUID(lease_token), visibility_timeout_seconds)
