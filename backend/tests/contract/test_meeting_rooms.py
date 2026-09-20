"""사옥 회의실 예약 연동 (SCAX-WP-007).

예약 시스템(THE CONNECT)은 **대역**이다 — 실제 호출은 조사(조회만)와 코디의 실물 확인에만 있다.
여기서 못박는 것: 회의실을 안 고르면 부르지 않는다 · 잡히면 저장되고 장소가 선다 ·
잡히지 않아도 **회의는 남는다** · 사내와 사외가 다른 자리로 간다 · 계정은 어디에도 새지 않는다.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import logging
from threading import Event, Lock, Thread
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.actions.domain import ActionError as ActionCenterError
from ax_workspace.modules.ax_execution.actions import ActionApplication
from ax_workspace.modules.meetings.rooms import (
    MeetingRoom,
    ReservationBooked,
    RoomGatewayUnavailable,
    RoomOutcomeUnknown,
    RoomUnavailable,
)
from ax_workspace.modules.meetings.commands import MeetingReservationInput
from ax_workspace.modules.work.action_materials import ActionMaterialDraftApplication, ActionMaterialError
from ax_workspace.platform.actions import POST_COMMIT_RECEIPT_PENDING
from ax_workspace.platform.persistence import (
    ActionItemRecord,
    ConversationTurnRecord,
    MeetingRoomCreationAttemptRecord,
)
from ax_workspace.platform.the_connect import TheConnectGateway

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


def test_the_connect_transport_failure_is_an_unknown_mutation_outcome() -> None:
    gateway = TheConnectGateway(
        base_url="https://tdl.invalid",
        email="booker@example.com",
        password="secret",
        company_id=1,
        notify=False,
        timeout_seconds=0.01,
    )
    gateway._authenticated = True

    class BrokenOpener:
        def open(self, request, *, timeout):
            raise TimeoutError

    gateway._opener = BrokenOpener()
    with pytest.raises(RoomOutcomeUnknown):
        gateway.update("reservation-1", date="2026-09-20", start="10:00", end="11:00", room_id=3)


def test_the_connect_transport_failure_on_a_read_is_not_an_unknown_mutation() -> None:
    gateway = TheConnectGateway(
        base_url="https://tdl.invalid",
        email="booker@example.com",
        password="secret",
        company_id=1,
        notify=False,
        timeout_seconds=0.01,
    )
    gateway._authenticated = True

    class BrokenOpener:
        def open(self, request, *, timeout):
            raise TimeoutError

    gateway._opener = BrokenOpener()
    with pytest.raises(RoomGatewayUnavailable):
        gateway.rooms()


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
    return client.post(
        "/api/meetings",
        headers={**MINA, "Idempotency-Key": f"meeting-room-test-{uuid4()}"},
        json=body,
    )


def _room_action_confirmation(client, application, *, message_key: str, title: str):
    starts = datetime.now(UTC) + timedelta(days=2)
    conversation = client.post("/api/conversations", headers=MINA, json={"title": "회의 생성"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": message_key},
        json={"body": "회의실을 예약해줘", "context": []},
    ).json()
    with application._session_factory() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id
    proposal = application.propose_action(
        application.authenticated_principal("mina"),
        execution_id,
        "meeting.reservation.create",
        "회의 생성 확인",
        {
            "title": title,
            "starts_at": starts.isoformat().replace("+00:00", "Z"),
            "ends_at": (starts + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "room_id": 3,
        },
    )
    path = f"/api/action-items/{proposal['action_id']}"
    item = client.get(path, headers=MINA).json()
    return path, {
        "expected_version": item["expected_version"],
        "base_submission_version": item["submission_version"],
        "draft": item["edit_contract"]["values"],
    }


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


@pytest.mark.no_auto_idempotency_key
def test_room_creation_requires_a_durable_caller_key_before_the_provider_is_called(tmp_path) -> None:
    client, _, gateway = _stack(tmp_path)
    starts = datetime.now(UTC) + timedelta(days=2)
    response = client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "title": "식별자 없는 회의",
            "starts_at": starts.isoformat().replace("+00:00", "Z"),
            "ends_at": (starts + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "room_id": 3,
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "reservation_idempotency_required"
    assert gateway.created == []


def test_mcp_room_creation_declares_and_enforces_its_retry_key(tmp_path) -> None:
    _, application, gateway = _stack(tmp_path)
    facade = McpReportsFacade(application._settings, "mina")
    facade._application._room_gateway = gateway
    server = _create_bound_persona_server(facade)
    tool = next(tool for tool in asyncio.run(server.list_tools()) if tool.name == "meeting_create")
    request_schema = tool.input_schema["$defs"]["McpMeetingReservationInput"]
    assert request_schema["if"]["required"] == ["room_id"]
    assert request_schema["then"]["required"] == ["idempotency_key"]
    assert "idempotency_key" in tool.description

    starts = datetime.now(UTC) + timedelta(days=2)
    request = {
        "title": "MCP 회의실 예약",
        "starts_at": starts.isoformat().replace("+00:00", "Z"),
        "ends_at": (starts + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "room_id": 3,
    }
    with pytest.raises(Exception, match="idempotency_key"):
        asyncio.run(server.call_tool("meeting_create", {"request": request}))
    assert gateway.created == []

    arguments = {"request": {**request, "idempotency_key": "mcp-room-create-1"}}
    first = asyncio.run(server.call_tool("meeting_create", arguments))
    replay = asyncio.run(server.call_tool("meeting_create", arguments))

    assert not first.is_error and not replay.is_error
    assert replay.structured_content == first.structured_content
    assert len(gateway.created) == 1


def test_unknown_room_creation_outcome_is_persisted_and_same_key_never_posts_again(tmp_path) -> None:
    class AppliedThenDisconnectedGateway(FakeRoomGateway):
        def create(self, request) -> ReservationBooked:
            self.created.append(request)
            raise RoomOutcomeUnknown("provider applied the create but its response was lost")

    gateway = AppliedThenDisconnectedGateway()
    client, _, _ = _stack(tmp_path, gateway=gateway)
    starts = datetime.now(UTC) + timedelta(days=2)
    headers = {**MINA, "Idempotency-Key": "lost-room-create-response"}
    body = {
        "title": "결과 확인 회의",
        "starts_at": starts.isoformat().replace("+00:00", "Z"),
        "ends_at": (starts + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "room_id": 3,
    }

    first = client.post("/api/meetings", headers=headers, json=body)
    replay = client.post("/api/meetings", headers=headers, json=body)

    assert first.status_code == 201, first.text
    assert replay.status_code == 201, replay.text
    assert replay.json() == first.json()
    reservation = first.json()["meeting"]["room_reservation"]
    assert reservation["status"] == "needs_verification"
    assert reservation["reason"] == "reservation_needs_verification"
    assert len(gateway.created) == 1


def test_a_pending_room_creation_attempt_is_never_automatically_replayed(tmp_path) -> None:
    client, application, gateway = _stack(tmp_path)
    starts = datetime.now(UTC) + timedelta(days=2)
    key = "crashed-room-create"
    body = {
        "title": "중단된 생성 확인",
        "starts_at": starts.isoformat().replace("+00:00", "Z"),
        "ends_at": (starts + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "room_id": 3,
    }
    request = MeetingReservationInput.model_validate(body)
    now = datetime.now(UTC)
    with application._session_factory() as session:
        session.add(
            MeetingRoomCreationAttemptRecord(
                owner_id="mina",
                request_key=key,
                payload_fingerprint=application._room_creation_fingerprint(request),
                status="pending",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    recovered = client.post(
        "/api/meetings", headers={**MINA, "Idempotency-Key": key}, json=body
    )

    assert recovered.status_code == 201, recovered.text
    reservation = recovered.json()["meeting"]["room_reservation"]
    assert reservation["status"] == "needs_verification"
    assert reservation["reason"] == "reservation_needs_verification"
    assert gateway.created == []


def test_one_room_creation_key_cannot_be_reused_for_different_content(tmp_path) -> None:
    client, _, gateway = _stack(tmp_path)
    starts = datetime.now(UTC) + timedelta(days=2)
    headers = {**MINA, "Idempotency-Key": "one-logical-room-submit"}
    body = {
        "title": "원래 회의",
        "starts_at": starts.isoformat().replace("+00:00", "Z"),
        "ends_at": (starts + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "room_id": 3,
    }
    assert client.post("/api/meetings", headers=headers, json=body).status_code == 201

    changed = client.post("/api/meetings", headers=headers, json={**body, "title": "다른 회의"})

    assert changed.status_code == 409, changed.text
    assert changed.json()["detail"]["code"] == "reservation_idempotency_conflict"
    assert len(gateway.created) == 1


def test_confirmed_unknown_room_creation_is_a_replayable_action_receipt(tmp_path) -> None:
    class AppliedThenDisconnectedGateway(FakeRoomGateway):
        def create(self, request) -> ReservationBooked:
            self.created.append(request)
            raise RoomOutcomeUnknown("provider applied the create but its response was lost")

    gateway = AppliedThenDisconnectedGateway()
    client, application, _ = _stack(tmp_path, gateway=gateway)
    starts = datetime.now(UTC) + timedelta(days=2)
    conversation = client.post("/api/conversations", headers=MINA, json={"title": "회의 생성"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": "unknown-room-create-action"},
        json={"body": "회의실을 예약해줘", "context": []},
    ).json()
    with application._session_factory() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id
    proposal = application.propose_action(
        application.authenticated_principal("mina"),
        execution_id,
        "meeting.reservation.create",
        "회의 생성 확인",
        {
            "title": "승인으로 만드는 회의",
            "starts_at": starts.isoformat().replace("+00:00", "Z"),
            "ends_at": (starts + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "room_id": 3,
        },
    )
    path = f"/api/action-items/{proposal['action_id']}"
    item = client.get(path, headers=MINA).json()
    body = {
        "expected_version": item["expected_version"],
        "base_submission_version": item["submission_version"],
        "draft": item["edit_contract"]["values"],
    }

    approved = client.post(path + "/commands/confirm", headers=MINA, json=body)
    replay = client.post(path + "/commands/confirm", headers=MINA, json=body)

    assert approved.status_code == 200, approved.text
    assert replay.status_code == 200, replay.text
    receipt = approved.json()["execution_result"]
    assert receipt["meeting"]["room_reservation"]["status"] == "needs_verification"
    assert replay.json()["execution_result"] == receipt
    assert len(gateway.created) == 1


def test_confirm_rejects_an_unknown_material_before_booking_the_room(tmp_path) -> None:
    client, application, gateway = _stack(tmp_path)
    starts = datetime.now(UTC) + timedelta(days=2)
    conversation = client.post("/api/conversations", headers=MINA, json={"title": "회의 생성"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": "invalid-material-room-action"},
        json={"body": "회의실을 예약해줘", "context": []},
    ).json()
    with application._session_factory() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id
    proposal = application.propose_action(
        application.authenticated_principal("mina"),
        execution_id,
        "meeting.reservation.create",
        "회의 생성 확인",
        {
            "title": "첨부 검증 회의",
            "starts_at": starts.isoformat().replace("+00:00", "Z"),
            "ends_at": (starts + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "room_id": 3,
        },
    )
    path = f"/api/action-items/{proposal['action_id']}"
    item = client.get(path, headers=MINA).json()

    refused = client.post(
        path + "/commands/confirm",
        headers=MINA,
        json={
            "expected_version": item["expected_version"],
            "base_submission_version": item["submission_version"],
            "draft": item["edit_contract"]["values"],
            "attachment_draft_ids": ["00000000-0000-0000-0000-000000000099"],
        },
    )

    assert refused.status_code == 404, refused.text
    assert gateway.created == []
    assert client.get(path, headers=MINA).json()["status"] == "awaiting_review"
    with application._session_factory() as session:
        assert session.query(MeetingRoomCreationAttemptRecord).count() == 0


def test_failure_immediately_after_booking_already_owns_the_rollback_compensation(
    tmp_path, monkeypatch
) -> None:
    client, application, gateway = _stack(tmp_path)
    path, body = _room_action_confirmation(
        client,
        application,
        message_key="early-rollback-room-action",
        title="빠른 보상 확인 회의",
    )
    def fail_after_booking(*args, **kwargs):
        raise ActionCenterError("injected failure after booking")

    monkeypatch.setattr(ActionApplication, "execute_confirmed", fail_after_booking)

    failed = client.post(path + "/commands/confirm", headers=MINA, json=body)

    assert failed.status_code == 422, failed.text
    assert len(gateway.created) == 1 and gateway.cancelled == ["9001"]
    with application._session_factory() as session:
        [attempt] = session.query(MeetingRoomCreationAttemptRecord).all()
        assert attempt.status == "compensated" and attempt.meeting_id is None


def test_action_rollback_cancels_a_known_booking_and_allows_a_safe_retry(
    tmp_path, monkeypatch
) -> None:
    client, application, gateway = _stack(tmp_path)
    starts = datetime.now(UTC) + timedelta(days=2)
    conversation = client.post("/api/conversations", headers=MINA, json={"title": "회의 생성"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": "rollback-room-action"},
        json={"body": "회의실을 예약해줘", "context": []},
    ).json()
    with application._session_factory() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id
    proposal = application.propose_action(
        application.authenticated_principal("mina"),
        execution_id,
        "meeting.reservation.create",
        "회의 생성 확인",
        {
            "title": "보상 확인 회의",
            "starts_at": starts.isoformat().replace("+00:00", "Z"),
            "ends_at": (starts + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "room_id": 3,
        },
    )
    path = f"/api/action-items/{proposal['action_id']}"
    staged = client.post(
        path + "/material-drafts/links",
        headers=MINA,
        json={"url": "https://example.com/agenda", "label": "안건 자료"},
    ).json()
    item = client.get(path, headers=MINA).json()
    body = {
        "expected_version": item["expected_version"],
        "base_submission_version": item["submission_version"],
        "draft": item["edit_contract"]["values"],
        "attachment_draft_ids": [staged["material_draft_id"]],
    }
    original_claim = ActionMaterialDraftApplication.claim

    def fail_after_booking(*args, **kwargs):
        raise ActionMaterialError("injected claim failure")

    monkeypatch.setattr(ActionMaterialDraftApplication, "claim", fail_after_booking)
    failed = client.post(path + "/commands/confirm", headers=MINA, json=body)

    assert failed.status_code == 422, failed.text
    assert len(gateway.created) == 1
    assert gateway.cancelled == ["9001"]
    with application._session_factory() as session:
        [attempt] = session.query(MeetingRoomCreationAttemptRecord).all()
        assert attempt.status == "compensated" and attempt.meeting_id is None

    monkeypatch.setattr(ActionMaterialDraftApplication, "claim", original_claim)
    retried = client.post(path + "/commands/confirm", headers=MINA, json=body)

    assert retried.status_code == 200, retried.text
    assert len(gateway.created) == 2
    assert len(application.my_meetings(application.authenticated_principal("mina"))) == 1


def test_crash_after_compensation_cancel_never_restores_a_booked_receipt(
    tmp_path, monkeypatch
) -> None:
    client, application, gateway = _stack(tmp_path)
    starts = datetime.now(UTC) + timedelta(days=2)
    conversation = client.post("/api/conversations", headers=MINA, json={"title": "회의 생성"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": "crashed-compensation-room-action"},
        json={"body": "회의실을 예약해줘", "context": []},
    ).json()
    with application._session_factory() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id
    proposal = application.propose_action(
        application.authenticated_principal("mina"),
        execution_id,
        "meeting.reservation.create",
        "회의 생성 확인",
        {
            "title": "보상 중단 확인 회의",
            "starts_at": starts.isoformat().replace("+00:00", "Z"),
            "ends_at": (starts + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "room_id": 3,
        },
    )
    path = f"/api/action-items/{proposal['action_id']}"
    staged = client.post(
        path + "/material-drafts/links",
        headers=MINA,
        json={"url": "https://example.com/agenda", "label": "안건 자료"},
    ).json()
    item = client.get(path, headers=MINA).json()
    body = {
        "expected_version": item["expected_version"],
        "base_submission_version": item["submission_version"],
        "draft": item["edit_contract"]["values"],
        "attachment_draft_ids": [staged["material_draft_id"]],
    }
    original_claim = ActionMaterialDraftApplication.claim
    original_persist = application._persist_room_creation_compensation

    def fail_after_booking(*args, **kwargs):
        raise ActionMaterialError("injected claim failure")

    def crash_after_cancel(owner_id, request_key, *, status, reservation, failure_reason):
        if status == "compensated":
            raise RuntimeError("process stopped after provider cancel")
        return original_persist(
            owner_id,
            request_key,
            status=status,
            reservation=reservation,
            failure_reason=failure_reason,
        )

    monkeypatch.setattr(ActionMaterialDraftApplication, "claim", fail_after_booking)
    monkeypatch.setattr(application, "_persist_room_creation_compensation", crash_after_cancel)
    failed = client.post(path + "/commands/confirm", headers=MINA, json=body)

    assert failed.status_code == 422, failed.text
    assert len(gateway.created) == 1 and gateway.cancelled == ["9001"]
    with application._session_factory() as session:
        [attempt] = session.query(MeetingRoomCreationAttemptRecord).all()
        assert attempt.status == "needs_verification"
        assert attempt.reservation["status"] == "needs_verification"

    monkeypatch.setattr(ActionMaterialDraftApplication, "claim", original_claim)
    monkeypatch.setattr(application, "_persist_room_creation_compensation", original_persist)
    retried = client.post(path + "/commands/confirm", headers=MINA, json=body)

    assert retried.status_code == 200, retried.text
    assert len(gateway.created) == 1
    assert retried.json()["execution_result"]["meeting"]["room_reservation"]["status"] == "needs_verification"


def test_uncertain_compensation_cannot_be_hidden_by_rejecting_the_action(
    tmp_path, monkeypatch
) -> None:
    class CancelResponseLostGateway(FakeRoomGateway):
        def cancel(self, external_id: str) -> None:
            self.cancelled.append(external_id)
            raise RoomOutcomeUnknown("provider may have applied the cancellation")

    gateway = CancelResponseLostGateway()
    client, application, _ = _stack(tmp_path, gateway=gateway)
    path, body = _room_action_confirmation(
        client,
        application,
        message_key="uncertain-compensation-room-action",
        title="불확실 보상 확인 회의",
    )
    staged = client.post(
        path + "/material-drafts/links",
        headers=MINA,
        json={"url": "https://example.com/agenda", "label": "안건 자료"},
    ).json()
    body["attachment_draft_ids"] = [staged["material_draft_id"]]
    body["draft"] = {**body["draft"], "title": "실패한 최종 수정 회의"}
    original_claim = ActionMaterialDraftApplication.claim

    def fail_after_booking(*args, **kwargs):
        raise ActionMaterialError("injected claim failure")

    monkeypatch.setattr(ActionMaterialDraftApplication, "claim", fail_after_booking)
    failed = client.post(path + "/commands/confirm", headers=MINA, json=body)

    assert failed.status_code == 422, failed.text
    assert len(gateway.created) == 1 and gateway.cancelled == ["9001"]
    with application._session_factory() as session:
        [attempt] = session.query(MeetingRoomCreationAttemptRecord).all()
        assert attempt.status == "needs_verification" and attempt.meeting_id is None
        assert attempt.request_payload["title"] == "실패한 최종 수정 회의"
        assert attempt.request_payload["attachment_draft_ids"] == [
            staged["material_draft_id"]
        ]

    restarted_http = create_app(application._settings)
    application = restarted_http.state.workflow_application
    application._room_gateway = gateway
    client = TestClient(restarted_http)
    recovered = client.get(path, headers=MINA).json()
    assert recovered["edit_contract"]["values"]["title"] == "실패한 최종 수정 회의"
    assert recovered["current_question"] == "이전에 시도한 회의실 예약을 확인 필요 회의로 기록하세요"
    assert [command["id"] for command in recovered["allowed_commands"]] == ["confirm"]

    rejected = client.post(
        path + "/commands/reject",
        headers=MINA,
        json={"expected_version": body["expected_version"]},
    )
    assert rejected.status_code == 422, rejected.text
    assert client.get(path, headers=MINA).json()["status"] == "awaiting_review"

    monkeypatch.setattr(ActionMaterialDraftApplication, "claim", original_claim)
    changed = client.post(
        path + "/commands/confirm",
        headers=MINA,
        json={**body, "draft": {**body["draft"], "title": "또 다른 회의"}},
    )
    assert changed.status_code == 422, changed.text
    assert "이전 회의실 예약 결과" in changed.text
    assert len(gateway.created) == 1
    assert client.get(path, headers=MINA).json()["status"] == "awaiting_review"

    other_staged = client.post(
        path + "/material-drafts/links",
        headers=MINA,
        json={"url": "https://example.com/other", "label": "다른 자료"},
    ).json()
    changed_attachment = client.post(
        path + "/commands/confirm",
        headers=MINA,
        json={
            **body,
            "attachment_draft_ids": [other_staged["material_draft_id"]],
        },
    )
    assert changed_attachment.status_code == 422, changed_attachment.text
    assert "이전 회의실 예약 결과" in changed_attachment.text
    assert len(gateway.created) == 1

    confirmed = client.post(
        path + "/commands/confirm",
        headers=MINA,
        json={
            "expected_version": recovered["expected_version"],
            "base_submission_version": recovered["submission_version"],
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    assert len(gateway.created) == 1
    result = confirmed.json()
    assert result["execution_result"]["meeting"]["title"] == "실패한 최종 수정 회의"
    assert result["execution_result"]["meeting"]["room_reservation"]["status"] == "needs_verification"
    assert len(result["material_results"]) == 1
    assert result["material_results"][0]["material_draft_id"] == staged[
        "material_draft_id"
    ]
    assert result["material_results"][0]["url"] == "https://example.com/agenda"
    detail = client.get(path, headers=MINA).json()
    assert next(
        draft for draft in detail["material_drafts"]
        if draft["material_draft_id"] == staged["material_draft_id"]
    )["state"] == "claimed"


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


def test_an_invalid_meeting_is_rejected_before_a_room_is_requested(tmp_path) -> None:
    """로컬 생성 규칙을 통과하지 못한 요청은 provider에 나가지 않는다."""
    client, _, gateway = _stack(tmp_path)
    starts = datetime.now(UTC) + timedelta(days=2)
    broken = client.post(
        "/api/meetings",
        headers={**MINA, "Idempotency-Key": "invalid-local-meeting"},
        json={
            "title": "저장되지 못할 회의",
            # 끝이 시작보다 앞선다 — local Meeting 규칙이 provider보다 먼저 거른다.
            "starts_at": starts.isoformat().replace("+00:00", "Z"),
            "ends_at": (starts - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "attendee_ids": ["jiho"],
            "external_attendees": [],
            "agendas": [],
            "room_id": 3,
        },
    )
    assert broken.status_code == 422, broken.text
    # 로컬 규칙이 provider 호출보다 먼저 선다.
    assert gateway.created == [] and gateway.cancelled == []
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


def test_confirmed_time_change_receipt_includes_the_room_sync_failure_and_replays_it(tmp_path) -> None:
    client, application, gateway = _stack(tmp_path)
    meeting_id = _book(client, room_id=3).json()["meeting"]["meeting_id"]
    starts = datetime.now(UTC) + timedelta(days=4)
    changes = {
        "starts_at": starts.isoformat().replace("+00:00", "Z"),
        "ends_at": (starts + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
    }
    conversation = client.post("/api/conversations", headers=MINA, json={"title": "회의 시간 변경"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": "room-sync-receipt"},
        json={"body": "시간을 바꿔줘", "context": []},
    ).json()
    with application._session_factory() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id
    proposal = application.propose_action(
        application.authenticated_principal("mina"),
        execution_id,
        "meeting.info.update",
        "회의 수정 확인",
        {"meeting_id": meeting_id, "changes": changes},
    )
    item = client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()
    gateway.error = RoomGatewayUnavailable("예약 시스템에 닿지 못했습니다")
    url = f"/api/action-items/{item['action_item_id'].upper()}/commands/approve"
    body = {"expected_version": item["expected_version"]}

    confirmed = client.post(url, headers=MINA, json=body)
    assert confirmed.status_code == 200, confirmed.text
    receipt = confirmed.json()["execution_result"]
    current = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()
    assert receipt == current
    assert receipt["meeting"]["room_reservation"]["reason"] == "reservation_unavailable"
    changed = client.patch(
        f"/api/meetings/{meeting_id}",
        headers=MINA,
        json={"title": "승인 뒤 별도로 바뀐 제목"},
    )
    assert changed.status_code == 200, changed.text
    replay = client.post(url, headers=MINA, json=body)
    assert replay.status_code == 200
    assert replay.json()["execution_result"] == receipt
    assert client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["meeting"]["title"] == "승인 뒤 별도로 바뀐 제목"


def test_unknown_room_update_outcome_stays_for_verification_without_automatic_retry(tmp_path) -> None:
    class AppliedThenDisconnectedGateway(FakeRoomGateway):
        def update(self, external_id: str, **fields) -> None:
            self.updated.append({"external_id": external_id, **fields})
            raise RoomOutcomeUnknown("provider applied the update but its response was lost")

    gateway = AppliedThenDisconnectedGateway()
    client, application, _ = _stack(tmp_path, gateway=gateway)
    meeting_id = _book(client, room_id=3).json()["meeting"]["meeting_id"]
    starts = datetime.now(UTC) + timedelta(days=4)
    conversation = client.post("/api/conversations", headers=MINA, json={"title": "회의 시간 변경"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": "room-sync-unknown-outcome"},
        json={"body": "시간을 바꿔줘", "context": []},
    ).json()
    with application._session_factory() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id
    proposal = application.propose_action(
        application.authenticated_principal("mina"),
        execution_id,
        "meeting.info.update",
        "회의 수정 확인",
        {
            "meeting_id": meeting_id,
            "changes": {
                "starts_at": starts.isoformat().replace("+00:00", "Z"),
                "ends_at": (starts + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            },
        },
    )
    item = client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()
    url = f"/api/action-items/{item['action_item_id']}/commands/approve"
    body = {"expected_version": item["expected_version"]}

    approved = client.post(url, headers=MINA, json=body)
    replayed = client.post(url, headers=MINA, json=body)

    assert approved.status_code == 200, approved.text
    assert replayed.status_code == 200, replayed.text
    receipt = approved.json()["execution_result"]
    reservation = receipt["meeting"]["room_reservation"]
    assert reservation["status"] == "needs_verification"
    assert reservation["reason"] == "reservation_needs_verification"
    assert replayed.json()["execution_result"] == receipt
    assert len(gateway.updated) == 1
    with application._session_factory() as session:
        stored = session.get(ActionItemRecord, UUID(item["action_item_id"])).result
    assert POST_COMMIT_RECEIPT_PENDING not in stored


def test_room_sync_patches_only_room_fields_into_the_frozen_action_receipt(tmp_path) -> None:
    client, application, gateway = _stack(tmp_path)
    meeting_id = _book(client, room_id=3, title="승인한 제목").json()["meeting"]["meeting_id"]
    starts = datetime.now(UTC) + timedelta(days=4)
    conversation = client.post("/api/conversations", headers=MINA, json={"title": "회의 시간 변경"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": "room-sync-frozen-receipt"},
        json={"body": "시간을 바꿔줘", "context": []},
    ).json()
    with application._session_factory() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id
    proposal = application.propose_action(
        application.authenticated_principal("mina"),
        execution_id,
        "meeting.info.update",
        "회의 수정 확인",
        {
            "meeting_id": meeting_id,
            "changes": {
                "starts_at": starts.isoformat().replace("+00:00", "Z"),
                "ends_at": (starts + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            },
        },
    )
    original_update = gateway.update

    def mutate_during_room_sync(*args, **kwargs):
        original_update(*args, **kwargs)
        changed = client.patch(
            f"/api/meetings/{meeting_id}",
            headers=MINA,
            json={
                "title": "후속 동기화 중 별도로 바뀐 제목",
                "location": "동기화와 무관한 별도 장소",
            },
        )
        assert changed.status_code == 200, changed.text

    gateway.update = mutate_during_room_sync
    item = client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()
    approved = client.post(
        f"/api/action-items/{item['action_item_id']}/commands/approve",
        headers=MINA,
        json={"expected_version": item["expected_version"]},
    )
    assert approved.status_code == 200, approved.text
    receipt = approved.json()["execution_result"]["meeting"]
    assert receipt["title"] == "승인한 제목"
    assert receipt["location"] == "회의실 3 (6인)"
    current = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["meeting"]
    assert current["title"] == "후속 동기화 중 별도로 바뀐 제목"
    assert current["location"] == "동기화와 무관한 별도 장소"


def test_expired_replay_waits_for_the_live_room_sync_before_recovery(tmp_path) -> None:
    class BlockingGateway(FakeRoomGateway):
        def __init__(self) -> None:
            super().__init__()
            self.entered = Event()
            self.second_entered = Event()
            self.release = Event()
            self.calls = 0
            self.calls_lock = Lock()

        def update(self, *args, **kwargs) -> None:
            with self.calls_lock:
                self.calls += 1
                call = self.calls
            if call == 1:
                self.entered.set()
                assert self.release.wait(5), "the test did not release the room update"
            else:
                self.second_entered.set()
            raise RoomGatewayUnavailable("예약 시스템에 닿지 못했습니다")

    gateway = BlockingGateway()
    client, application, _ = _stack(tmp_path, gateway=gateway)
    meeting_id = _book(client, room_id=3).json()["meeting"]["meeting_id"]
    starts = datetime.now(UTC) + timedelta(days=4)
    conversation = client.post("/api/conversations", headers=MINA, json={"title": "회의 시간 변경"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": "room-sync-concurrent-replay"},
        json={"body": "시간을 바꿔줘", "context": []},
    ).json()
    with application._session_factory() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id
    proposal = application.propose_action(
        application.authenticated_principal("mina"),
        execution_id,
        "meeting.info.update",
        "회의 수정 확인",
        {
            "meeting_id": meeting_id,
            "changes": {
                "starts_at": starts.isoformat().replace("+00:00", "Z"),
                "ends_at": (starts + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            },
        },
    )
    item = client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()
    url = f"/api/action-items/{item['action_item_id']}/commands/approve"
    body = {"expected_version": item["expected_version"]}
    responses: dict[str, object] = {}
    first_done = Event()
    replay_done = Event()

    def approve() -> None:
        responses["first"] = client.post(url, headers=MINA, json=body)
        first_done.set()

    def replay() -> None:
        with TestClient(client.app) as replay_client:
            responses["replay"] = replay_client.post(url, headers=MINA, json=body)
        replay_done.set()

    first_thread = Thread(target=approve)
    first_thread.start()
    assert gateway.entered.wait(2), "the first approval did not enter room synchronization"
    with application._session_factory() as session:
        action = session.get(ActionItemRecord, UUID(item["action_item_id"]))
        action.result = {
            **action.result,
            POST_COMMIT_RECEIPT_PENDING: {
                **action.result[POST_COMMIT_RECEIPT_PENDING],
                "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            },
        }
        session.commit()
    with TestClient(client.app) as observer:
        detail = observer.get(f"/api/action-items/{item['action_item_id']}", headers=MINA).json()
        legacy = next(
            action
            for action in observer.get("/api/actions", headers=MINA).json()
            if action["action_id"] == item["action_item_id"]
        )
    assert "execution_result" not in detail
    assert legacy["state"] == "approved" and legacy["result"] is None
    replay_thread = Thread(target=replay)
    replay_thread.start()
    finished_before_final_receipt = replay_done.wait(0.25)
    second_call_before_release = gateway.second_entered.is_set()
    gateway.release.set()
    assert first_done.wait(5) and replay_done.wait(5)
    first_thread.join()
    replay_thread.join()

    assert not finished_before_final_receipt
    assert not second_call_before_release
    assert gateway.second_entered.is_set()
    first = responses["first"]
    replayed = responses["replay"]
    assert first.status_code == 200, first.text
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["execution_result"] == first.json()["execution_result"]
    assert first.json()["execution_result"]["meeting"]["room_reservation"]["reason"] == "reservation_unavailable"


def test_replay_recovers_an_expired_room_receipt_lease(tmp_path) -> None:
    client, application, gateway = _stack(tmp_path)
    meeting_id = _book(client, room_id=3).json()["meeting"]["meeting_id"]
    starts = datetime.now(UTC) + timedelta(days=4)
    conversation = client.post("/api/conversations", headers=MINA, json={"title": "회의 시간 변경"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": "room-sync-expired-lease"},
        json={"body": "시간을 바꿔줘", "context": []},
    ).json()
    with application._session_factory() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id
    proposal = application.propose_action(
        application.authenticated_principal("mina"),
        execution_id,
        "meeting.info.update",
        "회의 수정 확인",
        {
            "meeting_id": meeting_id,
            "changes": {
                "starts_at": starts.isoformat().replace("+00:00", "Z"),
                "ends_at": (starts + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            },
        },
    )
    item = client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()
    url = f"/api/action-items/{item['action_item_id']}/commands/approve"
    body = {"expected_version": item["expected_version"]}
    assert client.post(url, headers=MINA, json=body).status_code == 200
    with application._session_factory() as session:
        action = session.get(ActionItemRecord, UUID(item["action_item_id"]))
        action.result = {
            **action.result,
            POST_COMMIT_RECEIPT_PENDING: {
                "owner": "terminated-worker",
                "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            },
        }
        session.commit()
    gateway.error = RoomGatewayUnavailable("예약 시스템에 닿지 못했습니다")

    recovered = client.post(url, headers=MINA, json=body)

    assert recovered.status_code == 200, recovered.text
    receipt = recovered.json()["execution_result"]
    assert receipt["meeting"]["room_reservation"]["reason"] == "reservation_unavailable"
    with application._session_factory() as session:
        stored = session.get(ActionItemRecord, UUID(item["action_item_id"])).result
    assert POST_COMMIT_RECEIPT_PENDING not in stored
    assert stored == receipt

    with application._session_factory() as session:
        action = session.get(ActionItemRecord, UUID(item["action_item_id"]))
        action.result = {
            **action.result,
            POST_COMMIT_RECEIPT_PENDING: {
                "owner": "second-terminated-worker",
                "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            },
        }
        session.commit()

    def transient_recovery_failure(_action_item_id):
        raise RuntimeError("temporary recovery outage")

    application._recover_meeting_receipt_patch = transient_recovery_failure
    try:
        application._wait_for_post_commit_meeting_receipt(
            item["action_item_id"], timeout_seconds=0.05
        )
    except ActionCenterError as error:
        assert "still being finalized" in str(error)
    else:  # pragma: no cover - the assertion above is the intended path
        raise AssertionError("a failed recovery must leave the receipt pending")
    with application._session_factory() as session:
        pending = session.get(ActionItemRecord, UUID(item["action_item_id"])).result
    assert POST_COMMIT_RECEIPT_PENDING in pending


def test_attendee_removal_approval_returns_its_receipt_after_access_is_removed(tmp_path) -> None:
    client, application, _ = _stack(tmp_path)
    starts = datetime.now(UTC) + timedelta(days=2)
    created = client.post(
        "/api/meetings",
        headers=JIHO,
        json={
            "title": "참석자 변경",
            "starts_at": starts.isoformat().replace("+00:00", "Z"),
            "ends_at": (starts + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "attendee_ids": ["mina"],
        },
    ).json()["meeting"]
    conversation = client.post("/api/conversations", headers=MINA, json={"title": "참석 종료"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": "remove-self-receipt"},
        json={"body": "나를 참석자에서 빼줘", "context": []},
    ).json()
    with application._session_factory() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id
    proposal = application.propose_action(
        application.authenticated_principal("mina"),
        execution_id,
        "meeting.info.update",
        "참석자 변경 확인",
        {"meeting_id": created["meeting_id"], "changes": {"attendee_ids": ["jiho"]}},
    )
    item = client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()
    approved = client.post(
        f"/api/action-items/{item['action_item_id']}/commands/approve",
        headers=MINA,
        json={"expected_version": item["expected_version"]},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["execution_result"]["meeting"]["meeting_id"] == created["meeting_id"]
    assert client.get(f"/api/meetings/{created['meeting_id']}", headers=MINA).status_code == 404


def test_legacy_action_decision_runs_the_same_room_sync_hooks(tmp_path) -> None:
    client, application, gateway = _stack(tmp_path)
    meeting_id = _book(client, room_id=3).json()["meeting"]["meeting_id"]
    starts = datetime.now(UTC) + timedelta(days=6)
    conversation = client.post("/api/conversations", headers=MINA, json={"title": "기존 승인 경로"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": "legacy-room-sync"},
        json={"body": "시간을 바꿔줘", "context": []},
    ).json()
    with application._session_factory() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id
    proposal = application.propose_action(
        application.authenticated_principal("mina"),
        execution_id,
        "meeting.info.update",
        "회의 수정 확인",
        {
            "meeting_id": meeting_id,
            "changes": {
                "starts_at": starts.isoformat().replace("+00:00", "Z"),
                "ends_at": (starts + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            },
        },
    )
    approved = client.post(
        f"/api/actions/{proposal['action_id']}/decide",
        headers=MINA,
        json={"expected_version": proposal["version"], "decision": "approve"},
    )
    assert approved.status_code == 200, approved.text
    assert len(gateway.updated) == 1


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
