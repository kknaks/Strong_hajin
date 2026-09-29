"""상위 변경 — **만든 뒤에도 옮긴다**, 그리고 두 게이트 (SPEC-007 §4 · WORK-007 Phase B-2).

앞판의 계약은 「상위를 옮기는 명령이 **없다**」였고 `PATCH` 가 `parent_task_id` 를 **알 수 없는 필드로
거절**했다(미정 EU-15). SPEC-007 §4 가 D-17 로 그 자리를 열었다. 여기서 닫는 것은 넷이다.

1. **칸 둘이 받아진다** — `parent_task_id` · `clear_parent`. 함께 보내면 거절이다.
2. **검증 0~7 이 순서대로** 걸리고 **V-8 파급 검사가 맨 뒤**다.
3. **직속 하위의 V-8 파급**이 새 오류 코드 하나(`WORK_CHILDREN_DIRECT_NESTING`, 409)로 거절된다 —
   읽을 수 없는 하위까지 세고 이름은 읽을 수 있는 것만 낸다.
4. **프로젝트가 자손 전체에 따라간다** — 손자·증손자까지. 선행 잠금에 걸리면 **아무것도 안 움직인다**.

**소급 재배치를 하지 않는다** — 어긋남이 되는 이동을 거절할 뿐이고, 그 사실도 여기서 센다.
"""
from uuid import uuid4

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from fastapi.testclient import TestClient

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
YUNA = {"X-Demo-Persona": "yuna"}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    return TestClient(create_app(settings)), database_url


def _project(client, name: str, *members: str) -> str:
    created = client.post("/api/projects", headers=JIHO, json={"name": name})
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


def _move(client, headers, task_id: str, **body):
    version = _detail(client, headers, task_id)["version"]
    return client.patch(f"/api/tasks/{task_id}", headers=headers, json={"expected_version": version, **body})


def _child_by_request(client, key: str, title: str, parent_task_id: str, sender, assignee_id: str, receiver) -> str:
    """**담당자를 갈아 한 층을 더한다** — 요청·수락이 받는 사람의 새 중심 업무를 세운다.

    V-8 이 「같은 사람의 직접 작업 아래 직접 작업」을 **생성에서** 막으므로, 한 사람이 든 3층 트리를
    직접 만들 수 없다. 깊이가 필요한 무대는 이 문으로 층을 쌓는다 (SPEC-003 §5 중심 업무 판정).
    """
    sent = client.post(
        "/api/work-requests", headers={**sender, "Idempotency-Key": key},
        json={"title": title, "assignee_id": assignee_id, "parent_task_id": parent_task_id},
    )
    assert sent.status_code == 201, sent.text
    _accept(client, sent.json()["request_id"], receiver)
    return sent.json()["task_id"]


def _accept(client, request_id: str, headers) -> dict:
    current = client.get(f"/api/work-requests/{request_id}", headers=headers)
    assert current.status_code == 200, current.text
    accepted = client.post(
        f"/api/work-requests/{request_id}/accept", headers=headers,
        json={"expected_version": current.json()["version"]},
    )
    assert accepted.status_code == 200, accepted.text
    return accepted.json()


# ---- 1. 칸 둘이 받아진다 ---------------------------------------------------------


def test_the_patch_now_takes_the_parent_field_and_moves_the_task(tmp_path) -> None:
    """**지금까지 422 였던 칸이 받아진다** (SPEC-007 §4 · D-17).

    성공하면 **회차가 오르고 진행 기록에 남는다**.
    """
    client, _ = _stack(tmp_path)
    parent = _task(client, MINA, "p1-1", title="상위")
    orphan = _task(client, MINA, "p1-2", title="떠 있는 업무")
    assert _detail(client, MINA, orphan["task_id"])["parent"] is None

    moved = _move(client, MINA, orphan["task_id"], parent_task_id=parent["task_id"])
    assert moved.status_code == 200, moved.text
    assert moved.json()["version"] == orphan["version"] + 1

    view = _detail(client, MINA, orphan["task_id"])
    assert view["parent"]["task_id"] == parent["task_id"]
    # 상위 쪽에서도 하위로 보인다.
    assert [row["task_id"] for row in _detail(client, MINA, parent["task_id"])["children"]] == [orphan["task_id"]]
    # **진행 기록에 남는다** — 바뀐 키 이름이 요약에 들어간다.
    history = client.get(f"/api/tasks/{orphan['task_id']}/history", headers=MINA).json()
    assert any("parent_task_id" in str(row.get("summary") or "") for row in history["activity"]), history


def test_clear_parent_detaches_the_task_and_leaves_the_project_alone(tmp_path) -> None:
    """`clear_parent` 로 비우면 **프로젝트는 그대로 남는다** — 따라갈 상위가 없다 (SPEC-007 §4 7번 표)."""
    client, _ = _stack(tmp_path)
    project_id = _project(client, "한 프로젝트", "mina")
    parent = _task(client, MINA, "p2-1", title="상위", project_id=project_id)
    child = _task(client, MINA, "p2-2", title="하위", parent_task_id=parent["task_id"])
    assert _detail(client, MINA, child["task_id"])["project_id"] == project_id

    detached = _move(client, MINA, child["task_id"], clear_parent=True)
    assert detached.status_code == 200, detached.text
    view = _detail(client, MINA, child["task_id"])
    assert view["parent"] is None
    # **프로젝트는 그대로다.**
    assert view["project_id"] == project_id


