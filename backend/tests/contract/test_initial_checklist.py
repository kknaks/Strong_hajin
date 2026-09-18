"""업무를 만들 때 이미 알고 있는 단계는 그때 적는다.

Someone asking for work, assigning it, or writing it down for themselves usually already knows the first few steps.
They belong to the work from the start — in order, written by the person who wrote them — and a request's steps
survive the judgement that turns it into a Task.
"""
from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}



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
    return TestClient(app), app.state.workflow_application


def test_a_task_can_be_written_down_with_its_first_steps(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    created = client.post(
        "/api/tasks",
        headers=MINA,
        json={"title": "단계까지 아는 업무", "checklist": ["  자료 모으기 ", "초안 쓰기", "   ", "검토 요청"]},
    )
    assert created.status_code == 201, created.text

    view = client.get(f"/api/tasks/{created.json()['task_id']}", headers=MINA).json()
    # Blank lines are not steps; the rest keep the order they were written in.
    assert [row["text"] for row in view["checklist"]] == ["자료 모으기", "초안 쓰기", "검토 요청"]
    assert [row["position"] for row in view["checklist"]] == [1, 2, 3]
    assert {row["created_by"] for row in view["checklist"]} == {"mina"}
    assert view["checklist_progress"] == {"done": 0, "total": 3}


def test_the_steps_someone_asked_for_survive_the_judgement(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    request = client.post(
        "/api/work-requests",
        headers=MINA,
        json={"title": "단계까지 부탁한 요청", "assignee_id": "jiho", "checklist": ["자료 모으기", "초안 쓰기"]},
    )
    assert request.status_code == 201, request.text

    # v2: 발송은 업무를 세우고 **담당은 수락이 세운다** (SPEC-003 §4). 체크리스트는 요청과 함께 왔고
    # 수락한 업무의 체크리스트가 된다 — 쓴 사람은 부탁한 쪽이다.
    _accept(client, request.json())
    [task] = [row for row in client.get("/api/my-work", headers=JIHO).json() if row["title"] == "단계까지 부탁한 요청"]
    view = client.get(f"/api/tasks/{task['task_id']}", headers=JIHO).json()
    assert [row["text"] for row in view["checklist"]] == ["자료 모으기", "초안 쓰기"]
    # The person who wrote the step is the one who asked, not the one who accepted it.
    assert {row["created_by"] for row in view["checklist"]} == {"mina"}
    # And the holder may work them as their own from the first moment.
    checked = client.patch(
        f"/api/tasks/{task['task_id']}/checklist/{view['checklist'][0]['item_id']}",
        headers=JIHO,
        json={"expected_version": view["checklist"][0]["version"], "done": True},
    )
    assert checked.status_code == 200, checked.text


def test_assigned_work_carries_the_steps_the_assigner_wrote(tmp_path) -> None:
    client, application = _stack(tmp_path)
    jiho = application.authenticated_principal("jiho")
    assigned = application.assign_task(jiho, "단계까지 배정한 업무", "mina", idempotency_key="checklist-assign", checklist=["현황 파악", "보고서 작성"])
    task_id = assigned["task"]["task_id"]

    # 배정도 수락을 기다리지 않는다 — 명령이 성공하면 상대의 업무 목록에 이미 서 있다.
    view = client.get(f"/api/tasks/{task_id}", headers=MINA).json()
    assert [row["text"] for row in view["checklist"]] == ["현황 파악", "보고서 작성"]
    assert {row["created_by"] for row in view["checklist"]} == {"jiho"}


def test_a_proposal_shows_the_steps_it_would_create(tmp_path, monkeypatch) -> None:
    """A person approving work creation sees the steps that come with it, not just the title."""
    from uuid import UUID

    from ax_workspace.entrypoints.mcp import McpReportsFacade
    from ax_workspace.platform.persistence import ConversationTurnRecord, make_session_factory

    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    app = create_app(settings)
    client = TestClient(app)

    conversation = client.post("/api/conversations", headers=MINA, json={"title": "위임 턴"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": "delegated-mina"},
        json={"body": "업무 하나 만들어줘", "context": []},
    )
    with make_session_factory(database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"])).execution_id
    monkeypatch.setenv("AX_MCP_CAUSATION_ID", str(execution_id))

    proposed = McpReportsFacade(settings, "mina").create_self_task(
        "AX가 제안한 업무",
        "checklist-draft",
        checklist=["자료 모으기", "초안 쓰기"],
        due_date="2026-09-30",
    )
    [card] = [row for row in client.get("/api/actions", headers=MINA).json() if row["action_id"] == proposed["action_id"]]
    steps = [row for row in card["preview"] if row["id"] == "checklist"]
    assert steps and "자료 모으기" in steps[0]["value"] and "초안 쓰기" in steps[0]["value"]
    assert "2단계" in steps[0]["value"]

    client.post(
        f"/api/actions/{proposed['action_id']}/decide",
        headers=MINA,
        json={"decision": "approve", "expected_version": card["version"]},
    )
    [task] = [row for row in client.get("/api/my-work", headers=MINA).json() if row["title"] == "AX가 제안한 업무"]
    view = client.get(f"/api/tasks/{task['task_id']}", headers=MINA).json()
    assert [row["text"] for row in view["checklist"]] == ["자료 모으기", "초안 쓰기"]
