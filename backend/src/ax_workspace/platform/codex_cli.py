"""Single production/development adapter for all SCAX LLM calls.

The adapter follows the isolated Codex runtime contract used by
agent-workflow-studio: temporary work directories, a dedicated CODEX_HOME,
no host config/rules/skills/plugins, and guaranteed temporary-file cleanup.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
from tempfile import TemporaryDirectory
import threading
import time
from time import perf_counter
from typing import Any, Callable

from ax_workspace.modules.ax_execution.ai import (
    AiConversationRequest,
    AiConversationResult,
    AiEventSink,
    AiGeneration,
    AiProviderEvent,
    AiToolInvocation,
    AiGenerationRequest,
    AiProviderProvenance,
    CancelToken,
    ProviderCancelled,
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
                body = _structured_body(payload, request.output_schema)
            except (OSError, json.JSONDecodeError, KeyError, AttributeError, TypeError) as error:
                raise ProviderRequestFailed("Codex CLI returned invalid structured output", provenance) from error
            if not body:
                raise ProviderRequestFailed("Codex CLI returned an empty generation", provenance)
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

    def converse(
        self,
        request: AiConversationRequest,
        *,
        sink: AiEventSink | None = None,
        cancel: CancelToken | None = None,
    ) -> AiConversationResult:
        """Run one persisted Codex CLI conversation turn under the same isolated policy.

        `codex exec --json` JSONL is consumed while the process runs: each observed event is normalized and handed
        to the sink immediately (agent messages arrive as completed items; no token deltas are fabricated). When the
        cancel token is set the subprocess is stopped and ProviderCancelled is raised.
        """
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
            ingest = CodexEventIngest(sink)
            started = perf_counter()
            try:
                result = _invoke_runner(
                    self._runner,
                    command,
                    self._conversation_arguments(request, output_path, prompt),
                    work_dir,
                    self._conversation_environment(request, runtime_home),
                    self._profile.timeout_seconds,
                    ingest.consume_line,
                    (lambda: cancel.is_set()) if cancel is not None else None,
                )
            except subprocess.TimeoutExpired as error:
                raise ProviderRequestFailed("Codex CLI conversation timed out", ingest.provenance(request, started, self._profile)) from error
            except EventIngestFailed as error:
                # Persisting an observed event failed: the execution was stopped; the turn must not look complete.
                raise ProviderRequestFailed("Codex CLI events could not be persisted", ingest.provenance(request, started, self._profile)) from error
            except OSError as error:
                raise ProviderUnavailable("Codex CLI could not start") from error
            # A legacy runner returns the whole stdout at once; feed it through the same ingest path.
            if not ingest.consumed_any and result.stdout:
                for line in result.stdout.splitlines():
                    ingest.consume_line(line)
            provenance = ingest.provenance(request, started, self._profile)
            if cancel is not None and cancel.is_set():
                raise ProviderCancelled("Codex CLI conversation was cancelled", provenance)
            if result.returncode != 0:
                raise ProviderRequestFailed("Codex CLI conversation failed", provenance)
            try:
                body = output_path.read_text(encoding="utf-8").strip()
            except OSError as error:
                raise ProviderRequestFailed("Codex CLI returned no conversation response", provenance) from error
            if not body:
                raise ProviderRequestFailed("Codex CLI returned an empty conversation response", provenance)
            return AiConversationResult(
                ingest.run_ref,
                ingest.session_ref or request.provider_session_ref,
                body,
                ingest.tool_invocations(),
                usage=ingest.usage,
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

    #: How to answer a question about people, teams and work. It is guidance to the provider, not a pipeline: the
    #: model chooses its tools, and nothing here runs on its behalf. What it must not do is invent a connection.
    RELATIONSHIP_POLICY = (
        "SCAX 조회 지침:\n"
        "- 목록 하나로 답할 수 있는 질문은 소유 도구를 바로 부르고 거기서 멈춘다. `내 업무`는 `task_list`,"
        " `나에게 온 요청`은 `work_request_list`, `내 회의`는 `list_meetings`다. 관계를 묻지 않은 질문에"
        " graph를 걷지 않는다 — 이미 답이 손에 있는데 더 걷는 것은 답을 늦출 뿐이다.\n"
        "- 사람·팀·업무·회의·자료가 어떻게 이어져 있는지 묻는 질문은 `graph_search`로 시작 node를 찾고,"
        " `graph_neighbors`로 명시된 관계만 넓힌 뒤, 필요한 것만 소유 도구(`task_get`·`meeting_get`·"
        "`work_request_get`·`task_materials_list`)로 읽는다.\n"
        "- 문서 본문이나 회의 발화를 찾아야 하는 질문은 `task_material_search`로 시작 node를 얻은 뒤 같은 순서로 넓힌다.\n"
        "- 도구가 돌려주지 않은 관계는 말하지 않는다. 관계를 그림이나 표로 지어내지 말고, 조회한 것만 근거로 답한다.\n"
        "- 여러 개를 나열할 때는 도구가 준 canonical id의 대상만 말한다."
    )

    @classmethod
    def _conversation_prompt(cls, request: AiConversationRequest) -> str:
        """The turn as the provider sees it: policy, what this conversation already stands on, then the message.

        Everything above the message is server-built from the canonical conversation and re-authorized for this
        principal, so a follow-up like `그중 기한이 가장 빠른 것` has real ids to start from whether or not the
        provider kept a checkpoint of its own.
        """
        sections: list[str] = [cls.RELATIONSHIP_POLICY]
        if request.recent_exchanges:
            told = "\n".join(
                f"- {'사용자' if item.get('role') == 'user' else 'AX'}: {item.get('body', '')}"
                for item in request.recent_exchanges
            )
            sections.append(f"이 대화에서 지금까지 오간 말(요약이 아니라 실제 발화, 최근 순):\n{told}")
        if request.seed_references:
            seeds = "\n".join(
                f"- {item.get('ref', '')}: {item.get('title', '')}" for item in request.seed_references
            )
            sections.append(
                "이 대화의 이전 turn이 실제로 조회한 것들(지금 권한으로 다시 확인함). 이어지는 질문의 시작 node 후보다:\n"
                f"{seeds}"
            )
        if request.context_references:
            references = "\n".join(
                f"- {item.get('resource_type', 'resource')}:{item.get('resource_id', '')}: {item.get('summary', '')}"
                for item in request.context_references
            )
            sections.append(f"Context references authorized for this turn:\n{references}")
        sections.append(f"User message:\n{request.prompt}")
        return "\n\n".join(sections)

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
        """Fold a complete Codex JSONL transcript into redacted tool receipts (offline/legacy path)."""
        ingest = CodexEventIngest(None)
        for line in stdout.splitlines():
            ingest.consume_line(line)
        return ingest.tool_invocations()


def _structured_body(payload: Any, output_schema: dict[str, Any]) -> str:
    """Hand back what the caller asked for.

    The daily report asks for a single text field and wants that text. Every other caller — Meeting refinement and
    summary — passes its own schema and parses the structure itself, so unwrapping a `body` key that its schema never
    mentioned would turn every one of those generations into an invalid response.
    """
    if list(output_schema.get("properties", {})) == ["body"]:
        return payload["body"].strip()
    return json.dumps(payload, ensure_ascii=False)


def _invoke_runner(runner, command, arguments, cwd, environment, timeout_seconds, on_line, should_cancel) -> ProcessResult:
    """Call a streaming runner; fall back to the legacy 5-argument runner used by older tests."""
    try:
        return runner(command, arguments, cwd, environment, timeout_seconds, on_line=on_line, should_cancel=should_cancel)
    except TypeError as error:
        if "on_line" not in str(error) and "positional" not in str(error):
            raise
        return runner(command, arguments, cwd, environment, timeout_seconds)


_TOOL_ITEM_TYPES = frozenset({"mcp_tool_call", "command_execution", "web_search", "file_change"})


class CodexEventIngest:
    """Normalizes `codex exec --json` lines into AiProviderEvents as they are observed.

    Only facts present in the stream are emitted: agent_message text on item.completed, tool lifecycle on
    item.started/item.completed, usage on turn.completed, error items. Timing is SCAX's observation clock: a tool's
    latency is recorded only when both its start and its terminal event were observed.
    """

    def __init__(self, sink: AiEventSink | None) -> None:
        self._sink = sink
        self._calls: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self.run_ref: str | None = None
        self.session_ref: str | None = None
        self.observed_model: str | None = None
        self.observed_tier: str | None = None
        self.usage: dict[str, Any] | None = None
        self.consumed_any = False
        self.error_messages: list[str] = []

    def provenance(self, request: AiConversationRequest, started: float, profile: CodexCliProfile) -> AiProviderProvenance:
        return AiProviderProvenance(
            provider_run_ref=self.run_ref,
            provider_session_ref=self.session_ref or request.provider_session_ref,
            requested_model=profile.model,
            observed_model=self.observed_model,
            requested_tier=profile.service_tier,
            observed_tier=self.observed_tier,
            latency_ms=int((perf_counter() - started) * 1000),
            usage=self.usage,
        )

    def consume_line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(event, dict):
            return
        self.consumed_any = True
        now = datetime.now(UTC)
        event_type = str(event.get("type", ""))
        if isinstance(event.get("model"), str):
            self.observed_model = event["model"]
        if isinstance(event.get("service_tier"), str):
            self.observed_tier = event["service_tier"]
        if event_type == "thread.started":
            self.session_ref = event.get("thread_id") or (event.get("thread") or {}).get("id") or self.session_ref
            return
        if event_type == "turn.started":
            self.run_ref = event.get("turn_id") or (event.get("turn") or {}).get("id") or self.run_ref
            self._emit(AiProviderEvent("turn_started", now, provider_run_ref=self.run_ref, provider_session_ref=self.session_ref))
            return
        if event_type in {"turn.completed", "turn.failed"}:
            self.run_ref = event.get("turn_id") or (event.get("turn") or {}).get("id") or self.run_ref
            if isinstance(event.get("usage"), dict):
                self.usage = event["usage"]
            error = event.get("error")
            message = _summarize_tool_error(error) if error else None
            self._emit(
                AiProviderEvent(
                    "turn_completed" if event_type == "turn.completed" else "turn_failed",
                    now,
                    provider_run_ref=self.run_ref,
                    provider_session_ref=self.session_ref,
                    usage=self.usage,
                    error_message=message,
                )
            )
            return
        if event_type == "error":
            message = _summarize_tool_error(event.get("message") or event.get("error") or event)
            self.error_messages.append(message)
            self._emit(AiProviderEvent("error", now, error_message=message))
            return
        if not event_type.startswith("item."):
            return
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        item_type = str(item.get("type", ""))
        item_id = str(item.get("id") or event.get("item_id") or f"item-{len(self._order)}")
        phase = event_type.split(".", 1)[1]  # started | updated | completed
        if item_type == "agent_message":
            text = item.get("text") if isinstance(item.get("text"), str) else None
            if phase == "completed" and text:
                self._emit(AiProviderEvent("item_completed", now, item_id=item_id, item_type=item_type, text=text))
            return
        if item_type == "error":
            message = _summarize_tool_error(item.get("message"))
            self.error_messages.append(message)
            self._emit(AiProviderEvent("error", now, item_id=item_id, item_type=item_type, error_message=message))
            return
        if item_type == "reasoning":
            return  # raw reasoning is never projected
        if item_type not in _TOOL_ITEM_TYPES and "tool" not in item_type:
            return
        current = self._calls.get(item_id)
        if current is None:
            tool_name = str(item.get("tool") or item.get("name") or item.get("tool_name") or item_type)
            current = {
                "tool_name": tool_name,
                "display_name": _display_name(item_type, tool_name, item),
                "input_summary": "입력 정보 없음",
                "state": "running",
                "result_summary": None,
                "error_summary": None,
                "latency_ms": None,
                "started_at": now if phase == "started" else None,
                "completed_at": None,
            }
            self._calls[item_id] = current
            self._order.append(item_id)
        if isinstance(item.get("arguments"), dict):
            current["input_summary"] = _summarize_tool_arguments(item["arguments"])
        elif item_type == "command_execution" and isinstance(item.get("command"), str):
            current["input_summary"] = f"명령: {_truncate(item['command'], 60)}"
        status = str(item.get("status", ""))
        if status in {"failed", "error", "declined"} or item.get("error"):
            current["state"] = "failed"
            # 실패한 MCP 호출은 이유를 결과 안에 담아 온다. 그것을 버리고 `failed`만 남기면 사람이 실행 rail에서
            # 무엇이 잘못됐는지 알 수 없다 — 모델은 같은 응답을 그대로 받는데 사람만 못 본다.
            reason = item.get("error")
            current["error_summary"] = (
                _summarize_tool_result(item["result"], tool_name=current["tool_name"])
                if reason in (None, "") and item.get("result") is not None
                else _summarize_tool_error(reason or status)
            )
        elif status in {"completed", "success"} or phase == "completed":
            current["state"] = "completed"
            current["result_summary"] = (
                _summarize_tool_result(item.get("result"), tool_name=current["tool_name"])
                if item_type == "mcp_tool_call"
                else f"종료 코드 {item.get('exit_code')}" if item_type == "command_execution" and item.get("exit_code") is not None else "완료"
            )
        if current["state"] in {"completed", "failed"} and current["completed_at"] is None:
            current["completed_at"] = now
            if current["started_at"] is not None:
                current["latency_ms"] = int((now - current["started_at"]).total_seconds() * 1000)
        self._emit(AiProviderEvent(f"item_{phase}", now, item_id=item_id, item_type=item_type, tool=self._invocation(item_id)))

    def _invocation(self, call_id: str) -> AiToolInvocation:
        return AiToolInvocation(provider_call_id=call_id, **self._calls[call_id])

    def tool_invocations(self) -> list[AiToolInvocation]:
        return [self._invocation(call_id) for call_id in self._order]

    def _emit(self, event: AiProviderEvent) -> None:
        if self._sink is not None:
            self._sink.accept(event)


def _display_name(item_type: str, tool_name: str, item: dict[str, Any]) -> str:
    if item_type == "mcp_tool_call":
        return tool_name.replace("_", " ")
    if item_type == "command_execution":
        return "명령 실행"
    if item_type == "web_search":
        return "웹 검색"
    if item_type == "file_change":
        return "파일 변경"
    return tool_name.replace("_", " ")


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
    on_line: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> ProcessResult:
    """Run the CLI in its own process group and stream stdout lines to `on_line` as they arrive.

    Cancel and timeout stop the whole group (Codex spawns MCP child processes), so nothing is orphaned. A failing
    `on_line` callback is not best-effort: the stream is stopped and EventIngestFailed is raised so the turn cannot
    complete with missing persisted events.
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


class EventIngestFailed(RuntimeError):
    """A sink/on_line callback failed while the provider was running; the execution was stopped."""


def _stop_process_group(process: subprocess.Popen) -> None:
    """Stop the Codex process and every child in its process group (SIGINT, SIGTERM, then SIGKILL) and reap it."""
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


def _signal_group(pgid: int, signum: signal.Signals) -> None:
    try:
        os.killpg(pgid, signum)
    except ProcessLookupError:
        pass
    except PermissionError:
        pass
