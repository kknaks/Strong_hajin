"""**일을 보내면 사람도 따라간다** — 소속과 참여의 정합 (SPEC-005 §4 · BASE-004 어긋남 ③·④).

업무는 프로젝트에 들어가는데 사람은 안 들어갔다. 프로젝트 밖 사람에게 일을 보내면 그 사람은 자기 앞에
놓인 일을 열지 못했고, 그 일이 어느 프로젝트의 것인지도 보지 못했다. 그리고 상위 업무를 다른 프로젝트로
옮기면 **자식만 따라가고 손자는 옛 프로젝트에 남았다** — 선행은 같은 프로젝트 안에서만 성립하므로
저장소가 강제하는 불변식이 그 자리에서 데이터 안에서 깨졌다.

**이것은 화면이 아니라 도메인 버그 수정이다.** 프로젝트 화면을 만들지 않아도 이미 일어나고 있던 일이고,
화면은 그것을 드러냈을 뿐이다.

**거는 자리는 라우트가 아니라 application 메서드다** (SPEC-005 §5). 같은 명령이 HTTP · MCP ·
판단함 명령 세 입구로 들어오므로 입구에 걸면 조용히 빠지는 경로가 생긴다.
"""
from uuid import UUID

from fastapi.testclient import TestClient
from legacy_acceptance import make_assignment_look_pending
import pytest

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import ConversationTurnRecord, make_session_factory

YUNA = {"X-Demo-Persona": "yuna"}      # 대표 — 조직 전체가 배정 범위다
JIHO = {"X-Demo-Persona": "jiho"}      # 제품팀장
MINA = {"X-Demo-Persona": "mina"}      # 제품팀 구성원
HYEON = {"X-Demo-Persona": "hyeon"}    # 인사 — 제품팀 밖이고, 어느 프로젝트에도 없다


@pytest.fixture()
def stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    app = create_app(Settings(RuntimeProfile.TEST, database_url))
    return TestClient(app), app.state.workflow_application


@pytest.fixture()
def client(stack) -> TestClient:
    return stack[0]


def _project(client: TestClient, *, name: str = "한빛 통합 마케팅", by: dict | None = None) -> str:
    made = client.post("/api/projects", headers=by or YUNA, json={"name": name})
    assert made.status_code == 201, made.text
    return made.json()["project_id"]


def _send(client: TestClient, project_id: str | None, title: str, *, key: str, to: str = "hyeon", by: dict | None = None) -> dict:
    sent = client.post(
        "/api/work-requests",
        headers={**(by or YUNA), "Idempotency-Key": key},
        json={"title": title, "assignee_id": to, **({"project_id": project_id} if project_id else {})},
    )
    assert sent.status_code == 201, sent.text
    return sent.json()


def _history(client: TestClient, project_id: str, *, by: dict | None = None) -> list[tuple]:
    rows = client.get(f"/api/projects/{project_id}/participation-history", headers=by or YUNA)
    assert rows.status_code == 200, rows.text
    return [(row["member_id"], row["assignment_kind"], row["end_reason"]) for row in rows.json()]


def _selector(client: TestClient, who: dict) -> set[str]:
    """그 사람의 프로젝트 셀렉터 — **자동 초대·해제의 관측면**이다 (SPEC-005 §4 I-8)."""
    return {row["name"] for row in client.get("/api/projects", headers=who).json()}


# ---- 자동 초대 — 붙는 자리 둘 (D-11 · ~~D-12~~ → D-28) ----


def test_sending_a_request_puts_the_recipient_on_that_project(client: TestClient) -> None:
    """**붙는 자리 #1 — 업무 요청 발송.**

    요청은 묻지 않고 상위의 프로젝트를 물려받으면서 **받는 사람이 그 프로젝트에 있는지는 보지 않았다.**
    보내기 전에는 그 프로젝트가 그 사람의 셀렉터에 없고, 보낸 뒤에는 **있다.**
    """
    project_id = _project(client)
    assert _selector(client, HYEON) == set()

    _send(client, project_id, "인사 자료 정리", key="join-1")

    assert _selector(client, HYEON) == {"한빛 통합 마케팅"}
    # **`참여`(member)로 붙는다 — `lead` 가 아니다.**
    assert ("hyeon", "member", None) in _history(client, project_id)


def test_proposing_a_new_holder_puts_that_person_on_the_project_too(client: TestClient) -> None:
    """**붙는 자리 #2 — 담당 교체 제안.** 새 담당이 그 프로젝트 밖일 수 있고, 붙는 조건이 #1 과 같다."""
    project_id = _project(client)
    planned = client.post(
        f"/api/projects/{project_id}/tasks", headers=YUNA, json={"title": "플레이스 썸네일 제작"}
    ).json()
    assert _selector(client, HYEON) == set()

    handed = client.post(
        f"/api/tasks/{planned['task_id']}/reassign",
        headers=YUNA,
        json={"expected_version": planned["version"], "assignee_id": "hyeon"},
    )
    assert handed.status_code == 200, handed.text

    assert _selector(client, HYEON) == {"한빛 통합 마케팅"}
    assert ("hyeon", "member", None) in _history(client, project_id)


