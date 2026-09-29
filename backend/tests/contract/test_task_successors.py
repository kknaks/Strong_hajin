"""후행 — **같은 표를 반대로 읽는다** (SPEC-007 §4 · WORK-007 Phase B-1 · B-3).

**후행은 넷째 관계가 아니다.** `task_predecessors` 행 하나가 양쪽을 답하고, 이 파일이 닫는 것은 셋이다.

1. **쓰기 경로가 없다** — B 가 A 를 선행으로 고르면 A 의 후행에 B 가 서고, 빠지면 사라진다.
2. **못 읽는 후행은 건수로 접는다** — 배열에 자리도 `task_id` 도 없다. 선행과 **반대 규칙**이다.
3. **해제는 전용 명령 하나**다 — A 쪽에서도 열리고, B 의 회차로 판정하며, 멱등이 아니다.

**목록·프로젝트 상세에는 싣지 않는다**(N×M) — 그 사실도 여기서 센다.
"""
import json
from uuid import uuid4

from sqlalchemy import select

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import TaskPredecessorRecord, TaskProposalRecord, make_session_factory
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


def _release(client, headers, task_id: str, successor_task_id: str, expected_version: int):
    return client.delete(
        f"/api/tasks/{task_id}/successors/{successor_task_id}",
        headers=headers,
        params={"expected_version": expected_version},
    )


# ---- 1. 쓰기 경로가 없다 — 같은 행을 반대로 읽은 것이다 -----------------------------


def test_choosing_a_predecessor_makes_the_other_task_a_successor_without_any_write(tmp_path) -> None:
    """**B 가 A 를 선행으로 고르면 A 의 후행에 B 가 선다** — 후행을 쓰는 명령을 한 번도 안 불렀다.

    그리고 저장된 행은 **하나**다. 양방향으로 두 행을 세우지 않는다 (SPEC-007 §4 Data Contract).
    """
    client, database_url = _stack(tmp_path)
    project_id = _project(client, "mina")
    first = _task(client, MINA, "s1-1", title="먼저", project_id=project_id)
    later = _task(client, MINA, "s1-2", title="나중", project_id=project_id, preceding_task_ids=[first["task_id"]])

    view = _detail(client, MINA, first["task_id"])
    assert [row["task_id"] for row in view["successors"]] == [later["task_id"]]
    assert view["hidden_successor_count"] == 0
    # 후행 줄의 모양 — `TaskSummaryView` + `version` (SPEC-007 §4).
    row = view["successors"][0]
    assert row["title"] == "나중" and row["state"] == "open"
    assert row["assignee"]["member_id"] == "mina"
    assert row["due_date"] is None
    assert row["version"] == later["version"]

    # **후행 쪽에는 후행이 없다** — 방향이 있는 관계다.
    assert _detail(client, MINA, later["task_id"])["successors"] == []

    # 저장은 **행 하나**다. 반대 방향 행이 함께 서 있으면 실패다.
    factory = make_session_factory(database_url)
    with factory() as session:
        rows = session.scalars(select(TaskPredecessorRecord)).all()
        assert len(rows) == 1
        assert str(rows[0].task_id) == later["task_id"]
        assert str(rows[0].predecessor_task_id) == first["task_id"]


def test_dropping_the_predecessor_drops_the_successor_too(tmp_path) -> None:
    """A 가 B 의 선행에서 빠지면 **A 의 후행에서 B 가 사라진다** — 같은 행이므로 같이 움직인다."""
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina")
    first = _task(client, MINA, "s2-1", title="먼저", project_id=project_id)
    later = _task(client, MINA, "s2-2", title="나중", project_id=project_id, preceding_task_ids=[first["task_id"]])
    assert len(_detail(client, MINA, first["task_id"])["successors"]) == 1

    cleared = client.patch(
        f"/api/tasks/{later['task_id']}",
        headers=MINA,
        json={"expected_version": later["version"], "preceding_task_ids": []},
    )
    assert cleared.status_code == 200, cleared.text
    view = _detail(client, MINA, first["task_id"])
    assert view["successors"] == [] and view["hidden_successor_count"] == 0


