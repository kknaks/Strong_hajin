"""프로젝트는 조직 단위와 나란한 두 번째 축이다 — 부서를 가로질러 묶이고, 그만큼만 열린다.

이 조직의 일은 고객사 프로젝트 단위로 움직이고 부서를 가로지른다. 조직 축 하나로는 그 관계를 표현할 수 없어서
프로젝트 담당자가 자기 프로젝트의 업무를 보지 못했다. 여기서 고정하는 것은 그 반대편도 함께다: 프로젝트에
붙었다고 그 사람의 부서 밖 다른 일까지 열리지는 않는다.
"""
from fastapi.testclient import TestClient
import pytest

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database

YUNA = {"X-Demo-Persona": "yuna"}      # 대표
JIHO = {"X-Demo-Persona": "jiho"}      # 제품팀장
MINA = {"X-Demo-Persona": "mina"}      # 제품팀 구성원
HYEON = {"X-Demo-Persona": "hyeon"}    # 인사 — 제품팀 밖


@pytest.fixture()
def settings(tmp_path) -> Settings:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    return Settings(RuntimeProfile.TEST, database_url)


@pytest.fixture()
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


def _project(client: TestClient, *, name: str = "한빛 통합 마케팅", unit: str = "product", by: dict | None = None) -> dict:
    made = client.post(
        "/api/projects",
        headers=by or JIHO,
        json={"name": name, "organization_unit_id": unit, "description": "부서를 가로지르는 한 건"},
    )
    assert made.status_code == 201, made.text
    return made.json()


def test_work_without_a_project_is_the_ordinary_case(client: TestClient) -> None:
    """프로젝트를 말하지 않은 업무가 지금까지와 똑같이 동작한다. 프로젝트는 더해지는 것이지 요구되는 것이 아니다."""
    made = client.post("/api/tasks", headers=MINA, json={"title": "비품대장 정리"})
    assert made.status_code == 201, made.text
    assert made.json()["project_id"] is None
    assert client.get(f"/api/tasks/{made.json()['task_id']}", headers=MINA).json()["project_id"] is None


def test_a_project_member_from_another_unit_reads_that_project_and_nothing_more(client: TestClient) -> None:
    project = _project(client)
    # 프로젝트에 붙기 전: 현우는 제품팀 사람이 아니고, 이 프로젝트가 있다는 것도 모른다.
    assert client.get("/api/projects", headers=HYEON).json() == []
    assert client.get(f"/api/projects/{project['project_id']}", headers=HYEON).status_code == 404

    inside = client.post("/api/tasks", headers=MINA, json={"title": "홈페이지 디자인 기획", "project_id": project["project_id"]})
    assert inside.status_code == 201, inside.text
    outside = client.post("/api/tasks", headers=MINA, json={"title": "내 개인 업무"})
    assert outside.status_code == 201

    # 붙기 전에는 둘 다 못 읽는다.
    assert client.get(f"/api/tasks/{inside.json()['task_id']}", headers=HYEON).status_code == 404

    joined = client.post(
        f"/api/projects/{project['project_id']}/members", headers=JIHO, json={"member_id": "hyeon", "kind": "member"}
    )
    assert joined.status_code == 201, joined.text
    # 유효기간을 말하지 않았으므로 비어 있다 — 지금부터 계속이라는 뜻이다.
    assert joined.json()["valid_from"] is None and joined.json()["valid_until"] is None

    # 붙은 뒤: 그 프로젝트의 업무는 읽고, 같은 사람의 프로젝트 밖 업무는 여전히 못 읽는다.
    assert client.get(f"/api/tasks/{inside.json()['task_id']}", headers=HYEON).status_code == 200
    assert client.get(f"/api/tasks/{outside.json()['task_id']}", headers=HYEON).status_code == 404
    assert {row["name"] for row in client.get("/api/projects", headers=HYEON).json()} == {"한빛 통합 마케팅"}


