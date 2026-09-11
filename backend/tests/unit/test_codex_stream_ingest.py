"""`codex exec --json` lines are consumed while the process runs; only observed facts become events."""
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import time

import pytest

from ax_workspace.modules.ax_execution.ai import AiConversationRequest, AiDelegatedToolContext, ProviderCancelled
from ax_workspace.platform.codex_cli import CodexCliMcpServer, CodexCliProfile, CodexCliProviderAdapter, CodexEventIngest, ProcessResult


class RecordingSink:
    def __init__(self) -> None:
        self.events = []

    def accept(self, event) -> None:
        self.events.append(event)


def _lines() -> list[str]:
    return [
        json.dumps({"type": "thread.started", "thread_id": "thread_1"}),
        json.dumps({"type": "turn.started"}),
        json.dumps({"type": "item.completed", "item": {"id": "item_0", "type": "reasoning", "text": "secret chain of thought"}}),
        json.dumps({"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": "먼저 업무를 조회합니다."}}),
        json.dumps({"type": "item.started", "item": {"id": "item_2", "type": "mcp_tool_call", "server": "scax", "tool": "task_list", "arguments": {"limit": 5}, "status": "in_progress"}}),
        json.dumps({"type": "item.completed", "item": {"id": "item_2", "type": "mcp_tool_call", "server": "scax", "tool": "task_list", "result": {"content": [{"type": "text", "text": "[]"}]}, "status": "completed"}}),
        json.dumps({"type": "item.completed", "item": {"id": "item_3", "type": "agent_message", "text": "내 업무는 없습니다."}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 3}}),
    ]


def test_ingest_emits_only_observed_events_and_never_reasoning() -> None:
    sink = RecordingSink()
    ingest = CodexEventIngest(sink)
    for line in _lines():
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
    assert all("secret" not in json.dumps(asdict(event), default=str) for event in sink.events)
    [tool] = ingest.tool_invocations()
    assert tool.tool_name == "task_list" and tool.state == "completed" and tool.input_summary == "입력: limit=5"
    assert tool.started_at is not None and tool.completed_at is not None and tool.latency_ms is not None and tool.latency_ms >= 0
    assert ingest.usage == {"input_tokens": 10, "output_tokens": 3} and ingest.session_ref == "thread_1"


def test_tool_latency_is_not_estimated_without_an_observed_start() -> None:
    ingest = CodexEventIngest(None)
    ingest.consume_line(json.dumps({"type": "item.completed", "item": {"id": "x", "type": "mcp_tool_call", "tool": "task_get", "status": "completed", "result": {}}}))
    [tool] = ingest.tool_invocations()
    assert tool.started_at is None and tool.completed_at is not None and tool.latency_ms is None


def _adapter(tmp_path, runner) -> CodexCliProviderAdapter:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    return CodexCliProviderAdapter(
        CodexCliProfile(runtime_home=tmp_path / "runtime", auth_file=auth_file, timeout_seconds=5),
        runner=runner,
        scax_mcp_server=CodexCliMcpServer(command="/usr/bin/python3", arguments=("-m", "x"), environment={}),
    )


def _request() -> AiConversationRequest:
    return AiConversationRequest(prompt="내 업무", provider_session_ref=None, context_references=[], delegated_tool_context=AiDelegatedToolContext("mina", "exec-1"))


def test_adapter_streams_lines_to_the_sink_before_the_process_ends(tmp_path) -> None:
    sink = RecordingSink()
    seen_during_run: list[int] = []

    def runner(command, arguments, cwd, environment, timeout, on_line=None, should_cancel=None) -> ProcessResult:
        for line in _lines():
            on_line(line + "\n")
            seen_during_run.append(len(sink.events))  # the sink already has the event when the next line arrives
        Path(arguments[arguments.index("--output-last-message") + 1]).write_text(
            json.dumps({"body": "내 업무는 없습니다.", "follow_up_candidates": []}),
            encoding="utf-8",
        )
        return ProcessResult("\n".join(_lines()), "", 0)

    result = _adapter(tmp_path, runner).converse(_request(), sink=sink)
    assert seen_during_run[-1] == len(sink.events) == 6 and seen_during_run[0] == 0
    assert result.body == "내 업무는 없습니다." and result.usage == {"input_tokens": 10, "output_tokens": 3}
    assert [tool.tool_name for tool in result.tool_invocations] == ["task_list"]


def test_adapter_stops_when_the_cancel_token_is_set(tmp_path) -> None:
    class Token:
        def __init__(self) -> None:
            self.flag = False

        def is_set(self) -> bool:
            return self.flag

    token = Token()
    stopped = {"asked": False}

    def runner(command, arguments, cwd, environment, timeout, on_line=None, should_cancel=None) -> ProcessResult:
        on_line(json.dumps({"type": "turn.started"}) + "\n")
        token.flag = True
        # Like the real runner: notice the cancel request and stop the process without a final message.
        stopped["asked"] = should_cancel()
        return ProcessResult("", "", -2)

    with pytest.raises(ProviderCancelled):
        _adapter(tmp_path, runner).converse(_request(), sink=RecordingSink(), cancel=token)
    assert stopped["asked"] is True


