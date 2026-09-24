import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from datetime import UTC, datetime, timedelta
from uuid import UUID
from legacy_acceptance import pending_request

from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.bootstrap.report_worker import DailyReportGenerationWorker
from ax_workspace.modules.ax_execution.ai import AiConversationResult, AiGeneration, AiToolInvocation
from ax_workspace.platform.codex_cli import CodexCliProviderAdapter
from ax_workspace.platform.persistence import (
    ProviderCallRecord,
    ReportDraftRecord,
    ConversationMessageRecord,
    ConversationTurnRecord,
    AppointmentRecord,
    CapabilityRecord,
    ToolInvocationRecord,
    EmploymentPeriodRecord,
    RoleCapabilityRecord,
    RoleRecord,
    WorkflowNodeExecutionRecord,
    WorkflowRunRecord,
    make_session_factory,
)
from ax_workspace.entrypoints.mcp import McpReportsFacade
from ax_workspace.platform.work_tasks import business_date

# Reports use Seoul business dates; task activity recorded "now" must land on today's report.
REPORT_DATE = business_date(datetime.now(UTC))


class ContractTestAiProvider:
    """A test-only provider double; development composition never uses it."""

    def generate(self, request) -> AiGeneration:
        assert "authorized evidence" in request.prompt
        return AiGeneration(
            provider_run_ref="run_contract_test",
            provider_session_ref="thread_contract_test",
            body="오늘 처리한 업무를 확인했습니다.",
            requested_model="gpt-5.6-terra",
            observed_model="gpt-5.6-terra",
            requested_tier="fast",
            observed_tier="fast",
            latency_ms=12,
            usage={"input_tokens": 11, "output_tokens": 9},
        )

    def converse(self, request, *, sink=None, cancel=None) -> AiConversationResult:
        return AiConversationResult(
            provider_run_ref="chat_turn_contract_test",
            provider_session_ref="chat_session_contract_test",
            body="업무 요청을 확인했습니다.",
            tool_invocations=[
                AiToolInvocation(
                    provider_call_id="tool_call_contract_test",
                    tool_name="work_request_list",
                    display_name="업무 요청 조회",
                    input_summary="현재 사용자 요청만 조회",
                    state="completed",
                    result_summary="1건 조회",
                    error_summary=None,
                    latency_ms=7,
                )
            ],
        )


def _client_with_seeded_database(
    tmp_path,
    *,
    report_provider=None,
) -> TestClient:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    return TestClient(
        create_app(
            Settings(RuntimeProfile.TEST, database_url),
            report_provider=report_provider,
        )
    )


def _complete_report_generation(client: TestClient, tmp_path, *, max_attempts: int = 3) -> dict:
    application = client.app.state.workflow_application
    settings = Settings(
        RuntimeProfile.TEST,
        f"sqlite:///{tmp_path / 'demo.db'}",
        report_queue_max_attempts=max_attempts,
    )
    worker = DailyReportGenerationWorker(
        settings,
        provider=application._report_provider,
        queue_factory=lambda _session: application.memory_job_queue,
    )
    assert asyncio.run(worker.run_once()) is True
    return client.get(
        f"/api/daily-reports/status?report_date={REPORT_DATE}", headers={"X-Demo-Persona": "mina"}
    ).json()


def test_cancelled_conversation_holds_queued_fragments_until_a_later_send(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path, report_provider=ContractTestAiProvider())
    headers = {"X-Demo-Persona": "mina"}
    conversation = client.post("/api/conversations", headers=headers, json={"title": "취소"}).json()
    first = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**headers, "Idempotency-Key": "first"},
        json={"body": "첫 발화", "context": []},
    )
    assert first.status_code == 202
    queued = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**headers, "Idempotency-Key": "queued"},
        json={"body": "대기 발화", "context": []},
    )
    assert queued.json()["queued"] is True
    current = client.get(f"/api/conversations/{conversation['conversation_id']}", headers=headers).json()
    cancelled = client.post(
        f"/api/conversations/{conversation['conversation_id']}/cancel",
        headers=headers,
        json={"expected_version": current["version"]},
    )
    assert cancelled.status_code == 200
    held = cancelled.json()
    assert [turn["state"] for turn in held["turns"]] == ["cancelled"]
    resumed = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**headers, "Idempotency-Key": "resume"},
        json={"body": "다시 시작", "context": []},
    )
    assert resumed.status_code == 202
    assert resumed.json()["queued"] is False