def test_a_cancelled_or_finished_successor_stays_in_the_list_with_its_state(tmp_path) -> None:
    """**취소·완료된 후행도 배열에 남는다** — 관계가 살아 있으면 실리고 상태가 그 줄에 보인다.

    선행 쪽이 같은 모양이다(활성 여부만 보고 상태는 안 본다).
    """
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina")
    first = _task(client, MINA, "s3-1", title="먼저", project_id=project_id)
    later = _task(client, MINA, "s3-2", title="나중", project_id=project_id, preceding_task_ids=[first["task_id"]])

    cancelled = client.post(
        f"/api/tasks/{later['task_id']}/cancel",
        headers=MINA,
        json={"expected_version": later["version"], "reason": "필요 없어졌다"},
    )
    assert cancelled.status_code == 200, cancelled.text
    row = _detail(client, MINA, first["task_id"])["successors"][0]
    assert row["task_id"] == later["task_id"] and row["state"] == "cancelled"


def test_successors_are_ordered_by_when_the_relation_was_made(tmp_path) -> None:
    """정렬은 **관계가 선 순서**(오래된 것이 위)다 — `position` 이 아니다 (SPEC-007 §4).

    `position` 은 **그 업무가 선행을 고른 순서**라 내 화면에서 뜻이 없다. 두 후행이 각자
    `position=0` 으로 서므로, `position` 으로 정렬하면 차례가 조회마다 달라진다.
    """
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina")
    first = _task(client, MINA, "s4-1", title="먼저", project_id=project_id)
    early = _task(client, MINA, "s4-2", title="먼저 기다린 쪽", project_id=project_id, preceding_task_ids=[first["task_id"]])
    late = _task(client, MINA, "s4-3", title="나중에 기다린 쪽", project_id=project_id, preceding_task_ids=[first["task_id"]])

    order = [row["task_id"] for row in _detail(client, MINA, first["task_id"])["successors"]]
    assert order == [early["task_id"], late["task_id"]]


# ---- 2. 못 읽는 후행은 건수로 접는다 ----------------------------------------------


def test_a_successor_the_reader_cannot_open_is_only_a_count(tmp_path) -> None:
    """**배열에 자리도 `task_id` 도 없다.** 선행과 **반대 규칙**인 것이 계약이다 (D-10).

    선행은 「시작을 막는 이유」라 못 읽어도 자리를 남기는데, 후행은 막는 것이 없다. 그리고 식별자를
    내면 그것으로 해제 명령을 부를 수 있어 「비공개 후행에는 입구가 없다」(D-16)가 뒷문으로 깨진다.
    """
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina", "jiho")
    first = _task(client, MINA, "s5-1", title="민아의 선행", project_id=project_id)
    hidden = _task(client, JIHO, "s5-2", title="지호만 아는 후행", project_id=project_id, preceding_task_ids=[first["task_id"]])

    # 같은 프로젝트라 민아도 읽는다 — 먼저 그 사실을 확인한다.
    assert _detail(client, MINA, first["task_id"])["successors"][0]["title"] == "지호만 아는 후행"

    # **읽는 쪽**이 프로젝트 밖으로 나가면 못 읽는다 — 업무는 프로젝트에 남으므로 상대를 빼서는
    # 가려지지 않는다. 민아는 자기가 든 `first` 는 그대로 읽는다.
    released = client.delete(f"/api/projects/{project_id}/members/mina", headers=JIHO)
    assert released.status_code == 204, released.text
    view = _detail(client, MINA, first["task_id"])
    assert view["successors"] == []
    assert view["hidden_successor_count"] == 1
    # **식별자도 제목도 어디에도 없다.** 위 `== []` 가 배열을 비웠다고만 말하므로, 응답 «전체» 를
    # 직렬화해 그 두 문자열을 찾는다 — 어느 칸에 새더라도 여기서 걸린다 (검수 W-8③).
    body = json.dumps(view, ensure_ascii=False)
    assert hidden["task_id"] not in body
    assert "지호만 아는 후행" not in body


