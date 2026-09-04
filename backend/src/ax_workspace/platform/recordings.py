"""Recording storage adapters."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from ax_workspace.modules.meetings.recordings import StoredRecording


_RECORDING_ID = re.compile(r"^[0-9a-f-]{36}$")


class LocalDirectoryRecordingStorage:
    """Stores bytes under an opaque application-owned key."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def put(
        self,
        *,
        recording_id: str,
        original_name: str,
        content_type: str,
        data: bytes,
    ) -> StoredRecording:
        if not _RECORDING_ID.match(recording_id):
            raise ValueError("invalid recording identifier")
        if not data:
            raise ValueError("recording audio is required")
        if len(data) > 256 * 1024 * 1024:
            raise ValueError("recording audio exceeds the 256 MiB local limit")
        key = f"meetings/recordings/{recording_id}"
        path = (self._root / key).resolve()
        if self._root not in path.parents:
            raise ValueError("recording storage key escapes the root")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".uploading")
        temporary.write_bytes(data)
        temporary.replace(path)
        return StoredRecording(
            storage_key=key,
            content_type=content_type or "application/octet-stream",
            original_name=Path(original_name or "recording").name[:300],
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )
