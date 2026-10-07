"""프롬프트는 argv 가 아니라 stdin 으로 간다 (WORK-012 WP2 수정 1 W-1).

리눅스는 인자 하나에 128KiB(`MAX_ARG_STRLEN`) 상한이 있다. 최종 합성 프롬프트(재전사 전량 + AI 맥락 목록)는 운영 회의 크기만으로
그 상한을 넘는다(be-wp2-fix1-report §W-1 셈). Codex(`exec … -`)·Claude(`--print`, 프롬프트 인자 없음) 모두 stdin 을 프롬프트로 읽는다.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from ax_workspace.modules.ax_execution.ai import AiConversationRequest, AiDelegatedToolContext, AiGenerationRequest
from ax_workspace.platform.claude_cli import ClaudeCliProviderAdapter
from ax_workspace.platform.cli_process import ProcessResult, subprocess_runner
from ax_workspace.platform.codex_cli import CodexCliMcpServer, CodexCliProfile, CodexCliProviderAdapter

BIG = "가" * 200_000  # UTF-8 600,000 바이트 — 128KiB 인자 상한의 4배 넘게


@pytest.mark.serial
def test_the_process_runner_streams_a_prompt_far_larger_than_one_argv_slot_through_stdin(tmp_path) -> None:
    """실제 자식 프로세스 — 600KB 프롬프트가 stdin 으로 다 닿는다(argv 였으면 리눅스에서 E2BIG)."""
    script = "import sys; data = sys.stdin.read(); print(len(data.encode('utf-8')))"
    result = subprocess_runner(sys.executable, ["-c", script], tmp_path, {"PATH": "/usr/bin:/bin"}, 30, stdin_text=BIG)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(len(BIG.encode("utf-8")))


@pytest.mark.serial
def test_without_a_prompt_the_child_still_sees_an_empty_closed_stdin(tmp_path) -> None:
    result = subprocess_runner(sys.executable, ["-c", "import sys; print(repr(sys.stdin.read()))"], tmp_path, {"PATH": "/usr/bin:/bin"}, 30)
    assert result.stdout.strip() == "''"


def _codex(tmp_path, runner) -> CodexCliProviderAdapter:
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    return CodexCliProviderAdapter(
        CodexCliProfile(runtime_home=tmp_path / "runtime", auth_file=auth), runner=runner,
        scax_mcp_server=CodexCliMcpServer(command="python", arguments=(), environment={}),
    )


@pytest.mark.parametrize("session_ref", [None, "019a0000-0000-7000-8000-000000000000"])
def test_codex_conversation_puts_dash_in_argv_and_the_prompt_on_stdin(tmp_path, session_ref) -> None:
    seen: dict = {}

    def runner(command, arguments, cwd, environment, timeout, on_line=None, should_cancel=None, stdin_text=None):
        seen["arguments"], seen["stdin"] = arguments, stdin_text
        payload = {"body": "답", "elements": [], "follow_up_candidates": []}
        Path(arguments[arguments.index("--output-last-message") + 1]).write_text(json.dumps(payload), encoding="utf-8")
        return ProcessResult("", "", 0)

    _codex(tmp_path, runner).converse(AiConversationRequest(BIG, session_ref, [], AiDelegatedToolContext("mina", "x")))
    assert seen["arguments"][-1] == "-"
    if session_ref:
        assert seen["arguments"][:3] == ["exec", "resume", session_ref]
    assert BIG in seen["stdin"]
    assert max(len(argument.encode()) for argument in seen["arguments"]) < 128 * 1024


def test_codex_generation_also_reads_the_prompt_from_stdin(tmp_path) -> None:
    seen: dict = {}

    def runner(command, arguments, cwd, environment, timeout, stdin_text=None):
        seen["arguments"], seen["stdin"] = arguments, stdin_text
        Path(arguments[arguments.index("--output-last-message") + 1]).write_text(json.dumps({"x": 1}), encoding="utf-8")
        return ProcessResult("", "", 0)

    _codex(tmp_path, runner).generate(AiGenerationRequest(prompt=BIG, output_schema={"type": "object"}))
    assert seen["arguments"][-1] == "-" and seen["stdin"] == BIG


def test_claude_conversation_and_generation_keep_the_prompt_out_of_argv(tmp_path, monkeypatch) -> None:
    from ax_workspace.platform import claude_cli
    from ax_workspace.platform.codex_cli import CodexCliMcpServer as Server

    seen: list[dict] = []

    def runner(command, arguments, cwd, environment, timeout, on_line=None, should_cancel=None, stdin_text=None):
        seen.append({"arguments": arguments, "stdin": stdin_text})
        if on_line is not None:
            on_line(json.dumps({"type": "result", "subtype": "success", "session_id": "s1",
                                "structured_output": {"body": "답", "elements": [], "follow_up_candidates": []}}))
        return ProcessResult(json.dumps({"structured_output": {"x": 1}}), "", 0)

    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: "/usr/bin/claude")
    adapter = ClaudeCliProviderAdapter(scax_mcp_server=Server(command="python", arguments=(), environment={}), runner=runner)
    try:
        adapter.converse(AiConversationRequest(BIG, "s0", [], AiDelegatedToolContext("mina", "x")))
    except Exception:  # noqa: BLE001 — 응답 해석은 이 시험의 몫이 아니다. argv·stdin 만 본다
        pass
    try:
        adapter.generate(AiGenerationRequest(prompt=BIG, output_schema={"type": "object"}))
    except Exception:  # noqa: BLE001
        pass
    assert len(seen) == 2
    for call in seen:
        assert BIG in (call["stdin"] or "")
        assert all(BIG not in argument for argument in call["arguments"])
    assert "--resume" in seen[0]["arguments"]


# ── WP2 재검수 W-r2-3 · W-r2-4 ─────────────────────────────────────────────────────────────────────────────────


def test_a_runner_that_cannot_take_stdin_is_refused_before_it_runs(tmp_path) -> None:
    """stdin 을 못 받는 러너로 프롬프트를 조용히 빼고 돌리지 않는다 — 부르기 전에 오류(빈 프롬프트·이중 실행 없음)."""
    from ax_workspace.platform.cli_process import invoke_plain_runner, invoke_runner

    calls: list = []

    def old_runner(command, arguments, cwd, environment, timeout, on_line=None, should_cancel=None):
        calls.append(arguments)
        return ProcessResult("", "", 0)

    with pytest.raises(TypeError, match="stdin"):
        invoke_runner(old_runner, "codex", ["-"], tmp_path, {}, 5, None, None, stdin_text="프롬프트")
    with pytest.raises(TypeError, match="stdin"):
        invoke_plain_runner(old_runner, "codex", ["-"], tmp_path, {}, 5, "프롬프트")
    assert calls == []


def test_a_type_error_inside_the_runner_is_not_retried_as_another_call_shape(tmp_path) -> None:
    """러너 안에서 난 `TypeError` 가 'on_line' 같은 낱말을 품어도 다른 모양으로 다시 부르지 않는다 — CLI 를 두 번 띄우지 않는다."""
    from ax_workspace.platform.cli_process import invoke_runner

    calls: list = []

    def runner(command, arguments, cwd, environment, timeout, on_line=None, should_cancel=None, stdin_text=None):
        calls.append(stdin_text)
        raise TypeError("unexpected keyword argument 'on_line' somewhere deep inside")

    with pytest.raises(TypeError):
        invoke_runner(runner, "codex", ["-"], tmp_path, {}, 5, None, None, stdin_text="프롬프트")
    assert calls == ["프롬프트"]


@pytest.mark.serial
def test_the_prompt_is_written_as_utf8_whatever_the_childs_locale(tmp_path) -> None:
    """W-r2-4 — `Popen(encoding="utf-8")`: 한국어 프롬프트가 로케일과 무관하게 UTF-8 바이트로 닿는다."""
    script = "import sys; data = sys.stdin.buffer.read(); print(len(data), data.decode('utf-8') == '가나다')"
    result = subprocess_runner(sys.executable, ["-c", script], tmp_path, {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"}, 30, stdin_text="가나다")
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["9", "True"]
