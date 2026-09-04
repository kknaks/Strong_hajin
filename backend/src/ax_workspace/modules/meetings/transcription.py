"""Immutable Meeting transcript contracts.

Realtime partial text is deliberately absent here: it is an ephemeral UI
projection.  This contract starts only with a provider-finalised segment, the
source of record for refinement and summary.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class FinalTranscriptSegment:
    source_segment_key: str
    start_ms: int
    end_ms: int
    text: str
    speaker_label: str | None = None

    def validate(self) -> None:
        if not self.source_segment_key.strip():
            raise ValueError("source segment key is required")
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("transcript segment offsets are invalid")
        if not self.text.strip():
            raise ValueError("transcript segment text is required")


@dataclass(frozen=True, slots=True)
class RealtimeTranscriptionCredential:
    temporary_key: str
    expires_at: datetime
    client_reference_id: str
    websocket_url: str
    model: str
    enable_speaker_diarization: bool


@dataclass(frozen=True, slots=True)
class FinalTranscriptionResult:
    provider_reference: str
    provider_file_ref: str | None
    segments: tuple[FinalTranscriptSegment, ...]
    provider_request_id: str | None = None
    cleanup_warnings: tuple[str, ...] = ()


class TranscriptionFailure(RuntimeError):
    """Normalized STT failure; it never exposes a provider body or a credential."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool,
        provider_reference: str | None = None,
        provider_request_id: str | None = None,
        cleanup_warnings: tuple[str, ...] = (),
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.provider_reference = provider_reference
        self.provider_request_id = provider_request_id
        self.cleanup_warnings = cleanup_warnings


class RealtimeTranscriptionKeyIssuer(Protocol):
    def issue(self, *, client_reference_id: str, max_session_duration_seconds: int) -> RealtimeTranscriptionCredential: ...


class FinalTranscriber(Protocol):
    def transcribe(
        self,
        *,
        data: bytes,
        original_name: str,
        content_type: str,
        client_reference_id: str,
    ) -> FinalTranscriptionResult: ...