def test_sending_to_someone_already_on_the_project_changes_nothing(client: TestClient) -> None:
    """**멱등이다** — 이력에 줄이 늘지 않고 관계 종류가 **바뀌지도 않는다.** 승격 경로가 아니다 (D-11)."""
    project_id = _project(client)
    client.post(f"/api/projects/{project_id}/members", headers=YUNA, json={"member_id": "hyeon", "kind": "lead"})
    before = _history(client, project_id)

    _send(client, project_id, "인사 자료 정리", key="idem-1")

    assert _history(client, project_id) == before
    assert ("hyeon", "lead", None) in before, "원래 관계 종류가 그대로여야 합니다"


def test_assigning_is_the_whole_condition_and_the_assigner_is_not_asked_for_permission(client: TestClient) -> None:
    """**열쇠가 없다 — 배정이 성립하면 붙는다** (D-28, 사용자 결정 · 2026-09-22).

    **~~앞 판은 「그 프로젝트에서의 업무 배정 권한」을 열쇠로 세웠다 (D-12)~~.** 그 열쇠는
    **프로젝트 축의 판정**이었고, **조직 축 권한으로 배정하는 팀장이 거기서 탈락했다** — 그러면
    받는 사람은 **일은 맡았는데 그 일이 도는 프로젝트를 못 본다.** D-11 이 고치려던 증상이
    그대로 남는 자리라 **게이트를 뺐다.**

    지호는 이 프로젝트의 **참여자**다 — 관리 권한이 없고(`may_manage=False`), 그 프로젝트에서
    일을 올릴 자격도 없다. **조직 축의 배정 권한으로 배정은 된다.** 이제 **그것으로 충분하다**:
    배정이 성립했으므로 **더 물을 것이 없다.**

    **함께 움직이지 않는 것을 같은 테스트에서 잰다** — 「그 프로젝트에 업무를 **올리는**」 거절은
    **그대로 산다**(L-03). 게이트를 둘 다 뺀 것이 아니다.
    """
    project_id = _project(client)
    client.post(f"/api/projects/{project_id}/members", headers=YUNA, json={"member_id": "jiho"})
    assert client.get(f"/api/projects/{project_id}", headers=JIHO).json()["may_manage"] is False
    # **L-03 — 올리는 문은 그대로 잠겨 있다.** 초대의 게이트를 뺀 것이 이 게이트를 열지 않았다.
    planned = client.post(f"/api/projects/{project_id}/tasks", headers=JIHO, json={"title": "썸네일 제작"})
    assert planned.status_code == 422, planned.text
    # **어느 문이 거절했는지까지 잰다** — 이 라우트는 제목·일정 검증으로도 422 를 낸다. 사유를 안 재면
    # 거절 이유가 갈리는 날 그대로 초록이면서 다른 것을 재게 된다 (문구 출처: `assignments.py`).
    assert "올릴 수 있는 자격" in planned.text, planned.text
    assert _selector(client, MINA) == set()

    task = client.post(f"/api/projects/{project_id}/tasks", headers=YUNA, json={"title": "썸네일 제작"}).json()
    handed = client.post(
        f"/api/tasks/{task['task_id']}/reassign",
        headers=JIHO,
        json={"expected_version": task["version"], "assignee_id": "mina"},
    )
    # **배정은 된다** — 조직 축의 배정 권한이 답한다. 그리고 **그 성립이 곧 초대의 조건**이다.
    assert handed.status_code == 200, handed.text

    # **L-01 — 붙는다.** 배정한 사람이 그 프로젝트에서 배정할 자격이 있는지는 **묻지 않았다.**
    assert _selector(client, MINA) == {"한빛 통합 마케팅"}
    # **L-02 — 붙는 관계는 `참여` 그대로다.** 게이트를 뺀 것이 관계 종류를 올리지 않는다.
    assert ("mina", "member", None) in _history(client, project_id)


def test_the_person_a_gateless_invite_brought_in_is_taken_back_off_when_they_decline(client: TestClient) -> None:
    """**L-05 — 새로 붙은 사람에게도 떼는 경로가 그대로 돈다** (D-13·D-14).

    게이트를 뺀 자리로 붙은 사람이라고 **예외가 되지 않는다.** 거절하면 조건 ①(이 명령이 실제로
    새로 붙였다)·②(그 프로젝트에 다른 활성 업무가 없다)를 **똑같이 보고** 참여가 닫힌다.
    **행은 지워지지 않는다** — 참여 이력에 **닫힌 줄**로 남는다.
    """
    project_id = _project(client)
    client.post(f"/api/projects/{project_id}/members", headers=YUNA, json={"member_id": "jiho"})
    task = client.post(f"/api/projects/{project_id}/tasks", headers=YUNA, json={"title": "썸네일 제작"}).json()
    handed = client.post(
        f"/api/tasks/{task['task_id']}/reassign",
        headers=JIHO,
        json={"expected_version": task["version"], "assignee_id": "mina"},
    )
    assert handed.status_code == 200, handed.text
    assert _selector(client, MINA) == {"한빛 통합 마케팅"}

    declined = client.post(
        f"/api/task-assignments/{handed.json()['assignment_id']}/decline",
        headers=MINA,
        json={"reason": "못 맡습니다"},
    )
    assert declined.status_code == 200, declined.text

    assert _selector(client, MINA) == set()
    assert ("mina", "member", "요청 거절") in _history(client, project_id)


