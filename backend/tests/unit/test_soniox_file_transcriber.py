import json

import pytest

from ax_workspace.modules.meetings.retranscribe import Recording
from ax_workspace.modules.meetings.stream import SttUpstreamError
from ax_workspace.platform import soniox


class _FakeResponse:
    def __init__(self, payload: bytes, *, declared: int | None = None) -> None:
        self.payload = payload
        self.read_once = False
        self.headers = {"Content-Length": str(declared if declared is not None else len(payload))}

    def read(self, _size: int = -1) -> bytes:
        if self.read_once:
            return b""
        self.read_once = True
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False


def _transcriber(monkeypatch, answers: list):
    calls: list[str] = []

    def fake_urlopen(request, timeout=None):
        del timeout
        calls.append(request.full_url)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(soniox.urlrequest, "urlopen", fake_urlopen)
    monkeypatch.setattr(soniox.time, "sleep", lambda _seconds: None)
    return soniox.SonioxFileTranscriber(api_key="x"), calls


def _tokens_body(text: str) -> bytes:
    return json.dumps(
        {"tokens": [{"text": text, "speaker": "1", "start_ms": 0, "end_ms": 900}]}
    ).encode()


def test_cut_result_retries_only_the_same_transcription_result(monkeypatch) -> None:
    whole = _tokens_body("끝까지 받은 결과")
    transcriber, calls = _transcriber(
        monkeypatch,
        [
            _FakeResponse(b'{"id": "file-1"}'),
            _FakeResponse(b'{"id": "tx-1"}'),
            _FakeResponse(b'{"status": "completed"}'),
            _FakeResponse(whole[:10], declared=len(whole)),
            _FakeResponse(whole[:10], declared=len(whole)),
            _FakeResponse(whole),
        ],
    )

    tokens = transcriber.transcribe(Recording(data=b"audio", filename="m.webm"))

    assert [token.text for token in tokens] == ["끝까지 받은 결과"]
    result_calls = [url for url in calls if url.endswith("/transcript")]
    assert len(result_calls) == 3 and len(set(result_calls)) == 1
    assert len([url for url in calls if url.endswith("/v1/files")]) == 1
    assert len([url for url in calls if url.endswith("/v1/transcriptions")]) == 1


def test_result_that_never_arrives_whole_fails_closed(monkeypatch) -> None:
    whole = _tokens_body("끝내 못 받은 결과")
    transcriber, calls = _transcriber(
        monkeypatch,
        [
            _FakeResponse(b'{"id": "file-1"}'),
            _FakeResponse(b'{"id": "tx-1"}'),
            _FakeResponse(b'{"status": "completed"}'),
            *[_FakeResponse(whole[:10], declared=len(whole)) for _ in range(3)],
        ],
    )

    with pytest.raises(SttUpstreamError) as raised:
        transcriber.transcribe(Recording(data=b"audio", filename="m.webm"))

    assert getattr(raised.value, "stage", "") == "result"
    assert len([url for url in calls if url.endswith("/transcript")]) == 3
