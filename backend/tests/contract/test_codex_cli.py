import json
from pathlib import Path
import sys
import pytest

from ax_workspace.bootstrap.application import create_scax_mcp_server
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.modules.ax_execution.ai import (
    AiConversationRequest,
    AiDelegatedToolContext,
    AiGenerationRequest,
    ProviderRequestFailed,
    ProviderResponseInvalid,
)
from ax_workspace.modules.ax_execution.tool_catalog import TOOL_CATALOG, tool_display_title
from ax_workspace.platform.codex_cli import (
    CodexCliMcpServer,
    CodexCliProfile,
    CodexCliProviderAdapter,
    ProcessResult,
)


def test_conversation_returns_elements_without_exposing_unvalidated_json(tmp_path):
    payload = {"body": "먼저 {{a}}를 확인하세요.", "elements": [
        {"key": "a", "type": "resource_reference", "ref": "task:t1"},
    ], "follow_up_candidates": []}
    events = []

    class Sink:
        def accept(self, event):
            events.append(event)

    def runner(command, arguments, cwd, environment, timeout, on_line=None, should_cancel=None):
        on_line(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(payload)}}))
        Path(arguments[arguments.index("--output-last-message") + 1]).write_text(json.dumps(payload))
        return ProcessResult("", "", 0)

    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    provider = CodexCliProviderAdapter(CodexCliProfile(runtime_home=tmp_path / "runtime", auth_file=auth), runner=runner,
        scax_mcp_server=CodexCliMcpServer(command="python", arguments=(), environment={}))
    result = provider.converse(AiConversationRequest("내 업무", None, [], AiDelegatedToolContext("mina", "exec-1")), sink=Sink())
    assert result.answer_elements == payload["elements"]
    assert not any(event.text for event in events), "Final content waits for server reference validation"


@pytest.mark.parametrize("payload", [
    {"body": "{{unknown}}", "elements": [], "follow_up_candidates": []},
    {"body": {"unexpected": "object"}, "elements": [], "follow_up_candidates": []},
    {"body": "답변", "elements": [{"type": "execute", "command": "approve"}], "follow_up_candidates": []},
])
def test_invalid_conversation_output_is_a_provider_failure(tmp_path, payload):
    def runner(command, arguments, cwd, environment, timeout):
        Path(arguments[arguments.index("--output-last-message") + 1]).write_text(json.dumps(payload))
        return ProcessResult("", "", 0)
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    provider = CodexCliProviderAdapter(CodexCliProfile(runtime_home=tmp_path / "runtime", auth_file=auth), runner=runner,
        scax_mcp_server=CodexCliMcpServer(command="python", arguments=(), environment={}))
    with pytest.raises(ProviderRequestFailed):
        provider.converse(AiConversationRequest("내 업무", None, [], AiDelegatedToolContext("mina", "exec-1")))


def test_scax_mcp_tool_receipts_have_korean_display_names() -> None:
    previously_unmapped = {
        "action_item_command",
        "action_item_get",
        "action_item_list",
        "conversation_search",
        "task_block",
        "task_cancel",
        "task_checklist_add",
        "task_checklist_archive",
        "task_checklist_reorder",
        "task_checklist_update",
        "task_progress_batch",
        "task_complete",
        "task_create_self",
        "task_resume",
        "task_start",
        "task_update",
        "work_request_amend",
        "work_request_assignee_candidates",
        "work_request_create",
        "work_request_history",
    }

    assert previously_unmapped <= TOOL_CATALOG.keys()
    assert all(any("가" <= char <= "힣" for char in tool_display_title(name)) for name in previously_unmapped)


