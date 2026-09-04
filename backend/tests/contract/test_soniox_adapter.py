from __future__ import annotations

import json
from urllib.error import HTTPError
from io import BytesIO

import pytest

from ax_workspace.modules.meetings.transcription import TranscriptionFailure
from ax_workspace.platform.soniox import SonioxTranscriptionAdapter, _segments_from_tokens


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


def test_soniox_key_issuer_fails_explicitly_without_a_long_lived_provider_key(monkeypatch) -> None:
    monkeypatch.delenv("SONIOX_API_KEY", raising=False)
    adapter = SonioxTranscriptionAdapter(api_key=None)
    with pytest.raises(TranscriptionFailure, match="provider_unavailable") as error:
        adapter.issue(client_reference_id="meeting-recording:1", max_session_duration_seconds=900)
    assert error.value.retryable is False


def test_soniox_temporary_key_is_single_use_and_bound_to_the_server_reference() -> None:
    requests = []

    def opener(request, timeout):
        requests.append((request.full_url, request.get_method(), request.data, timeout))
        return _Response({"api_key": "temporary-only", "expires_at": "2026-09-04T03:00:00Z"})

    issued = SonioxTranscriptionAdapter(api_key="secret", opener=opener).issue(
        client_reference_id="meeting-recording:123",
        max_session_duration_seconds=900,
    )

    assert issued.temporary_key == "temporary-only"
    assert issued.client_reference_id == "meeting-recording:123"
    assert issued.model == "stt-rt-v5"
    payload = json.loads(requests[0][2])
    assert payload == {
        "usage_type": "transcribe_websocket",
        "expires_in_seconds": 900,
        "single_use": True,
        "max_session_duration_seconds": 900,
        "client_reference_id": "meeting-recording:123",
    }
    assert "secret" not in requests[0][2].decode()


def test_soniox_final_token_parser_preserves_anonymous_labels_and_offsets() -> None:
    segments = _segments_from_tokens(
        [
            {"id": "t1", "start_ms": 0, "end_ms": 400, "text": "안녕", "speaker": 1},
            {"id": "t2", "start_ms": 400, "end_ms": 800, "text": "하세요", "speaker": 1},
        ]
    )

    assert [segment.source_segment_key for segment in segments] == ["t1", "t2"]
    assert [segment.speaker_label for segment in segments] == ["Speaker 1", "Speaker 1"]
    assert [(segment.start_ms, segment.end_ms) for segment in segments] == [(0, 400), (400, 800)]


def test_async_error_prefers_soniox_error_type_and_keeps_request_reference() -> None:
    responses = iter(
        [
            _Response({"id": "file-1"}),
            _Response({"id": "transcription-1"}),
            _Response({"status": "error", "status_code": 500, "error_type": "unauthenticated", "request_id": "req-42"}),
            _Response({}),  # best-effort transcription cleanup
            _Response({}),  # best-effort file cleanup
        ]
    )
    adapter = SonioxTranscriptionAdapter(api_key="secret", opener=lambda *_args, **_kwargs: next(responses), sleeper=lambda _seconds: None)

    with pytest.raises(TranscriptionFailure) as error:
        adapter.transcribe(data=b"audio", original_name="meeting.webm", content_type="audio/webm", client_reference_id="meeting-recording:1")

    assert error.value.code == "provider_auth_error"
    assert error.value.provider_reference == "transcription-1"
    assert error.value.provider_request_id == "req-42"


def test_async_success_returns_cleanup_warning_for_auditable_follow_up() -> None:
    calls = 0

    def opener(_request, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _Response({"id": "file-1"})
        if calls == 2:
            return _Response({"id": "transcription-1"})
        if calls == 3:
            return _Response({"status": "completed"})
        if calls == 4:
            return _Response({"request_id": "req-final", "tokens": [{"id": "t1", "start_ms": 0, "end_ms": 500, "text": "완료", "speaker": 1}]})
        if calls == 5:
            raise HTTPError("https://api.soniox.com/v1/transcriptions/transcription-1", 500, "error", {}, BytesIO(b'{"error_type":"internal_error"}'))
        return _Response({})

    result = SonioxTranscriptionAdapter(api_key="secret", opener=opener, sleeper=lambda _seconds: None).transcribe(
        data=b"audio",
        original_name="meeting.webm",
        content_type="audio/webm",
        client_reference_id="meeting-recording:1",
    )

    assert result.provider_reference == "transcription-1"
    assert result.provider_request_id == "req-final"
    assert result.cleanup_warnings == ("cleanup:transcriptions:provider_request_failed",)
