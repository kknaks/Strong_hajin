"""Claude CLI adapter honors the same answer contract as Codex: `platform/claude_cli.py`."""
import json

import pytest

from ax_workspace.modules.ax_execution.ai import (
    AiConversationRequest,
    AiDelegatedToolContext,
    ProviderRequestFailed,
)
from ax_workspace.platform.claude_cli import ClaudeCliProfile, ClaudeCliProviderAdapter
from ax_workspace.platform.cli_process import ProcessResult, ScaxMcpServer


def _adapter(runner) -> ClaudeCliProviderAdapter:
    return ClaudeCliProviderAdapter(
        ClaudeCliProfile(timeout_seconds=5),
        runner=runner,
        scax_mcp_server=ScaxMcpServer(command="python", arguments=(), environment={}),
    )


def _request() -> AiConversationRequest:
    return AiConversationRequest("내 업무", None, [], AiDelegatedToolContext("mina", "exec-1"))


def _result_line(payload, *, is_error: bool = False) -> str:
    return json.dumps({
        "type": "result",
        "uuid": "run_1",
        "session_id": "sess_1",
        "is_error": is_error,
        "result": json.dumps(payload),
        "structured_output": payload,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    })


def test_conversation_returns_elements_without_exposing_unvalidated_json() -> None:
    payload = {
        "body": "먼저 {{a}}를 확인하세요.",
        "elements": [{"key": "a", "type": "resource_reference", "ref": "task:t1"}],
        "follow_up_candidates": [],
    }
    events = []

    class Sink:
        def accept(self, event):
            events.append(event)

    def runner(command, arguments, cwd, environment, timeout, on_line=None, should_cancel=None):
        on_line(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "c1", "name": "StructuredOutput", "input": payload},
        ]}}))
        on_line(_result_line(payload))
        return ProcessResult("", "", 0)

    result = _adapter(runner).converse(_request(), sink=Sink())
    assert result.answer_elements == payload["elements"]
    assert not any(event.text for event in events), "The structured answer never arrives as a text event"


@pytest.mark.parametrize("payload", [
    {"body": "{{unknown}}", "elements": [], "follow_up_candidates": []},
    {"body": {"unexpected": "object"}, "elements": [], "follow_up_candidates": []},
    {"body": "답변", "elements": [{"type": "execute", "command": "approve"}], "follow_up_candidates": []},
])
def test_invalid_conversation_output_is_a_provider_failure(payload) -> None:
    def runner(command, arguments, cwd, environment, timeout, on_line=None, should_cancel=None):
        on_line(_result_line(payload))
        return ProcessResult("", "", 0)

    with pytest.raises(ProviderRequestFailed):
        _adapter(runner).converse(_request())


def test_a_returncode_failure_without_any_result_event_is_a_provider_failure() -> None:
    def runner(command, arguments, cwd, environment, timeout, on_line=None, should_cancel=None):
        return ProcessResult("", "claude: internal error", 1)

    with pytest.raises(ProviderRequestFailed):
        _adapter(runner).converse(_request())

def test_a_conversation_that_asked_for_its_own_schema_gets_that_structure_back() -> None:
    """**자기 스키마를 건 호출은 그 스키마대로 읽힌다** — Codex 어댑터와 같은 계약이다 (`9fbf4f5`).

    이 자리가 없어서 회의 AI 요약이 통째로 실패한 적이 있다: 부르는 쪽이 `output_schema` 를 걸었는데
    어댑터가 대화 계약(`body`·`elements`·`follow_up_candidates`)으로 읽으려다 모델이 무엇을 내든
    `ProviderResponseInvalid` 를 던졌다. 그 고침은 `codex_cli.py` 에 있었고 **`af01fa9` 가 어댑터를
    둘로 쪼개면서 이 파일에는 같은 자리가 고쳐지지 않은 채로 생겼다** — `ai_provider` 설정 한 줄로
    갈리는 경로라 한쪽만 고치면 provider 를 바꾸는 순간 회의가 다시 깨진다.
    """
    from ax_workspace.modules.meetings.batch import OUTPUT_SCHEMA as BATCH_OUTPUT_SCHEMA, parse_output

    produced = {
        "agendas": [
            {
                "title": "다음 스프린트 범위",
                "lines": [
                    {"text": "로그인 개편을 먼저 낸다.", "evidence": [{"from_ms": 0, "to_ms": 900}], "task_id": None}
                ],
                "todos": [],
            }
        ]
    }

    def runner(command, arguments, cwd, environment, timeout, on_line=None, should_cancel=None):
        on_line(_result_line(produced))
        return ProcessResult("", "", 0)

    request = AiConversationRequest(
        "이번 구간을 정리해 주세요",
        "thread-1",
        [],
        AiDelegatedToolContext("mina", "meeting-batch:mina"),
        output_schema=BATCH_OUTPUT_SCHEMA,
    )
    result = _adapter(runner).converse(request)

    # 받은 그대로 돌려주고, **배치가 실제로 읽는 파서까지** 지난다.
    assert json.loads(result.body) == produced
    assert [agenda.title for agenda in parse_output(result.body)] == ["다음 스프린트 범위"]
    # 대화 계약 전용 필드는 자기 스키마를 건 turn 에 붙지 않는다.
    assert result.answer_elements is None
    assert result.follow_up_candidates == []


