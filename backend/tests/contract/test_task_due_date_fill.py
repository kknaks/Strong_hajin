"""완료가 비어 있는 마감일을 채운다 (SPEC-003 §4 「날짜 채움」 · SPEC-004 §5 넷째 자리 · WORK-009 1-3).

`completed_at` 이 찍히는 두 전이 — 본인·배정 업무의 최종 완료와 요청 Task 의 완료 보고 제출 — 가 비어 있던
`due_date` 를 그 날(서울)로 채운다. 값이 있으면 건드리지 않고, 보완 요청·재개가 `completed_at` 을 지워도
채운 마감일은 되돌리지 않는다. 시작이 시작 예정일을 채우는 것과 같은 수준으로 이력·배정 검증이 돈다.
"""
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.work import application as work_application
from ax_workspace.modules.work import task_projection
from ax_workspace.platform.persistence import TaskScheduleRecord, make_session_factory

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}

#: 서울로는 이미 다음 날이다 — UTC 날짜를 쓰면 하루가 어긋난다.
_INSTANT = datetime(2026, 9, 9, 16, 30, tzinfo=UTC)
_SEOUL_DAY = "2026-09-10"


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _INSTANT if tz is None else _INSTANT.astimezone(tz)


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(task_projection, "datetime", _FrozenDateTime)
    monkeypatch.setattr(work_application, "datetime", _FrozenDateTime)
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    client = TestClient(create_app(settings))
    client.database_url = database_url
    return client


def _create(client, **fields) -> dict:
    created = client.post("/api/tasks", headers={**MINA, "Idempotency-Key": f"due-fill-{abs(hash(fields['title']))}"}, json=fields)
    assert created.status_code == 201, created.text
    return created.json()


def _requested_task(client, title: str, **fields) -> str:
    request = client.post(
        "/api/work-requests",
        headers={**MINA, "Idempotency-Key": f"due-fill-request-{abs(hash(title))}"},
        json={"title": title, "assignee_id": "jiho", **fields},
    ).json()
    accepted = client.post(
        f"/api/work-requests/{request['request_id']}/accept",
        headers=JIHO, json={"expected_version": request["version"]},
    )
    assert accepted.status_code in {200, 409}, accepted.text
    [task] = [row for row in client.get("/api/my-work", headers=JIHO).json() if row["title"] == title]
    current = client.get(f"/api/tasks/{task['task_id']}", headers=JIHO).json()
    started = client.post(f"/api/tasks/{task['task_id']}/start", headers=JIHO, json={"expected_version": current["version"]})
    assert started.status_code == 200, started.text
    return task["task_id"]


def _report(client, task_id: str):
    current = client.get(f"/api/tasks/{task_id}", headers=JIHO).json()
    return client.post(
        f"/api/tasks/{task_id}/completion-report",
        headers=JIHO, json={"expected_version": current["version"], "summary": "정리했습니다"},
    )


def test_completing_work_without_a_due_date_fills_it_with_the_seoul_day(client) -> None:
    task = _create(client, title="마감일 없는 업무")
    assert task["due_date"] is None
    done = client.post(f"/api/tasks/{task['task_id']}/complete", headers=MINA, json={"expected_version": task["version"]})
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["state"] == "done"
    assert body["due_date"] == _SEOUL_DAY
    assert body["completed_at"].startswith("2026-09-09T16:30")
    # 시작하지 않고 끝낸 일 — 시작 예정일·실제 시작일은 비어 있는 채로 둔다(채움은 마감일만).
    assert body["start_date"] is None and body["started_at"] is None
    assert body["schedule_release"]["released_count"] == 0
    history = client.get(f"/api/tasks/{task['task_id']}/history", headers=MINA).json()
    assert history["versions"][-1]["snapshot"]["due_date"] == _SEOUL_DAY
    assert history["activity"][0]["event_kind"] == "task.state_changed"
    diff = client.get(
        f"/api/tasks/{task['task_id']}/history/diff", headers=MINA,
        params={"from": history["versions"][-2]["version"], "to": history["versions"][-1]["version"]},
    )
    assert diff.status_code == 200, diff.text
    assert diff.json()["changes"]["due_date"] == {"before": None, "after": _SEOUL_DAY}


def test_completing_after_starting_fills_the_due_date_even_before_the_start_date(client) -> None:
    """채움은 「시작 ≤ 마감」 검사를 지나지 않는다 — 시작 예정일 채움과 같다."""
    task = _create(client, title="미래 시작 업무", start_date="2026-09-20")
    started = client.post(f"/api/tasks/{task['task_id']}/start", headers=MINA, json={"expected_version": task["version"]})
    assert started.status_code == 200, started.text
    done = client.post(
        f"/api/tasks/{task['task_id']}/complete", headers=MINA, json={"expected_version": started.json()["version"]}
    )
    assert done.status_code == 200, done.text
    assert done.json()["start_date"] == "2026-09-20" and done.json()["due_date"] == _SEOUL_DAY


def test_an_existing_due_date_is_left_alone(client) -> None:
    task = _create(client, title="마감일 있는 업무", due_date="2026-09-30")
    done = client.post(f"/api/tasks/{task['task_id']}/complete", headers=MINA, json={"expected_version": task["version"]})
    assert done.status_code == 200, done.text
    assert done.json()["due_date"] == "2026-09-30"


