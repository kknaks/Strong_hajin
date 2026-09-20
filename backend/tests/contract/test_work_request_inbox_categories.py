"""Inbox references are request CC relationships, never TaskReference edges."""
from dataclasses import replace
import asyncio

import pytest

from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from test_task_lifecycle_v2_support import stack, MINA, JIHO, YUNA, MINSEOK, own_task, send_request


def inbox(client, headers):
    response = client.get("/api/work-requests/inbox", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("decision", ["accept", "reject"])
def test_cc_sees_union_and_results_but_cannot_answer_even_with_decide_capability(tmp_path, decision):
    client, _ = stack(tmp_path)
    reference = send_request(client, "참조로 받은 요청", "jiho", MINA, cc_member_ids=["yuna", "yuna"])
    work = send_request(client, "담당자로 받은 요청", "yuna", MINA)
    assert {(row["request_id"], row["category"]) for row in inbox(client, YUNA)} == {
        (reference["request_id"], "reference"), (work["request_id"], "work"),
    }
    assert len(inbox(client, YUNA)) == 2
    assert inbox(client, MINA) == []  # Sending is not receiving.
    assert inbox(client, MINSEOK) == []
    rid = reference["request_id"]
    for command in ("accept", "reject"):
        denied = client.post(f"/api/work-requests/{rid}/{command}", headers=YUNA,
                             json={"expected_version": reference["version"], "reason": "CC는 판단하지 않음"})
        assert denied.status_code == 403, denied.text
    detail = client.get(f"/api/work-requests/{rid}", headers=YUNA)
    assert detail.status_code == 200 and detail.json()["state"] == "pending"
    before = client.get(f"/api/work-requests/{rid}/timeline", headers=YUNA).json()
    assert before["review_decisions"] == []
    assert not any(row["resource"]["id"] == rid for row in client.get("/api/action-items", headers=YUNA).json())

    result = client.post(f"/api/work-requests/{rid}/{decision}", headers=JIHO,
                         json={"expected_version": reference["version"], "reason": "일정상 진행 불가"})
    assert result.status_code == 200, result.text
    assert inbox(client, JIHO) == []  # Existing assignee disappearance after answering.
    row = next(row for row in inbox(client, YUNA) if row["request_id"] == rid)
    assert row["category"] == "reference" and row["state"] == result.json()["state"]
    history = client.get(f"/api/work-requests/{rid}/timeline", headers=YUNA)
    assert history.status_code == 200
    assert [(row["actor_member_id"], row["decision"]) for row in history.json()["review_decisions"]] == [("jiho", decision)]
    assert client.get(f"/api/work-requests/{rid}", headers=MINSEOK).status_code == 404


def test_cc_inbox_needs_read_not_decide_and_task_references_do_not_receive(tmp_path):
    client, _ = stack(tmp_path)
    request = send_request(client, "CC 읽기", "jiho", MINA, cc_member_ids=["yuna"])
    application = client.app.state.workflow_application
    reader = replace(application.authenticated_principal("yuna"), capabilities=frozenset({"work_request.read"}))
    [row] = application.work_request_inbox(reader)
    assert row["request_id"] == request["request_id"] and row["category"] == "reference"
    own_task(client, "일반 업무", YUNA)
    referenced = own_task(client, "참조 대상 업무", MINSEOK)
    referencing = own_task(client, "참조하는 업무", MINSEOK)
    linked = client.post(f"/api/tasks/{referencing}/references", headers=MINSEOK,
                         json={"referenced_task_id": referenced})
    assert linked.status_code == 201, linked.text
    assert inbox(client, MINSEOK) == []
    assert [row["request_id"] for row in inbox(client, YUNA)] == [request["request_id"]]


def test_negotiating_assignee_stays_work_and_cc_stays_reference(tmp_path):
    client, _ = stack(tmp_path)
    request = send_request(client, "일정 협의", "jiho", MINA, cc_member_ids=["yuna"])
    response = client.post(f"/api/work-requests/{request['request_id']}/negotiate", headers=JIHO,
                           json={"expected_version": request["version"], "conditions": {"due_date": "2030-01-01"}})
    assert response.status_code == 200, response.text
    assert [(row["category"], row["state"]) for row in inbox(client, JIHO)] == [("work", "negotiating")]
    assert [(row["category"], row["state"]) for row in inbox(client, YUNA)] == [("reference", "negotiating")]


def test_mcp_inbox_preserves_the_same_categories_as_http(tmp_path):
    client, _ = stack(tmp_path)
    send_request(client, "참조 요청", "jiho", MINA, cc_member_ids=["yuna"])
    send_request(client, "담당 요청", "yuna", MINA)
    server = _create_bound_persona_server(McpReportsFacade(client.app.state.workflow_application._settings, "yuna"))
    response = asyncio.run(server.call_tool("work_request_inbox", {}))
    assert response.structured_content["result"] == inbox(client, YUNA)
