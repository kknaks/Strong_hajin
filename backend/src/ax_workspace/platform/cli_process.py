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
import inspect
import os
import re
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


def invalid_response_message(output_schema: dict[str, Any] | None) -> str:
    """무효 응답을 **누가 읽는지**에 맞춘 한 줄.

    대화는 사람이 채팅창에서 그대로 읽는다. 자기 스키마를 건 호출(회의 배치·합성)은 사람에게 다시 물을
    자리가 없고, 이 줄은 그 회차의 기록에만 남는다 — 「다시 요청해 주세요」는 거기서 읽을 사람이 없는 말이다.

    **어댑터 둘이 함께 쓴다** — Codex·Claude 가 각자 `converse` 를 갖고 같은 판정을 하므로, 한쪽에만
    두면 다른 쪽이 조용히 옛 문구로 돌아간다.
    """
    if output_schema is not None:
        return "요청한 형식의 응답을 받지 못했습니다."
    return "답변 형식을 확인하지 못했습니다. 다시 요청해 주세요."


#: 로그로 남길 stderr 요약의 상한(자). 원인 한 줄이면 충분하고, 길면 프롬프트 조각이 따라 나온다.
STDERR_SUMMARY_LIMIT = 600
_MASK = "***"
#: 비밀이 실릴 수 있는 모양들. 키 이름이 있으면 값만, 없으면 토큰 자체를 가린다.
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+"), rf"\1 {_MASK}"),
    (
        re.compile(
            r"(?i)\b([A-Za-z0-9_-]*(?:api[_-]?key|token|secret|password|passwd|authorization|cookie|credential)s?)"
            r"(\"?\s*[:=]\s*\"?)[^\s\"',;]+"
        ),
        rf"\1\2{_MASK}",
    ),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?"), _MASK),
    (re.compile(r"\b(?:sk|pk|rk|sess)-[A-Za-z0-9_-]{8,}"), _MASK),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"), rf"\1{_MASK}@"),
)


def summarize_stderr(stderr: str, *, limit: int = STDERR_SUMMARY_LIMIT) -> str:
    """CLI 가 실패하며 남긴 stderr 를 **로그 한 줄**로 줄인다 — 비밀은 가리고 길이는 자른다.

    원인은 대개 끝에 있으므로 뒤쪽을 남긴다. 비어 있으면 빈 문자열이다.
    """
    text = " | ".join(line.strip() for line in stderr.splitlines() if line.strip())
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    if len(text) > limit:
        text = "…" + text[-(limit - 1):]
    return text


def _accepts(runner: Callable[..., Any], name: str) -> bool:
    """러너가 그 키워드를 **시그니처로** 받는가 — `**kwargs` 도 받는 것으로 본다.

    호출해 보고 `TypeError` 글자를 맞춰 다른 모양으로 다시 부르지 않는다(WORK-012 WP2 재검수 W-r2-3): 러너 안에서 다른
    이유로 난 `TypeError` 가 같은 낱말을 품으면 **CLI 프로세스를 두 번 띄우고**, stdin 없는 되돌이는 Codex 를 빈 프롬프트로
    돌린다. 부르기 **전에** 모양을 한 번 정한다.
    """
    try:
        parameters = inspect.signature(runner).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == name or parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters
    )


def invoke_runner(
    runner, command, arguments, cwd, environment, timeout_seconds, on_line, should_cancel, stdin_text: str | None = None
) -> ProcessResult:
    """스트리밍 러너를 부른다 — 모양은 **러너 시그니처로 한 번 정하고** 한 번만 부른다.

    `stdin_text` — **프롬프트는 argv 가 아니라 stdin 으로 넘긴다** (WORK-012 WP2 수정 1 W-1 · 리눅스 한 인자 128KiB 상한).
    제품 러너(`subprocess_runner`)는 셋 다 받는다. 프롬프트를 넘겨야 하는데 `stdin_text` 를 못 받는 러너면 **조용히 빼고 돌리지
    않고** 오류로 끝낸다 — Codex 는 argv 의 `-` 를 보고 빈 stdin 을 프롬프트로 읽게 된다. `on_line` 을 모르는 옛 시험 러너는
    5인자로 부른다(프롬프트가 없는 호출에만 해당).
    """
    if stdin_text is not None:
        if not _accepts(runner, "stdin_text"):
            raise TypeError("this runner cannot take the prompt on stdin (stdin_text)")
        return runner(
            command, arguments, cwd, environment, timeout_seconds,
            on_line=on_line, should_cancel=should_cancel, stdin_text=stdin_text,
        )
    if _accepts(runner, "on_line"):
        return runner(command, arguments, cwd, environment, timeout_seconds, on_line=on_line, should_cancel=should_cancel)
    return runner(command, arguments, cwd, environment, timeout_seconds)


def invoke_plain_runner(runner, command, arguments, cwd, environment, timeout_seconds, stdin_text: str) -> ProcessResult:
    """단발 생성(`generate`)용 — 프롬프트를 stdin 으로 넘긴다. 못 받는 러너면 오류다(조용히 빼고 돌리지 않는다 · W-r2-3)."""
    if not _accepts(runner, "stdin_text"):
        raise TypeError("this runner cannot take the prompt on stdin (stdin_text)")
    return runner(command, arguments, cwd, environment, timeout_seconds, stdin_text=stdin_text)


def subprocess_runner(
    command: str,
    arguments: list[str],
    cwd: Path,
    environment: dict[str, str],
    timeout_seconds: int,
    on_line: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    stdin_text: str | None = None,
) -> ProcessResult:
    """Run the CLI in its own process group and stream stdout lines to `on_line` as they arrive.

    Cancel and timeout stop the whole group (a CLI may spawn MCP child processes), so nothing is orphaned.
    `stdin_text` 가 있으면 그 글을 stdin 으로 흘리고 닫는다 — 프롬프트가 argv 한도(128KiB)에 걸리지 않는다. 쓰기는 따로 도는
    스레드가 해서, CLI 가 stdout 을 먼저 쏟아도 파이프가 서로 막히지 않는다. 없으면 지금처럼 `/dev/null`.
    """
    process = subprocess.Popen(
        [command, *arguments],
        cwd=cwd,
        env=environment,
        stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        # 로케일과 무관하게 UTF-8 — 한국어 프롬프트를 stdin 에 쓰고 JSONL 을 읽는다(WP2 재검수 W-r2-4). 깨진 바이트는 대체한다.
        encoding="utf-8",
        errors="replace",
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

    def feed_stdin() -> None:
        assert process.stdin is not None
        try:
            process.stdin.write(stdin_text or "")
        except (BrokenPipeError, OSError):
            pass  # CLI 가 먼저 끝났다 — 결과는 returncode 와 stderr 가 말한다
        finally:
            try:
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

    readers = [threading.Thread(target=drain_stdout, daemon=True), threading.Thread(target=drain_stderr, daemon=True)]
    if stdin_text is not None:
        readers.append(threading.Thread(target=feed_stdin, daemon=True))
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