def test_a_completion_report_fills_the_due_date_and_asking_for_more_keeps_it(client) -> None:
    task_id = _requested_task(client, "요청 업무 — 마감일 없음")
    reported = _report(client, task_id)
    assert reported.status_code == 200, reported.text
    assert reported.json()["due_date"] == _SEOUL_DAY
    assert reported.json()["schedule_release"]["released_count"] == 0

    item = next(
        row for row in client.get("/api/action-items", headers=MINA).json()
        if row["kind"] == "task.delivery" and row["resource"]["id"] == task_id
    )
    asked = client.post(
        f"/api/action-items/{item['action_item_id']}/commands/request_changes",
        headers=MINA, json={"expected_version": item["expected_version"], "reason": "수치가 빠졌습니다"},
    )
    assert asked.status_code == 200, asked.text
    view = client.get(f"/api/tasks/{task_id}", headers=JIHO).json()
    # 보완 요청은 실제 종료일을 지우지만 채운 마감일은 되돌리지 않는다.
    assert view["completed_at"] is None
    assert view["due_date"] == _SEOUL_DAY


def test_a_completion_report_keeps_a_due_date_the_request_carried(client) -> None:
    task_id = _requested_task(client, "요청 업무 — 마감일 있음", due_date="2026-09-25")
    reported = _report(client, task_id)
    assert reported.status_code == 200, reported.text
    assert reported.json()["due_date"] == "2026-09-25"


def test_reopening_keeps_the_filled_due_date(client) -> None:
    task = _create(client, title="재개할 업무")
    done = client.post(
        f"/api/tasks/{task['task_id']}/complete", headers=MINA, json={"expected_version": task["version"]}
    ).json()
    reopened = client.post(
        f"/api/tasks/{task['task_id']}/reopen", headers=MINA, json={"expected_version": done["version"]}
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["completed_at"] is None
    assert reopened.json()["due_date"] == _SEOUL_DAY


# ---- 넷째 자리의 배정 검증 — 살아 있는 배정을 두고 「닫을 것이 없음」을 증명한다 (SPEC-004 §5·§6 · OQ-405) ----

_START = "2026-09-08"  # 시작 예정일만 있는 업무 — 기간은 그날 하루, 채움 뒤 [09-08, 09-10] 으로 넓어진다


def _hook_calls(monkeypatch) -> list[str]:
    """배정 검증 훅이 **실제로 불렸나** — 0 건이 「안 돌아서 0」이 아니라 「돌았고 닫을 것이 없어 0」임을 본다."""
    calls: list[str] = []
    original = work_application.TaskApplication._release_schedules_outside

    def spy(self, task):
        calls.append(str(task.id))
        return original(self, task)

    monkeypatch.setattr(work_application.TaskApplication, "_release_schedules_outside", spy)
    return calls


def _live_slots(client, task_id: str) -> list[str]:
    with make_session_factory(client.database_url)() as session:
        rows = session.query(TaskScheduleRecord).filter(
            TaskScheduleRecord.task_id == UUID(task_id), TaskScheduleRecord.released_at.is_(None)
        ).all()
        return [row.on_date.isoformat() for row in rows]


def _slot(client, task_id: str, headers) -> None:
    created = client.post(
        f"/api/tasks/{task_id}/schedules",
        headers=headers, json={"on_date": _START, "starts_at": "10:00", "ends_at": "11:00"},
    )
    assert created.status_code == 201, created.text


def test_completing_fills_the_due_date_through_the_schedule_hook_and_keeps_live_slots(client, monkeypatch) -> None:
    task = _create(client, title="배정이 있는 업무", start_date=_START)
    _slot(client, task["task_id"], MINA)
    calls = _hook_calls(monkeypatch)
    current = client.get(f"/api/tasks/{task['task_id']}", headers=MINA).json()

    done = client.post(f"/api/tasks/{task['task_id']}/complete", headers=MINA, json={"expected_version": current["version"]})

    assert done.status_code == 200, done.text
    assert done.json()["due_date"] == _SEOUL_DAY
    assert done.json()["schedule_release"] == {"released_count": 0, "reason": None}
    assert calls == [task["task_id"]]  # 훅이 돌았다 — 날짜가 바뀐 자리다
    assert _live_slots(client, task["task_id"]) == [_START]


def test_a_completion_report_fills_through_the_hook_and_keeps_live_slots(client, monkeypatch) -> None:
    task_id = _requested_task(client, "배정이 있는 요청 업무", start_date=_START)
    _slot(client, task_id, JIHO)
    calls = _hook_calls(monkeypatch)

    reported = _report(client, task_id)

    assert reported.status_code == 200, reported.text
    assert reported.json()["due_date"] == _SEOUL_DAY
    assert reported.json()["schedule_release"] == {"released_count": 0, "reason": None}
    assert calls == [task_id]
    assert _live_slots(client, task_id) == [_START]


def test_completing_with_a_due_date_already_set_does_not_run_the_hook(client, monkeypatch) -> None:
    """날짜가 안 바뀌면 훅을 부르지 않는다 — 시작일 채움과 같은 규칙(`_schedule_release_for`)."""
    task = _create(client, title="마감일 있는 배정 업무", start_date=_START, due_date="2026-09-12")
    _slot(client, task["task_id"], MINA)
    calls = _hook_calls(monkeypatch)
    current = client.get(f"/api/tasks/{task['task_id']}", headers=MINA).json()
    done = client.post(f"/api/tasks/{task['task_id']}/complete", headers=MINA, json={"expected_version": current["version"]})
    assert done.status_code == 200, done.text
    assert done.json()["schedule_release"] == {"released_count": 0, "reason": None}
    assert calls == []
    assert _live_slots(client, task["task_id"]) == [_START]
