import json
import threading

from ax_workspace.modules.meetings.finalize import FinalizeSessionLost
from ax_workspace.modules.meetings.finalize_service import (
    FAILURE_UNFINISHED,
    MeetingFinalizeService,
)
from ax_workspace.modules.meetings.retranscribe import (
    FAILED_EMPTY,
    FAILED_NO_RECORDING,
    FAILED_PROVIDER,
    FAILED_RESULT,
    FAILED_TIMEOUT,
    FAILED_UPLOAD,
    Recording,
)
from ax_workspace.modules.meetings.stream import SttToken, SttUpstreamError


VALID_OUTPUT = json.dumps(
    {
        "title_candidate": None,
        "agendas": [
            {
                "title": "정리",
                "merged_from": [],
                "concluded": False,
                "lines": [],
                "todos": [],
            }
        ],
    },
    ensure_ascii=False,
)


class _Gateway:
    def __init__(self, *, session_ref: str | None = "session-1", commit_failures: int = 0) -> None:
        self.recording: Recording | None = Recording(b"audio", "meeting.webm")
        self.source = {
            "meeting_id": "meeting-1",
            "session_ref": session_ref,
            "persona_id": "mina",
            "meeting": {"title": "회의", "starts_on": "2026-09-13 (일)"},
            "memo_agendas": [],
            "ai_agendas": [],
            "memo_lines": [],
            "ai_lines": [],
            "transcript": [{"content": "다시 들은 말"}],
            "covered_ms": (0, 900),
            "next_meeting_starts_on": None,
        }
        self.commit_failures = commit_failures
        self.commit_attempts = 0
        self.committed = []
        self.failures: list[str] = []
        self.replaced = []
        self.transcript_source: str | None = None

    def recording_for(self, _meeting_id):
        return self.recording

    def replace_transcript(self, _meeting_id, blocks):
        self.replaced = blocks
        return len(blocks)

    def set_transcript_source(self, _meeting_id, source):
        self.transcript_source = source

    def load_input(self, _meeting_id):
        return self.source

    def existing_task_titles(self, _persona_id):
        return set()

    def commit_success(self, _meeting_id, notes, *, cold_start):
        self.commit_attempts += 1
        if self.commit_failures:
            self.commit_failures -= 1
            raise RuntimeError("storage unavailable")
        self.committed.append((notes, cold_start))

    def commit_failure(self, _meeting_id, reason):
        self.failures.append(reason)


class _Agent:
    def __init__(self) -> None:
        self.calls = []

    def run_final(self, **request):
        self.calls.append(request)
        return VALID_OUTPUT


class _Transcriber:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result if result is not None else [SttToken("말.", True, "1", 0, 900)]
        self.error = error

    def transcribe(self, _recording):
        if self.error is not None:
            raise self.error
        return self.result


def _service(gateway: _Gateway, agent: _Agent, *, transcriber=None) -> MeetingFinalizeService:
    lock = threading.Lock()
    return MeetingFinalizeService(
        gateway=gateway,
        agent=agent,
        lock_for=lambda _meeting_id: lock,
        transcriber=transcriber if transcriber is not None else _Transcriber(),
    )


def test_finalize_retries_the_complete_attempt_including_commit() -> None:
    gateway = _Gateway(commit_failures=2)
    agent = _Agent()

    assert _service(gateway, agent).run("meeting-1") is True
    assert gateway.commit_attempts == 3
    assert len(agent.calls) == 3
    assert len(gateway.committed) == 1 and gateway.failures == []


def test_finalize_settles_failure_after_the_attempt_limit() -> None:
    gateway = _Gateway(commit_failures=99)
    agent = _Agent()

    assert _service(gateway, agent).run("meeting-1") is False
    assert gateway.commit_attempts == 3
    assert gateway.failures == [FAILURE_UNFINISHED]


def test_finalize_cold_start_sends_the_transcript_when_the_session_is_missing() -> None:
    gateway = _Gateway(session_ref=None)
    agent = _Agent()
    service = _service(gateway, agent)

    assert service.run("meeting-1") is True
    assert service.cold_starts == 1
    assert agent.calls[0]["session_ref"] is None
    assert "다시 들은 말" in agent.calls[0]["prompt"]


