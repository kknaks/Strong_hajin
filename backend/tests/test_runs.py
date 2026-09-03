from fastapi.testclient import TestClient
from sqlalchemy import select
from uuid import UUID

from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.modules.ax_execution.ai import AiGeneration
from ax_workspace.platform.persistence import (
    ProviderCallRecord,
    ReportDraftRecord,
    WorkflowNodeExecutionRecord,
    WorkflowRunRecord,
    make_session_factory,
)


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


def _client_with_seeded_database(
    tmp_path,
    *,
    report_provider=None,
    technical_spike: bool = False,
) -> TestClient:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url, technical_spike=technical_spike)
    return TestClient(
        create_app(
            Settings(RuntimeProfile.TEST, database_url),
            report_provider=report_provider,
            technical_spike=technical_spike,
        )
    )


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
        json={"report_date": "2026-09-03"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "draft"
    assert body["report_id"]
    assert body["draft_id"]
    assert body["draft_version"] == 1
    assert body["body"]
    assert body["workflow_run_id"]
    assert body["definition_version_id"]
    assert body["workflow_state"] == "completed"
    assert body["submission_status"] == "unsubmitted"
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


def test_generate_draft_fails_explicitly_without_the_codex_cli_binary(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("ax_workspace.platform.codex_cli.shutil.which", lambda _: None)
    client = _client_with_seeded_database(tmp_path)

    response = client.post(
        "/api/daily-reports/generate-draft",
        headers={"X-Demo-Persona": "mina"},
        json={"report_date": "2026-09-03"},
    )

    assert response.status_code == 503
    assert "binary is not available" in response.json()["detail"]


def test_daily_report_edit_submit_and_history_are_report_owned_operations(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path, report_provider=ContractTestAiProvider())
    generated = client.post(
        "/api/daily-reports/generate-draft",
        headers={"X-Demo-Persona": "mina"},
        json={"report_date": "2026-09-03"},
    ).json()

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

    history = client.get(
        f"/api/daily-reports/{generated['report_id']}/history",
        headers={"X-Demo-Persona": "mina"},
    )
    assert history.status_code == 200
    assert [item["version"] for item in history.json()["drafts"]] == [1, 2]
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


def test_self_created_task_enters_my_work_and_only_allows_valid_lifecycle_transitions(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path)

    created = client.post(
        "/api/tasks",
        headers={"X-Demo-Persona": "mina"},
        json={"title": "고객 피드백 정리"},
    )

    assert created.status_code == 201
    task = created.json()
    assert task["state"] == "open"
    assert client.get("/api/my-work", headers={"X-Demo-Persona": "mina"}).json()[0]["task_id"] == task["task_id"]
    assert client.post(f"/api/tasks/{task['task_id']}/complete", headers={"X-Demo-Persona": "mina"}, json={"expected_version": 1}).status_code == 422
    assert client.post(f"/api/tasks/{task['task_id']}/start", headers={"X-Demo-Persona": "mina"}, json={"expected_version": 1}).json()["state"] == "in_progress"
    assert client.post(
        f"/api/tasks/{task['task_id']}/block", headers={"X-Demo-Persona": "mina"}, json={"reason": "고객 자료 대기", "expected_version": 2}
    ).json()["state"] == "blocked"
    assert client.post(f"/api/tasks/{task['task_id']}/resume", headers={"X-Demo-Persona": "mina"}, json={"expected_version": 3}).json()["state"] == "in_progress"
    completed = client.post(
        f"/api/tasks/{task['task_id']}/complete",
        headers={"X-Demo-Persona": "mina"},
        json={"expected_version": 4},
    )
    assert completed.json()["state"] == "done"


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
        json={"expected_version": 1},
    )
    assert cancelled.json()["state"] == "cancelled"
    assert client.get("/api/my-work", headers={"X-Demo-Persona": "mina"}).json() == []


def test_work_request_creates_a_task_only_after_the_assignee_accepts(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path)
    created = client.post(
        "/api/work-requests",
        headers={"X-Demo-Persona": "mina"},
        json={"title": "제품 요구사항 확인", "assignee_id": "jiho"},
    )
    assert created.status_code == 201
    request = created.json()
    assert request["state"] == "pending"
    assert client.get("/api/my-work", headers={"X-Demo-Persona": "jiho"}).json() == []
    assert client.get("/api/action-inbox", headers={"X-Demo-Persona": "jiho"}).json() == [request]

    accepted = client.post(
        f"/api/work-requests/{request['request_id']}/accept",
        headers={"X-Demo-Persona": "jiho"},
        json={"expected_version": request["version"]},
    )
    assert accepted.status_code == 200
    assert accepted.json()["state"] == "accepted"
    assert accepted.json()["task_id"]
    assert accepted.json()["assignment_state"] == "active"
    assert client.get("/api/my-work", headers={"X-Demo-Persona": "jiho"}).json()[0]["task_id"] == accepted.json()["task_id"]


def test_work_request_rejection_never_creates_a_task(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path)
    created = client.post(
        "/api/work-requests",
        headers={"X-Demo-Persona": "mina"},
        json={"title": "지금은 수락할 수 없는 요청", "assignee_id": "jiho"},
    ).json()

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
    client = _client_with_seeded_database(tmp_path)
    created = client.post(
        "/api/work-requests",
        headers={"X-Demo-Persona": "mina"},
        json={"title": "일정 협의가 필요한 요청", "assignee_id": "jiho"},
    ).json()

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
    assert response.json() == {
        "member_id": "mina",
        "display_name": "민아 (구성원)",
        "organizations": [{"id": "product", "name": "제품팀"}, {"id": "scax", "name": "SCAX"}],
        "capabilities": ["daily_report.submit", "meeting.followup.request", "task.accept", "work.read"],
    }


def test_organization_principal_projects_persona_specific_grants(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path)

    mina = client.get("/api/organization/me", headers={"X-Demo-Persona": "mina"}).json()
    sora = client.get("/api/organization/me", headers={"X-Demo-Persona": "sora"}).json()

    assert mina["organizations"] != sora["organizations"]
    assert "work.read" in mina["capabilities"]
    assert sora["capabilities"] == ["contract.legal_review"]


def test_meeting_assignment_never_enters_my_work_before_the_selected_assignee_accepts(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path, technical_spike=True)
    started = client.post("/api/runs/meeting-followups", headers={"X-Demo-Persona": "mina"}, json={"input": {}}).json()
    assert started["waiting_on"] == ["choose-assignment"]

    requested = client.post(
        f"/api/runs/{started['run_id']}/decisions/choose-assignment",
        headers={"X-Demo-Persona": "mina"},
        json={"decision": "accept", "payload": {"assignee_id": "mina"}},
    )
    assert requested.status_code == 200
    assert requested.json()["waiting_on"] == ["accept-assignment"]
    assert client.get("/api/my-work", headers={"X-Demo-Persona": "mina"}).json() == []

    inbox = client.get("/api/inbox", headers={"X-Demo-Persona": "mina"}).json()
    assert {item["node_id"] for item in inbox} == {"accept-assignment"}
    completed = client.post(
        f"/api/runs/{started['run_id']}/decisions/accept-assignment",
        headers={"X-Demo-Persona": "mina"},
        json={"decision": "accept"},
    )
    assert completed.json()["state"] == "completed"
    assert client.get("/api/my-work", headers={"X-Demo-Persona": "mina"}).json()[0]["state"] == "active"


def test_assignment_rejection_never_enters_my_work(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path, technical_spike=True)
    started = client.post("/api/runs/meeting-followups", headers={"X-Demo-Persona": "mina"}, json={"input": {}}).json()
    client.post(
        f"/api/runs/{started['run_id']}/decisions/choose-assignment",
        headers={"X-Demo-Persona": "mina"},
        json={"decision": "accept", "payload": {"assignee_id": "mina"}},
    )

    rejected = client.post(
        f"/api/runs/{started['run_id']}/decisions/accept-assignment",
        headers={"X-Demo-Persona": "mina"},
        json={"decision": "reject", "rationale": "일정상 수락할 수 없습니다."},
    )

    assert rejected.json()["state"] == "rejected"
    assert client.get("/api/my-work", headers={"X-Demo-Persona": "mina"}).json() == []


def test_meeting_assignment_candidates_are_limited_to_seeded_task_acceptors(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path, technical_spike=True)

    response = client.get("/api/meeting-assignment-candidates", headers={"X-Demo-Persona": "mina"})

    assert response.status_code == 200
    assert response.json() == [
        {"id": "mina", "display_name": "민아 (구성원)"},
        {"id": "demo-admin", "display_name": "데모 관리자"},
    ]


def test_meeting_assignment_rejects_a_seeded_persona_without_task_acceptance_capability(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path, technical_spike=True)
    started = client.post("/api/runs/meeting-followups", headers={"X-Demo-Persona": "mina"}, json={"input": {}}).json()

    response = client.post(
        f"/api/runs/{started['run_id']}/decisions/choose-assignment",
        headers={"X-Demo-Persona": "mina"},
        json={"decision": "accept", "payload": {"assignee_id": "sora"}},
    )

    assert response.status_code == 422
    assert client.get("/api/my-work", headers={"X-Demo-Persona": "sora"}).json() == []


def test_only_the_selected_assignee_can_accept_an_assignment(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path, technical_spike=True)
    started = client.post("/api/runs/meeting-followups", headers={"X-Demo-Persona": "mina"}, json={"input": {}}).json()
    client.post(
        f"/api/runs/{started['run_id']}/decisions/choose-assignment",
        headers={"X-Demo-Persona": "mina"},
        json={"decision": "accept", "payload": {"assignee_id": "mina"}},
    )

    intruder = client.post(
        f"/api/runs/{started['run_id']}/decisions/accept-assignment",
        headers={"X-Demo-Persona": "demo-admin"},
        json={"decision": "accept"},
    )

    assert intruder.status_code == 403
    assert client.get("/api/my-work", headers={"X-Demo-Persona": "mina"}).json() == []


def test_contract_effect_waits_for_both_independent_human_acceptances(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path, technical_spike=True)
    started = client.post(
        "/api/runs/contract-review",
        headers={"X-Demo-Persona": "demo-admin"},
        json={"input": {"contract_id": "contract-17"}},
    ).json()
    assert set(started["waiting_on"]) == {"legal", "finance"}

    legal = client.post(
        f"/api/runs/{started['run_id']}/decisions/legal",
        headers={"X-Demo-Persona": "sora"},
        json={"decision": "accept"},
    ).json()
    assert legal["waiting_on"] == ["finance"]
    assert "contract.approve" not in {item["tool_name"] for item in legal["tool_results"]}

    completed = client.post(
        f"/api/runs/{started['run_id']}/decisions/finance",
        headers={"X-Demo-Persona": "minseok"},
        json={"decision": "accept"},
    ).json()
    assert completed["state"] == "completed"
    assert "contract.approve" in {item["tool_name"] for item in completed["tool_results"]}


def test_contract_rejection_prevents_the_post_join_effect(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path, technical_spike=True)
    started = client.post(
        "/api/runs/contract-review",
        headers={"X-Demo-Persona": "demo-admin"},
        json={"input": {"contract_id": "contract-18"}},
    ).json()

    rejected = client.post(
        f"/api/runs/{started['run_id']}/decisions/legal",
        headers={"X-Demo-Persona": "sora"},
        json={"decision": "reject"},
    ).json()

    assert rejected["state"] == "rejected"
    assert "contract.approve" not in {item["tool_name"] for item in rejected["tool_results"]}


def test_assigned_contract_reviewer_can_view_but_unrelated_persona_cannot_view_the_run(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path, technical_spike=True)
    started = client.post(
        "/api/runs/contract-review",
        headers={"X-Demo-Persona": "demo-admin"},
        json={"input": {"contract_id": "contract-visibility"}},
    ).json()

    reviewer = client.get(f"/api/runs/{started['run_id']}", headers={"X-Demo-Persona": "sora"})
    unrelated = client.get(f"/api/runs/{started['run_id']}", headers={"X-Demo-Persona": "mina"})

    assert reviewer.status_code == 200
    assert reviewer.json()["waiting_on"] == ["legal", "finance"]
    assert unrelated.status_code == 403


def test_run_state_and_audit_are_recovered_by_a_fresh_application_instance(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url, technical_spike=True)
    first_client = TestClient(create_app(Settings(RuntimeProfile.TEST, database_url), technical_spike=True))
    started = first_client.post("/api/runs/weekly-report", headers={"X-Demo-Persona": "mina"}, json={"input": {}}).json()

    restarted_client = TestClient(create_app(Settings(RuntimeProfile.TEST, database_url), technical_spike=True))
    recovered = restarted_client.get(
        f"/api/runs/{started['run_id']}", headers={"X-Demo-Persona": "mina"}
    )

    assert recovered.status_code == 200
    assert recovered.json()["state"] == "waiting_for_decision"
    assert recovered.json()["waiting_on"] == ["confirm"]
    assert recovered.json()["audit"][0]["event_type"] == "workflow_run.started"


def test_all_nine_catalog_examples_complete_through_the_shared_runtime(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path, technical_spike=True)
    cases = (
        ("team-daily-rollup", "jiho", {}, (("confirm", "jiho"),)),
        ("weekly-report", "mina", {}, (("confirm", "mina"),)),
        ("monthly-close", "jiho", {}, (("confirm", "jiho"),)),
        ("meeting-followups", "mina", {}, (("choose-assignment", "mina"), ("accept-assignment", "mina"))),
        ("onboarding", "demo-admin", {}, (("confirm", "demo-admin"),)),
        ("offboarding", "demo-admin", {}, (("confirm", "demo-admin"),)),
        ("customer-visit-report", "mina", {}, (("confirm", "mina"),)),
        ("contract-review", "demo-admin", {"contract_id": "contract-19"}, (("legal", "sora"), ("finance", "minseok"))),
    )

    for workflow_id, starter, input_data, decisions in cases:
        started = client.post(
            f"/api/runs/{workflow_id}", headers={"X-Demo-Persona": starter}, json={"input": input_data}
        )
        assert started.status_code == 201, started.text
        run_id = started.json()["run_id"]
        result = started
        for node_id, actor in decisions:
            payload = {"assignee_id": "mina"} if node_id == "choose-assignment" else {}
            result = client.post(
                f"/api/runs/{run_id}/decisions/{node_id}",
                headers={"X-Demo-Persona": actor},
                json={"decision": "accept", "payload": payload},
            )
            assert result.status_code == 200, result.text
        assert result.json()["state"] == "completed"
        assert result.json()["tool_results"]
