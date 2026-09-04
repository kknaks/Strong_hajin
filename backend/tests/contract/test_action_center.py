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

    accepted = _command(client, MINA, item["action_item_id"], "accept", expected_version=item["expected_version"])
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "resolved"
    assert [task["title"] for task in client.get("/api/my-work", headers=MINA).json()] == ["배정된 업무"]
    assert [row for row in _pending(client, MINA) if row["kind"] == "task.assignment"] == []
    assert assigned["task"]["title"] == "배정된 업무"

    # A re-sent accept is a receipt, and a declined-after-accepted command is refused rather than guessed at.
    assert _command(client, MINA, item["action_item_id"], "accept", expected_version=item["expected_version"]).status_code == 200
    assert _command(client, MINA, item["action_item_id"], "decline", expected_version=item["expected_version"], reason="역시 어렵습니다").status_code == 422
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


def _rounds(client, headers, action_item_id: str) -> list[dict]:
    return client.get(f"/api/action-items/{action_item_id}", headers=headers).json()["rounds"]


def test_only_the_answer_just_given_replays_and_a_stale_resend_is_refused(tmp_path) -> None:
    """A receipt must prove it is *this* answer coming back, not merely an answer of the same shape."""
    client, _ = _stack(tmp_path)
    client.post("/api/work-requests", headers=MINA, json={"title": "두 번 조정될 요청", "assignee_id": "jiho", "description": "처음 설명"})
    [round_one] = _pending(client, JIHO)
    item_id = round_one["action_item_id"]

    first_adjust = {"expected_version": round_one["expected_version"], "reason": "1회차 사유", "changes": {"title": "1회차 제안"}}
    assert _command(client, JIHO, item_id, "adjust", **first_adjust).status_code == 200
    [awaiting] = _pending(client, MINA)
    first_revise = {"expected_version": awaiting["expected_version"], "changes": {"title": "1회차 수정"}}
    assert _command(client, MINA, item_id, "revise", **first_revise).status_code == 200

    [round_two] = _pending(client, JIHO)
    second_adjust = {"expected_version": round_two["expected_version"], "reason": "2회차 사유", "changes": {"title": "2회차 제안"}}
    assert _command(client, JIHO, item_id, "adjust", **second_adjust).status_code == 200
    [awaiting_again] = _pending(client, MINA)
    second_revise = {"expected_version": awaiting_again["expected_version"], "changes": {"title": "2회차 수정"}}
    assert _command(client, MINA, item_id, "revise", **second_revise).status_code == 200

    before = _rounds(client, MINA, item_id)
    assert [row["submission_version"] for row in before] == [1, 2, 3]

    # A round-one command arriving late carries a version and a payload that no longer describe anything current.
    assert _command(client, MINA, item_id, "revise", **first_revise).status_code == 422
    assert _command(client, JIHO, item_id, "adjust", **first_adjust).status_code == 422
    # Even the right version with the wrong content is not this principal's answer.
    assert _command(client, MINA, item_id, "revise", expected_version=awaiting_again["expected_version"], changes={"title": "보내지 않은 수정"}).status_code == 422
    assert _command(client, JIHO, item_id, "adjust", expected_version=round_two["expected_version"], reason="보내지 않은 사유").status_code == 422

    # Only the answer that was actually just given comes back as a receipt.
    replayed = _command(client, MINA, item_id, "revise", **second_revise)
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["action_item_id"] == item_id and replayed.json()["submission_version"] == 3

    after = _rounds(client, MINA, item_id)
    assert [row["submission_version"] for row in after] == [1, 2, 3]
    assert [len(row["decisions"]) for row in after] == [len(row["decisions"]) for row in before]
    assert [row["content_hash"] for row in after] == [row["content_hash"] for row in before]

    # Accepting settles it, and that acceptance replays without producing a second Task.
    [final] = _pending(client, JIHO)
    accept = {"expected_version": final["expected_version"]}
    assert _command(client, JIHO, item_id, "accept", **accept).status_code == 200
    assert _command(client, JIHO, item_id, "accept", **accept).status_code == 200
    assert len(client.get("/api/my-work", headers=JIHO).json()) == 1
    # A stale acceptance, against the version the request had before it was accepted, is not a receipt either.
    assert _command(client, JIHO, item_id, "accept", expected_version=final["expected_version"] - 1).status_code == 422