class _LostSessionAgent(_Agent):
    """resume 는 언제나 「세션이 여기 없다」. 새 세션(콜드 스타트)은 `cold_failures` 번 실패한 뒤 된다."""

    def __init__(self, *, cold_failures: int = 0) -> None:
        super().__init__()
        self.cold_failures = cold_failures

    def run_final(self, **request):
        self.calls.append(request)
        if request["session_ref"] is not None:
            raise FinalizeSessionLost("session not here")
        if self.cold_failures:
            self.cold_failures -= 1
            raise RuntimeError("provider failed")
        return VALID_OUTPUT


def test_finalize_falls_back_to_cold_start_when_the_session_cannot_be_resumed() -> None:
    """B-03 — 세션은 API 파드가 열고 합성은 워커 파드가 돈다. resume 이 안 되면 **같은 시도 안에서** 콜드 스타트."""
    gateway = _Gateway(session_ref="session-from-another-pod")
    agent = _LostSessionAgent()
    service = _service(gateway, agent)

    assert service.run("meeting-1") is True
    assert [call["session_ref"] for call in agent.calls] == ["session-from-another-pod", None]
    assert "다시 들은 말" not in agent.calls[0]["prompt"], "resume 은 세션이 기억하는 원문을 다시 싣지 않는다"
    assert "다시 들은 말" in agent.calls[1]["prompt"], "콜드 스타트는 저장된 원문 전량을 싣는다"
    assert service.cold_starts == 1
    assert len(gateway.committed) == 1 and gateway.committed[0][1] is True
    assert gateway.failures == []


def test_a_lost_session_does_not_spend_retries_on_the_same_resume() -> None:
    """세션 없음은 세 번 다시 걸 이유가 아니다 — 첫 시도에서 콜드로 넘어가면 남은 시도도 콜드다."""
    gateway = _Gateway(session_ref="session-from-another-pod")
    agent = _LostSessionAgent(cold_failures=1)

    assert _service(gateway, agent).run("meeting-1") is True
    assert [call["session_ref"] for call in agent.calls] == ["session-from-another-pod", None, None]


def test_cold_start_that_also_fails_settles_the_same_failure_as_before() -> None:
    gateway = _Gateway(session_ref="session-from-another-pod")
    agent = _LostSessionAgent(cold_failures=99)

    assert _service(gateway, agent).run("meeting-1") is False
    assert [call["session_ref"] for call in agent.calls] == ["session-from-another-pod", None, None, None]
    assert gateway.failures == [FAILURE_UNFINISHED]
    assert gateway.committed == []


def test_retranscription_failures_stop_before_final_generation() -> None:
    def staged(stage: str) -> SttUpstreamError:
        error = SttUpstreamError("provider failed")
        error.stage = stage
        return error

    cases = (
        (None, None, FAILED_NO_RECORDING),
        (_Transcriber(result=[]), Recording(b"audio", "m.webm"), FAILED_EMPTY),
        (_Transcriber(error=TimeoutError()), Recording(b"audio", "m.webm"), FAILED_TIMEOUT),
        (_Transcriber(error=staged("upload")), Recording(b"audio", "m.webm"), FAILED_UPLOAD),
        (_Transcriber(error=staged("result")), Recording(b"audio", "m.webm"), FAILED_RESULT),
        (_Transcriber(error=SttUpstreamError("no stage")), Recording(b"audio", "m.webm"), FAILED_PROVIDER),
    )
    for transcriber, recording, expected in cases:
        gateway = _Gateway()
        gateway.recording = recording
        agent = _Agent()
        service = MeetingFinalizeService(
            gateway=gateway,
            agent=agent,
            lock_for=lambda _meeting_id: threading.Lock(),
            transcriber=transcriber,
        )

        assert service.run("meeting-1") is False
        assert gateway.failures == [expected]
        assert agent.calls == []
