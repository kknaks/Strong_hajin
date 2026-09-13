import json
from pathlib import Path

from ax_workspace.modules.ax_execution.ai import (
    AiConversationRequest,
    AiDelegatedToolContext,
)
from ax_workspace.platform.codex_cli import (
    CodexCliMcpServer,
    CodexCliProfile,
    CodexCliProviderAdapter,
)


class RecordingSink:
    def __init__(self) -> None:
        self.events = []

    def accept(self, event) -> None:
        self.events.append(event)


def codex_lines() -> list[str]:
    return [
        json.dumps({"type": "thread.started", "thread_id": "thread_1"}),
        json.dumps({"type": "turn.started"}),
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "item_0",
                    "type": "reasoning",
                    "text": "secret chain of thought",
                },
            }
        ),
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "item_1",
                    "type": "agent_message",
                    "text": "먼저 업무를 조회합니다.",
                },
            }
        ),
        json.dumps(
            {
                "type": "item.started",
                "item": {
                    "id": "item_2",
                    "type": "mcp_tool_call",
                    "server": "scax",
                    "tool": "task_list",
                    "arguments": {"limit": 5},
                    "status": "in_progress",
                },
            }
        ),
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "item_2",
                    "type": "mcp_tool_call",
                    "server": "scax",
                    "tool": "task_list",
                    "result": {"content": [{"type": "text", "text": "[]"}]},
                    "status": "completed",
                },
            }
        ),
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "item_3",
                    "type": "agent_message",
                    "text": "내 업무는 없습니다.",
                },
            }
        ),
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 10, "output_tokens": 3},
            }
        ),
    ]


def codex_adapter(tmp_path, runner) -> CodexCliProviderAdapter:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    return CodexCliProviderAdapter(
        CodexCliProfile(
            runtime_home=tmp_path / "runtime", auth_file=auth_file, timeout_seconds=5
        ),
        runner=runner,
        scax_mcp_server=CodexCliMcpServer(
            command="/usr/bin/python3", arguments=("-m", "x"), environment={}
        ),
    )


def codex_request() -> AiConversationRequest:
    return AiConversationRequest(
        prompt="내 업무",
        provider_session_ref=None,
        context_references=[],
        delegated_tool_context=AiDelegatedToolContext("mina", "exec-1"),
    )


def write_last_message(arguments, body: str = "내 업무는 없습니다.") -> None:
    Path(arguments[arguments.index("--output-last-message") + 1]).write_text(
        json.dumps({"body": body, "follow_up_candidates": []}),
        encoding="utf-8",
    )