def test_a_revision_that_changes_nothing_is_refused_however_it_is_written(tmp_path) -> None:
    """The UI disables the button; the server is what makes an empty round impossible."""
    client, _ = _stack(tmp_path)
    client.post(
        "/api/work-requests",
        headers=MINA,
        json={"title": "그대로인 요청", "assignee_id": "jiho", "description": "처음 설명", "due_date": "2026-09-10"},
    )
    [item] = _pending(client, JIHO)
    _command(client, JIHO, item["action_item_id"], "adjust", expected_version=item["expected_version"], reason="다시 봐 주세요")
    [waiting] = _pending(client, MINA)

    # Every field repeated at its current value is as empty a revision as sending no fields at all.
    for changes in ({}, {"title": "그대로인 요청"}, {"title": "그대로인 요청", "description": "처음 설명", "due_date": "2026-09-10"}):
        refused = _command(client, MINA, waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], changes=changes)
        assert refused.status_code == 422, f"{changes} was accepted: {refused.text}"

    # The same question is still waiting on the same round, untouched.
    [still] = _pending(client, MINA)
    assert still["action_item_id"] == waiting["action_item_id"] and still["expected_version"] == waiting["expected_version"]
    assert [row["submission_version"] for row in _rounds(client, MINA, waiting["action_item_id"])] == [1]

    # The REST route the request module owns refuses it on the same grounds, not just the command path.
    request_id = still["resource"]["id"]
    direct = client.post(
        f"/api/work-requests/{request_id}/resubmit",
        headers=MINA,
        json={"expected_version": still["expected_version"], "title": "그대로인 요청"},
    )
    assert direct.status_code == 422, direct.text
    assert [row["submission_version"] for row in _rounds(client, MINA, waiting["action_item_id"])] == [1]

    # A real change still goes through and is the only thing that makes a new round.
    accepted = _command(client, MINA, waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], changes={"title": "정말 바뀐 요청"})
    assert accepted.status_code == 200, accepted.text
    assert [row["submission_version"] for row in _rounds(client, MINA, waiting["action_item_id"])] == [1, 2]


def test_every_command_must_name_the_version_it_is_answering(tmp_path) -> None:
    """Optimistic concurrency is the contract, not an option: a command without a version is refused."""
    client, _ = _stack(tmp_path)
    client.post("/api/work-requests", headers=MINA, json={"title": "버전 계약", "assignee_id": "jiho"})
    [item] = _pending(client, JIHO)

    missing = client.post(f"/api/action-items/{item['action_item_id']}/commands/accept", headers=JIHO, json={})
    assert missing.status_code == 422, missing.text
    assert _command(client, JIHO, item["action_item_id"], "accept", expected_version=item["expected_version"] - 1).status_code == 422
    assert _command(client, JIHO, item["action_item_id"], "accept", expected_version=item["expected_version"] + 1).status_code == 422
    assert client.get("/api/my-work", headers=JIHO).json() == []
    assert _command(client, JIHO, item["action_item_id"], "accept", expected_version=item["expected_version"]).status_code == 200


