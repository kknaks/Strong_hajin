"""Immutable Meeting transcript contracts.

Realtime partial text is deliberately absent here: it is an ephemeral UI
projection.  This contract starts only with a provider-finalised segment, the
source of record for refinement and summary.
"""
from __future__ import annotations

from dataclasses import dataclass


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