def test_a_conversation_that_asked_for_the_final_notes_schema_parses_as_final_notes() -> None:
    """종료 합성도 같은 자리를 지난다 — 배치와 다른 스키마 한 벌이라 따로 건다."""
    from ax_workspace.modules.meetings.finalize import FINAL_OUTPUT_SCHEMA, parse_final_output

    produced = {
        "title_candidate": "9월 정기 회의",
        "agendas": [
            {
                "title": "배포 일정",
                "merged_from": [],
                "concluded": True,
                "lines": [
                    {"text": "금요일에 낸다.", "evidence": [{"from_ms": 0, "to_ms": 900}], "from_lines": []}
                ],
                "todos": [],
            }
        ],
    }

    def runner(command, arguments, cwd, environment, timeout, on_line=None, should_cancel=None):
        on_line(_result_line(produced))
        return ProcessResult("", "", 0)

    request = AiConversationRequest(
        "합성해 주세요",
        "thread-1",
        [],
        AiDelegatedToolContext("mina", "meeting-finalize:mina"),
        output_schema=FINAL_OUTPUT_SCHEMA,
    )
    result = _adapter(runner).converse(request)

    assert json.loads(result.body) == produced
    notes = parse_final_output(result.body)
    assert notes.title_candidate == "9월 정기 회의"
    assert [agenda.title for agenda in notes.agendas] == ["배포 일정"]


def test_an_invalid_response_says_something_the_reader_of_that_turn_can_use() -> None:
    """무효 응답 문구는 **누가 읽는지**에 맞춘다 — 두 어댑터가 같은 공용 헬퍼를 쓴다.

    대화는 사람이 채팅창에서 읽으므로 「다시 요청해 주세요」가 맞다. 자기 스키마를 건 호출은 사람에게
    다시 물을 자리가 없고 그 줄은 회차 기록에만 남는다.
    """
    from ax_workspace.modules.ax_execution.ai import ProviderResponseInvalid
    from ax_workspace.modules.meetings.batch import OUTPUT_SCHEMA as BATCH_OUTPUT_SCHEMA

    def runner(command, arguments, cwd, environment, timeout, on_line=None, should_cancel=None):
        # 스키마가 요구한 키가 없다 — 부르는 쪽 스키마로도 읽히지 않는 답이다.
        on_line(_result_line({"something": "else"}))
        return ProcessResult("", "", 0)

    schema_request = AiConversationRequest(
        "정리해 주세요",
        "thread-1",
        [],
        AiDelegatedToolContext("mina", "meeting-batch:mina"),
        output_schema=BATCH_OUTPUT_SCHEMA,
    )
    # 자기 스키마를 건 turn 은 어댑터가 그대로 넘기고, 스키마 검증은 부르는 쪽(parse_output)이 한다.
    body = _adapter(runner).converse(schema_request).body
    assert json.loads(body) == {"something": "else"}

    # 대화 turn 은 어댑터가 그 자리에서 거절하고 사람이 읽을 한 줄을 남긴다.
    with pytest.raises((ProviderResponseInvalid, ProviderRequestFailed)) as raised:
        _adapter(runner).converse(_request())
    assert "다시 요청해 주세요" in str(raised.value)