def test_a_mix_of_readable_and_hidden_successors_splits_into_the_array_and_the_count(tmp_path) -> None:
    """**섞인 갈래** — 읽을 수 있는 후행은 배열에, 못 읽는 것은 건수에 (검수 W-8①).

    앞판의 단언은 「전부 보인다(0)」와 「전부 가려진다(1)」 두 끝뿐이었다. 가운데가 비어 있으면
    **배열과 건수가 같은 목록을 두 번 세거나 한 번도 안 세는** 실수가 통과한다.
    """
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina", "jiho")
    first = _task(client, MINA, "s5m-1", title="민아의 선행", project_id=project_id)
    mine = _task(client, MINA, "s5m-2", title="민아가 든 후행", project_id=project_id, preceding_task_ids=[first["task_id"]])
    theirs = _task(client, JIHO, "s5m-3", title="지호만 아는 후행", project_id=project_id, preceding_task_ids=[first["task_id"]])

    # 프로젝트 안에서는 둘 다 보인다 — 먼저 그 사실을 확인한다.
    both = _detail(client, MINA, first["task_id"])
    assert [row["task_id"] for row in both["successors"]] == [mine["task_id"], theirs["task_id"]]
    assert both["hidden_successor_count"] == 0

    # 민아가 프로젝트 밖으로 나가면 **자기가 든 것만** 남는다.
    released = client.delete(f"/api/projects/{project_id}/members/mina", headers=JIHO)
    assert released.status_code == 204, released.text
    view = _detail(client, MINA, first["task_id"])
    assert [row["task_id"] for row in view["successors"]] == [mine["task_id"]]
    assert view["successors"][0]["title"] == "민아가 든 후행"
    assert view["hidden_successor_count"] == 1
    # **가려진 쪽은 식별자도 제목도 없다** — 배열이 비지 않았으므로 이 단언이 실제로 일한다.
    body = json.dumps(view, ensure_ascii=False)
    assert theirs["task_id"] not in body
    assert "지호만 아는 후행" not in body


def test_the_two_successor_fields_are_response_only_and_refused_as_input(tmp_path) -> None:
    """**`successors`·`hidden_successor_count` 는 응답 전용이다** (SPEC-007 § Validation · 검수 W-8②).

    `extra='forbid'` 가 구조적으로 보장하지만 **아무도 못질하지 않았다** — 나중에 그것이 풀리면
    조용히 열린다. 편집(`PATCH`)과 생성(`POST`) 두 입구에 못을 박는다.
    """
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina")
    first = _task(client, MINA, "s5r-1", title="선행", project_id=project_id)
    later = _task(client, MINA, "s5r-2", title="후행", project_id=project_id, preceding_task_ids=[first["task_id"]])

    for field, value in (("successors", [{"task_id": later["task_id"]}]), ("hidden_successor_count", 3)):
        edited = client.patch(
            f"/api/tasks/{first['task_id']}", headers=MINA,
            json={"expected_version": first["version"], field: value},
        )
        assert edited.status_code == 422, (field, edited.text)
        assert field in edited.text, (field, edited.text)
        created = client.post(
            "/api/tasks", headers={**MINA, "Idempotency-Key": f"s5r-{field}"},
            json={"title": "입력으로 오면 안 되는 값", field: value},
        )
        assert created.status_code == 422, (field, created.text)
        assert field in created.text, (field, created.text)

    # **아무것도 바뀌지 않았다** — 거절이 값을 흘려 넣지 않는다.
    view = _detail(client, MINA, first["task_id"])
    assert view["version"] == first["version"]
    assert [row["task_id"] for row in view["successors"]] == [later["task_id"]]
    assert view["hidden_successor_count"] == 0


def test_the_read_only_branch_carries_the_successor_fields_too(tmp_path) -> None:
    """**`access: "read_only"` 상세에도 두 칸이 실린다** — 갈래를 가르지 않는다 (코디 판정 · W-3).

    읽기 전용은 **보는 범위가 아니라 고치는 범위**다. 사라지는 것은 고치는 입구뿐이다.
    """
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina", "yuna")
    first = _task(client, MINA, "s6-1", title="참조자가 있는 업무", project_id=project_id, cc_member_ids=["yuna"])
    later = _task(client, MINA, "s6-2", title="나중", project_id=project_id, preceding_task_ids=[first["task_id"]])

    view = _detail(client, YUNA, first["task_id"])
    assert view["access"] == "read_only"
    assert [row["task_id"] for row in view["successors"]] == [later["task_id"]]
    assert view["hidden_successor_count"] == 0
    # `references` 는 이 갈래에서 **여전히 빠져 있다** — SPEC-007 이 그것을 고치지 않았다(§2.7).
    assert "references" not in view


