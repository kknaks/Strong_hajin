"""Claude Code CLI adapter for all SCAX LLM calls — same `AiProvider` contract as `codex_cli.py`.

Isolation differs from Codex on purpose: Claude Code's credentials live in the OS keychain, not a portable
auth file, so a copied/isolated `CLAUDE_CONFIG_DIR` cannot authenticate (verified empirically — it reports
"Not logged in"). Isolation here instead comes from flags plus a throwaway `cwd`: `--strict-mcp-config`
(only the SCAX server), `--setting-sources ""` (no user/project/local settings or CLAUDE.md discovery),
`--tools ""` (every built-in tool off; MCP tools are unaffected — verified empirically), and
`--disable-slash-commands`. The default (shared) config dir is used, so turns share session history with
any interactive `claude` use on this machine; that is an accepted trade-off for a CLI-backed local
provider, not a security boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo

from ax_workspace.platform.cli_process import (
    EventIngestFailed,
    ScaxMcpServer,
    invalid_response_message as _invalid_response_message,
    invoke_runner as _invoke_runner,
    structured_body as _structured_body,
    subprocess_runner as _subprocess_runner,
)
from ax_workspace.platform.tool_receipts import (
    summarize_tool_arguments as _summarize_tool_arguments,
    summarize_tool_error as _summarize_tool_error,
    summarize_tool_result as _summarize_tool_result,
)
from ax_workspace.modules.ax_execution.tool_catalog import tool_display_title

from ax_workspace.modules.ax_execution.ai import (
    AiConversationRequest,
    AiConversationResult,
    AiFollowUpCandidate,
    AiEventSink,
    AiGeneration,
    AiProviderEvent,
    AiToolInvocation,
    AiGenerationRequest,
    AiProviderProvenance,
    CancelToken,
    ProviderCancelled,
    ProviderRequestFailed,
    ProviderResponseInvalid,
    ProviderUnavailable,
)
from ax_workspace.modules.ax_execution.answer_documents import AnswerDocument
from ax_workspace.platform.codex_cli import _CONVERSATION_OUTPUT_SCHEMA, CodexCliProviderAdapter

#: The MCP tool namespace Claude Code prefixes every tool from a configured server with.
_MCP_TOOL_PREFIX = "mcp__scax__"
#: The synthetic tool call `--json-schema` makes the model emit its final answer through, instead of a
#: closing text block. Never shown on the tool rail — it carries the answer, not a real MCP call.
_STRUCTURED_OUTPUT_TOOL = "StructuredOutput"


@dataclass(frozen=True, slots=True)
class ClaudeCliProfile:
    model: str = "claude-sonnet-5"
    effort: str = "low"
    #: Hard per-turn spend cap (`--max-budget-usd`). A malformed/looping turn cannot run away silently.
    max_budget_usd: float = 1.0
    permission_mode: str = "bypassPermissions"
    timeout_seconds: int = 180


class ClaudeCliProviderAdapter:
    """Structured one-shot generation; no caller can invoke a CLI process directly."""

    def __init__(
        self,
        profile: ClaudeCliProfile | None = None,
        runner=None,
        command: str = "claude",
        scax_mcp_server: ScaxMcpServer | None = None,
    ) -> None:
        self._profile = profile or ClaudeCliProfile()
        self._runner = runner or _subprocess_runner
        self._command_name = command
        self._scax_mcp_server = scax_mcp_server

    def generate(self, request: AiGenerationRequest) -> AiGeneration:
        binary = shutil.which(self._command_name)
        if binary is None and self._runner is _subprocess_runner:
            raise ProviderUnavailable("Claude CLI binary is not available")
        command = binary or self._command_name
        with TemporaryDirectory(prefix="scax-claude-") as temporary:
            work_dir = Path(temporary)
            started = perf_counter()
            arguments = self._isolation_arguments() + [
                "--output-format",
                "json",
                "--json-schema",
                json.dumps(request.output_schema),
                request.prompt,
            ]
            try:
                result = self._runner(command, arguments, work_dir, dict(os.environ), self._profile.timeout_seconds)
            except subprocess.TimeoutExpired as error:
                raise ProviderRequestFailed("Claude CLI generation timed out") from error
            except OSError as error:
                raise ProviderUnavailable("Claude CLI could not start") from error
            latency_ms = int((perf_counter() - started) * 1000)
            if result.returncode != 0:
                raise ProviderRequestFailed("Claude CLI generation failed")
            try:
                envelope = json.loads(result.stdout)
            except json.JSONDecodeError as error:
                raise ProviderRequestFailed("Claude CLI returned invalid output") from error
            if envelope.get("is_error"):
                _raise_for_terminal_error(envelope)
            try:
                payload = envelope.get("structured_output")
                if payload is None:
                    payload = json.loads(envelope.get("result") or "")
                body = _structured_body(payload, request.output_schema)
            except (json.JSONDecodeError, KeyError, AttributeError, TypeError) as error:
                raise ProviderRequestFailed("Claude CLI returned invalid structured output") from error
            if not body:
                raise ProviderRequestFailed("Claude CLI returned an empty generation")
            observed_model, observed_tier, usage = _observed_provenance(envelope)
            return AiGeneration(
                provider_run_ref=envelope.get("uuid"),
                provider_session_ref=envelope.get("session_id"),
                body=body,
                requested_model=self._profile.model,
                observed_model=observed_model,
                requested_tier=self._profile.permission_mode,
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
        """Run one persisted Claude Code CLI conversation turn under the same isolation policy as Codex.

        `claude --print --output-format=stream-json` JSONL is consumed while the process runs: each observed
        event is normalized and handed to the sink immediately. When the cancel token is set the subprocess
        is stopped and ProviderCancelled is raised.
        """
        binary = shutil.which(self._command_name)
        if binary is None and self._runner is _subprocess_runner:
            raise ProviderUnavailable("Claude CLI binary is not available")
        command = binary or self._command_name
        server = self._scax_mcp_server
        if server is None:
            raise ProviderRequestFailed("SCAX MCP binding is not configured for conversation tools")
        with TemporaryDirectory(prefix="scax-claude-chat-") as temporary:
            work_dir = Path(temporary)
            mcp_config_path = work_dir / "mcp-config.json"
            mcp_config_path.write_text(json.dumps(self._mcp_config(request, server)), encoding="utf-8")
            schema = request.output_schema if request.output_schema is not None else _CONVERSATION_OUTPUT_SCHEMA
            prompt = self._conversation_prompt(request)
            ingest = ClaudeEventIngest(sink)
            started = perf_counter()
            try:
                result = _invoke_runner(
                    self._runner,
                    command,
                    self._conversation_arguments(request, server, mcp_config_path, schema, prompt),
                    work_dir,
                    dict(os.environ),
                    self._profile.timeout_seconds,
                    ingest.consume_line,
                    (lambda: cancel.is_set()) if cancel is not None else None,
                )
            except subprocess.TimeoutExpired as error:
                raise ProviderRequestFailed("Claude CLI conversation timed out", ingest.provenance(request, started, self._profile)) from error
            except EventIngestFailed as error:
                raise ProviderRequestFailed("Claude CLI events could not be persisted", ingest.provenance(request, started, self._profile)) from error
            except OSError as error:
                raise ProviderUnavailable("Claude CLI could not start") from error
            if not ingest.consumed_any and result.stdout:
                for line in result.stdout.splitlines():
                    ingest.consume_line(line)
            provenance = ingest.provenance(request, started, self._profile)
            if cancel is not None and cancel.is_set():
                raise ProviderCancelled("Claude CLI conversation was cancelled", provenance)
            if result.returncode != 0:
                raise ProviderRequestFailed("Claude CLI conversation failed", provenance)
            if ingest.terminal_error:
                _raise_for_terminal_error({"result": ingest.terminal_error}, provenance)
            try:
                payload = ingest.structured_payload
                if payload is None:
                    payload = json.loads(ingest.final_text or "")
            except (json.JSONDecodeError, ValueError, TypeError) as error:
                raise ProviderResponseInvalid(_invalid_response_message(request.output_schema), provenance) from error
            if request.output_schema is not None:
                # 부르는 쪽이 자기 스키마를 걸었으면 그 모양 그대로 돌려준다 — 걸지도 않은 대화 계약
                # (`body`·`elements`·`follow_up_candidates`)을 여기서 찾으면 그 호출은 모델이 무엇을 내든
                # 전부 무효 응답이 된다. 검증은 같은 스키마를 가진 부르는 쪽이 한 번 더 한다.
                #
                # **Codex 어댑터와 같은 판정이다** (`codex_cli.converse`). 어댑터가 둘로 갈린 뒤에도 회의
                # 배치·합성은 어느 쪽으로 돌든 자기 스키마로 답을 받아야 한다 — 한쪽만 고치면 provider 를
                # 바꾸는 순간 회의 AI 요약이 통째로 실패한다. 실제로 그 일이 있었다 (`9fbf4f5`).
                return AiConversationResult(
                    ingest.run_ref,
                    ingest.session_ref or request.provider_session_ref,
                    json.dumps(payload, ensure_ascii=False),
                    ingest.tool_invocations(),
                    usage=ingest.usage,
                )
            try:
                document = AnswerDocument.model_validate({"body": payload["body"], "elements": payload["elements"]})
                candidates = [
                    AiFollowUpCandidate(label=str(item["label"]), user_text=str(item["user_text"]))
                    for item in payload["follow_up_candidates"]
                ]
            except (ValueError, KeyError, TypeError) as error:
                raise ProviderResponseInvalid(_invalid_response_message(request.output_schema), provenance) from error
            return AiConversationResult(
                ingest.run_ref,
                ingest.session_ref or request.provider_session_ref,
                document.body,
                ingest.tool_invocations(),
                usage=ingest.usage,
                follow_up_candidates=candidates,
                answer_elements=[element.model_dump() for element in document.elements],
            )

    def _isolation_arguments(self) -> list[str]:
        return [
            "--print",
            "--model",
            self._profile.model,
            "--effort",
            self._profile.effort,
            "--max-budget-usd",
            str(self._profile.max_budget_usd),
            "--setting-sources",
            "",
            "--tools",
            "",
            "--disable-slash-commands",
            "--strict-mcp-config",
            "--permission-mode",
            self._profile.permission_mode,
        ]

    def _conversation_arguments(
        self,
        request: AiConversationRequest,
        server: ScaxMcpServer,
        mcp_config_path: Path,
        schema: dict[str, Any],
        prompt: str,
    ) -> list[str]:
        arguments = [
            *self._isolation_arguments(),
            "--output-format",
            "stream-json",
            "--verbose",
            "--mcp-config",
            str(mcp_config_path),
            "--json-schema",
            json.dumps(schema),
        ]
        if server.enabled_tools:
            # 「부르지 마라」고 프롬프트로 말하는 대신 **도구를 주지 않는다**. resume 에도 실려 매 turn 이 자기 목록을 가져간다.
            allowed = " ".join(f"{_MCP_TOOL_PREFIX}{name}" for name in server.enabled_tools)
            arguments += ["--allowedTools", allowed]
        if request.provider_session_ref:
            arguments += ["--resume", request.provider_session_ref]
        return [*arguments, prompt]

    @staticmethod
    def _mcp_config(request: AiConversationRequest, server: ScaxMcpServer) -> dict[str, Any]:
        return {
            "mcpServers": {
                "scax": {
                    "type": "stdio",
                    "command": server.command,
                    "args": list(server.arguments),
                    "env": {
                        **server.environment,
                        "AX_MCP_PERSONA": request.delegated_tool_context.principal_id,
                        "AX_MCP_CAUSATION_ID": request.delegated_tool_context.causation_id,
                    },
                }
            }
        }

    #: Same policy text Codex uses — the model's tool routing and answer format contract does not depend on
    #: which CLI is driving it.
    RELATIONSHIP_POLICY = CodexCliProviderAdapter.RELATIONSHIP_POLICY
    MEETING_CREATION_POLICY = CodexCliProviderAdapter.MEETING_CREATION_POLICY
    TASK_PROGRESS_POLICY = CodexCliProviderAdapter.TASK_PROGRESS_POLICY
    WORK_AND_REPORT_ROUTING_POLICY = CodexCliProviderAdapter.WORK_AND_REPORT_ROUTING_POLICY
    ANSWER_PRESENTATION_POLICY = CodexCliProviderAdapter.ANSWER_PRESENTATION_POLICY
    FOLLOW_UP_POLICY = CodexCliProviderAdapter.FOLLOW_UP_POLICY

    @classmethod
    def _conversation_prompt(cls, request: AiConversationRequest) -> str:
        sections: list[str] = [
            cls.RELATIONSHIP_POLICY,
            cls.MEETING_CREATION_POLICY,
            cls.TASK_PROGRESS_POLICY,
            cls.WORK_AND_REPORT_ROUTING_POLICY,
            cls.ANSWER_PRESENTATION_POLICY,
            cls.FOLLOW_UP_POLICY,
        ]
        if request.asked_at is not None:
            local = request.asked_at.astimezone(ZoneInfo(request.timezone_name))
            sections.append(
                f"이 질문이 접수된 시각: {local.strftime('%Y-%m-%d %H:%M')} ({request.timezone_name}).\n"
                "`오늘`·`어제`·`지난달`은 이 시각을 기준으로 해석한다. 다른 곳에서 지금 시각을 짐작하지 않는다."
            )
        if request.recent_exchanges:
            told = "\n".join(
                f"- [turn:{item.get('turn_id', '')}] {'사용자' if item.get('role') == 'user' else 'AX'}: {item.get('body', '')}"
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


def _extract_tool_result_payload(content: Any) -> Any:
    """Normalize a `tool_result` block's `content` into the JSON value it represents, if any.

    Anthropic's tool_result content is ordinarily a list of `{"type": "text", "text": ...}` blocks; Claude
    Code's own error path instead uses a plain string. Either way, the text usually carries JSON — parse it
    when it does, and fall back to the raw text (or `None`) otherwise.
    """
    if isinstance(content, list):
        texts = [item.get("text") for item in content if isinstance(item, dict) and isinstance(item.get("text"), str)]
        content = "\n".join(texts) if texts else None
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            return content
    # Observed live: our MCP server's bare list/scalar returns arrive relayed as `{"result": <value>}` — the
    # SDK's own structured-content wrapping for a tool whose return isn't already an object. Codex exposes
    # the tool's `structured_content` directly and never needs this; unwrap only Claude's own convention.
    if isinstance(content, dict) and content.keys() == {"result"}:
        return content["result"]
    return content


def _raise_for_terminal_error(envelope: dict[str, Any], provenance: AiProviderProvenance | None = None) -> None:
    message = str(envelope.get("result") or "Claude CLI conversation failed")
    if "not logged in" in message.lower() or "/login" in message.lower():
        raise ProviderUnavailable("Claude CLI authentication is not available")
    raise ProviderRequestFailed(f"Claude CLI conversation failed: {message}", provenance)


def _observed_provenance(envelope: dict[str, Any]) -> tuple[str | None, str | None, dict[str, Any] | None]:
    usage = _normalize_usage(envelope.get("usage"))
    observed_tier = None
    raw_usage = envelope.get("usage") or {}
    if isinstance(raw_usage, dict):
        observed_tier = raw_usage.get("service_tier")
    model_usage = envelope.get("modelUsage") or {}
    observed_model = None
    best_tokens = -1
    for model_name, stats in model_usage.items() if isinstance(model_usage, dict) else []:
        total = int(stats.get("inputTokens", 0)) + int(stats.get("outputTokens", 0))
        if total > best_tokens:
            best_tokens, observed_model = total, stats.get("canonicalModel", model_name)
    return observed_model, observed_tier, usage


def _normalize_usage(usage: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep Claude's own usage shape but mirror Codex's key names so shared UI reads either provider alike."""
    if not isinstance(usage, dict):
        return usage
    normalized = dict(usage)
    if "cache_read_input_tokens" in usage:
        normalized.setdefault("cached_input_tokens", usage["cache_read_input_tokens"])
    if "cache_creation_input_tokens" in usage:
        normalized.setdefault("cache_write_input_tokens", usage["cache_creation_input_tokens"])
    details = usage.get("output_tokens_details")
    if isinstance(details, dict) and "thinking_tokens" in details:
        normalized.setdefault("reasoning_output_tokens", details["thinking_tokens"])
    return normalized


