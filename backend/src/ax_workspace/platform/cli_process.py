"""Provider-neutral subprocess plumbing shared by every CLI-backed AI adapter.

Streams stdout lines to a callback as they arrive, stops the whole process group (not just the leading
process) on cancel/timeout so no MCP child is orphaned, and treats a failing line callback as fatal: the
stream stops and `EventIngestFailed` is raised so a turn can never look complete with missing persisted
events.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import signal
import subprocess
import threading
import time
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ProcessResult:
    stdout: str
    stderr: str
    returncode: int


ProcessRunner = Callable[[str, list[str], Path, dict[str, str], int], ProcessResult]


@dataclass(frozen=True, slots=True)
class ScaxMcpServer:
    """Composition-owned SCAX stdio server configuration for an isolated CLI turn, any provider."""

    command: str
    arguments: tuple[str, ...]
    environment: dict[str, str]
    #: 이 turn 에 열 도구 이름. 비면 서버가 노출하는 전부다 — 회의 배치는 레지스트리로 좁힌다
    #: (SCAX-SPEC-004 §7.2-3). 서버 id 접두를 붙이지 않는다: 틀리면 조용히 도구 0개로 돈다.
    enabled_tools: tuple[str, ...] = ()


class EventIngestFailed(RuntimeError):
    """A sink/on_line callback failed while the provider was running; the execution was stopped."""


def structured_body(payload: Any, output_schema: dict[str, Any]) -> str:
    """Hand back what the caller asked for.

    The daily report asks for a single text field and wants that text. Every other caller — Meeting refinement and
    summary — passes its own schema and parses the structure itself, so unwrapping a `body` key that its schema never
    mentioned would turn every one of those generations into an invalid response.
    """
    if list(output_schema.get("properties", {})) == ["body"]:
        return payload["body"].strip()
    return json.dumps(payload, ensure_ascii=False)


def invoke_runner(runner, command, arguments, cwd, environment, timeout_seconds, on_line, should_cancel) -> ProcessResult:
    """Call a streaming runner; fall back to the legacy 5-argument runner used by older tests."""
    try:
        return runner(command, arguments, cwd, environment, timeout_seconds, on_line=on_line, should_cancel=should_cancel)
    except TypeError as error:
        if "on_line" not in str(error) and "positional" not in str(error):
            raise
        return runner(command, arguments, cwd, environment, timeout_seconds)


def subprocess_runner(
    command: str,
    arguments: list[str],
    cwd: Path,
    environment: dict[str, str],
    timeout_seconds: int,
    on_line: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> ProcessResult:
    """Run the CLI in its own process group and stream stdout lines to `on_line` as they arrive.

    Cancel and timeout stop the whole group (a CLI may spawn MCP child processes), so nothing is orphaned.
    """
    process = subprocess.Popen(
        [command, *arguments],
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    stdout_lines: list[str] = []
    stderr_chunks: list[str] = []
    ingest_failure: list[BaseException] = []
    deadline = time.monotonic() + timeout_seconds

    def drain_stderr() -> None:
        assert process.stderr is not None
        for chunk in process.stderr:
            stderr_chunks.append(chunk)

    def drain_stdout() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            stdout_lines.append(line)
            if on_line is not None and not ingest_failure:
                try:
                    on_line(line)
                except Exception as error:  # noqa: BLE001 - recorded and surfaced; ingestion of further lines stops
                    ingest_failure.append(error)

    readers = [threading.Thread(target=drain_stdout, daemon=True), threading.Thread(target=drain_stderr, daemon=True)]
    for reader in readers:
        reader.start()
    while process.poll() is None:
        if ingest_failure:
            _stop_process_group(process)
            break
        if should_cancel is not None and should_cancel():
            _stop_process_group(process)
            break
        if time.monotonic() > deadline:
            _stop_process_group(process)
            for reader in readers:
                reader.join(timeout=2)
            raise subprocess.TimeoutExpired([command, *arguments], timeout_seconds)
        time.sleep(0.05)
    for reader in readers:
        reader.join(timeout=5)
    returncode = process.wait(timeout=5) if process.poll() is None else process.returncode
    if ingest_failure:
        raise EventIngestFailed("provider event could not be persisted") from ingest_failure[0]
    return ProcessResult("".join(stdout_lines), "".join(stderr_chunks), returncode if returncode is not None else -1)


def _stop_process_group(process: subprocess.Popen) -> None:
    """Stop the process and every child in its process group (SIGINT, SIGTERM, then SIGKILL) and reap it."""
    try:
        pgid = os.getpgid(process.pid)
    except ProcessLookupError:
        return
    for signum, grace in ((signal.SIGINT, 3.0), (signal.SIGTERM, 3.0), (signal.SIGKILL, 3.0)):
        if process.poll() is not None and signum is not signal.SIGKILL:
            # The parent already exited; still sweep the group once so MCP children do not linger.
            _signal_group(pgid, signum)
            return
        _signal_group(pgid, signum)
        try:
            process.wait(timeout=grace)
            _signal_group(pgid, signal.SIGTERM if signum is signal.SIGINT else signum)
            return
        except subprocess.TimeoutExpired:
            continue


def _signal_group(pgid: int, signum: "signal.Signals") -> None:
    try:
        os.killpg(pgid, signum)
    except ProcessLookupError:
        pass
    except PermissionError:
        pass