def test_sending_both_the_parent_and_the_clear_flag_is_refused(tmp_path) -> None:
    """둘을 **함께 보내면 거절**이다 — 무엇을 원했는지 알 수 없다 (SPEC-007 § Validation)."""
    client, _ = _stack(tmp_path)
    parent = _task(client, MINA, "p3-1", title="상위")
    child = _task(client, MINA, "p3-2", title="하위")

    both = _move(client, MINA, child["task_id"], parent_task_id=parent["task_id"], clear_parent=True)
    assert both.status_code == 422, both.text
    # 문장은 pydantic 오류 목록으로 나간다 — 기존 편집 거절(`title cannot be cleared`)과 같은 모양이다.
    assert "parent_task_id and clear_parent cannot be sent together" in both.text
    assert _detail(client, MINA, child["task_id"])["parent"] is None


def test_resending_the_same_parent_is_not_an_error(tmp_path) -> None:
    """같은 값을 다시 보낸 것은 **아무 일도 아니다** — 재전송이 실패가 되지 않는다."""
    client, _ = _stack(tmp_path)
    parent = _task(client, MINA, "p4-1", title="상위")
    child = _task(client, MINA, "p4-2", title="하위", parent_task_id=parent["task_id"])

    again = _move(client, MINA, child["task_id"], parent_task_id=parent["task_id"])
    assert again.status_code == 200, again.text
    assert _detail(client, MINA, child["task_id"])["parent"]["task_id"] == parent["task_id"]


# ---- 2. 검증 1~5 — 기존 `parent_for()` 를 그대로 쓴다 -------------------------------


def test_an_unreadable_parent_is_a_404(tmp_path) -> None:
    """검증 1 — 읽을 수 없는 상위는 **없는 것과 같다**(`WORK_PARENT_NOT_FOUND`, 404)."""
    client, _ = _stack(tmp_path)
    mine = _task(client, MINA, "p5-1", title="내 업무")
    theirs = _task(client, JIHO, "p5-2", title="지호만 아는 업무")

    denied = _move(client, MINA, mine["task_id"], parent_task_id=theirs["task_id"])
    assert denied.status_code == 404, denied.text
    # 아예 없는 업무도 같은 말이다.
    assert _move(client, MINA, mine["task_id"], parent_task_id=str(uuid4())).status_code == 404


def test_a_finished_parent_is_a_conflict(tmp_path) -> None:
    """검증 2 — 끝난 업무에는 매달 수 없다(`WORK_PARENT_CLOSED`, 409)."""
    client, _ = _stack(tmp_path)
    parent = _task(client, MINA, "p6-1", title="곧 끝날 상위")
    child = _task(client, MINA, "p6-2", title="떠 있는 업무")
    done = client.post(
        f"/api/tasks/{parent['task_id']}/complete", headers=MINA, json={"expected_version": parent["version"]}
    )
    assert done.status_code == 200, done.text

    denied = _move(client, MINA, child["task_id"], parent_task_id=parent["task_id"])
    assert denied.status_code == 409, denied.text
    assert "끝난 업무" in denied.json()["detail"]


def test_a_parent_whose_assignment_is_not_settled_is_a_conflict(tmp_path) -> None:
    """검증 3 — 수락 전 요청 업무 아래에 둘 수 없다(`WORK_PARENT_UNASSIGNED`, 409)."""
    client, _ = _stack(tmp_path)
    sent = client.post(
        "/api/work-requests", headers={**MINA, "Idempotency-Key": "p7-req"},
        json={"title": "아직 수락 안 된 요청", "assignee_id": "jiho"},
    )
    assert sent.status_code == 201, sent.text
    pending_task = sent.json()["task_id"]
    child = _task(client, MINA, "p7-1", title="떠 있는 업무")

    denied = _move(client, MINA, child["task_id"], parent_task_id=pending_task)
    assert denied.status_code == 409, denied.text
    assert "수락" in denied.json()["detail"]


def test_a_cycle_is_refused_at_any_depth(tmp_path) -> None:
    """검증 4 — 자기 자신·**자기 조상**을 상위로 둘 수 없다(`WORK_PARENT_CYCLE`, 422).

    깊이 제한이 없으므로 **손자에서 조부모를 가리키는 것**도 같은 거절이다.

    ⚠ **담당자를 번갈아 세운다.** V-8 이 같은 사람의 직접 작업 중첩을 **생성에서** 막으므로
    민아–민아–민아 3층을 직접 만들 수 없다. 요청·수락이 중간 층에 새 중심 업무를 세운다.
    """
    client, _ = _stack(tmp_path)
    grand = _task(client, MINA, "p8-1", title="조부모")
    middle = _child_by_request(client, "p8-2", "부모", grand["task_id"], MINA, "jiho", JIHO)
    leaf = _task(client, JIHO, "p8-3", title="손자", parent_task_id=middle)

    for target in (grand["task_id"], middle, leaf["task_id"]):
        denied = _move(client, MINA, grand["task_id"], parent_task_id=target)
        assert denied.status_code == 422, (target, denied.text)
        assert "상위 업무를 하위로" in denied.json()["detail"]
    assert _detail(client, MINA, grand["task_id"])["parent"] is None


