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



def _accept(client, request: dict, headers=JIHO) -> None:
    """받는 사람이 수락한다 — 여기서 담당이 확정되고 그 업무가 「내 업무」에 선다.

    W1 에서는 이 단계가 없었다(발송이 곧 배정). v2 가 되돌린 것은 **이 한 단계뿐**이고,
    아래 테스트들이 보는 관계·이력·완료는 그대로다.
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


def _fresh_client(database_url, tmp_path):
    """A new process would see only what the database holds; this is the same read without any in-memory carry-over."""
    return TestClient(create_app(Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))))


def _origin(client, headers, task_id: str) -> dict:
    return client.get(f"/api/tasks/{task_id}", headers=headers).json()["origin"]


def test_a_task_i_made_for_myself_has_no_counterpart_to_name(tmp_path) -> None:
    """Nobody asked for it and nobody assigned it, so there is no requester and no assigner to invent."""
    client, _, _ = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "내가 만든 업무"}).json()

    assert _origin(client, MINA, task["task_id"]) is None
    # The list projection says the same thing, so no surface has to invent a label of its own.
    [listed] = [row for row in client.get("/api/my-work", headers=MINA).json() if row["task_id"] == task["task_id"]]
    assert listed["origin"] is None
    # The canonical record still says exactly what happened: mina assigned this to herself.
    assert listed["assignee"] == {"member_id": "mina", "display_name": "민아 (구성원)"}


def test_a_sent_request_names_the_requester_and_survives_a_new_session(tmp_path) -> None:
    client, _, database_url = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "요청해서 생긴 업무", "assignee_id": "jiho"}).json()
    # v2: 발송은 업무를 세우고 **담당은 수락이 세운다** (SPEC-003 §4). 그 뒤는 예전과 같다.
    _accept(client, request)
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
    assigned = application.assign_task(jiho, "배정된 업무", "mina", idempotency_key="origin-assign-1")
    # 수락 단계가 없다 — 배정이 성공한 자리에서 이미 민아가 들고 있다 (WORK-001 Phase 4).
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
    proposal = application.propose_action(
        mina, execution_id, "task.create_self", "업무 생성 확인", {"title": "AX가 만든 업무", "due_date": "2026-09-30"}
    )
    client.post(
        f"/api/action-items/{proposal['action_id']}/commands/confirm",
        headers=MINA,
        json={"expected_version": proposal["version"], "base_submission_version": 1},
    )

    [task] = [row for row in client.get("/api/my-work", headers=MINA).json() if row["title"] == "AX가 만든 업무"]
    detail = _fresh_client(database_url, tmp_path).get(f"/api/tasks/{task['task_id']}", headers=MINA).json()
    assert detail["lineage"]["source_action_item_id"] == proposal["action_id"]
    # AX prepared it and mina approved it: the source is worth showing, but she is not her own counterpart.
    assert detail["origin"]["kind"] == "self_created"
    assert detail["origin"]["actor"] is None and detail["origin"]["actor_role"] is None
    assert detail["origin"]["source"]["type"] == "action_item"


def test_the_origin_source_is_hidden_from_someone_who_cannot_read_it(tmp_path) -> None:
    """A Task may be readable while the request behind it is not; the actor label stays, the source does not."""
    client, application, _ = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "비공개 요청 제목", "assignee_id": "jiho"}).json()
    # v2: 발송은 업무를 세우고 **담당은 수락이 세운다** (SPEC-003 §4). 그 뒤는 예전과 같다.
    _accept(client, request)
    [task] = client.get("/api/my-work", headers=JIHO).json()

    # Sora holds no relationship to either the task or the request.
    assert client.get(f"/api/tasks/{task['task_id']}", headers=SORA).status_code in {403, 404}
    # The requester reads their own request but not the assignee's task list.
    assert client.get("/api/my-work", headers=MINA).json() == []
    # Nothing anywhere exposes the request title to a principal without access to it.
    assert "비공개 요청 제목" not in str(client.get("/api/my-work", headers=SORA).json())
    assert request["request_id"] not in str(client.get("/api/my-work", headers=SORA).json())


def test_resending_the_same_request_creates_exactly_one_task(tmp_path) -> None:
    """한 요청에 업무 하나. 같은 멱등 키의 재전송은 **영수증**이지 두 번째 생성이 아니다."""
    client, _, database_url = _stack(tmp_path)
    body = {"title": "한 번만 생기는 업무", "assignee_id": "jiho"}
    first = client.post("/api/work-requests", headers={**MINA, "Idempotency-Key": "once"}, json=body)
    second = client.post("/api/work-requests", headers={**MINA, "Idempotency-Key": "once"}, json=body)

    assert first.status_code == 201
    assert second.status_code == 201 and second.json()["request_id"] == first.json()["request_id"]
    # 재전송은 **한 건**이다 — 수락도 한 번이고, 그 뒤 담당자의 목록에 한 줄만 선다.
    _accept(client, first.json())
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

    request = client.post("/api/work-requests", headers=MINA, json={"title": "권한 확인용 요청", "assignee_id": "jiho"}).json()
    # v2: 발송은 업무를 세우고 **담당은 수락이 세운다** (SPEC-003 §4). 그 뒤는 예전과 같다.
    _accept(client, request)
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
    # v2: 발송은 업무를 세우고 **담당은 수락이 세운다** (SPEC-003 §4). 그 뒤는 예전과 같다.
    _accept(client, request)
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
    # **자료는 이제 열린다** — 요청자는 자기가 부탁한 업무의 연결 자료를 읽는다 (정책 V-21).
    # 이 줄이 W1 에서 막혀 있던 것이 「상세는 보이는데 파일은 못 연다」의 실체였고, v2 가 닫는 자리다.
    # **읽기가 열린다고 쓰기가 열리는 것이 아니다** — 바로 위 세 줄(수정·시작·체크리스트)은 그대로 막힌다.
    assert client.get(f"/api/tasks/{task['task_id']}/materials", headers=MINA).status_code == 200

    # Backward: the source names the request it came from, for a principal allowed to read it.
    assert detail["origin"]["source"] == {"type": "work_request", "id": request["request_id"], "title": "왕복 확인 요청"}
    assert client.get("/api/work-requests", headers=MINA).json()[0]["task_id"] == task["task_id"]
    # The assignee holds the Task fully and sees the same source.
    owned = client.get(f"/api/tasks/{task['task_id']}", headers=JIHO).json()
    assert owned["access"] == "owner" and "checklist" in owned
    assert owned["origin"]["source"]["id"] == request["request_id"]

    # Someone with no relationship to either resource still gets nothing.
    assert client.get(f"/api/tasks/{task['task_id']}", headers=SORA).status_code in {403, 404}


def test_the_task_names_its_current_assignee_from_the_active_assignment(tmp_path) -> None:
    """Who holds the work is a server projection, not a name the caller looked up.

    On a request-origin Task the requester and the assignee are different people, which is exactly where a client-side
    guess goes wrong.
    """
    client, _, _ = _stack(tmp_path)
    request = client.post("/api/work-requests", headers=MINA, json={"title": "담당자 확인 요청", "assignee_id": "jiho"}).json()
    # v2: 발송은 업무를 세우고 **담당은 수락이 세운다** (SPEC-003 §4). 그 뒤는 예전과 같다.
    _accept(client, request)
    [task] = client.get("/api/my-work", headers=JIHO).json()

    # The requester reads the Task: the origin names them, the assignee names the other person.
    requester_view = client.get(f"/api/tasks/{task['task_id']}", headers=MINA).json()
    assert requester_view["origin"]["actor"]["member_id"] == "mina"
    assert requester_view["assignee"] == {"member_id": "jiho", "display_name": "지호 (팀장)"}
    # The holder sees the same assignee, and the list projection agrees.
    assert client.get(f"/api/tasks/{task['task_id']}", headers=JIHO).json()["assignee"] == requester_view["assignee"]
    assert [row["assignee"] for row in client.get("/api/my-work", headers=JIHO).json()] == [requester_view["assignee"]]


def test_a_direct_assigner_can_follow_the_work_they_handed_out_without_holding_it(tmp_path) -> None:
    """The assigner keeps a read on what they assigned: current assignee and state, and no way to drive it."""
    client, application, _ = _stack(tmp_path)
    jiho = application.authenticated_principal("jiho")
    assigned = application.assign_task(jiho, "배정한 업무", "mina", idempotency_key="origin-assign-2")
    # 수락 단계가 없다 — 배정이 성공한 자리에서 이미 민아가 들고 있다 (WORK-001 Phase 4).
    [task] = [row for row in client.get("/api/my-work", headers=MINA).json() if row["title"] == "배정한 업무"]

    seen = client.get(f"/api/tasks/{task['task_id']}", headers=JIHO)
    assert seen.status_code == 200, seen.text
    detail = seen.json()
    assert detail["access"] == "read_only"
    assert detail["origin"] == {
        "kind": "direct_assignment",
        "actor_role": "배정자",
        "actor": {"member_id": "jiho", "display_name": "지호 (팀장)"},
        "source": None,
    }
    assert detail["assignee"] == {"member_id": "mina", "display_name": "민아 (구성원)"}
    assert detail["state"] == "open"
    assert "checklist" not in detail
    # Following is not driving.
    for path, payload in (
        (f"/api/tasks/{task['task_id']}", {"expected_version": detail["version"], "title": "몰래 수정"}),
        (f"/api/tasks/{task['task_id']}/checklist", {"text": "몰래 추가"}),
    ):
        method = client.patch if path.endswith(task["task_id"]) else client.post
        assert method(path, headers=JIHO, json=payload).status_code in {403, 404}
    assert client.post(f"/api/tasks/{task['task_id']}/start", headers=JIHO, json={"expected_version": detail["version"]}).status_code in {403, 404}
    # Someone with neither the assignment relationship nor the capability still sees nothing.
    assert client.get(f"/api/tasks/{task['task_id']}", headers=SORA).status_code in {403, 404}


def test_an_ax_task_points_back_at_the_action_item_that_proposed_it(tmp_path) -> None:
    client, application, database_url = _stack(tmp_path)
    from ax_workspace.platform.persistence import ConversationTurnRecord, make_session_factory

    conversation = client.post("/api/conversations", headers=MINA, json={"title": "제안"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": "origin-ax-source"},
        json={"body": "업무를 만들어줘", "context": []},
    ).json()
    with make_session_factory(database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id
    mina = application.authenticated_principal("mina")
    proposal = application.propose_action(
        mina, execution_id, "task.create_self", "업무 생성 확인", {"title": "AX 왕복 업무", "due_date": "2026-09-30"}
    )
    client.post(
        f"/api/action-items/{proposal['action_id']}/commands/confirm",
        headers=MINA,
        json={"expected_version": proposal["version"], "base_submission_version": 1},
    )
    [task] = [row for row in client.get("/api/my-work", headers=MINA).json() if row["title"] == "AX 왕복 업무"]

    # Task → ActionItem, named and openable.
    origin = client.get(f"/api/tasks/{task['task_id']}", headers=MINA).json()["origin"]
    assert origin["source"] == {"type": "action_item", "id": proposal["action_id"], "title": "AX가 만든 업무"} or origin["source"] == {
        "type": "action_item",
        "id": proposal["action_id"],
        "title": origin["source"]["title"],
    }
    assert origin["source"]["type"] == "action_item" and origin["source"]["id"] == proposal["action_id"]
    assert client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).status_code == 200

    # ActionItem → derived Task, so the round trip closes.
    detail = client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()
    assert detail["derived_task_id"] == task["task_id"]

    # A principal without the action capability sees the Task's actor but not the proposal behind it.
    assert client.get(f"/api/action-items/{proposal['action_id']}", headers=JIHO).status_code in {403, 404}


def _ax_execution(client, application, database_url, key: str) -> UUID:
    from ax_workspace.platform.persistence import ConversationTurnRecord, make_session_factory

    conversation = client.post("/api/conversations", headers=MINA, json={"title": "제안"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": key},
        json={"body": "업무를 만들어줘", "context": []},
    ).json()
    with make_session_factory(database_url)() as session:
        return session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id


def test_a_follow_up_proposal_on_an_ax_created_task_still_previews_its_target(tmp_path) -> None:
    """The preview reads the target Task, and that Task's own origin points back at an ActionItem.

    Both directions run in one request, so the action layer must hand the task layer a real authorized Action
    lookup rather than itself.
    """
    client, application, database_url = _stack(tmp_path)
    mina = application.authenticated_principal("mina")
    created = application.propose_action(
        mina,
        _ax_execution(client, application, database_url, "follow-up-1"),
        "task.create_self",
        "업무 생성 확인",
        {"title": "AX가 만든 원본 업무", "due_date": "2026-09-30"},
    )
    client.post(
        f"/api/action-items/{created['action_id']}/commands/confirm",
        headers=MINA,
        json={"expected_version": created["version"], "base_submission_version": 1},
    )
    [task] = [row for row in client.get("/api/my-work", headers=MINA).json() if row["title"] == "AX가 만든 원본 업무"]

    # A second proposal that edits that same Task; its preview has to resolve the target title.
    follow_up = application.propose_action(
        mina,
        _ax_execution(client, application, database_url, "follow-up-2"),
        "task.update",
        "업무 수정 확인",
        {"task_id": task["task_id"], "expected_version": task["version"], "changes": {"due_date": "2026-10-01"}},
    )
    assert follow_up["subject"] == "AX가 만든 원본 업무"
    assert {row["label"]: row["value"] for row in follow_up["preview"]}["대상 업무"] == "AX가 만든 원본 업무"

    detail = client.get(f"/api/action-items/{follow_up['action_id']}", headers=MINA)
    assert detail.status_code == 200, detail.text
    assert detail.json()["subject"] == "AX가 만든 원본 업무"
    listed = client.get("/api/actions", headers=MINA)
    assert listed.status_code == 200, listed.text
    assert "업무 수정 확인" in [row["title"] for row in listed.json()]


def test_the_source_label_is_the_work_that_was_created_not_the_proposal_label(tmp_path) -> None:
    client, application, database_url = _stack(tmp_path)
    mina = application.authenticated_principal("mina")
    proposal = application.propose_action(
        mina,
        _ax_execution(client, application, database_url, "label-1"),
        "task.create_self",
        "업무 생성 확인",
        {"title": "AX 왕복 업무", "due_date": "2026-09-30"},
    )
    client.post(
        f"/api/action-items/{proposal['action_id']}/commands/confirm",
        headers=MINA,
        json={"expected_version": proposal["version"], "base_submission_version": 1},
    )
    [task] = [row for row in client.get("/api/my-work", headers=MINA).json() if row["title"] == "AX 왕복 업무"]

    origin = client.get(f"/api/tasks/{task['task_id']}", headers=MINA).json()["origin"]
    # The label names the work, not the internal confirmation wording.
    assert origin["source"] == {"type": "action_item", "id": proposal["action_id"], "title": "AX 왕복 업무"}
    assert "업무 생성 확인" not in str(origin)


def test_without_the_action_capability_the_task_reads_but_its_proposal_does_not(tmp_path) -> None:
    client, application, database_url = _stack(tmp_path)
    from ax_workspace.modules.organization_access.domain import ACTION_READ, Principal
    from ax_workspace.modules.work.application import TaskApplication
    from ax_workspace.platform.persistence import make_session_factory
    from ax_workspace.platform.work_tasks import SqlAlchemyTaskRepository, SqlAlchemyWorkRequestRepository

    mina = application.authenticated_principal("mina")
    proposal = application.propose_action(
        mina,
        _ax_execution(client, application, database_url, "withheld-1"),
        "task.create_self",
        "업무 생성 확인",
        {"title": "권한 없는 왕복 업무", "due_date": "2026-09-30"},
    )
    client.post(
        f"/api/action-items/{proposal['action_id']}/commands/confirm",
        headers=MINA,
        json={"expected_version": proposal["version"], "base_submission_version": 1},
    )
    [task] = [row for row in client.get("/api/my-work", headers=MINA).json() if row["title"] == "권한 없는 왕복 업무"]

    limited = Principal(mina.id, mina.display_name, mina.organization_scope, frozenset(mina.capabilities - {ACTION_READ}))
    with make_session_factory(database_url)() as session:
        tasks = TaskApplication(
            SqlAlchemyTaskRepository(session), SqlAlchemyWorkRequestRepository(session), application._action_repository(session)
        )
        view = tasks.get(limited, UUID(task["task_id"]))

    # The Task is theirs to read; the proposal behind it is not, so no source and no title leak.
    assert view["title"] == "권한 없는 왕복 업무" and view["access"] == "owner"
    # No readable source and no counterpart: there is nothing left to say about where it came from.
    assert view["origin"] is None


def test_the_activity_says_who_did_what_to_whom(tmp_path) -> None:
    """A ledger line reads as a sentence about people, not as a field dump."""
    client, application, database_url = _stack(tmp_path)
    from ax_workspace.platform.persistence import ActivityEventRecord, make_session_factory
    from sqlalchemy import select

    jiho = application.authenticated_principal("jiho")
    application.assign_task(jiho, "배정한 업무", "mina", idempotency_key="origin-assign-2")
    client.post("/api/work-requests", headers=MINA, json={"title": "요청한 업무", "assignee_id": "jiho"})
    client.post("/api/tasks", headers=MINA, json={"title": "내가 만든 업무"})

    with make_session_factory(database_url)() as session:
        summaries = {row.event_kind: row.safe_summary for row in session.scalars(select(ActivityEventRecord))}
    assert summaries["task.assigned"] == "지호가 담당자를 민아로 지정함: 배정한 업무"
    assert summaries["work_request.created"] == "민아가 지호에게 업무를 보냄: 요청한 업무"
    # A task nobody handed over says only what happened, with no second party invented.
    assert summaries["task.created"] == "업무 생성: 내가 만든 업무"
