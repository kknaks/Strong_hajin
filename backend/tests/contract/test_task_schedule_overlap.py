"""시간이 겹치면 **뒤에 오는 것을 막는다** (SPEC-004 §2.9 · DEC-003 증보 8 K22 · WORK-004 Phase BE-3).

**무엇을 증명하는가.** 「하루 한 칸」이 막던 것은 **같은 업무의 같은 날**뿐이었다. 이제 배정
생성·시각 변경이 **나의 다른 일정**과 겹치면 `409` 다 — 그 일정이 **다른 업무의 배정**이든
**회의**든 같다. 겹침을 읽는 **함수가 하나**이므로 두 표가 같은 규칙으로 읽힌다.

**경계는 겹침이 아니다** — 반열림 `[시작, 끝)`. 11:00 에 끝나는 일정이 있어도 11:00 시작 배정이 선다.
**자정을 넘는 회의는 날짜별로 쪼개** 비교되므로 그 다음 날 새벽 배정도 막힌다 — 배정은 자정을
넘을 수 없지만 회의는 넘는다.

**영수증이 겹침보다 먼저다** (증보 K12). 같은 멱등 키의 재전송은 **자기가 방금 만든 배정과 같은
구간**이라 자기 자신과 겹치는데, 그래도 `200` 영수증이다 — 그 순서를 뒤집으면 **드래그 연타가
`409` 를 맞는다.**

**무엇을 증명하지 않는가.** 반열림과 자정 분할의 **규칙 자체**는 `tests/unit/test_time_blocks.py` 가
데이터베이스 없이 증명한다. **회의 생성·시각 변경 쪽 검증은 Phase BE-4** 다 — 이 판은 배정 쪽이고,
같은 함수를 그쪽이 그대로 재사용한다. **동시 요청 둘이 각각 검사를 통과하는 틈은 감추지 않는다** —
그것은 계약이 인정한 한계이고(§2.9 한계 표) 여기서 없다고 주장하지 않는다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import make_session_factory
from ax_workspace.platform.task_schedules import SqlAlchemyTaskScheduleRepository

MINA = {"X-Demo-Persona": "mina"}  # 제품팀원 — 업무의 활성 담당자
JIHO = {"X-Demo-Persona": "jiho"}  # 제품팀장 — **남의 시간**이다

#: 자동 취소 판정이 닿지 않는 먼 미래. 이 파일은 시각 축을 보지 회의 상태를 보지 않는다.
FIRST = date(2027, 3, 1)
LAST = date(2027, 3, 5)
DAY = date(2027, 3, 3)
OFFICE = ZoneInfo("Asia/Seoul")
#: SPEC §4 Case Matrix `TASK_SCHEDULE_OVERLAP` 의 문구. 화면 문구는 프론트가 만든다 (§I).
OVERLAP_MESSAGE = "이미 다른 일정이 있는 시간입니다"


def _stack(tmp_path) -> tuple[TestClient, str]:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    return TestClient(create_app(settings)), database_url


def _task(client: TestClient, title: str = "월~금짜리 업무", headers: dict[str, str] = MINA) -> str:
    created = client.post(
        "/api/tasks",
        headers=headers,
        json={"title": title, "start_date": FIRST.isoformat(), "due_date": LAST.isoformat()},
    )
    assert created.status_code == 201, created.text
    return created.json()["task_id"]


def _slot(client: TestClient, task_id: str, on_date: date, start: str, end: str, **kwargs):
    return client.post(
        f"/api/tasks/{task_id}/schedules",
        headers=kwargs.pop("headers", MINA),
        json={"on_date": on_date.isoformat(), "starts_at": start, "ends_at": end},
        **kwargs,
    )


def _standing(client: TestClient, task_id: str, on_date: date, start: str, end: str, **kwargs) -> dict:
    created = _slot(client, task_id, on_date, start, end, **kwargs)
    assert created.status_code == 201, created.text
    return created.json()


def _retime(client: TestClient, schedule: dict, start: str, end: str, headers: dict[str, str] = MINA):
    return client.patch(
        f"/api/task-schedules/{schedule['schedule_id']}",
        headers=headers,
        json={"expected_version": schedule["version"], "starts_at": start, "ends_at": end},
    )


def _meeting(
    client: TestClient,
    *,
    starts: datetime,
    ends: datetime,
    headers: dict[str, str] = MINA,
    title: str = "겹침을 만드는 회의",
    **body,
) -> dict:
    created = client.post(
        "/api/meetings",
        headers=headers,
        json={"title": title, "starts_at": starts.isoformat(), "ends_at": ends.isoformat(), **body},
    )
    assert created.status_code == 201, created.text
    return created.json()["meeting"]


def _office(on_date: date, hour: int, minute: int = 0) -> datetime:
    return datetime(on_date.year, on_date.month, on_date.day, hour, minute, tzinfo=OFFICE)


# ---- 배정 대 배정: 다른 업무와도 겹칠 수 없다 ---------------------------------------


def test_a_second_task_cannot_take_time_that_the_first_one_already_holds(tmp_path) -> None:
    """「하루 한 칸」이 못 막던 자리다 — **표는 같은데 업무가 다르다.**"""
    client, _ = _stack(tmp_path)
    held, wanted = _task(client, "먼저 잡은 일"), _task(client, "나중에 온 일")
    _standing(client, held, DAY, "10:00", "11:00")

    refused = _slot(client, wanted, DAY, "10:30", "11:30")

    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == OVERLAP_MESSAGE


def test_time_that_merely_touches_the_boundary_stands(tmp_path) -> None:
    """반열림 `[시작, 끝)` — 10:00–11:00 과 11:00–12:00 은 **둘 다 선다.**

    **새로 정한 규칙이 아니다**: 회의 조회의 docstring 이 이미 같은 말을 쓴다.
    """
    client, _ = _stack(tmp_path)
    held, wanted = _task(client, "오전"), _task(client, "오전 직후")
    _standing(client, held, DAY, "10:00", "11:00")

    assert _slot(client, wanted, DAY, "11:00", "12:00").status_code == 201
    # 앞쪽 경계도 같다 — 10:00 에 끝나는 배정은 10:00 에 시작하는 것을 막지 않는다.
    assert _slot(client, _task(client, "오전 직전"), DAY, "09:00", "10:00").status_code == 201


def test_a_slot_wholly_inside_another_is_an_overlap(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    held, wanted = _task(client, "온종일"), _task(client, "그 안에 끼우려는 일")
    _standing(client, held, DAY, "09:00", "18:00")

    assert _slot(client, wanted, DAY, "13:00", "13:30").status_code == 409


def test_the_same_times_on_another_day_stand(tmp_path) -> None:
    """겹침은 **그 날의 그 시간**이다 — 날이 다르면 같은 시각이 그대로 선다."""
    client, _ = _stack(tmp_path)
    held, wanted = _task(client, "화요일"), _task(client, "수요일")
    _standing(client, held, DAY, "10:00", "11:00")

    assert _slot(client, wanted, DAY + timedelta(days=1), "10:00", "11:00").status_code == 201


def test_the_day_taken_refusal_still_answers_for_the_same_task_and_day(tmp_path) -> None:
    """겹침이 「하루 한 칸」을 가리지 않는다 — **같은 업무·같은 날의 두 번째 생성**은 그 말로 거절된다."""
    client, _ = _stack(tmp_path)
    task_id = _task(client)
    _standing(client, task_id, DAY, "10:00", "11:00")

    refused = _slot(client, task_id, DAY, "14:00", "15:00")

    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == "이 날에는 이미 시간 배정이 있습니다"


def test_a_range_outside_the_task_span_is_still_answered_by_the_span(tmp_path) -> None:
    """겹침 검사가 **다른 거절을 앞지르지 않는다** — 기간 밖은 422 이고 문구가 기간을 적는다."""
    client, _ = _stack(tmp_path)
    held, wanted = _task(client, "기간 안"), _task(client, "기간 밖으로 가려는 일")
    _standing(client, held, DAY, "10:00", "11:00")

    refused = _slot(client, wanted, date(2027, 3, 9), "10:30", "11:30")

    assert refused.status_code == 422, refused.text
    assert "2027-03-01~2027-03-05" in refused.json()["detail"]


# ---- 시각 변경: 옮겨서 겹치게 만들어도 같다. 그리고 **자기 자신은 뺀다** ----------------


def test_moving_a_slot_onto_another_one_is_refused(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    held, moving = _task(client, "오전을 든 일"), _task(client, "오후에서 옮겨 오는 일")
    _standing(client, held, DAY, "10:00", "11:00")
    afternoon = _standing(client, moving, DAY, "14:00", "15:00")

    refused = _retime(client, afternoon, "10:30", "11:30")

    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == OVERLAP_MESSAGE


def test_moving_a_slot_onto_the_boundary_of_another_stands(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    held, moving = _task(client, "오전을 든 일"), _task(client, "붙여 세우는 일")
    _standing(client, held, DAY, "10:00", "11:00")
    afternoon = _standing(client, moving, DAY, "14:00", "15:00")

    moved = _retime(client, afternoon, "11:00", "12:00")

    assert moved.status_code == 200, moved.text
    assert (moved.json()["starts_at"], moved.json()["version"]) == ("11:00", 2)


def test_a_slot_never_collides_with_itself_when_it_slides(tmp_path) -> None:
    """**자기 자신은 뺀다.** 10:00–11:00 을 10:30–11:30 으로 끄는 것은 자기와 겹치지만 정상이다 —
    빼지 않으면 세로 손잡이가 **한 칸도 움직이지 못한다.**
    """
    client, _ = _stack(tmp_path)
    slot = _standing(client, _task(client), DAY, "10:00", "11:00")

    slid = _retime(client, slot, "10:30", "11:30")

    assert slid.status_code == 200, slid.text
    assert (slid.json()["starts_at"], slid.json()["ends_at"], slid.json()["version"]) == ("10:30", "11:30", 2)


# ---- 영수증이 겹침보다 먼저다 (증보 K12) — **이 Phase 에서 가장 틀리기 쉬운 자리** -------


def test_the_same_idempotency_key_replays_as_a_receipt_and_never_as_an_overlap(tmp_path) -> None:
    """드래그 연타 — 같은 키의 재전송은 **`200` 영수증**이고 `409` 가 아니다.

    재전송은 **자기가 방금 만든 배정과 같은 구간**이라 자기 자신과 겹친다. 겹침 검사를 멱등 원장
    **앞**에 두면 이 호출이 거절되고, **아무 일도 일어나지 않았는데** 사람에게 「이미 다른 일정이
    있습니다」라고 거짓말을 하게 된다 — 멱등 키가 존재하는 이유가 바로 이 연타다.
    """
    client, _ = _stack(tmp_path)
    task_id = _task(client)
    key = {**MINA, "Idempotency-Key": "drag-once"}

    first = _slot(client, task_id, DAY, "10:00", "11:00", headers=key)
    again = _slot(client, task_id, DAY, "10:00", "11:00", headers=key)
    once_more = _slot(client, task_id, DAY, "10:00", "11:00", headers=key)

    assert first.status_code == 201, first.text
    assert (again.status_code, once_more.status_code) == (200, 200), (again.text, once_more.text)
    # **두 번째 effect 가 없다** — 본문도 회차도 그대로다.
    assert again.json() == first.json() and once_more.json() == first.json()
    rows = client.get("/api/calendar", headers=MINA, params={"from": FIRST, "to": LAST}).json()
    standing = [row for row in rows if row["kind"] == "task" and row["task_id"] == task_id][0]["schedules"]
    assert [(entry["starts_at"], entry["version"]) for entry in standing] == [("10:00", 1)]


# ---- 회의도 블록이다 — 표는 둘이어도 문은 하나다 -----------------------------------


def test_a_meeting_blocks_the_time_it_holds(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    task_id = _task(client)
    _meeting(client, starts=_office(DAY, 10), ends=_office(DAY, 11))

    refused = _slot(client, task_id, DAY, "10:30", "11:30")

    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == OVERLAP_MESSAGE


def test_a_slot_that_starts_when_a_meeting_ends_stands(tmp_path) -> None:
    """회의에도 **같은 반열림**이 걸린다 — 두 표가 한 규칙으로 읽히는 것이 이 판의 요지다."""
    client, _ = _stack(tmp_path)
    task_id = _task(client)
    _meeting(client, starts=_office(DAY, 10), ends=_office(DAY, 11))

    assert _slot(client, task_id, DAY, "11:00", "12:00").status_code == 201


def test_a_meeting_that_crosses_midnight_blocks_both_dates(tmp_path) -> None:
    """**자정을 넘는 회의는 날짜별로 쪼개** 비교된다 (K22).

    배정은 `Date` + `Time` 이라 자정을 넘을 수 없다 — 쪼개지 않으면 **다음 날 새벽에는 그 회의가
    없는 것**이 되어 00:30 배정이 통과한다. 그 구현이 BE-3 이므로 증명도 여기서 한다.
    """
    client, _ = _stack(tmp_path)
    late, dawn, after = _task(client, "밤"), _task(client, "새벽"), _task(client, "새벽 뒤")
    _meeting(client, starts=_office(DAY, 23), ends=_office(DAY + timedelta(days=1), 1))

    assert _slot(client, late, DAY, "23:30", "23:45").status_code == 409
    # 다음 날 조각이 실제로 서 있다 — 이 줄이 자정 분할의 증명이다.
    blocked = _slot(client, dawn, DAY + timedelta(days=1), "00:30", "01:30")
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"] == OVERLAP_MESSAGE
    # 그 조각도 반열림이다 — 01:00 에 끝나므로 01:00 시작 배정은 선다.
    assert _slot(client, after, DAY + timedelta(days=1), "01:00", "02:00").status_code == 201


def test_a_cancelled_meeting_holds_no_time(tmp_path) -> None:
    """취소된 회의는 블록이 아니다 — 자동 취소가 지난 「예정」을 옮기므로, 세면 **되살릴 수 없는
    이유로 그 시간이 영구히 막힌다.**
    """
    client, _ = _stack(tmp_path)
    task_id = _task(client)
    meeting = _meeting(client, starts=_office(DAY, 10), ends=_office(DAY, 11))
    cancelled = client.delete(f"/api/meetings/{meeting['meeting_id']}", headers=MINA)
    assert cancelled.status_code == 204, cancelled.text

    assert _slot(client, task_id, DAY, "10:30", "11:30").status_code == 201


# ---- 「나의 시간만」 — 남의 일정은 애초에 안 보인다 (배정 쪽) -------------------------


def test_someone_elses_meeting_does_not_block_my_slot(tmp_path) -> None:
    """배정 쪽이 보는 것은 **나의 시간만**이다. 주최자·참석자 전원을 보는 것은 회의 쪽(BE-4)이다."""
    client, _ = _stack(tmp_path)
    task_id = _task(client)
    _meeting(client, starts=_office(DAY, 10), ends=_office(DAY, 11), headers=JIHO, title="팀장의 회의")

    assert _slot(client, task_id, DAY, "10:30", "11:30").status_code == 201


def test_a_meeting_i_attend_blocks_my_slot_even_when_someone_else_called_it(tmp_path) -> None:
    """**주최자만이 아니다** — 참석 회의는 이미 내 캘린더에 서 있으므로 내 시간을 차지한다 (§D)."""
    client, _ = _stack(tmp_path)
    task_id = _task(client)
    _meeting(
        client, starts=_office(DAY, 10), ends=_office(DAY, 11), headers=JIHO,
        title="팀장이 부른 회의", attendee_ids=["mina"],
    )

    refused = _slot(client, task_id, DAY, "10:30", "11:30")

    assert refused.status_code == 409, refused.text


def test_someone_elses_assignment_does_not_block_my_slot(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    mine = _task(client)
    theirs = _task(client, "팀장의 일", headers=JIHO)
    _standing(client, theirs, DAY, "10:00", "11:00", headers=JIHO)

    assert _slot(client, mine, DAY, "10:30", "11:30").status_code == 201


def test_a_slot_on_a_finished_task_holds_no_time(tmp_path) -> None:
    """끝난 업무는 **캘린더에서 빠진다**(§2.5 나) — 그러면 그 배정도 블록이 아니다.

    그렇지 않으면 이미 완료된 일의 시간표가 **되살릴 수 없는 이유로** 달력을 계속 점유한다.
    """
    client, _ = _stack(tmp_path)
    finished, fresh = _task(client, "끝낼 일"), _task(client, "그 자리에 들어오는 일")
    _standing(client, finished, DAY, "10:00", "11:00")
    done = client.post(
        f"/api/tasks/{finished}/complete",
        headers=MINA,
        json={"expected_version": client.get(f"/api/tasks/{finished}", headers=MINA).json()["version"]},
    )
    assert done.status_code == 200, done.text

    assert _slot(client, fresh, DAY, "10:30", "11:30").status_code == 201


# ---- 기존 데이터는 그대로 둔다 — 검증은 **새 쓰기에만** 걸린다 (K22) ------------------


def test_already_overlapping_rows_are_neither_closed_nor_repaired(tmp_path) -> None:
    """이미 겹쳐 있는 것을 **소급해 닫거나 고치는 쓰기를 만들지 않는다.**

    검증이 없던 동안 쌓인 행이 실재한다. 그 둘을 살려 두고, 그 뒤의 정상 쓰기도 그것들을 건드리지
    않는다 — 겹침 때문에 행이 사라지는 경로는 **없다.**
    """
    client, database_url = _stack(tmp_path)
    one, two = _task(client, "먼저"), _task(client, "겹쳐 쌓인 것")
    _standing(client, one, DAY, "10:00", "11:00")
    with make_session_factory(database_url)() as session:
        # 검증을 지나지 않는 자리에서 직접 세운다 — 검증 이전에 쌓인 행을 재현한다.
        SqlAlchemyTaskScheduleRepository(session).create(
            UUID(two), DAY, datetime.strptime("10:30", "%H:%M").time(), datetime.strptime("11:30", "%H:%M").time()
        )
        session.commit()

    # 그 뒤의 정상 쓰기 — 겹치지 않는 날에 한 칸.
    assert _slot(client, one, DAY + timedelta(days=1), "10:00", "11:00").status_code == 201

    rows = client.get("/api/calendar", headers=MINA, params={"from": FIRST, "to": LAST}).json()
    standing = {
        row["task_id"]: [(entry["on_date"], entry["starts_at"]) for entry in row["schedules"]]
        for row in rows
        if row["kind"] == "task"
    }
    assert (DAY.isoformat(), "10:00") in standing[one]
    assert standing[two] == [(DAY.isoformat(), "10:30")]
