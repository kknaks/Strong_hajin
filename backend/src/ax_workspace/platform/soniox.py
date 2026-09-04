"""Soniox adapters for the Meeting transcription ports.

This adapter is deliberately synchronous because it is invoked only by the
separate Meeting worker.  Browser audio uses a restricted temporary key; the
long-lived key remains solely in this process environment.
"""
from __future__ import annotations

from datetime import datetime
from dataclasses import replace
import json
import os
from time import sleep
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from ax_workspace.modules.meetings.transcription import (
    FinalTranscriptSegment,
    FinalTranscriber,
    FinalTranscriptionResult,
    RealtimeTranscriptionCredential,
    RealtimeTranscriptionKeyIssuer,
    TranscriptionFailure,
)


SONIOX_API = "https://api.soniox.com/v1"
SONIOX_WEBSOCKET = "wss://stt-rt.soniox.com/transcribe-websocket"


class SonioxTranscriptionAdapter(RealtimeTranscriptionKeyIssuer, FinalTranscriber):
    def __init__(
        self,
        api_key: str | None = None,
        *,
        opener: Callable[..., Any] = urlopen,
        sleeper: Callable[[float], None] = sleep,
        poll_seconds: float = 1.0,
        max_polls: int = 1_800,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.getenv("SONIOX_API_KEY")
        self._opener = opener
        self._sleeper = sleeper
        self._poll_seconds = poll_seconds
        self._max_polls = max_polls

    def issue(self, *, client_reference_id: str, max_session_duration_seconds: int) -> RealtimeTranscriptionCredential:
        if not 1 <= max_session_duration_seconds <= 18_000:
            raise TranscriptionFailure("invalid_session_duration", retryable=False)
        payload = self._request_json(
            "/auth/temporary-api-key",
            method="POST",
            body={
                "usage_type": "transcribe_websocket",
                "expires_in_seconds": min(3_600, max(60, max_session_duration_seconds)),
                "single_use": True,
                "max_session_duration_seconds": max_session_duration_seconds,
                "client_reference_id": client_reference_id[:256],
            },
        )
        try:
            return RealtimeTranscriptionCredential(
                temporary_key=str(payload["api_key"]),
                expires_at=datetime.fromisoformat(str(payload["expires_at"]).replace("Z", "+00:00")),
                client_reference_id=client_reference_id,
                websocket_url=SONIOX_WEBSOCKET,
                model="stt-rt-v5",
                enable_speaker_diarization=True,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise TranscriptionFailure("provider_format_error", retryable=False) from error

    def transcribe(
        self,
        *,
        data: bytes,
        original_name: str,
        content_type: str,
        client_reference_id: str,
    ) -> FinalTranscriptionResult:
        if not data:
            raise TranscriptionFailure("empty_recording", retryable=False)
        file_id: str | None = None
        transcription_id: str | None = None
        result: FinalTranscriptionResult | None = None
        cleanup_warnings: list[str] = []
        failure: TranscriptionFailure | None = None
        try:
            file_id = self._upload(data, original_name, content_type)
            created = self._request_json(
                "/transcriptions",
                method="POST",
                body={
                    "model": "stt-async-v5",
                    "file_id": file_id,
                    "enable_speaker_diarization": True,
                    "client_reference_id": client_reference_id[:256],
                },
            )
            transcription_id = str(created["id"])
            for _ in range(self._max_polls):
                status = self._request_json(f"/transcriptions/{transcription_id}")
                if status.get("status") == "completed":
                    transcript = self._request_json(f"/transcriptions/{transcription_id}/transcript")
                    result = FinalTranscriptionResult(
                        provider_reference=transcription_id,
                        provider_file_ref=file_id,
                        segments=tuple(_segments_from_tokens(transcript.get("tokens"))),
                        provider_request_id=_provider_request_id(transcript),
                    )
                    break
                if status.get("status") == "error":
                    raise TranscriptionFailure(
                        _stable_error(status),
                        retryable=False,
                        provider_reference=transcription_id,
                        provider_request_id=_provider_request_id(status),
                    )
                self._sleeper(self._poll_seconds)
            if result is None:
                raise TranscriptionFailure("provider_timeout", retryable=True, provider_reference=transcription_id)
        except TranscriptionFailure as error:
            failure = error
        finally:
            if transcription_id is not None:
                warning = self._delete(f"/transcriptions/{transcription_id}")
                if warning is not None:
                    cleanup_warnings.append(warning)
            if file_id is not None:
                warning = self._delete(f"/files/{file_id}")
                if warning is not None:
                    cleanup_warnings.append(warning)
        if failure is not None:
            raise TranscriptionFailure(
                failure.code,
                retryable=failure.retryable,
                provider_reference=failure.provider_reference,
                provider_request_id=failure.provider_request_id,
                cleanup_warnings=tuple(cleanup_warnings),
            ) from failure
        if result is None:  # defensive: the loop either returns a result or raises.
            raise TranscriptionFailure("provider_request_failed", retryable=True)
        return replace(result, cleanup_warnings=tuple(cleanup_warnings))

    def _upload(self, data: bytes, original_name: str, content_type: str) -> str:
        boundary = f"----scax-{uuid4().hex}"
        name = original_name.replace('"', "")[:200] or "recording"
        body = b"".join(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode(),
                f"Content-Type: {content_type or 'application/octet-stream'}\r\n\r\n".encode(),
                data,
                f"\r\n--{boundary}--\r\n".encode(),
            ]
        )
        payload = self._request_json(
            "/files",
            method="POST",
            raw_body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        try:
            return str(payload["id"])
        except (KeyError, TypeError) as error:
            raise TranscriptionFailure("provider_format_error", retryable=False) from error

    def _delete(self, path: str) -> str | None:
        try:
            self._request_json(path, method="DELETE")
            return None
        except TranscriptionFailure as error:
            return f"cleanup:{path.rsplit('/', 1)[0].rsplit('/', 1)[-1]}:{error.code}"

    def _request_json(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        raw_body: bytes | None = None,
        content_type: str = "application/json",
    ) -> dict[str, Any]:
        if not self._api_key:
            raise TranscriptionFailure("provider_unavailable", retryable=False)
        data = raw_body if raw_body is not None else (json.dumps(body).encode("utf-8") if body is not None else None)
        request = Request(
            f"{SONIOX_API}{path}",
            data=data,
            method=method,
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": content_type},
        )
        try:
            with self._opener(request, timeout=30) as response:
                raw = response.read()
        except HTTPError as error:
            payload = _http_error_payload(error)
            raise TranscriptionFailure(
                _stable_error(payload) if payload else _stable_http_error(error.code),
                retryable=error.code in {408, 429, 500, 502, 503, 504},
                provider_request_id=_provider_request_id(payload),
            ) from error
        except (TimeoutError, URLError) as error:
            raise TranscriptionFailure("provider_timeout", retryable=True) from error
        try:
            return json.loads(raw) if raw else {}
        except (TypeError, ValueError) as error:
            raise TranscriptionFailure("provider_format_error", retryable=False) from error


def _segments_from_tokens(tokens: Any) -> list[FinalTranscriptSegment]:
    if not isinstance(tokens, list) or not tokens:
        raise TranscriptionFailure("provider_format_error", retryable=False)
    segments: list[FinalTranscriptSegment] = []
    for index, token in enumerate(tokens, start=1):
        if not isinstance(token, dict):
            raise TranscriptionFailure("provider_format_error", retryable=False)
        try:
            start_ms = _milliseconds(token, "start_ms", "start_time_ms", "start_time", "start")
            end_ms = _milliseconds(token, "end_ms", "end_time_ms", "end_time", "end")
            text = str(token["text"])
        except (KeyError, TypeError, ValueError) as error:
            raise TranscriptionFailure("provider_format_error", retryable=False) from error
        if not text.strip() or end_ms <= start_ms:
            continue
        speaker = token.get("speaker")
        segments.append(
            FinalTranscriptSegment(
                source_segment_key=str(token.get("id") or f"token:{index}"),
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
                speaker_label=(f"Speaker {speaker}" if speaker is not None else None),
            )
        )
    if not segments:
        raise TranscriptionFailure("provider_format_error", retryable=False)
    return segments


def _milliseconds(token: dict[str, Any], *names: str) -> int:
    for name in names:
        if name in token:
            value = float(token[name])
            return int(value if name.endswith("_ms") else value * 1_000)
    raise KeyError(names[0])


def _stable_http_error(status: int) -> str:
    if status in {401, 403}:
        return "provider_auth_error"
    if status in {402, 429}:
        return "provider_quota_error"
    if status == 400:
        return "provider_format_error"
    return "provider_request_failed"


def _stable_error(payload: dict[str, Any]) -> str:
    error_type = str(payload.get("error_type") or "")
    if error_type in {"unauthenticated", "authorization_error"}:
        return "provider_auth_error"
    if error_type in {
        "limit_exceeded",
        "organization_balance_exhausted",
        "organization_monthly_budget_exhausted",
        "project_monthly_budget_exhausted",
    }:
        return "provider_quota_error"
    if error_type in {"request_timeout", "timeout"}:
        return "provider_timeout"
    if error_type in {"invalid_request", "invalid_audio", "unsupported_media_type"}:
        return "provider_format_error"
    return _stable_http_error(int(payload.get("status_code") or 500))


def _provider_request_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get("request_id")
    return str(value) if value else None


def _http_error_payload(error: HTTPError) -> dict[str, Any] | None:
    try:
        raw = error.read()
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None
    except (OSError, TypeError, ValueError):
        return None