def test_the_grant_says_which_rule_made_it_and_ends_when_the_assignment_does(client: TestClient) -> None:
    """프로젝트 권한도 보직 권한과 같은 길로 생긴다 — 규칙이 만들고, 배정이 끝나면 함께 끝난다."""
    project = _project(client)
    task = client.post("/api/tasks", headers=MINA, json={"title": "브랜드블로그 키워드 선정", "project_id": project["project_id"]})
    client.post(f"/api/projects/{project['project_id']}/members", headers=JIHO, json={"member_id": "hyeon"})

    profile = client.get("/api/organization/me", headers=HYEON).json()
    [grant] = [row for row in profile["grants"] if row["scope_kind"] == "project"]
    assert grant["scope_ref"] == project["project_id"]
    assert grant["origin_rule_id"] == "standard:project_assignment:project:role:project-participant"
    assert client.get(f"/api/tasks/{task.json()['task_id']}", headers=HYEON).status_code == 200

    released = client.delete(f"/api/projects/{project['project_id']}/members/hyeon", headers=JIHO)
    assert released.status_code == 204, released.text
    # 프로젝트에서 빠지면 그 프로젝트로 얻었던 것도 끝난다. 조직 안에서 갖던 것은 그대로다.
    after = client.get("/api/organization/me", headers=HYEON).json()
    assert not [row for row in after["grants"] if row["scope_kind"] == "project"]
    assert after["capabilities"], "조직 안에서 갖던 권한까지 사라졌습니다"
    assert client.get(f"/api/tasks/{task.json()['task_id']}", headers=HYEON).status_code == 404