def test_a_revision_may_only_change_the_fields_a_revision_owns(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    client.post("/api/work-requests", headers=MINA, json={"title": "허용 필드", "assignee_id": "jiho"})
    [item] = _pending(client, JIHO)
    _command(client, JIHO, item["action_item_id"], "adjust", expected_version=item["expected_version"], reason="고쳐 주세요")
    [waiting] = _pending(client, MINA)

    # A field a revision does not own is refused rather than quietly dropped from a successful-looking answer.
    refused = _command(
        client, MINA, waiting["action_item_id"], "revise",
        expected_version=waiting["expected_version"], changes={"title": "고친 제목", "assignee_id": "sora"},
    )
    assert refused.status_code == 422, refused.text
    assert "assignee_id" in refused.text
    assert [row["submission_version"] for row in _rounds(client, MINA, waiting["action_item_id"])] == [1]
    # The fields it does own still work, clear_due_date included.
    accepted = _command(
        client, MINA, waiting["action_item_id"], "revise",
        expected_version=waiting["expected_version"], changes={"title": "고친 제목", "clear_due_date": True},
    )
    assert accepted.status_code == 200, accepted.text


def test_a_direct_assignment_answers_on_the_task_version_it_was_shown(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    client.post("/api/tasks/assign", headers=JIHO, json={"title": "배정 A", "assignee_id": "mina"})
    client.post("/api/tasks/assign", headers=JIHO, json={"title": "배정 B", "assignee_id": "mina"})
    items = {row["subject"]: row for row in _pending(client, MINA)}
    accept_target, decline_target = items["배정 A"], items["배정 B"]

    assert _command(client, MINA, accept_target["action_item_id"], "accept").status_code == 422
    assert _command(client, MINA, accept_target["action_item_id"], "accept", expected_version=accept_target["expected_version"] + 1).status_code == 422
    assert _command(client, MINA, accept_target["action_item_id"], "accept", expected_version=accept_target["expected_version"]).status_code == 200
    # Acceptance leaves the Task version alone, so the same call replays as a receipt.
    replay = _command(client, MINA, accept_target["action_item_id"], "accept", expected_version=accept_target["expected_version"])
    assert replay.status_code == 200 and replay.json()["status"] == "resolved"

    # A decline cancels the Task, which moves it one version on; only that exact relation replays.
    assert _command(client, MINA, decline_target["action_item_id"], "decline", expected_version=decline_target["expected_version"], reason="여력 없음").status_code == 200
    receipt = _command(client, MINA, decline_target["action_item_id"], "decline", expected_version=decline_target["expected_version"], reason="여력 없음")
    assert receipt.status_code == 200 and receipt.json()["status"] == "resolved"
    assert _command(client, MINA, decline_target["action_item_id"], "decline", expected_version=decline_target["expected_version"], reason="다른 사유").status_code == 422
    assert _command(client, MINA, decline_target["action_item_id"], "decline", expected_version=decline_target["expected_version"] + 1, reason="여력 없음").status_code == 422
    assert [task["title"] for task in client.get("/api/my-work", headers=MINA).json()] == ["배정 A"]


def test_an_ax_proposal_answers_on_the_version_it_was_shown(tmp_path) -> None:
    client, application = _stack(tmp_path)
    proposal = _ax_proposal(client, application, MINA, "mina", "task.create_self", "업무 생성 확인", {"title": "AX 업무"})
    [item] = _pending(client, MINA)
    assert item["action_item_id"] == proposal["action_id"]

    assert _command(client, MINA, item["action_item_id"], "approve").status_code == 422
    assert _command(client, MINA, item["action_item_id"], "approve", expected_version=item["expected_version"] + 1).status_code == 422
    assert client.get("/api/my-work", headers=MINA).json() == []
    assert _command(client, MINA, item["action_item_id"], "approve", expected_version=item["expected_version"]).status_code == 200
    replay = _command(client, MINA, item["action_item_id"], "approve", expected_version=item["expected_version"])
    assert replay.status_code == 200 and replay.json()["status"] == "resolved"
    # The version the effect actually consumed is the only one that replays.
    assert _command(client, MINA, item["action_item_id"], "approve", expected_version=item["expected_version"] + 1).status_code == 422
    assert [task["title"] for task in client.get("/api/my-work", headers=MINA).json()] == ["AX 업무"]


def test_a_legacy_negotiate_cannot_leave_the_ledger_unreadable(tmp_path) -> None:
    """The compatibility endpoint writes what the canonical reader can read, or it does not write at all."""
    client, _ = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "원장 보호", "assignee_id": "jiho"}).json()

    poisoned = client.post(
        f"/api/work-requests/{request['request_id']}/negotiate",
        headers=JIHO,
        json={"expected_version": request["version"], "conditions": {"note": "조정", "changes": {"assignee_id": "sora", "priority": "high"}}},
    )
    assert poisoned.status_code == 422, poisoned.text

    # Both sides can still read their judgement ledger, and the request was not moved.
    assert client.get("/api/action-items", headers=MINA).status_code == 200
    assert client.get("/api/action-items", headers=JIHO).status_code == 200
    assert client.get("/api/work-requests", headers=MINA).json()[0]["state"] == "pending"

    # A proposal the canonical path accepts goes through the legacy endpoint too, and stays readable.
    fine = client.post(
        f"/api/work-requests/{request['request_id']}/negotiate",
        headers=JIHO,
        json={"expected_version": request["version"], "conditions": {"note": "조정", "changes": {"due_date": "2026-12-01"}}},
    )
    assert fine.status_code == 200, fine.text
    [waiting] = _pending(client, MINA)
    assert waiting["suggested_changes"] == {"due_date": "2026-12-01"}


