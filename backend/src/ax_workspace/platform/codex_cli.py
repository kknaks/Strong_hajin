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
    AiGeneration,
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

class CodexCliProviderAdapter:
    """Structured one-shot generation; no caller can invoke a CLI process directly."""

    def __init__(
        self,
        profile: CodexCliProfile | None = None,
        runner: ProcessRunner | None = None,
        command: str = "codex",
    ) -> None:
        self._profile = profile or CodexCliProfile()
        self._runner = runner or _subprocess_runner
        self._command_name = command

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


def prepare_isolated_codex_home(runtime_home: Path, *, auth_file: Path) -> Path:
    """Prepare a minimal runtime home that contains only the auth symlink."""
    if not auth_file.is_file():
        raise ProviderUnavailable("Codex CLI authentication is not available")
    runtime_home.mkdir(mode=0o700, parents=True, exist_ok=True)
    for forbidden in ("AGENTS.md", "config.toml"):
        if (runtime_home / forbidden).exists():
            raise ProviderUnavailable("isolated Codex runtime contains forbidden host configuration")
    skills_dir = runtime_home / "skills"
    if skills_dir.exists() and any(skills_dir.iterdir()):
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
