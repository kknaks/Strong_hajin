"""Where a Task came from, answered by the server.

Requester, assigner, assignee and administrator are four different roles. The server resolves which one a Task's
origin actually names and says so, so no client re-derives it from a list it happens to be holding. The answer must
survive a reload and a new session, because it comes from the canonical source FK, not from memory.
"""
from uuid import UUID

from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
SORA = {"X-Demo-Persona": "sora"}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    app = create_app(settings)
    return TestClient(app), app.state.workflow_application, database_url


def _fresh_client(database_url, tmp_path):
    """A new process would see only what the database holds; this is the same read without any in-memory carry-over."""
    return TestClient(create_app(Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))))


def _origin(client, headers, task_id: str) -> dict:
    return client.get(f"/api/tasks/{task_id}", headers=headers).json()["origin"]


def test_a_self_created_task_names_its_creator_not_a_requester(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "내가 만든 업무"}).json()

    origin = _origin(client, MINA, task["task_id"])
    assert origin["kind"] == "self_created"
    assert origin["actor_role"] == "생성자"
    assert origin["actor"] == {"member_id": "mina", "display_name": "민아 (구성원)"}
    assert origin["source"] is None
    # The list projection carries the same answer, so no surface has to ask twice.
    [listed] = [row for row in client.get("/api/my-work", headers=MINA).json() if row["task_id"] == task["task_id"]]
    assert listed["origin"] == origin


def test_an_accepted_request_names_the_requester_and_survives_a_new_session(tmp_path) -> None:
    client, _, database_url = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "요청해서 생긴 업무", "assignee_id": "jiho"}).json()
    [judgement] = client.get("/api/action-items", headers=JIHO).json()
    accepted = client.post(
        f"/api/action-items/{judgement['action_item_id']}/commands/accept",
        headers=JIHO,
        json={"expected_version": judgement["expected_version"]},
    )
    assert accepted.status_code == 200, accepted.text
    [task] = client.get("/api/my-work", headers=JIHO).json()

    # The assignee sees who asked for the work, not who happened to create the row.
    origin = _origin(client, JIHO, task["task_id"])
    assert origin["kind"] == "work_request"
    assert origin["actor_role"] == "요청자"
    assert origin["actor"] == {"member_id": "mina", "display_name": "민아 (구성원)"}
    assert origin["source"] == {"type": "work_request", "id": request["request_id"], "title": "요청해서 생긴 업무"}

    # A new session reading only the database gives the same answer.
    fresh = _fresh_client(database_url, tmp_path)
    assert _origin(fresh, JIHO, task["task_id"]) == origin
    [listed] = fresh.get("/api/my-work", headers=JIHO).json()
    assert listed["origin"] == origin

    # And the requester can navigate the other way without a client-side join.
    [stored] = [row for row in fresh.get("/api/work-requests", headers=MINA).json() if row["request_id"] == request["request_id"]]
    assert stored["task_id"] == task["task_id"]


def test_a_direct_assignment_names_the_assigner_not_the_assignee(tmp_path) -> None:
    client, application, database_url = _stack(tmp_path)
    jiho = application.authenticated_principal("jiho")
    assigned = application.assign_task(jiho, "배정된 업무", "mina")
    client.post(f"/api/task-assignments/{assigned['assignment_id']}/accept", headers=MINA)
    [task] = [row for row in client.get("/api/my-work", headers=MINA).json() if row["title"] == "배정된 업무"]

    origin = _origin(client, MINA, task["task_id"])
    assert origin["kind"] == "direct_assignment"
    assert origin["actor_role"] == "배정자"
    assert origin["actor"] == {"member_id": "jiho", "display_name": "지호 (팀장)"}
    # The assignee is never reported as the origin actor, and neither is an administrator capability.
    assert origin["actor"]["member_id"] != "mina"
    assert _fresh_client(database_url, tmp_path).get(f"/api/tasks/{task['task_id']}", headers=MINA).json()["origin"] == origin


