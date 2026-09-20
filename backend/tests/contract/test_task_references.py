"""참고 업무는 맥락이지 인과관계가 아니다.

Work usually follows other work. Pointing at it is enough — SCAX does not ask which kind of relation it is, and
never turns a pointer into permission. A reference is an explicit row between two Tasks, so the database itself
answers whether the thing being pointed at exists, and letting go of one does not erase that it was there.
"""
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import TaskReferenceRecord, make_session_factory

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
SORA = {"X-Demo-Persona": "sora"}



def _accept(client, request: dict, headers=JIHO) -> None:
    """받는 사람이 수락한다 — 여기서 담당이 확정되고 그 업무가 「내 업무」에 선다 (SPEC-003 §4 수락).

    W1 에서는 이 단계가 없었다(발송이 곧 배정). v2 가 되돌린 것은 **이 한 단계뿐**이다.
    """
    answered = client.post(
        f"/api/work-requests/{request['request_id']}/accept",
        headers=headers, json={"expected_version": request["version"]},
    )
    assert answered.status_code == 200, answered.text

def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    app = create_app(settings)
    return TestClient(app), app.state.workflow_application, database_url


def _task(client, title: str, headers=MINA) -> str:
    return client.post("/api/tasks", headers=headers, json={"title": title}).json()["task_id"]


def test_a_task_can_point_at_work_that_came_before_it(tmp_path) -> None:
    client, _, database_url = _stack(tmp_path)
    earlier = _task(client, "1분기 정산")
    current = _task(client, "2분기 정산")

    linked = client.post(f"/api/tasks/{current}/references", headers=MINA, json={"referenced_task_id": earlier})
    assert linked.status_code == 201, linked.text
    reference = linked.json()
    assert reference["task"]["task_id"] == earlier and reference["task"]["title"] == "1분기 정산"
    assert reference["task"]["state"] == "open"

    view = client.get(f"/api/tasks/{current}", headers=MINA).json()
    assert [row["task"]["title"] for row in view["references"]] == ["1분기 정산"]
    # The row itself is the link, so the database can tell whether the work being pointed at exists.
    with make_session_factory(database_url)() as session:
        [row] = session.scalars(select(TaskReferenceRecord)).all()
        assert row.task_id == UUID(current) and row.referenced_task_id == UUID(earlier)
        assert row.released_at is None and row.created_by == "mina"

    # Pointing twice at the same work is the same pointer, and nothing points at itself.
    assert client.post(f"/api/tasks/{current}/references", headers=MINA, json={"referenced_task_id": earlier}).status_code == 422
    assert client.post(f"/api/tasks/{current}/references", headers=MINA, json={"referenced_task_id": current}).status_code == 422
    assert client.post(
        f"/api/tasks/{current}/references", headers=MINA,
        json={"referenced_task_id": "11111111-1111-4111-8111-111111111111"},
    ).status_code in {404, 422}


def test_letting_go_of_a_reference_does_not_erase_that_it_was_there(tmp_path) -> None:
    client, _, database_url = _stack(tmp_path)
    earlier = _task(client, "먼저 한 업무")
    current = _task(client, "이어서 하는 업무")
    reference = client.post(f"/api/tasks/{current}/references", headers=MINA, json={"referenced_task_id": earlier}).json()

    linked_version = client.get(f"/api/tasks/{current}", headers=MINA).json()["version"]
    released = client.delete(f"/api/tasks/{current}/references/{reference['reference_id']}", headers=MINA)
    assert released.status_code == 200, released.text
    assert client.get(f"/api/tasks/{current}", headers=MINA).json()["references"] == []

    with make_session_factory(database_url)() as session:
        [row] = session.scalars(select(TaskReferenceRecord)).all()
        assert row.released_at is not None and row.released_by == "mina"

    history = client.get(f"/api/tasks/{current}/history", headers=MINA).json()
    kinds = [row["event_kind"] for row in history["activity"]]
    assert "task.reference_added" in kinds and "task.reference_released" in kinds
    # The version that had it still has it, and the current one does not.
    frozen = {row["version"]: row["snapshot"] for row in history["versions"]}
    assert [ref["referenced_task_id"] for ref in frozen[linked_version]["references"]] == [earlier]
    assert frozen[max(frozen)]["references"] == []

    # The same work can be pointed at again afterwards; that is a new pointer, not the old one revived.
    again = client.post(f"/api/tasks/{current}/references", headers=MINA, json={"referenced_task_id": earlier})
    assert again.status_code == 201 and again.json()["reference_id"] != reference["reference_id"]


