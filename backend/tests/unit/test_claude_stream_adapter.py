"""Claude stream parsing and injected-runner behavior stay deterministic and process-free."""

from dataclasses import asdict
import json

import pytest

from ax_workspace.modules.ax_execution.ai import ProviderCancelled, ProviderRequestFailed
from ax_workspace.platform.claude_cli import ClaudeEventIngest
from ax_workspace.platform.cli_process import ProcessResult
from claude_stream_support import (
    RecordingSink,
    claude_adapter,
    claude_lines,
    claude_request,
)


def test_ingest_emits_only_observed_events_and_never_the_structured_output_call() -> None:
    sink = RecordingSink()
    ingest = ClaudeEventIngest(sink)
    for line in claude_lines():
        ingest.consume_line(line)
    kinds = [(event.kind, event.item_type) for event in sink.events]
    assert kinds == [
        ("turn_started", None),
        ("item_completed", "agent_message"),
        ("item_started", "mcp_tool_call"),
        ("item_completed", "mcp_tool_call"),
        ("turn_completed", None),
    ]
    texts = [event.text for event in sink.events if event.text]
    # The final JSON answer never arrives as a text block — only as the StructuredOutput call's input.
    assert texts == ["먼저 업무를 조회합니다."]
    assert all("StructuredOutput" not in json.dumps(asdict(event), default=str) for event in sink.events)
    [tool] = ingest.tool_invocations()
    assert tool.tool_name == "task_list" and tool.state == "completed"
    assert tool.input_summary == "입력: limit=5"
    assert tool.started_at is not None and tool.completed_at is not None
    assert tool.latency_ms is not None and tool.latency_ms >= 0
    assert ingest.usage == {"input_tokens": 10, "output_tokens": 3, "service_tier": "standard"}
    assert ingest.session_ref == "sess_1"
    assert ingest.run_ref == "run_1"
    assert ingest.structured_payload == {"body": "내 업무는 없습니다.", "elements": [], "follow_up_candidates": []}


def test_a_completed_call_the_stream_echoes_no_content_for_is_never_reported_as_no_result() -> None:
    """Observed live: a real `my_task_list` call that returned 29 tasks still echoed no `content` in the
    stream. Reporting that as "결과 없음" (no result) would read as an empty list, which is false."""
    ingest = ClaudeEventIngest(None)
    ingest.consume_line(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "call_1", "name": "mcp__scax__my_task_list", "input": {}},
    ]}}))
    ingest.consume_line(json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "call_1", "is_error": False},
    ]}}))
    [tool] = ingest.tool_invocations()
    assert tool.state == "completed"
    assert tool.result_summary == "결과 수신"


def test_a_bare_list_result_arrives_wrapped_and_is_unwrapped_before_summarizing() -> None:
    """Observed live: our MCP server's bare-list return relays as `{"result": [...]}`, not the list itself."""
    ingest = ClaudeEventIngest(None)
    ingest.consume_line(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "call_1", "name": "mcp__scax__my_task_list", "input": {}},
    ]}}))
    tasks = [{"task_id": "t1"}, {"task_id": "t2"}]
    ingest.consume_line(json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "call_1", "is_error": False, "content": json.dumps({"result": tasks})},
    ]}}))
    [tool] = ingest.tool_invocations()
    assert tool.result_summary == "결과: 2건 조회"


def test_an_empty_string_content_is_also_reported_as_received_not_no_result() -> None:
    """Observed live: the same missing-content case can arrive as `""` instead of an absent field."""
    ingest = ClaudeEventIngest(None)
    ingest.consume_line(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "call_1", "name": "mcp__scax__my_task_list", "input": {}},
    ]}}))
    ingest.consume_line(json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "call_1", "is_error": False, "content": ""},
    ]}}))
    [tool] = ingest.tool_invocations()
    assert tool.result_summary == "결과 수신"


def test_a_genuinely_empty_collection_result_is_still_reported_precisely() -> None:
    """Unlike a missing echo, a real `[]` answer (the tool found nothing) keeps its precise "0건" wording."""
    ingest = ClaudeEventIngest(None)
    ingest.consume_line(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "call_1", "name": "mcp__scax__my_task_list", "input": {}},
    ]}}))
    ingest.consume_line(json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "call_1", "is_error": False, "content": "[]"},
    ]}}))
    [tool] = ingest.tool_invocations()
    assert tool.result_summary == "결과: 0건 조회"


def test_a_tool_result_for_an_unseen_call_id_is_ignored_not_crashed_on() -> None:
    ingest = ClaudeEventIngest(None)
    ingest.consume_line(json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "never-started", "content": "ok"},
    ]}}))
    assert ingest.tool_invocations() == []


def test_adapter_streams_lines_to_the_sink_before_the_process_ends() -> None:
    sink = RecordingSink()
    seen_during_run: list[int] = []

    def runner(command, arguments, cwd, environment, timeout, on_line=None, should_cancel=None):
        for line in claude_lines():
            on_line(line + "\n")
            seen_during_run.append(len(sink.events))
        return ProcessResult("", "", 0)

    result = claude_adapter(runner).converse(claude_request(), sink=sink)
    # Unlike Codex's `thread.started`, the first Claude line (`system` init) already carries turn_started.
    assert seen_during_run[-1] == len(sink.events) == 5 and seen_during_run[0] == 1
    assert result.body == "내 업무는 없습니다."
    assert result.usage == {"input_tokens": 10, "output_tokens": 3, "service_tier": "standard"}
    assert [tool.tool_name for tool in result.tool_invocations] == ["task_list"]


def test_adapter_stops_when_the_cancel_token_is_set() -> None:
    class Token:
        def __init__(self) -> None:
            self.flag = False

        def is_set(self) -> bool:
            return self.flag

    token = Token()
    stopped = {"asked": False}

    def runner(command, arguments, cwd, environment, timeout, on_line=None, should_cancel=None):
        on_line(json.dumps({"type": "system", "session_id": "sess_1"}) + "\n")
        token.flag = True
        stopped["asked"] = should_cancel()
        return ProcessResult("", "", -2)

    with pytest.raises(ProviderCancelled):
        claude_adapter(runner).converse(claude_request(), sink=RecordingSink(), cancel=token)
    assert stopped["asked"] is True


def test_a_usage_limit_error_fails_the_turn_instead_of_returning_a_stale_answer() -> None:
    def runner(command, arguments, cwd, environment, timeout, on_line=None, should_cancel=None):
        for line in claude_lines(is_error=True):
            on_line(line + "\n")
        return ProcessResult("", "", 0)

    with pytest.raises(ProviderRequestFailed):
        claude_adapter(runner).converse(claude_request(), sink=RecordingSink())
