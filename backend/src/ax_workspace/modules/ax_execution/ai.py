"""Provider-neutral application contract for structured and conversational AI calls."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class AiGenerationRequest:
    prompt: str
    output_schema: dict[str, Any]
    profile: "AiProviderProfileRequest | None" = None


@dataclass(frozen=True, slots=True)
class AiProviderProfileRequest:
    """A provider-neutral requested profile; adapters apply their approved policy."""

    model: str | None = None
    service_tier: str | None = None
    reasoning_effort: str | None = None
    timeout_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class AiGeneration:
    provider_run_ref: str | None
    provider_session_ref: str | None
    body: str
    requested_model: str
    observed_model: str | None
    requested_tier: str
    observed_tier: str | None
    latency_ms: int
    usage: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class AiDelegatedToolContext:
    """Server-bound delegated identity for tools; it is never prompt content."""

    principal_id: str
    causation_id: str


@dataclass(frozen=True, slots=True)
class AiConversationRequest:
    prompt: str
    provider_session_ref: str | None
    context_references: list[dict[str, str]]
    delegated_tool_context: AiDelegatedToolContext


@dataclass(frozen=True, slots=True)
class AiToolInvocation:
    provider_call_id: str | None
    tool_name: str
    display_name: str
    input_summary: str
    state: str
    result_summary: str | None
    error_summary: str | None
    latency_ms: int | None  # observed wall-clock between item.started and its terminal event; None when a start was never observed
    target_resource_id: str | None = None
    target_resource_version: str | None = None
    audit_ref: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AiConversationResult:
    provider_run_ref: str | None
    provider_session_ref: str | None
    body: str
    tool_invocations: list[AiToolInvocation]
    usage: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class AiProviderEvent:
    """One observed provider lifecycle event, normalized. No token deltas or raw reasoning are ever synthesized:
    an adapter emits only what the provider actually reported, stamped with the time SCAX observed it."""

    kind: str  # turn_started | item_started | item_updated | item_completed | turn_completed | turn_failed | error
    observed_at: datetime
    item_id: str | None = None
    item_type: str | None = None  # agent_message | mcp_tool_call | command_execution | reasoning | ...
    text: str | None = None  # completed agent_message text
    tool: AiToolInvocation | None = None  # for tool-like items, already redacted
    provider_run_ref: str | None = None
    provider_session_ref: str | None = None
    usage: dict[str, Any] | None = None
    error_message: str | None = None


class AiEventSink(Protocol):
    """Receives provider events during execution; implementations persist them in short transactions."""

    def accept(self, event: AiProviderEvent) -> None: ...


class CancelToken(Protocol):
    def is_set(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class AiProviderProvenance:
    provider_run_ref: str | None = None
    provider_session_ref: str | None = None
    requested_model: str | None = None
    observed_model: str | None = None
    requested_tier: str | None = None
    observed_tier: str | None = None
    latency_ms: int | None = None
    usage: dict[str, Any] | None = None


class AiProvider(Protocol):
    def generate(self, request: AiGenerationRequest) -> AiGeneration: ...

    def converse(
        self,
        request: AiConversationRequest,
        *,
        sink: AiEventSink | None = None,
        cancel: CancelToken | None = None,
    ) -> AiConversationResult:
        """Run one turn. When a sink is given, observed events are delivered while the turn runs; when the cancel token
        is set, the adapter stops the underlying execution and raises ProviderCancelled."""
        ...


class ProviderFailure(RuntimeError):
    """Normalized provider failure; never include credentials or the raw prompt."""

    def __init__(self, message: str, provenance: AiProviderProvenance | None = None) -> None:
        super().__init__(message)
        self.provenance = provenance or AiProviderProvenance()


class ProviderUnavailable(ProviderFailure):
    pass


class ProviderRequestFailed(ProviderFailure):
    pass


class ProviderCancelled(ProviderFailure):
    """The execution was stopped because the turn was cancelled; not a provider fault."""