def test_real_subprocess_runner_streams_and_cancels(tmp_path) -> None:
    from ax_workspace.platform.codex_cli import _subprocess_runner

    lines: list[str] = []
    # Emit a few lines, then wait far longer than this test may take. Whether the process is stopped is then a fact
    # about cancellation, not about which thread the machine happened to schedule: without it the runner would hit
    # its timeout and raise instead of returning.
    script = (
        "import sys,time\n"
        "for i in range(5):\n"
        "    print('{\"type\":\"line\",\"i\":%d}' % i, flush=True)\n"
        "time.sleep(600)\n"
    )
    started = time.monotonic()
    result = _subprocess_runner(
        "python3", ["-c", script], tmp_path, {"PATH": "/usr/bin:/bin"}, 10,
        on_line=lines.append, should_cancel=lambda: len(lines) >= 5,
    )
    assert len(lines) == 5, "the lines emitted before the process blocked should all have been ingested"
    # It returned rather than timing out, and the process it stopped did not exit on its own terms.
    assert time.monotonic() - started < 10
    assert result.returncode != 0
    legacy = _subprocess_runner("python3", ["-c", "print('ok')"], tmp_path, {"PATH": "/usr/bin:/bin"}, 10)
    assert legacy.returncode == 0 and legacy.stdout.strip() == "ok"


def test_sink_failure_stops_the_run_and_fails_the_turn_instead_of_completing_silently(tmp_path) -> None:
    from ax_workspace.modules.ax_execution.ai import ProviderRequestFailed
    from ax_workspace.platform.codex_cli import _subprocess_runner

    class FailingSink:
        def __init__(self) -> None:
            self.calls = 0

        def accept(self, event) -> None:
            self.calls += 1
            raise RuntimeError("database unavailable")

    # Real subprocess path: the first ingested event fails -> the process is stopped, not run to completion.
    lines: list[str] = []
    # One line, then a wait far longer than this test: being stopped is then a fact about the failed ingest rather
    # than about scheduling. Without stopping, the runner would raise TimeoutExpired instead of EventIngestFailed.
    script = "import time\nprint('{\"type\":\"turn.started\"}', flush=True)\ntime.sleep(600)\n"

    def boom(line: str) -> None:
        lines.append(line)
        raise RuntimeError("database unavailable")

    started = time.monotonic()
    with pytest.raises(Exception) as raised:
        _subprocess_runner("python3", ["-c", script], tmp_path, {"PATH": "/usr/bin:/bin"}, 10, on_line=boom)
    assert raised.type.__name__ == "EventIngestFailed" and len(lines) == 1 and time.monotonic() - started < 10

    # Adapter path: the failure surfaces as a provider failure with provenance, never as a completed result.
    def runner(command, arguments, cwd, environment, timeout, on_line=None, should_cancel=None) -> ProcessResult:
        from ax_workspace.platform.codex_cli import EventIngestFailed

        try:
            on_line(json.dumps({"type": "turn.started"}) + "\n")
        except Exception as error:  # noqa: BLE001
            raise EventIngestFailed("provider event could not be persisted") from error
        raise AssertionError("must not continue after a sink failure")

    sink = FailingSink()
    with pytest.raises(ProviderRequestFailed, match="events could not be persisted"):
        _adapter(tmp_path, runner).converse(_request(), sink=sink)
    assert sink.calls == 1


def test_cancel_and_timeout_stop_the_whole_process_group_including_children(tmp_path) -> None:
    import os

    from ax_workspace.platform.codex_cli import _subprocess_runner

    # The parent prints its child's pid, then both idle; cancelling must take the child (an MCP-like helper) with it.
    script = (
        "import subprocess, sys, time, json\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(json.dumps({'type': 'child', 'pid': child.pid}), flush=True)\n"
        "time.sleep(60)\n"
    )
    lines: list[str] = []
    result = _subprocess_runner("python3", ["-c", script], tmp_path, {"PATH": "/usr/bin:/bin"}, 30, on_line=lines.append, should_cancel=lambda: len(lines) >= 1)
    child_pid = json.loads(lines[0])["pid"]
    deadline = time.monotonic() + 5
    alive = True
    while time.monotonic() < deadline and alive:
        try:
            os.kill(child_pid, 0)
            alive = True
            time.sleep(0.1)
        except ProcessLookupError:
            alive = False
    assert alive is False, "the MCP-like child survived the cancellation"
    assert result.returncode != 0
