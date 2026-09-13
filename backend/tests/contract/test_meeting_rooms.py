"""사옥 회의실 예약 연동 (SCAX-WP-007).

예약 시스템(THE CONNECT)은 **대역**이다 — 실제 호출은 조사(조회만)와 코디의 실물 확인에만 있다.
여기서 못박는 것: 회의실을 안 고르면 부르지 않는다 · 잡히면 저장되고 장소가 선다 ·
잡히지 않아도 **회의는 남는다** · 사내와 사외가 다른 자리로 간다 · 계정은 어디에도 새지 않는다.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging

from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.meetings.rooms import (
    MeetingRoom,
    ReservationBooked,
    RoomGatewayUnavailable,
    RoomUnavailable,
)

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}

SECRET_EMAIL = "booker@tdl.example"
SECRET_PASSWORD = "s3cret-do-not-log"


# --------------------------------------------------------------------- 대역


class FakeRoomGateway:
    """예약 시스템 대역. 무엇을 받았는지 그대로 들고 있어 payload 분기를 그 자리에서 본다."""

    #: 정원이 셋으로 갈린 방들 — 「정원을 감당하는 가장 작은 방」 규칙이 실제로 갈라지는 모양이다.
    ALL_ROOMS = (
        MeetingRoom(room_id=2, name="회의실 2 (4인)", capacity=4),
        MeetingRoom(room_id=3, name="회의실 3 (6인)", capacity=6),
        MeetingRoom(room_id=5, name="회의실 5 (12인)", capacity=12),
    )

    def __init__(self) -> None:
        self.created: list = []
        self.updated: list[dict] = []
        self.cancelled: list[str] = []
        self.error: Exception | None = None
        self.next_id = "9001"
        #: 그 시간에 이미 찬 방 번호. `available` 과 `create` 가 같은 사실을 읽는다.
        self.busy: set[int] = set()
        self.availability_calls: list[tuple[str, str, str]] = []
        # 두 축을 함께 세운다: 민아는 **이름**으로(데모 이메일이 회사 계정과 다르다),
        # 지호는 **이메일**로 이어진다. 실제 데모가 꼭 이 모양이다.
        self.people = [
            {"name": "민아 (구성원)", "email": "mina@company.example"},
            {"name": "다른 지호", "email": "jiho@scax.example"},
        ]

    def rooms(self) -> list[MeetingRoom]:
        if self.error is not None:
            raise self.error
        return list(self.ALL_ROOMS)

    def available(self, date: str, start: str, end: str) -> list[MeetingRoom]:
        if self.error is not None:
            raise self.error
        self.availability_calls.append((date, start, end))
        return [room for room in self.ALL_ROOMS if room.room_id not in self.busy]

    def members(self) -> list[dict[str, str]]:
        if self.error is not None:
            raise self.error
        return list(self.people)

    def create(self, request) -> ReservationBooked:
        self.created.append(request)
        if self.error is not None:
            raise self.error
        if request.room_id in self.busy:
            free = tuple(room for room in self.ALL_ROOMS if room.room_id not in self.busy)
            raise RoomUnavailable("그 시간에 그 회의실은 이미 예약돼 있습니다", available=free)
        room = next(one for one in self.ALL_ROOMS if one.room_id == request.room_id)
        return ReservationBooked(external_id=self.next_id, room_id=room.room_id, room_name=room.name)

    def update(self, external_id: str, *, date: str, start: str, end: str, room_id: int | None = None) -> None:
        if self.error is not None:
            raise self.error
        self.updated.append({"external_id": external_id, "date": date, "start": start, "end": end, "room_id": room_id})

    def cancel(self, external_id: str) -> None:
        if self.error is not None:
            raise self.error
        self.cancelled.append(external_id)


# --------------------------------------------------------------------- 발판


def _stack(tmp_path, *, gateway: FakeRoomGateway | None = None, configured: bool = True):
    database_url = f"sqlite:///{tmp_path / 'ax_demo.db'}"
    reset_database(database_url)
    settings = Settings(
        RuntimeProfile.TEST,
        database_url,
        room_booking_email=SECRET_EMAIL if configured else "",
        room_booking_password=SECRET_PASSWORD if configured else "",
    )
    app = create_app(settings)
    application = app.state.workflow_application
    room_gateway = gateway if gateway is not None else FakeRoomGateway()
    application._room_gateway = room_gateway
    return TestClient(app), application, room_gateway


def _book(client: TestClient, *, room_id: int | None, externals=("김외부",), attendees=("jiho",), title="예약할 회의"):
    starts = datetime.now(UTC) + timedelta(days=2)
    body = {
        "title": title,
        "starts_at": starts.isoformat().replace("+00:00", "Z"),
        "ends_at": (starts + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "attendee_ids": list(attendees),
        "external_attendees": list(externals),
        "agendas": [{"title": "첫 안건"}],
    }
    if room_id is not None:
        body["room_id"] = room_id
    return client.post("/api/meetings", headers=MINA, json=body)


# --------------------------------------------------------------------- 회의실 목록


def test_the_room_list_comes_from_the_booking_system_rather_than_a_list_we_keep(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    listed = client.get("/api/meetings/rooms", headers=MINA)
    assert listed.status_code == 200, listed.text
    assert listed.json() == [
        {"room_id": 2, "name": "회의실 2 (4인)", "capacity": 4},
        {"room_id": 3, "name": "회의실 3 (6인)", "capacity": 6},
        {"room_id": 5, "name": "회의실 5 (12인)", "capacity": 12},
    ]


def test_without_an_account_there_are_no_rooms_to_offer_rather_than_invented_ones(tmp_path) -> None:
    """계정이 없으면 **빈 목록**이다 — 잡을 수 없는 방을 화면에 세우지 않는다."""
    client, application, _ = _stack(tmp_path, configured=False)
    application._room_gateway = None
    assert client.get("/api/meetings/rooms", headers=MINA).json() == []


def test_asking_with_a_time_returns_only_the_rooms_free_then(tmp_path) -> None:
    """모달이 거절 뒤 회의실 칸만 다시 그리는 자리 (D36-3)."""
    gateway = FakeRoomGateway()
    gateway.busy = {3}
    client, _, _ = _stack(tmp_path, gateway=gateway)

    starts = datetime(2026, 9, 14, 1, 0, tzinfo=UTC)  # 서울 10:00
    listed = client.get(
        "/api/meetings/rooms",
        headers=MINA,
        params={
            "starts_at": starts.isoformat().replace("+00:00", "Z"),
            "ends_at": (starts + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        },
    )
    assert listed.status_code == 200, listed.text
    assert listed.json() == [
        {"room_id": 2, "name": "회의실 2 (4인)", "capacity": 4, "available": True},
        {"room_id": 5, "name": "회의실 5 (12인)", "capacity": 12, "available": True},
    ]
    # 물어본 시간은 **사옥 지역 시각**이다 — UTC 로 물으면 아홉 시간 어긋난 방 상태를 받는다.
    assert gateway.availability_calls == [("2026-09-14", "10:00", "11:00")]


def test_asking_without_a_time_still_returns_the_whole_list(tmp_path) -> None:
    """기존 호출은 그대로다 — 시간을 모르면 가능 여부도 말하지 않는다."""
    client, _, gateway = _stack(tmp_path)
    gateway.busy = {3}
    rows = client.get("/api/meetings/rooms", headers=MINA).json()
    assert [row["room_id"] for row in rows] == [2, 3, 5]
    assert all("available" not in row for row in rows)
    assert gateway.availability_calls == []


# --------------------------------------------------------------------- 예약 시점


def test_a_meeting_without_a_room_never_calls_the_booking_system(tmp_path) -> None:
    """「회의실 선택 안 함」이 기본이다 — 부르지 않은 것은 실패도 아니다."""
    client, _, gateway = _stack(tmp_path)
    made = _book(client, room_id=None)
    assert made.status_code == 201, made.text
    assert gateway.created == []
    assert made.json()["meeting"]["room_reservation"] is None
    assert made.json()["meeting"]["location"] is None


def test_choosing_a_room_books_it_and_the_place_becomes_that_room(tmp_path) -> None:
    client, application, gateway = _stack(tmp_path)
    made = _book(client, room_id=3)
    assert made.status_code == 201, made.text
    head = made.json()["meeting"]

    assert head["location"] == "회의실 3 (6인)"
    assert head["room_reservation"] == {
        "status": "booked",
        "room_name": "회의실 3 (6인)",
        "reason": None,
        # 고른 방이 그대로 잡혔다 — 대체가 아니다.
        "replaced": False,
        "requested_room_name": None,
    }
    # **외부 식별자도 계정도 응답에 없다** — 화면이 알 일이 아니다 (§2 노출).
    assert "external_id" not in head["room_reservation"] and "room_id" not in head["room_reservation"]
    assert gateway.created and gateway.created[0].room_id == 3

    # 원장에는 남는다 — 바꾸고 거두려면 그 번호가 있어야 한다.
    with application._session_factory() as session:
        from uuid import UUID

        from ax_workspace.platform.persistence import MeetingRecord

        stored = session.get(MeetingRecord, UUID(head["meeting_id"])).room_reservation
    assert stored["external_id"] == "9001" and stored["room_id"] == 3


def test_when_no_room_can_be_replaced_the_meeting_is_not_made_at_all(tmp_path) -> None:
    """대체할 방도 없으면 **회의를 만들지 않는다** (D36-2) — 방 없는 회의가 원장에 남지 않는다."""
    gateway = FakeRoomGateway()
    gateway.busy = {2, 3, 5}  # 그 시간에 전부 찼다
    client, _, _ = _stack(tmp_path, gateway=gateway)

    refused = _book(client, room_id=3)
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail["code"] == "room_unavailable"
    assert detail["message"] and detail["available_rooms"] == []
    # 회의 원장에 아무것도 남지 않았다.
    assert client.get("/api/meetings", headers=MINA).json()["upcoming"] == []


def test_the_refusal_carries_the_rooms_the_person_can_still_choose(tmp_path) -> None:
    """거절이 막다른 길이면 안 된다 — 그 시간에 **가능한 방**을 함께 낸다."""
    gateway = FakeRoomGateway()
    gateway.busy = {2, 3}  # 12인실만 남았는데 인원이 열셋이라 대체가 안 된다
    client, _, _ = _stack(tmp_path, gateway=gateway)

    refused = _book(client, room_id=3, externals=[f"손님{index}" for index in range(11)])
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail["code"] == "room_unavailable"
    assert detail["available_rooms"] == [{"room_id": 5, "name": "회의실 5 (12인)", "capacity": 12}]


def test_a_booking_system_that_cannot_be_reached_refuses_with_no_rooms_to_offer(tmp_path) -> None:
    """닿지도 못한 실패는 가능한 방을 알 길이 없다 — 지어내지 않고 빈 목록이다."""
    gateway = FakeRoomGateway()
    gateway.error = RoomGatewayUnavailable("예약 시스템에 닿지 못했습니다")
    client, _, _ = _stack(tmp_path, gateway=gateway)

    refused = _book(client, room_id=5)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "reservation_unavailable"
    assert refused.json()["detail"]["available_rooms"] == []
    assert client.get("/api/meetings", headers=MINA).json()["upcoming"] == []


def test_without_an_account_the_meeting_is_refused_rather_than_left_roomless(tmp_path) -> None:
    client, application, _ = _stack(tmp_path, configured=False)
    application._room_gateway = None

    refused = _book(client, room_id=3)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "reservation_auth_failed"
    assert refused.json()["detail"]["available_rooms"] == []


def test_a_seat_taken_for_a_meeting_that_could_not_be_saved_is_given_back(tmp_path) -> None:
    """예약이 성공한 뒤 회의 저장이 깨지면 **잡아 둔 자리를 되돌린다** — 주인 없는 예약을 남기지 않는다."""
    client, _, gateway = _stack(tmp_path)
    starts = datetime.now(UTC) + timedelta(days=2)
    broken = client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "title": "저장되지 못할 회의",
            # 끝이 시작보다 앞선다 — 예약 입력은 통과하고 회의 저장에서 걸린다.
            "starts_at": starts.isoformat().replace("+00:00", "Z"),
            "ends_at": (starts - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "attendee_ids": ["jiho"],
            "external_attendees": [],
            "agendas": [],
            "room_id": 3,
        },
    )
    assert broken.status_code == 422, broken.text
    # 자리는 잡혔다가 거둬졌다.
    assert gateway.created and gateway.cancelled == ["9001"]
    assert client.get("/api/meetings", headers=MINA).json()["upcoming"] == []


# --------------------------------------------------------------------- 참석자 → payload


# --------------------------------------------------------------------- 수정 · 취소 동기화


def test_moving_the_meeting_moves_the_seat_it_holds(tmp_path) -> None:
    client, _, gateway = _stack(tmp_path)
    meeting_id = _book(client, room_id=3).json()["meeting"]["meeting_id"]

    moved = datetime.now(UTC) + timedelta(days=5)
    answer = client.patch(
        f"/api/meetings/{meeting_id}",
        headers=MINA,
        json={
            "starts_at": moved.isoformat().replace("+00:00", "Z"),
            "ends_at": (moved + timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
        },
    )
    assert answer.status_code == 200, answer.text
    [update] = gateway.updated
    assert update["external_id"] == "9001" and update["room_id"] == 3
    # 자리는 그대로 잡혀 있다.
    assert answer.json()["meeting"]["room_reservation"]["status"] == "booked"


def test_editing_only_the_title_does_not_touch_the_booking(tmp_path) -> None:
    """시간이 그대로면 옮길 자리가 없다 — 예약 시스템을 괜히 부르지 않는다."""
    client, _, gateway = _stack(tmp_path)
    meeting_id = _book(client, room_id=3).json()["meeting"]["meeting_id"]
    client.patch(f"/api/meetings/{meeting_id}", headers=MINA, json={"title": "이름만 고친다"})
    assert gateway.updated == []


def test_cancelling_the_meeting_gives_the_seat_back(tmp_path) -> None:
    client, _, gateway = _stack(tmp_path)
    meeting_id = _book(client, room_id=3).json()["meeting"]["meeting_id"]

    assert client.delete(f"/api/meetings/{meeting_id}", headers=MINA).status_code == 204
    assert gateway.cancelled == ["9001"]
    head = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["meeting"]
    assert head["room_reservation"] == {
        "status": "cancelled",
        "room_name": "회의실 3 (6인)",
        "reason": None,
        "replaced": False,
        "requested_room_name": None,
    }
    assert head["location"] is None


def test_a_seat_that_cannot_be_given_back_does_not_stop_the_cancellation(tmp_path) -> None:
    """거두지 못해도 회의 취소는 선다 — 어긋난 사실만 남는다."""
    client, _, gateway = _stack(tmp_path)
    meeting_id = _book(client, room_id=3).json()["meeting"]["meeting_id"]
    gateway.error = RoomGatewayUnavailable("예약 시스템에 닿지 못했습니다")

    assert client.delete(f"/api/meetings/{meeting_id}", headers=MINA).status_code == 204
    head = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["meeting"]
    assert head["status"] == "cancelled"
    assert head["room_reservation"]["status"] == "booked"
    assert head["room_reservation"]["reason"] == "reservation_unavailable"


def test_quick_start_never_books_a_room(tmp_path) -> None:
    """바로 시작은 값을 묻지 않는다 — 고르지 않은 방을 잡을 수도 없다."""
    client, _, gateway = _stack(tmp_path)
    started = client.post("/api/meetings/quick-start", headers=MINA, json={})
    assert started.status_code == 201
    assert gateway.created == []
    assert started.json()["meeting"]["room_reservation"] is None


# --------------------------------------------------------------------- 계정은 새지 않는다


def test_the_booking_account_never_reaches_the_response_or_the_log(tmp_path, caplog) -> None:
    """계정과 비밀번호는 응답에도 로그에도 남지 않는다 — 실패한 회차에도."""
    gateway = FakeRoomGateway()
    gateway.error = RoomGatewayUnavailable("예약 시스템에 닿지 못했습니다")
    client, _, _ = _stack(tmp_path, gateway=gateway)

    # 성공한 회차도 함께 본다 — 새는 자리는 실패 쪽에만 있는 것이 아니다.
    other = tmp_path / "again"
    other.mkdir()
    healthy, _, _ = _stack(other)

    with caplog.at_level(logging.DEBUG):
        refused = _book(client, room_id=3)
        rooms = client.get("/api/meetings/rooms", headers=MINA)
        made = _book(healthy, room_id=3)
        detail = healthy.get(f"/api/meetings/{made.json()['meeting']['meeting_id']}", headers=MINA)

    assert refused.status_code == 409 and made.status_code == 201
    spoken = refused.text + rooms.text + made.text + detail.text + caplog.text
    assert SECRET_PASSWORD not in spoken
    assert SECRET_EMAIL not in spoken
