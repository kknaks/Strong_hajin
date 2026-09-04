"""Ports for Meeting recording objects.

Meeting owns recording identity and state. Storage is deliberately a port: the
local directory adapter is useful for the first production-path milestone, but
neither the Meeting aggregate nor its database tables treat a filesystem path as
a browser capability.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class StoredRecording:
    storage_key: str
    content_type: str
    original_name: str
    size_bytes: int
    sha256: str


class RecordingStorage(Protocol):
    def put(
        self,
        *,
        recording_id: str,
        original_name: str,
        content_type: str,
        data: bytes,
    ) -> StoredRecording: ...