def test_successors_are_not_in_the_list_or_the_project_detail(tmp_path) -> None:
    """**상세 하나에만 싣는다.** 목록에 실으면 줄 수 × 후행 수 만큼 권한 판정이 돈다 (SPEC-007 §4).

    프로젝트 상세도 그대로다 — 거기서는 **클라이언트가 선행을 뒤집는다**(SPEC-005 D-07).
    """
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina")
    first = _task(client, MINA, "s7-1", title="먼저", project_id=project_id)
    _task(client, MINA, "s7-2", title="나중", project_id=project_id, preceding_task_ids=[first["task_id"]])

    for path in ("/api/tasks", "/api/my-work"):
        rows = client.get(path, headers=MINA).json()
        assert rows, path
        for row in rows:
            assert "successors" not in row, (path, sorted(row))
            assert "hidden_successor_count" not in row, (path, sorted(row))

    project = client.get(f"/api/projects/{project_id}", headers=MINA).json()
    for row in project["tasks"]:
        assert "successors" not in row and "hidden_successor_count" not in row, sorted(row)
        # 역산의 재료는 그대로다.
        assert "preceding_task_ids" in row


def test_a_chain_of_successors_does_not_walk_the_read_probe_round_and_round(tmp_path) -> None:
    """**되돌이 방지 빗장** — 후행 판정이 읽기 판정 사슬을 돌지 않는다 (SPEC-007 §4 ⚠).

    A → B → C → D 사슬에서 A 의 상세가 B 를 읽을 수 있나 묻고, 그 판정이 B 의 상세를 만들면서
    다시 C 를 묻는 식으로 겹쳐 쌓일 수 있다. 빗장이 그 안쪽에서 관계 요약을 접으므로 **밖으로 나가는
    답은 한 겹**이다 — 선행 요약이 같은 자리에 같은 빗장을 갖고 있다.
    """
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina")
    chain = [_task(client, MINA, "s8-0", title="0", project_id=project_id)]
    for index in range(1, 5):
        chain.append(
            _task(
                client, MINA, f"s8-{index}", title=str(index), project_id=project_id,
                preceding_task_ids=[chain[-1]["task_id"]],
            )
        )
    view = _detail(client, MINA, chain[0]["task_id"])
    # **한 겹만** 나온다 — 직속 후행 하나이고 그 줄이 자기 후행을 데려오지 않는다.
    assert [row["task_id"] for row in view["successors"]] == [chain[1]["task_id"]]
    assert set(view["successors"][0]) == {"task_id", "title", "state", "due_date", "assignee", "version"}


def test_the_mcp_detail_tool_carries_the_same_two_fields(tmp_path) -> None:
    """**새 도구를 내지 않는다** — 업무 상세를 내는 기존 도구가 같은 확장을 실어 나른다 (SPEC-007 §4)."""
    import asyncio

    from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server

    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina")
    first = _task(client, MINA, "s9-1", title="먼저", project_id=project_id)
    later = _task(client, MINA, "s9-2", title="나중", project_id=project_id, preceding_task_ids=[first["task_id"]])

    server = _create_bound_persona_server(McpReportsFacade(client.app.state.workflow_application._settings, "mina"))
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert not [name for name in names if "successor" in name], sorted(names)

    result = asyncio.run(server.call_tool("task_get", {"task_id": first["task_id"]}))
    assert not result.is_error, result
    payload = result.structured_content
    assert [row["task_id"] for row in payload["successors"]] == [later["task_id"]]
    assert payload["hidden_successor_count"] == 0


# ---- 3. 후행 해제 — 전용 명령 하나 ------------------------------------------------


def test_the_owner_of_a_predecessor_may_release_their_own_successor(tmp_path) -> None:
    """**A 의 담당자가 자기 후행을 해제할 수 있다** — B 를 고칠 권한이 없어도 된다 (OQ-708).

    바뀌는 것은 **B 의 선행 배열**이고 회차도 B 의 것이 오른다. **A 의 회차는 움직이지 않는다.**
    행은 **남아 있고** `released_at` 이 찬다.
    """
    client, database_url = _stack(tmp_path)
    project_id = _project(client, "mina", "jiho")
    first = _task(client, MINA, "r1-1", title="민아의 선행", project_id=project_id)
    later = _task(client, JIHO, "r1-2", title="지호의 후행", project_id=project_id, preceding_task_ids=[first["task_id"]])

    # 민아는 지호의 업무를 **고칠 수 없다** — 그래도 해제는 통과한다.
    denied = client.patch(
        f"/api/tasks/{later['task_id']}", headers=MINA,
        json={"expected_version": later["version"], "title": "민아가 고친다"},
    )
    assert denied.status_code == 404, denied.text

    before = _detail(client, MINA, first["task_id"])["version"]
    released = _release(client, MINA, first["task_id"], later["task_id"], later["version"])
    assert released.status_code == 200, released.text
    body = released.json()
    assert body["successors"] == [] and body["hidden_successor_count"] == 0
    assert body["task_id"] == first["task_id"] and body["successor_task_id"] == later["task_id"]
    # **B 의 회차가 올랐고** A 는 그대로다.
    assert body["task_version"] == later["version"] + 1
    assert _detail(client, MINA, first["task_id"])["version"] == before

    # **행은 지워지지 않고 닫힌다.**
    factory = make_session_factory(database_url)
    with factory() as session:
        rows = session.scalars(select(TaskPredecessorRecord)).all()
        assert len(rows) == 1 and rows[0].released_at is not None
        assert rows[0].released_by == "mina"
        # **제안 행이 0건이다** — 동의를 기다리는 상태를 만들지 않는다.
        assert session.scalars(select(TaskProposalRecord)).all() == []

    # B 쪽에서도 선행이 사라졌다.
    assert _detail(client, JIHO, later["task_id"])["preceding_task_ids"] == []


