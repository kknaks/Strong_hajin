"""시간 배정의 계약 — 생성 · 시각 변경 · 합본 조회 (SPEC-004 §4 · WORK-004 Phase BE-1).

**무엇을 증명하는가.** 배정이라는 것이 **존재한다**까지다: 그 업무의 활성 담당자가 기간 안의 하루에
시간 한 칸을 세우고, 같은 날 두 번째 생성이 거절되며, 같은 멱등 키의 재전송이 **영수증**으로 돌아오고,
시각 변경이 **그 배정 자신의 회차**로 잠기며, 합본 조회가 업무와 회의를 한 배열로 낸다.

**무엇을 증명하지 않는가.** 여기는 SQLite 계약 테스트다 — **하루 한 칸을 데이터베이스가 지킨다**는
것은 `tests/integration/postgres/test_task_schedules_postgres.py` 가 살아 있는 PostgreSQL 에 묻는다.
이 파일의 `409` 는 application 이 사람에게 이유를 말하는 자리일 뿐 부분 unique 를 증명하지 않는다.
기간이 바뀔 때 배정을 닫는 **세 자리**(D1)와 `schedule_release` 는 **Phase BE-2** 의 몫이라 여기 없다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.meetings.application import MeetingApplication
from ax_workspace.platform.meetings import SqlAlchemyMeetingRepository
from ax_workspace.platform.persistence import make_session_factory

MINA = {"X-Demo-Persona": "mina"}        # 제품팀원 — 업무의 활성 담당자
YUNA = {"X-Demo-Persona": "yuna"}        # 대표 — 조직 전체 범위라 민아의 업무를 **읽을 수 있다**
MINSEOK = {"X-Demo-Persona": "minseok"}  # 재무팀원 — 읽을 수 없다

#: 자동 취소 판정이 닿지 않는 먼 미래. 이 파일은 시각 축의 **계약**을 보지 회의 상태를 보지 않는다.
FIRST = date(2027, 3, 1)
LAST = date(2027, 3, 5)
OFFICE = ZoneInfo("Asia/Seoul")


def _stack(tmp_path) -> tuple[TestClient, str]:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    return TestClient(create_app(settings)), database_url


def _task(client: TestClient, title: str = "월~금짜리 업무", headers: dict[str, str] = MINA, **body) -> str:
    created = client.post("/api/tasks", headers=headers, json={"title": title, **body})
    assert created.status_code == 201, created.text
    return created.json()["task_id"]


def _spanned_task(client: TestClient, headers: dict[str, str] = MINA) -> str:
    return _task(client, headers=headers, start_date=FIRST.isoformat(), due_date=LAST.isoformat())


def _schedule(client: TestClient, task_id: str, on_date: date, start: str, end: str, **kwargs):
    return client.post(
        f"/api/tasks/{task_id}/schedules",
        headers=kwargs.pop("headers", MINA),
        json={"on_date": on_date.isoformat(), "starts_at": start, "ends_at": end},
        **kwargs,
    )


def _meeting(client: TestClient, title: str, day: date, headers: dict[str, str] = MINA, **body):
    created = client.post(
        "/api/meetings",
        headers=headers,
        json={
            "title": title,
            "starts_at": datetime.combine(day, datetime.min.time(), tzinfo=OFFICE).replace(hour=10).isoformat(),
            "ends_at": datetime.combine(day, datetime.min.time(), tzinfo=OFFICE).replace(hour=11).isoformat(),
            **body,
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["meeting"]


# ---- 생성: 생성 전용이고, 영수증이 409 보다 먼저다 (증보 K10·K12) ----------------


def test_a_day_inside_the_span_gets_one_slot_and_the_body_names_its_own_version(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    task_id = _spanned_task(client)

    created = _schedule(client, task_id, date(2027, 3, 3), "10:00", "11:30")

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["task_id"] == task_id
    assert (body["on_date"], body["starts_at"], body["ends_at"]) == ("2027-03-03", "10:00", "11:30")
    assert body["version"] == 1
    # **제목·담당자를 싣지 않는다** — 복사하면 업무 제목이 바뀔 때 캘린더가 조용히 낡는다.
    # **닫힘도 드러내지 않는다** — 닫힌 배정은 조회에 실릴 자리가 없다.
    assert set(body) == {"schedule_id", "task_id", "on_date", "starts_at", "ends_at", "version"}


def test_a_second_create_on_a_day_that_is_already_taken_is_refused_not_overwritten(tmp_path) -> None:
    """**생성은 덮어쓰지 않는다** (증보 K10). 그 날의 시각을 바꾸는 것은 `PATCH` 의 일이다."""
    client, _ = _stack(tmp_path)
    task_id = _spanned_task(client)
    first = _schedule(client, task_id, date(2027, 3, 3), "10:00", "11:00").json()

    second = _schedule(client, task_id, date(2027, 3, 3), "14:00", "15:00")

    assert second.status_code == 409, second.text
    still = client.get("/api/calendar", headers=MINA, params={"from": "2027-03-01", "to": "2027-03-05"}).json()
    rows = [row for row in still if row["kind"] == "task" and row["task_id"] == task_id]
    assert [entry["schedule_id"] for entry in rows[0]["schedules"]] == [first["schedule_id"]]
    assert rows[0]["schedules"][0]["starts_at"] == "10:00"


def test_the_same_idempotency_key_comes_back_as_a_receipt_before_any_conflict(tmp_path) -> None:
    """**영수증이 409 보다 먼저다** (증보 K12) — 드래그 연타가 자기 자신 때문에 거절되지 않는다."""
    client, _ = _stack(tmp_path)
    task_id = _spanned_task(client)
    key = {"Idempotency-Key": "drag-once"}

    created = _schedule(client, task_id, date(2027, 3, 3), "10:00", "11:00", headers={**MINA, **key})
    again = _schedule(client, task_id, date(2027, 3, 3), "10:00", "11:00", headers={**MINA, **key})

    assert created.status_code == 201, created.text
    # 새로 만들어진 것이 없으므로 `200` 이다. 회차도 올라가지 않는다 — 두 번째 effect 가 없다.
    assert again.status_code == 200, again.text
    assert again.json() == created.json()


def test_the_same_key_with_a_different_body_is_a_conflict_not_a_receipt(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    task_id = _spanned_task(client)
    key = {"Idempotency-Key": "drag-once"}
    assert _schedule(client, task_id, date(2027, 3, 3), "10:00", "11:00", headers={**MINA, **key}).status_code == 201

    other = _schedule(client, task_id, date(2027, 3, 4), "10:00", "11:00", headers={**MINA, **key})

    assert other.status_code == 409, other.text


@pytest.mark.no_auto_idempotency_key
def test_creating_a_slot_without_an_idempotency_key_is_refused(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    task_id = _task(client, start_date=FIRST.isoformat(), due_date=LAST.isoformat(),
                    headers={**MINA, "Idempotency-Key": "seed-task"})

    refused = _schedule(client, task_id, date(2027, 3, 3), "10:00", "11:00")

    assert refused.status_code == 422, refused.text


# ---- 거절: 기간 · 시각 · 상태 (SPEC-004 §4 Case Matrix) ------------------------


def test_a_day_outside_the_span_is_refused_and_the_message_carries_that_span(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    task_id = _spanned_task(client)

    refused = _schedule(client, task_id, date(2027, 3, 9), "10:00", "11:00")

    assert refused.status_code == 422, refused.text
    assert "2027-03-01" in refused.text and "2027-03-05" in refused.text


def test_a_task_with_only_a_due_date_accepts_that_single_day_and_nothing_else(tmp_path) -> None:
    """한쪽만 있으면 **그 날 하루**다 (증보 K7) — 기간이 없는 것과 다르다."""
    client, _ = _stack(tmp_path)
    task_id = _task(client, due_date=LAST.isoformat())

    assert _schedule(client, task_id, LAST, "10:00", "11:00").status_code == 201
    assert _schedule(client, task_id, LAST - timedelta(days=1), "10:00", "11:00").status_code == 422


def test_a_task_with_no_dates_at_all_cannot_hold_a_slot(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    task_id = _task(client)

    refused = _schedule(client, task_id, date(2027, 3, 3), "10:00", "11:00")

    assert refused.status_code == 422, refused.text
    assert "기간" in refused.text


def test_a_reversed_span_is_read_as_min_to_max_rather_than_as_an_empty_one(tmp_path) -> None:
    """뒤집힌 기간은 **실재한다** (증보 K11).

    시작 전이가 비어 있던 시작일을 **오늘**로 채우는데 그 자리는 `validate_schedule` 을 지나지 않는다.
    마감일이 지난 업무를 그때 시작하면 `start_date > due_date` 가 선다 — 그 업무에도 배정이 서야 한다.
    """
    client, _ = _stack(tmp_path)
    today = datetime.now(UTC).astimezone(ZoneInfo("Asia/Seoul")).date()
    overdue = today - timedelta(days=3)
    task_id = _task(client, title="마감이 지난 업무", due_date=overdue.isoformat())
    started = client.post(
        f"/api/tasks/{task_id}/start",
        headers=MINA,
        json={"expected_version": client.get(f"/api/tasks/{task_id}", headers=MINA).json()["version"]},
    )
    assert started.status_code == 200, started.text
    detail = client.get(f"/api/tasks/{task_id}", headers=MINA).json()
    assert detail["start_date"] > detail["due_date"], detail

    inside = _schedule(client, task_id, today - timedelta(days=1), "10:00", "11:00")

    assert inside.status_code == 201, inside.text
    row = next(
        row
        for row in client.get(
            "/api/calendar",
            headers=MINA,
            params={"from": overdue.isoformat(), "to": today.isoformat()},
        ).json()
        if row["kind"] == "task" and row["task_id"] == task_id
    )
    # **화면은 이 값을 다시 계산하지 않는다** (증보 K14) — 서버가 정규화한 구간 그대로다.
    assert (row["span_from"], row["span_to"]) == (overdue.isoformat(), today.isoformat())


def test_an_end_that_is_not_after_the_start_is_refused(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    task_id = _spanned_task(client)

    assert _schedule(client, task_id, date(2027, 3, 3), "11:00", "11:00").status_code == 422
    assert _schedule(client, task_id, date(2027, 3, 3), "12:00", "11:00").status_code == 422


def test_a_finished_task_takes_no_new_slot(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    task_id = _spanned_task(client)
    cancelled = client.post(
        f"/api/tasks/{task_id}/cancel",
        headers=MINA,
        json={
            "expected_version": client.get(f"/api/tasks/{task_id}", headers=MINA).json()["version"],
            "reason": "더 필요하지 않다",
        },
    )
    assert cancelled.status_code == 200, cancelled.text

    refused = _schedule(client, task_id, date(2027, 3, 3), "10:00", "11:00")

    assert refused.status_code == 409, refused.text


# ---- 권한: 담당 관계가 정본이고, 읽기 여부가 403 과 404 를 가른다 (증보 K5·K6) ----


def test_someone_who_can_read_the_work_but_does_not_hold_it_is_told_so(tmp_path) -> None:
    """**읽을 수 있다는 건 존재를 이미 아는 것**이다 — 404 로 숨기면 화면이 거짓말을 한다 (증보 K6)."""
    client, _ = _stack(tmp_path)
    task_id = _spanned_task(client)
    assert client.get(f"/api/tasks/{task_id}", headers=YUNA).status_code == 200

    refused = _schedule(client, task_id, date(2027, 3, 3), "10:00", "11:00", headers=YUNA)

    assert refused.status_code == 403, refused.text


def test_someone_who_cannot_read_the_work_is_answered_as_if_it_were_not_there(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    task_id = _spanned_task(client)
    assert client.get(f"/api/tasks/{task_id}", headers=MINSEOK).status_code == 404

    refused = _schedule(client, task_id, date(2027, 3, 3), "10:00", "11:00", headers=MINSEOK)

    assert refused.status_code == 404, refused.text


# ---- 시각 변경: 회차의 주인은 그 배정이다 (증보 K8·K10) ------------------------


def test_retiming_keeps_the_same_row_and_raises_only_the_schedule_version(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    task_id = _spanned_task(client)
    created = _schedule(client, task_id, date(2027, 3, 3), "10:00", "11:00").json()
    task_version = client.get(f"/api/tasks/{task_id}", headers=MINA).json()["version"]

    moved = client.patch(
        f"/api/task-schedules/{created['schedule_id']}",
        headers=MINA,
        json={"expected_version": created["version"], "starts_at": "14:00", "ends_at": "15:30"},
    )

    assert moved.status_code == 200, moved.text
    assert moved.json()["schedule_id"] == created["schedule_id"]
    assert (moved.json()["starts_at"], moved.json()["ends_at"]) == ("14:00", "15:30")
    assert moved.json()["version"] == created["version"] + 1
    # **업무 회차는 그대로다** — 배정만 바뀌는데 업무 회차를 올리면 다른 화면의 잠금이 깨진다.
    assert client.get(f"/api/tasks/{task_id}", headers=MINA).json()["version"] == task_version


def test_a_stale_schedule_version_is_refused(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    task_id = _spanned_task(client)
    created = _schedule(client, task_id, date(2027, 3, 3), "10:00", "11:00").json()
    path = f"/api/task-schedules/{created['schedule_id']}"
    assert client.patch(
        path, headers=MINA, json={"expected_version": 1, "starts_at": "14:00", "ends_at": "15:00"}
    ).status_code == 200

    stale = client.patch(
        path, headers=MINA, json={"expected_version": 1, "starts_at": "16:00", "ends_at": "17:00"}
    )

    assert stale.status_code == 409, stale.text


def test_retiming_needs_a_version_and_refuses_a_date_move(tmp_path) -> None:
    """**회차는 무조건 필수**이고 **`on_date` 는 받지 않는다** — 날짜 이동 경로가 없다 (§2.3 R6)."""
    client, _ = _stack(tmp_path)
    task_id = _spanned_task(client)
    created = _schedule(client, task_id, date(2027, 3, 3), "10:00", "11:00").json()
    path = f"/api/task-schedules/{created['schedule_id']}"

    assert client.patch(path, headers=MINA, json={"starts_at": "14:00", "ends_at": "15:00"}).status_code == 422
    assert client.patch(
        path,
        headers=MINA,
        json={"expected_version": 1, "on_date": "2027-03-04", "starts_at": "14:00", "ends_at": "15:00"},
    ).status_code == 422


def test_an_unknown_schedule_is_answered_as_one_that_is_not_there(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    _spanned_task(client)

    missing = client.patch(
        "/api/task-schedules/00000000-0000-4000-8000-000000000000",
        headers=MINA,
        json={"expected_version": 1, "starts_at": "14:00", "ends_at": "15:00"},
    )

    assert missing.status_code == 404, missing.text


# ---- 합본 조회: 한 배열, `kind` 로 가른다 (증보 K2·K13·K14) --------------------


def test_the_calendar_answers_with_one_array_split_by_kind(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    task_id = _spanned_task(client)
    _schedule(client, task_id, date(2027, 3, 3), "10:00", "11:00")
    meeting = _meeting(client, "제품 주간 회의", date(2027, 3, 4))

    rows = client.get(
        "/api/calendar", headers=MINA, params={"from": "2027-03-01", "to": "2027-03-05"}
    ).json()

    task_row = next(row for row in rows if row["kind"] == "task" and row["task_id"] == task_id)
    assert (task_row["span_from"], task_row["span_to"]) == ("2027-03-01", "2027-03-05")
    assert task_row["state"] == "open"
    assert [entry["on_date"] for entry in task_row["schedules"]] == ["2027-03-03"]
    # **원소에 `task_id` 를 다시 싣지 않는다** — 매달린 업무 행이 이미 갖는다.
    assert set(task_row["schedules"][0]) == {"schedule_id", "on_date", "starts_at", "ends_at", "version"}
    # **「내가 담당인가」를 싣지 않는다** (증보 K13) — 축이 `my_work` 라 언제나 참이다.
    assert "is_active_assignee" not in task_row

    meeting_row = next(row for row in rows if row["kind"] == "meeting")
    assert meeting_row["meeting_id"] == meeting["meeting_id"]
    # **주최자 이름은 합본 조회가 조인해서 낸다** (증보 K4) — member id 를 그리지 않는다.
    assert meeting_row["created_by_display_name"] and meeting_row["created_by_display_name"] != meeting_row["created_by"]
    assert "attendees" not in meeting_row and "purpose" not in meeting_row


def test_a_task_with_no_dates_still_stands_in_the_calendar_with_an_empty_span(tmp_path) -> None:
    """좌측 레일이 기간 없는 업무를 들어야 **날짜부터** 정할 수 있다 (R2)."""
    client, _ = _stack(tmp_path)
    task_id = _task(client, title="기한 없는 업무")

    rows = client.get(
        "/api/calendar", headers=MINA, params={"from": "2027-03-01", "to": "2027-03-05"}
    ).json()

    row = next(row for row in rows if row["kind"] == "task" and row["task_id"] == task_id)
    assert (row["span_from"], row["span_to"]) == (None, None)
    assert row["schedules"] == []


def test_a_finished_task_leaves_the_calendar_while_a_submitted_one_stays(tmp_path) -> None:
    """끝난 업무는 **조회 필터**로 빠지고 `COMPLETION_SUBMITTED` 는 **남는다** (§B·§C)."""
    client, _ = _stack(tmp_path)
    done_id = _spanned_task(client)
    kept_id = _spanned_task(client)
    # **두 업무가 같은 날의 다른 시간을 든다** — 같은 시간을 주면 겹침으로 거절되어(증보 K22)
    # 뒤 업무가 배정 없이 서고, 이 테스트가 재는 것이 조용히 사라진다.
    for task_id, start, end in ((done_id, "10:00", "11:00"), (kept_id, "11:00", "12:00")):
        assert _schedule(client, task_id, date(2027, 3, 3), start, end).status_code == 201
    finished = client.post(
        f"/api/tasks/{done_id}/complete",
        headers=MINA,
        json={"expected_version": client.get(f"/api/tasks/{done_id}", headers=MINA).json()["version"]},
    )
    assert finished.status_code == 200, finished.text

    rows = client.get(
        "/api/calendar", headers=MINA, params={"from": "2027-03-01", "to": "2027-03-05"}
    ).json()

    standing = {row["task_id"] for row in rows if row["kind"] == "task"}
    assert done_id not in standing and kept_id in standing


def test_slots_outside_the_asked_range_are_not_carried_in_the_row(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    task_id = _spanned_task(client)
    _schedule(client, task_id, date(2027, 3, 1), "10:00", "11:00")
    _schedule(client, task_id, date(2027, 3, 5), "10:00", "11:00")

    rows = client.get(
        "/api/calendar", headers=MINA, params={"from": "2027-03-01", "to": "2027-03-02"}
    ).json()

    row = next(row for row in rows if row["kind"] == "task" and row["task_id"] == task_id)
    assert [entry["on_date"] for entry in row["schedules"]] == ["2027-03-01"]


def test_the_calendar_refuses_a_reversed_or_missing_range(tmp_path) -> None:
    client, _ = _stack(tmp_path)

    assert client.get(
        "/api/calendar", headers=MINA, params={"from": "2027-03-05", "to": "2027-03-01"}
    ).status_code == 422
    assert client.get("/api/calendar", headers=MINA, params={"from": "2027-03-01"}).status_code == 422


# ---- `GET /api/meetings` 의 기간 파라미터 (확정 — §H · 증보 K2) ----------------


def test_a_range_turns_the_meeting_list_into_one_array_with_no_partition_or_cursor(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    inside = _meeting(client, "이 주의 회의", date(2027, 3, 4))
    _meeting(client, "다음 달 회의", date(2027, 4, 4))

    ranged = client.get("/api/meetings", headers=MINA, params={"from": "2027-03-01", "to": "2027-03-05"})

    assert ranged.status_code == 200, ranged.text
    rows = ranged.json()
    assert isinstance(rows, list)
    assert [row["meeting_id"] for row in rows] == [inside["meeting_id"]]


def test_without_a_range_the_meeting_list_keeps_its_two_partitions(tmp_path) -> None:
    """회의 화면은 그 인자를 보내지 않으므로 **지금 그대로**다."""
    client, _ = _stack(tmp_path)
    _meeting(client, "이 주의 회의", date(2027, 3, 4))

    board = client.get("/api/meetings", headers=MINA).json()

    assert set(board) == {"upcoming", "past"}
    assert set(board["past"]) == {"items", "next_cursor"}
    assert any(row["title"] == "이 주의 회의" for row in board["upcoming"])


def test_half_a_range_is_refused_rather_than_silently_ignored(tmp_path) -> None:
    client, _ = _stack(tmp_path)

    assert client.get("/api/meetings", headers=MINA, params={"from": "2027-03-01"}).status_code == 422
    assert client.get("/api/meetings", headers=MINA, params={"to": "2027-03-05"}).status_code == 422
    assert client.get(
        "/api/meetings", headers=MINA, params={"from": "2027-03-05", "to": "2027-03-01"}
    ).status_code == 422


def test_the_range_argument_does_not_reach_the_other_two_readers_of_the_same_query(tmp_path) -> None:
    """`meetings_visible_to` 의 소비처는 **셋**이고 이 work 가 바꾸는 것은 **하나**다.

    시각 조건은 **선택적 인자(기본 없음)** 라, 회의 목록(`my_meetings`)과 자료 검색(`readable_rows`)은
    그것을 넘기지 않으므로 창 밖 회의를 계속 본다. 무조건 필터로 짜면 셋이 함께 바뀐다.
    """
    client, database_url = _stack(tmp_path)
    near = _meeting(client, "이 주의 회의", date(2027, 3, 4))
    far = _meeting(client, "다음 달 회의", date(2027, 4, 4))
    application = client.app.state.workflow_application
    principal = application.authenticated_principal("mina")

    narrow = client.get("/api/meetings", headers=MINA, params={"from": "2027-03-01", "to": "2027-03-05"}).json()
    assert [row["meeting_id"] for row in narrow] == [near["meeting_id"]]

    mine = {row["meeting_id"] for row in application.my_meetings(principal)}
    assert {near["meeting_id"], far["meeting_id"]} <= mine

    with make_session_factory(database_url)() as session:
        meetings = MeetingApplication(SqlAlchemyMeetingRepository(session))
        readable = {row["meeting_id"] for row in meetings.readable_rows(principal)}
    assert {near["meeting_id"], far["meeting_id"]} <= readable
