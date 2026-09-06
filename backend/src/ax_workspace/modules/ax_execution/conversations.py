"""AX Conversation public commands and durable queue contracts."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from ax_workspace.modules.ax_execution.actions import action_commands
from ax_workspace.modules.organization_access.domain import ACTION_DECIDE, ACTION_READ, Principal


class ConversationError(Exception):
    pass


class ConversationQueueOverflow(ConversationError):
    def __init__(self, queue_size: int, limit: int) -> None:
        super().__init__("conversation queue is full")
        self.queue_size = queue_size
        self.limit = limit


@dataclass(frozen=True, slots=True)
class ConversationContextReferenceInput:
    """A client-selected reference whose summary is resolved server-side."""

    resource_type: str
    resource_id: str
    resource_version: int
    included: bool


class ConversationContextResolver(Protocol):
    def resolve(
        self,
        principal: Principal,
        references: list[ConversationContextReferenceInput],
    ) -> list[dict[str, str | bool]]: ...


@dataclass(frozen=True, slots=True)
class ConversationExecution:
    turn_id: UUID
    conversation_id: UUID
    execution_id: UUID


@dataclass(frozen=True, slots=True)
class ConversationQueueMessage:
    message_id: str
    lease_token: str
    read_count: int
    execution: ConversationExecution


class ConversationExecutionQueue(Protocol):
    """Transport port. Lease/retry state is not Conversation domain state; every write is fenced by the lease token."""

    def enqueue(self, execution: ConversationExecution) -> None: ...

    def read_group_heads(
        self,
        *,
        visibility_timeout_seconds: int,
        quantity: int,
    ) -> list[ConversationQueueMessage]: ...

    def archive(self, message_id: str, lease_token: str) -> bool: ...

    def release(self, message_id: str, lease_token: str, *, delay_seconds: int, error: str | None = None) -> bool: ...

    def extend_visibility(self, message_id: str, lease_token: str, visibility_timeout_seconds: int) -> bool: ...


class ConversationRepository(Protocol):
    def create(self, owner_id: str, title: str) -> Any: ...
    def conversation(self, conversation_id: UUID, owner_id: str, *, lock: bool = False) -> Any | None: ...
    def list_for(self, owner_id: str) -> list[Any]: ...
    def accept_fragment(self, conversation: Any, body: str, context: list[dict[str, str | bool]], idempotency_key: str | None) -> tuple[Any, Any | None, bool, int]: ...
    def cancel_active(self, conversation: Any, expected_version: int) -> Any: ...
    def retry_turn(self, conversation: Any, failed_turn_id: UUID, actor_id: str) -> Any: ...
    def view(self, conversation: Any, *, include_actions: bool = False, principal: Any = None) -> dict[str, Any]: ...


class AnswerResourcePort(Protocol):
    """Reading back what a turn named, through the module that owns each thing.

    It is asked again every time the conversation is read: a reference is a canonical id, and whether it may be shown
    — and under what title — is the owning module's answer now, not what was true when the turn ran.
    """

    def resolve(self, principal: Principal, references: list[dict[str, Any]]) -> list[dict[str, Any]]: ...


class ConversationApplication:
    """API-facing command/query service. It never invokes a provider."""

    def __init__(
        self,
        repository: ConversationRepository,
        context_resolver: ConversationContextResolver,
        answer_resources: AnswerResourcePort | None = None,
    ) -> None:
        self._repository = repository
        self._context_resolver = context_resolver
        self._answer_resources = answer_resources

    def create(self, principal: Principal, title: str = "새 대화") -> dict[str, Any]:
        return self._view(principal, self._repository.create(str(principal.id), title.strip() or "새 대화"))

    def list(self, principal: Principal) -> list[dict[str, Any]]:
        return [self._view(principal, item) for item in self._repository.list_for(str(principal.id))]

    def get(self, principal: Principal, conversation_id: UUID) -> dict[str, Any]:
        return self._view(principal, self._owned(principal, conversation_id))

    def accept_message(
        self,
        principal: Principal,
        conversation_id: UUID,
        body: str,
        context: list[ConversationContextReferenceInput],
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        if not body.strip():
            raise ConversationError("message is required")
        resolved_context = self._context_resolver.resolve(principal, context)
        conversation = self._owned(principal, conversation_id, lock=True)
        message, turn, queued, queue_size = self._repository.accept_fragment(
            conversation,
            body.strip(),
            resolved_context,
            idempotency_key,
        )
        return {
            "conversation_id": str(conversation.id),
            "message_id": str(message.id),
            "turn_id": str(turn.id) if turn else None,
            "queued": queued,
            "queue_size": queue_size,
        }

    def cancel(self, principal: Principal, conversation_id: UUID, expected_version: int) -> dict[str, Any]:
        return self._view(
            principal,
            self._repository.cancel_active(self._owned(principal, conversation_id, lock=True), expected_version)
        )

    def retry(self, principal: Principal, conversation_id: UUID, turn_id: UUID) -> dict[str, Any]:
        """Re-submit a failed/cancelled turn as a new Turn with the original fragments and context (idempotent)."""
        conversation = self._owned(principal, conversation_id, lock=True)
        turn = self._repository.retry_turn(conversation, turn_id, str(principal.id))
        return {"conversation_id": str(conversation.id), "turn_id": str(turn.id), "retry_of_turn_id": str(turn.retry_of_turn_id)}

    def _view(self, principal: Principal, conversation: Any) -> dict[str, Any]:
        view = self._repository.view(conversation, include_actions=ACTION_READ in principal.capabilities, principal=principal)
        # Approval commands come from the ledger + the caller's current capability, never inferred by the client.
        for action in view.get("actions", []):
            action["commands"] = action_commands(action.get("state"), ACTION_DECIDE in principal.capabilities)
        references = view.get("answer_resources") or []
        view["answer_resources"] = (
            self._answer_resources.resolve(principal, references) if self._answer_resources is not None else []
        )
        return view

    def _owned(self, principal: Principal, conversation_id: UUID, *, lock: bool = False) -> Any:
        conversation = self._repository.conversation(conversation_id, str(principal.id), lock=lock)
        if conversation is None:
            raise ConversationError("conversation was not found")
        return conversation
