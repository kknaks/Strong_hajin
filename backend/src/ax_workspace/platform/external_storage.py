"""카톡 첨부·프로필 이미지 저장본 — hostPath(`AX_EXTERNAL_CHANNEL_STORAGE_DIR`) 아래 (SPEC-008 §5 저장 · D-29·D-30).

`LocalDirectoryMaterialStorage`(`platform/materials.py`) 와 같은 방식이다 — 키는 애플리케이션이 만들고 여기서 모양을
검사한다. DB 는 키(경로)만 갖는다. 보존·파기 기간이 없어 카톡 저장본은 지우지 않는다(프로필 이미지 바꾸기·삭제만 지운다).
"""
from __future__ import annotations

import os
from pathlib import Path
import re

# kakao/<integration uuid>/<aid sha256 hex> · profiles/<member id>/<uuid>
_SAFE_KEY = re.compile(r"^(?:kakao/[0-9a-f-]{36}/[0-9a-f]{64}|profiles/[A-Za-z0-9_.-]{1,100}/[0-9a-f-]{36})$")


class LocalDirectoryExternalStorage:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def _path(self, key: str) -> Path:
        if not _SAFE_KEY.match(key) or ".." in key:
            raise ValueError("invalid external channel storage key")
        path = (self._root / key).resolve()
        if self._root not in path.parents:
            raise ValueError("external channel storage key escapes the root")
        return path

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_bytes(data)
        os.replace(temporary, path)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return path.read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)