def test_work_with_no_project_invites_nobody_and_is_not_an_error(client: TestClient) -> None:
    """**업무에 프로젝트가 없으면 아무 일도 일어나지 않는다** — 붙일 곳이 없다. 오류가 아니다."""
    sent = _send(client, None, "프로젝트 없는 일", key="noproject-1")

    assert sent["project_id"] is None
    assert _selector(client, HYEON) == set()


def test_direct_assignment_is_not_one_of_the_two_places(client: TestClient) -> None:
    """**직접 배정은 붙는 자리가 아니다** (SPEC-005 §4 세어서 뺀 자리).

    그 경로로 태어난 업무에는 **프로젝트가 없다** — 배정 생성이 `project_id` 를 싣지 않고, 그 입력이
    non-null 프로젝트를 **명시적으로 거부한다.** 걸어도 언제나 무동작이라 「붙는 셋」으로 세면
    닫을 수 없는 테스트가 생긴다.
    """
    _project(client)
    assigned = client.post(
        "/api/tasks/assign",
        headers={**YUNA, "Idempotency-Key": "direct-1"},
        json={"title": "직접 배정한 일", "assignee_id": "hyeon"},
    )
    assert assigned.status_code == 201, assigned.text

    assert assigned.json()["task"]["project_id"] is None
    assert _selector(client, HYEON) == set()


# ---- 자동 해제 — 떼는 자리 넷, 조건 둘 (D-13·D-14·D-15) ----


def test_rejecting_the_request_takes_the_person_back_off(client: TestClient) -> None:
    """**떼는 자리 #1 — 요청 거절.** 붙은 근거가 그 요청인데 **그 근거가 사라졌다** (D-13)."""
    project_id = _project(client)
    request = _send(client, project_id, "인사 자료 정리", key="off-1")
    assert _selector(client, HYEON) == {"한빛 통합 마케팅"}

    rejected = client.post(
        f"/api/work-requests/{request['request_id']}/reject",
        headers=HYEON,
        json={"expected_version": 1, "reason": "지금은 못 맡습니다"},
    )
    assert rejected.status_code == 200, rejected.text

    assert _selector(client, HYEON) == set()
    # **행을 지우지 않는다** — 「붙었다가 거절로 떨어졌다」도 일어난 일이다.
    assert ("hyeon", "member", "요청 거절") in _history(client, project_id)


def test_withdrawing_the_request_takes_the_person_back_off(client: TestClient) -> None:
    """**떼는 자리 #2 — 요청 철회.** 사유가 다른 하나이고, **철회라고 조건이 느슨해지지 않는다.**"""
    project_id = _project(client)
    request = _send(client, project_id, "인사 자료 정리", key="off-2")

    withdrawn = client.post(
        f"/api/work-requests/{request['request_id']}/withdraw", headers=YUNA, json={"expected_version": 1}
    )
    assert withdrawn.status_code == 200, withdrawn.text

    assert _selector(client, HYEON) == set()
    assert ("hyeon", "member", "요청 철회") in _history(client, project_id)


def test_declining_the_assignment_a_request_created_takes_the_person_off_too(client: TestClient) -> None:
    """**떼는 자리 #3 — 배정 거절.** 요청 발송은 요청 원장만이 아니라 **기다리는 배정도 함께** 세운다.

    받는 사람은 요청 거절로도 **배정 거절로도** 같은 일을 끝낼 수 있고, **어느 쪽으로 닫아도 뗀다.**
    여기를 빠뜨리면 **한쪽 문으로 닫았을 때만 사람이 남는** 자리가 생긴다.
    """
    project_id = _project(client)
    request = _send(client, project_id, "인사 자료 정리", key="off-3")
    pending = client.get(f"/api/tasks/{request['task_id']}/assignments", headers=YUNA).json()["pending"]
    assert pending["assignment_kind"] == "request_effect"

    declined = client.post(
        f"/api/task-assignments/{pending['assignment_id']}/decline", headers=HYEON, json={"reason": "못 맡습니다"}
    )
    assert declined.status_code == 200, declined.text

    assert _selector(client, HYEON) == set()
    # **셋째 사유를 만들지 않는다** — 배정 쪽 거절도 「요청 거절」을 그대로 쓴다.
    assert ("hyeon", "member", "요청 거절") in _history(client, project_id)