def test_this_tasks_own_v8_violation_keeps_the_existing_code(tmp_path) -> None:
    """검증 5 — **이 업무**가 직접 작업이 되는데 새 상위가 중심 업무가 아니면 **기존 코드**다
    (`WORK_DIRECT_NESTING`, 409).

    새 코드(`WORK_CHILDREN_DIRECT_NESTING`)와 **합치지 않는다** — 이쪽은 「지금 만들려는 관계」가
    어긋나는 것이고 그쪽은 「이미 있는 관계」가 깨지는 것이다 (D-18).
    """
    client, _ = _stack(tmp_path)
    # 민아의 중심 업무 → 민아의 직접 작업. 그 직접 작업은 중심 업무가 아니다.
    central = _task(client, MINA, "p9-1", title="민아의 중심 업무")
    direct = _task(client, MINA, "p9-2", title="민아의 직접 작업", parent_task_id=central["task_id"])
    other = _task(client, MINA, "p9-3", title="옮길 민아 업무")

    denied = _move(client, MINA, other["task_id"], parent_task_id=direct["task_id"])
    assert denied.status_code == 409, denied.text
    assert "직접 작업은 중심 업무의 바로 아래에만" in denied.json()["detail"]


# ---- 3. 검증 6 — 직속 하위의 V-8 파급 (새 오류 코드) --------------------------------


def _same_holder_tree(client):
    """민아의 **중심 업무 두 개**와, 그 하나 아래의 「민아의 직접 작업 + 그 아래 민아의 직접 작업」.

    바깥(`mover`)은 지금 중심 업무다 — 부모가 없다. 그 아래 민아 담당 하위가 하나 서 있고,
    그 하위는 `mover` 가 중심 업무이므로 **지금은 V-8 을 지킨다**. `mover` 를 「민아가 든 다른 중심
    업무」 아래로 옮기면 `mover` 가 직접 작업이 되어 중심 업무 자격을 잃고, **그 하위가 어긋난다.**
    """
    anchor = _task(client, MINA, "v8-anchor", title="민아의 다른 중심 업무")
    mover = _task(client, MINA, "v8-mover", title="옮길 업무")
    child = _task(client, MINA, "v8-child", title="민아가 든 직속 하위", parent_task_id=mover["task_id"])
    return anchor, mover, child


def test_moving_under_the_same_holder_breaks_the_direct_children_and_is_refused(tmp_path) -> None:
    """검증 6 — **직속 하위가 V-8 을 어기게 되면 409** 이고 **막는 하위 이름이 본문에 있다**.

    새 상위의 활성 담당자가 이 업무의 담당자와 **같을** 때만 이 갈래가 선다 — 그때 이 업무가 직접
    작업이 되어 중심 업무가 아니게 된다 (SPEC-003 §5 중심 업무 판정 그대로).
    """
    client, _ = _stack(tmp_path)
    anchor, mover, child = _same_holder_tree(client)

    denied = _move(client, MINA, mover["task_id"], parent_task_id=anchor["task_id"])
    assert denied.status_code == 409, denied.text
    detail = denied.json()["detail"]
    assert "중심 업무 밖에 놓입니다" in detail
    assert "민아가 든 직속 하위" in detail
    # **기존 코드와 문장이 다르다** — 무엇을 고쳐야 하는지가 갈린다 (D-18).
    assert "직접 작업은 중심 업무의 바로 아래에만" not in detail

    # **아무것도 움직이지 않았다.**
    assert _detail(client, MINA, mover["task_id"])["parent"] is None
    assert _detail(client, MINA, child["task_id"])["parent"]["task_id"] == mover["task_id"]


def test_the_gate_names_the_blocking_child_and_the_hidden_half_lives_in_a_unit_test(tmp_path) -> None:
    """막는 하위의 **이름**이 본문에 있다. 그리고 **못 읽는 하위 갈래는 이 표면에서 도달할 수 없다.**

    게이트가 서는 조건은 「담당자가 이 업무의 담당자와 같은 직속 하위」이고, 업무 편집은 **그 업무를
    든 사람**만 부를 수 있다(소유 투영). 그래서 실제 요청에서 막는 하위는 **언제나 부르는 사람 자신이
    든 것**이고 반드시 읽을 수 있다 — 여기서 「제목이 가려지는」 응답을 만들 수가 없다.

    SPEC-007 §4 의 「읽을 수 없는 하위까지 전부 센다」는 그래서 **방어**다. 그 절반은
    `tests/unit/test_task_children_nesting_gate.py` 가 그 층에서 센다 — 읽기 판정을 거짓으로 돌려
    **이름이 사라지고 건수만 남고 그래도 막는 것**을 확인한다.
    """
    client, _ = _stack(tmp_path)
    anchor = _task(client, JIHO, "v8h-anchor", title="지호의 다른 중심 업무")
    mover = _task(client, JIHO, "v8h-mover", title="지호가 옮길 업무")
    _task(client, JIHO, "v8h-child", title="지호가 든 직속 하위", parent_task_id=mover["task_id"])

    named = _move(client, JIHO, mover["task_id"], parent_task_id=anchor["task_id"])
    assert named.status_code == 409, named.text
    detail = named.json()["detail"]
    assert "지호가 든 직속 하위" in detail
    # 이름이 나왔으므로 **건수 문구는 서지 않는다** — 두 갈래가 한 응답에 섞이지 않는다.
    assert "건이 중심 업무 밖에 놓입니다" not in detail
    # 민아는 같은 이동을 부를 수 없다 — 그 업무를 들지 않았다. 그것이 위 갈래가 닫혀 있는 이유다.
    version = _detail(client, JIHO, mover["task_id"])["version"]
    outsider = client.patch(
        f"/api/tasks/{mover['task_id']}", headers=MINA,
        json={"expected_version": version, "parent_task_id": anchor["task_id"]},
    )
    assert outsider.status_code == 404, outsider.text


