"""오디오 원본 저장 어댑터 — 중계 경로가 청크를 **덧붙이는** 자리.

원본은 회의당 파일 하나이고 스트림이 도는 동안 계속 자란다. 별도 업로드 경로를 두지 않는다 (SPEC-004 §5.5-2).
저장 위치는 이 층 밖으로 나가지 않는다 — API 도 화면도 key 를 받지 않는다 (§5.5-4).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re


_MEETING_ID = re.compile(r"^[0-9a-f-]{36}$")

# 선언된 형식 → 파일 확장자. `auto` 는 컨테이너 헤더가 스스로 말하므로 그대로 둔다.
_EXTENSIONS = {
    "auto": "bin",
    "webm": "webm",
    "webm/opus": "webm",
    "ogg/opus": "ogg",
    "ogg": "ogg",
    "mp3": "mp3",
    "wav": "wav",
    "flac": "flac",
    "pcm_s16le": "pcm",
    "mulaw": "pcm",
}


class LocalDirectoryRecordingStorage:
    """회의 하나에 파일 하나. 청크는 append 되고, 쓰지 못하면 그 자리에서 예외가 난다."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def extension_for(self, audio_format: str) -> str:
        return _EXTENSIONS.get((audio_format or "").strip().lower(), "bin")

    def append(self, meeting_id: str, chunk: bytes, *, extension: str) -> str:
        """청크를 **받은 순서 그대로** 원본 끝에 붙이고 저장 key 를 돌려준다.

        webm 청크는 첫 조각 뒤로 홀로 서지 못한다 — 자르거나 다시 프레이밍하거나 건너뛰면 파일이 깨진다.
        실패는 `OSError` 로 그대로 올라가고, 부르는 쪽이 그 자리에서 스트림을 끊는다 (SPEC-004 §5.5-3).
        """
        path = self._path(meeting_id, extension)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as handle:
            handle.write(chunk)
        return self._key(meeting_id, extension)

    def size(self, key: str) -> int:
        path = self._resolve(key)
        return path.stat().st_size if path.is_file() else 0

    def get(self, key: str) -> bytes:
        path = self._resolve(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return path.read_bytes()

    def digest(self, key: str) -> str:
        return hashlib.sha256(self.get(key)).hexdigest()

    def _key(self, meeting_id: str, extension: str) -> str:
        if not _MEETING_ID.match(meeting_id):
            raise ValueError("invalid meeting identifier")
        return f"meetings/audio/{meeting_id}.{extension}"

    def _path(self, meeting_id: str, extension: str) -> Path:
        return self._resolve(self._key(meeting_id, extension))

    def _resolve(self, key: str) -> Path:
        path = (self._root / key).resolve()
        if self._root not in path.parents:
            raise ValueError("recording storage key escapes the root")
        return path
