"""프로젝트 선행업무와 finish-to-start 시작 게이트 (SPEC-001 U-13·U-14·S-12 · §6 · DEC-001 D-20).

**상위·참고와 다른 세 번째 관계**다. 여기서 닫는 것은 다섯이다.

1. 생성·수정이 **같은 배열**을 받고, 수정은 **전체 교체**이며 생략은 보존이다.
2. 다섯 거절이 **각각 다른 오류**다 — 프로젝트 미선택·불일치·자기 자신·중복·순환.
3. 시작 게이트가 **`시작 전` 에서 나가는 두 문**에만 걸리고 취소된 선행은 막지 않는다.
4. **남은 선행이 있는 업무의 프로젝트는 바꿀 수 없다** — 하위가 상위를 따라가는 경로에도.
5. 투영과 **프로젝트 상세 업무 줄**이 선행 배열을 내고, 볼 수 없는 선행은 제목 없이 건수만 낸다.
"""
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import TaskPredecessorRecord, make_session_factory
from fastapi.testclient import TestClient

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
YUNA = {"X-Demo-Persona": "yuna"}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    return TestClient(create_app(settings)), database_url


def _project(client, *members: str) -> str:
    created = client.post("/api/projects", headers=JIHO, json={"name": "순서가 있는 프로젝트"})
    assert created.status_code == 201, created.text
    project_id = created.json()["project_id"]
    for member_id in members:
        joined = client.post(f"/api/projects/{project_id}/members", headers=JIHO, json={"member_id": member_id})
        assert joined.status_code == 201, joined.text
    return project_id


def _task(client, headers, key: str, **body) -> dict:
    created = client.post("/api/tasks", headers={**headers, "Idempotency-Key": key}, json=body)
    assert created.status_code == 201, created.text
    return created.json()