def test_the_children_gate_runs_before_the_project_cascade_gate(tmp_path) -> None:
    """**검증 6 이 7 보다 먼저다** — 둘 다 걸리는 이동에서 6 의 문장이 나온다 (SPEC-007 §4 순서).

    6 은 「이동 자체가 규칙을 깬다」이고 7 은 「그 이동의 프로젝트 파급이 잠겨 있다」다. 순서가 뒤면
    사람이 선행을 비우고 다시 눌렀는데 **또 다른 거절**을 받는다.
    """
    client, _ = _stack(tmp_path)
    here = _project(client, "여기", "mina")
    there = _project(client, "저기", "mina")
    # 새 상위는 **다른 프로젝트**에 있고 담당자는 민아다 → 6 과 7 이 함께 걸린다.
    anchor = _task(client, MINA, "ord-anchor", title="저기의 민아 업무", project_id=there)
    mover = _task(client, MINA, "ord-mover", title="옮길 업무", project_id=here)
    lock = _task(client, MINA, "ord-lock", title="여기의 선행", project_id=here)
    locked = client.patch(
        f"/api/tasks/{mover['task_id']}", headers=MINA,
        json={"expected_version": mover["version"], "preceding_task_ids": [lock["task_id"]]},
    )
    assert locked.status_code == 200, locked.text
    _task(client, MINA, "ord-child", title="민아가 든 직속 하위", parent_task_id=mover["task_id"])

    denied = _move(client, MINA, mover["task_id"], parent_task_id=anchor["task_id"])
    assert denied.status_code == 409, denied.text
    detail = denied.json()["detail"]
    assert "중심 업무 밖에 놓입니다" in detail
    assert "선행업무를 먼저 비워야" not in detail


def test_the_gate_looks_at_direct_children_only_and_not_the_whole_subtree(tmp_path) -> None:
    """**자손 전체를 돌지 않는다 — 직속 하위만 본다** (SPEC-007 §4 · D-18).

    **왜 손자를 안 보는가.** 손자의 V-8 판정은 **자기 부모(= 이 업무의 하위)의 중심 업무 여부**를 보고,
    그 값은 이 이동으로 **바뀌지 않는다** — 이동이 이 업무의 «담당자»를 바꾸지 않기 때문이다.
    그래서 프로젝트 파급(자손 전체)과 **범위가 다르다**: 프로젝트는 값이 자손에 전파되고 V-8 은
    부모–자식 **한 쌍**의 판정이다.

    무대: 옮길 업무의 직속 하위는 **지호**가 들고(→ 어긋나지 않는다), 그 아래 손자는 **민아**가 든다.
    손자까지 봤다면 이 이동이 거절될 것이다. 통과해야 맞다.
    """
    client, _ = _stack(tmp_path)
    anchor = _task(client, MINA, "v8d-anchor", title="민아의 다른 중심 업무")
    mover = _task(client, MINA, "v8d-mover", title="옮길 업무")
    # 직속 하위는 지호 담당 — 민아가 옮겨도 이 쌍은 어긋나지 않는다.
    middle = _child_by_request(client, "v8d-req", "지호가 든 직속 하위", mover["task_id"], MINA, "jiho", JIHO)
    # 손자는 민아 담당 — 손자 판정은 `middle` 의 중심 업무 여부를 보고, 그 값은 이 이동과 무관하다.
    grandchild = _child_by_request(client, "v8d-grand", "민아가 든 손자", middle, JIHO, "mina", MINA)

    moved = _move(client, MINA, mover["task_id"], parent_task_id=anchor["task_id"])
    assert moved.status_code == 200, moved.text
    assert _detail(client, MINA, mover["task_id"])["parent"]["task_id"] == anchor["task_id"]
    # 손자의 상위는 그대로다 — **서버가 하위를 옮기지 않았다**.
    assert _detail(client, MINA, grandchild)["parent"]["task_id"] == middle


def test_a_child_held_by_someone_else_does_not_trip_the_gate(tmp_path) -> None:
    """담당자가 **다른** 직속 하위는 게이트에 걸리지 않는다 — V-8 은 「같은 사람의 직접 작업」이다."""
    client, _ = _stack(tmp_path)
    anchor = _task(client, MINA, "v8o-anchor", title="민아의 다른 중심 업무")
    mover = _task(client, MINA, "v8o-mover", title="옮길 업무")
    sent = client.post(
        "/api/work-requests", headers={**MINA, "Idempotency-Key": "v8o-req"},
        json={"title": "지호가 든 하위", "assignee_id": "jiho", "parent_task_id": mover["task_id"]},
    )
    assert sent.status_code == 201, sent.text
    _accept(client, sent.json()["request_id"], JIHO)

    moved = _move(client, MINA, mover["task_id"], parent_task_id=anchor["task_id"])
    assert moved.status_code == 200, moved.text