def test_the_owner_of_the_successor_may_close_the_same_relation(tmp_path) -> None:
    """**B 의 값을 고칠 수 있는 사람도** 같은 관계를 닫는다 — 기존 경로가 이미 그렇다."""
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina", "jiho")
    first = _task(client, MINA, "r2-1", title="민아의 선행", project_id=project_id)
    later = _task(client, JIHO, "r2-2", title="지호의 후행", project_id=project_id, preceding_task_ids=[first["task_id"]])

    released = _release(client, JIHO, first["task_id"], later["task_id"], later["version"])
    assert released.status_code == 200, released.text
    assert released.json()["successors"] == []


def test_a_bystander_who_may_edit_neither_side_gets_403(tmp_path) -> None:
    """**둘 중 어느 쪽도 고칠 수 없으면 403** 이다 (SPEC-007 §5 권한)."""
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina", "jiho", "yuna")
    first = _task(client, MINA, "r3-1", title="민아의 선행", project_id=project_id)
    later = _task(client, JIHO, "r3-2", title="지호의 후행", project_id=project_id, preceding_task_ids=[first["task_id"]])

    # 유나는 같은 프로젝트라 **둘 다 읽지만** 어느 쪽도 고칠 수 없다.
    assert _detail(client, YUNA, first["task_id"])["access"] == "read_only"
    denied = _release(client, YUNA, first["task_id"], later["task_id"], later["version"])
    assert denied.status_code == 403, denied.text
    assert "자격" in denied.json()["detail"]


def test_releasing_twice_is_not_idempotent(tmp_path) -> None:
    """**멱등이 아니다** — 이미 닫힌 관계에는 대상이 없으므로 404 다.

    두 사람이 같은 관계를 닫으면 **먼저가 이기고 늦은 쪽이 이 404 를 본다** — 그것이 충돌을 없애는
    방식이다(관계 하나에 고치는 사람이 둘이다).
    """
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina", "jiho")
    first = _task(client, MINA, "r4-1", title="선행", project_id=project_id)
    later = _task(client, JIHO, "r4-2", title="후행", project_id=project_id, preceding_task_ids=[first["task_id"]])

    assert _release(client, MINA, first["task_id"], later["task_id"], later["version"]).status_code == 200
    # 늦은 쪽 — 회차가 맞더라도 **대상이 없다.**
    again = _release(client, JIHO, first["task_id"], later["task_id"], later["version"] + 1)
    assert again.status_code == 404, again.text


def test_a_stale_successor_version_is_a_conflict(tmp_path) -> None:
    """회차가 어긋나면 **409** 다 — `expected_version` 은 **B 의 회차**이고 후행 줄이 낸 값이다."""
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina")
    first = _task(client, MINA, "r5-1", title="선행", project_id=project_id)
    later = _task(client, MINA, "r5-2", title="후행", project_id=project_id, preceding_task_ids=[first["task_id"]])

    stale = _release(client, MINA, first["task_id"], later["task_id"], later["version"] + 5)
    assert stale.status_code == 409, stale.text
    # 후행 줄이 내는 값이 그 회차다 — 그것으로 부르면 통과한다.
    row = _detail(client, MINA, first["task_id"])["successors"][0]
    assert _release(client, MINA, first["task_id"], later["task_id"], row["version"]).status_code == 200


