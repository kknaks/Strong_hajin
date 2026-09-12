"""Soniox 실시간 STT 어댑터 — **`SONIOX_API_KEY` 를 읽는 유일한 코드**.

- 브라우저는 provider 주소도 키도 모른다 (SCAX-SPEC-004 §5.2-2). 이 모듈은 서버 안에서만 산다 —
  `entrypoints`·응답 스키마 어디에도 `soniox` 라는 글자가 없다.
- 옵션은 고정이다 — `stt-rt-v5` · `language_hints:["ko"]` · `enable_speaker_diarization:true` ·
  **endpoint detection 미사용**(조기 파이널라이즈가 화자 분리를 깎는다). 옵션 자체를 config 에 싣지 않는 것이 「미사용」이다.
- 프로토콜: 최초 JSON config → 오디오 바이트 → 빈 문자열로 종료. 응답은
  `{tokens:[{text, is_final, speaker, start_ms, end_ms}], error_code, error_message, finished}`.
- **연결 실패·끊김은 `SttUpstreamError` 하나로 낸다** — `error{reason:"upstream"}` 이 설계한 실패다.
  그 밖은 그대로 전파한다. **재연결하지 않는다** (§5.2-7).

종료 뒤에는 **저장된 음원 전체를 한 번 더** 전사한다 (`stt-async-v5` · 사용자 결정 D44, 2026-09-11).
실시간과 **완전히 따로 사는 갈래**다 — 프로토콜도(REST) 모델도 다르고, 공유하는 것은 키와 `SttToken` 뿐이다.
흐름: `POST /v1/files`(멀티파트) → `POST /v1/transcriptions` → 상태 폴링 → `GET …/transcript`.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
import os
import logging
import time
from http.client import IncompleteRead
from typing import Any
from urllib import error as urlerror, request as urlrequest
from uuid import uuid4

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from ax_workspace.modules.meetings.retranscribe import (
    POLL_SECONDS,
    RESULT_BACKOFF_SECONDS,
    RESULT_RETRIES,
    TIMEOUT_SECONDS,
    Recording,
    uploadable,
)
from ax_workspace.modules.meetings.stream import (
    AudioDeclaration,
    SttSession,
    SttToken,
    SttUpstreamError,
)


logger = logging.getLogger(__name__)

#: 한 번에 읽어 들이는 조각. 본문을 한 번의 `read()` 로 받으려 하면 큰 응답이 중간에 끊긴다.
_READ_CHUNK = 64 * 1024

SONIOX_REALTIME_URL = "wss://stt-rt.soniox.com/transcribe-websocket"
SONIOX_API_URL = "https://api.soniox.com"
_MODEL = "stt-rt-v5"
_ASYNC_MODEL = "stt-async-v5"
_LANGUAGE_HINTS = ["ko"]
_AUTO_FORMAT = "auto"

# 컨테이너 형식은 헤더가 스스로 형식·샘플레이트·채널을 말한다 — provider 에는 `auto` 로 넘기고 수치를 싣지 않는다.
# 브라우저가 보내는 `webm/opus` 도 여기에 든다. raw PCM 계열만 선언값을 그대로 옮긴다.
_CONTAINER_FORMATS = frozenset({"auto", "webm", "webm/opus", "ogg", "ogg/opus", "mp3", "mp4", "m4a", "wav", "flac", "aac"})


def is_container_format(audio_format: str) -> bool:
    return (audio_format or "").strip().lower() in _CONTAINER_FORMATS


def build_config(api_key: str, audio: AudioDeclaration) -> dict[str, object]:
    """최초 JSON config. **endpoint detection 키가 없다** — 그것이 「미사용」이다.

    클라이언트가 선언한 형식을 그대로 넘기지 않는다: 컨테이너면 `auto` 로 바꾸고 `sample_rate`·`num_channels`
    를 뺀다. 컨테이너 헤더가 이미 그 값을 들고 있고, 어긋난 수치를 함께 보내면 provider 가 거절한다.
    """
    container = is_container_format(audio.format)
    config: dict[str, object] = {
        "api_key": api_key,
        "model": _MODEL,
        "audio_format": _AUTO_FORMAT if container else audio.format,
        "language_hints": list(_LANGUAGE_HINTS),
        "enable_speaker_diarization": True,
    }
    if not container:
        config["sample_rate"] = audio.sample_rate
        config["num_channels"] = audio.channels
    return config


class SonioxRealtimeSession:
    def __init__(self, socket) -> None:
        self._socket = socket
        self._closed = False
        self._ended = False

    async def send_audio(self, chunk: bytes) -> None:
        try:
            await self._socket.send(chunk)
        except ConnectionClosed as error:
            raise SttUpstreamError("업스트림 연결이 끊겼습니다") from error

    async def tokens(self) -> AsyncIterator[list[SttToken]]:
        while True:
            try:
                raw = await self._socket.recv()
            except ConnectionClosed as error:
                raise SttUpstreamError("업스트림 연결이 끊겼습니다") from error
            message = json.loads(raw)
            if message.get("error_code") is not None:
                raise SttUpstreamError(f"업스트림 오류 {message['error_code']}")
            yield [_to_token(token) for token in message.get("tokens", [])]
            if message.get("finished"):
                return

    async def finish(self, *, timeout_seconds: float) -> AsyncIterator[list[SttToken]]:
        """종료 프레임을 보내고 `finished` 까지 남은 토큰을 낸다.

        마지막 몇 초의 발화는 이 신호를 받고서야 확정된다(실측: end frame 없이 30초를 기다려도 확정되지 않는다).
        상한을 넘기면 받은 데까지이고 그 자체가 실패는 아니다 — 회의를 붙들어 두지 않는다.
        """
        await self._send_end()
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return
            try:
                raw = await asyncio.wait_for(self._socket.recv(), timeout=remaining)
            except (TimeoutError, ConnectionClosed):
                return
            message = json.loads(raw)
            if message.get("error_code") is not None:
                # 종료 중의 provider 오류는 이미 받은 것을 버릴 이유가 아니다.
                return
            tokens = [_to_token(token) for token in message.get("tokens", [])]
            if tokens:
                yield tokens
            if message.get("finished"):
                return

    async def keepalive(self) -> None:
        """오디오가 없는 동안 연결을 살려 둔다 — provider 는 조용한 업스트림을 끊는다."""
        try:
            await self._socket.send(json.dumps({"type": "keepalive"}))
        except ConnectionClosed as error:
            raise SttUpstreamError("업스트림 연결이 끊겼습니다") from error

    async def _send_end(self) -> None:
        """더 올 오디오가 없다는 신호. 한 번만 보낸다."""
        if self._ended:
            return
        self._ended = True
        try:
            await self._socket.send("")
        except ConnectionClosed:
            pass

    async def close(self) -> None:
        """종료 프레임 뒤 소켓을 닫는다. 이미 보냈으면 닫기만 한다."""
        if self._closed:
            return
        self._closed = True
        await self._send_end()
        await self._socket.close()


def _to_token(raw: dict) -> SttToken:
    speaker = raw.get("speaker")
    return SttToken(
        text=raw["text"],
        is_final=bool(raw.get("is_final", False)),
        speaker=None if speaker is None else str(speaker),
        start_ms=int(raw.get("start_ms", 0)),
        end_ms=int(raw.get("end_ms", 0)),
    )


class SonioxRealtimeConnector:
    """실제 Soniox. 키는 만들 때 한 번 받는다 — 로그·응답·프레임 어디에도 싣지 않는다."""

    def __init__(self, api_key: str | None = None, *, url: str = SONIOX_REALTIME_URL) -> None:
        self._api_key = api_key if api_key is not None else os.getenv("SONIOX_API_KEY", "")
        self._url = url

    async def connect(self, audio: AudioDeclaration) -> SttSession:
        if not self._api_key:
            raise SttUpstreamError("업스트림 자격 증명이 서버에 없습니다")
        try:
            socket = await websockets.connect(self._url)
        except (OSError, WebSocketException) as error:
            raise SttUpstreamError("업스트림에 연결하지 못했습니다") from error
        try:
            await socket.send(json.dumps(build_config(self._api_key, audio)))
        except ConnectionClosed as error:
            raise SttUpstreamError("업스트림 연결이 끊겼습니다") from error
        return SonioxRealtimeSession(socket)


# --- 종료 뒤 재전사 (`stt-async-v5` · 사용자 결정 D44) ------------------------------


class SonioxFileTranscriber:
    """음원 파일 하나를 통째로 다시 전사한다. 실시간 갈래와 **키만 공유한다.**

    **폴링 사이에 아무것도 붙들지 않는다** — 여기는 데이터베이스를 모르고, 부르는 쪽이 트랜잭션을
    닫고 온다. 한 시간짜리 회의가 상한(기본 1200초) 안에 끝나기를 기다리는 동안 세션을 쥐고 있으면
    그 연결이 그 시간만큼 묶인다.

    실패는 둘로 가른다 — 올리지 못했거나 그쪽이 거절한 것은 `SttUpstreamError`, 상한을 넘긴 것은
    `TimeoutError` 다. 부르는 쪽은 둘 다 「실시간 원문으로 간다」로 다루지만, 로그에서는 갈려야 한다.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = SONIOX_API_URL,
        poll_seconds: int = POLL_SECONDS,
        timeout_seconds: int = TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.getenv("SONIOX_API_KEY", "")
        self._base_url = base_url.rstrip("/")
        self._poll_seconds = poll_seconds
        self._timeout_seconds = timeout_seconds

    def transcribe(self, recording: Recording) -> list[SttToken]:
        if not self._api_key:
            raise SttUpstreamError("업스트림 자격 증명이 서버에 없습니다")
        payload, filename = uploadable(recording)
        deadline = time.monotonic() + self._timeout_seconds
        file_id = self._upload(payload, filename)
        transcription_id = self._request(file_id)
        self._wait(transcription_id, deadline=deadline)
        return self._fetch(transcription_id)

    # ---- 내부 ----

    def _stage_error(self, stage: str, message: str) -> SttUpstreamError:
        """어느 걸음에서 깨졌는지를 예외에 붙인다 — 위층이 사람 말 문구를 그 표로 고른다."""
        error = SttUpstreamError(message)
        error.stage = stage
        return error

    def _upload(self, payload: bytes, filename: str) -> str:
        boundary = f"----scax{uuid4().hex}"
        body = b"".join((
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            payload,
            f"\r\n--{boundary}--\r\n".encode(),
        ))
        answer = self._call(
            "POST", "/v1/files", body=body, content_type=f"multipart/form-data; boundary={boundary}",
            what="파일 업로드", stage="upload",
        )
        return str(answer["id"])

    def _request(self, file_id: str) -> str:
        answer = self._call(
            "POST",
            "/v1/transcriptions",
            body=json.dumps({
                "file_id": file_id,
                "model": _ASYNC_MODEL,
                "language_hints": list(_LANGUAGE_HINTS),
                "enable_speaker_diarization": True,
            }).encode("utf-8"),
            content_type="application/json",
            what="재전사 요청",
        )
        return str(answer["id"])

    def _wait(self, transcription_id: str, *, deadline: float) -> None:
        """`completed` 가 될 때까지 본다. 상한을 넘기면 `TimeoutError` — 거절과 다른 예외다."""
        while True:
            answer = self._call("GET", f"/v1/transcriptions/{transcription_id}", what="재전사 상태")
            status = answer.get("status")
            if status == "completed":
                return
            if status == "error":
                # **그쪽 본문을 그대로 싣지 않는다** — 사유는 로그가 알고 화면은 정해진 한 줄을 읽는다.
                raise SttUpstreamError("업스트림이 재전사를 마치지 못했습니다")
            if time.monotonic() + self._poll_seconds > deadline:
                raise TimeoutError("재전사가 상한 안에 끝나지 않았습니다")
            time.sleep(self._poll_seconds)

    def _fetch(self, transcription_id: str) -> list[SttToken]:
        """결과를 받는다 — **끊기면 같은 전사 건으로 결과만 다시 받는다** (RESULT_RETRIES 회).

        실물에서 `IncompleteRead(131070 bytes read, 44042 more expected)` 로 큰 본문이 중간에 끊겼다.
        전사는 이미 `completed` 라 다시 전사할 이유가 없다 — 받는 일만 다시 하면 된다.
        """
        last: Exception | None = None
        for attempt in range(RESULT_RETRIES):
            try:
                answer = self._call(
                    "GET", f"/v1/transcriptions/{transcription_id}/transcript", what="재전사 결과", stage="result"
                )
            except SttUpstreamError as error:
                last = error
                if attempt + 1 >= RESULT_RETRIES:
                    break
                wait = RESULT_BACKOFF_SECONDS[min(attempt, len(RESULT_BACKOFF_SECONDS) - 1)]
                logger.warning("재전사 결과를 받다 끊겼습니다 — %d초 뒤 다시 받습니다 (%d/%d)",
                               wait, attempt + 1, RESULT_RETRIES)
                time.sleep(wait)
                continue
            return [_to_final_token(raw) for raw in answer.get("tokens") or []]
        raise last if last is not None else self._stage_error("result", "재전사 결과 실패")

    def _call(self, method: str, path: str, *, body: bytes | None = None,
              content_type: str | None = None, what: str, stage: str = "") -> dict:
        request = urlrequest.Request(f"{self._base_url}{path}", data=body, method=method)
        request.add_header("Authorization", f"Bearer {self._api_key}")
        if content_type:
            request.add_header("Content-Type", content_type)
        try:
            with urlrequest.urlopen(request, timeout=60.0) as response:
                return json.loads(_read_all(response).decode("utf-8") or "{}")
        except urlerror.HTTPError as error:
            error.read()
            raise self._stage_error(stage, f"{what} 실패 (HTTP {error.code})") from error
        except Exception as error:  # noqa: BLE001 — 그쪽 본문을 문구에 싣지 않는다. 무엇이 깨졌는지는 로그가 안다
            raise self._stage_error(stage, f"{what} 실패") from error


def _read_all(response: Any) -> bytes:
    """본문을 **끝까지** 읽는다 — 한 번의 `read()` 로는 큰 응답이 중간에 끊긴다.

    `Content-Length` 가 있으면 그만큼 다 받았는지 확인하고, 모자라면 받다 끊긴 것이다(`IncompleteRead`).
    chunked 라 길이를 모르면 빈 조각이 올 때까지 읽는다.
    """
    chunks: list[bytes] = []
    while True:
        chunk = response.read(_READ_CHUNK)
        if not chunk:
            break
        chunks.append(chunk)
    payload = b"".join(chunks)
    declared = response.headers.get("Content-Length") if response.headers else None
    if declared is not None and payload and len(payload) < int(declared):
        raise IncompleteRead(payload, int(declared) - len(payload))
    return payload


def _to_final_token(raw: dict) -> SttToken:
    """재전사 결과 토큰. 전량이 확정이라 `is_final=True` 다 — 잠정 개념이 없다."""
    speaker = raw.get("speaker")
    return SttToken(
        text=raw.get("text") or "",
        is_final=True,
        speaker=None if speaker is None else str(speaker),
        start_ms=int(raw.get("start_ms") or 0),
        end_ms=int(raw.get("end_ms") or 0),
    )