def test_clearing_the_parent_never_trips_the_children_gate(tmp_path) -> None:
    """상위를 **비우면** 이 업무는 반드시 중심 업무다 — 어긋남이 생길 수 없다."""
    client, _ = _stack(tmp_path)
    central = _task(client, MINA, "v8c-central", title="민아의 중심 업무")
    mover = _task(client, MINA, "v8c-mover", title="옮길 업무")
    _task(client, MINA, "v8c-child", title="민아가 든 하위", parent_task_id=mover["task_id"])
    # 먼저 어긋나지 않는 자리로 한 번 넣는다 — 지호가 든 중심 업무 아래.
    assert _move(client, MINA, mover["task_id"], parent_task_id=central["task_id"]).status_code == 409
    # 비우는 방향은 언제나 통과한다.
    assert _move(client, MINA, mover["task_id"], clear_parent=True).status_code == 200


# ---- 4. 검증 7 — 프로젝트가 자손 전체에 따라간다 -------------------------------------


def test_moving_the_parent_carries_the_project_down_to_grandchildren(tmp_path) -> None:
    """**손자·증손자까지** 프로젝트가 따라간다 — 직속 자식만 옮기면 실패다 (SPEC-007 §4 7번).

    이동 뒤 **그 트리 안에 프로젝트가 둘 이상인 업무가 없다.**
    """
    client, _ = _stack(tmp_path)
    here = _project(client, "여기", "mina")
    there = _project(client, "저기", "mina")
    destination = _task(client, MINA, "pr1-dst", title="저기의 상위", project_id=there)

    mover = _task(client, MINA, "pr1-1", title="옮길 업무", project_id=here)
    # **담당자를 번갈아 쌓는다** — V-8 이 같은 사람의 직접 작업 중첩을 생성에서 막는다.
    child = _child_by_request(client, "pr1-2", "자식", mover["task_id"], MINA, "jiho", JIHO)
    grand = _task(client, JIHO, "pr1-3", title="손자", parent_task_id=child)["task_id"]
    great = _child_by_request(client, "pr1-4", "증손자", grand, JIHO, "mina", MINA)
    subtree = [mover["task_id"], child, grand, great]
    for task_id in subtree[1:]:
        assert _detail(client, JIHO, task_id)["project_id"] == here, task_id

    moved = _move(client, MINA, mover["task_id"], parent_task_id=destination["task_id"])
    assert moved.status_code == 200, moved.text
    # **손자·증손자까지** 따라간다 — 직속 자식만 옮기면 여기서 실패한다.
    for task_id in subtree:
        assert _detail(client, JIHO, task_id)["project_id"] == there, task_id
    assert {_detail(client, JIHO, task_id)["project_id"] for task_id in subtree} == {there}


def test_a_new_parent_without_a_project_empties_the_whole_subtree(tmp_path) -> None:
    """새 상위에 프로젝트가 **없으면** 이 업무와 자손의 프로젝트가 **비워진다** (SPEC-007 §4 7번 표)."""
    client, _ = _stack(tmp_path)
    here = _project(client, "여기", "mina")
    destination = _task(client, MINA, "pr2-dst", title="프로젝트 없는 상위")
    mover = _task(client, MINA, "pr2-1", title="옮길 업무", project_id=here)
    child = _child_by_request(client, "pr2-2", "자식", mover["task_id"], MINA, "jiho", JIHO)
    grand = _task(client, JIHO, "pr2-3", title="손자", parent_task_id=child)["task_id"]

    moved = _move(client, MINA, mover["task_id"], parent_task_id=destination["task_id"])
    assert moved.status_code == 200, moved.text
    # **읽는 사람을 갈아 확인한다** — 프로젝트가 비면 프로젝트 축의 읽기가 함께 사라지므로,
    # 각 업무를 그것을 «든» 사람이 읽는다. 그 자체가 파급이 실제로 일어났다는 증거다.
    assert _detail(client, MINA, mover["task_id"])["project_id"] is None
    for task_id in (child, grand):
        assert _detail(client, JIHO, task_id)["project_id"] is None, task_id


def test_a_descendant_holding_a_predecessor_refuses_the_whole_move(tmp_path) -> None:
    """자손 하나가 남은 선행을 들고 있으면 **이동 전체가 409** 이고 **아무것도 움직이지 않는다**.

    프로젝트도 상위도 그대로다 — **부분 이동이 없다**. 그리고 **기존 오류 코드**를 그대로 쓴다
    (`WORK_PROJECT_LOCKED_BY_PREDECESSORS`) — 새로 짓지 않는다.
    """
    client, _ = _stack(tmp_path)
    here = _project(client, "여기", "mina")
    there = _project(client, "저기", "mina")
    destination = _task(client, MINA, "pr3-dst", title="저기의 상위", project_id=there)

    mover = _task(client, MINA, "pr3-1", title="옮길 업무", project_id=here)
    # 자손이 **같은 프로젝트 안에서** 선행을 들고 있다 — **손자**여야 「자손 전체」를 세는 것이 보인다.
    anchor = _task(client, MINA, "pr3-anchor", title="여기의 선행", project_id=here)
    child = _child_by_request(client, "pr3-2", "자식", mover["task_id"], MINA, "jiho", JIHO)
    grand = _task(client, JIHO, "pr3-3", title="선행을 든 손자", parent_task_id=child)
    locked = client.patch(
        f"/api/tasks/{grand['task_id']}", headers=JIHO,
        json={"expected_version": grand["version"], "preceding_task_ids": [anchor["task_id"]]},
    )
    assert locked.status_code == 200, locked.text

    denied = _move(client, MINA, mover["task_id"], parent_task_id=destination["task_id"])
    assert denied.status_code == 409, denied.text
    assert "선행업무를 먼저 비워야" in denied.json()["detail"]

    # **아무것도 움직이지 않았다** — 상위도 프로젝트도.
    assert _detail(client, MINA, mover["task_id"])["parent"] is None
    for task_id in (mover["task_id"], child, grand["task_id"]):
        assert _detail(client, JIHO, task_id)["project_id"] == here, task_id
    assert _detail(client, JIHO, grand["task_id"])["preceding_task_ids"] == [anchor["task_id"]]


