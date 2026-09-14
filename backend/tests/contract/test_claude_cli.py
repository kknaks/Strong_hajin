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
