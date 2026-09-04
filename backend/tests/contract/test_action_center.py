"""One judgement ledger.

Every path that needs a person's decision — a WorkRequest, a direct TaskAssignment, an AX gated proposal — is one
canonical ActionItem in one query, with one envelope shape. What differs between kinds is the policy the server
applies: which commands the current principal may run, and what the question in front of them says. The client reads
that projection; it never derives a command, a field, or a permission from the kind.
"""
from uuid import UUID

from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import ConversationTurnRecord, make_session_factory

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    app = create_app(settings)
    return TestClient(app), app.state.workflow_application


def _ax_proposal(client, application, headers, persona: str, action_type: str, title: str, payload: dict) -> dict:
    """A gated AX proposal, created the way a delegated turn creates one."""
    conversation = client.post("/api/conversations", headers=headers, json={"title": "판단 통합"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**headers, "Idempotency-Key": f"action-center-{action_type}-{persona}"},
        json={"body": "제안해줘", "context": []},
    )
    assert accepted.status_code == 202, accepted.text
    with make_session_factory(application._settings.database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"])).execution_id
    return application.propose_action(application.authenticated_principal(persona), execution_id, action_type, title, payload)


def test_one_query_returns_every_kind_of_pending_judgement_with_its_own_commands(tmp_path) -> None:
    client, application = _stack(tmp_path)

    # Two different origins, one ledger: a request Jiho must answer, and Jiho's own AX proposal awaiting approval.
    request = client.post(
        "/api/work-requests",
        headers=MINA,
        json={"title": "견적 재검토", "assignee_id": "jiho", "description": "9월 견적 재검토"},
    )
    assert request.status_code == 201, request.text
    request = request.json()
    proposal = _ax_proposal(client, application, JIHO, "jiho", "task.create_self", "업무 생성 확인", {"title": "AX가 만든 업무"})

    pending = client.get("/api/action-items", headers=JIHO)
    assert pending.status_code == 200, pending.text
    items = {item["kind"]: item for item in pending.json()}
    assert set(items) == {"work_request.acceptance", "ax.task.create_self"}

    # One envelope shape for both, carrying the server's own presentation.
    for item in items.values():
        assert set(item) >= {
            "action_item_id",
            "kind",
            "status",
            "subject",
            "operation_label",
            "preview",
            "current_question",
            "allowed_commands",
            "submission_version",
            "waiting_on",
            "resource",
        }
        assert item["status"] == "awaiting_review"
        assert item["waiting_on"] == {"member_id": "jiho", "display_name": "지호 (팀장)"}
        assert isinstance(item["preview"], list)

    review = items["work_request.acceptance"]
    assert review["subject"] == "견적 재검토" and review["operation_label"] == "업무 요청"
    assert [command["id"] for command in review["allowed_commands"]] == ["accept", "adjust", "reject"]
    assert {row["label"] for row in review["preview"]} >= {"요청자", "설명"}
    assert review["resource"] == {"type": "work_request", "id": request["request_id"]}
    # A command that needs the actor to say why says so; the client does not decide that.
    assert [command["id"] for command in review["allowed_commands"] if command["requires_reason"]] == ["adjust", "reject"]

    proposal_item = items["ax.task.create_self"]
    assert proposal_item["subject"] == "AX가 만든 업무" and proposal_item["operation_label"] == "업무 생성"
    assert [command["id"] for command in proposal_item["allowed_commands"]] == ["approve", "reject"]
    assert proposal_item["resource"] == {"type": "action", "id": proposal["action_id"]}

    # The same ledger is principal-scoped: Mina owes nothing here, she is waiting on Jiho.
    assert client.get("/api/action-items", headers=MINA).json() == []


def test_pending_holds_only_what_this_principal_must_answer_now(tmp_path) -> None:
    client, application = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "검토 요청", "assignee_id": "jiho"}).json()

    # Before anyone answers, the reviewer owes the decision and the requester owes nothing.
    [before] = client.get("/api/action-items", headers=JIHO).json()
    assert before["kind"] == "work_request.acceptance" and before["submission_version"] == 1
    assert client.get("/api/action-items", headers=MINA).json() == []

    # After the reviewer asks for a change, the same question moves to the requester; it never becomes a second item.
    adjusted = client.post(
        f"/api/work-requests/{request['request_id']}/negotiate",
        headers=JIHO,
        json={"expected_version": request["version"], "conditions": {"note": "기한을 늦춰 주세요"}},
    )
    assert adjusted.status_code == 200, adjusted.text
    assert client.get("/api/action-items", headers=JIHO).json() == []
    [waiting] = client.get("/api/action-items", headers=MINA).json()
    assert waiting["status"] == "awaiting_revision"
    assert waiting["waiting_on"] == {"member_id": "mina", "display_name": "민아 (구성원)"}
    assert waiting["current_question"] == "조정 요청에 답해 수정안을 다시 보낼지 결정하세요"
    assert [command["id"] for command in waiting["allowed_commands"]] == ["revise", "withdraw"]
    # Same judgement question, same identity across the round trip: an adjustment is never a second ActionItem.
    assert waiting["action_item_id"] == before["action_item_id"]
    assert waiting["submission_version"] == 1
