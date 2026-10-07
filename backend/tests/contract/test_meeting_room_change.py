"""회의실 셀렉트 · 회의 수정의 회의실 · AX 회의 수정 카드 (SPEC-010 §4.1 · §4.3 · §2.4 · WORK-012 WP3-BE · WP3 계약 고정 1~4).

예약 시스템(The Connect)은 대역이다(`test_meeting_rooms.FakeRoomGateway`). 실물 방 변경 1회는 코디 E2E 몫이다.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from ax_workspace.modules.meetings.rooms import RoomGatewayUnavailable
from ax_workspace.platform.persistence import ConversationTurnRecord

from test_meeting_rooms import JIHO, MINA, _book, _stack


def _meeting(client, meeting_id: str) -> dict:
    return client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()


def _slot(meeting: dict, *, shift_hours: int = 0) -> dict:
    starts = datetime.fromisoformat(meeting["meeting"]["starts_at"]) + timedelta(hours=shift_hours)
    ends = datetime.fromisoformat(meeting["meeting"]["ends_at"]) + timedelta(hours=shift_hours)
    return {"starts_at": starts.isoformat(), "ends_at": ends.isoformat()}


# ── 목록 — 인자 넷 · 항목 여섯 · 503 (SPEC-010 §4.1 · 계약 고정 4) ─────────────────────────────────────────────────


def test_people_keeps_only_rooms_big_enough(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    rows = client.get("/api/meetings/rooms", headers=MINA, params={"people": 5}).json()
    assert [row["room_id"] for row in rows] == [3, 5]
    assert all(row["available"] and row["unavailable_reason"] is None and row["current"] is False for row in rows)


def test_editing_a_meeting_does_not_count_its_own_reservation_as_busy(tmp_path) -> None:
    client, _, gateway = _stack(tmp_path)
    meeting_id = _book(client, room_id=3).json()["meeting"]["meeting_id"]
    gateway.busy, gateway.occupant = {3}, {3: "9001"}
    params = {**_slot(_meeting(client, meeting_id)), "people": 3, "meeting_id": meeting_id}
    rows = client.get("/api/meetings/rooms", headers=MINA, params=params).json()
    by_id = {row["room_id"]: row for row in rows}
    assert by_id[3] == {"room_id": 3, "name": "회의실 3 (6인)", "capacity": 6, "available": True, "current": True, "unavailable_reason": None}
    assert gateway.ignored[-1] == "9001"


def test_the_current_room_stays_listed_disabled_with_a_reason(tmp_path) -> None:
    client, _, gateway = _stack(tmp_path)
    meeting_id = _book(client, room_id=3).json()["meeting"]["meeting_id"]
    # 다른 예약이 새 시간에 3번을 쥐었다 — 기존 줄은 비활성 + 이유(시간). 다른 쓸 수 없는 방은 목록에 없다.
    gateway.busy, gateway.occupant = {2, 3}, {2: "x", 3: "someone-else"}
    params = {**_slot(_meeting(client, meeting_id), shift_hours=2), "people": 3, "meeting_id": meeting_id}
    rows = client.get("/api/meetings/rooms", headers=MINA, params=params).json()
    assert [row["room_id"] for row in rows] == [3, 5]
    assert rows[0]["current"] is True and rows[0]["available"] is False and rows[0]["unavailable_reason"] == "time_conflict"
    # 정원이 모자라면 이유는 정원이다.
    gateway.busy = set()
    rows = client.get("/api/meetings/rooms", headers=MINA, params={**params, "people": 8}).json()
    current = next(row for row in rows if row["room_id"] == 3)
    assert current["available"] is False and current["unavailable_reason"] == "capacity"


def test_an_unreachable_booking_system_is_503_not_an_empty_list(tmp_path) -> None:
    client, _, gateway = _stack(tmp_path)
    gateway.error = RoomGatewayUnavailable("예약 시스템에 닿지 못했습니다")
    refused = client.get("/api/meetings/rooms", headers=MINA)
    assert refused.status_code == 503, refused.text
    assert refused.json()["detail"]["code"] == "ROOM_SERVICE_UNAVAILABLE"


def test_meeting_id_must_be_a_meeting_the_caller_can_edit(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    theirs = client.post(
        "/api/meetings",
        headers=JIHO,
        json={"title": "남의 회의", "starts_at": "2026-12-01T10:00:00+09:00", "ends_at": "2026-12-01T11:00:00+09:00"},
    ).json()["meeting"]["meeting_id"]
    assert client.get("/api/meetings/rooms", headers=MINA, params={"meeting_id": theirs}).status_code == 404


def test_the_ax_room_tool_follows_the_same_rules(tmp_path) -> None:
    client, application, gateway = _stack(tmp_path)
    facade = McpReportsFacade(application._settings, "mina")
    facade._application._room_gateway = gateway  # MCP 조립도 같은 대역을 쓴다
    server = _create_bound_persona_server(facade)
    result = asyncio.run(server.call_tool("meeting_room_list", {"people": 5}))
    assert not result.is_error, result
    rows = result.structured_content["result"]
    assert [row["room_id"] for row in rows] == [3, 5]


# ── PATCH `room` (SPEC-010 §4.3 · 계약 고정 2·3) ───────────────────────────────────────────────────────────────


def test_choosing_another_room_moves_the_reservation_with_put(tmp_path) -> None:
    client, _, gateway = _stack(tmp_path)
    meeting_id = _book(client, room_id=3).json()["meeting"]["meeting_id"]
    patched = client.patch(f"/api/meetings/{meeting_id}", headers=MINA, json={"room": {"room_id": 5}})
    assert patched.status_code == 200, patched.text
    assert gateway.updated[-1]["room_id"] == 5 and gateway.updated[-1]["external_id"] == "9001"
    meeting = patched.json()["meeting"]
    assert meeting["location"] == "회의실 5 (12인)"
    assert meeting["room_reservation"]["room_name"] == "회의실 5 (12인)" and meeting["room_reservation"]["status"] == "booked"
    assert len(gateway.created) == 1  # 처음 예약 하나뿐 — 새 예약을 또 잡지 않았다


def test_adding_a_room_to_a_meeting_without_one_books_once_behind_the_retry_key(tmp_path) -> None:
    client, _, gateway = _stack(tmp_path)
    meeting_id = _book(client, room_id=None).json()["meeting"]["meeting_id"]
    keyless = client.patch(f"/api/meetings/{meeting_id}", headers=MINA, json={"room": {"room_id": 3}})
    assert keyless.status_code == 422 and keyless.json()["detail"]["code"] == "reservation_idempotency_required"
    headers = {**MINA, "Idempotency-Key": "add-room-1"}
    first = client.patch(f"/api/meetings/{meeting_id}", headers=headers, json={"room": {"room_id": 3}})
    assert first.status_code == 200, first.text
    assert first.json()["meeting"]["room_reservation"]["status"] == "booked"
    assert first.json()["meeting"]["location"] == "회의실 3 (6인)"
    assert len(gateway.created) == 1
    # 같은 키로 다시 와도 예약을 두 번 잡지 않는다(이미 쥔 자리라 이번엔 PUT 이동이다 — 같은 방).
    again = client.patch(f"/api/meetings/{meeting_id}", headers=headers, json={"room": {"room_id": 3}})
    assert again.status_code == 200 and len(gateway.created) == 1


def test_no_room_cancels_the_reservation_and_clears_the_place(tmp_path) -> None:
    client, _, gateway = _stack(tmp_path)
    meeting_id = _book(client, room_id=3).json()["meeting"]["meeting_id"]
    patched = client.patch(f"/api/meetings/{meeting_id}", headers=MINA, json={"room": {"room_id": None}})
    assert patched.status_code == 200, patched.text
    assert gateway.cancelled == ["9001"]
    meeting = patched.json()["meeting"]
    assert meeting["location"] is None and meeting["room_reservation"]["status"] == "cancelled"


def test_a_room_full_at_the_new_time_is_refused_without_changing_the_meeting(tmp_path) -> None:
    client, _, gateway = _stack(tmp_path)
    meeting_id = _book(client, room_id=3).json()["meeting"]["meeting_id"]
    before = _meeting(client, meeting_id)["meeting"]
    gateway.busy, gateway.occupant = {3}, {3: "another-booking"}
    refused = client.patch(f"/api/meetings/{meeting_id}", headers=MINA, json=_slot({"meeting": before}, shift_hours=3))
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail["code"] == "ROOM_BOOKING_REFUSED"
    assert [room["room_id"] for room in detail["available_rooms"]] == [2, 5]
    assert _meeting(client, meeting_id)["meeting"]["starts_at"] == before["starts_at"]  # 회의는 그대로
    assert gateway.updated == []  # 자동 대체하지 않는다(OQ-1004)


def test_more_people_than_the_room_holds_is_refused(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    meeting_id = _book(client, room_id=2).json()["meeting"]["meeting_id"]  # 4인실 · 지금 3명
    refused = client.patch(
        f"/api/meetings/{meeting_id}", headers=MINA, json={"external_attendees": ["김외부", "박외부", "최외부"]}
    )
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "ROOM_BOOKING_REFUSED"


def test_an_unknown_room_is_422_room_not_found(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    meeting_id = _book(client, room_id=3).json()["meeting"]["meeting_id"]
    refused = client.patch(f"/api/meetings/{meeting_id}", headers=MINA, json={"room": {"room_id": 99}})
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "ROOM_NOT_FOUND"


def test_an_unreachable_booking_system_keeps_the_edit_and_reports_the_failure(tmp_path) -> None:
    """저장 때 장애는 지금 동작 그대로(회의는 바뀐다) + 응답이 실패를 싣는다 (OQ-1008 · 계약 고정 3)."""
    client, _, gateway = _stack(tmp_path)
    meeting_id = _book(client, room_id=3).json()["meeting"]["meeting_id"]
    gateway.error = RoomGatewayUnavailable("예약 시스템에 닿지 못했습니다")
    slot = _slot(_meeting(client, meeting_id), shift_hours=1)
    patched = client.patch(f"/api/meetings/{meeting_id}", headers=MINA, json=slot)
    assert patched.status_code == 200, patched.text
    meeting = patched.json()["meeting"]
    assert meeting["starts_at"] == datetime.fromisoformat(slot["starts_at"]).astimezone().isoformat() or meeting["starts_at"]
    assert meeting["room_reservation"]["status"] == "failed"
    assert meeting["room_reservation"]["reason"] == "reservation_unavailable"
    # 자리는 여전히 쥔 것으로 본다 — 다음 수정이 새 예약을 또 잡지 않고 같은 예약을 다시 맞춘다.
    gateway.error = None
    again = client.patch(f"/api/meetings/{meeting_id}", headers=MINA, json=_slot(_meeting(client, meeting_id), shift_hours=1))
    assert again.status_code == 200, again.text
    assert len(gateway.created) == 1 and gateway.updated[-1]["external_id"] == "9001"


# ── AX 회의 수정 카드 (SPEC-010 §2.4 · 계약 고정 1) · 옛 meeting.update ─────────────────────────────────────────


def _proposal(client, application, action_type: str, payload: dict, *, key: str) -> dict:
    conversation = client.post("/api/conversations", headers=MINA, json={"title": "회의 바꾸기"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": key},
        json={"body": "회의를 바꿔줘", "context": []},
    ).json()
    with application._session_factory() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id
    proposal = application.propose_action(
        application.authenticated_principal("mina"), execution_id, action_type, "회의 수정 확인", payload
    )
    return client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()


def _confirm(client, item: dict, draft: dict):
    return client.post(
        f"/api/action-items/{item['action_item_id']}/commands/confirm",
        headers=MINA,
        json={"expected_version": item["expected_version"], "base_submission_version": item["submission_version"], "draft": draft},
    )


def test_the_ax_update_card_has_its_own_editor_contract(tmp_path) -> None:
    client, application, _ = _stack(tmp_path)
    meeting_id = _book(client, room_id=3, title="원래 회의").json()["meeting"]["meeting_id"]
    item = _proposal(
        client, application, "meeting.info.update",
        {"meeting_id": meeting_id, "changes": {"title": "AX 가 고친 제목", "location": "강남역 카페"}},
        key="update-card-contract",
    )
    contract = item["edit_contract"]
    assert contract["editor"] == "meeting_update"
    assert set(contract["values"]) == {
        "meeting_id", "title", "purpose", "starts_at", "ends_at", "attendee_ids", "external_attendees", "room_id", "room_name",
        "proposed_room_id", "proposed_room_name", "room_proposed",
    }
    assert (contract["values"]["proposed_room_id"], contract["values"]["proposed_room_name"]) == (None, None)
    assert contract["values"]["room_proposed"] is False
    assert contract["values"]["title"] == "AX 가 고친 제목"
    assert (contract["values"]["room_id"], contract["values"]["room_name"]) == (3, "회의실 3 (6인)")
    assert {field["id"] for field in contract["fields"]} == {
        "title", "purpose", "starts_at", "ends_at", "attendee_ids", "external_attendees", "room",
    }
    # AX 가 낸 사외 장소 글자는 저장하지 않고 미리보기에도 남기지 않는다(W-r2-6).
    assert all(row["id"] != "location" for row in item["preview"])
    assert [command["id"] for command in item["allowed_commands"]] == ["confirm", "reject"]


def test_confirming_the_card_with_a_room_change_runs_the_same_rules_as_patch(tmp_path) -> None:
    client, application, gateway = _stack(tmp_path)
    meeting_id = _book(client, room_id=3, title="원래 회의").json()["meeting"]["meeting_id"]
    item = _proposal(
        client, application, "meeting.info.update", {"meeting_id": meeting_id, "changes": {"title": "AX 제목"}},
        key="update-card-room",
    )
    confirmed = _confirm(client, item, {"room": {"room_id": 5}, "purpose": "사람이 고친 목적"})
    assert confirmed.status_code == 200, confirmed.text
    meeting = _meeting(client, meeting_id)["meeting"]
    assert meeting["title"] == "AX 제목" and meeting["purpose"] == "사람이 고친 목적"  # 바뀐 칸만 + AX 제안은 남는다
    assert gateway.updated[-1]["room_id"] == 5 and meeting["location"] == "회의실 5 (12인)"


def test_confirming_the_card_into_a_full_room_is_refused_like_patch(tmp_path) -> None:
    client, application, gateway = _stack(tmp_path)
    meeting_id = _book(client, room_id=3).json()["meeting"]["meeting_id"]
    item = _proposal(
        client, application, "meeting.info.update", {"meeting_id": meeting_id, "changes": {"title": "그대로"}},
        key="update-card-refused",
    )
    gateway.busy = {5}
    refused = _confirm(client, item, {"room": {"room_id": 5}})
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "ROOM_BOOKING_REFUSED"
    assert isinstance(refused.json()["detail"]["available_rooms"], list)
    assert _meeting(client, meeting_id)["meeting"]["title"] != "그대로"


def test_the_legacy_meeting_update_rides_the_same_recheck(tmp_path) -> None:
    """옛 `meeting.update` 도 저장 직전 재확인 · 409 를 비켜 가지 않는다 (SPEC-010 §2.4 · §4.3)."""
    client, application, gateway = _stack(tmp_path)
    meeting_id = _book(client, room_id=3).json()["meeting"]["meeting_id"]
    meeting = _meeting(client, meeting_id)
    slot = _slot(meeting, shift_hours=3)
    item = _proposal(
        client, application, "meeting.update",
        {"meeting_id": meeting_id, "expected_version": 1, **slot},
        key=f"legacy-update-{uuid4()}",
    )
    gateway.busy, gateway.occupant = {3}, {3: "another-booking"}
    refused = _confirm(client, item, item["edit_contract"]["values"])
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "ROOM_BOOKING_REFUSED"
    assert _meeting(client, meeting_id)["meeting"]["starts_at"] == meeting["meeting"]["starts_at"]


# ── 검수 F-1 — AX 가 제안한 방 · 사람의 선택이 언제나 이긴다 (계약 고정 5) ────────────────────────────────────────


def _room_proposal(client, application, key: str) -> tuple[str, dict]:
    meeting_id = _book(client, room_id=3, title="원래 회의").json()["meeting"]["meeting_id"]
    item = _proposal(
        client, application, "meeting.info.update",
        {"meeting_id": meeting_id, "changes": {"title": "AX 제목", "room": {"room_id": 5}}},
        key=key,
    )
    return meeting_id, item


def test_the_card_shows_the_room_ax_proposed_beside_the_current_one(tmp_path) -> None:
    client, application, _ = _stack(tmp_path)
    _, item = _room_proposal(client, application, "proposed-room-values")
    values = item["edit_contract"]["values"]
    assert (values["room_id"], values["room_name"]) == (3, "회의실 3 (6인)")
    assert (values["proposed_room_id"], values["proposed_room_name"]) == (5, "회의실 5 (12인)")
    assert values["room_proposed"] is True


def test_keeping_the_existing_room_beats_the_ax_proposal(tmp_path) -> None:
    client, application, gateway = _stack(tmp_path)
    meeting_id, item = _room_proposal(client, application, "proposed-room-keep")
    confirmed = _confirm(client, item, {"room": {"keep": True}})
    assert confirmed.status_code == 200, confirmed.text
    meeting = _meeting(client, meeting_id)["meeting"]
    assert meeting["title"] == "AX 제목"  # 다른 제안 칸은 그대로 선다
    assert gateway.updated == [] and gateway.cancelled == [] and meeting["location"] == "회의실 3 (6인)"


def test_a_draft_without_room_never_applies_the_ax_room(tmp_path) -> None:
    client, application, gateway = _stack(tmp_path)
    meeting_id, item = _room_proposal(client, application, "proposed-room-absent")
    confirmed = _confirm(client, item, {})
    assert confirmed.status_code == 200, confirmed.text
    assert gateway.updated == [] and _meeting(client, meeting_id)["meeting"]["location"] == "회의실 3 (6인)"


def test_confirming_the_proposed_room_as_shown_moves_there(tmp_path) -> None:
    client, application, gateway = _stack(tmp_path)
    meeting_id, item = _room_proposal(client, application, "proposed-room-accept")
    confirmed = _confirm(client, item, {"room": {"room_id": 5}})
    assert confirmed.status_code == 200, confirmed.text
    assert gateway.updated[-1]["room_id"] == 5
    assert _meeting(client, meeting_id)["meeting"]["location"] == "회의실 5 (12인)"


def test_room_keep_cannot_be_mixed_with_a_room_id(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    meeting_id = _book(client, room_id=3).json()["meeting"]["meeting_id"]
    mixed = client.patch(f"/api/meetings/{meeting_id}", headers=MINA, json={"room": {"keep": True, "room_id": 5}})
    assert mixed.status_code == 422
    kept = client.patch(f"/api/meetings/{meeting_id}", headers=MINA, json={"room": {"keep": True}, "title": "제목만"})
    assert kept.status_code == 200 and kept.json()["meeting"]["location"] == "회의실 3 (6인)"


# ── 검수 W-1 — 결과를 모르는 예약(`needs_verification`) ─────────────────────────────────────────────────────────


def _mark_unverified(application, meeting_id: str, *, external_id: str | None) -> None:
    from ax_workspace.modules.meetings.rooms import STATUS_NEEDS_VERIFICATION, RoomReservation

    with application._session_factory() as session:
        application._meetings(session).attach_reservation(
            UUID(meeting_id),
            RoomReservation(
                status=STATUS_NEEDS_VERIFICATION, room_id=3, room_name="회의실 3 (6인)",
                external_id=external_id, reason="reservation_outcome_unknown",
            ),
            location="회의실 3 (6인)",
        )
        session.commit()


def test_an_unconfirmed_new_reservation_blocks_room_touching_edits(tmp_path) -> None:
    """잡혔는지 모르는 새 예약 — 방을 고르면 이중 예약, 시각만 옮기면 Connect 쪽이 옛 시각에 남는다. 확인 뒤에 바꾼다."""
    client, application, gateway = _stack(tmp_path)
    meeting_id = _book(client, room_id=None).json()["meeting"]["meeting_id"]
    _mark_unverified(application, meeting_id, external_id=None)
    headers = {**MINA, "Idempotency-Key": "unverified-add"}
    for body in ({"room": {"room_id": 5}}, _slot(_meeting(client, meeting_id), shift_hours=1), {"external_attendees": ["가", "나"]}):
        refused = client.patch(f"/api/meetings/{meeting_id}", headers=headers, json=body)
        assert refused.status_code == 409, (body, refused.text)
        assert refused.json()["detail"]["code"] == "ROOM_RESERVATION_UNCONFIRMED"
    assert gateway.created == [] and gateway.updated == []
    # 방과 무관한 칸은 막지 않는다.
    assert client.patch(f"/api/meetings/{meeting_id}", headers=MINA, json={"title": "제목만"}).status_code == 200


def test_an_unconfirmed_move_still_holds_its_seat_and_is_reconciled(tmp_path) -> None:
    """결과를 모르는 이동(외부 번호 있음)은 쥔 자리다 — 다음 수정은 같은 예약을 PUT 으로 맞춘다(새 예약 없음)."""
    client, application, gateway = _stack(tmp_path)
    meeting_id = _book(client, room_id=3).json()["meeting"]["meeting_id"]
    _mark_unverified(application, meeting_id, external_id="9001")
    moved = client.patch(f"/api/meetings/{meeting_id}", headers=MINA, json={"room": {"room_id": 5}})
    assert moved.status_code == 200, moved.text
    assert len(gateway.created) == 1 and gateway.updated[-1] == {**gateway.updated[-1], "external_id": "9001", "room_id": 5}
    assert moved.json()["meeting"]["room_reservation"]["status"] == "booked"


# ── 검수 W-2 — AX 확정·옛 `meeting.update` 의 재확인은 액션 트랜잭션 밖에서 ─────────────────────────────────────


def test_the_ax_confirm_rechecks_the_room_outside_the_action_transaction(tmp_path, monkeypatch) -> None:
    client, application, gateway = _stack(tmp_path)
    meeting_id = _book(client, room_id=3).json()["meeting"]["meeting_id"]
    item = _proposal(
        client, application, "meeting.info.update", {"meeting_id": meeting_id, "changes": {"title": "AX 제목"}},
        key="recheck-outside",
    )
    inside: list[bool] = []
    calls: list[bool] = []
    run_once = application._run_action_command_once

    def tracked(*args, **kwargs):
        inside.append(True)
        try:
            return run_once(*args, **kwargs)
        finally:
            inside.pop()

    original_rooms = gateway.rooms

    def rooms():
        calls.append(bool(inside))
        return original_rooms()

    monkeypatch.setattr(application, "_run_action_command_once", tracked)
    monkeypatch.setattr(gateway, "rooms", rooms)
    confirmed = _confirm(client, item, {"room": {"room_id": 5}})
    assert confirmed.status_code == 200, confirmed.text
    assert calls and not any(calls)  # 재확인(목록·가용)은 언제나 트랜잭션 밖
    assert gateway.updated[-1]["room_id"] == 5


def test_the_legacy_update_refusal_also_comes_from_outside_the_transaction(tmp_path, monkeypatch) -> None:
    client, application, gateway = _stack(tmp_path)
    meeting_id = _book(client, room_id=3).json()["meeting"]["meeting_id"]
    meeting = _meeting(client, meeting_id)
    item = _proposal(
        client, application, "meeting.update",
        {"meeting_id": meeting_id, "expected_version": 1, **_slot(meeting, shift_hours=3)},
        key="legacy-outside",
    )
    inside: list[bool] = []
    seen: list[bool] = []
    run_once = application._run_action_command_once

    def tracked(*args, **kwargs):
        inside.append(True)
        try:
            return run_once(*args, **kwargs)
        finally:
            inside.pop()

    original_available = gateway.available

    def available(*args, **kwargs):
        seen.append(bool(inside))
        return original_available(*args, **kwargs)

    monkeypatch.setattr(application, "_run_action_command_once", tracked)
    monkeypatch.setattr(gateway, "available", available)
    gateway.busy, gateway.occupant = {3}, {3: "another-booking"}
    refused = _confirm(client, item, item["edit_contract"]["values"])
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "ROOM_BOOKING_REFUSED"
    assert seen == [False]


# ── 재검수 F-r2-1 — 「제안 없음」 과 「예약 없음 제안」 을 가른다 (계약 고정 6) ──────────────────────────────────


def test_a_time_only_proposal_does_not_propose_a_room(tmp_path) -> None:
    client, application, _ = _stack(tmp_path)
    meeting_id = _book(client, room_id=3).json()["meeting"]["meeting_id"]
    item = _proposal(
        client, application, "meeting.info.update",
        {"meeting_id": meeting_id, "changes": _slot(_meeting(client, meeting_id), shift_hours=1)},
        key="time-only-proposal",
    )
    values = item["edit_contract"]["values"]
    assert values["room_proposed"] is False and values["proposed_room_id"] is None
    assert (values["room_id"], values["room_name"]) == (3, "회의실 3 (6인)")


def test_proposing_no_reservation_is_a_room_proposal(tmp_path) -> None:
    client, application, _ = _stack(tmp_path)
    meeting_id = _book(client, room_id=3).json()["meeting"]["meeting_id"]
    item = _proposal(
        client, application, "meeting.info.update",
        {"meeting_id": meeting_id, "changes": {"room": {"room_id": None}}},
        key="no-room-proposal",
    )
    values = item["edit_contract"]["values"]
    assert values["room_proposed"] is True
    assert (values["proposed_room_id"], values["proposed_room_name"]) == (None, None)


# ── 재검수 W-r2-2 — 예약 시스템에 이미 없는 예약 ─────────────────────────────────────────────────────────────


def test_a_reservation_gone_from_the_booking_system_is_settled_as_no_seat(tmp_path) -> None:
    """결과 모르던 취소가 실제로 반영됐던 예약 — 다음 이동이 「없음」 을 받으면 자리를 비운 것으로 확정한다(PUT 되풀이 없음)."""
    client, application, gateway = _stack(tmp_path)
    meeting_id = _book(client, room_id=3).json()["meeting"]["meeting_id"]
    _mark_unverified(application, meeting_id, external_id="9001")
    gateway.gone = {"9001"}
    first = client.patch(f"/api/meetings/{meeting_id}", headers=MINA, json=_slot(_meeting(client, meeting_id), shift_hours=1))
    assert first.status_code == 200, first.text
    reservation = first.json()["meeting"]["room_reservation"]
    assert reservation["status"] == "failed" and reservation["reason"] == "reservation_gone"
    again = client.patch(f"/api/meetings/{meeting_id}", headers=MINA, json=_slot(_meeting(client, meeting_id), shift_hours=1))
    assert again.status_code == 200 and gateway.updated == []  # 없는 예약을 다시 옮기려 하지 않는다
    # 방을 다시 고르면 새로 잡는다(키 필요 — 생성 울타리).
    rebooked = client.patch(
        f"/api/meetings/{meeting_id}", headers={**MINA, "Idempotency-Key": "rebook-after-gone"}, json={"room": {"room_id": 3}}
    )
    assert rebooked.status_code == 200 and len(gateway.created) == 2


def test_cancelling_a_reservation_that_is_already_gone_counts_as_cancelled(tmp_path) -> None:
    client, application, gateway = _stack(tmp_path)
    meeting_id = _book(client, room_id=3).json()["meeting"]["meeting_id"]
    gateway.gone = {"9001"}
    cancelled = client.patch(f"/api/meetings/{meeting_id}", headers=MINA, json={"room": {"room_id": None}})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["meeting"]["room_reservation"]["status"] == "cancelled"
    assert cancelled.json()["meeting"]["location"] is None


# ── 재검수 W-r2-3 — 실행 직전 예약 상태 대조 ─────────────────────────────────────────────────────────────────


def test_a_seat_that_appeared_after_planning_is_moved_not_booked_twice(tmp_path, monkeypatch) -> None:
    """계획(락 밖) 뒤 복구가 자리를 확정했다 — 실행은 새로 잡지 않고 그 예약을 옮긴다."""
    from ax_workspace.modules.meetings.rooms import STATUS_BOOKED, RoomReservation

    client, application, gateway = _stack(tmp_path)
    meeting_id = _book(client, room_id=None).json()["meeting"]["meeting_id"]
    execute = application._execute_room_plan

    def recovered_in_between(principal, plan):
        with application._session_factory() as session:
            application._meetings(session).attach_reservation(
                UUID(meeting_id),
                RoomReservation(status=STATUS_BOOKED, room_id=2, room_name="회의실 2 (4인)", external_id="recovered-1"),
                location="회의실 2 (4인)",
            )
            session.commit()
        return execute(principal, plan)

    monkeypatch.setattr(application, "_execute_room_plan", recovered_in_between)
    patched = client.patch(
        f"/api/meetings/{meeting_id}", headers={**MINA, "Idempotency-Key": "seat-appeared"}, json={"room": {"room_id": 5}}
    )
    assert patched.status_code == 200, patched.text
    assert gateway.created == []
    assert gateway.updated[-1]["external_id"] == "recovered-1" and gateway.updated[-1]["room_id"] == 5
    assert patched.json()["meeting"]["location"] == "회의실 5 (12인)"