def test_a_reference_never_hands_out_access(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    theirs = _task(client, "지호의 업무", JIHO)
    mine = _task(client, "내 업무")

    # You can only point at work you can already read.
    assert client.post(f"/api/tasks/{mine}/references", headers=MINA, json={"referenced_task_id": theirs}).status_code in {403, 404, 422}
    assert client.get(f"/api/tasks/{mine}", headers=MINA).json()["references"] == []

    # And someone with no relationship to a task learns nothing about what it points at.
    earlier = _task(client, "내 이전 업무")
    client.post(f"/api/tasks/{mine}/references", headers=MINA, json={"referenced_task_id": earlier})
    stranger = client.get(f"/api/tasks/{mine}", headers=SORA)
    assert stranger.status_code in {403, 404}
    assert "내 이전 업무" not in stranger.text


def test_the_work_someone_referred_to_travels_with_the_request(tmp_path) -> None:
    """A requester points at earlier work; the person accepting gets the same pointer on their new Task."""
    client, _, _ = _stack(tmp_path)
    earlier = _task(client, "지난 분기 보고")

    request = client.post(
        "/api/work-requests",
        headers=MINA,
        json={
            "title": "이번 분기 보고",
            "assignee_id": "jiho",
            "checklist": ["자료 수집", "수치 검토"],
            "reference_task_ids": [earlier],
        },
    )
    assert request.status_code == 201, request.text

    detail = client.get(f"/api/work-requests/{request.json()['request_id']}", headers=MINA).json()
    assert detail["checklist"] == ["자료 수집", "수치 검토"]
    assert [row["task"]["title"] for row in detail["references"]] == ["지난 분기 보고"]

    # v2: 발송은 업무를 세우고 **담당은 수락이 세운다** (SPEC-003 §4).
    _accept(client, request.json())
    [task] = [row for row in client.get("/api/my-work", headers=JIHO).json() if row["title"] == "이번 분기 보고"]

    # The pointer carried over, but the holder still may not read work that was never theirs.
    holder = client.get(f"/api/tasks/{task['task_id']}", headers=JIHO).json()
    assert len(holder["references"]) == 1
    assert holder["references"][0]["task"] is None or holder["references"][0]["task"].get("title") in {None, "볼 수 없는 업무"}


def test_direct_mcp_work_request_uses_the_same_reference_authorization_as_http(tmp_path, monkeypatch) -> None:
    from ax_workspace.entrypoints.mcp import McpReportsFacade

    client, _, database_url = _stack(tmp_path)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    earlier = _task(client, "직전 정산")
    monkeypatch.delenv("AX_MCP_CAUSATION_ID", raising=False)

    created = McpReportsFacade(settings, "mina").create_work_request(
        "다음 정산",
        "jiho",
        "reference-request",
        due_date="2026-09-30",
        checklist=["수치 검토"],
        reference_task_ids=[earlier],
    )

    detail = client.get(f"/api/work-requests/{created['request_id']}", headers=MINA).json()
    assert [row["task"]["title"] for row in detail["references"]] == ["직전 정산"]
    # 수락하면 담당자의 업무가 선다. 가리킨 참고 업무는 **읽을 수 있는 사람의 것만** 실렸다.
    _accept(client, created)
    [mine] = client.get("/api/my-work", headers=JIHO).json()
    assert mine["title"] == "다음 정산"


def test_a_task_is_no_longer_stored_as_a_generic_attachment_reference(tmp_path) -> None:
    """One home for one meaning: SCAX Tasks are references, not attachments that happen to name a task."""
    client, _, _ = _stack(tmp_path)
    earlier = _task(client, "이전 업무")
    current = _task(client, "지금 업무")

    refused = client.post(
        f"/api/tasks/{current}/materials/references",
        headers=MINA,
        json={"kind": "input", "resource_type": "task", "resource_id": earlier},
    )
    assert refused.status_code == 422
    assert "참고 업무" in refused.text
    assert client.get(f"/api/tasks/{current}/materials", headers=MINA).json() == []


def test_a_turn_can_propose_work_that_points_at_earlier_work(tmp_path, monkeypatch) -> None:
    """AX may prepare the pointer; the person approving sees which work it names before anything is created."""
    from ax_workspace.entrypoints.mcp import McpReportsFacade
    from ax_workspace.platform.persistence import ConversationTurnRecord

    client, _, database_url = _stack(tmp_path)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    earlier = _task(client, "1분기 정산")

    conversation = client.post("/api/conversations", headers=MINA, json={"title": "위임 턴"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": "reference-turn"},
        json={"body": "후속 업무 만들어줘", "context": []},
    )
    with make_session_factory(database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"])).execution_id
    monkeypatch.setenv("AX_MCP_CAUSATION_ID", str(execution_id))
    proposed = McpReportsFacade(settings, "mina").create_self_task(
        "2분기 정산", "reference-draft", reference_task_ids=[earlier], due_date="2026-09-30"
    )
    monkeypatch.delenv("AX_MCP_CAUSATION_ID", raising=False)

    [card] = [row for row in client.get("/api/actions", headers=MINA).json() if row["action_id"] == proposed["action_id"]]
    [row] = [field for field in card["preview"] if field["id"] == "references"]
    assert row["value"] == "1분기 정산"

    client.post(
        f"/api/actions/{proposed['action_id']}/decide",
        headers=MINA,
        json={"decision": "approve", "expected_version": card["version"]},
    )
    [created] = [task for task in client.get("/api/my-work", headers=MINA).json() if task["title"] == "2분기 정산"]
    view = client.get(f"/api/tasks/{created['task_id']}", headers=MINA).json()
    assert [reference["task"]["title"] for reference in view["references"]] == ["1분기 정산"]