def test_emptying_the_project_is_also_gated_by_a_remaining_predecessor(tmp_path) -> None:
    """새 상위에 프로젝트가 없어 **비워질 때**도 남은 선행이 같은 게이트에 걸린다.

    「선행이 하나라도 있으면 프로젝트는 필수」이기 때문이다 (SPEC-001 계승).
    """
    client, _ = _stack(tmp_path)
    here = _project(client, "여기", "mina")
    destination = _task(client, MINA, "pr4-dst", title="프로젝트 없는 상위")
    anchor = _task(client, MINA, "pr4-anchor", title="선행", project_id=here)
    mover = _task(client, MINA, "pr4-1", title="선행을 든 업무", project_id=here,
                  preceding_task_ids=[anchor["task_id"]])

    denied = _move(client, MINA, mover["task_id"], parent_task_id=destination["task_id"])
    assert denied.status_code == 409, denied.text
    assert _detail(client, MINA, mover["task_id"])["project_id"] == here
    assert _detail(client, MINA, mover["task_id"])["parent"] is None


def test_moving_inside_the_same_project_is_not_gated_by_predecessors(tmp_path) -> None:
    """**프로젝트가 안 바뀌면 선행 잠금이 걸리지 않는다** — 잠그는 것은 «프로젝트 파급»이다.

    게이트를 무조건 걸면 선행을 든 업무는 **같은 프로젝트 안에서도 상위를 옮길 수 없게** 되는데,
    SPEC-007 §4 7번은 「**프로젝트 파급**이 선행 잠금에 걸리지 않는가」를 묻는다 — 파급이 없으면
    잠글 것이 없다. 이 선택을 test 로 못 박는다.
    """
    client, _ = _stack(tmp_path)
    here = _project(client, "여기", "mina")
    destination = _task(client, MINA, "pr5-dst", title="같은 프로젝트의 상위", project_id=here)
    anchor = _task(client, MINA, "pr5-anchor", title="선행", project_id=here)
    mover = _task(client, MINA, "pr5-1", title="선행을 든 업무", project_id=here,
                  preceding_task_ids=[anchor["task_id"]])

    moved = _move(client, MINA, mover["task_id"], parent_task_id=destination["task_id"])
    assert moved.status_code == 200, moved.text
    assert _detail(client, MINA, mover["task_id"])["project_id"] == here
    assert _detail(client, MINA, mover["task_id"])["preceding_task_ids"] == [anchor["task_id"]]


def test_the_server_never_relocates_children_on_its_own(tmp_path) -> None:
    """**소급 재배치가 없다** — 어긋남이 되는 이동을 «거절»할 뿐이다 (SPEC-007 §1 Scope Out · EU-8).

    코드에 「서버가 하위의 상위를 바꾸는」 자리가 없음을 함께 센다: 상위를 쓰는 곳은 생성과 이 이동
    둘뿐이고, 둘 다 **대상 업무 자신의** 상위를 쓴다.
    """
    import ast
    from pathlib import Path

    client, _ = _stack(tmp_path)
    anchor, mover, child = _same_holder_tree(client)
    # 거절이다 — 서버가 하위를 다른 곳으로 옮겨 이동을 성사시키지 않는다.
    assert _move(client, MINA, mover["task_id"], parent_task_id=anchor["task_id"]).status_code == 409
    assert _detail(client, MINA, child["task_id"])["parent"]["task_id"] == mover["task_id"]

    # `parent_task_id` 에 **대입**하는 자리 전수 — application 층은 한 곳뿐이어야 한다.
    source = Path(__file__).resolve().parents[2] / "src/ax_workspace/modules/work/application.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Attribute) and target.attr == "parent_task_id"
    ]
    assert len(assignments) == 1, [ast.unparse(node) for node in assignments]
    # 그 한 곳이 **옮겨지는 업무 자신**이다 — 하위를 가리키는 변수가 아니다.
    assert ast.unparse(assignments[0]).startswith("task.parent_task_id ="), ast.unparse(assignments[0])


