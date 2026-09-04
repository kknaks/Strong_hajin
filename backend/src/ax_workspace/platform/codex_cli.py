"""Single production/development adapter for all SCAX LLM calls.

The adapter follows the isolated Codex runtime contract used by
agent-workflow-studio: temporary work directories, a dedicated CODEX_HOME,
no host config/rules/skills/plugins, and guaranteed temporary-file cleanup.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any, Callable

from ax_workspace.modules.ax_execution.ai import (
    AiConversationRequest,
    AiConversationResult,
    AiGeneration,
    AiToolInvocation,
    AiGenerationRequest,
    AiProviderProvenance,
    ProviderRequestFailed,
    ProviderUnavailable,
)


@dataclass(frozen=True, slots=True)
class ProcessResult:
    stdout: str
    stderr: str
    returncode: int


ProcessRunner = Callable[[str, list[str], Path, dict[str, str], int], ProcessResult]


@dataclass(frozen=True, slots=True)
class CodexCliProfile:
    model: str = "gpt-5.6-terra"
    service_tier: str = "fast"
    reasoning_effort: str = "low"
    timeout_seconds: int = 90
    runtime_home: Path = Path(".scax/codex-runtime")
    auth_file: Path = Path.home() / ".codex" / "auth.json"


@dataclass(frozen=True, slots=True)
class CodexCliMcpServer:
    """Composition-owned SCAX stdio server configuration for an isolated Codex turn."""

    command: str
    arguments: tuple[str, ...]
    environment: dict[str, str]


class CodexCliProviderAdapter:
    """Structured one-shot generation; no caller can invoke a CLI process directly."""

    def __init__(
        self,
        profile: CodexCliProfile | None = None,
        runner: ProcessRunner | None = None,
        command: str = "codex",
        scax_mcp_server: CodexCliMcpServer | None = None,
    ) -> None:
        self._profile = profile or CodexCliProfile()
        self._runner = runner or _subprocess_runner
        self._command_name = command
        self._scax_mcp_server = scax_mcp_server

    def generate(self, request: AiGenerationRequest) -> AiGeneration:
        binary = shutil.which(self._command_name)
        if binary is None and self._runner is _subprocess_runner:
            raise ProviderUnavailable("Codex CLI binary is not available")
        command = binary or self._command_name
        runtime_home = prepare_isolated_codex_home(
            self._profile.runtime_home,
            auth_file=self._profile.auth_file,
        )

        with TemporaryDirectory(prefix="scax-codex-") as temporary:
            work_dir = Path(temporary)
            schema_path = work_dir / "daily-report-output-schema.json"
            output_path = work_dir / "daily-report-output.json"
            schema_path.write_text(json.dumps(request.output_schema), encoding="utf-8")
            started = perf_counter()
            try:
                result = self._runner(
                    command,
                    self._arguments(schema_path, output_path, request.prompt),
                    work_dir,
                    {**os.environ, "CODEX_HOME": str(runtime_home)},
                    self._profile.timeout_seconds,
                )
            except subprocess.TimeoutExpired as error:
                raise ProviderRequestFailed("Codex CLI generation timed out") from error
            except OSError as error:
                raise ProviderUnavailable("Codex CLI could not start") from error

            latency_ms = int((perf_counter() - started) * 1000)
            run_ref, thread_ref, observed_model, observed_tier, usage = self._provenance(result.stdout)
            provenance = AiProviderProvenance(
                provider_run_ref=run_ref,
                provider_session_ref=thread_ref,
                requested_model=self._profile.model,
                observed_model=observed_model,
                requested_tier=self._profile.service_tier,
                observed_tier=observed_tier,
                latency_ms=latency_ms,
                usage=usage,
            )
            if result.returncode != 0:
                raise ProviderRequestFailed("Codex CLI generation failed", provenance)
            try:
                payload = json.loads(output_path.read_text(encoding="utf-8"))
                body = payload["body"].strip()
            except (OSError, json.JSONDecodeError, KeyError, AttributeError) as error:
                raise ProviderRequestFailed("Codex CLI returned invalid structured output", provenance) from error
            if not body:
                raise ProviderRequestFailed("Codex CLI returned an empty daily report", provenance)
            return AiGeneration(
                provider_run_ref=run_ref,
                provider_session_ref=thread_ref,
                body=body,
                requested_model=self._profile.model,
                observed_model=observed_model,
                requested_tier=self._profile.service_tier,
                observed_tier=observed_tier,
                latency_ms=latency_ms,
                usage=usage,
            )

    def converse(self, request: AiConversationRequest) -> AiConversationResult:
        """Run one persisted Codex CLI conversation turn under the same isolated policy."""
        binary = shutil.which(self._command_name)
        if binary is None and self._runner is _subprocess_runner:
            raise ProviderUnavailable("Codex CLI binary is not available")
        command = binary or self._command_name
        runtime_home = prepare_isolated_codex_home(
            self._profile.runtime_home,
            auth_file=self._profile.auth_file,
        )
        with TemporaryDirectory(prefix="scax-codex-chat-") as temporary:
            work_dir = Path(temporary)
            output_path = work_dir / "assistant-message.txt"
            prompt = self._conversation_prompt(request)
            started = perf_counter()
            try:
                result = self._runner(
                    command,
                    self._conversation_arguments(request, output_path, prompt),
                    work_dir,
                    self._conversation_environment(request, runtime_home),
                    self._profile.timeout_seconds,
                )
            except subprocess.TimeoutExpired as error:
                raise ProviderRequestFailed("Codex CLI conversation timed out") from error
            except OSError as error:
                raise ProviderUnavailable("Codex CLI could not start") from error
            latency_ms = int((perf_counter() - started) * 1000)
            run_ref, session_ref, _, _, _ = self._provenance(result.stdout)
            provenance = AiProviderProvenance(
                provider_run_ref=run_ref,
                provider_session_ref=session_ref or request.provider_session_ref,
                requested_model=self._profile.model,
                requested_tier=self._profile.service_tier,
                latency_ms=latency_ms,
            )
            if result.returncode != 0:
                raise ProviderRequestFailed("Codex CLI conversation failed", provenance)
            try:
                body = output_path.read_text(encoding="utf-8").strip()
            except OSError as error:
                raise ProviderRequestFailed("Codex CLI returned no conversation response", provenance) from error
            if not body:
                raise ProviderRequestFailed("Codex CLI returned an empty conversation response", provenance)
            return AiConversationResult(
                run_ref,
                session_ref or request.provider_session_ref,
                body,
                self._tool_invocations(result.stdout),
            )

    def _conversation_arguments(
        self,
        request: AiConversationRequest,
        output_path: Path,
        prompt: str,
    ) -> list[str]:
        common = [
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "-c",
            'shell_environment_policy.inherit="none"',
            "-c",
            "features.skip_host_skill_discovery=true",
            "-c",
            "features.plugins=false",
            "-c",
            "features.plugin_sharing=false",
            "-c",
            "features.remote_plugin=false",
            "-m",
            self._profile.model,
            "-c",
            f'service_tier="{self._profile.service_tier}"',
            "-c",
            f'model_reasoning_effort="{self._profile.reasoning_effort}"',
            "--json",
            "--output-last-message",
            str(output_path),
            *self._mcp_overrides(request),
        ]
        if request.provider_session_ref:
            # `exec resume` has no --sandbox flag in Codex CLI 0.153.0; retain
            # the policy using its supported per-run config override.
            return [
                "exec",
                "resume",
                request.provider_session_ref,
                "-c",
                'sandbox_mode="read-only"',
                *common,
                prompt,
            ]
        return ["exec", "--sandbox", "read-only", "--color", "never", *common, prompt]

    def _mcp_overrides(self, request: AiConversationRequest) -> list[str]:
        server = self._scax_mcp_server
        if server is None:
            raise ProviderRequestFailed("SCAX MCP binding is not configured for conversation tools")
        del request
        return [
            "-c",
            f"mcp_servers.scax.command={json.dumps(server.command)}",
            "-c",
            f"mcp_servers.scax.args={json.dumps(list(server.arguments))}",
            "-c",
            "mcp_servers.scax.env_vars=[\"AX_MCP_PERSONA\", \"AX_MCP_CAUSATION_ID\", \"AX_PROFILE\", \"DATABASE_URL\"]",
            "-c",
            'mcp_servers.scax.default_tools_approval_mode="approve"',
        ]

    def _conversation_environment(
        self,
        request: AiConversationRequest,
        runtime_home: Path,
    ) -> dict[str, str]:
        server = self._scax_mcp_server
        if server is None:
            raise ProviderRequestFailed("SCAX MCP binding is not configured for conversation tools")
        return {
            **os.environ,
            "CODEX_HOME": str(runtime_home),
            **server.environment,
            "AX_MCP_PERSONA": request.delegated_tool_context.principal_id,
            "AX_MCP_CAUSATION_ID": request.delegated_tool_context.causation_id,
        }

    @staticmethod
    def _conversation_prompt(request: AiConversationRequest) -> str:
        if not request.context_references:
            return request.prompt
        references = "\n".join(
            f"- {item.get('resource_type', 'resource')}:{item.get('resource_id', '')}: {item.get('summary', '')}"
            for item in request.context_references
        )
        return f"Context references authorized for this turn:\n{references}\n\nUser message:\n{request.prompt}"

    def _arguments(
        self,
        schema_path: Path,
        output_path: Path,
        rendered_prompt: str,
    ) -> list[str]:
        return [
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--ignore-user-config",
            "--ignore-rules",
            "-c",
            'shell_environment_policy.inherit="none"',
            "-c",
            "features.skip_host_skill_discovery=true",
            "-c",
            "features.plugins=false",
            "-c",
            "features.plugin_sharing=false",
            "-c",
            "features.remote_plugin=false",
            "-c",
            "features.skill_search=false",
            "-c",
            "features.skill_mcp_dependency_install=false",
            "--ephemeral",
            "--json",
            "--color",
            "never",
            "-m",
            self._profile.model,
            "-c",
            f'service_tier="{self._profile.service_tier}"',
            "-c",
            f'model_reasoning_effort="{self._profile.reasoning_effort}"',
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
            rendered_prompt,
        ]

    @staticmethod
    def _provenance(
        stdout: str,
    ) -> tuple[str | None, str | None, str | None, str | None, dict[str, Any] | None]:
        run_ref: str | None = None
        thread_ref: str | None = None
        observed_model: str | None = None
        observed_tier: str | None = None
        usage: dict[str, Any] | None = None
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "thread.started":
                thread_ref = event.get("thread_id") or event.get("thread", {}).get("id")
            if event.get("type") in {"turn.started", "turn.completed"}:
                run_ref = event.get("turn_id") or event.get("turn", {}).get("id") or run_ref
            if isinstance(event.get("model"), str):
                observed_model = event["model"]
            if isinstance(event.get("service_tier"), str):
                observed_tier = event["service_tier"]
            if isinstance(event.get("usage"), dict):
                usage = event["usage"]
        return run_ref, thread_ref, observed_model, observed_tier, usage

    @staticmethod
    def _tool_invocations(stdout: str) -> list[AiToolInvocation]:
        """Fold Codex JSONL MCP/tool lifecycle events into redacted, human-readable summaries."""
        calls: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            item = event.get("item") if isinstance(event.get("item"), dict) else event
            item_type = str(item.get("type", event.get("type", "")))
            if "tool" not in item_type and "mcp" not in item_type:
                continue
            call_id = str(item.get("id") or item.get("call_id") or event.get("item_id") or f"event-{len(order)}")
            if call_id not in calls:
                tool_name = str(item.get("tool") or item.get("name") or item.get("tool_name") or "Tool")
                calls[call_id] = {
                    "tool_name": tool_name,
                    "display_name": tool_name.replace("_", " "),
                    "input_summary": "입력 정보 없음",
                    "state": "running",
                    "result_summary": None,
                    "error_summary": None,
                    "latency_ms": None,
                }
                order.append(call_id)
            current = calls[call_id]
            if isinstance(item.get("arguments"), dict):
                current["input_summary"] = _summarize_tool_arguments(item["arguments"])
            status = str(item.get("status", ""))
            if status in {"failed", "error"} or item_type.endswith("failed") or item.get("error"):
                current["state"] = "failed"
                current["error_summary"] = _summarize_tool_error(item.get("error"))
            elif status in {"completed", "success"} or item_type.endswith("result"):
                current["state"] = "completed"
                current["result_summary"] = _summarize_tool_result(item.get("result"), tool_name=current["tool_name"])
            if isinstance(item.get("duration_ms"), int):
                current["latency_ms"] = item["duration_ms"]
        return [AiToolInvocation(provider_call_id=call_id, **calls[call_id]) for call_id in order]


_SAFE_ARGUMENT_KEYS = frozenset(
    {"title", "task_id", "request_id", "report_id", "draft_id", "expected_version", "assignee_id", "report_date", "state", "action", "limit", "query"}
)
_RESULT_KEYS = ("title", "state", "status", "draft_version", "version", "task_id", "request_id", "report_id", "action_id", "searched_materials")


def _truncate(text: str, limit: int = 48) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _summarize_tool_arguments(arguments: dict[str, Any]) -> str:
    """Keep identifiers and short titles; mask free text such as bodies, reasons, and notes."""
    if not arguments:
        return "입력 없음"
    parts: list[str] = []
    for key, value in arguments.items():
        if key in _SAFE_ARGUMENT_KEYS and isinstance(value, (str, int, float, bool)):
            parts.append(f"{key}={_truncate(str(value))}")
        elif isinstance(value, list):
            parts.append(f"{key}=[{len(value)}건]")
        else:
            parts.append(f"{key}=비공개")
    return "입력: " + ", ".join(parts)


def _summarize_tool_error(error: Any) -> str:
    if isinstance(error, dict):
        message = error.get("message") or error.get("detail") or error.get("code")
        if message:
            return f"실패: {_truncate(str(message), 80)}"
    if isinstance(error, str) and error.strip():
        return f"실패: {_truncate(error, 80)}"
    return "도구 실행 실패"


_CONTENT_BEARING_TOOLS = frozenset({"task_material_search"})


def _summarize_tool_result(result: Any, *, tool_name: str = "") -> str:
    """Summarize an MCP tool result without echoing raw payloads.

    Tools whose results carry document text never fall back to echoing a string payload, whatever the transport's
    serialization shape; their evidence is projected separately as bounded excerpts.
    """
    payload: Any = None
    if isinstance(result, dict):
        payload = result.get("structured_content") or result.get("structuredContent")
        if payload is None:
            for content in result.get("content") or []:
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    try:
                        payload = json.loads(content["text"])
                    except json.JSONDecodeError:
                        payload = content["text"]
                    break
        if result.get("isError") or result.get("is_error"):
            return _summarize_tool_error(payload if isinstance(payload, str) else result.get("error"))
    elif result is not None:
        payload = result
    if isinstance(payload, list):
        return f"결과: {len(payload)}건 조회"
    if isinstance(payload, dict) and isinstance(payload.get("results"), list) and "searched_materials" in payload:
        # material.search: report counts and file names only; excerpts stay out of the timeline.
        names = sorted({str(item.get("name", "")) for item in payload["results"] if isinstance(item, dict)} - {""})
        unavailable = payload.get("unavailable_materials") if isinstance(payload.get("unavailable_materials"), list) else []
        summary = f"결과: 자료 {int(payload['searched_materials'])}개 검색, 관련 구간 {len(payload['results'])}건"
        if names:
            summary += f" ({_truncate(', '.join(names), 60)})"
        if unavailable:
            summary += f", 읽지 못한 자료 {len(unavailable)}개"
        return summary
    if tool_name in _CONTENT_BEARING_TOOLS:
        return "결과 수신 (자료 내용은 근거 카드에만 표시)"
    if isinstance(payload, dict):
        facts = [f"{key}={_truncate(str(payload[key]), 32)}" for key in _RESULT_KEYS if key in payload and payload[key] not in (None, "")]
        if facts:
            return "결과: " + ", ".join(facts)
        return f"결과: 항목 {len(payload)}개 수신"
    if isinstance(payload, str) and payload.strip():
        return f"결과: {_truncate(payload, 80)}"
    return "결과 없음"


def prepare_isolated_codex_home(runtime_home: Path, *, auth_file: Path) -> Path:
    """Prepare a minimal runtime home that contains only the auth symlink."""
    runtime_home = runtime_home.resolve()
    if not auth_file.is_file():
        raise ProviderUnavailable("Codex CLI authentication is not available")
    runtime_home.mkdir(mode=0o700, parents=True, exist_ok=True)
    for forbidden in ("AGENTS.md", "config.toml"):
        if (runtime_home / forbidden).exists():
            raise ProviderUnavailable("isolated Codex runtime contains forbidden host configuration")
    skills_dir = runtime_home / "skills"
    allowed_system_skills = skills_dir / ".system"
    unexpected_skills = (
        [entry for entry in skills_dir.iterdir() if entry != allowed_system_skills]
        if skills_dir.exists()
        else []
    )
    if unexpected_skills:
        raise ProviderUnavailable("isolated Codex runtime contains forbidden skills")

    runtime_auth = runtime_home / "auth.json"
    if runtime_auth.exists() or runtime_auth.is_symlink():
        if runtime_auth.resolve() != auth_file.resolve():
            raise ProviderUnavailable("isolated Codex runtime authentication is invalid")
    else:
        runtime_auth.symlink_to(auth_file)
    return runtime_home


def _subprocess_runner(
    command: str,
    arguments: list[str],
    cwd: Path,
    environment: dict[str, str],
    timeout_seconds: int,
) -> ProcessResult:
    completed = subprocess.run(
        [command, *arguments],
        capture_output=True,
        check=False,
        cwd=cwd,
        env=environment,
        text=True,
        timeout=timeout_seconds,
    )
    return ProcessResult(completed.stdout, completed.stderr, completed.returncode)
