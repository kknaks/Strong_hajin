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
    cli_run_ref: str | None
    cli_thread_ref: str | None
    body: str
    requested_model: str
    observed_model: str | None
    requested_tier: str
    observed_tier: str | None
    latency_ms: int
    usage: dict[str, Any] | None


class AiProvider(Protocol):
    def generate(self, request: AiGenerationRequest) -> AiGeneration: ...


class ProviderFailure(RuntimeError):
    """Normalized provider failure; never include credentials or the raw prompt."""


class ProviderUnavailable(ProviderFailure):
    pass


class ProviderRequestFailed(ProviderFailure):
    pass