def test_work_can_join_a_project_later_and_its_parts_come_along(client: TestClient) -> None:
    """일이 먼저 있고 프로젝트가 나중에 생기는 것이 보통이다. 상위 업무가 옮겨 가면 그 안의 일도 함께 간다."""
    parent = client.post("/api/tasks", headers=MINA, json={"title": "한빛 9월 통합 마케팅"}).json()
    child = client.post(
        "/api/tasks", headers=MINA, json={"title": "홈페이지 디자인 기획", "parent_task_id": parent["task_id"]}
    ).json()
    assert parent["project_id"] is None and child["project_id"] is None

    project = _project(client)
    # 하위 업무가 붙으면서 상위 업무의 회차가 올라간다. 지금 값으로 답한다.
    current = client.get(f"/api/tasks/{parent['task_id']}", headers=MINA).json()
    moved = client.patch(
        f"/api/tasks/{parent['task_id']}",
        headers=MINA,
        json={"expected_version": current["version"], "project_id": project["project_id"]},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["project_id"] == project["project_id"]
    assert client.get(f"/api/tasks/{child['task_id']}", headers=MINA).json()["project_id"] == project["project_id"]

    # 하위 업무만 따로 옮기지는 못한다. 그러면 한 일의 부분들이 서로 다른 프로젝트에 흩어진다.
    alone = client.patch(
        f"/api/tasks/{child['task_id']}",
        headers=MINA,
        json={"expected_version": child["version"], "clear_project": True},
    )
    assert alone.status_code == 422, alone.text


def test_a_project_member_sees_the_parts_they_could_already_open(client: TestClient) -> None:
    """추적은 부분을 보는 일이다. 이미 따로 열 수 있는 하위 업무를 목록에서만 감추면 추적이 되지 않는다.

    감추던 것은 요청자·배정자 관계를 위한 규칙이었다. 같은 프로젝트의 하위 업무는 그 자체로 읽히므로 여기서
    빼면 접근만 남고 쓸모가 사라진다. 읽을 수 없는 부분은 여전히 이름도 개수도 나오지 않는다.
    """
    project = _project(client)
    parent = client.post(
        "/api/tasks", headers=MINA, json={"title": "한빛 9월 통합 마케팅", "project_id": project["project_id"]}
    ).json()
    client.post("/api/tasks", headers=MINA, json={"title": "홈페이지 디자인 기획", "parent_task_id": parent["task_id"]})
    client.post(f"/api/projects/{project['project_id']}/members", headers=JIHO, json={"member_id": "hyeon"})

    seen = client.get(f"/api/tasks/{parent['task_id']}", headers=HYEON)
    assert seen.status_code == 200, seen.text
    body = seen.json()
    assert body["access"] == "read_only"
    assert [child["title"] for child in body["children"]] == ["홈페이지 디자인 기획"]
    assert body["child_progress"] == {"done": 0, "total": 1}
    # 안을 열어 준 것은 아니다: 그 사람의 작업 공간인 체크리스트는 여전히 오지 않는다.
    assert "checklist" not in body


def test_work_can_sit_on_a_project_before_anyone_holds_it(client: TestClient) -> None:
    """무슨 일이 있는지와 누가 하는지는 다른 질문이다. 계획을 먼저 펼치고 사람을 나중에 붙인다.

    배정 행이 하나도 없다는 것이 곧 `담당자 미정`이다. `미정`이라는 이름의 가짜 담당자를 만들지 않는다.
    """
    project = _project(client)
    client.post(f"/api/projects/{project['project_id']}/members", headers=JIHO, json={"member_id": "jiho", "kind": "lead"})

    planned = client.post(
        f"/api/projects/{project['project_id']}/tasks",
        headers=JIHO,
        json={"title": "플레이스 썸네일 이미지 제작", "due_date": "2026-09-30"},
    )
    assert planned.status_code == 201, planned.text
    task_id = planned.json()["task_id"]
    assert planned.json()["project_id"] == project["project_id"]

    detail = client.get(f"/api/tasks/{task_id}", headers=JIHO).json()
    assert detail["assignee"] is None, "아무도 들지 않은 일에 담당자가 생겼습니다"
    # 아무도 들고 있지 않으므로 누구의 내 업무에도 들어가지 않는다.
    assert task_id not in {row["task_id"] for row in client.get("/api/my-work", headers=JIHO).json()}
    # 프로젝트에는 있다 — 계획은 거기에 있다.
    assert task_id in {row["task_id"] for row in client.get(f"/api/projects/{project['project_id']}", headers=JIHO).json()["tasks"]}

    # 담당이 사람을 붙인다. 옮겨 올 자리가 없다는 이유로 거절하지 않는다.
    handed = client.post(
        f"/api/tasks/{task_id}/reassign",
        headers=JIHO,
        json={"expected_version": detail["version"], "assignee_id": "mina"},
    )
    assert handed.status_code == 200, handed.text
    # 그래도 그 사람이 수락해야 자기 업무가 된다.
    assert handed.json()["status"] == "pending"
    assert task_id not in {row["task_id"] for row in client.get("/api/my-work", headers=MINA).json()}
    # 붙였다는 사실이 그 사람의 판단함에 닿는다. 닿지 않으면 수락할 방법이 없다.
    [item] = [row for row in client.get("/api/action-items", headers=MINA).json() if row["subject"] == "플레이스 썸네일 이미지 제작"]
    accepted = client.post(
        f"/api/action-items/{item['action_item_id']}/commands/accept",
        headers=MINA,
        json={"expected_version": item["expected_version"]},
    )
    assert accepted.status_code == 200, accepted.text
    assert task_id in {row["task_id"] for row in client.get("/api/my-work", headers=MINA).json()}


def test_a_project_lead_may_put_work_on_someone_from_another_unit(client: TestClient) -> None:
    """프로젝트 담당은 그 프로젝트 안에서 일을 만들 수 있어야 한다. 그러지 못하면 프로젝트는 같이 보는 묶음에 그친다."""
    project = _project(client)
    client.post(f"/api/projects/{project['project_id']}/members", headers=JIHO, json={"member_id": "jiho", "kind": "lead"})
    # 현우는 제품팀 밖 사람이다. 조직 축으로는 지호의 배정 범위에 없다.
    assert "hyeon" not in {row["id"] for row in client.get("/api/task-assignment-candidates", headers=JIHO).json()}

    client.post(f"/api/projects/{project['project_id']}/members", headers=JIHO, json={"member_id": "hyeon"})
    assert "hyeon" in {row["id"] for row in client.get("/api/task-assignment-candidates", headers=JIHO).json()}

    planned = client.post(
        f"/api/projects/{project['project_id']}/tasks", headers=JIHO, json={"title": "썸네일 제작"}
    ).json()
    handed = client.post(
        f"/api/tasks/{planned['task_id']}/reassign",
        headers=JIHO,
        json={"expected_version": planned["version"], "assignee_id": "hyeon"},
    )
    assert handed.status_code == 200, handed.text

    # 프로젝트 참여자는 배정하지 못한다. 담당과 참여가 다른 말이어야 하는 이유다.
    refused = client.post(
        f"/api/projects/{project['project_id']}/tasks", headers=HYEON, json={"title": "참여자가 올리는 일"}
    )
    assert refused.status_code == 422, refused.text


def test_the_screen_is_told_what_it_may_do_rather_than_guessing(client: TestClient) -> None:
    """화면이 권한을 추측해 버튼을 그리면 눌러야 아는 거절이 된다. 서버가 먼저 말한다."""
    project = _project(client)
    client.post(f"/api/projects/{project['project_id']}/members", headers=JIHO, json={"member_id": "hyeon"})

    assert client.get(f"/api/projects/{project['project_id']}", headers=JIHO).json()["may_manage"] is True
    # 현우는 이 프로젝트에 붙어 있어 읽지만, 사람을 붙이고 떼는 것은 그 프로젝트를 소유한 조직의 일이다.
    outside = client.get(f"/api/projects/{project['project_id']}", headers=HYEON).json()
    assert outside["may_manage"] is False
    refused = client.post(f"/api/projects/{project['project_id']}/members", headers=HYEON, json={"member_id": "mina"})
    assert refused.status_code == 403, refused.text


def test_a_project_cannot_be_a_back_door_into_work_that_is_not_in_it(client: TestClient) -> None:
    """읽을 수 없는 프로젝트에 일을 밀어 넣어 그 프로젝트 사람들에게 보이게 할 수 없다."""
    project = _project(client)
    # 현우는 이 프로젝트를 읽을 수 없으므로, 자기 업무를 여기에 매달 수도 없다.
    refused = client.post("/api/tasks", headers=HYEON, json={"title": "몰래 넣기", "project_id": project["project_id"]})
    assert refused.status_code == 404, refused.text


def test_a_lead_may_only_open_a_project_where_their_authority_reaches(client: TestClient) -> None:
    """프로젝트를 여는 권한은 그것을 소유할 조직에서 나온다. 제품팀장이 피플팀의 프로젝트를 열지 못한다."""
    refused = client.post("/api/projects", headers=JIHO, json={"name": "인사 개편", "organization_unit_id": "people"})
    assert refused.status_code == 403, refused.text


def test_the_person_who_may_sign_in_as_the_organization_sees_every_project(client: TestClient) -> None:
    _project(client)
    _project(client, name="사내 인사 시스템 개편", unit="people", by=YUNA)
    assert {row["name"] for row in client.get("/api/projects", headers=YUNA).json()} == {
        "한빛 통합 마케팅",
        "사내 인사 시스템 개편",
    }


def test_what_a_person_holds_is_not_what_they_may_read(client: TestClient, settings: Settings) -> None:
    """`내 업무`와 `조직의 업무`는 다른 질문이고, AX의 도구가 그 둘을 구분해 답한다.

    프로젝트로 남의 업무를 읽는 사람에게 `오늘 할 일`을 물었을 때 남의 일이 자기 일로 섞여 나오면 답이 틀린
    것이다 — 읽을 수 있다는 것과 내가 해야 한다는 것은 같지 않다.
    """
    from ax_workspace.entrypoints.mcp import McpReportsFacade

    project = _project(client)
    theirs = client.post(
        "/api/tasks", headers=JIHO, json={"title": "지호가 든 프로젝트 업무", "project_id": project["project_id"]}
    ).json()
    client.post(f"/api/projects/{project['project_id']}/members", headers=JIHO, json={"member_id": "hyeon"})
    mine = client.post("/api/tasks", headers=HYEON, json={"title": "현이 든 업무"}).json()

    facade = McpReportsFacade(settings, "hyeon")

    assert {row["task_id"] for row in facade.list_tasks()} == {mine["task_id"]}
    # 넓히는 길은 남아 있고, 넓혔을 때만 넓어진다.
    readable = {row["task_id"] for row in facade.list_tasks(mine=False)}
    assert theirs["task_id"] in readable and mine["task_id"] in readable
