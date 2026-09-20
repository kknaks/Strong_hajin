"""참조 수신함 읽음 — 읽으면 **수신함에서만** 접힌다 (SPEC-001 U-12 · §6 · DEC-001 D-19).

여기서 닫는 것은 넷이다.

1. **읽음은 사용자별이다** — 내가 읽어도 다른 참조자의 수신함은 그대로다.
2. **필터는 수신함 `reference` 갈래 하나에만 걸린다** — 요청 목록·업무 조회·`work` 갈래에는 없다.
3. **멱등이다** — 두 번째도 200 이고 `read_at` 이 처음 값 그대로이며, 요청 회차도 CC 관계도 그대로다.
4. **참조자만이다** — 아니면 403, 없거나 못 읽으면 404(같은 말).
"""
import pytest
from sqlalchemy import select

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import (
    ResourceRelationshipRecord,
    WorkRequestReadReceiptRecord,
    make_session_factory,
)
from fastapi.testclient import TestClient

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
YUNA = {"X-Demo-Persona": "yuna"}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    return TestClient(create_app(settings)), database_url


def _sent(client, *, cc: list[str], key: str, title: str = "참조가 걸린 요청") -> dict:
    response = client.post(
        "/api/work-requests",
        headers={**MINA, "Idempotency-Key": key},
        json={"title": title, "assignee_id": "jiho", "cc_member_ids": cc},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _inbox(client, headers) -> list[dict]:
    response = client.get("/api/work-requests/inbox", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _categories(client, headers) -> dict[str, set[str]]:
    rows: dict[str, set[str]] = {"work": set(), "reference": set()}
    for row in _inbox(client, headers):
        rows[row["category"]].add(row["request_id"])
    return rows


# ---- 읽으면 수신함에서만 접힌다 -------------------------------------------------


def test_reading_a_reference_folds_it_out_of_my_inbox_and_nowhere_else(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    request = _sent(client, cc=["yuna"], key="fold-once")

    assert _categories(client, YUNA)["reference"] == {request["request_id"]}

    read = client.post(f"/api/work-requests/{request['request_id']}/read", headers=YUNA)
    assert read.status_code == 200, read.text
    assert read.json()["request_id"] == request["request_id"] and read.json()["read"] is True
    first_read_at = read.json()["read_at"]

    # 수신함에서만 사라진다.
    assert _categories(client, YUNA)["reference"] == set()

    # **요청 목록(`참조 업무` 탭의 원천)에는 읽음 필터가 없다** — 읽은 것도 계속 나온다.
    listed = client.get("/api/work-requests", headers=YUNA)
    assert listed.status_code == 200, listed.text
    assert request["request_id"] in {row["request_id"] for row in listed.json()}
    # 상세도 그대로 열린다.
    assert client.get(f"/api/work-requests/{request['request_id']}", headers=YUNA).status_code == 200

    # **업무 조회에도 필터가 없다.**
    assert request["task_id"] in {row["task_id"] for row in client.get("/api/tasks", headers=YUNA).json()}
    assert client.get(f"/api/tasks/{request['task_id']}", headers=YUNA).status_code == 200

    # **CC 관계가 지워지지 않는다** — 읽은 뒤에도 그 사람은 참조자다.
    assert client.get(f"/api/work-requests/{request['request_id']}", headers=YUNA).json()["cc_member_ids"] == ["yuna"]
    assert first_read_at


def test_a_second_read_is_the_same_receipt_and_never_touches_the_request_row(tmp_path) -> None:
    """**멱등이다** — 두 번째도 200 이고 `read_at` 이 처음 값 그대로다. 회차도 오르지 않는다."""
    client, database_url = _stack(tmp_path)
    request = _sent(client, cc=["yuna"], key="idempotent-read")
    before = client.get(f"/api/work-requests/{request['request_id']}", headers=MINA).json()

    first = client.post(f"/api/work-requests/{request['request_id']}/read", headers=YUNA).json()
    second = client.post(f"/api/work-requests/{request['request_id']}/read", headers=YUNA)
    assert second.status_code == 200, second.text
    assert second.json() == first

    after = client.get(f"/api/work-requests/{request['request_id']}", headers=MINA).json()
    # **요청 행의 회차를 올리지 않는다** — 남이 들고 있던 낙관적 잠금이 그대로 유효하다.
    assert after["version"] == before["version"] and after["state"] == before["state"]
    withdrawn = client.post(
        f"/api/work-requests/{request['request_id']}/withdraw",
        headers=MINA,
        json={"expected_version": before["version"]},
    )
    assert withdrawn.status_code == 200, withdrawn.text

    with make_session_factory(database_url)() as session:
        assert len(session.scalars(select(WorkRequestReadReceiptRecord)).all()) == 1


def test_two_reads_racing_in_one_session_converge_on_a_single_row(tmp_path) -> None:
    """같은 사람의 **동시 두 번**이 행 하나다 — 유일성 제약이 답하고 진 쪽은 이긴 행을 읽는다."""
    client, database_url = _stack(tmp_path)
    request = _sent(client, cc=["yuna"], key="racing-read")
    application = client.app.state.workflow_application
    principal = application.authenticated_principal("yuna")
    from uuid import UUID

    from ax_workspace.platform.work_tasks import SqlAlchemyWorkRequestRepository

    request_id = UUID(request["request_id"])
    # 두 transaction 이 **각자 「없다」를 보고** 둘 다 넣으러 간다 — 제약이 둘째를 거절하고,
    # 그 쪽은 이긴 행을 읽어 같은 시각을 돌려준다.
    factory = make_session_factory(database_url)
    with factory() as first_session, factory() as second_session:
        first = SqlAlchemyWorkRequestRepository(first_session).mark_read(request_id, "yuna")
        first_at = first.read_at
        first_session.commit()
        second = SqlAlchemyWorkRequestRepository(second_session).mark_read(request_id, "yuna")
        # 같은 **순간**이다. SQLite 는 시간대를 저장하지 않아 다시 읽은 값이 naive 로 돌아오므로
        # 표면으로 나가는 글자는 application 이 UTC 로 맞춘다 — 아래 영수증이 그 사실을 본다.
        assert second.read_at.replace(tzinfo=None) == first_at.replace(tzinfo=None)
        second_session.commit()

    with make_session_factory(database_url)() as session:
        assert len(session.scalars(select(WorkRequestReadReceiptRecord)).all()) == 1
    receipt = application.mark_work_request_reference_read(principal, request_id)
    assert receipt["read_at"].endswith("+00:00")
    assert receipt["read_at"].startswith(first_at.replace(tzinfo=None).isoformat())


def test_my_read_never_moves_another_referrers_inbox(tmp_path) -> None:
    """**읽음 상태가 사용자별이다** — 참조자 A 가 읽어도 참조자 B 의 수신함은 그대로다."""
    client, _ = _stack(tmp_path)
    request = _sent(client, cc=["yuna", "minseok"], key="per-person-read")
    assert set(request["cc_member_ids"]) == {"yuna", "minseok"}
    other = {"X-Demo-Persona": "minseok"}

    assert _categories(client, YUNA)["reference"] == {request["request_id"]}
    assert _categories(client, other)["reference"] == {request["request_id"]}

    assert client.post(f"/api/work-requests/{request['request_id']}/read", headers=YUNA).status_code == 200

    assert _categories(client, YUNA)["reference"] == set()
    # 뱃지는 필터가 걸린 건수를 그대로 쓰므로, B 의 건수가 줄지 않는다는 것이 곧 뱃지가 그대로라는 뜻이다.
    assert _categories(client, other)["reference"] == {request["request_id"]}


# ---- 권한: 참조자만 -------------------------------------------------------------


def test_only_a_referrer_may_mark_it_read(tmp_path) -> None:
    """참조자가 아니면 **403**, 없거나 못 읽는 요청은 **404**(같은 말)다."""
    client, _ = _stack(tmp_path)
    request = _sent(client, cc=["yuna"], key="only-cc-reads")
    path = f"/api/work-requests/{request['request_id']}/read"

    # 요청자와 담당자에게는 이 명령이 없다 — 읽을 수는 있으므로 403 이다.
    assert client.post(path, headers=MINA).status_code == 403
    assert client.post(path, headers=JIHO).status_code == 403

    # 관계가 아예 없는 사람에게는 **존재도 알리지 않는다.**
    stranger = _sent(client, cc=[], key="stranger-cannot-see")
    assert client.post(f"/api/work-requests/{stranger['request_id']}/read", headers=YUNA).status_code == 404
    from uuid import uuid4

    assert client.post(f"/api/work-requests/{uuid4()}/read", headers=YUNA).status_code == 404


# ---- 업무 요청 갈래는 바뀌지 않는다 ---------------------------------------------


def test_the_work_branch_of_the_inbox_is_untouched_by_reading(tmp_path) -> None:
    """읽음이 **수락을 대신하지 않는다** — `work` 갈래에는 필터가 없고 수락·거절 흐름이 그대로다."""
    client, _ = _stack(tmp_path)
    request = _sent(client, cc=["yuna"], key="work-branch-intact")

    # 담당자에게는 `work` 로 선다. 참조자가 아니므로 읽음 명령 자체가 없다(403).
    assert _categories(client, JIHO)["work"] == {request["request_id"]}
    assert client.post(f"/api/work-requests/{request['request_id']}/read", headers=JIHO).status_code == 403
    assert _categories(client, JIHO)["work"] == {request["request_id"]}

    accepted = client.post(
        f"/api/work-requests/{request['request_id']}/accept",
        headers=JIHO,
        json={"expected_version": request["version"]},
    )
    assert accepted.status_code == 200, accepted.text
    # 수락하면 `work` 갈래에서 빠진다 — 읽음이 아니라 **답**이 그 갈래를 접는다.
    assert _categories(client, JIHO)["work"] == set()


def test_the_read_command_is_not_exposed_to_agents(tmp_path) -> None:
    """**읽음 명령은 MCP·AX 표면에 없다** — 사람이 본 사실을 에이전트가 대신 기록하지 않는다 (D-19)."""
    import asyncio
    from types import SimpleNamespace

    from ax_workspace.entrypoints.mcp import _create_bound_persona_server
    from ax_workspace.modules.organization_access.catalog import CAPABILITY_IDS

    reader = SimpleNamespace(
        principal=SimpleNamespace(display_name="inventory", capabilities=frozenset(CAPABILITY_IDS))
    )
    names = {tool.name for tool in asyncio.run(_create_bound_persona_server(reader).list_tools())}
    assert not [name for name in names if "read" in name and "request" in name]
    # 수신함 도구는 그대로 있고, 그 설명이 「처리된 것까지 포함해」를 더는 말하지 않는다.
    assert "work_request_inbox" in names
    from ax_workspace.modules.ax_execution.tool_catalog import TOOL_CATALOG

    description = TOOL_CATALOG["work_request_inbox"].description
    assert "including processed ones" not in description
    assert "has not marked read" in description


def test_the_agent_surface_reads_the_same_filtered_projection(tmp_path) -> None:
    """**MCP 가 같은 projection 을 쓴다** — 읽은 참고 항목이 그 표면에서도 나오지 않는다."""
    client, _ = _stack(tmp_path)
    request = _sent(client, cc=["yuna"], key="same-projection")
    from ax_workspace.entrypoints.mcp import McpReportsFacade

    facade = McpReportsFacade(client.app.state.workflow_application._settings, "yuna")
    assert [row["request_id"] for row in facade.work_request_inbox() if row["category"] == "reference"] == [
        request["request_id"]
    ]
    assert client.post(f"/api/work-requests/{request['request_id']}/read", headers=YUNA).status_code == 200
    assert [row for row in facade.work_request_inbox() if row["category"] == "reference"] == []
    # 읽은 것도 요청 목록 도구에서는 계속 읽힌다 — 필터가 한 표면에만 있다.
    assert request["request_id"] in {row["request_id"] for row in facade.list_work_requests()}
