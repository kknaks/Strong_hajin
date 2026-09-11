import json
from pathlib import Path
import sys

from ax_workspace.bootstrap.application import create_scax_mcp_server
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.modules.ax_execution.ai import (
    AiConversationRequest,
    AiDelegatedToolContext,
    AiGenerationRequest,
)
from ax_workspace.platform.codex_cli import (
    CodexCliMcpServer,
    CodexCliProfile,
    CodexCliProviderAdapter,
    ProcessResult,
)


def test_scax_mcp_server_uses_the_python_module_in_source_runtime(monkeypatch) -> None:
    monkeypatch.delenv("SCAX_RUNTIME_EXECUTABLE", raising=False)

    server = create_scax_mcp_server(Settings(RuntimeProfile.TEST, "sqlite://"))

    assert server.command == sys.executable
    assert server.arguments == ("-m", "ax_workspace.entrypoints.mcp")


def test_scax_mcp_server_reenters_the_compiled_runtime_binary(monkeypatch) -> None:
    monkeypatch.setenv("SCAX_RUNTIME_EXECUTABLE", "/opt/scax/scax")

    server = create_scax_mcp_server(Settings(RuntimeProfile.TEST, "sqlite://"))

    assert server.command == "/opt/scax/scax"
    assert server.arguments == ("mcp",)


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
        assert 'shell_environment_policy.inherit="none"' in arguments
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
    assert result.provider_session_ref == "thread_123"
    assert result.provider_run_ref == "turn_456"
    assert result.observed_model == "gpt-5.6-terra"
    assert result.observed_tier is None
    assert captured["environment"]["CODEX_HOME"] == str(tmp_path / "isolated-runtime")
    assert not captured["cwd"].exists()


def test_codex_cli_conversation_injects_only_server_bound_scax_mcp_context(tmp_path) -> None:
    auth_file = tmp_path / "host-auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}

    def runner(command: str, arguments: list[str], cwd: Path, environment: dict[str, str], timeout: int) -> ProcessResult:
        captured["arguments"] = arguments
        captured["environment"] = environment
        output_path = Path(arguments[arguments.index("--output-last-message") + 1])
        output_path.write_text("업무를 조회했습니다.", encoding="utf-8")
        return ProcessResult(
            stdout="\n".join(
                [
                    json.dumps({"type": "thread.started", "thread_id": "thread_123"}),
                    json.dumps(
                        {
                            "type": "item.started",
                            "item": {
                                "id": "item_2",
                                "type": "mcp_tool_call",
                                "server": "scax",
                                "tool": "task_list",
                                "arguments": {},
                                "status": "in_progress",
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {
                                "id": "item_2",
                                "type": "mcp_tool_call",
                                "server": "scax",
                                "tool": "task_list",
                                "result": {"content": []},
                                "status": "completed",
                            },
                        }
                    ),
                    json.dumps({"type": "turn.completed", "turn_id": "turn_456"}),
                ]
            ),
            stderr="",
            returncode=0,
        )

    provider = CodexCliProviderAdapter(
        CodexCliProfile(runtime_home=tmp_path / "isolated-runtime", auth_file=auth_file),
        runner=runner,
        scax_mcp_server=CodexCliMcpServer(
            command="/usr/bin/python3",
            arguments=("-m", "ax_workspace.entrypoints.mcp"),
            environment={"AX_PROFILE": "test", "DATABASE_URL": "postgresql://example/scax"},
        ),
    )
    result = provider.converse(
        AiConversationRequest(
            prompt="내 업무를 보여줘",
            provider_session_ref=None,
            context_references=[],
            delegated_tool_context=AiDelegatedToolContext(
                principal_id="mina",
                causation_id="turn-execution-1",
            ),
        )
    )

    arguments = captured["arguments"]
    assert result.body == "업무를 조회했습니다."
    assert [(item.tool_name, item.state) for item in result.tool_invocations] == [("task_list", "completed")]
    assert "--ignore-user-config" in arguments
    assert 'shell_environment_policy.inherit="none"' in arguments
    assert "--sandbox" in arguments
    assert arguments[arguments.index("--sandbox") + 1] == "read-only"
    assert 'mcp_servers.scax.command="/usr/bin/python3"' in arguments
    assert 'mcp_servers.scax.args=["-m", "ax_workspace.entrypoints.mcp"]' in arguments
    assert 'mcp_servers.scax.env_vars=["AX_MCP_PERSONA", "AX_MCP_CAUSATION_ID", "AX_PROFILE", "DATABASE_URL"]' in arguments
    assert 'mcp_servers.scax.default_tools_approval_mode="approve"' in arguments
    assert "postgresql://example/scax" not in " ".join(arguments)
    assert "mina" not in " ".join(arguments)
    assert captured["environment"]["AX_MCP_PERSONA"] == "mina"
    assert captured["environment"]["AX_MCP_CAUSATION_ID"] == "turn-execution-1"
    assert captured["environment"]["DATABASE_URL"] == "postgresql://example/scax"
    assert "mina" not in provider._conversation_prompt(  # type: ignore[attr-defined]
        AiConversationRequest(
            prompt="내 업무를 보여줘",
            provider_session_ref=None,
            context_references=[],
            delegated_tool_context=AiDelegatedToolContext(
                principal_id="mina",
                causation_id="turn-execution-1",
            ),
        )
    )


def test_a_structured_schema_comes_back_as_its_own_structure_not_a_report_body(tmp_path) -> None:
    """Only the daily report asks for one text field. A caller that asked for segments must get segments.

    The Meeting refinement and summary pass their own schemas; if the adapter insisted on the report's `body` key
    every one of those calls would fail as an invalid provider response no matter what the model returned.
    """
    auth_file = tmp_path / "host-auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    produced = {"segments": [{"text": "안녕하세요.", "start_ms": 0, "end_ms": 900}]}

    def runner(command: str, arguments: list[str], cwd: Path, environment: dict[str, str], timeout: int) -> ProcessResult:
        schema = json.loads(Path(arguments[arguments.index("--output-schema") + 1]).read_text(encoding="utf-8"))
        assert schema["required"] == ["segments"]
        Path(arguments[arguments.index("--output-last-message") + 1]).write_text(
            json.dumps(produced, ensure_ascii=False), encoding="utf-8"
        )
        return ProcessResult(json.dumps({"type": "turn.completed", "turn_id": "turn_1"}), "", 0)

    provider = CodexCliProviderAdapter(
        CodexCliProfile(runtime_home=tmp_path / "runtime", auth_file=auth_file),
        runner=runner,
    )
    result = provider.generate(
        AiGenerationRequest(
            prompt="정제해 주세요",
            output_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"segments": {"type": "array", "items": {"type": "object"}}},
                "required": ["segments"],
            },
        )
    )

    assert json.loads(result.body) == produced