def test_declining_a_handover_proposal_takes_the_person_off(client: TestClient) -> None:
    """**떼는 자리 #3 의 나머지 절반** — 담당 교체 제안이 세운 배정도 같은 문으로 닫힌다."""
    project_id = _project(client)
    planned = client.post(
        f"/api/projects/{project_id}/tasks", headers=YUNA, json={"title": "플레이스 썸네일 제작"}
    ).json()
    client.post(
        f"/api/tasks/{planned['task_id']}/reassign",
        headers=YUNA,
        json={"expected_version": planned["version"], "assignee_id": "hyeon"},
    )
    assert _selector(client, HYEON) == {"한빛 통합 마케팅"}
    pending = client.get(f"/api/tasks/{planned['task_id']}/assignments", headers=YUNA).json()["pending"]
    assert pending["assignment_kind"] == "direct"

    declined = client.post(
        f"/api/task-assignments/{pending['assignment_id']}/decline", headers=HYEON, json={"reason": "못 맡습니다"}
    )
    assert declined.status_code == 200, declined.text

    assert _selector(client, HYEON) == set()
    assert ("hyeon", "member", "요청 거절") in _history(client, project_id)


def _ax_assign_proposal(client: TestClient, application, *, persona: str, headers: dict, title: str, to: str) -> dict:
    """판단함에 서는 **업무 배정 제안** — 위임 턴이 만드는 그대로다.

    `cancel_assignment` 가 봉투에 서는 **유일한 자리**가 이 제안이다
    (`modules/actions/policy.py:129-136`: `task.assign` · `resolved` · 배정이 `pending` · 보낸 사람).
    """
    conversation = client.post("/api/conversations", headers=headers, json={"title": "배정 철회"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**headers, "Idempotency-Key": f"cancel-surface-{persona}"},
        json={"body": "배정 제안해줘", "context": []},
    )
    assert accepted.status_code == 202, accepted.text
    with make_session_factory(application._settings.database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"])).execution_id
    return application.propose_action(
        application.authenticated_principal(persona),
        execution_id,
        "task.assign",
        "업무 배정 확인",
        {"title": title, "assignee_id": to, "due_date": "2026-09-30"},
    )


def test_the_cancel_assignment_surface_lands_in_the_one_method(stack) -> None:
    """**떼는 자리 #4 의 표면을 실제로 지난다** — `POST /api/action-items/{id}/commands/cancel_assignment`.

    이 판의 전제는 **세 입구(HTTP · MCP · 판단함)가 같은 application 메서드로 모인다**는 것이다
    (SPEC-005 §5). 메서드만 직접 부르는 테스트는 그 전제를 **한 번도 확인하지 않는다** — 명령이
    다른 것을 부르도록 바뀌어도 초록이다. 그래서 그 명령을 **판단함 라우트로** 눌러
    `TaskAssignmentApplication.cancel()` 의 결과가 그대로 나오는지를 여기서 잰다.

    ⚠ **이 표면이 거두는 배정에는 프로젝트가 없다.** 그 봉투는 `task.assign` AX 제안에만 서고,
    그 제안의 초안은 `project_id` 를 **언제나 `None` 으로 못 박는다**
    (`modules/work/drafts.py:35` · `TaskAssignmentInput.read_legacy_empty_project`). 그래서 여기서는
    **명령이 그 메서드로 모이는 것까지**를 증명하고, **프로젝트가 걸린 배정의 철회**는 담당 교체 제안이
    세우는 행이라 아래 테스트가 이어 받는다.
    """
    client, application = stack
    _project(client)
    proposal = _ax_assign_proposal(
        client, application, persona="yuna", headers=YUNA, title="거둘 배정", to="hyeon"
    )
    item = client.get(f"/api/action-items/{proposal['action_id']}", headers=YUNA).json()
    confirmed = client.post(
        f"/api/action-items/{proposal['action_id']}/commands/confirm",
        headers=YUNA,
        json={"expected_version": item["expected_version"], "base_submission_version": 1},
    )
    assert confirmed.status_code == 200, confirmed.text
    [sent] = [
        row
        for row in client.get("/api/task-assignments/sent", headers=YUNA).json()
        if row["task"]["title"] == "거둘 배정"
    ]
    # 신규 경로는 활성 담당을 바로 세우므로(W1), 「수락을 기다리는」 과거 모양을 세워 그 명령이 선다.
    make_assignment_look_pending(application._settings.database_url, sent["assignment_id"])

    waiting = client.get(f"/api/action-items/{proposal['action_id']}", headers=YUNA).json()
    assert [command["id"] for command in waiting["allowed_commands"]] == ["cancel_assignment"]
    cancelled = client.post(
        f"/api/action-items/{proposal['action_id']}/commands/cancel_assignment",
        headers=YUNA,
        json={"expected_version": waiting["expected_version"]},
    )
    assert cancelled.status_code == 200, cancelled.text

    # **그 메서드가 낸 결과 그대로다** — 배정은 철회되고 아무도 들지 않은 업무는 닫힌다.
    [settled] = [
        row
        for row in client.get("/api/task-assignments/sent", headers=YUNA).json()
        if row["assignment_id"] == sent["assignment_id"]
    ]
    assert settled["status"] == "cancelled"
    assert settled["task"]["state"] == "cancelled"
    # 그리고 **그 업무에는 프로젝트가 없다** — 이 표면으로는 자동 해제가 구조적으로 일어날 수 없다.
    assert client.get(f"/api/tasks/{settled['task']['task_id']}", headers=YUNA).json()["project_id"] is None
    assert _selector(client, HYEON) == set()


def test_withdrawing_a_handover_proposal_takes_the_person_off(stack) -> None:
    """**떼는 자리 #4 — 배정 철회.** 붙는 자리 #2 의 나머지 절반이다.

    받는 사람이 답하기 전에 **보낸 쪽이 거두는 길**이고, 그 철회가 거두는 배정을 담당 교체 제안이
    만든다. 거절(#3)만 다루고 철회를 빠뜨리면 **「보냈다 거뒀는데 사람은 프로젝트에 남는」** 자리가
    생긴다 — D-13 이 「거절·철회」라고 둘을 나란히 적은 이유가 그것이다.

    **거는 자리는 application 메서드다** (SPEC-005 §5). 그 명령이 부르는 것은
    `TaskAssignmentApplication.cancel()` 하나이고, **그 표면 연결은 바로 위 테스트가 실제 라우트로
    확인한다** (`platform/action_center.py:619-623`).

    ⚠ **여기서 메서드를 직접 부르는 이유를 감추지 않는다.** 판단함의 `cancel_assignment` 봉투는
    `task.assign` AX 제안에만 서고 그 제안의 업무는 **프로젝트를 가질 수 없다**. 담당 교체 제안이
    세우는 이 배정(프로젝트가 걸린 유일한 `pending` 배정)에는 **오늘 철회 표면이 없다** — 그래서
    이 자리는 메서드로 잰다. 표면이 생기면 이 테스트가 그 표면으로 올라와야 한다.
    """
    client, application = stack
    project_id = _project(client)
    planned = client.post(
        f"/api/projects/{project_id}/tasks", headers=YUNA, json={"title": "플레이스 썸네일 제작"}
    ).json()
    handed = client.post(
        f"/api/tasks/{planned['task_id']}/reassign",
        headers=YUNA,
        json={"expected_version": planned["version"], "assignee_id": "hyeon"},
    ).json()
    assert _selector(client, HYEON) == {"한빛 통합 마케팅"}

    with make_session_factory(application._settings.database_url)() as session:
        application._assignments(session).cancel(
            application.authenticated_principal("yuna"), UUID(handed["assignment_id"])
        )
        session.commit()

    assert _selector(client, HYEON) == set()
    # **셋째 사유를 만들지 않는다** — 배정 쪽 철회도 「요청 철회」를 그대로 쓴다.
    assert ("hyeon", "member", "요청 철회") in _history(client, project_id)


def test_an_original_member_is_not_taken_off_by_their_own_refusal(client: TestClient) -> None:
    """**조건 ① 거짓** — 이 요청이 붙인 것이 아니면 떼지 않는다 (D-14).

    붙이는 명령은 이미 붙어 있으면 새 행을 만들지 않으므로, 「이 요청이 붙였나」의 구분은
    **붙이는 그 순간에만** 설 수 있다.
    """
    project_id = _project(client)
    client.post(f"/api/projects/{project_id}/members", headers=YUNA, json={"member_id": "hyeon"})
    request = _send(client, project_id, "인사 자료 정리", key="cond1-1")

    client.post(
        f"/api/work-requests/{request['request_id']}/reject",
        headers=HYEON,
        json={"expected_version": 1, "reason": "지금은 못 맡습니다"},
    )

    assert _selector(client, HYEON) == {"한빛 통합 마케팅"}
    assert ("hyeon", "member", None) in _history(client, project_id)


def test_someone_still_holding_other_work_here_stays_on(client: TestClient) -> None:
    """**조건 ② 거짓** — 하나만 거절돼도 그 사람은 **아직 그 프로젝트 일을 하고 있다** (D-14 · S-7)."""
    project_id = _project(client)
    first = _send(client, project_id, "인사 자료 정리", key="cond2-1")
    _send(client, project_id, "채용 공고 검토", key="cond2-2")

    client.post(
        f"/api/work-requests/{first['request_id']}/reject",
        headers=HYEON,
        json={"expected_version": 1, "reason": "이건 못 맡습니다"},
    )

    assert _selector(client, HYEON) == {"한빛 통합 마케팅"}
    assert ("hyeon", "member", None) in _history(client, project_id)


def test_cancelling_after_acceptance_never_takes_anyone_off(client: TestClient) -> None:
    """**수락 뒤 취소로는 안 뗀다** (D-15).

    가르는 선은 **「그 일이 있었나」**다. 거절·철회는 없었다는 뜻이고 수락 뒤 종료는 있었다는 뜻이다.
    완료·취소로 뗀다면 **끝낼수록 사라지는** 화면이 된다.
    """
    project_id = _project(client)
    planned = client.post(
        f"/api/projects/{project_id}/tasks", headers=YUNA, json={"title": "플레이스 썸네일 제작"}
    ).json()
    client.post(
        f"/api/tasks/{planned['task_id']}/reassign",
        headers=YUNA,
        json={"expected_version": planned["version"], "assignee_id": "hyeon"},
    )
    [item] = [row for row in client.get("/api/action-items", headers=HYEON).json() if row["subject"] == "플레이스 썸네일 제작"]
    accepted = client.post(
        f"/api/action-items/{item['action_item_id']}/commands/accept",
        headers=HYEON,
        json={"expected_version": item["expected_version"]},
    )
    assert accepted.status_code == 200, accepted.text

    current = client.get(f"/api/tasks/{planned['task_id']}", headers=HYEON).json()
    cancelled = client.post(
        f"/api/tasks/{planned['task_id']}/cancel",
        headers=HYEON,
        json={"expected_version": current["version"], "reason": "상황이 바뀌었습니다"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["state"] == "cancelled"

    assert _selector(client, HYEON) == {"한빛 통합 마케팅"}
    assert ("hyeon", "member", None) in _history(client, project_id)


def test_no_new_rejection_branch_appears_on_the_invite_path(client: TestClient) -> None:
    """**새 거절 갈래가 0건이다** (D-28). 배정 자체가 막히면 **기존 배정 거절이 먼저 난다.**

    **게이트가 없어진 뒤에 더 참이 됐다** — 「배정은 되는데 초대가 막히는」 경우가 아예 없으므로,
    남는 거절은 **배정 자체의 기존 거절 하나**다 (SPEC-005 §4 에러 · L-04).
    """
    project_id = _project(client, by=JIHO)
    planned = client.post(
        f"/api/projects/{project_id}/tasks", headers=JIHO, json={"title": "썸네일 제작"}
    ).json()

    # 지호의 배정 범위 밖 사람이다 — 프로젝트에도 없다. 막히는 것은 **배정**이고 초대가 아니다.
    refused = client.post(
        f"/api/tasks/{planned['task_id']}/reassign",
        headers=JIHO,
        json={"expected_version": planned["version"], "assignee_id": "hyeon"},
    )
    assert refused.status_code == 422, refused.text
    assert "assignment scope" in refused.text
    assert _selector(client, HYEON) == set()


# ---- 손자 프로젝트 종속 — 자손 전체 (D-19) ----


def _own(client: TestClient, title: str, *, by: dict, **fields) -> dict:
    made = client.post("/api/tasks", headers=by, json={"title": title, **fields})
    assert made.status_code == 201, made.text
    return made.json()


def _handed_down(client: TestClient, title: str, parent_id: str, *, to: str, who: dict, key: str) -> str:
    """**요청을 한 번 주고받아 한 층을 더 쌓는다.**

    자기 직접 작업은 중심 업무의 바로 아래에만 둘 수 있어서(정책 V-8) 깊은 트리는 **사람이 바뀌며**
    자란다 — 그것이 이 저장소에서 손자·증손자가 실제로 생기는 모양이다.
    """
    sent = client.post(
        "/api/work-requests",
        headers={**YUNA, "Idempotency-Key": key},
        json={"title": title, "assignee_id": to, "parent_task_id": parent_id},
    )
    assert sent.status_code == 201, sent.text
    [item] = [row for row in client.get("/api/action-items", headers=who).json() if row["subject"] == title]
    accepted = client.post(
        f"/api/action-items/{item['action_item_id']}/commands/accept",
        headers=who,
        json={"expected_version": item["expected_version"]},
    )
    assert accepted.status_code == 200, accepted.text
    return sent.json()["task_id"]


def _three_generations(client: TestClient, project_id: str) -> dict[str, str]:
    """상위 → 자식(현우) → 손자(현우의 직접 작업) → 증손자(미나) — **네 층**."""
    top = _own(client, "상위", by=MINA, project_id=project_id)["task_id"]
    child = _handed_down(client, "자식", top, to="hyeon", who=HYEON, key="tree-1")
    grandchild = client.post("/api/tasks", headers=HYEON, json={"title": "손자", "parent_task_id": child})
    assert grandchild.status_code == 201, grandchild.text
    grandchild_id = grandchild.json()["task_id"]
    great = _handed_down(client, "증손자", grandchild_id, to="mina", who=MINA, key="tree-2")
    return {"상위": top, "자식": child, "손자": grandchild_id, "증손자": great}


def test_moving_a_parent_carries_every_descendant_not_just_its_children(client: TestClient) -> None:
    """**상위를 옮기면 자식도 손자도 증손자도 따라간다** (D-19 · 어긋남 ③).

    직속만 옮기면 손자가 옛 프로젝트에 남고, **선행은 같은 프로젝트 안에서만 성립하므로**
    저장소가 강제하는 불변식이 데이터 안에서 깨진다.
    """
    here = _project(client, name="옛 프로젝트")
    there = _project(client, name="새 프로젝트")
    for project_id in (here, there):
        client.post(f"/api/projects/{project_id}/members", headers=YUNA, json={"member_id": "mina"})
    tree = _three_generations(client, here)

    current = client.get(f"/api/tasks/{tree['상위']}", headers=MINA).json()
    moved = client.patch(
        f"/api/tasks/{tree['상위']}",
        headers=MINA,
        json={"expected_version": current["version"], "project_id": there},
    )
    assert moved.status_code == 200, moved.text

    # **옛 프로젝트에 그 트리의 업무가 한 건도 남지 않는다.**
    assert client.get(f"/api/projects/{here}", headers=YUNA).json()["tasks"] == []
    assert {row["title"] for row in client.get(f"/api/projects/{there}", headers=YUNA).json()["tasks"]} == {
        "상위", "자식", "손자", "증손자",
    }


def test_a_locked_grandchild_refuses_the_whole_move_and_nothing_budges(client: TestClient) -> None:
    """**자손 중 하나라도 남은 선행을 들고 있으면 이동 전체가 거절된다** (D-19).

    직속만 보면 **잠긴 프로젝트의 손자가 게이트를 우회한다.** 거절은 **이미 있는 코드**다 —
    `WORK_PROJECT_LOCKED_BY_PREDECESSORS`(409)이고 새 이름을 짓지 않는다. 전이 게이트
    `WORK_PREDECESSORS_UNFINISHED` 는 **다른 자리**라 이 판이 건드리지 않는다.
    """
    here = _project(client, name="옛 프로젝트")
    there = _project(client, name="새 프로젝트")
    for project_id in (here, there):
        client.post(f"/api/projects/{project_id}/members", headers=YUNA, json={"member_id": "mina"})
    tree = _three_generations(client, here)
    earlier = _own(client, "선행", by=HYEON, project_id=here)["task_id"]
    # **손자에만** 선행을 건다 — 직속만 보는 게이트는 이것을 못 본다.
    grandchild = client.get(f"/api/tasks/{tree['손자']}", headers=HYEON).json()
    locked = client.patch(
        f"/api/tasks/{tree['손자']}",
        headers=HYEON,
        json={"expected_version": grandchild["version"], "preceding_task_ids": [earlier]},
    )
    assert locked.status_code == 200, locked.text

    current = client.get(f"/api/tasks/{tree['상위']}", headers=MINA).json()
    refused = client.patch(
        f"/api/tasks/{tree['상위']}",
        headers=MINA,
        json={"expected_version": current["version"], "project_id": there},
    )

    assert refused.status_code == 409, refused.text
    # 이 저장소에서 그 코드의 표면은 **거절 메시지**다 — `test_task_predecessors.py` 가 같은 문장으로
    # 이 갈래를 붙잡는다. **전이 게이트의 말("선행업무가 끝나지 않았습니다")이 아니다.**
    assert "선행업무를 먼저 비워야" in refused.json()["detail"]
    assert "손자" in refused.json()["detail"], "게이트가 자손까지 보지 않았습니다"
    # **아무것도 움직이지 않았다** — 부분 이동이 없다.
    assert client.get(f"/api/projects/{there}", headers=YUNA).json()["tasks"] == []
    assert {row["title"] for row in client.get(f"/api/projects/{here}", headers=YUNA).json()["tasks"]} == {
        "상위", "자식", "손자", "증손자", "선행",
    }


def _edges(client: TestClient, project_id: str) -> set[tuple[str, str]]:
    """그 프로젝트 화면이 그리는 **선행 변(邊)** 전부 — `(업무, 그 업무의 선행)` 쌍이다.

    이동 뒤의 불변식을 **값이 있는 집합으로** 세기 위한 관측면이고, 화면이 간트 연결선을 긋는
    바로 그 배열(`tasks[].preceding_task_ids`)에서 나온다.
    """
    rows = client.get(f"/api/projects/{project_id}", headers=YUNA).json()["tasks"]
    return {(row["task_id"], predecessor) for row in rows for predecessor in row["preceding_task_ids"]}


def _task_ids(client: TestClient, project_id: str) -> set[str]:
    return {row["task_id"] for row in client.get(f"/api/projects/{project_id}", headers=YUNA).json()["tasks"]}


def test_after_the_move_no_descendant_keeps_a_predecessor_in_the_old_project(client: TestClient) -> None:
    """이동 뒤 **어느 자손의 선행도 옛 프로젝트에 남아 있지 않다** (SPEC-005 §6 손자 종속 마지막 줄).

    ⚠ **비지 않은 집합으로 센다.** 선행이 하나도 없는 트리를 옮겨 놓고 `set(...) <= known` 을 돌리면
    그것은 **공집합 비교라 언제나 참**이다 — 게이트를 통째로 지워도 초록이고, 아무것도 증명하지 않는다.
    「구조적으로 자명하다」면 **그 자명함이 깨지는 경로**를 테스트가 지켜야 한다. 그 경로는 하나다:
    **게이트가 자손을 안 보게 되는 것.** 그러면 선행을 든 손자가 딸려 가고, 그 선행은 옛 프로젝트에
    남아 **선행은 같은 프로젝트 안에서만 성립한다**는 불변식이 데이터 안에서 깨진다.

    그래서 이 테스트는 **실제로 선행을 든 손자**를 세우고 세 가지를 차례로 잰다:
    ① 그 선행이 **이동을 막는다**(`WORK_PROJECT_LOCKED_BY_PREDECESSORS`) ·
    ② 풀면 **손자까지 따라온다** · ③ 옮겨진 뒤의 선행 관계는 **새 프로젝트 안에서만** 서고
    **옛 프로젝트를 가리키는 변이 0건**이다 — 그 셈을 **변이 실재하는 상태**에서 한다.
    """
    here = _project(client, name="옛 프로젝트")
    there = _project(client, name="새 프로젝트")
    for project_id in (here, there):
        for member_id in ("mina", "hyeon"):
            client.post(f"/api/projects/{project_id}/members", headers=YUNA, json={"member_id": member_id})
    tree = _three_generations(client, here)
    earlier = _own(client, "선행", by=HYEON, project_id=here)["task_id"]

    # **손자가 실제로 선행을 든다** — 여기서부터 관측 집합이 비지 않는다.
    grandchild = client.get(f"/api/tasks/{tree['손자']}", headers=HYEON).json()
    linked = client.patch(
        f"/api/tasks/{tree['손자']}",
        headers=HYEON,
        json={"expected_version": grandchild["version"], "preceding_task_ids": [earlier]},
    )
    assert linked.status_code == 200, linked.text
    assert (tree["손자"], earlier) in _edges(client, here), "선행이 화면에 서지 않으면 아래가 공허해집니다"

    # ① 그 선행이 **이동 전체를 막는다.** 직속만 보는 게이트는 이 손자를 못 본다.
    current = client.get(f"/api/tasks/{tree['상위']}", headers=MINA).json()
    refused = client.patch(
        f"/api/tasks/{tree['상위']}",
        headers=MINA,
        json={"expected_version": current["version"], "project_id": there},
    )
    assert refused.status_code == 409, refused.text
    assert "선행업무를 먼저 비워야" in refused.json()["detail"]
    assert _task_ids(client, there) == set(), "거절인데 무언가 움직였습니다"

    # ② **선행을 풀면 옮길 수 있고, 손자까지 따라온다.**
    grandchild = client.get(f"/api/tasks/{tree['손자']}", headers=HYEON).json()
    assert client.patch(
        f"/api/tasks/{tree['손자']}",
        headers=HYEON,
        json={"expected_version": grandchild["version"], "preceding_task_ids": []},
    ).status_code == 200
    current = client.get(f"/api/tasks/{tree['상위']}", headers=MINA).json()
    moved = client.patch(
        f"/api/tasks/{tree['상위']}",
        headers=MINA,
        json={"expected_version": current["version"], "project_id": there},
    )
    assert moved.status_code == 200, moved.text
    assert set(tree.values()) <= _task_ids(client, there), "자손 하나가 옛 프로젝트에 남았습니다"
    # 옛 프로젝트에는 **선행 업무만** 남는다 — 그 업무가 아래 셈의 「옛 프로젝트 쪽」이다.
    left_behind = _task_ids(client, here)
    assert left_behind == {earlier}

    # ③ **옛 프로젝트를 가리키는 변이 0건인지를, 변이 실재하는 상태에서 센다.**
    # 새 프로젝트 안에서 선행을 다시 이어 관측 집합을 비지 않게 만든다 — 그러지 않으면 아래 두 줄은
    # 공집합끼리의 비교가 되어 게이트가 없어져도 통과한다.
    partner = _own(client, "새 프로젝트의 선행", by=HYEON, project_id=there)["task_id"]
    grandchild = client.get(f"/api/tasks/{tree['손자']}", headers=HYEON).json()
    assert client.patch(
        f"/api/tasks/{tree['손자']}",
        headers=HYEON,
        json={"expected_version": grandchild["version"], "preceding_task_ids": [partner]},
    ).status_code == 200

    edges = _edges(client, there)
    assert edges == {(tree["손자"], partner)}, "관측 집합이 비면 다음 단언이 아무것도 증명하지 않습니다"
    known = _task_ids(client, there)
    assert {predecessor for _, predecessor in edges} <= known, "선행이 다른 프로젝트에 남았습니다"
    assert {predecessor for _, predecessor in edges} & left_behind == set()
    # 뗀 선행 행은 남지만 **활성이 아니라 화면에 서지 않는다** — 옛 관계가 되살아나지 않는다.
    assert earlier not in {predecessor for _, predecessor in edges}
    assert tree["증손자"] in known
