"""PostgreSQL-backed persistence for AX conversation fragments and turns."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Engine, delete, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from ax_workspace.platform.actions import SqlAlchemyActionRepository
from ax_workspace.modules.ax_execution.ai import (
    AiConversationRequest,
    AiConversationResult,
    ProviderResponseInvalid,
    AiProviderEvent,
    AiToolInvocation,
    ProviderCancelled,
    ProviderFailure,
    ProviderRequestFailed,
)
from ax_workspace.modules.ax_execution.conversations import (
    DEFAULT_CONVERSATION_LIST_LIMIT,
    ConversationContextReferenceInput,
    ConversationError,
    ConversationNotFound,
    ConversationExecution,
    ConversationExecutionQueue,
    ConversationQueueOverflow,
)
from ax_workspace.modules.ax_execution.ai import AiDelegatedToolContext
from ax_workspace.modules.organization_access.domain import Principal
from ax_workspace.platform.persistence import (
    ActionItemRecord,
    ContextReferenceRecord,
    ConversationAnswerResourceRecord,
    ConversationGraphReceiptRecord,
    ConversationContentEvidenceRecord,
    ConversationMessageRecord,
    ConversationProviderSessionReferenceRecord,
    ConversationRecord,
    ConversationTurnRecord,
    ConversationAuditEventRecord,
    ToolInvocationRecord,
    TaskAssignmentRecord,
    TaskRecord,
    WorkRequestRecord,
)

def _scope_to_window(statement: Any, column: Any, ids: set[Any] | None) -> Any:
    """When paginating, keep only rows for turns the returned message window can point at.

    `ids=None` means unpaginated (full conversation, current behavior unchanged). An empty `ids` set is a real
    window with no turn yet (e.g. only queued user messages) — that must match nothing, not silently drop the
    filter and return the whole conversation's rows.
    """
    if ids is None:
        return statement
    return statement.where(column.in_(ids)) if ids else statement.where(False)


class SqlAlchemyConversationRepository:
    def __init__(
        self,
        session: Session,
        queue: ConversationExecutionQueue,
        queue_limit: int = 8,
        *,
        actions: SqlAlchemyActionRepository,
    ) -> None:
        self._session = session
        self._actions = actions
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
        return self._session.scalar(statement.with_for_update().execution_options(populate_existing=True) if lock else statement)

    def list_for(self, owner_id: str, *, limit: int = DEFAULT_CONVERSATION_LIST_LIMIT) -> list[ConversationRecord]:
        return list(self._session.scalars(
            select(ConversationRecord).where(ConversationRecord.owner_id == owner_id)
            .order_by(ConversationRecord.updated_at.desc(), ConversationRecord.id).limit(limit)
        ))

    def accept_fragment(
        self,
        conversation: ConversationRecord,
        body: str,
        context: list[dict[str, str | bool]],
        idempotency_key: str | None,
        follow_up_candidate_id: UUID | None = None,
    ) -> tuple[ConversationMessageRecord, ConversationTurnRecord | None, bool, int]:
        if follow_up_candidate_id is not None:
            existing_candidate = self._session.scalar(
                select(ConversationMessageRecord).where(
                    ConversationMessageRecord.conversation_id == conversation.id,
                    ConversationMessageRecord.follow_up_candidate_id == follow_up_candidate_id,
                )
            )
            if existing_candidate is not None:
                return existing_candidate, self._turn(existing_candidate.turn_id), existing_candidate.turn_id is None, self._queued_count(conversation.id)
            source = next(
                (
                    candidate
                    for turn in self._session.scalars(
                        select(ConversationTurnRecord).where(
                            ConversationTurnRecord.conversation_id == conversation.id,
                            ConversationTurnRecord.state == "completed",
                        )
                    ).all()
                    for candidate in (turn.follow_up_candidates or [])
                    if candidate.get("candidate_id") == str(follow_up_candidate_id)
                ),
                None,
            )
            if source is None or " ".join(body.split()) != source.get("user_text"):
                raise ConversationError("follow-up candidate is not available")
            body = str(source["user_text"])
            idempotency_key = f"follow-up:{follow_up_candidate_id}"
        if idempotency_key:
            existing = self._session.scalar(select(ConversationMessageRecord).where(ConversationMessageRecord.conversation_id == conversation.id, ConversationMessageRecord.idempotency_key == idempotency_key))
            if existing is not None:
                return existing, self._turn(existing.turn_id), existing.turn_id is None, self._queued_count(conversation.id)
        active = self._active_turn(conversation.id)
        queue_size = self._queued_count(conversation.id)
        if active is not None and queue_size >= self._queue_limit:
            raise ConversationQueueOverflow(queue_size, self._queue_limit)
        now = datetime.now(UTC)
        message = self._message(
            conversation.id,
            None,
            "user",
            body,
            now,
            idempotency_key,
            follow_up_candidate_id=follow_up_candidate_id,
        )
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
        turn.progress_state = "completed"
        turn.current_tool_display_name = None
        turn.provider_run_ref = result.provider_run_ref
        turn.provider_session_ref = result.provider_session_ref
        turn.completed_at = now
        turn.execution_completed_at = now
        if result.usage is not None:
            turn.usage = result.usage
        turn.follow_up_candidates = self._normalize_follow_up_candidates(turn.id, result)
        conversation = self._session.get(ConversationRecord, turn.conversation_id)
        assert conversation is not None
        if result.provider_session_ref:
            self._session.add(ConversationProviderSessionReferenceRecord(conversation_id=conversation.id, provider_session_ref=result.provider_session_ref, recorded_at=now))
        # The provider's final message is authoritative; it confirms (or replaces) the streamed partial body.
        assistant = self._streaming_assistant(turn)
        if assistant is None:
            assistant = self._message(conversation.id, turn.id, "assistant", result.body, now, None, body_state="final")
        else:
            assistant.body = result.body
            assistant.body_state = "final"
        assistant.answer_document = (
            {"version": 1, "elements": result.answer_elements} if result.answer_elements is not None else None
        )
        for invocation in result.tool_invocations:
            self._upsert_tool(turn, invocation, now)
        next_turn = self.drain(conversation)
        if next_turn is not None:
            self._enqueue(next_turn)
        return next_turn

    def fail(self, turn: ConversationTurnRecord, error: ProviderFailure) -> ConversationTurnRecord | None:
        now = datetime.now(UTC)
        cancelled = isinstance(error, ProviderCancelled) or turn.state == "cancelled"
        turn.state = "cancelled" if cancelled else "failed"
        turn.progress_state = turn.state
        turn.current_tool_display_name = None
        turn.provider_run_ref = error.provenance.provider_run_ref or turn.provider_run_ref
        turn.provider_session_ref = error.provenance.provider_session_ref or turn.provider_session_ref
        turn.completed_at = now
        turn.execution_completed_at = now
        turn.normalized_error = None if cancelled else str(error)
        # Partial assistant text stays visible with a terminal body state instead of disappearing.
        assistant = self._streaming_assistant(turn)
        if assistant is not None:
            assistant.body_state = turn.state
        conversation = self._session.get(ConversationRecord, turn.conversation_id)
        assert conversation is not None
        self._session.add(
            ConversationAuditEventRecord(
                conversation_id=conversation.id,
                turn_id=turn.id,
                event_type=f"conversation.turn.{turn.state}",
                payload={"error": str(error)},
                occurred_at=now,
            )
        )
        next_turn = self.drain(conversation)
        if next_turn is not None:
            self._enqueue(next_turn)
        return next_turn

    def cancel(self, turn: ConversationTurnRecord) -> None:
        """DB-first terminal transition; the worker observes it and stops the provider process, then ignores late events."""
        turn.state = "cancelled"
        turn.progress_state = "cancelled"
        turn.current_tool_display_name = None
        turn.completed_at = datetime.now(UTC)
        turn.execution_completed_at = turn.completed_at
        assistant = self._streaming_assistant(turn)
        if assistant is not None:
            assistant.body_state = "cancelled"

    def cancel_active(self, conversation: ConversationRecord, expected_version: int) -> ConversationRecord:
        if conversation.version != expected_version:
            raise ConversationError("conversation version is stale")
        active = self._active_turn(conversation.id)
        if active is None:
            raise ConversationError("conversation has no active turn")
        self.cancel(active)
        conversation.version += 1
        conversation.updated_at = datetime.now(UTC)
        self._session.add(
            ConversationAuditEventRecord(
                conversation_id=conversation.id,
                turn_id=active.id,
                event_type="conversation.turn.cancelled",
                payload={},
                occurred_at=conversation.updated_at,
            )
        )
        return conversation

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
            # 물은 때는 물은 때다. 큐에서 기다리다 달이 바뀌어도 `지난달`의 뜻은 달라지지 않는다.
            asked_at=turn.started_at,
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

    def owner_for_execution(self, execution: ConversationExecution) -> str | None:
        turn = self._session.scalar(
            select(ConversationTurnRecord).where(
                ConversationTurnRecord.id == execution.turn_id,
                ConversationTurnRecord.conversation_id == execution.conversation_id,
                ConversationTurnRecord.execution_id == execution.execution_id,
            )
        )
        if turn is None:
            return None
        conversation = self._session.get(ConversationRecord, turn.conversation_id)
        return conversation.owner_id if conversation else None

    def claim_execution(
        self, execution: ConversationExecution, principal: Principal
    ) -> tuple[ConversationTurnRecord, AiConversationRequest] | None:
        turn = self._execution_turn(execution, lock=True)
        if turn is None or turn.state in {"completed", "failed", "cancelled"}:
            return None
        # The worker obtains a process-held advisory guard before this transition.
        # A `running` row is therefore recoverable only after its prior owner dies
        # and PostgreSQL releases that guard.
        if turn.state == "pending":
            turn.state = "running"
        now = datetime.now(UTC)
        turn.progress_state = "preparing"
        turn.current_tool_display_name = None
        if turn.execution_started_at is None:
            turn.execution_started_at = now
        # The assistant row exists from the first moment of execution so re-entry hydrates partial text and state.
        assistant = self._streaming_assistant(turn)
        if assistant is None:
            self._message(turn.conversation_id, turn.id, "assistant", "", now, None, body_state="streaming")
        elif turn.execution_attempt_count > 0:
            # A transport re-attempt regenerates the whole answer: drop the previous attempt's partial text and tool rows.
            assistant.body = ""
            assistant.body_state = "streaming"
            self._session.execute(delete(ToolInvocationRecord).where(ToolInvocationRecord.turn_id == turn.id))
        try:
            request = self.request_for(turn, principal)
            # This is intentionally after the process-held advisory guard. Queue
            # reads that merely contend with a live worker never consume a
            # provider retry budget.
            turn.execution_attempt_count += 1
            return turn, request
        except ConversationError:
            self.fail(turn, ProviderRequestFailed("Conversation context is no longer authorized"))
            return None

    def fail_execution(self, execution: ConversationExecution, message: str) -> bool:
        turn = self._execution_turn(execution, lock=True)
        if turn is None or turn.state in {"completed", "failed", "cancelled"}:
            return False
        self.fail(turn, ProviderRequestFailed(message))
        return True

    def complete_execution(
        self,
        execution: ConversationExecution,
        result: AiConversationResult,
    ) -> bool:
        turn = self._execution_turn(execution, lock=True)
        if turn is None:
            return False
        if turn.state == "running":
            self.complete(turn, result)
        return turn.state in {"completed", "failed", "cancelled"}

    def fail_or_retry_execution(
        self,
        execution: ConversationExecution,
        max_attempts: int,
        error: ProviderFailure,
    ) -> bool:
        """Returns whether the transport message has reached a terminal state."""
        turn = self._execution_turn(execution, lock=True)
        if turn is None or turn.state in {"completed", "failed", "cancelled"}:
            return True
        if isinstance(error, ProviderResponseInvalid) or turn.execution_attempt_count >= max_attempts:
            self.fail(turn, error)
            return True
        return False

    # ---- live lifecycle projection ------------------------------------------------------------------------------

    def apply_event(self, execution: ConversationExecution, event: AiProviderEvent) -> bool:
        """Persist one observed provider event. Fenced: a turn that is no longer running ignores late events."""
        turn = self._execution_turn(execution, lock=True)
        if turn is None or turn.state != "running":
            return False
        if event.provider_run_ref and not turn.provider_run_ref:
            turn.provider_run_ref = event.provider_run_ref
        if event.provider_session_ref and not turn.provider_session_ref:
            turn.provider_session_ref = event.provider_session_ref
        if event.kind == "turn_started":
            turn.progress_state = "preparing"
        elif event.kind == "item_completed" and event.text:
            assistant = self._streaming_assistant(turn)
            if assistant is None:
                assistant = self._message(turn.conversation_id, turn.id, "assistant", "", event.observed_at, None, body_state="streaming")
            assistant.body = f"{assistant.body}\n\n{event.text}".strip() if assistant.body else event.text
            turn.progress_state = "composing"
        elif event.tool is not None:
            self._upsert_tool(turn, event.tool, event.observed_at)
            if event.tool.state in {"running", "pending"}:
                turn.progress_state = "tool_running"
                turn.current_tool_display_name = event.tool.display_name
            else:
                turn.progress_state = "composing"
                turn.current_tool_display_name = None
        elif event.kind in {"turn_completed", "turn_failed"}:
            if event.usage is not None:
                turn.usage = event.usage
        self._session.flush()
        return True

    def mark_retrying(self, execution: ConversationExecution) -> None:
        """A transport retry after a provider failure: the turn stays running for the user but shows `retrying`."""
        turn = self._execution_turn(execution, lock=True)
        if turn is not None and turn.state == "running":
            turn.progress_state = "retrying"
            turn.current_tool_display_name = None

    def is_cancelled(self, execution: ConversationExecution) -> bool:
        turn = self._execution_turn(execution)
        return turn is None or turn.state == "cancelled"

    def retry_turn(self, conversation: ConversationRecord, failed_turn_id: UUID, actor_id: str) -> ConversationTurnRecord:
        """One-click retry: a new Turn that re-submits the original user fragments and context references.
        The failed/cancelled Turn is preserved; the retry key makes repeated clicks return the same new Turn."""
        failed = self._session.get(ConversationTurnRecord, failed_turn_id)
        if failed is None or failed.conversation_id != conversation.id:
            raise ConversationNotFound("turn was not found")
        if failed.state not in {"failed", "cancelled"}:
            raise ConversationError("only a failed or cancelled turn can be retried")
        retry_key = f"retry:{failed.id}"
        existing = self._session.scalar(select(ConversationTurnRecord).where(ConversationTurnRecord.retry_key == retry_key))
        if existing is not None:
            return existing
        if self._active_turn(conversation.id) is not None:
            raise ConversationError("a turn is already active; wait for it before retrying")
        now = datetime.now(UTC)
        turn = ConversationTurnRecord(
            conversation_id=conversation.id,
            state="pending",
            progress_state="queued",
            provider_run_ref=None,
            provider_session_ref=self.latest_session(conversation),
            started_at=now,
            completed_at=None,
            normalized_error=None,
            retry_of_turn_id=failed.id,
            retry_key=retry_key,
        )
        self._session.add(turn)
        self._session.flush()
        fragments = self._session.scalars(
            select(ConversationMessageRecord)
            .where(ConversationMessageRecord.turn_id == failed.id, ConversationMessageRecord.role == "user")
            .order_by(ConversationMessageRecord.sequence)
        ).all()
        if not fragments:
            raise ConversationError("the failed turn has no user input to retry")
        for fragment in fragments:
            copy = self._message(conversation.id, turn.id, "user", fragment.body, now, None)
            for reference in self._session.scalars(select(ContextReferenceRecord).where(ContextReferenceRecord.message_id == fragment.id)):
                self._session.add(
                    ContextReferenceRecord(
                        conversation_id=conversation.id,
                        turn_id=turn.id,
                        message_id=copy.id,
                        resource_type=reference.resource_type,
                        resource_id=reference.resource_id,
                        resource_version=reference.resource_version,
                        summary=reference.summary,
                        included=reference.included,
                    )
                )
        conversation.version += 1
        conversation.updated_at = now
        self._session.add(
            ConversationAuditEventRecord(
                conversation_id=conversation.id,
                turn_id=turn.id,
                event_type="conversation.turn.retried",
                payload={"retry_of_turn_id": str(failed.id), "actor_id": actor_id},
                occurred_at=now,
            )
        )
        self._enqueue(turn)
        return turn

    def _streaming_assistant(self, turn: ConversationTurnRecord) -> ConversationMessageRecord | None:
        return self._session.scalar(
            select(ConversationMessageRecord)
            .where(ConversationMessageRecord.turn_id == turn.id, ConversationMessageRecord.role == "assistant")
            .order_by(ConversationMessageRecord.sequence.desc())
        )

    def _upsert_tool(self, turn: ConversationTurnRecord, invocation: AiToolInvocation, observed_at: datetime) -> ToolInvocationRecord:
        row = None
        if invocation.provider_call_id:
            row = self._session.scalar(
                select(ToolInvocationRecord).where(ToolInvocationRecord.turn_id == turn.id, ToolInvocationRecord.provider_call_id == invocation.provider_call_id)
            )
        if row is None:
            sequence = int(self._session.scalar(select(func.coalesce(func.max(ToolInvocationRecord.sequence), 0)).where(ToolInvocationRecord.turn_id == turn.id)) or 0) + 1
            row = ToolInvocationRecord(
                turn_id=turn.id, sequence=sequence, provider_call_id=invocation.provider_call_id, tool_name=invocation.tool_name,
                display_name=invocation.display_name, input_summary=invocation.input_summary, state=invocation.state,
                started_at=invocation.started_at, completed_at=None,
            )
            self._session.add(row)
        row.tool_name = invocation.tool_name
        row.display_name = invocation.display_name
        row.input_summary = invocation.input_summary
        row.state = invocation.state
        row.result_summary = invocation.result_summary
        row.error_summary = invocation.error_summary
        row.target_resource_id = invocation.target_resource_id
        row.target_resource_version = invocation.target_resource_version
        row.audit_ref = invocation.audit_ref
        if invocation.started_at is not None and row.started_at is None:
            row.started_at = invocation.started_at
        if invocation.state in {"completed", "failed", "denied"}:
            row.completed_at = row.completed_at or invocation.completed_at or observed_at
            # Observed latency only: never estimated when the start was not seen.
            row.latency_ms = invocation.latency_ms if invocation.latency_ms is not None else (
                int((row.completed_at - row.started_at).total_seconds() * 1000) if row.started_at is not None and row.completed_at is not None else None
            )
        self._session.flush()
        return row

    def latest_session(self, conversation: ConversationRecord) -> str | None:
        return self._session.scalar(select(ConversationProviderSessionReferenceRecord.provider_session_ref).where(ConversationProviderSessionReferenceRecord.conversation_id == conversation.id).order_by(ConversationProviderSessionReferenceRecord.recorded_at.desc(), ConversationProviderSessionReferenceRecord.id.desc()))

    def view(
        self,
        conversation: ConversationRecord,
        *,
        include_actions: bool = False,
        principal: Any = None,
        message_limit: int | None = None,
        before_sequence: int | None = None,
        list_mode: bool = False,
    ) -> dict[str, Any]:
        # 대화가 쌓일수록 매번 전체를 다시 읽지 않는다 — 창(기본 최근 `message_limit`개)만 열고, 스크롤을
        # 올리면 `before_sequence` 커서로 그 앞을 더 연다. 요약용 필드는 창 크기와 무관하게 항상 정확하다:
        # 목록 미리보기·검색·발화 수가 잘려 보이는 창 하나에 좌우되지 않아야 하기 때문이다.
        # `list_mode`는 그 요약 필드만 필요한 호출(대화 목록, 검색 범위 조회)을 위한 것이다 — 메시지 창과
        # 그것이 가리키는 turn/tool/graph/answer_resource/material_evidence를 아예 조회하지 않는다. 그
        # 리소스들을 여는 것은 이 창의 목적이 아니라 열람한 대화 하나를 상세히 볼 때의 목적이기 때문이다.
        has_more_messages = False
        if list_mode:
            messages = []
        else:
            message_query = select(ConversationMessageRecord).where(ConversationMessageRecord.conversation_id == conversation.id)
            if before_sequence is not None:
                message_query = message_query.where(ConversationMessageRecord.sequence < before_sequence)
            if message_limit is not None:
                fetched = list(self._session.scalars(
                    message_query.order_by(ConversationMessageRecord.sequence.desc()).limit(message_limit + 1)
                ))
                has_more_messages = len(fetched) > message_limit
                messages = list(reversed(fetched[:message_limit]))
            else:
                messages = list(self._session.scalars(message_query.order_by(ConversationMessageRecord.sequence)))

        first_user_message = self._session.scalar(
            select(ConversationMessageRecord.body)
            .where(ConversationMessageRecord.conversation_id == conversation.id, ConversationMessageRecord.role == "user")
            .order_by(ConversationMessageRecord.sequence)
            .limit(1)
        )
        user_message_count = int(self._session.scalar(
            select(func.count()).select_from(ConversationMessageRecord)
            .where(ConversationMessageRecord.conversation_id == conversation.id, ConversationMessageRecord.role == "user")
        ) or 0)
        has_final_answer = bool(self._session.scalar(
            select(ConversationMessageRecord.id)
            .where(
                ConversationMessageRecord.conversation_id == conversation.id,
                ConversationMessageRecord.role == "assistant",
                ConversationMessageRecord.body_state == "final",
                func.length(func.trim(ConversationMessageRecord.body)) > 0,
            )
            .limit(1)
        ))
        # Also window-independent and cheap: a list row's "발화 N · 대기열/상태" summary needs only these two
        # facts, never the messages/turns arrays themselves — so a list projection can skip fetching either.
        queued_message_count = int(self._session.scalar(
            select(func.count()).select_from(ConversationMessageRecord)
            .where(
                ConversationMessageRecord.conversation_id == conversation.id,
                ConversationMessageRecord.role == "user",
                ConversationMessageRecord.turn_id.is_(None),
            )
        ) or 0)
        latest_turn_state = self._session.scalar(
            select(ConversationTurnRecord.state)
            .where(ConversationTurnRecord.conversation_id == conversation.id)
            .order_by(ConversationTurnRecord.started_at.desc())
            .limit(1)
        )

        action_repository = self._actions
        if list_mode:
            # A list row never opens a turn's tool calls, graph steps, answer resources, or material evidence —
            # that is what selecting the conversation is for. Fetching them per row is what made the list itself
            # slow (one full detail projection per row, live-reauthorizing every citation on every listing).
            turns: list[Any] = []
            refs: list[Any] = []
            tools: list[Any] = []
            actions: list[Any] = []
            graph_steps: list[Any] = []
            answer_resources: list[Any] = []
            material_evidence_rows: list[Any] = []
        else:
            # A paginated window only needs the turns/tool calls/graph steps/answer resources/context refs its own
            # messages can point at — the rest belongs to a message window that was not asked for.
            turn_ids = {message.turn_id for message in messages if message.turn_id is not None}
            message_ids = {message.id for message in messages}
            scoped_to_window = message_limit is not None
            window_turn_ids = turn_ids if scoped_to_window else None
            window_message_ids = message_ids if scoped_to_window else None

            turns_query = _scope_to_window(
                select(ConversationTurnRecord).where(ConversationTurnRecord.conversation_id == conversation.id),
                ConversationTurnRecord.id, window_turn_ids,
            )
            turns = self._session.scalars(turns_query.order_by(ConversationTurnRecord.started_at, ConversationTurnRecord.id)).all()

            refs_query = _scope_to_window(
                select(ContextReferenceRecord).where(ContextReferenceRecord.conversation_id == conversation.id),
                ContextReferenceRecord.message_id, window_message_ids,
            )
            refs = self._session.scalars(refs_query.order_by(ContextReferenceRecord.message_id, ContextReferenceRecord.id)).all()

            tools_query = _scope_to_window(
                select(ToolInvocationRecord).join(ConversationTurnRecord).where(ConversationTurnRecord.conversation_id == conversation.id),
                ConversationTurnRecord.id, window_turn_ids,
            )
            tools = self._session.scalars(tools_query.order_by(ConversationTurnRecord.started_at, ToolInvocationRecord.sequence)).all()
            actions = (
                self._session.scalars(
                    select(ActionItemRecord)
                    .where(ActionItemRecord.conversation_id == conversation.id)
                    .order_by(ActionItemRecord.created_at, ActionItemRecord.id)
                ).all()
                if include_actions
                else []
            )
            graph_steps_query = _scope_to_window(
                select(ConversationGraphReceiptRecord).where(ConversationGraphReceiptRecord.conversation_id == conversation.id),
                ConversationGraphReceiptRecord.turn_id, window_turn_ids,
            )
            graph_steps = self._session.scalars(
                graph_steps_query.order_by(ConversationGraphReceiptRecord.observed_at, ConversationGraphReceiptRecord.sequence)
            ).all()
            answer_resources_query = _scope_to_window(
                select(ConversationAnswerResourceRecord).where(ConversationAnswerResourceRecord.conversation_id == conversation.id),
                ConversationAnswerResourceRecord.turn_id, window_turn_ids,
            )
            answer_resources = self._session.scalars(
                answer_resources_query
                .order_by(ConversationAnswerResourceRecord.observed_at, ConversationAnswerResourceRecord.sequence)
            ).all()
            material_evidence_rows = self._session.scalars(_scope_to_window(
                select(ConversationContentEvidenceRecord).where(ConversationContentEvidenceRecord.conversation_id == conversation.id),
                ConversationContentEvidenceRecord.turn_id, window_turn_ids,
            ).order_by(ConversationContentEvidenceRecord.recorded_at, ConversationContentEvidenceRecord.rank)).all()
        selected_candidates = {
            str(message.follow_up_candidate_id): str(message.id)
            for message in messages
            if message.follow_up_candidate_id is not None
        }
        return {
            # Canonical ids a turn read and named. They carry no title here: the application asks the owning module
            # for that at read time, so a reference someone may no longer open simply is not there.
            "answer_resources": [
                {
                    "reference_id": str(item.id),
                    "turn_id": str(item.turn_id),
                    "sequence": int(item.sequence),
                    "resource_type": item.resource_type,
                    "resource_id": item.resource_id,
                    "resource_version": item.resource_version,
                    "source_contexts": item.source_contexts,
                    "integrity_ref": item.integrity_ref,
                    "source_locator": item.source_locator,
                }
                for item in answer_resources
            ],
            # Where each turn actually walked: observed steps only, in the order they were observed.
            "graph_receipts": [
                {
                    "receipt_id": str(item.id),
                    "turn_id": str(item.turn_id),
                    "sequence": int(item.sequence),
                    "kind": item.kind,
                    "node_ref": item.node_ref,
                    "node_title": item.node_title,
                    "edge_kind": item.edge_kind,
                    "from_ref": item.from_ref,
                    "from_title": item.from_title,
                    "to_ref": item.to_ref,
                    "to_title": item.to_title,
                    "source_contexts": item.source_contexts,
                    "integrity_ref": item.integrity_ref,
                    "observed_at": item.observed_at.isoformat(),
                }
                for item in graph_steps
            ],
            "material_evidence": [
                {"evidence_id": str(item.id), "turn_id": str(item.turn_id),
                 "material_id": str(item.attachment_id), "attachment_id": str(item.attachment_id), "chunk_id": str(item.chunk_id),
                 "name": item.name, "integrity_ref": item.integrity_ref, "page": item.page, "excerpt": item.excerpt,
                 "source_contexts": item.source_contexts, "source_locator": item.source_locator, "header_context": item.header_context,
                 "extraction": item.extraction_snapshot, "query": item.query, "rank": item.rank, "recorded_at": item.recorded_at.isoformat()}
                for item in material_evidence_rows
            ],
            "has_more_messages": has_more_messages,
            # 목록 미리보기·검색·발화 수는 위 메시지 창이 잘려 있어도 항상 전체 대화 기준으로 정확하다.
            "first_user_message_excerpt": (first_user_message or "").strip()[:300] or None,
            "user_message_count": user_message_count,
            "has_final_answer": has_final_answer,
            "queued_message_count": queued_message_count,
            "latest_turn_state": latest_turn_state,
            "conversation_id": str(conversation.id),
            "title": conversation.title,
            "version": conversation.version,
            "messages": [
                {
                    "message_id": str(message.id),
                    "turn_id": str(message.turn_id) if message.turn_id else None,
                    "role": message.role,
                    "body": message.body,
                    "answer_document": message.answer_document,
                    "sequence": message.sequence,
                    "state": "queued"
                    if message.role == "user" and message.turn_id is None
                    else "accepted",
                    "body_state": message.body_state,
                    "idempotency_key": message.idempotency_key,
                    "follow_up_candidate_id": str(message.follow_up_candidate_id) if message.follow_up_candidate_id else None,
                    "created_at": message.created_at.isoformat(),
                }
                for message in messages
            ],
            "turns": [
                {
                    "turn_id": str(turn.id),
                    "state": turn.state,
                    "progress_state": turn.progress_state,
                    "current_tool_display_name": turn.current_tool_display_name,
                    "attempt": turn.execution_attempt_count,
                    "queued_at": turn.started_at.isoformat(),
                    "execution_started_at": turn.execution_started_at.isoformat() if turn.execution_started_at else None,
                    "execution_completed_at": turn.execution_completed_at.isoformat() if turn.execution_completed_at else None,
                    # Observed only: queue wait needs an execution start, total run time needs a terminal event.
                    "queue_wait_ms": int((turn.execution_started_at - turn.started_at).total_seconds() * 1000) if turn.execution_started_at else None,
                    "run_ms": int((turn.execution_completed_at - turn.execution_started_at).total_seconds() * 1000)
                    if turn.execution_started_at and turn.execution_completed_at
                    else None,
                    "retry_of_turn_id": str(turn.retry_of_turn_id) if turn.retry_of_turn_id else None,
                    "usage": turn.usage,
                    "provider_run_ref": turn.provider_run_ref,
                    "provider_session_ref": turn.provider_session_ref,
                    "error": turn.normalized_error,
                    "follow_up_candidates": [
                        {
                            **candidate,
                            "selected_message_id": selected_candidates.get(str(candidate["candidate_id"])),
                        }
                        for candidate in (turn.follow_up_candidates or [])
                    ] if turn.state == "completed" else [],
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
                    "started_at": tool.started_at.isoformat() if tool.started_at else None,
                    "completed_at": tool.completed_at.isoformat() if tool.completed_at else None,
                    "target_resource_id": tool.target_resource_id,
                    "target_resource_version": tool.target_resource_version,
                    "audit_ref": tool.audit_ref,
                }
                for tool in tools
            ],
            # Same canonical row and presentation as GET /api/actions: the chat card and the decision inbox never diverge.
            "actions": [action_repository.view(action, principal) for action in actions] if actions else [],
        }

    def _active_turn(self, conversation_id: UUID) -> ConversationTurnRecord | None:
        return self._session.scalar(select(ConversationTurnRecord).where(ConversationTurnRecord.conversation_id == conversation_id, ConversationTurnRecord.state.in_(("pending", "running"))))

    def _turn(self, turn_id: UUID | None) -> ConversationTurnRecord | None:
        return self._session.get(ConversationTurnRecord, turn_id) if turn_id else None

    def _execution_turn(
        self,
        execution: ConversationExecution,
        *,
        lock: bool = False,
    ) -> ConversationTurnRecord | None:
        if lock:
            # Every command that can add a message or drain the queue takes the aggregate lock first. Without this
            # shared order, an HTTP sender can hold Conversation while a worker holds Turn and both can then wait on
            # the other's message/FK insert (or race to the same per-conversation sequence).
            conversation = self._session.scalar(
                select(ConversationRecord)
                .where(ConversationRecord.id == execution.conversation_id)
                .with_for_update()
            )
            if conversation is None:
                return None
        statement = select(ConversationTurnRecord).where(
            ConversationTurnRecord.id == execution.turn_id,
            ConversationTurnRecord.conversation_id == execution.conversation_id,
            ConversationTurnRecord.execution_id == execution.execution_id,
        )
        return self._session.scalar(statement.with_for_update().execution_options(populate_existing=True) if lock else statement)

    def _queued_count(self, conversation_id: UUID) -> int:
        return int(self._session.scalar(select(func.count()).select_from(ConversationMessageRecord).where(ConversationMessageRecord.conversation_id == conversation_id, ConversationMessageRecord.role == "user", ConversationMessageRecord.turn_id.is_(None))) or 0)

    @staticmethod
    def _normalize_follow_up_candidates(turn_id: UUID, result: AiConversationResult) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        seen_labels: set[str] = set()
        seen_texts: set[str] = set()
        for candidate in result.follow_up_candidates:
            label = " ".join(candidate.label.split())
            user_text = " ".join(candidate.user_text.split())
            label_key = label.casefold()
            text_key = user_text.casefold()
            if not label or not user_text or len(label) > 120 or len(user_text) > 1000:
                continue
            if label_key in seen_labels or text_key in seen_texts:
                continue
            seen_labels.add(label_key)
            seen_texts.add(text_key)
            normalized.append(
                {
                    "candidate_id": str(uuid4()),
                    "source_turn_id": str(turn_id),
                    "label": label,
                    "user_text": user_text,
                }
            )
            if len(normalized) == 3:
                break
        return normalized if len(normalized) >= 2 else []

    def _message(self, conversation_id: UUID, turn_id: UUID | None, role: str, body: str, now: datetime, idempotency_key: str | None, *, body_state: str = "final", follow_up_candidate_id: UUID | None = None) -> ConversationMessageRecord:
        sequence = int(self._session.scalar(select(func.coalesce(func.max(ConversationMessageRecord.sequence), 0)).where(ConversationMessageRecord.conversation_id == conversation_id)) or 0) + 1
        message = ConversationMessageRecord(conversation_id=conversation_id, turn_id=turn_id, sequence=sequence, role=role, body=body, idempotency_key=idempotency_key, follow_up_candidate_id=follow_up_candidate_id, body_state=body_state, created_at=now)
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


class SqlAlchemyConversationExecutionGuard:
    """Process-held PostgreSQL advisory guard for one provider execution.

    Queue visibility is a transport concern.  This guard only prevents two live
    workers from invoking a provider for the same durable turn after a visibility
    timeout.  PostgreSQL releases it automatically when a crashed worker loses its
    database connection, allowing a later redelivery to recover the running turn.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @contextmanager
    def hold(self, execution: ConversationExecution):
        if self._engine.dialect.name != "postgresql":
            yield True
            return
        lock_key = int.from_bytes(
            hashlib.blake2b(str(execution.execution_id).encode(), digest_size=8).digest(),
            byteorder="big",
            signed=True,
        )
        # Advisory locks are session-scoped.  Use a dedicated AUTOCOMMIT
        # connection so holding it across Codex never leaves an idle database
        # transaction that a timeout could abort.  If this connection dies,
        # PostgreSQL releases the lock and a redelivery may resume; downstream
        # Action/causation idempotency remains the split-brain effect boundary.
        with self._engine.execution_options(isolation_level="AUTOCOMMIT").connect() as connection:
            acquired = bool(
                connection.execute(
                    text("SELECT pg_try_advisory_lock(:lock_key)"),
                    {"lock_key": lock_key},
                ).scalar_one()
            )
            try:
                yield acquired
            finally:
                if acquired and not connection.closed:
                    try:
                        connection.execute(
                            text("SELECT pg_advisory_unlock(:lock_key)"),
                            {"lock_key": lock_key},
                        )
                    except DBAPIError:
                        # A broken guard connection has already lost its server
                        # lock. Do not mask the provider's terminal persistence.
                        pass


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
        # Holding the work is the active assignment, never a column on the Task.
        held = record is not None and self._session.scalar(
            select(TaskAssignmentRecord).where(
                TaskAssignmentRecord.task_id == record.id,
                TaskAssignmentRecord.assignee_id == str(principal.id),
                TaskAssignmentRecord.status == "active",
            )
        )
        if record is None or not held:
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
