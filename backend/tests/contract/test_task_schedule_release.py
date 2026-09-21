"""배정이 **업무를 따라가는** 계약 — D1 세 자리와 `schedule_release` (SPEC-004 §5 · WORK-004 Phase BE-2).

**무엇을 증명하는가.** 업무의 날짜가 바뀌는 **세 자리**(업무 수정 · 시작 전이 · 조건 변경 제안 동의)가
각각 배정을 검증해 기간 밖인 것을 닫고, **닫은 건수를 응답에 싣는다**. 닫힌 배정은 **되살아나지 않고**
그 날은 **다시 배정할 수 있다**. 그리고 **날짜와 무관한 변경은 아무것도 닫지 않는다.**

**무엇을 증명하지 않는가.** 업무가 끝나서 캘린더에서 빠지는 것은 **쓰기가 아니라 읽기 필터**라
`test_task_schedules.py` 가 갖는다. 상태 변경 아홉 경로에는 아무것도 더하지 않았다.
동시성·부분 unique 는 `tests/integration/postgres/test_task_schedules_postgres.py` 의 몫이다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.organization_access.domain import Principal, TASK_READ, TASK_SELF_MANAGE
from ax_workspace.modules.work.errors import TaskAccessDenied

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}

FIRST = date(2027, 3, 1)
LAST = date(2027, 3, 5)
#: 닫힘 사유 둘. **셋째가 생기는 경로가 없다** (DEC-003 §J).
OUT_OF_RANGE = "out_of_range"
DATES_CLEARED = "task_dates_cleared"
#: 닫은 것이 없다는 말. `None` 과 다르다 — 「말할 것이 없다」를 화면이 읽을 수 있어야 한다.
NOTHING = {"released_count": 0, "reason": None}


def _stack(tmp_path) -> tuple[TestClient, str]:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    return TestClient(create_app(settings)), database_url


def _version(client: TestClient, task_id: str, headers: dict[str, str] = MINA) -> int:
    return int(client.get(f"/api/tasks/{task_id}", headers=headers).json()["version"])


def _task(client: TestClient, headers: dict[str, str] = MINA, **body) -> str:
    created = client.post("/api/tasks", headers=headers, json={"title": "월~금짜리 업무", **body})
    assert created.status_code == 201, created.text
    return created.json()["task_id"]


def _spanned_task(client: TestClient, headers: dict[str, str] = MINA) -> str:
    return _task(client, headers, start_date=FIRST.isoformat(), due_date=LAST.isoformat())


def _slot(client: TestClient, task_id: str, on_date: date, headers: dict[str, str] = MINA, start="10:00", end="11:00"):
    created = client.post(
        f"/api/tasks/{task_id}/schedules",
        headers=headers,
        json={"on_date": on_date.isoformat(), "starts_at": start, "ends_at": end},
    )
    assert created.status_code == 201, created.text
    return created.json()


def _slots(
    client: TestClient,
    task_id: str,
    headers: dict[str, str] = MINA,
    window: tuple[date, date] = (date(2027, 2, 1), date(2027, 4, 30)),
) -> list[str]:
    """지금 살아 있는 배정의 날짜 — **합본 조회가 정본**이다. 닫힌 것은 실리지 않는다."""
    rows = client.get(
        "/api/calendar",
        headers=headers,
        params={"from": window[0].isoformat(), "to": window[1].isoformat()},
    ).json()
    row = next((row for row in rows if row["kind"] == "task" and row["task_id"] == task_id), None)
    return [entry["on_date"] for entry in row["schedules"]] if row else []


def _edit(client: TestClient, task_id: str, headers: dict[str, str] = MINA, **changes):
    return client.patch(
        f"/api/tasks/{task_id}",
        headers=headers,
        json={"expected_version": _version(client, task_id, headers), **changes},
    )


# ---- 자리 ① 업무 수정 (SPEC-004 §5 · `application.py` `update`) -----------------


def test_narrowing_the_span_closes_the_slots_that_fall_outside_and_says_how_many(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    task_id = _spanned_task(client)
    for day in (date(2027, 3, 1), date(2027, 3, 3), date(2027, 3, 5)):
        _slot(client, task_id, day)

    shrunk = _edit(client, task_id, due_date="2027-03-03")

    assert shrunk.status_code == 200, shrunk.text
    # **업무 수정은 성공한다** — 배정이 닫히는 것은 오류가 아니다.
    assert shrunk.json()["due_date"] == "2027-03-03"
    assert shrunk.json()["schedule_release"] == {"released_count": 1, "reason": OUT_OF_RANGE}
    assert _slots(client, task_id) == ["2027-03-01", "2027-03-03"]


def test_a_closed_slot_does_not_come_back_when_the_span_grows_again(tmp_path) -> None:
    """**닫힘은 끝이다** — 되살아나는 전이가 없다. 그 날은 **새로 넣어야** 선다."""
    client, _ = _stack(tmp_path)
    task_id = _spanned_task(client)
    _slot(client, task_id, date(2027, 3, 5))
    assert _edit(client, task_id, due_date="2027-03-03").json()["schedule_release"]["released_count"] == 1

    widened = _edit(client, task_id, due_date="2027-03-05")

    assert widened.status_code == 200, widened.text
    # 늘리는 것은 아무것도 닫지 않는다.
    assert widened.json()["schedule_release"] == NOTHING
    assert _slots(client, task_id) == []
    # **그 날에 다시 배정할 수 있다** — 닫힌 행은 부분 unique 에서 빠진다.
    again = _slot(client, task_id, date(2027, 3, 5), start="14:00", end="15:00")
    assert _slots(client, task_id) == ["2027-03-05"]
    assert again["version"] == 1


def test_clearing_both_dates_closes_everything_with_its_own_reason(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    task_id = _spanned_task(client)
    for day in (date(2027, 3, 1), date(2027, 3, 3)):
        _slot(client, task_id, day)

    cleared = _edit(client, task_id, clear_start_date=True, clear_due_date=True)

    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["schedule_release"] == {"released_count": 2, "reason": DATES_CLEARED}
    assert _slots(client, task_id) == []


def test_clearing_one_date_leaves_the_other_day_and_keeps_the_reason_at_two(tmp_path) -> None:
    """한쪽만 지우면 **남은 한쪽을 그 날 하루로 읽는다** (증보 K7) — 그래서 **셋째 사유가 생기지 않는다.**"""
    client, _ = _stack(tmp_path)
    task_id = _spanned_task(client)
    for day in (date(2027, 3, 1), date(2027, 3, 5)):
        _slot(client, task_id, day)

    half = _edit(client, task_id, clear_start_date=True)

    assert half.status_code == 200, half.text
    assert half.json()["schedule_release"] == {"released_count": 1, "reason": OUT_OF_RANGE}
    assert _slots(client, task_id) == ["2027-03-05"]


def test_a_change_that_does_not_touch_the_dates_closes_nothing(tmp_path) -> None:
    """**날짜가 실제로 바뀌었을 때만 돈다** — 공통 지점 `touch()` 에 걸지 않는 이유다 (§J)."""
    client, _ = _stack(tmp_path)
    task_id = _spanned_task(client)
    _slot(client, task_id, date(2027, 3, 3))

    renamed = _edit(client, task_id, title="이름만 바뀐 업무")

    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["schedule_release"] == NOTHING
    assert _slots(client, task_id) == ["2027-03-03"]


# ---- 자리 ② 시작 전이 (`application.py` `transition`) ---------------------------


def test_starting_a_task_carries_the_bundle_and_closes_nothing(tmp_path) -> None:
    """**닫을 것이 없음을 증명한다.**

    시작 전이는 **비어 있던 시작일을 오늘로 채우는 것**이고, 그 결과 기간은 `[min, max]` 로 읽혀
    **원래 구간을 포함하는 쪽으로만 넓어진다** (증보 K11). 좁아지는 길이 없으므로 이 자리에서 닫히는
    배정은 **구조적으로 나올 수 없다.** 그래도 묶음은 낸다 — K3 이 세 자리 전부에 건수를 요구한다.
    """
    client, _ = _stack(tmp_path)
    today = datetime.now(UTC).astimezone(ZoneInfo("Asia/Seoul")).date()
    overdue = today - timedelta(days=3)
    task_id = _task(client, title="마감이 지난 업무", due_date=overdue.isoformat())
    kept = client.post(
        f"/api/tasks/{task_id}/schedules",
        headers=MINA,
        json={"on_date": overdue.isoformat(), "starts_at": "10:00", "ends_at": "11:00"},
    )
    assert kept.status_code == 201, kept.text

    started = client.post(
        f"/api/tasks/{task_id}/start", headers=MINA, json={"expected_version": _version(client, task_id)}
    )

    assert started.status_code == 200, started.text
    body = started.json()
    assert body["schedule_release"] == NOTHING
    # 뒤집힌 기간이 실재하고, 그 안의 배정은 **그대로 산다**.
    assert body["start_date"] > body["due_date"]
    assert _slots(client, task_id, window=(overdue, today)) == [overdue.isoformat()]


def test_starting_a_task_that_already_had_a_start_date_changes_no_date_at_all(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    task_id = _spanned_task(client)
    _slot(client, task_id, date(2027, 3, 3))

    started = client.post(
        f"/api/tasks/{task_id}/start", headers=MINA, json={"expected_version": _version(client, task_id)}
    )

    assert started.status_code == 200, started.text
    assert started.json()["schedule_release"] == NOTHING
    assert started.json()["start_date"] == FIRST.isoformat()
    assert _slots(client, task_id) == ["2027-03-03"]


def test_the_transitions_that_do_not_move_a_date_keep_their_old_response_shape(tmp_path) -> None:
    """**막힘·완료·취소는 날짜를 바꾸지 않는다** — 그 표면의 계약은 그대로다 (증보 K3 의 세 자리)."""
    client, _ = _stack(tmp_path)
    task_id = _spanned_task(client)
    started = client.post(
        f"/api/tasks/{task_id}/start", headers=MINA, json={"expected_version": _version(client, task_id)}
    )
    assert started.status_code == 200, started.text
    blocked = client.post(
        f"/api/tasks/{task_id}/block",
        headers=MINA,
        json={"expected_version": _version(client, task_id), "reason": "자료를 기다린다"},
    )
    assert blocked.status_code == 200, blocked.text

    assert "schedule_release" not in blocked.json()
    cancelled = client.post(
        f"/api/tasks/{task_id}/cancel",
        headers=MINA,
        json={"expected_version": _version(client, task_id), "reason": "필요 없어졌다"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert "schedule_release" not in cancelled.json()


# ---- 자리 ③ 조건 변경 제안 동의 (`application.py` `_apply_proposal`) ------------


def _request_task(client: TestClient) -> str:
    """민아가 지호에게 보낸 요청을 지호가 수락한다 — **담당은 지호**이고 제안은 민아가 낸다."""
    sent = client.post(
        "/api/work-requests",
        headers=MINA,
        json={
            "title": "기간이 있는 요청",
            "assignee_id": "jiho",
            "start_date": FIRST.isoformat(),
            "due_date": LAST.isoformat(),
        },
    )
    assert sent.status_code == 201, sent.text
    request_id, task_id = sent.json()["request_id"], sent.json()["task_id"]
    accepted = client.post(
        f"/api/work-requests/{request_id}/accept",
        headers=JIHO,
        json={"expected_version": client.get(f"/api/work-requests/{request_id}", headers=JIHO).json()["version"]},
    )
    assert accepted.status_code == 200, accepted.text
    return task_id


def test_agreeing_to_a_new_due_date_closes_what_falls_outside_and_reports_it(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    task_id = _request_task(client)
    for day in (date(2027, 3, 1), date(2027, 3, 5)):
        _slot(client, task_id, day, JIHO)

    proposed = client.post(
        f"/api/tasks/{task_id}/proposals",
        headers=MINA,
        json={
            "kind": "terms_change",
            "expected_version": _version(client, task_id, MINA),
            "reason": "출시가 앞당겨졌습니다",
            "payload": {"due_date": "2027-03-02"},
        },
    )
    assert proposed.status_code == 201, proposed.text

    agreed = client.post(
        f"/api/tasks/{task_id}/proposals/{proposed.json()['proposal']['proposal_id']}/respond",
        headers=JIHO,
        json={"expected_version": _version(client, task_id, JIHO), "agree": True},
    )

    assert agreed.status_code == 200, agreed.text
    assert agreed.json()["schedule_release"] == {"released_count": 1, "reason": OUT_OF_RANGE}
    assert _slots(client, task_id, JIHO) == ["2027-03-01"]


def test_declining_a_proposal_closes_nothing_but_still_answers_with_the_bundle(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    task_id = _request_task(client)
    _slot(client, task_id, date(2027, 3, 5), JIHO)
    proposed = client.post(
        f"/api/tasks/{task_id}/proposals",
        headers=MINA,
        json={
            "kind": "terms_change",
            "expected_version": _version(client, task_id, MINA),
            "reason": "당길 수 있을까요",
            "payload": {"due_date": "2027-03-02"},
        },
    )
    assert proposed.status_code == 201, proposed.text

    declined = client.post(
        f"/api/tasks/{task_id}/proposals/{proposed.json()['proposal']['proposal_id']}/respond",
        headers=JIHO,
        json={"expected_version": _version(client, task_id, JIHO), "agree": False, "reason": "무리입니다"},
    )

    assert declined.status_code == 200, declined.text
    assert declined.json()["schedule_release"] == NOTHING
    assert _slots(client, task_id, JIHO) == ["2027-03-05"]


def test_a_proposal_that_only_changes_the_title_closes_nothing(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    task_id = _request_task(client)
    _slot(client, task_id, date(2027, 3, 5), JIHO)
    proposed = client.post(
        f"/api/tasks/{task_id}/proposals",
        headers=MINA,
        json={
            "kind": "terms_change",
            "expected_version": _version(client, task_id, MINA),
            "reason": "이름만 바꿉시다",
            "payload": {"title": "이름이 바뀐 요청"},
        },
    )
    assert proposed.status_code == 201, proposed.text

    agreed = client.post(
        f"/api/tasks/{task_id}/proposals/{proposed.json()['proposal']['proposal_id']}/respond",
        headers=JIHO,
        json={"expected_version": _version(client, task_id, JIHO), "agree": True},
    )

    assert agreed.status_code == 200, agreed.text
    assert agreed.json()["schedule_release"] == NOTHING
    assert _slots(client, task_id, JIHO) == ["2027-03-05"]


# ---- K16 — 역량 문과 관계 검사는 **겹겹**이다 (DEC-003 증보 6) -------------------


def test_the_two_capability_gates_do_not_change_what_a_member_or_a_lead_can_do(tmp_path) -> None:
    """**기존 동작이 바뀌지 않는다** — 구성원도 팀장도 두 역량을 갖고 있어 그대로 지난다."""
    client, _ = _stack(tmp_path)
    for headers in (MINA, JIHO):
        task_id = _spanned_task(client, headers)
        created = client.post(
            f"/api/tasks/{task_id}/schedules",
            headers=headers,
            json={"on_date": "2027-03-03", "starts_at": "10:00", "ends_at": "11:00"},
        )
        assert created.status_code == 201, (headers, created.text)
        calendar = client.get(
            "/api/calendar", headers=headers, params={"from": "2027-03-01", "to": "2027-03-05"}
        )
        assert calendar.status_code == 200, (headers, calendar.text)
        assert any(row["kind"] == "task" and row["task_id"] == task_id for row in calendar.json())


def test_the_new_surfaces_stand_behind_the_same_capability_doors_as_their_neighbours(tmp_path) -> None:
    """읽기는 `task.read`, 쓰기는 `task.self_manage` 를 지난다 (증보 K16).

    K5 의 「판정은 담당 관계이고 봉투가 아니다」는 **봉투로 판정하지 말라**는 뜻이지 **봉투를 걷으라**는
    뜻이 아니다. 지금은 못 지나는 역할이 없지만, **역량 없는 역할이 생기는 순간 새 표면만 새면 안 된다.**
    """
    client, _ = _stack(tmp_path)
    task_id = _spanned_task(client)
    application = client.app.state.workflow_application
    full = application.authenticated_principal("mina")
    assert {TASK_READ, TASK_SELF_MANAGE} <= full.capabilities

    def without(capability: str) -> Principal:
        return Principal(
            full.id, full.display_name, full.organization_scope, frozenset(full.capabilities - {capability})
        )

    with pytest.raises(TaskAccessDenied):
        application.calendar(without(TASK_READ), FIRST, LAST)
    with pytest.raises(TaskAccessDenied):
        application.create_task_schedule(
            without(TASK_SELF_MANAGE),
            UUID(task_id),
            idempotency_key="no-envelope",
            on_date=date(2027, 3, 3),
            starts_at=time(10, 0),
            ends_at=time(11, 0),
        )