def test_action_routes_require_current_read_and_decide_capabilities(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path, report_provider=ContractTestAiProvider())
    application = client.app.state.workflow_application
    mina = application.authenticated_principal("mina")
    conversation = application.create_conversation(mina, "권한 회수 확인")
    accepted = application.accept_conversation_message(
        mina,
        "확인이 필요한 변경",
        UUID(conversation["conversation_id"]),
        [],
        "action-capability",
    )
    with make_session_factory(f"sqlite:///{tmp_path / 'demo.db'}")() as session:
        turn = session.get(ConversationTurnRecord, UUID(accepted["turn_id"]))
        assert turn is not None
        action = application.propose_action(
            mina,
            turn.execution_id,
            "task.create_self",
            "업무 생성 확인",
            {"title": "권한 회수 대상"},
        )

    no_capability = {"X-Demo-Persona": "sora"}
    assert client.get("/api/actions", headers=no_capability).status_code == 403
    assert (
        client.post(
            f"/api/actions/{action['action_id']}/decide",
            headers=no_capability,
            json={"expected_version": action["version"], "decision": "approve"},
        ).status_code
        == 403
    )

    with make_session_factory(f"sqlite:///{tmp_path / 'demo.db'}")() as session:
        session.execute(
            delete(RoleCapabilityRecord).where(
                RoleCapabilityRecord.role_id == "role:member",
                RoleCapabilityRecord.capability_id == "action.decide",
            )
        )
        session.commit()

    read_only = {"X-Demo-Persona": "mina"}
    readable_actions = client.get("/api/actions", headers=read_only)
    assert readable_actions.status_code == 200
    assert [item["action_id"] for item in readable_actions.json()] == [action["action_id"]]
    conversation_actions = client.get(
        f"/api/conversations/{conversation['conversation_id']}", headers=read_only
    ).json()["actions"]
    assert [item["action_id"] for item in conversation_actions] == [action["action_id"]]
    assert (
        client.post(
            f"/api/actions/{action['action_id']}/decide",
            headers=read_only,
            json={"expected_version": action["version"], "decision": "approve"},
        ).status_code
        == 403
    )

    with make_session_factory(f"sqlite:///{tmp_path / 'demo.db'}")() as session:
        session.execute(
            delete(RoleCapabilityRecord).where(
                RoleCapabilityRecord.role_id == "role:member",
                RoleCapabilityRecord.capability_id == "action.read",
            )
        )
        session.commit()

    revoked = {"X-Demo-Persona": "mina"}
    assert client.get("/api/actions", headers=revoked).status_code == 403
    assert client.get(
        f"/api/conversations/{conversation['conversation_id']}", headers=revoked
    ).json()["actions"] == []
    assert client.get("/api/conversations", headers=revoked).json()[0]["actions"] == []


