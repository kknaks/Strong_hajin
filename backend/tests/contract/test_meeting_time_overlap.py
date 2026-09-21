"""회의 쪽 겹침 검증 · 합본 조회의 `approval` (SPEC-004 §2.9 · §4 · 증보 K19·K22·K25 · Phase BE-4).

**무엇을 증명하는가.** BE-3 이 배정 쪽에 걸었던 **같은 문**(`overlapping_blocks`)이 이제 회의
생성·시각 변경에도 걸린다. 다른 것은 **묻는 사람의 수**뿐이다 — 배정은 「나의 시간만」, 회의는
**주최자 + 활성 참석자 전원**이다. 반열림 `[시작, 끝)` 도 자정 분할도 그 문 안에 그대로 있다.

**이 판이 SPEC §1 Scope Out 을 뒤집는다.** 그래서 **기존 회의 계약이 안 바뀌었다는 것**을 같은
파일에서 함께 센다 — 목록의 두 모양, `my_meetings`, 권한, 응답 행 모양, 그리고 **시각을 바꾸지
않는 편집은 이 검사를 지나지 않는다**는 것.

**막지 않는 것 셋.** `quick-start`(지금 당장 시작하는 것) · **사외 참석자**(계정이 없어 일정이
없다) · **취소된 회의**(K26). 그리고 **이미 겹쳐 있는 회의**는 그대로 둔다 — 검증은 새 쓰기에만.

**무엇을 증명하지 않는다.** 반열림·자정 분할의 규칙 자체는 `tests/unit/test_time_blocks.py` 가
데이터베이스 없이 답하고, 배정 쪽 계약은 `tests/contract/test_task_schedule_overlap.py` 가 갖는다.
**동시 요청 둘이 각각 검사를 통과하는 틈은 감추지 않는다** — 계약이 인정한 한계다 (§2.9 한계 표).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database

MINA = {"X-Demo-Persona": "mina"}        # 제품팀원 — 표시 이름 「민아 (구성원)」
JIHO = {"X-Demo-Persona": "jiho"}        # 제품팀장 — 「지호 (팀장)」
SORA = {"X-Demo-Persona": "sora"}        # 법무 자문 — `guest` 라 회의를 **만들지는 못한다**
MINSEOK = {"X-Demo-Persona": "minseok"}  # 재무팀원 — 제품팀 회의에 잡혀 있지 않다
HYEON = {"X-Demo-Persona": "hyeon"}      # 인사 — 같은 이유로 자기 시간이 비어 있다

#: 자동 취소 판정이 닿지 않는 먼 미래.
FIRST = date(2027, 3, 1)
LAST = date(2027, 3, 5)
DAY = date(2027, 3, 3)
NEXT = date(2027, 3, 4)
OFFICE = ZoneInfo("Asia/Seoul")


def _stack(tmp_path) -> tuple[TestClient, str]:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    return TestClient(create_app(settings)), database_url


def _office(on_date: date, hour: int, minute: int = 0) -> datetime:
    return datetime(on_date.year, on_date.month, on_date.day, hour, minute, tzinfo=OFFICE)


def _task(client: TestClient, title: str = "월~금짜리 업무", headers: dict[str, str] = MINA) -> str:
    created = client.post(
        "/api/tasks",
        headers=headers,
        json={"title": title, "start_date": FIRST.isoformat(), "due_date": LAST.isoformat()},
    )
    assert created.status_code == 201, created.text
    return created.json()["task_id"]


def _slot(client: TestClient, task_id: str, on_date: date, start: str, end: str, headers: dict[str, str] = MINA):
    created = client.post(
        f"/api/tasks/{task_id}/schedules",
        headers=headers,
        json={"on_date": on_date.isoformat(), "starts_at": start, "ends_at": end},
    )
    assert created.status_code == 201, created.text
    return created.json()


def _book(client: TestClient, starts: datetime, ends: datetime, headers: dict[str, str] = JIHO, **body):
    return client.post(
        "/api/meetings",
        headers=headers,
        json={"title": body.pop("title", "예약하려는 회의"), "starts_at": starts.isoformat(), "ends_at": ends.isoformat(), **body},
    )


def _booked(client: TestClient, starts: datetime, ends: datetime, headers: dict[str, str] = JIHO, **body) -> dict:
    created = _book(client, starts, ends, headers, **body)
    assert created.status_code == 201, created.text
    return created.json()["meeting"]


def _edit(client: TestClient, meeting_id: str, changes: dict, headers: dict[str, str] = JIHO):
    return client.patch(f"/api/meetings/{meeting_id}", headers=headers, json=changes)


# ---- 생성: 주최자 + 활성 참석자 전원의 시간을 본다 -----------------------------


def test_a_meeting_over_an_attendees_assignment_is_refused_and_names_only_that_person(tmp_path) -> None:
    """**이름은 말하고 내용은 말하지 않는다** (K22). 남의 시간은 busy/free 만 읽는다.

    참석자를 막는 것이 정당한 이유는 **참석 회의가 이미 그 사람 캘린더에 선다**는 것이다 —
    안 보이는 것을 근거로 막는 것이 아니다.
    """
    client, _ = _stack(tmp_path)
    _slot(client, _task(client, "민아의 비밀스런 업무"), DAY, "10:00", "11:00")

    refused = _book(client, _office(DAY, 10, 30), _office(DAY, 11, 30), attendee_ids=["mina"])

    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == "민아 님의 일정과 겹칩니다"
    # **제목도 내용도 없다** — 무슨 일정인지 말하지 않는다.
    assert "업무" not in refused.json()["detail"] and "비밀" not in refused.json()["detail"]


def test_the_organizers_own_time_is_read_too(tmp_path) -> None:
    """주최자도 참석자다 — 자기 배정 위에 자기 회의를 세울 수 없다."""
    client, _ = _stack(tmp_path)
    _slot(client, _task(client, "지호의 업무", headers=JIHO), DAY, "14:00", "15:00", headers=JIHO)

    refused = _book(client, _office(DAY, 14, 30), _office(DAY, 15, 30))

    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == "지호 님의 일정과 겹칩니다"


def test_a_meeting_over_an_attendees_other_meeting_is_refused(tmp_path) -> None:
    """블록은 **두 표**에서 나온다 — 배정만이 아니다."""
    client, _ = _stack(tmp_path)
    _booked(client, _office(DAY, 10), _office(DAY, 11), headers=MINA, title="민아가 이미 잡은 회의")

    refused = _book(client, _office(DAY, 10, 30), _office(DAY, 11, 30), attendee_ids=["mina"])

    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == "민아 님의 일정과 겹칩니다"


def test_a_meeting_that_merely_touches_the_boundary_stands(tmp_path) -> None:
    """반열림 — 11:00 에 끝나는 일정과 11:00 에 시작하는 회의는 **둘 다 선다.**"""
    client, _ = _stack(tmp_path)
    _slot(client, _task(client), DAY, "10:00", "11:00")

    assert _book(client, _office(DAY, 11), _office(DAY, 12), attendee_ids=["mina"]).status_code == 201
    assert _book(client, _office(DAY, 9), _office(DAY, 10), attendee_ids=["mina"]).status_code == 201


def test_a_third_partys_schedule_is_not_read(tmp_path) -> None:
    """검사 대상은 **그 회의의 사람들**이다 — 참석자가 아닌 사람의 일정은 회의를 막지 않는다."""
    client, _ = _stack(tmp_path)
    _slot(client, _task(client, "민아의 업무"), DAY, "10:00", "11:00")

    # 민아를 부르지 않은 회의는 민아의 배정과 무관하다.
    assert _book(client, _office(DAY, 10, 30), _office(DAY, 11, 30), attendee_ids=["sora"]).status_code == 201


def test_a_meeting_that_crosses_midnight_is_refused_against_the_next_dawn(tmp_path) -> None:
    """자정 분할은 **회의 쪽에서도** 같다 — 새벽 배정 위로 밤 회의를 세울 수 없다."""
    client, _ = _stack(tmp_path)
    _slot(client, _task(client, "새벽 업무"), NEXT, "00:30", "01:30")

    refused = _book(client, _office(DAY, 23), _office(NEXT, 1), attendee_ids=["mina"])

    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == "민아 님의 일정과 겹칩니다"


# ---- 막지 않는 것 셋 ----------------------------------------------------------


def test_quick_start_is_never_refused_for_overlapping(tmp_path) -> None:
    """**`quick-start` 는 막지 않는다** (확정 — §2.9). 지금 당장 시작하는 것이라 막으면 못 쓴다.

    분기로 빼지 않았다 — `quick_start` 는 겹침 검사가 사는 `_validated_creation` 을 **지나지 않는다.**
    그래서 「제외를 잊었다」가 생길 자리가 없다.
    """
    client, _ = _stack(tmp_path)
    now = datetime.now(OFFICE)
    # 지금 이 시각을 통째로 덮는 회의를 먼저 세운다 — 바로 시작이 걸린다면 여기서 걸린다.
    live = client.post(
        "/api/meetings",
        headers=JIHO,
        json={
            "title": "지금 진행 중인 회의",
            "starts_at": (now - timedelta(hours=1)).isoformat(),
            "ends_at": (now + timedelta(hours=3)).isoformat(),
        },
    )
    assert live.status_code == 201, live.text

    started = client.post("/api/meetings/quick-start", headers=JIHO)

    assert started.status_code == 201, started.text
    assert started.json()["meeting"]["status"] == "in_progress"


def test_an_external_attendee_never_blocks_the_meeting(tmp_path) -> None:
    """**사외 참석자는 검사 대상이 아니다** — 계정이 없어 일정이 없다.

    빼는 분기를 두지 않았다: 사외 이름은 `member_ids` 에 들어갈 member id 가 **애초에 없다.**
    """
    client, _ = _stack(tmp_path)

    created = _book(
        client, _office(DAY, 10), _office(DAY, 11),
        attendee_ids=["mina"], external_attendees=["한빛상사 김대표"],
    )

    assert created.status_code == 201, created.text
    assert created.json()["meeting"]["external_attendees"] == ["한빛상사 김대표"]


def test_a_cancelled_meeting_holds_no_time_on_the_meeting_side_either(tmp_path) -> None:
    """K26 — 취소된 회의는 블록이 아니다. 세면 **되살릴 수 없는 이유로** 그 시간이 영구히 막힌다."""
    client, _ = _stack(tmp_path)
    dropped = _booked(client, _office(DAY, 10), _office(DAY, 11), headers=MINA)
    assert client.delete(f"/api/meetings/{dropped['meeting_id']}", headers=MINA).status_code == 204

    assert _book(client, _office(DAY, 10, 30), _office(DAY, 11, 30), attendee_ids=["mina"]).status_code == 201


# ---- 시각 변경: 자기 자신은 빼고, 시각이 안 바뀌면 지나지 않는다 ------------------


def test_moving_a_meeting_onto_an_attendees_time_is_refused(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    _slot(client, _task(client), DAY, "10:00", "11:00")
    meeting = _booked(client, _office(DAY, 14), _office(DAY, 15), attendee_ids=["mina"])

    refused = _edit(
        client, meeting["meeting_id"],
        {"starts_at": _office(DAY, 10, 30).isoformat(), "ends_at": _office(DAY, 11, 30).isoformat()},
    )

    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == "민아 님의 일정과 겹칩니다"


def test_a_meeting_never_collides_with_itself_when_it_slides(tmp_path) -> None:
    """**자기 자신은 뺀다.** 빼지 않으면 시각을 **한 칸도** 옮길 수 없다.

    **옮겨진 순간까지 본다.** 10:30 사무실 시각은 `01:30Z` 다 — `MeetingInfoPatch` 가 UTC 로
    정규화하므로(증보 10 K28) SQLite 에서도 PostgreSQL 과 같은 순간이 남는다. 그 한 줄이 없으면
    이 단언이 `10:30+00:00` 이 되어 **검증한 시각과 저장된 시각이 아홉 시간 달라진다.**
    """
    client, _ = _stack(tmp_path)
    meeting = _booked(client, _office(DAY, 10), _office(DAY, 11), attendee_ids=["mina"])

    slid = _edit(
        client, meeting["meeting_id"],
        {"starts_at": _office(DAY, 10, 30).isoformat(), "ends_at": _office(DAY, 11, 30).isoformat()},
    )

    assert slid.status_code == 200, slid.text
    assert slid.json()["meeting"]["starts_at"] == "2027-03-03T01:30:00+00:00"
    assert slid.json()["meeting"]["ends_at"] == "2027-03-03T02:30:00+00:00"


def test_moving_a_meeting_to_the_boundary_of_another_stands(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    _slot(client, _task(client), DAY, "10:00", "11:00")
    meeting = _booked(client, _office(DAY, 14), _office(DAY, 15), attendee_ids=["mina"])

    moved = _edit(
        client, meeting["meeting_id"],
        {"starts_at": _office(DAY, 11).isoformat(), "ends_at": _office(DAY, 12).isoformat()},
    )

    assert moved.status_code == 200, moved.text


def test_editing_a_meeting_without_moving_it_never_asks_about_overlap(tmp_path) -> None:
    """**시각이 실제로 바뀐 경우에만 검사한다** — 검증은 새 쓰기에만 걸린다 (K22).

    안 걸면 **이미 겹쳐 있는 회의의 제목조차 고칠 수 없다.** 그것은 기존 데이터를 소급해 막는 것이고,
    계약이 그러지 말라고 못 박은 자리다.

    **진짜로 겹쳐 있는 한 쌍을 먼저 세운다.** 회의 하나만 두고 같은 시각을 다시 실어 보내면
    시각 검사가 **자기 자신을 빼므로**(`ignoring=meeting.id`) 가드가 있든 없든 `200` 이다 —
    그것으로는 이 계약을 재지 못한다. 그래서 **참석자만 바꾸는 경로**(관문을 지나지 않는다 — K30)로
    민아를 두 회의에 함께 넣어 **겹친 상태를 만든 뒤** 제목을 고친다.
    """
    client, _ = _stack(tmp_path)
    held = _booked(client, _office(DAY, 10), _office(DAY, 11), attendee_ids=["mina"], title="민아가 든 회의")
    # 두 번째 회의의 주최자는 **그 시각이 빈 사람**이어야 한다 — 지호로 세우면 자기 회의와 겹쳐 거절된다.
    overlapping = _booked(client, _office(DAY, 10), _office(DAY, 11), headers=MINSEOK, title="겹쳐 쌓일 회의")
    # **참석자만** 바꾸는 것은 겹침 관문을 지나지 않는다(K30) — 여기서 **실제로 겹친 한 쌍**이 선다.
    joined = _edit(client, overlapping["meeting_id"], {"attendee_ids": ["mina"]}, headers=MINSEOK)
    assert joined.status_code == 200, joined.text
    # 그 상태가 실재하는지 못 박는다: 민아는 이제 10–11 시에 **두 번** 잡혀 있고,
    # 그 시간이 빈 사람이 민아를 부르면 거절된다.
    doubled = _book(client, _office(DAY, 10, 30), _office(DAY, 11, 30), headers=HYEON, attendee_ids=["mina"])
    assert doubled.status_code == 409, doubled.text
    assert doubled.json()["detail"] == "민아 님의 일정과 겹칩니다"

    renamed = _edit(client, overlapping["meeting_id"], {"title": "이름만 바꾼다"}, headers=MINSEOK)

    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["meeting"]["title"] == "이름만 바꾼다"
    # 그리고 **겹쳐 있던 쪽도 그대로 산다** — 소급해 닫거나 고치는 쓰기가 없다.
    assert client.get(f"/api/meetings/{held['meeting_id']}", headers=JIHO).json()["meeting"]["status"] == "scheduled"


def test_adding_an_attendee_alone_does_not_pass_the_overlap_gate(tmp_path) -> None:
    """**계약이 드는 것은 「생성」과 「시각 변경」 둘이다** (§2.9 표). 참석자만 바뀌는 것은 그 둘이 아니다.

    그래서 바쁜 사람을 **나중에 불러 넣는 것은 막히지 않는다.** 이 테스트는 그 사실을 고정한다 —
    구멍으로 볼 수도 있는 자리이고, 넓히는 것은 계약을 바꾸는 일이라 여기서 하지 않았다.
    """
    client, _ = _stack(tmp_path)
    _slot(client, _task(client), DAY, "10:00", "11:00")
    meeting = _booked(client, _office(DAY, 10, 30), _office(DAY, 11, 30), attendee_ids=["sora"])

    added = _edit(client, meeting["meeting_id"], {"attendee_ids": ["sora", "mina"]})

    assert added.status_code == 200, added.text


def test_moving_a_meeting_asks_about_the_attendees_it_will_have(tmp_path) -> None:
    """시각과 참석자가 **함께** 오면 **바뀐 뒤의 명부**로 묻는다 — 옛 명부로 물으면 조용히 새 사람이 샌다."""
    client, _ = _stack(tmp_path)
    _slot(client, _task(client), DAY, "10:00", "11:00")
    meeting = _booked(client, _office(DAY, 14), _office(DAY, 15), attendee_ids=["sora"])

    refused = _edit(
        client, meeting["meeting_id"],
        {
            "starts_at": _office(DAY, 10, 30).isoformat(),
            "ends_at": _office(DAY, 11, 30).isoformat(),
            "attendee_ids": ["sora", "mina"],
        },
    )

    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == "민아 님의 일정과 겹칩니다"
    # **거절이면 아무것도 바뀌지 않는다** — 명부도 시각도 그대로다 (14:00 사무실 시각 = `05:00Z`).
    current = client.get(f"/api/meetings/{meeting['meeting_id']}", headers=JIHO).json()["meeting"]
    assert current["starts_at"] == "2027-03-03T05:00:00+00:00"
    assert {row["member_id"] for row in current["attendees"]} == {"jiho", "sora"}


def test_the_room_provider_is_never_called_before_the_overlap_is_answered(tmp_path) -> None:
    """회의실을 고른 생성도 **같은 문을 먼저 지난다** — `validate_creation` 이 provider 호출 앞에 선다.

    뒤에 두면 자리를 잡아 놓고 회의가 서지 않는 **고아 예약**이 남는다. 회의실 예약은 외부 provider
    설정이 없으면 스스로 없다고 답하므로, 여기서 세는 것은 **겹침이 먼저 답한다**는 순서다.
    """
    client, _ = _stack(tmp_path)
    _slot(client, _task(client), DAY, "10:00", "11:00")

    refused = client.post(
        "/api/meetings",
        headers=JIHO,
        json={
            "title": "회의실을 고른 회의",
            "starts_at": _office(DAY, 10, 30).isoformat(),
            "ends_at": _office(DAY, 11, 30).isoformat(),
            "attendee_ids": ["mina"],
            "room_id": 1,
        },
    )

    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == "민아 님의 일정과 겹칩니다"


# ---- 기존 회의 계약은 안 바뀌었다 (회귀) ----------------------------------------


def test_the_meeting_list_keeps_both_of_its_shapes(tmp_path) -> None:
    """1루프에서 `meetings_visible_to` 소비처 셋이 걸렸던 자리다 — 이 판도 그 셋을 바꾸지 않는다."""
    client, _ = _stack(tmp_path)
    meeting = _booked(client, _office(DAY, 10), _office(DAY, 11), attendee_ids=["mina"])

    ranged = client.get("/api/meetings", headers=JIHO, params={"from": FIRST.isoformat(), "to": LAST.isoformat()})
    assert ranged.status_code == 200
    assert isinstance(ranged.json(), list)
    [row] = [item for item in ranged.json() if item["meeting_id"] == meeting["meeting_id"]]
    # 행 모양 아홉 그대로 — 기간 갈래에는 `created_by_display_name` 이 없다 (증보 K4).
    assert set(row) == {
        "meeting_id", "title", "starts_at", "ends_at", "location",
        "status", "viewer_relation", "created_by", "attendee_count",
    }

    paged = client.get("/api/meetings", headers=JIHO)
    assert paged.status_code == 200
    assert set(paged.json()) == {"upcoming", "past"}
    assert set(paged.json()["past"]) == {"items", "next_cursor"}


def test_someone_who_may_not_open_a_meeting_still_cannot(tmp_path) -> None:
    """권한은 그대로다 — 겹침 검증이 열람 계약을 넓히거나 좁히지 않는다."""
    client, _ = _stack(tmp_path)
    meeting = _booked(client, _office(DAY, 10), _office(DAY, 11), headers=MINA)

    assert client.get(f"/api/meetings/{meeting['meeting_id']}", headers={"X-Demo-Persona": "minseok"}).status_code == 404


def test_a_meeting_with_no_conflict_is_created_exactly_as_before(tmp_path) -> None:
    client, _ = _stack(tmp_path)

    created = _book(client, _office(DAY, 10), _office(DAY, 11), attendee_ids=["mina"], location="3층 회의실")

    assert created.status_code == 201, created.text
    meeting = created.json()["meeting"]
    assert meeting["status"] == "scheduled" and meeting["location"] == "3층 회의실"
    assert {row["member_id"] for row in meeting["attendees"]} == {"jiho", "mina"}


# ---- 합본 조회의 `approval` (증보 K19) -----------------------------------------


def _requested_task(client: TestClient, title: str) -> str:
    """민아가 부탁하고 지호가 든다 — 완료 확인이 붙는 유일한 모양이다."""
    request = client.post("/api/work-requests", headers=MINA, json={"title": title, "assignee_id": "jiho"}).json()
    accepted = client.post(
        f"/api/work-requests/{request['request_id']}/accept",
        headers=JIHO,
        json={"expected_version": request["version"]},
    )
    assert accepted.status_code == 200, accepted.text
    [task] = [row for row in client.get("/api/my-work", headers=JIHO).json() if row["title"] == title]
    return task["task_id"]


def _calendar_row(client: TestClient, task_id: str, headers: dict[str, str] = JIHO) -> dict:
    rows = client.get(
        "/api/calendar", headers=headers, params={"from": FIRST.isoformat(), "to": LAST.isoformat()}
    ).json()
    return next(row for row in rows if row["kind"] == "task" and row["task_id"] == task_id)


def test_a_calendar_task_row_carries_the_approval_and_nothing_else_of_derived(tmp_path) -> None:
    """**`derived` 묶음 전체가 아니라 이 한 값만**이다 (K19). 행의 필드 집합을 그대로 못 박는다."""
    client, _ = _stack(tmp_path)
    task_id = _task(client, headers=JIHO)

    row = _calendar_row(client, task_id)

    assert row["approval"] is None
    assert set(row) == {
        "kind", "task_id", "title", "state", "approval",
        "start_date", "due_date", "span_from", "span_to", "version", "schedules",
    }
    assert "derived" not in row and "overdue_days" not in row and "blocking_children" not in row


def test_a_submitted_task_stands_as_done_and_says_it_is_awaiting_review(tmp_path) -> None:
    """**이 한 값이 없으면 카드가 거짓말을 한다** — `COMPLETION_SUBMITTED` 가 `"done"` 으로 투영된다."""
    client, _ = _stack(tmp_path)
    task_id = _requested_task(client, "확인을 기다릴 업무")
    current = client.get(f"/api/tasks/{task_id}", headers=JIHO).json()
    started = client.post(
        f"/api/tasks/{task_id}/start", headers=JIHO, json={"expected_version": current["version"]}
    )
    assert started.status_code == 200, started.text
    current = client.get(f"/api/tasks/{task_id}", headers=JIHO).json()
    reported = client.post(
        f"/api/tasks/{task_id}/completion-report",
        headers=JIHO,
        json={"expected_version": current["version"], "summary": "정리했습니다"},
    )
    assert reported.status_code == 200, reported.text

    row = _calendar_row(client, task_id)

    assert (row["state"], row["approval"]) == ("done", "awaiting_review")
    # **판정이 목록·상세와 같은 규칙이다** — 같은 함수를 지난다.
    [listed] = [item for item in client.get("/api/my-work", headers=JIHO).json() if item["task_id"] == task_id]
    assert listed["derived"]["approval"] == row["approval"]


def test_an_accepted_result_reads_as_approved_and_then_leaves_the_calendar(tmp_path) -> None:
    """승인까지 끝나면 업무가 내부 `DONE` 이 되어 **캘린더에서 빠진다** (§2.5 나).

    그래서 `approved` 는 캘린더 행으로는 보이지 않는다 — 목록이 그 값을 낸다. 그 사실까지 센다:
    `approval` 이 있어야 하는 이유는 **승인 전**을 말하기 위해서다.
    """
    client, _ = _stack(tmp_path)
    task_id = _requested_task(client, "승인까지 갈 업무")
    current = client.get(f"/api/tasks/{task_id}", headers=JIHO).json()
    client.post(f"/api/tasks/{task_id}/start", headers=JIHO, json={"expected_version": current["version"]})
    current = client.get(f"/api/tasks/{task_id}", headers=JIHO).json()
    client.post(
        f"/api/tasks/{task_id}/completion-report",
        headers=JIHO,
        json={"expected_version": current["version"], "summary": "정리했습니다"},
    )
    item = next(
        row
        for row in client.get("/api/action-items", headers=MINA).json()
        if row["kind"] == "task.delivery" and row["resource"]["id"] == task_id
    )
    accepted = client.post(
        f"/api/action-items/{item['action_item_id']}/commands/accept",
        headers=MINA,
        json={"expected_version": item["expected_version"]},
    )
    assert accepted.status_code == 200, accepted.text

    rows = client.get(
        "/api/calendar", headers=JIHO, params={"from": FIRST.isoformat(), "to": LAST.isoformat()}
    ).json()

    assert [row for row in rows if row["kind"] == "task" and row["task_id"] == task_id] == []
    [listed] = [
        item
        for item in client.get("/api/my-work", headers=JIHO, params={"include_closed": "true"}).json()
        if item["task_id"] == task_id
    ]
    assert listed["derived"]["approval"] == "approved"
