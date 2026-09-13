"""The real subprocess runner streams output and stops its whole process group."""

import json
import time

import pytest

from ax_workspace.platform.codex_cli import ProcessResult
from codex_stream_support import codex_adapter, codex_request


def test_real_subprocess_runner_streams_and_cancels(tmp_path) -> None:
    from ax_workspace.platform.codex_cli import _subprocess_runner

    lines: list[str] = []
    # Emit a few lines, then wait far longer than this test may take. Whether the process is stopped is then a fact
    # about cancellation, not about which thread the machine happened to schedule: without it the runner would hit
    # its timeout and raise instead of returning.
    script = (
        "import sys,time\n"
        "for i in range(5):\n"
        '    print(\'{"type":"line","i":%d}\' % i, flush=True)\n'
        "time.sleep(600)\n"
    )
    started = time.monotonic()
    result = _subprocess_runner(
        "python3",
        ["-c", script],
        tmp_path,
        {"PATH": "/usr/bin:/bin"},
        10,
        on_line=lines.append,
        should_cancel=lambda: len(lines) >= 5,
    )
    assert len(lines) == 5, (
        "the lines emitted before the process blocked should all have been ingested"
    )
    # It returned rather than timing out, and the process it stopped did not exit on its own terms.
    assert time.monotonic() - started < 10
    assert result.returncode != 0
    legacy = _subprocess_runner(
        "python3", ["-c", "print('ok')"], tmp_path, {"PATH": "/usr/bin:/bin"}, 10
    )
    assert legacy.returncode == 0 and legacy.stdout.strip() == "ok"


def test_sink_failure_stops_the_run_and_fails_the_turn_instead_of_completing_silently(
    tmp_path,
) -> None:
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
    script = (
        'import time\nprint(\'{"type":"turn.started"}\', flush=True)\ntime.sleep(600)\n'
    )

    def boom(line: str) -> None:
        lines.append(line)
        raise RuntimeError("database unavailable")

    started = time.monotonic()
    with pytest.raises(Exception) as raised:
        _subprocess_runner(
            "python3",
            ["-c", script],
            tmp_path,
            {"PATH": "/usr/bin:/bin"},
            10,
            on_line=boom,
        )
    assert (
        raised.type.__name__ == "EventIngestFailed"
        and len(lines) == 1
        and time.monotonic() - started < 10
    )

    # Adapter path: the failure surfaces as a provider failure with provenance, never as a completed result.
    def runner(
        command, arguments, cwd, environment, timeout, on_line=None, should_cancel=None
    ) -> ProcessResult:
        from ax_workspace.platform.codex_cli import EventIngestFailed

        try:
            on_line(json.dumps({"type": "turn.started"}) + "\n")
        except Exception as error:  # noqa: BLE001
            raise EventIngestFailed("provider event could not be persisted") from error
        raise AssertionError("must not continue after a sink failure")

    sink = FailingSink()
    with pytest.raises(ProviderRequestFailed, match="events could not be persisted"):
        codex_adapter(tmp_path, runner).converse(codex_request(), sink=sink)
    assert sink.calls == 1


def test_cancel_and_timeout_stop_the_whole_process_group_including_children(
    tmp_path,
) -> None:
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
    result = _subprocess_runner(
        "python3",
        ["-c", script],
        tmp_path,
        {"PATH": "/usr/bin:/bin"},
        30,
        on_line=lines.append,
        should_cancel=lambda: len(lines) >= 1,
    )
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
