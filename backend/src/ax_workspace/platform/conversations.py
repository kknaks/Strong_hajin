"""PostgreSQL-backed persistence for AX conversation fragments and turns."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ax_workspace.modules.ax_execution.ai import AiConversationRequest, AiConversationResult, ProviderFailure
from ax_workspace.modules.ax_execution.conversations import (
    ConversationContextReferenceInput,
    ConversationError,
    ConversationExecution,
    ConversationExecutionQueue,
    ConversationQueueOverflow,
)
from ax_workspace.modules.ax_execution.ai import AiDelegatedToolContext
from ax_workspace.modules.organization_access.domain import Principal
from ax_workspace.platform.persistence import (
    ContextReferenceRecord,
    ConversationMessageRecord,
    ConversationProviderSessionReferenceRecord,
    ConversationRecord,
    ConversationTurnRecord,
    ConversationAuditEventRecord,
    ToolInvocationRecord,
    TaskRecord,
    WorkRequestRecord,
)


class SqlAlchemyConversationRepository:
    def __init__(
        self,
        session: Session,
        queue: ConversationExecutionQueue,
        queue_limit: int = 8,
    ) -> None:
        self._session = session
        self._queue_limit = queue_limit
        self._queue = queue

    def create(self, owner_id: str, title: str) -> ConversationRecord:
        now = datetime.now(UTC)
        record = ConversationRecord(owner_id=owner_id, title=title, version=1, created_at=now, updated_at=now)
        self._session.add(record)
        self._session.flush()
        return record

    def conversation(self, conversation_id: UUID, owner_id: str, *, lock: bool = False) -> ConversationRecord | None:
        statement = select(ConversationRecord).where(ConversationRecord.id == conversation_id, ConversationRecord.owner_id == owner_id)
        return self._session.scalar(statement.with_for_update() if lock else statement)

    def list_for(self, owner_id: str) -> list[ConversationRecord]:
        return list(self._session.scalars(select(ConversationRecord).where(ConversationRecord.owner_id == owner_id).order_by(ConversationRecord.updated_at.desc(), ConversationRecord.id)))

    def accept_fragment(
        self,
        conversation: ConversationRecord,
        body: str,
        context: list[dict[str, str | bool]],
        idempotency_key: str | None,
    ) -> tuple[ConversationMessageRecord, ConversationTurnRecord | None, bool, int]:
        if idempotency_key:
            existing = self._session.scalar(select(ConversationMessageRecord).where(ConversationMessageRecord.conversation_id == conversation.id, ConversationMessageRecord.idempotency_key == idempotency_key))
            if existing is not None:
                return existing, self._turn(existing.turn_id), existing.turn_id is None, self._queued_count(conversation.id)
        active = self._active_turn(conversation.id)
        queue_size = self._queued_count(conversation.id)
        if active is not None and queue_size >= self._queue_limit:
            raise ConversationQueueOverflow(queue_size, self._queue_limit)
        now = datetime.now(UTC)
        message = self._message(conversation.id, None, "user", body, now, idempotency_key)
        for reference in context:
            self._session.add(
                ContextReferenceRecord(
                    conversation_id=conversation.id,
                    turn_id=None,
                    message_id=message.id,
                    resource_type=str(reference["resource_type"]),
                    resource_id=str(reference["resource_id"]),
                    resource_version=int(str(reference["resource_version"])),
                    summary=str(reference["summary"]),
                    included=bool(reference["included"]),
                )
            )
        conversation.version += 1
        conversation.updated_at = now
        if active is not None:
            return message, None, True, queue_size + 1
        turn = self.drain(conversation)
        assert turn is not None
        self._enqueue(turn)
        return message, turn, False, 0

    def complete(self, turn: ConversationTurnRecord, result: AiConversationResult) -> ConversationTurnRecord | None:
        now = datetime.now(UTC)
        turn.state = "completed"
        turn.provider_run_ref = result.provider_run_ref
        turn.provider_session_ref = result.provider_session_ref
        turn.completed_at = now
        conversation = self._session.get(ConversationRecord, turn.conversation_id)
        assert conversation is not None
        if result.provider_session_ref:
            self._session.add(ConversationProviderSessionReferenceRecord(conversation_id=conversation.id, provider_session_ref=result.provider_session_ref, recorded_at=now))
        self._message(conversation.id, turn.id, "assistant", result.body, now, None)
        for sequence, invocation in enumerate(result.tool_invocations, 1):
            self._session.add(ToolInvocationRecord(turn_id=turn.id, sequence=sequence, provider_call_id=invocation.provider_call_id, tool_name=invocation.tool_name, display_name=invocation.display_name, input_summary=invocation.input_summary, state=invocation.state, result_summary=invocation.result_summary, error_summary=invocation.error_summary, latency_ms=invocation.latency_ms, target_resource_id=invocation.target_resource_id, target_resource_version=invocation.target_resource_version, audit_ref=invocation.audit_ref, started_at=now, completed_at=now))
        next_turn = self.drain(conversation)
        if next_turn is not None:
            self._enqueue(next_turn)
        return next_turn

    def fail(self, turn: ConversationTurnRecord, error: ProviderFailure) -> ConversationTurnRecord | None:
        now = datetime.now(UTC)
        turn.state = "failed"
        turn.provider_run_ref = error.provenance.provider_run_ref
        turn.provider_session_ref = error.provenance.provider_session_ref
        turn.completed_at = now
        turn.normalized_error = str(error)
        conversation = self._session.get(ConversationRecord, turn.conversation_id)
        assert conversation is not None
        self._session.add(
            ConversationAuditEventRecord(
                conversation_id=conversation.id,
                turn_id=turn.id,
                event_type="conversation.turn.failed",
                payload={"error": str(error)},
                occurred_at=now,
            )
        )
        next_turn = self.drain(conversation)
        if next_turn is not None:
            self._enqueue(next_turn)
        return next_turn

    def cancel(self, turn: ConversationTurnRecord) -> None:
        turn.state = "cancelled"
        turn.completed_at = datetime.now(UTC)

    def drain(self, conversation: ConversationRecord) -> ConversationTurnRecord | None:
        if self._active_turn(conversation.id) is not None:
            return None
        fragments = self._session.scalars(select(ConversationMessageRecord).where(ConversationMessageRecord.conversation_id == conversation.id, ConversationMessageRecord.role == "user", ConversationMessageRecord.turn_id.is_(None)).order_by(ConversationMessageRecord.sequence)).all()
        if not fragments:
            return None
        now = datetime.now(UTC)
        turn = ConversationTurnRecord(
            conversation_id=conversation.id,
            state="pending",
            provider_run_ref=None,
            provider_session_ref=self.latest_session(conversation),
            started_at=now,
            completed_at=None,
            normalized_error=None,
        )
        self._session.add(turn)
        self._session.flush()
        fragment_ids = [fragment.id for fragment in fragments]
        for fragment in fragments:
            fragment.turn_id = turn.id
        for reference in self._session.scalars(select(ContextReferenceRecord).where(ContextReferenceRecord.message_id.in_(fragment_ids))):
            reference.turn_id = turn.id
        return turn

    def request_for(
        self,
        turn: ConversationTurnRecord,
        principal: Principal,
    ) -> AiConversationRequest:
        fragments = self._session.scalars(select(ConversationMessageRecord).where(ConversationMessageRecord.turn_id == turn.id, ConversationMessageRecord.role == "user").order_by(ConversationMessageRecord.sequence)).all()
        refs = self._session.scalars(
            select(ContextReferenceRecord)
            .where(ContextReferenceRecord.turn_id == turn.id)
            .order_by(ContextReferenceRecord.message_id, ContextReferenceRecord.id)
        ).all()
        refreshed = SqlAlchemyConversationContextResolver(self._session).resolve(
            principal,
            [
                ConversationContextReferenceInput(
                    resource_type=ref.resource_type,
                    resource_id=ref.resource_id,
                    resource_version=ref.resource_version,
                    included=ref.included,
                )
                for ref in refs
            ],
        )
        for stored, current in zip(refs, refreshed, strict=True):
            if (
                stored.resource_id != current["resource_id"]
                or stored.resource_version != int(str(current["resource_version"]))
                or stored.summary != current["summary"]
            ):
                raise ConversationError("context resource is stale")
        return AiConversationRequest(
            prompt="\n".join(fragment.body for fragment in fragments),
            provider_session_ref=turn.provider_session_ref,
            context_references=[
                {
                    "resource_type": str(item["resource_type"]),
                    "resource_id": str(item["resource_id"]),
                    "resource_version": str(item["resource_version"]),
                    "summary": str(item["summary"]),
                }
                for item in refreshed
                if item["included"]
            ],
            delegated_tool_context=AiDelegatedToolContext(
                principal_id=str(principal.id),
                causation_id=str(turn.execution_id),
            ),
        )

    def latest_session(self, conversation: ConversationRecord) -> str | None:
        return self._session.scalar(select(ConversationProviderSessionReferenceRecord.provider_session_ref).where(ConversationProviderSessionReferenceRecord.conversation_id == conversation.id).order_by(ConversationProviderSessionReferenceRecord.recorded_at.desc(), ConversationProviderSessionReferenceRecord.id.desc()))

    def view(self, conversation: ConversationRecord) -> dict[str, Any]:
        messages = self._session.scalars(select(ConversationMessageRecord).where(ConversationMessageRecord.conversation_id == conversation.id).order_by(ConversationMessageRecord.sequence)).all()
        turns = self._session.scalars(select(ConversationTurnRecord).where(ConversationTurnRecord.conversation_id == conversation.id).order_by(ConversationTurnRecord.started_at, ConversationTurnRecord.id)).all()
        refs = self._session.scalars(select(ContextReferenceRecord).where(ContextReferenceRecord.conversation_id == conversation.id).order_by(ContextReferenceRecord.message_id, ContextReferenceRecord.id)).all()
        tools = self._session.scalars(select(ToolInvocationRecord).join(ConversationTurnRecord).where(ConversationTurnRecord.conversation_id == conversation.id).order_by(ConversationTurnRecord.started_at, ToolInvocationRecord.sequence)).all()
        return {
            "conversation_id": str(conversation.id),
            "title": conversation.title,
            "version": conversation.version,
            "messages": [
                {
                    "message_id": str(message.id),
                    "turn_id": str(message.turn_id) if message.turn_id else None,
                    "role": message.role,
                    "body": message.body,
                    "sequence": message.sequence,
                    "state": "queued"
                    if message.role == "user" and message.turn_id is None
                    else "accepted",
                }
                for message in messages
            ],
            "turns": [
                {
                    "turn_id": str(turn.id),
                    "state": turn.state,
                    "provider_run_ref": turn.provider_run_ref,
                    "provider_session_ref": turn.provider_session_ref,
                    "error": turn.normalized_error,
                }
                for turn in turns
            ],
            "context_references": [
                {
                    "message_id": str(ref.message_id),
                    "turn_id": str(ref.turn_id) if ref.turn_id else None,
                    "resource_type": ref.resource_type,
                    "resource_id": ref.resource_id,
                    "resource_version": ref.resource_version,
                    "summary": ref.summary,
                    "included": ref.included,
                }
                for ref in refs
            ],
            "tool_invocations": [
                {
                    "turn_id": str(tool.turn_id),
                    "sequence": tool.sequence,
                    "provider_call_id": tool.provider_call_id,
                    "tool_name": tool.tool_name,
                    "display_name": tool.display_name,
                    "input_summary": tool.input_summary,
                    "state": tool.state,
                    "result_summary": tool.result_summary,
                    "error_summary": tool.error_summary,
                    "latency_ms": tool.latency_ms,
                    "target_resource_id": tool.target_resource_id,
                    "target_resource_version": tool.target_resource_version,
                    "audit_ref": tool.audit_ref,
                }
                for tool in tools
            ],
        }

    def _active_turn(self, conversation_id: UUID) -> ConversationTurnRecord | None:
        return self._session.scalar(select(ConversationTurnRecord).where(ConversationTurnRecord.conversation_id == conversation_id, ConversationTurnRecord.state.in_(("pending", "running"))))

    def _turn(self, turn_id: UUID | None) -> ConversationTurnRecord | None:
        return self._session.get(ConversationTurnRecord, turn_id) if turn_id else None

    def _queued_count(self, conversation_id: UUID) -> int:
        return int(self._session.scalar(select(func.count()).select_from(ConversationMessageRecord).where(ConversationMessageRecord.conversation_id == conversation_id, ConversationMessageRecord.role == "user", ConversationMessageRecord.turn_id.is_(None))) or 0)

    def _message(self, conversation_id: UUID, turn_id: UUID | None, role: str, body: str, now: datetime, idempotency_key: str | None) -> ConversationMessageRecord:
        sequence = int(self._session.scalar(select(func.coalesce(func.max(ConversationMessageRecord.sequence), 0)).where(ConversationMessageRecord.conversation_id == conversation_id)) or 0) + 1
        message = ConversationMessageRecord(conversation_id=conversation_id, turn_id=turn_id, sequence=sequence, role=role, body=body, idempotency_key=idempotency_key, created_at=now)
        self._session.add(message)
        self._session.flush()
        return message

    def _enqueue(self, turn: ConversationTurnRecord) -> None:
        self._queue.enqueue(
            ConversationExecution(
                turn_id=turn.id,
                conversation_id=turn.conversation_id,
                execution_id=turn.execution_id,
            )
        )


class SqlAlchemyConversationContextResolver:
    """Resolves reference data from current authorized SCAX records, never the client."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve(
        self,
        principal: Principal,
        references: list[ConversationContextReferenceInput],
    ) -> list[dict[str, str | bool]]:
        return [self._resolve_one(principal, reference) for reference in references]

    def _resolve_one(
        self,
        principal: Principal,
        reference: ConversationContextReferenceInput,
    ) -> dict[str, str | bool]:
        if reference.resource_type == "task":
            return self._task(principal, reference)
        if reference.resource_type == "work_request":
            return self._work_request(principal, reference)
        raise ConversationError("unsupported context resource type")

    def _task(
        self,
        principal: Principal,
        reference: ConversationContextReferenceInput,
    ) -> dict[str, str | bool]:
        record = self._session.get(TaskRecord, UUID(reference.resource_id))
        if record is None or record.owner_id != str(principal.id):
            raise ConversationError("context resource was not found")
        if record.version != reference.resource_version:
            raise ConversationError("context resource is stale")
        return {
            "resource_type": "task",
            "resource_id": str(record.id),
            "resource_version": str(record.version),
            "summary": f"업무: {record.title} ({record.state})",
            "included": reference.included,
        }

    def _work_request(
        self,
        principal: Principal,
        reference: ConversationContextReferenceInput,
    ) -> dict[str, str | bool]:
        record = self._session.get(WorkRequestRecord, UUID(reference.resource_id))
        if record is None or str(principal.id) not in {
            record.requester_id,
            record.assignee_id,
        }:
            raise ConversationError("context resource was not found")
        if record.version != reference.resource_version:
            raise ConversationError("context resource is stale")
        return {
            "resource_type": "work_request",
            "resource_id": str(record.id),
            "resource_version": str(record.version),
            "summary": f"업무 요청: {record.title} ({record.state})",
            "included": reference.included,
        }