@pytest.mark.serial
def test_generate_draft_creates_a_report_owned_draft_from_authorized_task_events(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path, report_provider=ContractTestAiProvider())
    task = client.post("/api/tasks", headers={"X-Demo-Persona": "mina"}, json={"title": "보고 근거 업무"}).json()
    client.post(
        f"/api/tasks/{task['task_id']}/start",
        headers={"X-Demo-Persona": "mina"},
        json={"expected_version": task["version"]},
    )

    response = client.post(
        "/api/daily-reports/generate-draft",
        headers={"X-Demo-Persona": "mina"},
        json={"report_date": REPORT_DATE},
    )

    assert response.status_code == 202
    receipt = response.json()
    assert receipt["generation_status"] == "queued" and receipt["report_id"] is None
    completed = _complete_report_generation(client, tmp_path)
    assert completed["generation_id"] == receipt["generation_id"]
    assert completed["generation_status"] == "completed" and completed["status"] == "draft"
    history = client.get(f"/api/daily-reports/{completed['report_id']}/history", headers={"X-Demo-Persona": "mina"}).json()
    latest = history["drafts"][-1]
    body = {
        "report_id": completed["report_id"],
        "draft_id": latest["draft_id"],
        "draft_version": latest["version"],
        **latest,
    }
    assert body["draft_version"] == 1 and body["body"]
    assert body["workflow_run_id"] and body["definition_version_id"]
    assert [item["task_version"] for item in body["source_refs"]] == [1, 2]
    assert body["source_refs"][-1] == {
        "task_id": task["task_id"],
        "task_version": 2,
        "state": "in_progress",
        "occurred_at": body["source_refs"][-1]["occurred_at"],
    }

    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    with make_session_factory(database_url)() as session:
        draft = session.get(ReportDraftRecord, UUID(body["draft_id"]))
        run = session.get(WorkflowRunRecord, UUID(body["workflow_run_id"]))
        assert draft is not None
        assert run is not None
        assert draft.workflow_run_id == run.id
        assert draft.definition_version_id == run.definition_version_id
        assert run.state == "completed"
        assert len(list(session.scalars(select(WorkflowNodeExecutionRecord).where(WorkflowNodeExecutionRecord.run_id == run.id)))) == 4
        provider_call = session.scalar(
            select(ProviderCallRecord).join(WorkflowNodeExecutionRecord).where(WorkflowNodeExecutionRecord.run_id == run.id)
        )
        assert provider_call is not None
        assert provider_call.provider_run_ref == "run_contract_test"
        assert provider_call.provider_session_ref == "thread_contract_test"
        assert provider_call.requested_model == "gpt-5.6-terra"
        assert provider_call.requested_tier == "fast"
        assert provider_call.observed_tier == "fast"


@pytest.mark.serial
def test_generate_draft_fails_explicitly_without_the_codex_cli_binary(tmp_path) -> None:
    client = _client_with_seeded_database(
        tmp_path,
        report_provider=CodexCliProviderAdapter(command="definitely-missing-codex-test-binary"),
    )

    response = client.post(
        "/api/daily-reports/generate-draft",
        headers={"X-Demo-Persona": "mina"},
        json={"report_date": REPORT_DATE},
    )

    assert response.status_code == 202
    failed = _complete_report_generation(client, tmp_path, max_attempts=1)
    assert failed["generation_status"] == "failed"
    assert failed["generation_error_code"] == "report_provider_failed"
    assert failed["report_id"] is None


@pytest.mark.serial
def test_daily_report_edit_submit_and_history_are_report_owned_operations(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path, report_provider=ContractTestAiProvider())
    before_generation = client.get(
        f"/api/daily-reports/status?report_date={REPORT_DATE}",
        headers={"X-Demo-Persona": "mina"},
    )
    assert before_generation.json() == {
        "report_date": REPORT_DATE,
        "status": "not_started",
        "report_id": None,
        "generation_id": None,
        "generation_status": None,
        "generation_error_code": None,
    }
    accepted = client.post(
        "/api/daily-reports/generate-draft",
        headers={"X-Demo-Persona": "mina"},
        json={"report_date": REPORT_DATE},
    ).json()
    completed = _complete_report_generation(client, tmp_path)
    history = client.get(f"/api/daily-reports/{completed['report_id']}/history", headers={"X-Demo-Persona": "mina"}).json()
    latest = history["drafts"][-1]
    generated = {
        "report_id": completed["report_id"],
        "draft_id": latest["draft_id"],
        "draft_version": latest["version"],
        "workflow_run_id": latest["workflow_run_id"],
        "definition_version_id": latest["definition_version_id"],
    }
    assert completed["generation_id"] == accepted["generation_id"]

    edited = client.post(
        f"/api/daily-reports/{generated['report_id']}/edit",
        headers={"X-Demo-Persona": "mina"},
        json={
            "draft_id": generated["draft_id"],
            "expected_version": generated["draft_version"],
            "body": "사람이 확인하고 보완한 보고입니다.",
            "exclude_source_refs": [],
            "include_source_refs": [],
        },
    )
    assert edited.status_code == 200
    edited_body = edited.json()
    assert edited_body["draft_version"] == 2
    assert edited_body["body"] == "사람이 확인하고 보완한 보고입니다."
    draft_status = client.get(
        f"/api/daily-reports/status?report_date={REPORT_DATE}",
        headers={"X-Demo-Persona": "mina"},
    )
    assert draft_status.json() == {
        "report_date": REPORT_DATE,
        "status": "draft",
        "report_id": generated["report_id"],
        "generation_id": accepted["generation_id"],
        "generation_status": "completed",
        "generation_error_code": None,
    }

    submitted = client.post(
        f"/api/daily-reports/{generated['report_id']}/submit",
        headers={"X-Demo-Persona": "mina"},
        json={
            "draft_id": edited_body["draft_id"],
            "expected_version": edited_body["draft_version"],
            "reason": None,
        },
    )
    assert submitted.status_code == 201
    submitted_body = submitted.json()
    assert submitted_body["submission_version"] == 1
    assert submitted_body["body"] == "사람이 확인하고 보완한 보고입니다."
    submitted_status = client.get(
        f"/api/daily-reports/status?report_date={REPORT_DATE}",
        headers={"X-Demo-Persona": "mina"},
    )
    assert submitted_status.json() == {
        "report_date": REPORT_DATE,
        "status": "submitted",
        "report_id": generated["report_id"],
        "generation_id": accepted["generation_id"],
        "generation_status": "completed",
        "generation_error_code": None,
    }

    history = client.get(
        f"/api/daily-reports/{generated['report_id']}/history",
        headers={"X-Demo-Persona": "mina"},
    )
    assert history.status_code == 200
    assert [item["version"] for item in history.json()["drafts"]] == [1, 2]
    assert {item["workflow_run_id"] for item in history.json()["drafts"]} == {generated["workflow_run_id"]}
    assert {item["definition_version_id"] for item in history.json()["drafts"]} == {generated["definition_version_id"]}
    assert history.json()["submissions"][0]["body"] == "사람이 확인하고 보완한 보고입니다."

    stale = client.post(
        f"/api/daily-reports/{generated['report_id']}/edit",
        headers={"X-Demo-Persona": "mina"},
        json={
            "draft_id": edited_body["draft_id"],
            "expected_version": 1,
            "body": "낡은 편집",
        },
    )
    assert stale.status_code == 422