def test_scax_mcp_server_uses_the_python_module_in_source_runtime(monkeypatch) -> None:
    monkeypatch.delenv("SCAX_RUNTIME_EXECUTABLE", raising=False)

    server = create_scax_mcp_server(Settings(RuntimeProfile.TEST, "sqlite://", web_origin="https://scax.example.test"))

    assert server.environment["AX_WEB_ORIGIN"] == "https://scax.example.test"
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
    observed = []

    class Sink:
        def accept(self, event) -> None:
            observed.append(event)

    def runner(command: str, arguments: list[str], cwd: Path, environment: dict[str, str], timeout: int) -> ProcessResult:
        captured["arguments"] = arguments
        captured["environment"] = environment
        schema = json.loads(Path(arguments[arguments.index("--output-schema") + 1]).read_text(encoding="utf-8"))
        assert schema["required"] == ["body", "follow_up_candidates", "elements"]
        assert schema["properties"]["follow_up_candidates"]["maxItems"] == 3
        output_path = Path(arguments[arguments.index("--output-last-message") + 1])
        output_path.write_text(
            json.dumps(
                {
                    "body": "업무를 조회했습니다.", "elements": [],
                    "follow_up_candidates": [
                        {"label": "기한순으로 보기", "user_text": "그 업무를 기한순으로 정리해줘"},
                        {"label": "우선순위 제안", "user_text": "먼저 할 업무를 제안해줘"},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
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
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {
                                "id": "answer_1",
                                "type": "agent_message",
                                "text": json.dumps(
                                    {
                                        "body": "업무를 조회했습니다.", "elements": [],
                                        "follow_up_candidates": [
                                            {"label": "기한순으로 보기", "user_text": "그 업무를 기한순으로 정리해줘"}
                                        ],
                                    },
                                    ensure_ascii=False,
                                ),
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
            environment={"AX_PROFILE": "test", "DATABASE_URL": "postgresql://example/scax", "AX_WEB_ORIGIN": "https://scax.example.test"},
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
        ),
        sink=Sink(),
    )

    arguments = captured["arguments"]
    assert result.body == "업무를 조회했습니다."
    assert [(item.label, item.user_text) for item in result.follow_up_candidates] == [
        ("기한순으로 보기", "그 업무를 기한순으로 정리해줘"),
        ("우선순위 제안", "먼저 할 업무를 제안해줘"),
    ]
    assert [event.text for event in observed if event.item_type == "agent_message"] == []
    assert [(item.tool_name, item.display_name, item.state) for item in result.tool_invocations] == [
        ("task_list", "열람 가능한 업무 조회", "completed")
    ]
    assert "--ignore-user-config" in arguments
    assert 'shell_environment_policy.inherit="none"' in arguments
    assert "--sandbox" in arguments
    assert arguments[arguments.index("--sandbox") + 1] == "read-only"
    assert 'mcp_servers.scax.command="/usr/bin/python3"' in arguments
    assert 'mcp_servers.scax.args=["-m", "ax_workspace.entrypoints.mcp"]' in arguments
    assert 'mcp_servers.scax.env_vars=["AX_MCP_PERSONA", "AX_MCP_CAUSATION_ID", "AX_PROFILE", "DATABASE_URL", "AX_WEB_ORIGIN"]' in arguments
    assert 'mcp_servers.scax.default_tools_approval_mode="approve"' in arguments
    prompt = arguments[-1]
    assert "서로 의미가 겹치는 후보는 제외" in prompt
    assert "유용한 후보가 없으면 빈 배열" in prompt
    assert "일반 사용자 발화" in prompt
    assert "UUID나 내부 식별자를 답변 본문에 노출하지 않는다" in prompt
    assert "이름과 제목" in prompt
    assert "일부 참석자를 식별하지 못해도" in prompt
    assert "사용자가 말한 관계·직책 표현" in prompt
    assert "그대로 `graph_search`에 전달" in prompt
    assert "서버가 존칭과 활성 직책을 조직 원장으로 해석" in prompt
    assert "호칭을 제외한 `팀장`" not in prompt
    assert "graph_search" in prompt and "graph_neighbors" in prompt
    assert "나/내가/내 관계의 시작은 `graph_overview`" in prompt
    assert "소속 관계" in prompt
    assert "meeting_create" in prompt
    assert "조직을 생략" in prompt
    assert "기본 지속시간은 1시간" in prompt
    assert "종료 시각만 있으면 1시간 전" in prompt
    assert "MeetingNote는 draft/final 상태와 immutable version" in prompt
    assert "날짜나 관련 업무가 필요하면 source_contexts의 meeting ID 하나" in prompt
    assert "`unavailable_materials`" in prompt
    assert "그 항목의 source_contexts가 준 meeting ID만 `meeting_get`" in prompt
    assert "WorkRequest나 새 Meeting으로 바꾸지 않는다" in prompt
    assert "`meeting_share`로 열람 공유 확인을 제안" in prompt
    assert "일보가 작성됐는지·생성 중인지·제출됐는지" in prompt
    assert "수평 업무 요청·부탁" in prompt
    assert "표시 이름을 말했으면 후보 도구" in prompt
    assert "postgresql://example/scax" not in " ".join(arguments)
    assert "mina" not in " ".join(arguments)
    assert captured["environment"]["AX_MCP_PERSONA"] == "mina"
    assert captured["environment"]["AX_MCP_CAUSATION_ID"] == "turn-execution-1"
    assert captured["environment"]["DATABASE_URL"] == "postgresql://example/scax"
    assert captured["environment"]["AX_WEB_ORIGIN"] == "https://scax.example.test"
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


def _schema_runner(payload: dict, expected_required: list[str]):
    """A runner that behaves like Codex CLI under `--output-schema`: it writes exactly what the schema asked for."""

    def runner(command, arguments, cwd, environment, timeout, on_line=None, should_cancel=None):
        schema = json.loads(Path(arguments[arguments.index("--output-schema") + 1]).read_text(encoding="utf-8"))
        assert schema["required"] == expected_required, "the caller's own schema must be the one enforced"
        if on_line is not None:
            on_line(json.dumps({"type": "turn.completed", "turn_id": "turn_1"}))
        Path(arguments[arguments.index("--output-last-message") + 1]).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        return ProcessResult(json.dumps({"type": "turn.completed", "turn_id": "turn_1"}), "", 0)

    return runner


def _conversation_provider(tmp_path, runner):
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    return CodexCliProviderAdapter(
        CodexCliProfile(runtime_home=tmp_path / "runtime", auth_file=auth),
        runner=runner,
        scax_mcp_server=CodexCliMcpServer(command="python", arguments=(), environment={}),
    )


def test_a_conversation_that_asked_for_its_own_schema_gets_that_structure_back(tmp_path) -> None:
    """회의 배치·합성은 대화 turn 으로 돌면서 자기 스키마를 건다. 그 응답에 `body` 키는 없다.

    파서가 대화 계약(`body`·`elements`·`follow_up_candidates`)을 고정으로 기대하면 그 호출은
    모델이 무엇을 내든 100% `ProviderResponseInvalid` 로 죽는다.
    """
    from ax_workspace.modules.meetings.batch import OUTPUT_SCHEMA as BATCH_OUTPUT_SCHEMA, parse_output

    produced = {
        "agendas": [
            {
                "title": "다음 스프린트 범위",
                "lines": [
                    {"text": "로그인 개편을 먼저 낸다.", "evidence": [{"from_ms": 0, "to_ms": 900}], "task_id": None}
                ],
                "todos": [],
            }
        ]
    }
    provider = _conversation_provider(tmp_path, _schema_runner(produced, ["agendas"]))

    result = provider.converse(
        AiConversationRequest(
            "이번 구간을 정리해 주세요",
            "thread-1",
            [],
            AiDelegatedToolContext("mina", "meeting-batch:mina"),
            output_schema=BATCH_OUTPUT_SCHEMA,
        )
    )

    # 배치가 실제로 읽는 자리까지 간다 — body 를 그 스키마의 파서에 그대로 먹인다.
    assert json.loads(result.body) == produced
    assert [agenda.title for agenda in parse_output(result.body)] == ["다음 스프린트 범위"]
    # 대화 계약 전용 필드는 자기 스키마를 건 turn 에 붙지 않는다.
    assert result.answer_elements is None
    assert result.follow_up_candidates == []


def test_a_conversation_that_asked_for_the_final_notes_schema_parses_as_final_notes(tmp_path) -> None:
    """회의 종료 합성도 같은 자리를 지난다 — 배치와 다른 스키마 한 벌이라 따로 건다."""
    from ax_workspace.modules.meetings.finalize import FINAL_OUTPUT_SCHEMA, parse_final_output

    produced = {
        "title_candidate": "9월 정기 회의",
        "agendas": [
            {
                "title": "배포 일정",
                "merged_from": [],
                "concluded": True,
                # 최종 줄은 **근거를 반드시 든다** (사용자 결정 「최종 회의록만 회의록이다」 2026-09-14).
                "lines": [
                    {"text": "금요일에 낸다.", "evidence": [{"from_ms": 0, "to_ms": 900}], "from_lines": []}
                ],
                "todos": [],
            }
        ],
    }
    provider = _conversation_provider(tmp_path, _schema_runner(produced, ["title_candidate", "agendas"]))

    result = provider.converse(
        AiConversationRequest(
            "합성해 주세요",
            "thread-1",
            [],
            AiDelegatedToolContext("mina", "meeting-finalize:mina"),
            output_schema=FINAL_OUTPUT_SCHEMA,
        )
    )

    assert parse_final_output(result.body).title_candidate == "9월 정기 회의"


def test_a_schema_bearing_turn_that_returns_broken_json_is_still_an_invalid_response(tmp_path) -> None:
    """스키마를 걸었다고 무엇이든 통과시키지 않는다 — JSON 이 아니면 그 회차는 무효다.

    다만 사유는 그 회차의 기록에만 남는 말이다: 채팅창에서 사람이 읽는 「다시 요청해 주세요」가 아니다.
    """
    def runner(command, arguments, cwd, environment, timeout, on_line=None, should_cancel=None):
        Path(arguments[arguments.index("--output-last-message") + 1]).write_text("not json", encoding="utf-8")
        return ProcessResult("", "", 0)

    provider = _conversation_provider(tmp_path, runner)

    with pytest.raises(ProviderResponseInvalid) as raised:
        provider.converse(
            AiConversationRequest(
                "이번 구간을 정리해 주세요",
                None,
                [],
                AiDelegatedToolContext("mina", "meeting-batch:mina"),
                output_schema={"type": "object", "properties": {"agendas": {"type": "array"}}, "required": ["agendas"]},
            )
        )
    assert "다시 요청해 주세요" not in str(raised.value)


def _failing_resume_runner(stderr: str):
    def runner(command, arguments, cwd, environment, timeout, on_line=None, should_cancel=None):
        assert arguments[:2] == ["exec", "resume"]
        return ProcessResult("", stderr, 1)

    return runner


def _resume_request(session_ref: str) -> AiConversationRequest:
    return AiConversationRequest(
        "회의를 마무리해 주세요",
        session_ref,
        [],
        AiDelegatedToolContext("mina", "meeting-finalize:mina"),
        output_schema={"type": "object", "properties": {"agendas": {"type": "array"}}, "required": ["agendas"]},
    )


def test_a_resume_whose_session_is_not_in_this_runtime_home_is_session_unavailable(tmp_path) -> None:
    """B-03 — 세션은 다른 파드의 런타임 홈에 있다. 같은 resume 은 다시 걸어도 같으므로 따로 알린다."""
    from ax_workspace.modules.ax_execution.ai import ProviderSessionUnavailable

    provider = _conversation_provider(tmp_path, _failing_resume_runner("Error: no rollout found for thread id"))

    with pytest.raises(ProviderSessionUnavailable):
        provider.converse(_resume_request("01a0f5e3-7ea6-7120-b0d1-93f21361c04a"))


def test_a_resume_that_fails_with_the_session_present_is_an_ordinary_failure(tmp_path) -> None:
    from ax_workspace.modules.ax_execution.ai import ProviderSessionUnavailable

    session_ref = "01a0f5e3-7ea6-7120-b0d1-93f21361c04a"
    rollout = tmp_path / "runtime" / "sessions" / "2026" / "10" / "01" / f"rollout-2026-10-01T05-15-22-{session_ref}.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text("{}\n", encoding="utf-8")
    provider = _conversation_provider(tmp_path, _failing_resume_runner("stream error: 500"))

    with pytest.raises(ProviderRequestFailed) as raised:
        provider.converse(_resume_request(session_ref))
    assert not isinstance(raised.value, ProviderSessionUnavailable)


def test_a_failed_codex_run_logs_a_masked_bounded_stderr_summary(tmp_path, caplog) -> None:
    stderr = (
        "Authorization: Bearer abc.def.ghi\n"
        "api_key=sk-proj-0123456789abcdef token: eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl\n"
        "postgresql://ax:hunter2@db:5432/ax\n"
        + "x" * 2000
        + "\nError: session not found"
    )
    provider = _conversation_provider(tmp_path, _failing_resume_runner(stderr))

    with caplog.at_level("WARNING", logger="ax_workspace.platform.codex_cli"), pytest.raises(ProviderRequestFailed):
        provider.converse(_resume_request("thread-1"))

    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "Error: session not found" in logged, "원인은 대개 끝에 있다 — 뒤쪽을 남긴다"
    assert "종료 코드 1" in logged
    for secret in ("abc.def.ghi", "sk-proj-0123456789abcdef", "eyJhbGciOi", "hunter2"):
        assert secret not in logged
    assert max(len(record.getMessage()) for record in caplog.records) < 800


def test_summarize_stderr_masks_secrets_and_keeps_the_tail() -> None:
    from ax_workspace.platform.cli_process import STDERR_SUMMARY_LIMIT, summarize_stderr

    assert summarize_stderr("") == ""
    summary = summarize_stderr("Bearer abc123\nOPENAI_API_KEY=sk-live-abcdefghijk\npassword: \"p@ss\"\n")
    assert "abc123" not in summary and "sk-live-abcdefghijk" not in summary and "p@ss" not in summary
    assert "Bearer ***" in summary and " | " in summary
    long = summarize_stderr("a" * 5000 + "TAIL")
    assert len(long) == STDERR_SUMMARY_LIMIT and long.endswith("TAIL")


def test_runtime_home_comes_from_settings_and_defaults_to_the_old_path(tmp_path, monkeypatch) -> None:
    """런타임 홈은 `SCAX_CODEX_RUNTIME_HOME` 이 정한다 — 없으면 지금까지의 자리다(배포 변경 없이 동작)."""
    from ax_workspace.bootstrap.application import create_codex_cli_provider

    monkeypatch.delenv("SCAX_CODEX_RUNTIME_HOME", raising=False)
    default = Settings.from_environment()
    assert default.codex_runtime_home == ".scax/codex-runtime"
    assert create_codex_cli_provider(default)._profile.runtime_home == Path(".scax/codex-runtime")

    monkeypatch.setenv("SCAX_CODEX_RUNTIME_HOME", str(tmp_path / "shared-codex"))
    configured = Settings.from_environment()
    assert configured.codex_runtime_home == str(tmp_path / "shared-codex")
    assert create_codex_cli_provider(configured)._profile.runtime_home == tmp_path / "shared-codex"

    monkeypatch.setenv("SCAX_CODEX_RUNTIME_HOME", "")
    assert Settings.from_environment().codex_runtime_home == ".scax/codex-runtime"


def test_a_shared_runtime_home_tolerates_another_process_linking_auth_first(tmp_path, monkeypatch) -> None:
    from ax_workspace.platform.codex_cli import prepare_isolated_codex_home

    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    home = tmp_path / "shared"
    home.mkdir()
    original = Path.symlink_to

    def raced(self, target, *args, **kwargs):
        original(self, target, *args, **kwargs)  # 다른 파드가 먼저 걸었다
        raise FileExistsError(str(self))

    monkeypatch.setattr(Path, "symlink_to", raced)
    assert prepare_isolated_codex_home(home, auth_file=auth) == home.resolve()