def test_an_adjustment_may_only_propose_the_fields_an_adjustment_owns(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    client.post("/api/work-requests", headers=MINA, json={"title": "제안 필드", "assignee_id": "jiho"})
    [item] = _pending(client, JIHO)

    refused = _command(
        client, JIHO, item["action_item_id"], "adjust",
        expected_version=item["expected_version"], reason="담당을 바꿔 주세요", changes={"assignee_id": "sora"},
    )
    assert refused.status_code == 422 and "assignee_id" in refused.text
    # Nothing moved: the question is still the reviewer's, on the same round, with no decision recorded.
    [again] = _pending(client, JIHO)
    assert again["expected_version"] == item["expected_version"]
    assert _rounds(client, JIHO, item["action_item_id"])[0]["decisions"] == []
    assert client.get("/api/action-items", headers=MINA).status_code == 200


def test_an_ax_approval_replays_only_on_the_version_it_consumed(tmp_path) -> None:
    """The Action's own approval door holds the same exact-version contract as the judgement it gates."""
    client, application = _stack(tmp_path)
    proposal = _ax_proposal(client, application, MINA, "mina", "task.create_self", "업무 생성 확인", {"title": "AX 업무"})
    decided = client.post(
        f"/api/actions/{proposal['action_id']}/decide", headers=MINA,
        json={"expected_version": proposal["version"], "decision": "approve"},
    )
    assert decided.status_code == 200, decided.text

    receipt = client.post(
        f"/api/actions/{proposal['action_id']}/decide", headers=MINA,
        json={"expected_version": proposal["version"], "decision": "approve"},
    )
    assert receipt.status_code == 200 and receipt.json()["state"] == "approved"
    # A version this decision never consumed is stale, even though the outcome happens to match.
    for version in (proposal["version"] + 1, proposal["version"] + 2, proposal["version"] - 1):
        stale = client.post(
            f"/api/actions/{proposal['action_id']}/decide", headers=MINA,
            json={"expected_version": version, "decision": "approve"},
        )
        assert stale.status_code == 422, f"{version}: {stale.text}"
    assert len(client.get("/api/my-work", headers=MINA).json()) == 1


def test_a_revision_can_clear_the_description_by_saying_it_is_empty(tmp_path) -> None:
    """An explicitly empty description is a change — the requester removing it — not an absent field."""
    client, _ = _stack(tmp_path)
    client.post(
        "/api/work-requests",
        headers=MINA,
        json={"title": "설명 지우기", "assignee_id": "jiho", "description": "지워질 설명", "due_date": "2026-09-30"},
    )
    [item] = _pending(client, JIHO)
    _command(client, JIHO, item["action_item_id"], "adjust", expected_version=item["expected_version"], reason="설명을 빼 주세요")
    [waiting] = _pending(client, MINA)

    cleared = _command(
        client, MINA, waiting["action_item_id"], "revise",
        expected_version=waiting["expected_version"], changes={"description": ""},
    )
    assert cleared.status_code == 200, cleared.text
    rounds = _rounds(client, MINA, waiting["action_item_id"])
    assert [row["submission_version"] for row in rounds] == [1, 2]
    assert rounds[1]["snapshot"]["description"] is None
    assert rounds[1]["snapshot"]["title"] == "설명 지우기" and rounds[1]["snapshot"]["due_date"] == "2026-09-30"
    assert rounds[1]["diff"]["description"] == {"before": "지워질 설명", "after": None}


def test_an_empty_title_is_refused_and_a_date_is_only_removed_by_asking_to_remove_it(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    client.post(
        "/api/work-requests",
        headers=MINA,
        json={"title": "필드 계약", "assignee_id": "jiho", "description": "설명", "due_date": "2026-09-30"},
    )
    [item] = _pending(client, JIHO)
    _command(client, JIHO, item["action_item_id"], "adjust", expected_version=item["expected_version"], reason="고쳐 주세요")
    [waiting] = _pending(client, MINA)
    revise = lambda changes: _command(
        client, MINA, waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], changes=changes
    )

    # A request must keep a title, and an empty date is not how a date is removed.
    empty_title = revise({"title": ""})
    assert empty_title.status_code == 422 and "제목" in empty_title.text
    assert revise({"title": "   "}).status_code == 422
    empty_date = revise({"due_date": ""})
    assert empty_date.status_code == 422 and "기한" in empty_date.text
    # Naming the date and leaving it empty stays an error even when the revision does change something else, so it is
    # never quietly dropped from an answer that otherwise succeeded.
    beside_a_real_change = revise({"title": "정말 바뀐 제목", "due_date": ""})
    assert beside_a_real_change.status_code == 422 and "기한" in beside_a_real_change.text
    assert revise({"title": "정말 바뀐 제목", "due_date": None}).status_code == 422
    assert [row["submission_version"] for row in _rounds(client, MINA, waiting["action_item_id"])] == [1]

    removed = revise({"clear_due_date": True})
    assert removed.status_code == 200, removed.text
    rounds = _rounds(client, MINA, waiting["action_item_id"])
    assert rounds[1]["snapshot"]["due_date"] is None and rounds[1]["snapshot"]["description"] == "설명"


def test_a_receipt_survives_the_basis_moving_underneath_it(tmp_path) -> None:
    """A revision's receipt is pinned by the round it produced, not by a request version anyone may move."""
    client, _ = _stack(tmp_path)
    request = client.post(
        "/api/work-requests", headers=MINA, json={"title": "영수증 유지", "assignee_id": "jiho", "description": "처음"}
    ).json()
    [item] = _pending(client, JIHO)
    _command(client, JIHO, item["action_item_id"], "adjust", expected_version=item["expected_version"], reason="고쳐 주세요")
    [waiting] = _pending(client, MINA)

    revision = {"expected_version": waiting["expected_version"], "changes": {"title": "고친 요청"}}
    assert _command(client, MINA, waiting["action_item_id"], "revise", **revision).status_code == 200
    assert _command(client, MINA, waiting["action_item_id"], "revise", **revision).status_code == 200

    # Someone adopts evidence on the new round, moving the request on.
    adopted = client.post(
        f"/api/work-requests/{request['request_id']}/evidence", headers=MINA, files={"file": ("근거.txt", b"one", "text/plain")}
    )
    assert adopted.status_code == 201 and adopted.json()["request_version"] > waiting["expected_version"]

    # The lost-response retry is still the same answer, not a stale error.
    replayed = _command(client, MINA, waiting["action_item_id"], "revise", **revision)
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["submission_version"] == 2
    assert [row["submission_version"] for row in _rounds(client, MINA, waiting["action_item_id"])] == [1, 2]
    # A different revision on that settled round is still refused.
    assert _command(
        client, MINA, waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], changes={"title": "보내지 않은 수정"}
    ).status_code == 422


