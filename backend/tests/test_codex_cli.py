import json
from pathlib import Path

from ax_workspace.modules.ax_execution.ai import AiGenerationRequest
from ax_workspace.platform.codex_cli import (
    CodexCliProfile,
    CodexCliProviderAdapter,
    ProcessResult,
)


def test_codex_cli_adapter_uses_an_isolated_ephemeral_structured_turn(tmp_path) -> None:
    auth_file = tmp_path / "host-auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}

    def runner(command: str, arguments: list[str], cwd: Path, environment: dict[str, str], timeout: int) -> ProcessResult:
        captured.update(
            command=command,
            arguments=arguments,
            cwd=cwd,
            environment=environment,
            timeout=timeout,
        )
        assert "--skip-git-repo-check" in arguments
        assert arguments[arguments.index("--sandbox") + 1] == "read-only"
        assert "--ignore-user-config" in arguments
        assert "--ignore-rules" in arguments
        assert "--ephemeral" in arguments
        assert "features.skip_host_skill_discovery=true" in arguments
        assert "features.plugins=false" in arguments
        assert 'service_tier="fast"' in arguments
        assert arguments[arguments.index("-m") + 1] == "gpt-5.6-terra"
        assert 'model_reasoning_effort="low"' in arguments
        output_path = Path(arguments[arguments.index("--output-last-message") + 1])
        output_path.write_text(json.dumps({"body": "실제 CLI 형식의 보고 초안"}), encoding="utf-8")
        return ProcessResult(
            stdout="\n".join(
                [
                    json.dumps({"type": "thread.started", "thread_id": "thread_123"}),
                    json.dumps({"type": "turn.completed", "turn_id": "turn_456", "model": "gpt-5.6-terra"}),
                ]
            ),
            stderr="",
            returncode=0,
        )

    provider = CodexCliProviderAdapter(
        CodexCliProfile(runtime_home=tmp_path / "isolated-runtime", auth_file=auth_file),
        runner=runner,
    )
    result = provider.generate(
        AiGenerationRequest(
            prompt="authorized evidence",
            output_schema={"type": "object", "properties": {"body": {"type": "string"}}, "required": ["body"]},
        )
    )

    assert result.body == "실제 CLI 형식의 보고 초안"
    assert result.cli_thread_ref == "thread_123"
    assert result.cli_run_ref == "turn_456"
    assert result.observed_model == "gpt-5.6-terra"
    assert result.observed_tier is None
    assert captured["environment"]["CODEX_HOME"] == str(tmp_path / "isolated-runtime")
    assert not captured["cwd"].exists()
