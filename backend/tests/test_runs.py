from fastapi.testclient import TestClient

from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings


def _client_with_seeded_database(tmp_path) -> TestClient:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    return TestClient(create_app(Settings(RuntimeProfile.TEST, database_url)))


def test_starting_daily_report_persists_a_version_pinned_run_and_waits_for_human_confirmation(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path)

    response = client.post(
        "/api/runs/daily-report",
        headers={"X-Demo-Persona": "mina"},
        json={"input": {}},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["workflow_id"] == "daily-report"
    assert body["definition_version"] == "2026-09-demo.1"
    assert body["state"] == "waiting_for_decision"
    assert body["waiting_on"] == ["confirm"]
    assert body["tool_results"][0]["tool_name"] == "work_record.lookup"


def test_daily_report_submits_immutable_snapshot_only_after_human_acceptance(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path)
    started = client.post("/api/runs/daily-report", headers={"X-Demo-Persona": "mina"}, json={"input": {}}).json()

    assert "daily_report.submit_snapshot" not in {item["tool_name"] for item in started["tool_results"]}
    completed = client.post(
        f"/api/runs/{started['run_id']}/decisions/confirm",
        headers={"X-Demo-Persona": "mina"},
        json={"decision": "accept", "rationale": "내용을 확인했습니다."},
    )

    assert completed.status_code == 200
    body = completed.json()
    assert body["state"] == "completed"
    assert "daily_report.submit_snapshot" in {item["tool_name"] for item in body["tool_results"]}
    event_types = [item["event_type"] for item in body["audit"]]
    assert event_types[0] == "workflow_run.started"
    assert "human_decision.requested" in event_types
    assert "human_decision.accepted" in event_types
    assert event_types[-1] == "workflow_run.completed"


def test_self_created_task_enters_my_work_and_only_allows_valid_lifecycle_transitions(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path)

    created = client.post(
        "/api/tasks",
        headers={"X-Demo-Persona": "mina"},
        json={"title": "고객 피드백 정리"},
    )

    assert created.status_code == 201
    task = created.json()
    assert task["state"] == "active"
    assert client.get("/api/my-work", headers={"X-Demo-Persona": "mina"}).json()[0]["task_id"] == task["task_id"]
    assert client.post(f"/api/tasks/{task['task_id']}/complete", headers={"X-Demo-Persona": "mina"}).status_code == 422
    assert client.post(f"/api/tasks/{task['task_id']}/start", headers={"X-Demo-Persona": "mina"}).json()["state"] == "in_progress"
    assert client.post(
        f"/api/tasks/{task['task_id']}/block", headers={"X-Demo-Persona": "mina"}, json={"reason": "고객 자료 대기"}
    ).json()["state"] == "blocked"
    assert client.post(f"/api/tasks/{task['task_id']}/resume", headers={"X-Demo-Persona": "mina"}).json()["state"] == "in_progress"
    assert client.post(f"/api/tasks/{task['task_id']}/complete", headers={"X-Demo-Persona": "mina"}).json()["state"] == "completed"


def test_meeting_assignment_never_enters_my_work_before_the_selected_assignee_accepts(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path)
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
    client = _client_with_seeded_database(tmp_path)
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
    client = _client_with_seeded_database(tmp_path)

    response = client.get("/api/meeting-assignment-candidates", headers={"X-Demo-Persona": "mina"})

    assert response.status_code == 200
    assert response.json() == [
        {"id": "mina", "display_name": "민아 (구성원)"},
        {"id": "demo-admin", "display_name": "데모 관리자"},
    ]


def test_meeting_assignment_rejects_a_seeded_persona_without_task_acceptance_capability(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path)
    started = client.post("/api/runs/meeting-followups", headers={"X-Demo-Persona": "mina"}, json={"input": {}}).json()

    response = client.post(
        f"/api/runs/{started['run_id']}/decisions/choose-assignment",
        headers={"X-Demo-Persona": "mina"},
        json={"decision": "accept", "payload": {"assignee_id": "sora"}},
    )

    assert response.status_code == 422
    assert client.get("/api/my-work", headers={"X-Demo-Persona": "sora"}).json() == []


def test_only_the_selected_assignee_can_accept_an_assignment(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path)
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
    client = _client_with_seeded_database(tmp_path)
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
    client = _client_with_seeded_database(tmp_path)
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
    client = _client_with_seeded_database(tmp_path)
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
    reset_database(database_url)
    first_client = TestClient(create_app(Settings(RuntimeProfile.TEST, database_url)))
    started = first_client.post("/api/runs/daily-report", headers={"X-Demo-Persona": "mina"}, json={"input": {}}).json()

    restarted_client = TestClient(create_app(Settings(RuntimeProfile.TEST, database_url)))
    recovered = restarted_client.get(
        f"/api/runs/{started['run_id']}", headers={"X-Demo-Persona": "mina"}
    )

    assert recovered.status_code == 200
    assert recovered.json()["state"] == "waiting_for_decision"
    assert recovered.json()["waiting_on"] == ["confirm"]
    assert recovered.json()["audit"][0]["event_type"] == "workflow_run.started"


def test_all_nine_catalog_examples_complete_through_the_shared_runtime(tmp_path) -> None:
    client = _client_with_seeded_database(tmp_path)
    cases = (
        ("daily-report", "mina", {}, (("confirm", "mina"),)),
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
