"""Provider-neutral application contract for structured and conversational AI calls."""
from __future__ import annotations

from dataclasses import dataclass
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
    latency_ms: int | None
    target_resource_id: str | None = None
    target_resource_version: str | None = None
    audit_ref: str | None = None


@dataclass(frozen=True, slots=True)
class AiConversationResult:
    provider_run_ref: str | None
    provider_session_ref: str | None
    body: str
    tool_invocations: list[AiToolInvocation]


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
    def converse(self, request: AiConversationRequest) -> AiConversationResult: ...


class ProviderFailure(RuntimeError):
    """Normalized provider failure; never include credentials or the raw prompt."""

    def __init__(self, message: str, provenance: AiProviderProvenance | None = None) -> None:
        super().__init__(message)
        self.provenance = provenance or AiProviderProvenance()


class ProviderUnavailable(ProviderFailure):
    pass


class ProviderRequestFailed(ProviderFailure):
    pass