def test_generate_draft_rejects_future_report_date(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path)

    response = client.post(
        "/api/daily-reports/generate-draft",
        headers={"X-Demo-Persona": "mina"},
        json={"report_date": "2099-01-01"},
    )

    assert response.status_code == 422


def test_daily_report_does_not_expose_the_legacy_human_confirmation_run(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path)
    response = client.post(
        "/api/runs/daily-report",
        headers={"X-Demo-Persona": "mina"},
        json={"input": {}},
    )
    assert response.status_code == 404


def test_task_and_work_request_capabilities_are_enforced_for_http_and_mcp(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path)
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"

    task_denied = client.get("/api/tasks", headers={"X-Demo-Persona": "sora"})
    request_denied = client.get("/api/work-requests", headers={"X-Demo-Persona": "sora"})

    assert task_denied.status_code == 403
    assert request_denied.status_code == 403

    facade = McpReportsFacade(Settings(RuntimeProfile.TEST, database_url), "sora", ContractTestAiProvider())
    with pytest.raises(Exception, match="task.read"):
        facade.list_tasks()
    with pytest.raises(Exception, match="work_request.read"):
        facade.list_work_requests()

    report_denied = client.post(
        "/api/daily-reports/generate-draft",
        headers={"X-Demo-Persona": "jiho"},
        json={"report_date": REPORT_DATE},
    )
    assert report_denied.status_code == 403
    assert "daily_report.generate" in report_denied.json()["detail"]


def test_task_cancel_and_stale_transition_leave_no_extra_mutation(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path)
    task = client.post("/api/tasks", headers={"X-Demo-Persona": "mina"}, json={"title": "취소할 업무"}).json()

    stale = client.post(
        f"/api/tasks/{task['task_id']}/start",
        headers={"X-Demo-Persona": "mina"},
        json={"expected_version": 99},
    )
    assert stale.status_code == 422
    assert client.post(
        f"/api/tasks/{task['task_id']}/start",
        headers={"X-Demo-Persona": "mina"},
    ).status_code == 422
    cancelled = client.post(
        f"/api/tasks/{task['task_id']}/cancel",
        headers={"X-Demo-Persona": "mina"},
        json={"expected_version": 1, "reason": "이번 분기에는 하지 않습니다"},
    )
    assert cancelled.json()["state"] == "cancelled"
    assert client.get("/api/my-work", headers={"X-Demo-Persona": "mina"}).json() == []


