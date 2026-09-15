import json
from pathlib import Path

from ax_workspace.modules.ax_execution.ai import (
    AiConversationRequest,
    AiDelegatedToolContext,
)
from ax_workspace.platform.claude_cli import (
    ClaudeCliProfile,
    ClaudeCliProviderAdapter,
)
from ax_workspace.platform.cli_process import ScaxMcpServer


class RecordingSink:
    def __init__(self) -> None:
        self.events = []

    def accept(self, event) -> None:
        self.events.append(event)


def _assistant(*blocks: dict) -> str:
    return json.dumps({"type": "assistant", "session_id": "sess_1", "message": {"content": list(blocks)}})


def _user(*blocks: dict) -> str:
    return json.dumps({"type": "user", "session_id": "sess_1", "message": {"content": list(blocks)}})


_FINAL_PAYLOAD = {"body": "내 업무는 없습니다.", "elements": [], "follow_up_candidates": []}


def claude_lines(*, is_error: bool = False, final_body: str | None = None) -> list[str]:
    payload = {**_FINAL_PAYLOAD, "body": final_body} if final_body else _FINAL_PAYLOAD
    lines = [
        json.dumps({"type": "system", "session_id": "sess_1", "subtype": "init"}),
        _assistant({"type": "text", "text": "먼저 업무를 조회합니다."}),
        _assistant({"type": "tool_use", "id": "call_1", "name": "mcp__scax__task_list", "input": {"limit": 5}}),
        _user({"type": "tool_result", "tool_use_id": "call_1", "is_error": False, "content": json.dumps({"result": []})}),
        _assistant({"type": "tool_use", "id": "call_2", "name": "StructuredOutput", "input": payload}),
        _user({"type": "tool_result", "tool_use_id": "call_2", "content": "Structured output provided successfully"}),
    ]
    result_event = {
        "type": "result",
        "uuid": "run_1",
        "session_id": "sess_1",
        "is_error": is_error,
        "result": "usage limit exceeded" if is_error else json.dumps(payload),
        "structured_output": None if is_error else payload,
        "usage": {"input_tokens": 10, "output_tokens": 3, "service_tier": "standard"},
        "modelUsage": {"claude-sonnet-5": {"inputTokens": 10, "outputTokens": 3, "canonicalModel": "claude-sonnet-5"}},
    }
    lines.append(json.dumps(result_event))
    return lines


def claude_adapter(runner) -> ClaudeCliProviderAdapter:
    return ClaudeCliProviderAdapter(
        ClaudeCliProfile(timeout_seconds=5),
        runner=runner,
        scax_mcp_server=ScaxMcpServer(command="/usr/bin/python3", arguments=("-m", "x"), environment={}),
    )


def claude_request() -> AiConversationRequest:
    return AiConversationRequest(
        prompt="내 업무",
        provider_session_ref=None,
        context_references=[],
        delegated_tool_context=AiDelegatedToolContext("mina", "exec-1"),
    )