def _detail(client, headers, task_id: str) -> dict:
    response = client.get(f"/api/tasks/{task_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _finish(client, headers, task_id: str) -> None:
    """선행 하나를 끝까지 민다 — 시작 게이트가 열리는지 보기 위한 준비다."""
    task = _detail(client, headers, task_id)
    started = client.post(f"/api/tasks/{task_id}/start", headers=headers, json={"expected_version": task["version"]})
    assert started.status_code == 200, started.text
    done = client.post(
        f"/api/tasks/{task_id}/complete", headers=headers, json={"expected_version": started.json()["version"]}
    )
    assert done.status_code == 200, done.text


# ---- 1. 같은 배열, 전체 교체 ----------------------------------------------------


def test_creation_stores_the_array_and_the_projection_gives_it_back(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina")
    first = _task(client, MINA, "p-1", title="레퍼런스 조사", project_id=project_id)
    second = _task(client, MINA, "p-2", title="시안 구성", project_id=project_id)

    later = _task(
        client,
        MINA,
        "p-3",
        title="초안 검토",
        project_id=project_id,
        preceding_task_ids=[first["task_id"], second["task_id"]],
    )
    assert later["preceding_task_ids"] == [first["task_id"], second["task_id"]]

    detail = _detail(client, MINA, later["task_id"])
    assert detail["preceding_task_ids"] == [first["task_id"], second["task_id"]]
    assert [(row["title"], row["state"]) for row in detail["predecessors"]] == [
        ("레퍼런스 조사", "open"),
        ("시안 구성", "open"),
    ]
    # 목록도 같은 배열을 낸다 — 한 곳에서 계산해 나눠 싣는다.
    rows = {row["task_id"]: row for row in client.get("/api/my-work", headers=MINA).json()}
    assert rows[later["task_id"]]["preceding_task_ids"] == [first["task_id"], second["task_id"]]


def test_an_edit_replaces_the_whole_array_and_an_omitted_field_preserves_it(tmp_path) -> None:
    """**전체 교체**다. 생략은 보존이고 빈 배열은 「전부 뗀다」다 (SPEC-001 §4)."""
    client, database_url = _stack(tmp_path)
    project_id = _project(client, "mina")
    first = _task(client, MINA, "e-1", title="먼저 하나", project_id=project_id)
    second = _task(client, MINA, "e-2", title="먼저 둘", project_id=project_id)
    later = _task(client, MINA, "e-3", title="나중", project_id=project_id, preceding_task_ids=[first["task_id"]])

    # 다른 것을 고쳐도 선행은 그대로다 — **생략하면 건드리지 않는다.**
    renamed = client.patch(
        f"/api/tasks/{later['task_id']}", headers=MINA, json={"expected_version": later["version"], "title": "나중 업무"}
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["preceding_task_ids"] == [first["task_id"]]
    # 값이 바뀌지 않아도 **회차는 오른다** — 그 명령이 업무를 지났다는 사실이다.
    assert renamed.json()["version"] == later["version"] + 1

    swapped = client.patch(
        f"/api/tasks/{later['task_id']}",
        headers=MINA,
        json={"expected_version": renamed.json()["version"], "preceding_task_ids": [second["task_id"]]},
    )
    assert swapped.status_code == 200, swapped.text
    assert swapped.json()["preceding_task_ids"] == [second["task_id"]]
    # **선행 변경이 회차를 올리고 진행 기록에 남는다.**
    assert swapped.json()["version"] == renamed.json()["version"] + 1
    history = client.get(f"/api/tasks/{later['task_id']}/history", headers=MINA).json()
    assert any("preceding_task_ids" in str(row.get("summary", "")) for row in history["activity"])

    cleared = client.patch(
        f"/api/tasks/{later['task_id']}",
        headers=MINA,
        json={"expected_version": swapped.json()["version"], "preceding_task_ids": []},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["preceding_task_ids"] == []

    # **뗀 관계는 행으로 남는다** — 닫힐 뿐 지워지지 않는다 (§5 보존).
    with make_session_factory(database_url)() as session:
        rows = session.scalars(select(TaskPredecessorRecord)).all()
        assert len(rows) == 2 and all(row.released_at is not None for row in rows)


def test_a_stale_version_is_refused_before_anything_is_replaced(tmp_path) -> None:
    """선행 변경에 **회차가 필수**다 — 어긋나면 아무것도 바뀌지 않는다.

    상태 코드는 **현행 그대로 422** 다. SPEC-001 Case Matrix 는 `WORK_VERSION_STALE` 을 409 로
    적지만 이 저장소의 `PATCH /api/tasks/{id}` 는 오래전부터 422 를 냈고, 그 차이는 선행과 무관한
    **기존 불일치**다. WORK-003 이 코드를 맞추지 않는다 — 「SPEC 과 다르면 코드를 맞추지 말고
    SPEC 개정을 먼저 올린다」가 이 work 의 규칙이다. 여기서는 **지금 사실을 못 박아** 두고,
    그 불일치는 따로 올린다.
    """
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina")
    first = _task(client, MINA, "v-1", title="먼저", project_id=project_id)
    later = _task(client, MINA, "v-2", title="나중", project_id=project_id)
    stale = client.patch(
        f"/api/tasks/{later['task_id']}",
        headers=MINA,
        json={"expected_version": later["version"] + 5, "preceding_task_ids": [first["task_id"]]},
    )
    assert stale.status_code == 422 and "stale" in stale.json()["detail"]
    # 거절이 **아무것도 바꾸지 않는다** — 그것이 이 시험의 본론이다.
    assert _detail(client, MINA, later["task_id"])["preceding_task_ids"] == []


# ---- 2. 다섯 거절이 각각 다른 오류 -----------------------------------------------


def test_a_predecessor_without_a_project_is_refused_by_its_own_name(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina")
    first = _task(client, MINA, "n-1", title="먼저", project_id=project_id)
    refused = client.post(
        "/api/tasks",
        headers={**MINA, "Idempotency-Key": "no-project"},
        json={"title": "프로젝트 없이 선행", "preceding_task_ids": [first["task_id"]]},
    )
    assert refused.status_code == 422, refused.text
    assert "프로젝트를 먼저 선택" in refused.json()["detail"]


def test_a_predecessor_from_another_project_is_refused_by_its_own_name(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    here = _project(client, "mina")
    other = client.post("/api/projects", headers=JIHO, json={"name": "다른 프로젝트"}).json()["project_id"]
    assert client.post(f"/api/projects/{other}/members", headers=JIHO, json={"member_id": "mina"}).status_code == 201
    elsewhere = _task(client, MINA, "m-1", title="다른 프로젝트의 업무", project_id=other)

    refused = client.post(
        "/api/tasks",
        headers={**MINA, "Idempotency-Key": "mismatch"},
        json={"title": "다른 프로젝트 선행", "project_id": here, "preceding_task_ids": [elsewhere["task_id"]]},
    )
    assert refused.status_code == 422, refused.text
    assert "같은 프로젝트의 업무만" in refused.json()["detail"]


def test_self_and_duplicate_each_get_their_own_refusal(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina")
    first = _task(client, MINA, "s-1", title="먼저", project_id=project_id)
    later = _task(client, MINA, "s-2", title="나중", project_id=project_id)

    itself = client.patch(
        f"/api/tasks/{later['task_id']}",
        headers=MINA,
        json={"expected_version": later["version"], "preceding_task_ids": [later["task_id"]]},
    )
    assert itself.status_code == 422 and "자기 자신" in itself.json()["detail"]

    twice = client.patch(
        f"/api/tasks/{later['task_id']}",
        headers=MINA,
        json={
            "expected_version": later["version"],
            "preceding_task_ids": [first["task_id"], first["task_id"]],
        },
    )
    # **조용히 털지 않는다** — 나머지 넷과 같은 모양으로 자기 이름의 거절을 받는다.
    assert twice.status_code == 422 and "이미 선행으로 지정된" in twice.json()["detail"]
    assert _detail(client, MINA, later["task_id"])["preceding_task_ids"] == []


def test_a_cycle_is_refused_by_its_own_name_and_nothing_is_stored(tmp_path) -> None:
    """A 의 선행에 B, B 의 선행에 A — **두 번째가 거절된다** (S-12)."""
    client, database_url = _stack(tmp_path)
    project_id = _project(client, "mina")
    a = _task(client, MINA, "c-1", title="A", project_id=project_id)
    b = _task(client, MINA, "c-2", title="B", project_id=project_id)
    c = _task(client, MINA, "c-3", title="C", project_id=project_id)

    first = client.patch(
        f"/api/tasks/{a['task_id']}",
        headers=MINA,
        json={"expected_version": a["version"], "preceding_task_ids": [b["task_id"]]},
    )
    assert first.status_code == 200, first.text
    second = client.patch(
        f"/api/tasks/{b['task_id']}",
        headers=MINA,
        json={"expected_version": b["version"], "preceding_task_ids": [a["task_id"]]},
    )
    assert second.status_code == 422 and "서로를 기다리게" in second.json()["detail"]

    # 한 겹 더 깊은 고리도 막는다 — 활성 변을 따라 끝까지 걷는다.
    assert client.patch(
        f"/api/tasks/{b['task_id']}",
        headers=MINA,
        json={"expected_version": b["version"], "preceding_task_ids": [c["task_id"]]},
    ).status_code == 200
    deep = client.patch(
        f"/api/tasks/{c['task_id']}",
        headers=MINA,
        json={"expected_version": c["version"], "preceding_task_ids": [a["task_id"]]},
    )
    assert deep.status_code == 422 and "서로를 기다리게" in deep.json()["detail"]

    with make_session_factory(database_url)() as session:
        active = [row for row in session.scalars(select(TaskPredecessorRecord)) if row.released_at is None]
        assert {(str(row.task_id), str(row.predecessor_task_id)) for row in active} == {
            (a["task_id"], b["task_id"]),
            (b["task_id"], c["task_id"]),
        }


def test_a_predecessor_this_person_cannot_read_is_not_found(tmp_path) -> None:
    """읽을 수 없는 업무는 선행이 될 수 없고, **없는 것과 같은 말**로 답한다."""
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina")
    later = _task(client, MINA, "u-1", title="나중", project_id=project_id)
    refused = client.patch(
        f"/api/tasks/{later['task_id']}",
        headers=MINA,
        json={"expected_version": later["version"], "preceding_task_ids": [str(uuid4())]},
    )
    assert refused.status_code == 404, refused.text


# ---- 3. 시작 게이트 --------------------------------------------------------------


@pytest.mark.parametrize("door", ["start", "complete"])
def test_an_unfinished_predecessor_closes_both_doors_out_of_open(tmp_path, door) -> None:
    """시작과 `시작 전` 의 완료 직행이 **같은 409** 로 막히고, 본문이 막는 선행의 이름을 낸다."""
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina")
    first = _task(client, MINA, "g-1", title="표지 디자인", project_id=project_id)
    later = _task(client, MINA, "g-2", title="보고서 발행", project_id=project_id, preceding_task_ids=[first["task_id"]])

    refused = client.post(
        f"/api/tasks/{later['task_id']}/{door}", headers=MINA, json={"expected_version": later["version"]}
    )
    assert refused.status_code == 409, refused.text
    assert "표지 디자인" in refused.json()["detail"]
    assert _detail(client, MINA, later["task_id"])["state"] == "open"


def test_finishing_or_cancelling_every_predecessor_opens_the_door(tmp_path) -> None:
    """**취소된 선행은 막지 않는다.** 남은 선행이 전부 완료거나 취소면 시작이 열린다 (U-14)."""
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina")
    done_one = _task(client, MINA, "o-1", title="끝낼 선행", project_id=project_id)
    cancel_one = _task(client, MINA, "o-2", title="접을 선행", project_id=project_id)
    later = _task(
        client,
        MINA,
        "o-3",
        title="후행",
        project_id=project_id,
        preceding_task_ids=[done_one["task_id"], cancel_one["task_id"]],
    )

    cancelled = client.post(
        f"/api/tasks/{cancel_one['task_id']}/cancel",
        headers=MINA,
        json={"expected_version": cancel_one["version"], "reason": "필요 없어졌다"},
    )
    assert cancelled.status_code == 200, cancelled.text
    # 아직 하나가 남아 있다.
    assert client.post(
        f"/api/tasks/{later['task_id']}/start", headers=MINA, json={"expected_version": later["version"]}
    ).status_code == 409

    _finish(client, MINA, done_one["task_id"])
    opened = client.post(
        f"/api/tasks/{later['task_id']}/start", headers=MINA, json={"expected_version": later["version"]}
    )
    assert opened.status_code == 200, opened.text
    assert opened.json()["state"] == "in_progress"


def test_reopening_a_predecessor_never_walks_the_follower_back(tmp_path) -> None:
    """**게이트는 시작 시점 판정이다** (U-14 · S-12). 시작한 뒤 선행이 다시 열려도 되돌리지 않는다."""
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina")
    first = _task(client, MINA, "r-1", title="선행", project_id=project_id)
    later = _task(client, MINA, "r-2", title="후행", project_id=project_id, preceding_task_ids=[first["task_id"]])
    _finish(client, MINA, first["task_id"])

    started = client.post(
        f"/api/tasks/{later['task_id']}/start", headers=MINA, json={"expected_version": later["version"]}
    )
    assert started.status_code == 200, started.text

    finished = _detail(client, MINA, first["task_id"])
    reopened = client.post(
        f"/api/tasks/{first['task_id']}/reopen", headers=MINA, json={"expected_version": finished["version"]}
    )
    assert reopened.status_code == 200, reopened.text

    # 후행은 그대로 `진행 중` 이고, **완료도 선행을 보지 않는다** — 막는 것은 시작이다.
    still = _detail(client, MINA, later["task_id"])
    assert still["state"] == "in_progress"
    completed = client.post(
        f"/api/tasks/{later['task_id']}/complete", headers=MINA, json={"expected_version": still["version"]}
    )
    assert completed.status_code == 200 and completed.json()["state"] == "done"


def test_a_reference_link_never_blocks_a_start_and_a_predecessor_never_changes_the_child_rule(tmp_path) -> None:
    """**관계 셋을 섞지 않는다** — 참고는 시작을 막지 않고, 선행은 하위 완료 규칙을 바꾸지 않는다."""
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina")
    context = _task(client, MINA, "x-1", title="맥락만 주는 업무", project_id=project_id)
    later = _task(client, MINA, "x-2", title="참고를 단 업무", project_id=project_id)
    linked = client.post(
        f"/api/tasks/{later['task_id']}/references", headers=MINA, json={"referenced_task_id": context["task_id"]}
    )
    assert linked.status_code == 201, linked.text
    started = client.post(
        f"/api/tasks/{later['task_id']}/start",
        headers=MINA,
        json={"expected_version": _detail(client, MINA, later["task_id"])["version"]},
    )
    assert started.status_code == 200, started.text

    # 선행이 달린 업무의 하위 완료 규칙은 그대로다 — **다른 코드**로 막힌다.
    parent = _task(client, MINA, "x-3", title="상위", project_id=project_id)
    _task(client, MINA, "x-4", title="미완 하위", project_id=project_id, parent_task_id=parent["task_id"])
    held = _detail(client, MINA, parent["task_id"])
    refused = client.post(
        f"/api/tasks/{parent['task_id']}/complete", headers=MINA, json={"expected_version": held["version"]}
    )
    assert refused.status_code == 409 and "하위 업무" in refused.json()["detail"]


# ---- 4. 프로젝트 변경 잠금 --------------------------------------------------------


def test_a_task_with_remaining_predecessors_cannot_change_its_project(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    here = _project(client, "mina")
    there = client.post("/api/projects", headers=JIHO, json={"name": "옮겨 갈 프로젝트"}).json()["project_id"]
    assert client.post(f"/api/projects/{there}/members", headers=JIHO, json={"member_id": "mina"}).status_code == 201
    first = _task(client, MINA, "l-1", title="선행", project_id=here)
    later = _task(client, MINA, "l-2", title="후행", project_id=here, preceding_task_ids=[first["task_id"]])

    refused = client.patch(
        f"/api/tasks/{later['task_id']}",
        headers=MINA,
        json={"expected_version": later["version"], "project_id": there},
    )
    assert refused.status_code == 409, refused.text
    assert "선행업무를 먼저 비워야" in refused.json()["detail"]

    # 선행을 비우면 열린다 — 출구가 있다.
    cleared = client.patch(
        f"/api/tasks/{later['task_id']}",
        headers=MINA,
        json={"expected_version": later["version"], "preceding_task_ids": []},
    )
    assert cleared.status_code == 200, cleared.text
    moved = client.patch(
        f"/api/tasks/{later['task_id']}",
        headers=MINA,
        json={"expected_version": cleared.json()["version"], "project_id": there},
    )
    assert moved.status_code == 200 and moved.json()["project_id"] == there


def test_a_child_that_would_be_dragged_along_locks_its_parents_project_too(tmp_path) -> None:
    """**하위가 상위를 따라 옮겨 가는 경로에도 같은 규칙이 걸린다** (SPEC-001 §6)."""
    client, _ = _stack(tmp_path)
    here = _project(client, "mina")
    there = client.post("/api/projects", headers=JIHO, json={"name": "옮겨 갈 프로젝트"}).json()["project_id"]
    assert client.post(f"/api/projects/{there}/members", headers=JIHO, json={"member_id": "mina"}).status_code == 201

    parent = _task(client, MINA, "d-1", title="상위", project_id=here)
    sibling = _task(client, MINA, "d-2", title="형제 선행", project_id=here, parent_task_id=parent["task_id"])
    child = _task(client, MINA, "d-3", title="하위", project_id=here, parent_task_id=parent["task_id"])
    bound = client.patch(
        f"/api/tasks/{child['task_id']}",
        headers=MINA,
        json={"expected_version": child["version"], "preceding_task_ids": [sibling["task_id"]]},
    )
    assert bound.status_code == 200, bound.text

    refused = client.patch(
        f"/api/tasks/{parent['task_id']}",
        headers=MINA,
        json={"expected_version": _detail(client, MINA, parent["task_id"])["version"], "project_id": there},
    )
    assert refused.status_code == 409, refused.text
    assert "선행업무를 먼저 비워야" in refused.json()["detail"]
    # 상위도 하위도 그대로 남는다 — 거절이 절반만 반영되지 않는다.
    assert _detail(client, MINA, parent["task_id"])["project_id"] == here
    assert _detail(client, MINA, child["task_id"])["project_id"] == here


# ---- 5. 조회에 드러나는 자리 ------------------------------------------------------


def test_the_project_detail_task_row_carries_the_array_without_a_new_query(tmp_path) -> None:
    """**간트 연결선의 유일한 원천**이다 — 프로젝트 상세의 업무 줄이 함께 낸다 (U-15)."""
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina")
    first = _task(client, MINA, "gg-1", title="선행", project_id=project_id)
    later = _task(client, MINA, "gg-2", title="후행", project_id=project_id, preceding_task_ids=[first["task_id"]])

    detail = client.get(f"/api/projects/{project_id}", headers=MINA)
    assert detail.status_code == 200, detail.text
    rows = {row["task_id"]: row for row in detail.json()["tasks"]}
    assert rows[later["task_id"]]["preceding_task_ids"] == [first["task_id"]]
    assert rows[first["task_id"]]["preceding_task_ids"] == []
    # 상위–하위는 **다른 그림**이라 같은 배열에 섞이지 않는다.
    assert rows[later["task_id"]]["parent_task_id"] is None


def test_a_predecessor_the_reader_cannot_open_shows_a_count_without_a_title(tmp_path) -> None:
    """**제목은 감추고 건수는 낸다** — 막는 이유를 숨기면 다음 걸음을 고를 수 없다 (§4)."""
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina", "jiho")
    hidden = _task(client, JIHO, "h-1", title="지호만 아는 선행", project_id=project_id)
    later = _task(client, MINA, "h-2", title="후행", project_id=project_id, preceding_task_ids=[hidden["task_id"]])

    # 같은 프로젝트라 민아도 읽는다 — 먼저 그 사실을 확인한다.
    assert _detail(client, MINA, later["task_id"])["predecessors"][0]["title"] == "지호만 아는 선행"

    # 프로젝트 밖으로 나가면 제목이 사라지고 **자리만** 남는다.
    released = client.delete(f"/api/projects/{project_id}/members/mina", headers=JIHO)
    assert released.status_code == 204, released.text
    view = _detail(client, MINA, later["task_id"])
    assert view["preceding_task_ids"] == [hidden["task_id"]]
    assert view["predecessors"] == [{"task_id": hidden["task_id"], "title": None, "state": None}]


# ---- 표면 일치: 모든 생성 표면이 같은 배열을 받는다 --------------------------------


def test_every_creation_surface_takes_the_same_predecessor_array(tmp_path) -> None:
    """REST · MCP · 업무 요청이 **같은 배열**을 받는다 (SPEC-001 §5 표면 일치)."""
    import asyncio

    from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server

    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina", "jiho")
    first = _task(client, MINA, "sfc-1", title="선행", project_id=project_id)

    server = _create_bound_persona_server(McpReportsFacade(client.app.state.workflow_application._settings, "mina"))
    made = asyncio.run(
        server.call_tool(
            "task_create_self",
            {
                "title": "도구가 만든 후행",
                "idempotency_key": "surface-mcp",
                "project_id": project_id,
                "preceding_task_ids": [first["task_id"]],
            },
        )
    )
    assert not made.is_error, made
    assert made.structured_content["preceding_task_ids"] == [first["task_id"]]

    sent = client.post(
        "/api/work-requests",
        headers={**MINA, "Idempotency-Key": "surface-request"},
        json={
            "title": "요청으로 만든 후행",
            "assignee_id": "jiho",
            "project_id": project_id,
            "preceding_task_ids": [first["task_id"]],
        },
    )
    assert sent.status_code == 201, sent.text
    assert _detail(client, JIHO, sent.json()["task_id"])["preceding_task_ids"] == [first["task_id"]]


def test_the_recipient_branch_of_task_creation_refuses_predecessors_by_name(tmp_path) -> None:
    """담당을 지정한 `POST /api/tasks` 는 프로젝트를 받지 않으므로 선행도 **이름으로** 거절한다."""
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina")
    first = _task(client, MINA, "rb-1", title="선행", project_id=project_id)
    refused = client.post(
        "/api/tasks",
        headers={**MINA, "Idempotency-Key": "recipient-preceding"},
        json={"title": "남에게 보내며 선행", "assignee_id": "jiho", "preceding_task_ids": [first["task_id"]]},
    )
    assert refused.status_code == 422, refused.text
    assert "preceding_task_ids" in refused.json()["detail"] and "쓸 수 없는 항목" in refused.json()["detail"]


def test_a_long_predecessor_chain_still_answers_one_detail_without_walking_itself(tmp_path) -> None:
    """선행이 사슬로 이어져도 상세 한 건은 **자기 선행 한 겹만** 낸다.

    읽기 판정이 상세 투영을 지나고 상세 투영이 다시 선행 요약을 만들기 때문에, 빗장이 없으면 A 를
    읽는 동안 B 를, B 를 읽는 동안 C 를 읽는 되돌이가 사슬 길이만큼 쌓인다. 순환은 이미 막혀 있어
    무한하지는 않지만 같은 질의가 겹친다 — 그 되돌이를 여기서 못 박는다.

    **접는 것은 되돌이뿐이다**: 밖으로 나가는 상세는 제목과 상태를 그대로 낸다.
    """
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina")
    chain = [_task(client, MINA, f"chain-{index}", title=f"{index}단계", project_id=project_id) for index in range(6)]
    for index in range(1, len(chain)):
        linked = client.patch(
            f"/api/tasks/{chain[index]['task_id']}",
            headers=MINA,
            json={
                "expected_version": chain[index]["version"],
                "preceding_task_ids": [chain[index - 1]["task_id"]],
            },
        )
        assert linked.status_code == 200, linked.text

    last = _detail(client, MINA, chain[-1]["task_id"])
    # **한 겹만** 낸다 — 사슬 전체가 아니라 직속 선행 하나다.
    assert last["preceding_task_ids"] == [chain[-2]["task_id"]]
    assert last["predecessors"] == [
        {"task_id": chain[-2]["task_id"], "title": "4단계", "state": "open"}
    ]
    # 목록도 같은 답이고, 사슬 어느 줄에서도 제목이 비지 않는다.
    rows = {row["task_id"]: row for row in client.get("/api/my-work", headers=MINA).json()}
    assert rows[chain[-1]["task_id"]]["preceding_task_ids"] == [chain[-2]["task_id"]]
    assert rows[chain[0]["task_id"]]["preceding_task_ids"] == []


def test_the_same_relation_written_twice_converges_on_one_active_row(tmp_path) -> None:
    """같은 관계의 **동시 두 번이 행 하나**다 — 활성 행 유일성이 그것을 답한다 (§5 동시성)."""
    client, database_url = _stack(tmp_path)
    project_id = _project(client, "mina")
    first = _task(client, MINA, "cc-1", title="선행", project_id=project_id)
    later = _task(client, MINA, "cc-2", title="후행", project_id=project_id, preceding_task_ids=[first["task_id"]])

    from ax_workspace.platform.work_tasks import SqlAlchemyTaskRepository

    with make_session_factory(database_url)() as session:
        # 같은 관계를 다시 쓴다 — 이미 활성이므로 **행을 더하지 않는다.**
        SqlAlchemyTaskRepository(session).replace_predecessors(
            UUID(later["task_id"]), [UUID(first["task_id"])], "mina"
        )
        session.commit()

    with make_session_factory(database_url)() as session:
        active = [row for row in session.scalars(select(TaskPredecessorRecord)) if row.released_at is None]
        assert len(active) == 1