def test_work_request_creates_a_task_without_waiting_for_the_assignee(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path)
    created = client.post(
        "/api/work-requests",
        headers={"X-Demo-Persona": "mina"},
        json={"title": "제품 요구사항 확인", "assignee_id": "jiho"},
    )
    assert created.status_code == 201
    request = created.json()
    # 출처 상태는 `pending` — 업무는 섰고 **담당은 수락을 기다린다** (SPEC-003 §4 발송 · 정책 V-10).
    assert request["state"] == "pending"
    assert request["task_id"] and request["assignment_state"] == "pending"
    # 받는 사람에게는 **답할 질문 하나**가 서고, 답하기 전에는 「내 업무」에 서지 않는다.
    assert [row["kind"] for row in client.get("/api/action-items", headers={"X-Demo-Persona": "jiho"}).json()] == [
        "work_request.acceptance"
    ]
    assert client.get("/api/my-work", headers={"X-Demo-Persona": "jiho"}).json() == []
    answered = client.post(
        f"/api/work-requests/{request['request_id']}/accept",
        headers={"X-Demo-Persona": "jiho"}, json={"expected_version": request["version"]},
    )
    assert answered.status_code == 200, answered.text
    assert client.get("/api/my-work", headers={"X-Demo-Persona": "jiho"}).json()[0]["task_id"] == request["task_id"]


def test_conversation_turn_persists_messages_context_provider_refs_and_tool_activity(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path, report_provider=ContractTestAiProvider())
    created = client.post(
        "/api/conversations",
        headers={"X-Demo-Persona": "mina"},
        json={"title": "업무 확인"},
    )
    assert created.status_code == 201

    response = client.post(
        f"/api/conversations/{created.json()['conversation_id']}/messages",
        headers={"X-Demo-Persona": "mina"},
        json={
            "body": "내 업무 요청을 확인해 주세요.",
            "context": [],
        },
    )

    assert response.status_code == 202
    accepted = response.json()
    assert accepted["conversation_id"] == created.json()["conversation_id"]
    assert accepted["queued"] is False
    assert accepted["turn_id"]

    pending = client.get(
        f"/api/conversations/{created.json()['conversation_id']}",
        headers={"X-Demo-Persona": "mina"},
    ).json()
    assert [message["role"] for message in pending["messages"]] == ["user"]
    assert pending["turns"][0]["state"] == "pending"

    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    with make_session_factory(database_url)() as session:
        assert session.scalar(select(ConversationTurnRecord)) is not None
        assert len(list(session.scalars(select(ConversationMessageRecord)))) == 1
        assert session.scalar(select(ToolInvocationRecord)) is None


def test_conversation_queues_fragments_without_losing_their_original_order(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path, report_provider=ContractTestAiProvider())
    created = client.post(
        "/api/conversations",
        headers={"X-Demo-Persona": "mina"},
        json={"title": "큐 대화"},
    ).json()
    first = client.post(
        f"/api/conversations/{created['conversation_id']}/messages",
        headers={"X-Demo-Persona": "mina"},
        json={"body": "첫 작업입니다.", "context": []},
    )
    assert first.status_code == 202
    assert first.json()["queued"] is False

    queued = client.post(
        f"/api/conversations/{created['conversation_id']}/messages",
        headers={"X-Demo-Persona": "mina"},
        json={"body": "첫 작업이 끝나면 이어서 확인해 주세요.", "context": []},
    )
    assert queued.status_code == 202
    assert queued.json()["queued"] is True
    assert queued.json()["turn_id"] is None
    assert queued.json()["queue_size"] == 1

    accepted = client.get(
        f"/api/conversations/{created['conversation_id']}",
        headers={"X-Demo-Persona": "mina"},
    ).json()
    assert [message["state"] for message in accepted["messages"]] == [
        "accepted",
        "queued",
    ]
    assert [message["body"] for message in accepted["messages"]] == [
        "첫 작업입니다.",
        "첫 작업이 끝나면 이어서 확인해 주세요.",
    ]


