"""SSE 응답을 **ASGI 로 직접** 읽는 시험 도구 — `TestClient` 는 응답이 끝날 때까지 본문을 모아 끝나지 않는 스트림을 못 읽는다.

같은 라우트 · 같은 허브 · 같은 DB 를 지나고, 다 읽으면 `http.disconnect` 를 보내 닫는다(브라우저가 떠나는 것과 같은 길).
쓰는 곳: `tests/contract/test_events_stream.py` · `tests/integration/postgres/test_external_channels_postgres.py`.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urlencode


class SseConnection:
    """ASGI 로 연 SSE 연결 하나 — 받은 바이트를 사건 단위로 나눠 준다."""

    def __init__(self, app, path: str, headers: dict[str, str] | None = None, query: dict[str, str] | None = None) -> None:
        self._app, self._path, self._headers, self._query = app, path, headers or {}, query or {}
        self._chunks: asyncio.Queue[bytes] = asyncio.Queue()
        self._buffer = ""
        self._left = asyncio.Event()
        self._sent_request = False
        self.status: int | None = None
        self.headers: dict[str, str] = {}
        self._started = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def __aenter__(self) -> "SseConnection":
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": self._path,
            "raw_path": self._path.encode(),
            "query_string": urlencode(self._query).encode(),
            "root_path": "",
            "headers": [(key.lower().encode(), value.encode()) for key, value in self._headers.items()],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
        self._task = asyncio.ensure_future(self._app(scope, self._receive, self._send))
        await asyncio.wait_for(self._started.wait(), timeout=5)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self._left.set()
        assert self._task is not None
        await asyncio.wait_for(self._task, timeout=5)

    async def _receive(self) -> dict[str, Any]:
        if not self._sent_request:
            self._sent_request = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await self._left.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            self.status = message["status"]
            self.headers = {key.decode().lower(): value.decode() for key, value in message.get("headers", [])}
            self._started.set()
        elif message["type"] == "http.response.body":
            if message.get("body"):
                await self._chunks.put(message["body"])

    async def body(self) -> str:
        """스트림이 아닌 응답(401 · 403)의 본문 전체."""
        assert self._task is not None
        await asyncio.wait_for(self._task, timeout=5)
        parts = []
        while not self._chunks.empty():
            parts.append(self._chunks.get_nowait().decode())
        return "".join(parts)

    async def frame(self, timeout: float = 5.0) -> dict[str, Any]:
        """다음 사건 하나 — `{event, id, data, retry, comment}` 중 실린 것만."""
        while "\n\n" not in self._buffer:
            chunk = await asyncio.wait_for(self._chunks.get(), timeout=timeout)
            self._buffer += chunk.decode()
        block, self._buffer = self._buffer.split("\n\n", 1)
        parsed: dict[str, Any] = {}
        for line in block.split("\n"):
            if line.startswith(":"):
                parsed["comment"] = line[1:].strip()
                continue
            field, _, value = line.partition(": ")
            if field == "data":
                parsed["data"] = json.loads(value)
            elif field == "id":
                parsed["id"] = int(value)
            elif field in {"event", "retry"}:
                parsed[field] = value
        return parsed

    async def frames_until(self, event: str, timeout: float = 5.0) -> list[dict[str, Any]]:
        """그 이름의 사건까지(포함) 받은 것 — 하트비트는 빼지 않는다."""
        seen = []
        while True:
            frame = await self.frame(timeout=timeout)
            seen.append(frame)
            if frame.get("event") == event:
                return seen

    async def nothing_within(self, seconds: float) -> bool:
        try:
            await self.frame(timeout=seconds)
        except TimeoutError:
            return True
        return False
