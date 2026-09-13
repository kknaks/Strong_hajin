"""HTTP, MCP and confirmed creation consume the same current meeting reservation."""
import asyncio

import pytest

from ax_workspace.entrypoints.mcp import McpReportsFacade, _create_bound_persona_server
from ax_workspace.modules.meetings.commands import MeetingReservationInput
from test_mcp_checklist import _delegated_turn
from test_unified_commands import _stack


@pytest.mark.parametrize("route", ["http", "mcp", "confirm"])
def test_meeting_creation_uses_the_same_schedule_and_normalized_content(tmp_path, monkeypatch, route):
    client, application = _stack(tmp_path)
    headers = {"X-Demo-Persona": "mina"}
    facade = McpReportsFacade(application._settings, "mina")
    values = {
        "title": "  검토 회의  ",
        "purpose": "   ",
        "starts_at": "2026-09-30T15:00:00+09:00",
        "ends_at": "2026-09-30T16:00:00+09:00",
        "attendee_ids": [" mina ", "mina"],
        "agendas": [{"title": " 확인할 안건 "}],
    }
    if route == "http":
        response = client.post("/api/meetings", headers=headers, json=values)
        assert response.status_code == 201, response.text
        detail = response.json()
    else:
        if route == "confirm":
            _delegated_turn(client, application, headers, "mina", monkeypatch)
        server = _create_bound_persona_server(facade)
        response = asyncio.run(server.call_tool("meeting_create", {"request": values}))
        assert not response.is_error, response
        detail = response.structured_content
        if route == "confirm":
            assert application.my_meetings(facade.principal) == []
            path = f"/api/action-items/{detail['action_id']}"
            item = client.get(path, headers=headers).json()
            draft = {**item["edit_contract"]["values"], "title": "최종 회의"}
            payload = {
                "expected_version": item["expected_version"],
                "base_submission_version": item["submission_version"],
                "draft": draft,
            }
            response = client.post(path + "/commands/confirm", headers=headers, json=payload)
            assert response.status_code == 200, response.text
            detail = response.json()["execution_result"]

    meeting = detail["meeting"]
    assert meeting["title"] == ("최종 회의" if route == "confirm" else "검토 회의")
    assert meeting["purpose"] is None
    assert meeting["starts_at"] == "2026-09-30T06:00:00+00:00"
    assert [person["member_id"] for person in meeting["attendees"]] == ["mina"]
    assert detail["agendas"][0]["title"] == "확인할 안건"
    server = _create_bound_persona_server(facade)
    fetched = asyncio.run(server.call_tool("meeting_get", {"meeting_id": meeting["meeting_id"]}))
    assert fetched.structured_content == client.get(
        f"/api/meetings/{meeting['meeting_id']}", headers=headers
    ).json()


def test_the_meeting_editor_contract_names_exactly_the_reservation_fields(tmp_path, monkeypatch):
    """The editor a person sees is the reservation contract itself.

    A client builds its confirm draft from these values, so a field the contract stops naming would be
    silently dropped and a field it never named would be refused by `extra='forbid'`. Pinning both sides
    to `MeetingReservationInput` is what keeps a card from drifting onto a shape the server cannot take.
    """
    client, application = _stack(tmp_path)
    headers = {"X-Demo-Persona": "mina"}
    facade = McpReportsFacade(application._settings, "mina")
    _delegated_turn(client, application, headers, "mina", monkeypatch)
    server = _create_bound_persona_server(facade)
    proposed = asyncio.run(server.call_tool("meeting_create", {"request": {
        "title": "편집 계약 확인",
        "starts_at": "2026-09-30T15:00:00+09:00",
        "ends_at": "2026-09-30T16:00:00+09:00",
    }}))
    assert not proposed.is_error, proposed

    item = client.get(f"/api/action-items/{proposed.structured_content['action_id']}", headers=headers).json()
    contract = item["edit_contract"]
    reservation = set(MeetingReservationInput.model_fields)
    assert contract["editor"] == "meeting"
    assert set(contract["values"]) == reservation
    assert {field["id"] for field in contract["fields"]} == reservation
    # The retired `meeting.create` shape must not reappear through the editor.
    assert reservation.isdisjoint({"organization_id", "description", "visibility", "include_initial_note"})

@pytest.mark.parametrize("delegated", [False, True])
def test_mcp_cannot_create_or_propose_a_meeting_with_an_oversized_title(tmp_path, monkeypatch, delegated):
    client, application = _stack(tmp_path)
    headers = {"X-Demo-Persona": "mina"}
    if delegated:
        _delegated_turn(client, application, headers, "mina", monkeypatch)
    facade = McpReportsFacade(application._settings, "mina")
    server = _create_bound_persona_server(facade)
    with pytest.raises(Exception):
        asyncio.run(
            server.call_tool(
                "meeting_create",
                {
                    "request": {
                        "title": "가" * 301,
                        "starts_at": "2026-09-30T06:00:00Z",
                        "ends_at": "2026-09-30T07:00:00Z",
                    }
                },
            )
        )
    assert application.my_meetings(facade.principal) == []
    assert client.get("/api/actions", headers=headers).json() == []