def test_conversation_context_is_resolved_server_side_and_rejects_stale_or_unowned_refs(
    tmp_path,
) -> None:
    client = _client_with_seeded_database(tmp_path)
    task = client.post(
        "/api/tasks",
        headers={"X-Demo-Persona": "mina"},
        json={"title": "서버가 읽어야 할 업무"},
    ).json()
    mina_conversation = client.post(
        "/api/conversations",
        headers={"X-Demo-Persona": "mina"},
        json={"title": "근거 대화"},
    ).json()

    accepted = client.post(
        f"/api/conversations/{mina_conversation['conversation_id']}/messages",
        headers={"X-Demo-Persona": "mina"},
        json={
            "body": "이 업무를 확인해 주세요.",
            "context": [
                {
                    "resource_type": "task",
                    "resource_id": task["task_id"],
                    "resource_version": task["version"],
                    "included": True,
                }
            ],
        },
    )
    assert accepted.status_code == 202
    context = client.get(
        f"/api/conversations/{mina_conversation['conversation_id']}",
        headers={"X-Demo-Persona": "mina"},
    ).json()["context_references"]
    assert context == [
        {
            "message_id": accepted.json()["message_id"],
            "turn_id": accepted.json()["turn_id"],
            "resource_type": "task",
            "resource_id": task["task_id"],
            "resource_version": task["version"],
            "summary": "업무: 서버가 읽어야 할 업무 (open)",
            "included": True,
        }
    ]

    stale = client.post(
        f"/api/conversations/{mina_conversation['conversation_id']}/messages",
        headers={"X-Demo-Persona": "mina"},
        json={
            "body": "오래된 근거입니다.",
            "context": [
                {
                    "resource_type": "task",
                    "resource_id": task["task_id"],
                    "resource_version": task["version"] + 1,
                    "included": True,
                }
            ],
        },
    )
    assert stale.status_code == 422
    assert "stale" in stale.json()["detail"]

    jiho_conversation = client.post(
        "/api/conversations",
        headers={"X-Demo-Persona": "jiho"},
        json={"title": "권한 밖 근거"},
    ).json()
    denied = client.post(
        f"/api/conversations/{jiho_conversation['conversation_id']}/messages",
        headers={"X-Demo-Persona": "jiho"},
        json={
            "body": "다른 사람 업무를 보겠습니다.",
            "context": [
                {
                    "resource_type": "task",
                    "resource_id": task["task_id"],
                    "resource_version": task["version"],
                    "included": True,
                }
            ],
        },
    )
    assert denied.status_code == 422
    assert "not found" in denied.json()["detail"]


def test_work_request_uses_authorized_organization_candidates_and_rejects_an_out_of_scope_assignee(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path)

    candidates = client.get(
        "/api/work-request-assignee-candidates",
        headers={"X-Demo-Persona": "mina"},
    )

    assert candidates.status_code == 200
    # 내가 업무를 보낼 수 있는 사람 — **판단 역량을 묻지 않는다** (WORK-001 Phase 3). 남는 것은 로그인 가능 ·
    # 본인 제외 · 조직 범위 교집합 셋이고, 데모 조직은 회사 하나라 그 셋을 지난 동료 전부가 뜬다.
    assert candidates.json() == [
        {"id": "hyeon", "display_name": "현우 (인사)"},
        {"id": "jiho", "display_name": "지호 (팀장)"},
        {"id": "minseok", "display_name": "민석 (재무)"},
        {"id": "sora", "display_name": "소라 (법무 자문)"},
        {"id": "yuna", "display_name": "유나 (대표)"},
    ]
    assert "mina" not in {row["id"] for row in candidates.json()}

    # **권한 밖 대상**은 명부에 없는 사람이다. 데모 조직은 회사 하나뿐이라 조직 범위 교집합이
    # 늘 겹치므로, 이 조직에서 후보 밖으로 남는 것은 원장에 없는 식별자와 재직하지 않는 구성원이다.
    unknown = client.post(
        "/api/work-requests",
        headers={"X-Demo-Persona": "mina"},
        json={"title": "임의 식별자 배정 시도", "assignee_id": "not-in-the-ledger"},
    )
    assert unknown.status_code == 422

    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    with make_session_factory(database_url)() as session:
        employment = session.scalar(
            select(EmploymentPeriodRecord).where(EmploymentPeriodRecord.member_id == "jiho")
        )
        assert employment is not None
        employment.state = "inactive"
        session.commit()

    inactive = client.post(
        "/api/work-requests",
        headers={"X-Demo-Persona": "mina"},
        json={"title": "비활성 구성원 배정 시도", "assignee_id": "jiho"},
    )
    assert inactive.status_code == 422


