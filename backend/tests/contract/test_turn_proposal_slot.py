"""하나의 위임 턴은 한 종류의 판단을 한 번만 사람 앞에 세운다.

제안 slot 은 provider 가 같은 턴을 다시 배달해도 두 번째 effect 를 만들지 않기 위한 것이다.
그 관용이 *다른 내용*의 두 번째 요청까지 첫 번째 receipt 로 답하는 데까지 번지면, AX 는 기록되지
않은 변경을 준비했다고 말하게 된다 — 도구는 성공을 돌려받았기 때문이다.
"""
import asyncio
from uuid import UUID

import pytest

from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from test_mcp_checklist import _delegated_turn
from test_unified_commands import _stack
from ax_workspace.platform.persistence import make_session_factory


MINA = {"X-Demo-Persona": "mina"}


def _task(client, title: str) -> dict:
    return client.post("/api/tasks", headers=MINA, json={"title": title}).json()


def _update(server, task: dict, title: str):
    return asyncio.run(server.call_tool("task_update", {
        "task_id": task["task_id"], "expected_version": 1, "title": title,
    }))


def test_a_redelivered_identical_command_reuses_the_one_proposal(tmp_path, monkeypatch):
    client, application = _stack(tmp_path)
    task = _task(client, "재배달 대상")
    _delegated_turn(client, application, MINA, "mina", monkeypatch)
    server = _create_bound_persona_server(McpReportsFacade(application._settings, "mina"))

    first = _update(server, task, "고친 제목")
    again = _update(server, task, "고친 제목")

    assert not first.is_error and not again.is_error
    assert again.structured_content["action_id"] == first.structured_content["action_id"]
    assert len(client.get("/api/action-items", headers=MINA).json()) == 1


def test_a_second_different_command_is_refused_rather_than_answered_with_the_first(tmp_path, monkeypatch):
    client, application = _stack(tmp_path)
    first_task, second_task = _task(client, "먼저 고칠 업무"), _task(client, "나중에 고칠 업무")
    _delegated_turn(client, application, MINA, "mina", monkeypatch)
    server = _create_bound_persona_server(McpReportsFacade(application._settings, "mina"))

    proposed = _update(server, first_task, "고친 제목")
    assert not proposed.is_error

    with pytest.raises(Exception) as refused:
        _update(server, second_task, "다른 업무의 제목")
    assert "이 턴에는 이미" in str(refused.value)

    # 사람 앞에 선 판단은 첫 번째 하나뿐이고, 그것이 가리키는 업무도 첫 번째다.
    [item] = client.get("/api/action-items", headers=MINA).json()
    assert item["action_item_id"] == proposed.structured_content["action_id"]
    detail = client.get(f"/api/action-items/{item['action_item_id']}", headers=MINA).json()
    assert UUID(detail["edit_contract"]["values"]["task_id"]) == UUID(first_task["task_id"])


def test_a_second_judgement_on_another_item_is_refused_in_the_same_turn(tmp_path, monkeypatch):
    """위임 승인 경로도 같은 규칙을 쓴다 — 이 규칙은 `propose_action` 하나가 소유한다."""
    client, application = _stack(tmp_path)
    jiho = {"X-Demo-Persona": "jiho"}
    for title in ("먼저 판단할 요청", "나중에 판단할 요청"):
        client.post("/api/work-requests", headers=MINA, json={"title": title, "assignee_id": "jiho"})
    first, second = client.get("/api/action-items", headers=jiho).json()
    _delegated_turn(client, application, jiho, "jiho", monkeypatch)
    server = _create_bound_persona_server(McpReportsFacade(application._settings, "jiho"))

    def accept(item):
        return asyncio.run(server.call_tool("action_item_command", {
            "action_item_id": item["action_item_id"],
            "command": "accept",
            "expected_version": item["expected_version"],
        }))

    assert not accept(first).is_error
    with pytest.raises(Exception) as refused:
        accept(second)
    assert "이 턴에는 이미" in str(refused.value)
    # 두 요청 모두 아직 사람의 판단을 기다린다 — 두 번째가 첫 번째 receipt 로 처리되지 않았다.
    assert [row["title"] for row in client.get("/api/work-requests", headers=MINA).json()
            if row["state"] == "pending"] == ["먼저 판단할 요청", "나중에 판단할 요청"]


def test_the_ledger_write_path_refuses_a_retired_action_type(tmp_path, monkeypatch):
    """걷은 계약은 정책이 막는다 — 도구가 없다는 사실에만 기대지 않는다."""
    from ax_workspace.modules.ax_execution.actions import ActionError
    from ax_workspace.platform.persistence import ConversationTurnRecord

    client, application = _stack(tmp_path)
    conversation = client.post("/api/conversations", headers=MINA, json={"title": "걷은 계약"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": "retired-proposal"},
        json={"body": "회의를 만들어줘", "context": []},
    ).json()
    with make_session_factory(application._settings.database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id

    with pytest.raises(ActionError, match="회의 화면"):
        application.propose_action(
            application.authenticated_principal("mina"),
            execution_id,
            "meeting.create",
            "회의 생성 확인",
            {"title": "걷은 계약으로 만든 제안"},
        )
    assert client.get("/api/action-items", headers=MINA).json() == []