def test_an_approved_ax_proposal_records_the_action_item_it_came_from(tmp_path) -> None:
    """A causation string is not lineage: the Task points at the ActionItem, and the ActionItem back at the Task."""
    client, application, database_url = _stack(tmp_path)
    from ax_workspace.platform.persistence import ConversationTurnRecord, make_session_factory

    conversation = client.post("/api/conversations", headers=MINA, json={"title": "제안"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": "origin-ax"},
        json={"body": "업무를 만들어줘", "context": []},
    ).json()
    with make_session_factory(database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id
    mina = application.authenticated_principal("mina")
    proposal = application.propose_action(mina, execution_id, "task.create_self", "업무 생성 확인", {"title": "AX가 만든 업무"})
    client.post(
        f"/api/action-items/{proposal['action_id']}/commands/approve",
        headers=MINA,
        json={"expected_version": proposal["version"]},
    )

    [task] = [row for row in client.get("/api/my-work", headers=MINA).json() if row["title"] == "AX가 만든 업무"]
    detail = _fresh_client(database_url, tmp_path).get(f"/api/tasks/{task['task_id']}", headers=MINA).json()
    assert detail["lineage"]["source_action_item_id"] == proposal["action_id"]
    assert detail["origin"]["kind"] == "self_created"


def test_the_origin_source_is_hidden_from_someone_who_cannot_read_it(tmp_path) -> None:
    """A Task may be readable while the request behind it is not; the actor label stays, the source does not."""
    client, application, _ = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "비공개 요청 제목", "assignee_id": "jiho"}).json()
    [judgement] = client.get("/api/action-items", headers=JIHO).json()
    client.post(
        f"/api/action-items/{judgement['action_item_id']}/commands/accept",
        headers=JIHO,
        json={"expected_version": judgement["expected_version"]},
    )
    [task] = client.get("/api/my-work", headers=JIHO).json()

    # Sora holds no relationship to either the task or the request.
    assert client.get(f"/api/tasks/{task['task_id']}", headers=SORA).status_code in {403, 404}
    # The requester reads their own request but not the assignee's task list.
    assert client.get("/api/my-work", headers=MINA).json() == []
    # Nothing anywhere exposes the request title to a principal without access to it.
    assert "비공개 요청 제목" not in str(client.get("/api/my-work", headers=SORA).json())
    assert request["request_id"] not in str(client.get("/api/my-work", headers=SORA).json())


def test_accepting_the_same_request_twice_creates_exactly_one_task(tmp_path) -> None:
    client, _, database_url = _stack(tmp_path)
    client.post("/api/work-requests", headers=MINA, json={"title": "한 번만 생기는 업무", "assignee_id": "jiho"}).json()
    [judgement] = client.get("/api/action-items", headers=JIHO).json()
    body = {"expected_version": judgement["expected_version"]}
    first = client.post(f"/api/action-items/{judgement['action_item_id']}/commands/accept", headers=JIHO, json=body)
    second = client.post(f"/api/action-items/{judgement['action_item_id']}/commands/accept", headers=JIHO, json=body)

    assert first.status_code == 200
    assert second.status_code in {200, 422}
    assert [row["title"] for row in client.get("/api/my-work", headers=JIHO).json()] == ["한 번만 생기는 업무"]
    with __import__("sqlalchemy").create_engine(database_url).begin() as connection:
        count = connection.execute(__import__("sqlalchemy").text("SELECT count(*) FROM tasks WHERE source_work_request_id IS NOT NULL")).scalar_one()
    assert int(count) == 1