def test_releasing_the_last_predecessor_opens_the_successors_start_gate(tmp_path) -> None:
    """해제 뒤 **B 의 시작 게이트가 열린다**(다른 선행이 없으면) — 그것이 이 명령의 결과다.

    ⚠ **B 의 담당자는 그것을 모른다.** 알림의 자리이고 **이 판의 범위 밖**이다(SPEC-007 §5 · D-20) —
    그래서 이 테스트는 게이트가 열리는 것만 세고 알림을 세지 않는다.
    """
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina", "jiho")
    first = _task(client, MINA, "r6-1", title="안 끝난 선행", project_id=project_id)
    later = _task(client, JIHO, "r6-2", title="후행", project_id=project_id, preceding_task_ids=[first["task_id"]])

    blocked = client.post(
        f"/api/tasks/{later['task_id']}/start", headers=JIHO, json={"expected_version": later["version"]}
    )
    assert blocked.status_code == 409, blocked.text
    assert "선행업무" in blocked.json()["detail"]

    released = _release(client, MINA, first["task_id"], later["task_id"], later["version"])
    assert released.status_code == 200, released.text
    started = client.post(
        f"/api/tasks/{later['task_id']}/start", headers=JIHO,
        json={"expected_version": released.json()["task_version"]},
    )
    assert started.status_code == 200, started.text


def test_a_relation_that_never_existed_is_the_same_404(tmp_path) -> None:
    """없는 것과 못 읽는 것과 이미 닫힌 것을 **같은 말로** 답한다 (SPEC-007 § Case Matrix)."""
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina")
    first = _task(client, MINA, "r7-1", title="선행", project_id=project_id)
    unrelated = _task(client, MINA, "r7-2", title="남", project_id=project_id)

    missing = _release(client, MINA, first["task_id"], unrelated["task_id"], unrelated["version"])
    assert missing.status_code == 404, missing.text
    # 아예 없는 업무도 같은 말이다.
    ghost = _release(client, MINA, first["task_id"], str(uuid4()), 1)
    assert ghost.status_code == 404, ghost.text


def test_a_private_successor_cannot_be_released_through_this_command(tmp_path) -> None:
    """**비공개 후행에는 입구가 없다** (D-16 · D-10).

    식별자가 응답에 내려오지 않으므로 화면이 단추를 그리지 않고, 식별자를 지어내 불러도 **못 읽는
    업무는 대상이 아니다** — 없는 것과 같은 404 다.
    """
    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina", "jiho")
    first = _task(client, MINA, "r8-1", title="민아의 선행", project_id=project_id)
    hidden = _task(client, JIHO, "r8-2", title="지호만 아는 후행", project_id=project_id, preceding_task_ids=[first["task_id"]])
    released = client.delete(f"/api/projects/{project_id}/members/mina", headers=JIHO)
    assert released.status_code == 204, released.text

    view = _detail(client, MINA, first["task_id"])
    assert view["successors"] == [] and view["hidden_successor_count"] == 1
    denied = _release(client, MINA, first["task_id"], hidden["task_id"], hidden["version"])
    assert denied.status_code == 404, denied.text
    # 관계는 **그대로 살아 있다** — 못 읽는 사람이 남의 관계를 닫지 못한다.
    assert _detail(client, JIHO, hidden["task_id"])["preceding_task_ids"] == [first["task_id"]]


def test_the_release_command_never_calls_the_notification_module(tmp_path, monkeypatch) -> None:
    """**알림이 나가지 않는다** — 이 판의 범위 밖임이 문서와 코드 주석에 명시되어 있다 (D-20).

    `modules/notifications.py` 가 이미 있으므로 「없어서 안 불렀다」가 근거가 될 수 없다.
    **불릴 수 있는 자리를 걸어 두고 0건임을 센다.**
    """
    from ax_workspace.platform import notifications as notifications_module

    client, _ = _stack(tmp_path)
    project_id = _project(client, "mina", "jiho")
    first = _task(client, MINA, "r9-1", title="선행", project_id=project_id)
    later = _task(client, JIHO, "r9-2", title="후행", project_id=project_id, preceding_task_ids=[first["task_id"]])

    calls: list[tuple] = []
    original = notifications_module.SqlAlchemyNotificationRepository.emit

    def _counted(self, *args, **kwargs):
        calls.append((args, kwargs))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(notifications_module.SqlAlchemyNotificationRepository, "emit", _counted)
    assert _release(client, MINA, first["task_id"], later["task_id"], later["version"]).status_code == 200
    assert calls == []
