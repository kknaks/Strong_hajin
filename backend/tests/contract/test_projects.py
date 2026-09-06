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
def client(tmp_path) -> TestClient:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    return TestClient(create_app(Settings(RuntimeProfile.TEST, database_url)))


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