def test_work_request_rejection_never_creates_a_task(tmp_path) -> None:
    """과거 행의 거절은 그대로 돈다 — 신규 경로가 그 명령을 열지 않을 뿐이다 (DEC-001 D-4)."""
    client = _client_with_seeded_database(tmp_path)
    created = pending_request(
        client, f"sqlite:///{tmp_path / 'demo.db'}", {"X-Demo-Persona": "mina"},
        title="지금은 수락할 수 없는 요청", assignee_id="jiho",
    )

    rejected = client.post(
        f"/api/work-requests/{created['request_id']}/reject",
        headers={"X-Demo-Persona": "jiho"},
        json={"expected_version": created["version"], "reason": "현재 우선순위와 맞지 않습니다."},
    )
    assert rejected.status_code == 200
    assert rejected.json()["state"] == "rejected"
    assert rejected.json()["task_id"] is None
    assert client.get("/api/my-work", headers={"X-Demo-Persona": "jiho"}).json() == []


def test_work_request_negotiation_updates_conditions_and_requires_a_fresh_decision(tmp_path) -> None:
    """조정 회차도 과거 행에만 있다 — 신규 요청은 `assigned` 로 서고 그 회차를 만들지 않는다."""
    client = _client_with_seeded_database(tmp_path)
    created = pending_request(
        client, f"sqlite:///{tmp_path / 'demo.db'}", {"X-Demo-Persona": "mina"},
        title="일정 협의가 필요한 요청", assignee_id="jiho",
    )

    negotiated = client.post(
        f"/api/work-requests/{created['request_id']}/negotiate",
        headers={"X-Demo-Persona": "jiho"},
        json={"expected_version": created["version"], "conditions": {"due_date": "2026-09-05"}},
    )
    assert negotiated.status_code == 200
    assert negotiated.json()["state"] == "negotiating"
    assert negotiated.json()["conditions"] == {"due_date": "2026-09-05"}
    assert client.get("/api/my-work", headers={"X-Demo-Persona": "jiho"}).json() == []

    accepted = client.post(
        f"/api/work-requests/{created['request_id']}/accept",
        headers={"X-Demo-Persona": "jiho"},
        json={"expected_version": negotiated.json()["version"]},
    )
    assert accepted.status_code == 200
    assert accepted.json()["task_id"]


def test_organization_profile_is_a_persisted_authorized_projection(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path)

    response = client.get("/api/organization/me", headers={"X-Demo-Persona": "mina"})

    assert response.status_code == 200
    body = response.json()
    grants = body.pop("grants")
    assert len(grants) == 1 and grants[0]["role_id"] == "role:member" and grants[0]["scope_ref"] == "product"
    assert body == {
        "member_id": "mina",
        "display_name": "민아 (구성원)",
        "assistant_character": {"character_key": "cream-cat", "version": 0},
        "organizations": [{"id": "product", "name": "제품팀"}, {"id": "scax", "name": "SCAX"}],
        "roles": ["구성원"],
        "capabilities": [
            "action.decide",
            "action.read",
            "daily_report.edit",
            "daily_report.generate",
            "daily_report.read",
            "daily_report.submit",
            "meeting.followup.request",
            "meeting.manage",
            "meeting.read",
            "meeting.record",
            "meeting.share",
            # 프로젝트를 볼 수 있다는 것뿐이다. 어느 프로젝트인지는 grant의 범위가 정한다.
            "project.read",
            "task.accept",
            "task.read",
            "task.self_manage",
            "work.read",
            "work_request.create",
            # v2: 자기에게 온 요청은 자기가 답한다 — 수신자 검사가 그 위에 따로 선다 (SPEC-003 §5 권한).
            "work_request.decide",
            "work_request.read",
        ],
    }