def test_reading_a_source_requires_the_request_capability_not_just_a_relationship(tmp_path) -> None:
    """Being the assignee is not permission to read the request behind the work.

    The origin actor is the Task's own fact and stays. The source resource belongs to the request module, so it needs
    that module's read capability as well as a relationship — a relationship alone must not open it.
    """
    client, application, database_url = _stack(tmp_path)
    from ax_workspace.modules.organization_access.domain import WORK_REQUEST_READ, Principal
    from ax_workspace.modules.work.application import TaskApplication
    from ax_workspace.platform.persistence import make_session_factory
    from ax_workspace.platform.work_tasks import SqlAlchemyTaskRepository, SqlAlchemyWorkRequestRepository

    client.post("/api/work-requests", headers=MINA, json={"title": "권한 확인용 요청", "assignee_id": "jiho"}).json()
    [judgement] = client.get("/api/action-items", headers=JIHO).json()
    client.post(
        f"/api/action-items/{judgement['action_item_id']}/commands/accept",
        headers=JIHO,
        json={"expected_version": judgement["expected_version"]},
    )
    [task] = client.get("/api/my-work", headers=JIHO).json()

    full = application.authenticated_principal("jiho")
    assert WORK_REQUEST_READ in full.capabilities
    # Same person, same active assignment, but without the request module's read capability.
    limited = Principal(full.id, full.display_name, full.organization_scope, frozenset(full.capabilities - {WORK_REQUEST_READ}))

    with make_session_factory(database_url)() as session:
        tasks = TaskApplication(SqlAlchemyTaskRepository(session), SqlAlchemyWorkRequestRepository(session))
        allowed = tasks.get(full, UUID(task["task_id"]))["origin"]
        restricted = tasks.get(limited, UUID(task["task_id"]))["origin"]

    assert allowed["source"] == {"type": "work_request", "id": allowed["source"]["id"], "title": "권한 확인용 요청"}
    # The actor label survives; the source does not, and its title never leaks.
    assert restricted["kind"] == "work_request" and restricted["actor_role"] == "요청자"
    assert restricted["actor"] == {"member_id": "mina", "display_name": "민아 (구성원)"}
    assert restricted["source"] is None
    assert "권한 확인용 요청" not in str(restricted)


def test_the_requester_can_open_the_derived_task_read_only_and_navigate_back(tmp_path) -> None:
    """Both directions, each permission-safe.

    A requester is not the assignee, so they may read the Task their request produced but not drive it. The Task's
    source, when they may read it, points back at the request it came from.
    """
    client, _, _ = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "왕복 확인 요청", "assignee_id": "jiho"}).json()
    [judgement] = client.get("/api/action-items", headers=JIHO).json()
    client.post(
        f"/api/action-items/{judgement['action_item_id']}/commands/accept",
        headers=JIHO,
        json={"expected_version": judgement["expected_version"]},
    )
    [task] = client.get("/api/my-work", headers=JIHO).json()

    # Forward: the requester opens the derived Task without holding it.
    seen = client.get(f"/api/tasks/{task['task_id']}", headers=MINA)
    assert seen.status_code == 200, seen.text
    detail = seen.json()
    assert detail["title"] == "왕복 확인 요청" and detail["access"] == "read_only"
    # Reading is not driving, and it is not the assignee's workspace either.
    assert "checklist" not in detail
    assert client.patch(f"/api/tasks/{task['task_id']}", headers=MINA, json={"expected_version": detail["version"], "title": "몰래 수정"}).status_code in {403, 404}
    assert client.post(f"/api/tasks/{task['task_id']}/start", headers=MINA, json={"expected_version": detail["version"]}).status_code in {403, 404}
    assert client.post(f"/api/tasks/{task['task_id']}/checklist", headers=MINA, json={"text": "몰래 추가"}).status_code in {403, 404}
    assert client.get(f"/api/tasks/{task['task_id']}/materials", headers=MINA).status_code in {403, 404}

    # Backward: the source names the request it came from, for a principal allowed to read it.
    assert detail["origin"]["source"] == {"type": "work_request", "id": request["request_id"], "title": "왕복 확인 요청"}
    assert client.get("/api/work-requests", headers=MINA).json()[0]["task_id"] == task["task_id"]
    # The assignee holds the Task fully and sees the same source.
    owned = client.get(f"/api/tasks/{task['task_id']}", headers=JIHO).json()
    assert owned["access"] == "owner" and "checklist" in owned
    assert owned["origin"]["source"]["id"] == request["request_id"]

    # Someone with no relationship to either resource still gets nothing.
    assert client.get(f"/api/tasks/{task['task_id']}", headers=SORA).status_code in {403, 404}
