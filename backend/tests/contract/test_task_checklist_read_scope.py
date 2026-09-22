"""**체크리스트·업무 내용의 읽기 범위** — 프로젝트가 읽기를 열고, 담당이 쓰기를 쥔다
(SPEC-005 §4 「체크리스트·업무 내용의 읽기 범위」 · D-29).

전에는 **활성 배정을 쥔 사람만** 체크리스트를 봤다. 같은 프로젝트에 붙어 함께 일하는 사람이 —
**리드조차** — 남의 업무를 열면 항목이 한 줄도 안 왔다. 프로젝트 화면의 우 레일이 그 자리에서
비었고, 그것이 「같이 하는 일」이라는 말과 어긋났다.

**읽기만 넓어진다.** 항목을 더하고 체크하고 지우고 순서를 바꾸는 것은 **그대로 활성 담당자**다.
그래서 **접근 값(`access`)을 셋으로 늘리지 않고**(그 값은 이미 밖으로 나가는 계약이다) **체크리스트를
싣는 조건만 따로 둔다**(SPEC-005 §4 ⓑ). 그 결과 **`read_only` 인데 항목이 실리는 조합**이 생기고,
**그것이 정상이다** — 접근 값은 이제 **쓰기 범위**만 뜻한다.

배역은 셋이다: 업무를 쥔 **민아**, 그 프로젝트의 **리드 지호**(만든 사람), **참여자 민석**.
그리고 프로젝트 **밖에서** 같은 업무를 읽는 **유나**(대표 — 조직 축으로 읽는다)가 경계를 그린다.
"""
from fastapi.testclient import TestClient
import pytest

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database

YUNA = {"X-Demo-Persona": "yuna"}          # 대표 — 조직 축으로 읽고, 이 프로젝트에는 안 붙는다
JIHO = {"X-Demo-Persona": "jiho"}          # 제품팀장 — 이 프로젝트를 만들었으니 **리드**다
MINA = {"X-Demo-Persona": "mina"}          # 제품팀 구성원 — **업무를 쥔 사람**
MINSEOK = {"X-Demo-Persona": "minseok"}    # 재무 구성원 — 이 프로젝트의 **참여자**


@pytest.fixture()
def client(tmp_path) -> TestClient:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    return TestClient(create_app(Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))))


@pytest.fixture()
def scene(client: TestClient) -> dict:
    """지호가 만든 프로젝트에 민석이 참여로 붙고, 민아가 그 안의 업무를 쥐고 체크리스트를 채운다."""
    project_id = client.post("/api/projects", headers=JIHO, json={"name": "한빛 통합 마케팅"}).json()["project_id"]
    joined = client.post(
        f"/api/projects/{project_id}/members", headers=JIHO, json={"member_id": "minseok", "kind": "member"}
    )
    assert joined.status_code in (200, 201), joined.text

    planned = client.post(
        f"/api/projects/{project_id}/tasks",
        headers=JIHO,
        json={"title": "플레이스 썸네일 제작", "description": "시안 세 벌을 만들고 고른다"},
    ).json()
    handed = client.post(
        f"/api/tasks/{planned['task_id']}/reassign",
        headers=JIHO,
        json={"expected_version": planned["version"], "assignee_id": "mina"},
    )
    assert handed.status_code == 200, handed.text
    accepted = client.post(f"/api/task-assignments/{handed.json()['assignment_id']}/accept", headers=MINA)
    assert accepted.status_code == 200, accepted.text

    url = f"/api/tasks/{planned['task_id']}/checklist"
    first = client.post(url, headers=MINA, json={"text": "레퍼런스 모으기"}).json()
    client.post(url, headers=MINA, json={"text": "시안 세 벌"})
    checked = client.patch(f"{url}/{first['item_id']}", headers=MINA, json={"done": True})
    assert checked.status_code == 200, checked.text
    return {"project_id": project_id, "task_id": planned["task_id"], "item_id": first["item_id"], "url": url}


def _detail(client: TestClient, task_id: str, who: dict) -> dict:
    got = client.get(f"/api/tasks/{task_id}", headers=who)
    assert got.status_code == 200, got.text
    return got.json()


def _history(client: TestClient, project_id: str, *, by: dict | None = None) -> list[tuple]:
    """참여 이력 — **관계 종류(리드/참여)의 관측면**이다."""
    rows = client.get(f"/api/projects/{project_id}/participation-history", headers=by or JIHO)
    assert rows.status_code == 200, rows.text
    return [(row["member_id"], row["assignment_kind"], row["end_reason"]) for row in rows.json()]


def test_a_participant_reads_the_checklist_of_someone_elses_task(client: TestClient, scene: dict) -> None:
    """**L-07 — 같은 프로젝트의 「남의」 업무에서 항목이 보인다.**

    민석은 이 업무의 담당이 아니고 앞으로도 아니다. 그래도 **같은 프로젝트에 붙어 있으므로** 항목이
    실린다. 접근 값은 **`read_only` 그대로**다 — 그 값이 말하는 것은 이제 **쓰기 범위**다.
    """
    view = _detail(client, scene["task_id"], MINSEOK)

    assert view["access"] == "read_only"
    assert [item["text"] for item in view["checklist"]] == ["레퍼런스 모으기", "시안 세 벌"]
    assert [item["done"] for item in view["checklist"]] == [True, False]


