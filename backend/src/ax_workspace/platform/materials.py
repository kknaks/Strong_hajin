"""MaterialStorage adapters. Local directory now; an Azure Blob container adapter implements the same port later."""
from __future__ import annotations

from pathlib import Path
import re

# tasks/<task>/<file>, work_requests/<request>/comments/<file>, work_requests/<request>/evidence/<file>
_SAFE_KEY = re.compile(r"^(?:tasks/[0-9a-f-]{36}|work_requests/[0-9a-f-]{36}/(?:comments|evidence))/[0-9a-f-]{36}$")


class LocalDirectoryMaterialStorage:
    """Stores material bytes under a root directory; keys are application-generated and validated."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if not _SAFE_KEY.match(key):
            raise ValueError("invalid material storage key")
        path = (self._root / key).resolve()
        if self._root not in path.parents:
            raise ValueError("material storage key escapes the root")
        return path

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return path.read_bytes()
