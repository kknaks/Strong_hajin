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


def _pending(client, headers) -> list[dict]:
    return client.get("/api/action-items", headers=headers).json()


def _command(client, headers, action_item_id: str, command: str, **payload):
    return client.post(f"/api/action-items/{action_item_id}/commands/{command}", headers=headers, json=payload)


def test_commands_run_the_owning_module_operation_and_resolve_the_same_action_item(tmp_path) -> None:
    """One command endpoint, but the effect is always the owning module's own operation."""
    client, _ = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "승인될 요청", "assignee_id": "jiho"}).json()
    [item] = _pending(client, JIHO)

    accepted = _command(client, JIHO, item["action_item_id"], "accept", expected_version=item["expected_version"])
    assert accepted.status_code == 200, accepted.text
    # The WorkRequest module produced the effect: the assignee now holds a real Task.
    assert accepted.json()["status"] == "resolved"
    current = client.get("/api/work-requests", headers=MINA).json()
    [stored] = [row for row in current if row["request_id"] == request["request_id"]]
    assert stored["state"] == "accepted"
    assert [task["title"] for task in client.get("/api/my-work", headers=JIHO).json()] == ["승인될 요청"]
    # Answered questions leave the pending ledger for everyone.
    assert _pending(client, JIHO) == [] and _pending(client, MINA) == []