def test_the_project_field_and_the_parent_field_do_not_fight_in_one_patch(tmp_path) -> None:
    """한 `PATCH` 가 상위와 프로젝트를 함께 보내면 **하위 규칙이 이긴다**.

    이동이 먼저 일어나 이 업무가 하위가 되고, 「하위 업무의 프로젝트는 상위 업무를 따릅니다」가
    그 뒤의 `project_id` 를 거절한다 — 기존 거절 그대로다.
    """
    client, _ = _stack(tmp_path)
    here = _project(client, "여기", "mina")
    parent = _task(client, MINA, "pr6-dst", title="상위")
    mover = _task(client, MINA, "pr6-1", title="옮길 업무")

    denied = _move(client, MINA, mover["task_id"], parent_task_id=parent["task_id"], project_id=here)
    assert denied.status_code == 422, denied.text
    assert "하위 업무의 프로젝트는 상위 업무를 따릅니다" in denied.json()["detail"]
    # 아무것도 움직이지 않았다.
    assert _detail(client, MINA, mover["task_id"])["parent"] is None


def test_a_cancelled_task_still_cannot_be_moved(tmp_path) -> None:
    """검증 0 — 취소된 업무는 편집할 수 없다. **상위 칸이 열려도 그 문은 그대로다.**"""
    client, _ = _stack(tmp_path)
    parent = _task(client, MINA, "pr7-dst", title="상위")
    mover = _task(client, MINA, "pr7-1", title="취소될 업무")
    cancelled = client.post(
        f"/api/tasks/{mover['task_id']}/cancel", headers=MINA,
        json={"expected_version": mover["version"], "reason": "접는다"},
    )
    assert cancelled.status_code == 200, cancelled.text

    denied = client.patch(
        f"/api/tasks/{mover['task_id']}", headers=MINA,
        json={"expected_version": cancelled.json()["version"], "parent_task_id": parent["task_id"]},
    )
    assert denied.status_code == 422, denied.text
    assert "cancelled" in denied.json()["detail"]


def test_only_someone_who_may_edit_the_task_may_move_it(tmp_path) -> None:
    """상위 변경은 **그 업무의 값을 고칠 수 있는 사람**의 것이다 — 새 권한을 만들지 않는다 (§5)."""
    client, _ = _stack(tmp_path)
    project_id = _project(client, "한 프로젝트", "mina", "yuna")
    parent = _task(client, MINA, "pr8-dst", title="상위", project_id=project_id)
    mover = _task(client, MINA, "pr8-1", title="민아의 업무", project_id=project_id)

    # 유나는 같은 프로젝트라 **읽지만** 고칠 수 없다.
    assert _detail(client, YUNA, mover["task_id"])["access"] == "read_only"
    version = _detail(client, YUNA, mover["task_id"])["version"]
    denied = client.patch(
        f"/api/tasks/{mover['task_id']}", headers=YUNA,
        json={"expected_version": version, "parent_task_id": parent["task_id"]},
    )
    assert denied.status_code == 404, denied.text
    assert _detail(client, MINA, mover["task_id"])["parent"] is None


# ---- 5. 검수 반영 — 누출 둘 ------------------------------------------------------


def test_the_project_lock_refusal_hides_the_title_of_a_descendant_the_caller_cannot_read(tmp_path) -> None:
    """게이트 7 의 거절 문장이 **못 읽는 자손의 제목을 내지 않는다** (검수 W-5).

    게이트 6 은 같은 자리에서 「읽을 수 있는 것만 이름, 나머지는 건수」로 가리는데 게이트 7 은 앞판에서
    자손 전체를 돌며 **제목을 그대로** 실었다. 두 게이트의 규율을 맞춘다 — 거절 사유로 남의 업무
    제목을 알려 주면 그 자체가 곁수로다.

    무대: 민아가 든 `mover` 아래에 **지호가 세운** 자식과 손자가 있고 손자가 선행을 들고 있다.
    민아를 `여기` 프로젝트에서 빼면 **손자를 읽지 못한다** — 자기가 든 `mover` 는 그대로 읽는다.
    """
    client, _ = _stack(tmp_path)
    here = _project(client, "여기", "mina", "jiho")
    there = _project(client, "저기", "mina")
    destination = _task(client, MINA, "w5-dst", title="저기의 상위", project_id=there)
    mover = _task(client, MINA, "w5-mover", title="옮길 업무", project_id=here)
    # 지호가 `mover` 를 읽을 수 있으므로(같은 프로젝트) 그 아래에 자기 업무를 세운다 —
    # **요청 경로를 쓰지 않는다**: 민아가 요청자가 되면 V-21 이 하위 트리 전체를 민아에게 열어 준다.
    child = _task(client, JIHO, "w5-child", title="지호의 자식", parent_task_id=mover["task_id"])
    grand = _task(client, JIHO, "w5-grand", title="선행을 든 손자", parent_task_id=child["task_id"])
    anchor = _task(client, JIHO, "w5-anchor", title="여기의 선행", project_id=here)
    locked = client.patch(
        f"/api/tasks/{grand['task_id']}", headers=JIHO,
        json={"expected_version": grand["version"], "preceding_task_ids": [anchor["task_id"]]},
    )
    assert locked.status_code == 200, locked.text

    # 민아를 빼기 **전에는** 손자가 보인다 — 무대가 맞는지 먼저 확인한다.
    assert _detail(client, MINA, grand["task_id"])["title"] == "선행을 든 손자"
    released = client.delete(f"/api/projects/{here}/members/mina", headers=JIHO)
    assert released.status_code == 204, released.text
    assert client.get(f"/api/tasks/{grand['task_id']}", headers=MINA).status_code == 404

    denied = _move(client, MINA, mover["task_id"], parent_task_id=destination["task_id"])
    assert denied.status_code == 409, denied.text
    detail = denied.json()["detail"]
    # 코드와 문장 머리는 **그대로다** — 기존 계약을 바꾸지 않았다.
    assert "선행업무를 먼저 비워야" in detail
    # **제목은 없고 건수만 있다.**
    assert "선행을 든 손자" not in detail
    assert "볼 수 없는 업무 1건" in detail
    # 아무것도 움직이지 않았다.
    assert _detail(client, MINA, mover["task_id"])["parent"] is None
    assert _detail(client, MINA, mover["task_id"])["project_id"] == here