def test_a_decision_keeps_the_server_facts_apart_from_the_conditions_a_person_wrote(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "조건 분리", "assignee_id": "jiho"}).json()
    client.post(f"/api/work-requests/{request['request_id']}/evidence", headers=MINA, files={"file": ("근거.txt", b"one", "text/plain")})
    # Read the item after the basis moved: the version it hands out is the one the answer will consume.
    [item] = _pending(client, JIHO)
    adjusted = _command(
        client, JIHO, item["action_item_id"], "adjust",
        expected_version=item["expected_version"], reason="기한을 늦춰 주세요", changes={"due_date": "2026-12-01"},
    )
    assert adjusted.status_code == 200, adjusted.text

    timeline = client.get(f"/api/work-requests/{request['request_id']}/timeline", headers=JIHO).json()
    [decision] = timeline["review_decisions"]
    conditions = decision["conditions"]
    # What the reviewer said stays at the top, where a client that renders conditions has always found it.
    assert set(conditions) == {"note", "changes", "_decision"}
    assert conditions["changes"] == {"due_date": "2026-12-01"} and conditions["note"] == "기한을 늦춰 주세요"
    # What the server froze lives under one reserved key of its own.
    facts = conditions["_decision"]
    assert set(facts) == {"expected_version", "evidence_hash", "evidence_manifest"}
    assert facts["expected_version"] == item["expected_version"]
    assert decision["evidence_hash"] == facts["evidence_hash"]
    assert [row["attachment_id"] for row in facts["evidence_manifest"]] == [row["attachment_id"] for row in timeline["submissions"][0]["evidence"]]
    # The requester still reads the proposal the way they always did.
    assert _pending(client, MINA)[0]["suggested_changes"] == {"due_date": "2026-12-01"}


def test_a_revision_receipt_holds_to_the_version_it_actually_consumed(tmp_path) -> None:
    """Evidence may move the request afterwards; that must not widen which requests count as this answer."""
    client, _ = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "정확한 재전송", "assignee_id": "jiho"}).json()
    [first] = _pending(client, JIHO)
    _command(client, JIHO, first["action_item_id"], "adjust", expected_version=first["expected_version"], reason="고쳐 주세요")
    [waiting] = _pending(client, MINA)
    changes = {"title": "고친 요청"}

    assert _command(client, MINA, waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], changes=changes).status_code == 200
    client.post(f"/api/work-requests/{request['request_id']}/evidence", headers=MINA, files={"file": ("근거.txt", b"one", "text/plain")})

    # The original call, re-sent: still the same answer even though the request has moved on since.
    assert _command(client, MINA, waiting["action_item_id"], "revise", expected_version=waiting["expected_version"], changes=changes).status_code == 200
    # Any other version is a different request, not this answer coming back.
    for version in (first["expected_version"], waiting["expected_version"] - 1, waiting["expected_version"] + 1, waiting["expected_version"] + 2):
        stale = _command(client, MINA, waiting["action_item_id"], "revise", expected_version=version, changes=changes)
        assert stale.status_code == 422, f"{version}: {stale.text}"
    assert [row["submission_version"] for row in _rounds(client, MINA, waiting["action_item_id"])] == [1, 2]
