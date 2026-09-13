"""Codex stream parsing and injected-runner behavior stay deterministic and process-free."""

from dataclasses import asdict
import json

import pytest

from ax_workspace.modules.ax_execution.ai import ProviderCancelled
from ax_workspace.platform.codex_cli import CodexEventIngest, ProcessResult
from codex_stream_support import (
    RecordingSink,
    codex_adapter,
    codex_lines,
    codex_request,
    write_last_message,
)


def test_ingest_emits_only_observed_events_and_never_reasoning() -> None:
    sink = RecordingSink()
    ingest = CodexEventIngest(sink)
    for line in codex_lines():
        ingest.consume_line(line)
    kinds = [(event.kind, event.item_type) for event in sink.events]
    assert kinds == [
        ("turn_started", None),
        ("item_completed", "agent_message"),
        ("item_started", "mcp_tool_call"),
        ("item_completed", "mcp_tool_call"),
        ("item_completed", "agent_message"),
        ("turn_completed", None),
    ]
    texts = [event.text for event in sink.events if event.text]
    assert texts == ["먼저 업무를 조회합니다.", "내 업무는 없습니다."]
    assert all(
        "secret" not in json.dumps(asdict(event), default=str) for event in sink.events
    )
    [tool] = ingest.tool_invocations()
    assert tool.tool_name == "task_list" and tool.state == "completed"
    assert tool.input_summary == "입력: limit=5"
    assert tool.started_at is not None and tool.completed_at is not None
    assert tool.latency_ms is not None and tool.latency_ms >= 0
    assert ingest.usage == {"input_tokens": 10, "output_tokens": 3}
    assert ingest.session_ref == "thread_1"


def test_tool_latency_is_not_estimated_without_an_observed_start() -> None:
    ingest = CodexEventIngest(None)
    ingest.consume_line(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "x",
                    "type": "mcp_tool_call",
                    "tool": "task_get",
                    "status": "completed",
                    "result": {},
                },
            }
        )
    )
    [tool] = ingest.tool_invocations()
    assert (
        tool.started_at is None
        and tool.completed_at is not None
        and tool.latency_ms is None
    )


def test_adapter_streams_lines_to_the_sink_before_the_process_ends(tmp_path) -> None:
    sink = RecordingSink()
    seen_during_run: list[int] = []

    def runner(
        command, arguments, cwd, environment, timeout, on_line=None, should_cancel=None
    ):
        for line in codex_lines():
            on_line(line + "\n")
            seen_during_run.append(len(sink.events))
        write_last_message(arguments)
        return ProcessResult("\n".join(codex_lines()), "", 0)

    result = codex_adapter(tmp_path, runner).converse(codex_request(), sink=sink)
    assert seen_during_run[-1] == len(sink.events) == 6 and seen_during_run[0] == 0
    assert result.body == "내 업무는 없습니다."
    assert result.usage == {"input_tokens": 10, "output_tokens": 3}
    assert [tool.tool_name for tool in result.tool_invocations] == ["task_list"]


def test_adapter_stops_when_the_cancel_token_is_set(tmp_path) -> None:
    class Token:
        def __init__(self) -> None:
            self.flag = False

        def is_set(self) -> bool:
            return self.flag

    token = Token()
    stopped = {"asked": False}

    def runner(
        command, arguments, cwd, environment, timeout, on_line=None, should_cancel=None
    ):
        on_line(json.dumps({"type": "turn.started"}) + "\n")
        token.flag = True
        stopped["asked"] = should_cancel()
        return ProcessResult("", "", -2)

    with pytest.raises(ProviderCancelled):
        codex_adapter(tmp_path, runner).converse(
            codex_request(), sink=RecordingSink(), cancel=token
        )
    assert stopped["asked"] is True