def test_the_project_lock_refusal_still_names_a_descendant_the_caller_can_read(tmp_path) -> None:
    """읽을 수 있는 자손은 **여전히 이름으로** 나온다 — 가리기가 이름을 통째로 없애지 않았다.

    W-5 를 고치면서 「전부 감춘다」로 가면 사람이 무엇을 비워야 하는지 알 수 없게 된다.
    """
    client, _ = _stack(tmp_path)
    here = _project(client, "여기", "mina")
    there = _project(client, "저기", "mina")
    destination = _task(client, MINA, "w5r-dst", title="저기의 상위", project_id=there)
    mover = _task(client, MINA, "w5r-mover", title="옮길 업무", project_id=here)
    anchor = _task(client, MINA, "w5r-anchor", title="여기의 선행", project_id=here)
    locked = client.patch(
        f"/api/tasks/{mover['task_id']}", headers=MINA,
        json={"expected_version": mover["version"], "preceding_task_ids": [anchor["task_id"]]},
    )
    assert locked.status_code == 200, locked.text

    denied = _move(client, MINA, mover["task_id"], parent_task_id=destination["task_id"])
    assert denied.status_code == 409, denied.text
    detail = denied.json()["detail"]
    assert "옮길 업무" in detail
    assert "볼 수 없는 업무" not in detail


def test_a_parent_whose_project_the_caller_cannot_read_is_refused(tmp_path) -> None:
    """**못 읽는 프로젝트로 서브트리를 밀어 넣을 수 없다** (검수 W-6).

    상위 P 를 읽을 수는 있으나 **P 의 프로젝트는 못 읽는** 사람이 자기 트리를 그 프로젝트로 밀어
    넣으면, 그 프로젝트를 아는 사람들의 화면에 **그가 읽을 수도 없는 자리**로 업무가 나타난다.

    거절은 **기존 프로젝트 읽기 거절 그대로**다(`project was not found`, 404) — 새 코드를 만들지 않았다.

    ⚠ **생성 경로는 이 가드를 지나지 않는다 — 의도한 차이다.** 그쪽은 이 판의 범위 밖이고 기존
    계약·테스트가 그 모양에 걸려 있다. 아래 `…_creation_path_still_inherits_without_the_guard` 가
    그 갈림을 못질한다.
    """
    client, _ = _stack(tmp_path)
    secret = _project(client, "지호만 아는 프로젝트", "jiho")
    # 참조자(cc)는 **그 업무 하나**를 읽는다 — 프로젝트를 읽는 것이 아니다. 그것이 이 무대의 핵이다.
    destination = _task(client, JIHO, "w6-dst", title="비공개 프로젝트의 상위", project_id=secret,
                        cc_member_ids=["mina"])
    mover = _task(client, MINA, "w6-mover", title="민아의 업무")

    assert _detail(client, MINA, destination["task_id"])["access"] == "read_only"
    assert [row["project_id"] for row in client.get("/api/projects", headers=MINA).json()] == []

    denied = _move(client, MINA, mover["task_id"], parent_task_id=destination["task_id"])
    assert denied.status_code == 404, denied.text
    assert denied.json()["detail"] == "project was not found"
    # **아무것도 움직이지 않았다** — 상위도 프로젝트도.
    view = _detail(client, MINA, mover["task_id"])
    assert view["parent"] is None and view["project_id"] is None


def test_the_creation_path_still_inherits_without_the_guard(tmp_path) -> None:
    """**생성은 가드 없이 상위를 따른다 — 그대로 두었다** (검수 W-6 의 범위 제약).

    W-6 을 「`project_for` 를 고친다」로 닫으면 생성 경로의 기존 계약이 함께 움직인다. 그래서
    **이동 경로에만** 가드를 걸었고, 두 경로가 갈리는 사실을 여기서 못질한다 — 나중에 누가
    「일관성」을 이유로 한쪽을 맞추려 할 때 이 테스트가 그 결정이 있었음을 말한다.
    """
    client, _ = _stack(tmp_path)
    secret = _project(client, "지호만 아는 프로젝트", "jiho")
    parent = _task(client, JIHO, "w6c-dst", title="비공개 프로젝트의 상위", project_id=secret,
                   cc_member_ids=["mina"])

    # 같은 상위 아래에 **새 업무를 만드는** 것은 통과하고 프로젝트를 물려받는다.
    created = _task(client, MINA, "w6c-new", title="새 하위", parent_task_id=parent["task_id"])
    assert created["project_id"] == secret
    # 그리고 그 프로젝트는 민아가 **여전히 읽을 수 없다** — 가드가 없다는 사실 자체다.
    assert client.get(f"/api/projects/{secret}", headers=MINA).status_code in {403, 404}
