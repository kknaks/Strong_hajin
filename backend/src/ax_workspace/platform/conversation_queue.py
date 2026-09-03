"""PGMQ transport for persisted AX conversation turns.

The queue is an execution transport only. Conversation, message, turn, context,
and tool records remain SCAX's durable domain source of truth.
"""
from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

from ax_workspace.modules.ax_execution.conversations import (
    ConversationExecution,
    ConversationQueueMessage,
)
from ax_workspace.platform.persistence import ConversationTurnRecord


CONVERSATION_EXECUTION_QUEUE = "ax_conversation_turn_execution"


class NullConversationExecutionQueue:
    """Test-only transport; it deliberately does not simulate PGMQ semantics."""

    def enqueue(self, execution: ConversationExecution) -> None:
        del execution

    def read_group_heads(
        self,
        *,
        visibility_timeout_seconds: int,
        quantity: int,
    ) -> list[ConversationQueueMessage]:
        del visibility_timeout_seconds, quantity
        return []

    def archive(self, message_id: int) -> None:
        del message_id

    def extend_visibility(self, message_id: int, visibility_timeout_seconds: int) -> None:
        del message_id, visibility_timeout_seconds


def ensure_conversation_queue(connection: Connection) -> None:
    """Install and empty the FIFO queue during explicit local/test reset only."""

    connection.execute(text("CREATE EXTENSION IF NOT EXISTS pgmq"))
    queue_table = f"pgmq.q_{CONVERSATION_EXECUTION_QUEUE}"
    if connection.scalar(text("SELECT to_regclass(:queue_table)"), {"queue_table": queue_table}):
        connection.execute(
            text("SELECT pgmq.drop_queue(:queue_name)"),
            {"queue_name": CONVERSATION_EXECUTION_QUEUE},
        )
    connection.execute(
        text("SELECT pgmq.create(:queue_name)"),
        {"queue_name": CONVERSATION_EXECUTION_QUEUE},
    )
    connection.execute(
        text("SELECT pgmq.create_fifo_index(:queue_name)"),
        {"queue_name": CONVERSATION_EXECUTION_QUEUE},
    )


class PgmqConversationTurnQueue:
    """SQL adapter around PGMQ; every call joins the caller's DB transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(self, execution: ConversationExecution) -> None:
        payload = {
            "turn_id": str(execution.turn_id),
            "conversation_id": str(execution.conversation_id),
            "execution_id": str(execution.execution_id),
        }
        headers = {"x-pgmq-group": str(execution.conversation_id)}
        self._session.execute(
            text(
                "SELECT pgmq.send(:queue_name, CAST(:payload AS jsonb), "
                "CAST(:headers AS jsonb))"
            ),
            {
                "queue_name": CONVERSATION_EXECUTION_QUEUE,
                "payload": json.dumps(payload),
                "headers": json.dumps(headers),
            },
        )

    def read_group_heads(
        self,
        *,
        visibility_timeout_seconds: int,
        quantity: int,
    ) -> list[ConversationQueueMessage]:
        rows = self._session.execute(
            text(
                "SELECT msg_id, read_ct, message "
                "FROM pgmq.read_grouped_head(:queue_name, :visibility_timeout, :quantity)"
            ),
            {
                "queue_name": CONVERSATION_EXECUTION_QUEUE,
                "visibility_timeout": visibility_timeout_seconds,
                "quantity": quantity,
            },
        ).mappings().all()
        messages: list[ConversationQueueMessage] = []
        for row in rows:
            payload = row["message"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            if not isinstance(payload, dict):
                raise RuntimeError("PGMQ conversation payload is invalid")
            messages.append(
                ConversationQueueMessage(
                    message_id=int(row["msg_id"]),
                    read_count=int(row["read_ct"]),
                    execution=ConversationExecution(
                        turn_id=UUID(str(payload["turn_id"])),
                        conversation_id=UUID(str(payload["conversation_id"])),
                        execution_id=UUID(str(payload["execution_id"])),
                    ),
                )
            )
        return messages

    def archive(self, message_id: int) -> None:
        self._session.execute(
            text("SELECT pgmq.archive(:queue_name, :message_id)"),
            {"queue_name": CONVERSATION_EXECUTION_QUEUE, "message_id": message_id},
        )

    def extend_visibility(
        self,
        message_id: int,
        visibility_timeout_seconds: int,
    ) -> None:
        self._session.execute(
            text("SELECT pgmq.set_vt(:queue_name, :message_id, :visibility_timeout)"),
            {
                "queue_name": CONVERSATION_EXECUTION_QUEUE,
                "message_id": message_id,
                "visibility_timeout": visibility_timeout_seconds,
            },
        )

    @staticmethod
    def matches(turn: ConversationTurnRecord, execution: ConversationExecution) -> bool:
        return (
            execution.turn_id == turn.id
            and execution.conversation_id == turn.conversation_id
            and execution.execution_id == turn.execution_id
        )