def test_organization_seed_persists_appointment_role_capability_and_grant_ledger(tmp_path) -> None:
    _client_with_seeded_database(tmp_path)
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"

    with make_session_factory(database_url)() as session:
        appointment = session.scalar(
            select(AppointmentRecord).where(AppointmentRecord.member_id == "mina")
        )
        assert appointment is not None
        # 배정은 그 사람이 실제로 앉아 있는 조직에 걸린다.
        assert appointment.organization_id == "product"
        role = session.get(RoleRecord, appointment.role_id)
        assert role is not None
        assert session.scalar(
            select(RoleCapabilityRecord).where(
                RoleCapabilityRecord.role_id == role.id,
                RoleCapabilityRecord.capability_id == "task.self_manage",
            )
        ) is not None
        assert session.get(CapabilityRecord, "task.self_manage") is not None


def test_expired_appointment_is_removed_from_the_server_principal_projection(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path)
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    with make_session_factory(database_url)() as session:
        appointment = session.scalar(
            select(AppointmentRecord).where(AppointmentRecord.member_id == "mina")
        )
        assert appointment is not None
        appointment.valid_until = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    profile = client.get("/api/organization/me", headers={"X-Demo-Persona": "mina"})
    denied = client.post(
        "/api/daily-reports/generate-draft",
        headers={"X-Demo-Persona": "mina"},
        json={"report_date": REPORT_DATE},
    )

    assert "daily_report.generate" not in profile.json()["capabilities"]
    assert denied.status_code == 403


def test_organization_principal_projects_persona_specific_grants(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path)

    mina = client.get("/api/organization/me", headers={"X-Demo-Persona": "mina"}).json()
    sora = client.get("/api/organization/me", headers={"X-Demo-Persona": "sora"}).json()

    assert mina["organizations"] != sora["organizations"]
    assert "work.read" in mina["capabilities"]
    # 외부 참여자는 회의만 읽는다.
    assert sora["capabilities"] == ["meeting.read"]


def test_repeated_comment_post_with_one_idempotency_key_creates_a_single_comment(tmp_path) -> None:
    """A double submit (two POSTs through the same React state window) must not become two comments."""
    client = _client_with_seeded_database(tmp_path)
    request = client.post(
        "/api/work-requests",
        headers={"X-Demo-Persona": "mina"},
        json={"title": "논의가 필요한 요청", "assignee_id": "jiho"},
    ).json()
    url = f"/api/work-requests/{request['request_id']}/comments"
    headers = {"X-Demo-Persona": "mina", "Idempotency-Key": "comment-submit-1"}

    first = client.post(url, headers=headers, json={"body": "논의 추가"})
    second = client.post(url, headers=headers, json={"body": "논의 추가"})
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["comment_id"] == second.json()["comment_id"]
    timeline = client.get(f"/api/work-requests/{request['request_id']}/timeline", headers={"X-Demo-Persona": "mina"}).json()
    assert [item["body"] for item in timeline["comments"]] == ["논의 추가"]

    # Reusing the key for different text is a conflict, never a silent overwrite of the stored comment.
    conflicting = client.post(url, headers=headers, json={"body": "다른 내용"})
    assert conflicting.status_code == 409, conflicting.text
    timeline = client.get(f"/api/work-requests/{request['request_id']}/timeline", headers={"X-Demo-Persona": "mina"}).json()
    assert [item["body"] for item in timeline["comments"]] == ["논의 추가"]

    # A different key is a different logical submit, and the same text may legitimately be said twice.
    again = client.post(url, headers={**headers, "Idempotency-Key": "comment-submit-2"}, json={"body": "논의 추가"})
    assert again.status_code == 201 and again.json()["comment_id"] != first.json()["comment_id"]
    # The key is scoped per author: another participant's identical key is their own comment.
    other = client.post(url, headers={"X-Demo-Persona": "jiho", "Idempotency-Key": "comment-submit-1"}, json={"body": "논의 추가"})
    assert other.status_code == 201 and other.json()["comment_id"] != first.json()["comment_id"]
    timeline = client.get(f"/api/work-requests/{request['request_id']}/timeline", headers={"X-Demo-Persona": "mina"}).json()
    assert len(timeline["comments"]) == 3

    # Without a key the old behaviour is unchanged: every post is its own comment.
    plain = client.post(url, headers={"X-Demo-Persona": "mina"}, json={"body": "키 없는 발언"})
    assert plain.status_code == 201
    assert len(client.get(f"/api/work-requests/{request['request_id']}/timeline", headers={"X-Demo-Persona": "mina"}).json()["comments"]) == 4