def test_an_adjustment_round_keeps_one_action_item_and_immutable_earlier_rounds(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    request = client.post(
        "/api/work-requests",
        headers=MINA,
        json={"title": "조정될 요청", "assignee_id": "jiho", "description": "처음 설명"},
    ).json()
    [first] = _pending(client, JIHO)

    # The reviewer must say why; an adjustment without a reason is refused.
    assert _command(client, JIHO, first["action_item_id"], "adjust", expected_version=first["expected_version"]).status_code == 422
    adjusted = _command(client, JIHO, first["action_item_id"], "adjust", expected_version=first["expected_version"], reason="기한을 늦춰 주세요")
    assert adjusted.status_code == 200, adjusted.text

    [waiting] = _pending(client, MINA)
    assert waiting["action_item_id"] == first["action_item_id"] and waiting["status"] == "awaiting_revision"

    # A revision that changes nothing is not a round.
    unchanged = _command(client, MINA, waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], changes={})
    assert unchanged.status_code == 422, unchanged.text

    revised = _command(
        client,
        MINA,
        waiting["action_item_id"],
        "revise",
        expected_version=waiting["expected_version"],
        changes={"title": "조정 반영한 요청", "description": "고친 설명"},
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["status"] == "awaiting_review" and revised.json()["submission_version"] == 2

    # The question came back to the reviewer as the same item, one round later.
    [second] = _pending(client, JIHO)
    assert second["action_item_id"] == first["action_item_id"] and second["submission_version"] == 2
    assert second["subject"] == "조정 반영한 요청"
    assert _pending(client, MINA) == []

    # Every earlier round survives with its own frozen content, decision and diff.
    detail = client.get(f"/api/action-items/{first['action_item_id']}", headers=MINA)
    assert detail.status_code == 200, detail.text
    rounds = detail.json()["rounds"]
    assert [row["submission_version"] for row in rounds] == [1, 2]
    assert rounds[0]["snapshot"]["title"] == "조정될 요청" and rounds[0]["snapshot"]["description"] == "처음 설명"
    assert rounds[0]["content_hash"] != rounds[1]["content_hash"]
    assert [decision["decision"] for decision in rounds[0]["decisions"]] == ["negotiate"]
    assert rounds[0]["decisions"][0]["reason"] == "기한을 늦춰 주세요"
    assert rounds[0]["decisions"][0]["actor_member_id"] == "jiho"
    assert rounds[1]["diff"]["title"] == {"before": "조정될 요청", "after": "조정 반영한 요청"}
    assert rounds[1]["decisions"] == []


def test_only_the_principal_the_item_waits_on_may_run_its_commands(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    client.post("/api/work-requests", headers=MINA, json={"title": "권한 확인 요청", "assignee_id": "jiho"}).json()
    [item] = _pending(client, JIHO)

    # The requester cannot answer their own request, and cannot revise before an adjustment was asked for.
    assert _command(client, MINA, item["action_item_id"], "accept", expected_version=item["expected_version"]).status_code in {403, 422}
    assert _command(client, MINA, item["action_item_id"], "revise", expected_version=item["expected_version"], changes={"title": "몰래 수정"}).status_code in {403, 422}
    # A command that is not offered on this item is refused rather than guessed at.
    assert _command(client, JIHO, item["action_item_id"], "withdraw", expected_version=item["expected_version"]).status_code == 422
    assert [row["title"] for row in client.get("/api/work-requests", headers=MINA).json()] == ["권한 확인 요청"]


def test_the_requester_can_withdraw_an_adjusted_request_and_it_leaves_every_ledger(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    client.post("/api/work-requests", headers=MINA, json={"title": "철회할 요청", "assignee_id": "jiho"}).json()
    [item] = _pending(client, JIHO)
    _command(client, JIHO, item["action_item_id"], "adjust", expected_version=item["expected_version"], reason="다시 생각해 주세요")
    [waiting] = _pending(client, MINA)

    withdrawn = _command(client, MINA, waiting["action_item_id"], "withdraw", expected_version=waiting["expected_version"])
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["status"] == "resolved"
    assert _pending(client, MINA) == [] and _pending(client, JIHO) == []
    [stored] = client.get("/api/work-requests", headers=MINA).json()
    assert stored["state"] == "withdrawn" and stored["task_id"] is None
    assert client.get("/api/my-work", headers=JIHO).json() == []


def test_an_ax_proposal_runs_its_effect_exactly_once_through_the_same_command_path(tmp_path) -> None:
    client, application = _stack(tmp_path)
    proposal = _ax_proposal(client, application, JIHO, "jiho", "task.create_self", "업무 생성 확인", {"title": "AX가 만든 업무"})
    [item] = [row for row in _pending(client, JIHO) if row["kind"] == "ax.task.create_self"]

    approved = _command(client, JIHO, item["action_item_id"], "approve", expected_version=item["expected_version"])
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "resolved"
    assert [task["title"] for task in client.get("/api/my-work", headers=JIHO).json()] == ["AX가 만든 업무"]

    # A retried command is an idempotent receipt, never a second Task.
    again = _command(client, JIHO, item["action_item_id"], "approve", expected_version=item["expected_version"])
    assert again.status_code == 200, again.text
    assert [task["title"] for task in client.get("/api/my-work", headers=JIHO).json()] == ["AX가 만든 업무"]
    assert client.get("/api/actions", headers=JIHO).json()[0]["state"] == "approved"
    assert proposal["action_id"] == item["action_item_id"]


def test_a_direct_assignment_is_the_same_kind_of_question_in_the_same_ledger(tmp_path) -> None:
    client, application = _stack(tmp_path)
    jiho = application.authenticated_principal("jiho")
    assigned = application.assign_task(jiho, "배정된 업무", "mina", description="맡아 주세요")

    [item] = [row for row in _pending(client, MINA) if row["kind"] == "task.assignment"]
    assert item["subject"] == "배정된 업무" and item["operation_label"] == "업무 배정"
    assert item["current_question"] == "이 업무 배정을 수락할지 결정하세요"
    assert [command["id"] for command in item["allowed_commands"]] == ["accept", "decline"]
    assert [command["requires_reason"] for command in item["allowed_commands"]] == [False, True]
    assert {row["label"] for row in item["preview"]} >= {"설명", "배정자"}
    assert item["waiting_on"] == {"member_id": "mina", "display_name": "민아 (구성원)"}
    # The assigner is not the one who owes an answer.
    assert [row for row in _pending(client, JIHO) if row["kind"] == "task.assignment"] == []

    accepted = _command(client, MINA, item["action_item_id"], "accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "resolved"
    assert [task["title"] for task in client.get("/api/my-work", headers=MINA).json()] == ["배정된 업무"]
    assert [row for row in _pending(client, MINA) if row["kind"] == "task.assignment"] == []
    assert assigned["task"]["title"] == "배정된 업무"

    # A re-sent accept is a receipt, and a declined-after-accepted command is refused rather than guessed at.
    assert _command(client, MINA, item["action_item_id"], "accept").status_code == 200
    assert _command(client, MINA, item["action_item_id"], "decline", reason="역시 어렵습니다").status_code == 422
    assert len(client.get("/api/my-work", headers=MINA).json()) == 1


def test_an_adjustment_carries_an_optional_structured_change_proposal_the_requester_answers(tmp_path) -> None:
    """조정 요청은 필수 사유 위에 '무엇을 이렇게 바꿔 달라'는 구조화 제안을 남길 수 있다."""
    client, _ = _stack(tmp_path)
    client.post(
        "/api/work-requests",
        headers=MINA,
        json={"title": "제안이 붙는 요청", "assignee_id": "jiho", "description": "처음 설명", "due_date": "2026-09-10"},
    )
    [first] = _pending(client, JIHO)

    adjusted = _command(
        client,
        JIHO,
        first["action_item_id"],
        "adjust",
        expected_version=first["expected_version"],
        reason="기한과 제목을 함께 손봐 주세요",
        changes={"title": "제안대로 고친 요청", "due_date": "2026-09-30"},
    )
    assert adjusted.status_code == 200, adjusted.text

    # The requester meets the proposal as a field-level ask, not as prose buried in the reason.
    [waiting] = _pending(client, MINA)
    assert waiting["suggested_changes"] == {"title": "제안대로 고친 요청", "due_date": "2026-09-30"}
    detail = client.get(f"/api/action-items/{waiting['action_item_id']}", headers=MINA).json()
    assert detail["suggested_changes"] == {"title": "제안대로 고친 요청", "due_date": "2026-09-30"}
    # It is a proposal, not an edit: the current round still holds what the requester actually submitted.
    assert detail["rounds"][0]["snapshot"]["title"] == "제안이 붙는 요청"
    assert detail["rounds"][0]["decisions"][0]["suggested_changes"] == {"title": "제안대로 고친 요청", "due_date": "2026-09-30"}
    assert detail["rounds"][0]["decisions"][0]["reason"] == "기한과 제목을 함께 손봐 주세요"

    # Applying it is the requester's own revision, and it clears the ask.
    revised = _command(
        client,
        MINA,
        waiting["action_item_id"],
        "revise",
        expected_version=waiting["expected_version"],
        changes={"title": "제안대로 고친 요청", "due_date": "2026-09-30"},
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["suggested_changes"] == {}
    [second] = _pending(client, JIHO)
    assert second["subject"] == "제안대로 고친 요청" and second["suggested_changes"] == {}

    # A reason-only adjustment stays legal and simply carries no proposal.
    plain = _command(client, JIHO, second["action_item_id"], "adjust", expected_version=second["expected_version"], reason="설명만 더 주세요")
    assert plain.status_code == 200, plain.text
    assert _pending(client, MINA)[0]["suggested_changes"] == {}


def test_a_resent_command_returns_the_same_receipt_instead_of_a_second_effect(tmp_path) -> None:
    """A lost response must not force the caller to choose between a duplicate Task and a stale error."""
    client, _ = _stack(tmp_path)
    client.post("/api/work-requests", headers=MINA, json={"title": "한 번만 수락될 요청", "assignee_id": "jiho"})
    [item] = _pending(client, JIHO)

    first = _command(client, JIHO, item["action_item_id"], "accept", expected_version=item["expected_version"])
    assert first.status_code == 200, first.text
    resent = _command(client, JIHO, item["action_item_id"], "accept", expected_version=item["expected_version"])
    assert resent.status_code == 200, resent.text
    assert resent.json()["status"] == "resolved" and resent.json()["action_item_id"] == item["action_item_id"]
    assert [task["title"] for task in client.get("/api/my-work", headers=JIHO).json()] == ["한 번만 수락될 요청"]

    # Only the decision that was actually made replays; a different answer to a settled question is still refused.
    assert _command(client, JIHO, item["action_item_id"], "reject", expected_version=item["expected_version"], reason="역시 아니오").status_code == 422
    detail = client.get(f"/api/action-items/{item['action_item_id']}", headers=JIHO).json()
    assert [decision["decision"] for decision in detail["rounds"][0]["decisions"]] == ["accept"]


def test_the_discussion_stays_on_one_action_item_across_rounds_and_never_moves_it(tmp_path) -> None:
    """댓글과 전체 논의는 ActionItem에 유지되고 상태를 직접 바꾸지 않는다."""
    client, _ = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "논의가 붙는 요청", "assignee_id": "jiho"}).json()
    asked = client.post(f"/api/work-requests/{request['request_id']}/comments", headers=JIHO, json={"body": "예산 근거가 있나요?"})
    assert asked.status_code == 201, asked.text
    [item] = _pending(client, JIHO)

    # Commenting is not a judgement: the question is still open and still Jiho's.
    assert _pending(client, JIHO)[0]["status"] == "awaiting_review"
    discussion = client.get(f"/api/action-items/{item['action_item_id']}", headers=JIHO).json()["discussion"]
    assert [row["body"] for row in discussion] == ["예산 근거가 있나요?"]

    _command(client, JIHO, item["action_item_id"], "adjust", expected_version=item["expected_version"], reason="근거를 붙여 주세요")
    [waiting] = _pending(client, MINA)
    answered = client.post(f"/api/work-requests/{request['request_id']}/comments", headers=MINA, json={"body": "9월 견적서를 붙였습니다"})
    assert answered.status_code == 201, answered.text
    _command(client, MINA, waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], changes={"description": "근거 추가"})

    # The same thread, with the same comment identities, survives the round it was written before.
    after = client.get(f"/api/action-items/{item['action_item_id']}", headers=JIHO).json()
    assert [row["comment_id"] for row in after["discussion"]] == [asked.json()["comment_id"], answered.json()["comment_id"]]
    assert [row["author_member_id"] for row in after["discussion"]] == ["jiho", "mina"]
    assert len(after["rounds"]) == 2


def test_the_detail_offers_commands_only_to_the_principal_the_item_waits_on(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    client.post("/api/work-requests", headers=MINA, json={"title": "차례가 있는 요청", "assignee_id": "jiho"})
    [item] = _pending(client, JIHO)

    # Both participants read the same question; only the one whose turn it is is offered a way to answer it.
    mine = client.get(f"/api/action-items/{item['action_item_id']}", headers=JIHO).json()
    theirs = client.get(f"/api/action-items/{item['action_item_id']}", headers=MINA).json()
    assert [command["id"] for command in mine["allowed_commands"]] == ["accept", "adjust", "reject"]
    assert theirs["allowed_commands"] == [] and theirs["waiting_on"]["member_id"] == "jiho"
    assert theirs["subject"] == mine["subject"] and theirs["status"] == mine["status"]