def test_the_lead_reads_it_too(client: TestClient, scene: dict) -> None:
    """**L-08 — 리드도 같다.** 전에는 **리드조차** 남의 체크리스트를 못 봤다.

    **리드와 참여자를 가르지 않는다** (D-29). 지호는 이 프로젝트의 리드이고 조직 축의
    「업무 전체 읽기」 자격은 없다 — 항목을 여는 것은 **프로젝트에 붙어 있다는 사실 하나**다.

    **첫 단언이 지키는 것**: 지호가 **리드라는 사실 자체**다. 장면은 지호가 프로젝트를 만들기만 하고,
    리드라는 것은 「만든 사람을 `lead` 로 붙인다」는 **코드 기본값**에서만 나온다 — 그 기본값이
    참여(`member`)로 바뀌는 날 이 테스트는 **「참여자가 둘」** 을 재게 되고 **L-08 은 조용히 관측
    대상을 잃는다.** 그래서 관계 종류를 이력에서 **직접** 재고 들어간다 (L-02 와 같은 방식).
    """
    assert ("jiho", "lead", None) in _history(client, scene["project_id"])

    view = _detail(client, scene["task_id"], JIHO)

    assert view["access"] == "read_only"
    assert [item["text"] for item in view["checklist"]] == ["레퍼런스 모으기", "시안 세 벌"]


def test_the_aggregate_comes_with_the_items(client: TestClient, scene: dict) -> None:
    """**L-09 — 항목은 오는데 집계가 비는 자리가 없다.**

    항목만 열고 집계를 닫으면 **같은 사실을 두 규칙으로 읽는 것**이 된다. 담당이 보는 값과
    **같은 값**이어야 한다.
    """
    mine = _detail(client, scene["task_id"], MINA)
    participant = _detail(client, scene["task_id"], MINSEOK)
    lead = _detail(client, scene["task_id"], JIHO)

    assert mine["checklist_progress"] == {"done": 1, "total": 2}
    assert participant["checklist_progress"] == mine["checklist_progress"]
    assert lead["checklist_progress"] == mine["checklist_progress"]


def test_writing_is_still_refused_for_everyone_but_the_holder(client: TestClient, scene: dict) -> None:
    """**L-11 — 읽기를 넓힌 것이 쓰기를 넓히지 않는다.**

    네 표면 전부(추가·수정·삭제·순서)가 **전과 같이 거절된다.** 참여자도 리드도 같다 —
    가드는 **활성 담당자**이고 이 판이 그 줄을 건드리지 않았다.
    """
    url, item_id = scene["url"], scene["item_id"]

    for who in (MINSEOK, JIHO):
        assert client.post(url, headers=who, json={"text": "몰래 추가"}).status_code == 404
        assert client.patch(f"{url}/{item_id}", headers=who, json={"done": False}).status_code == 404
        assert client.delete(f"{url}/{item_id}", headers=who).status_code == 404
        assert client.post(f"{url}/order", headers=who, json={"item_ids": [item_id]}).status_code == 404

    # 아무것도 움직이지 않았다 — 담당이 보는 목록이 그대로다.
    assert _detail(client, scene["task_id"], MINA)["checklist_progress"] == {"done": 1, "total": 2}


def test_outside_the_project_the_items_do_not_come(client: TestClient, scene: dict) -> None:
    """**L-12 — 프로젝트 «밖»에서 읽는 갈래에서는 항목이 안 실린다.**

    유나는 대표라 조직 축으로 이 업무를 읽는다. 그런데 **이 프로젝트에 붙어 있지는 않다** —
    읽기를 여는 것이 프로젝트가 아니라 조직이므로 **항목이 오지 않는다.** 그 갈래의 미터는
    프로젝트 상세의 `tasks[]` 집계가 그린다(그 집계는 담당과 무관하게 언제나 실린다).
    """
    view = _detail(client, scene["task_id"], YUNA)

    assert view["access"] == "read_only"
    assert "checklist" not in view
    assert "checklist_progress" not in view
    # 그 사람의 프로젝트 목록에 이 프로젝트가 없다 — 「밖에서 읽는다」의 뜻이 그것이다.
    assert "한빛 통합 마케팅" not in {row["name"] for row in client.get("/api/projects", headers=YUNA).json()}


def test_the_description_comes_to_everyone_who_may_read_the_task(client: TestClient, scene: dict) -> None:
    """**L-13 — 업무 내용은 전과 같이 온다.** 이 변경으로 사라지지 않았다.

    설명은 **읽기 전용 접근에도 이미 실리고 있었다** — 프로젝트 밖에서 읽는 갈래에서도 온다.
    """
    for who in (MINA, MINSEOK, JIHO, YUNA):
        assert _detail(client, scene["task_id"], who)["description"] == "시안 세 벌을 만들고 고른다"


def test_the_project_detail_does_not_grow_the_items(client: TestClient, scene: dict) -> None:
    """**`tasks[]` 는 넓어지지 않는다** (D-38). 집계는 전과 같이 언제나 실리고, **항목은 상세에만** 있다."""
    detail = client.get(f"/api/projects/{scene['project_id']}", headers=MINSEOK)
    assert detail.status_code == 200, detail.text
    row = next(task for task in detail.json()["tasks"] if task["task_id"] == scene["task_id"])

    assert row["checklist_progress"] == {"done": 1, "total": 2}
    assert "checklist" not in row