class ClaudeEventIngest:
    """Normalizes `claude --print --output-format=stream-json` lines into AiProviderEvents as they arrive.

    Only facts present in the stream are emitted: assistant text as progress commentary, MCP tool calls
    (`mcp__scax__*`) as tool lifecycle, and the model's schema-validated final answer from the synthetic
    `StructuredOutput` call `--json-schema` makes it emit instead of a closing text block.
    """

    def __init__(self, sink: AiEventSink | None) -> None:
        self._sink = sink
        self._calls: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self.run_ref: str | None = None
        self.session_ref: str | None = None
        self.usage: dict[str, Any] | None = None
        self.consumed_any = False
        self.error_messages: list[str] = []
        self.structured_payload: dict[str, Any] | None = None
        self.final_text: str | None = None
        self.terminal_error: str | None = None
        self._turn_started_emitted = False

    def provenance(self, request: AiConversationRequest, started: float, profile: ClaudeCliProfile) -> AiProviderProvenance:
        return AiProviderProvenance(
            provider_run_ref=self.run_ref,
            provider_session_ref=self.session_ref or request.provider_session_ref,
            requested_model=profile.model,
            observed_model=profile.model,
            requested_tier=profile.permission_mode,
            observed_tier=None,
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
        if isinstance(event.get("session_id"), str):
            self.session_ref = event["session_id"]
        if not self._turn_started_emitted:
            self._turn_started_emitted = True
            self._emit(AiProviderEvent("turn_started", now, provider_session_ref=self.session_ref))
        event_type = str(event.get("type", ""))
        if event_type == "assistant":
            self._consume_assistant(event, now)
            return
        if event_type == "user":
            self._consume_user(event, now)
            return
        if event_type == "result":
            self._consume_result(event, now)
            return

    def _consume_assistant(self, event: dict[str, Any], now: datetime) -> None:
        for block in (event.get("message") or {}).get("content", []):
            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    self._emit(AiProviderEvent("item_completed", now, item_type="agent_message", text=text))
            elif block_type == "tool_use":
                self._start_tool_call(block, now)

    def _start_tool_call(self, block: dict[str, Any], now: datetime) -> None:
        name = str(block.get("name") or "")
        call_id = str(block.get("id") or f"call-{len(self._order)}")
        if name == _STRUCTURED_OUTPUT_TOOL:
            # The answer itself, not a tool a person needs to see on the rail.
            if isinstance(block.get("input"), dict):
                self.structured_payload = block["input"]
            return
        tool_name = name[len(_MCP_TOOL_PREFIX):] if name.startswith(_MCP_TOOL_PREFIX) else name
        current = {
            "tool_name": tool_name,
            "display_name": tool_display_title(tool_name),
            "input_summary": _summarize_tool_arguments(block.get("input") or {}),
            "state": "running",
            "result_summary": None,
            "error_summary": None,
            "latency_ms": None,
            "started_at": now,
            "completed_at": None,
        }
        self._calls[call_id] = current
        self._order.append(call_id)
        self._emit(AiProviderEvent("item_started", now, item_id=call_id, item_type="mcp_tool_call", tool=self._invocation(call_id)))

    def _consume_user(self, event: dict[str, Any], now: datetime) -> None:
        for block in (event.get("message") or {}).get("content", []):
            if block.get("type") != "tool_result":
                continue
            call_id = str(block.get("tool_use_id") or "")
            current = self._calls.get(call_id)
            if current is None:
                continue  # the StructuredOutput acknowledgment, or a call this ingest never saw start
            is_error = bool(block.get("is_error"))
            payload = _extract_tool_result_payload(block.get("content"))
            if is_error:
                current["state"] = "failed"
                current["error_summary"] = _summarize_tool_error(payload)
            else:
                current["state"] = "completed"
                # The stream sometimes omits a completed call's content entirely (observed live: `None`, and
                # separately `""`) rather than echoing an empty collection (`[]`/`{}`, a real answer). Only
                # the former means "nothing to summarize" — the model still received the actual result.
                current["result_summary"] = (
                    "결과 수신" if payload in (None, "") else _summarize_tool_result(payload, tool_name=current["tool_name"])
                )
            current["completed_at"] = now
            if current["started_at"] is not None:
                current["latency_ms"] = int((now - current["started_at"]).total_seconds() * 1000)
            self._emit(AiProviderEvent("item_completed", now, item_id=call_id, item_type="mcp_tool_call", tool=self._invocation(call_id)))

    def _consume_result(self, event: dict[str, Any], now: datetime) -> None:
        self.run_ref = event.get("uuid") or self.run_ref
        self.usage = _normalize_usage(event.get("usage")) or self.usage
        if isinstance(event.get("structured_output"), dict):
            self.structured_payload = self.structured_payload or event["structured_output"]
        result_text = event.get("result")
        if isinstance(result_text, str):
            self.final_text = result_text
        if event.get("is_error"):
            self.terminal_error = result_text if isinstance(result_text, str) else "Claude CLI conversation failed"
            message = _summarize_tool_error(result_text)
            self.error_messages.append(message)
            self._emit(
                AiProviderEvent(
                    "turn_failed", now, provider_run_ref=self.run_ref, provider_session_ref=self.session_ref,
                    usage=self.usage, error_message=message,
                )
            )
            return
        self._emit(
            AiProviderEvent(
                "turn_completed", now, provider_run_ref=self.run_ref, provider_session_ref=self.session_ref,
                usage=self.usage,
            )
        )

    def _invocation(self, call_id: str) -> AiToolInvocation:
        return AiToolInvocation(provider_call_id=call_id, **self._calls[call_id])

    def tool_invocations(self) -> list[AiToolInvocation]:
        return [self._invocation(call_id) for call_id in self._order]

    def _emit(self, event: AiProviderEvent) -> None:
        if self._sink is not None:
            self._sink.accept(event)
