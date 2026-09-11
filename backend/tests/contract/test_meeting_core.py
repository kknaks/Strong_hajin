"""Meeting core contracts: identity, authorized projection, and immutable notes."""
from __future__ import annotations

from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database


def _client(tmp_path) -> TestClient:
    database_url = f"sqlite:///{tmp_path / 'ax_demo.db'}"
    reset_database(database_url)
    return TestClient(create_app(Settings(RuntimeProfile.TEST, database_url)))


def _create_private_meeting(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/meetings",
        headers={"X-Demo-Persona": "mina"},
        json={
            "organization_id": "scax",
            "title": "비공개 제품 회의",
            "starts_at": "2026-09-10T01:00:00Z",
            "ends_at": "2026-09-10T02:00:00Z",
            "visibility": "private",
            "attendee_ids": ["jiho"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_private_meeting_is_busy_only_until_attendee_or_share_relationship_allows_detail(tmp_path) -> None:
    client = _client(tmp_path)
    meeting = _create_private_meeting(client)
    meeting_id = meeting["meeting_id"]

    attendee = client.get(f"/api/meetings/{meeting_id}", headers={"X-Demo-Persona": "jiho"})
    assert attendee.status_code == 200
    assert attendee.json()["title"] == "비공개 제품 회의"
    assert attendee.json()["attendees"] == [{"member_id": "jiho", "display_name": "지호 (팀장)"}]

    concealed = client.get("/api/meetings", headers={"X-Demo-Persona": "sora"})
    assert concealed.status_code == 200
    assert concealed.json() == [
        {
            "kind": "busy",
            "starts_at": "2026-09-10T01:00:00+00:00",
            "ends_at": "2026-09-10T02:00:00+00:00",
        }
    ]
    assert client.get(f"/api/meetings/{meeting_id}", headers={"X-Demo-Persona": "sora"}).status_code == 404

    shared = client.post(
        f"/api/meetings/{meeting_id}/shares",
        headers={"X-Demo-Persona": "mina"},
        json={"member_id": "sora", "expected_version": meeting["version"]},
    )
    assert shared.status_code == 200, shared.text
    assert shared.json()["version"] == meeting["version"] + 1
    assert client.get(f"/api/meetings/{meeting_id}", headers={"X-Demo-Persona": "sora"}).json()["title"] == "비공개 제품 회의"

    revoked = client.request(
        "DELETE",
        f"/api/meetings/{meeting_id}/shares/sora",
        headers={"X-Demo-Persona": "mina"},
        json={"expected_version": shared.json()["version"]},
    )
    assert revoked.status_code == 200, revoked.text
    assert client.get(f"/api/meetings/{meeting_id}", headers={"X-Demo-Persona": "sora"}).status_code == 404


def test_meeting_note_appends_immutable_versions_and_rejects_stale_write(tmp_path) -> None:
    client = _client(tmp_path)
    meeting = _create_private_meeting(client)
    meeting_id = meeting["meeting_id"]
    headers = {"X-Demo-Persona": "mina"}

    created = client.post(
        f"/api/meetings/{meeting_id}/note",
        headers=headers,
        json={"body": "안건: 권한 모델 확인"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["version"] == 1
    assert created.json()["body"] == "안건: 권한 모델 확인"

    saved = client.patch(
        f"/api/meetings/{meeting_id}/note",
        headers=headers,
        json={"expected_version": 1, "body": "안건: 권한 모델 확인\n결정: private busy projection"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2

    stale = client.patch(
        f"/api/meetings/{meeting_id}/note",
        headers=headers,
        json={"expected_version": 1, "body": "뒤늦은 덮어쓰기"},
    )
    assert stale.status_code == 422

    detail = client.get(f"/api/meetings/{meeting_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["note"]["version"] == 2
    assert [version["body"] for version in detail.json()["note"]["versions"]] == [
        "안건: 권한 모델 확인",
        "안건: 권한 모델 확인\n결정: private busy projection",
    ]


def test_the_meeting_tools_actually_answer_for_the_persona_they_are_bound_to(tmp_path) -> None:
    """도구 목록에 있다는 것과 실제로 답한다는 것은 다르다.

    Discovery alone proved nothing: `meeting_list` was calling a method that does not exist and failed every time it
    was used. This calls both meeting tools the way a delegated turn does, and checks the answer is that persona's.
    """
    from ax_workspace.entrypoints.mcp import McpReportsFacade

    client = _client(tmp_path)
    database_url = client.app.state.workflow_application._settings.database_url
    settings = Settings(RuntimeProfile.TEST, database_url)
    mine = client.post(
        "/api/meetings",
        headers={"X-Demo-Persona": "mina"},
        json={
            "organization_id": "scax", "title": "도구가 답해야 할 회의",
            "starts_at": "2026-09-10T01:00:00Z", "ends_at": "2026-09-10T02:00:00Z",
            "visibility": "private", "attendee_ids": [],
        },
    ).json()

    facade = McpReportsFacade(settings, "mina")
    listed = facade.list_meetings()
    assert any(row.get("meeting_id") == mine["meeting_id"] for row in listed if row.get("kind") == "meeting")
    assert facade.get_meeting(mine["meeting_id"])["title"] == "도구가 답해야 할 회의"

    # Someone who may not read it gets a busy block at most, and never its title.
    other = McpReportsFacade(settings, "jiho").list_meetings()
    assert all(row.get("title") != "도구가 답해야 할 회의" for row in other)


def test_meeting_list_defaults_to_my_participation_and_can_explicitly_expand_to_visible_calendar(tmp_path) -> None:
    """`내 회의` must not turn an administrator's read scope into personal attendance."""
    from ax_workspace.entrypoints.mcp import McpReportsFacade

    client = _client(tmp_path)
    settings = client.app.state.workflow_application._settings
    visible_only = client.post(
        "/api/meetings",
        headers={"X-Demo-Persona": "mina"},
        json={
            "organization_id": "scax", "title": "대표에게 보이지만 대표 회의는 아님",
            "starts_at": "2026-09-09T01:00:00Z", "ends_at": "2026-09-09T02:00:00Z",
            "visibility": "private", "attendee_ids": [],
        },
    ).json()
    mine = client.post(
        "/api/meetings",
        headers={"X-Demo-Persona": "mina"},
        json={
            "organization_id": "scax", "title": "대표가 참석하는 회의",
            "starts_at": "2026-09-09T03:00:00Z", "ends_at": "2026-09-09T04:00:00Z",
            "visibility": "private", "attendee_ids": ["yuna"],
        },
    ).json()

    facade = McpReportsFacade(settings, "yuna")
    personal = facade.list_meetings()
    assert [row["meeting_id"] for row in personal] == [mine["meeting_id"]]

    visible = facade.list_meetings(include_visible=True)
    assert {row["meeting_id"] for row in visible if row.get("kind") == "meeting"} >= {
        mine["meeting_id"], visible_only["meeting_id"],
    }


def test_delegated_meeting_create_proposes_the_same_editable_action_instead_of_mutating(tmp_path, monkeypatch) -> None:
    from uuid import UUID

    from ax_workspace.entrypoints.mcp import McpReportsFacade
    from ax_workspace.platform.persistence import ConversationTurnRecord, make_session_factory

    client = _client(tmp_path)
    application = client.app.state.workflow_application
    settings = application._settings
    conversation = client.post("/api/conversations", headers={"X-Demo-Persona": "mina"}, json={"title": "회의 제안"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={"X-Demo-Persona": "mina", "Idempotency-Key": "delegated-meeting-create"},
        json={"body": "제품 회의를 잡아줘", "context": []},
    )
    assert accepted.status_code == 202, accepted.text
    with make_session_factory(settings.database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"])).execution_id

    monkeypatch.setenv("AX_MCP_CAUSATION_ID", str(execution_id))
    facade = McpReportsFacade(settings, "mina")
    proposal = facade.create_meeting(
        organization_id="scax",
        title="MCP 제품 회의",
        description="출시 범위와 담당자를 확인합니다.",
        starts_at="2026-09-14T01:00:00Z",
        ends_at="2026-09-14T02:00:00Z",
        visibility="private",
        attendee_ids=["jiho"],
    )
    assert proposal["action_type"] == "meeting.create" and proposal["state"] == "pending"
    assert client.get("/api/meetings", headers={"X-Demo-Persona": "mina"}).json() == []

    monkeypatch.delenv("AX_MCP_CAUSATION_ID")
    [item] = [row for row in facade.pending_action_items() if row["action_item_id"] == proposal["action_id"]]
    receipt = facade.run_action_command(
        item["action_item_id"],
        "confirm",
        expected_version=item["expected_version"],
        base_submission_version=item["submission_version"],
    )
    assert receipt["derived_meeting_id"]
    detail = facade.get_meeting(receipt["derived_meeting_id"])
    assert detail["title"] == "MCP 제품 회의"
    assert detail["description"] == "출시 범위와 담당자를 확인합니다."


def test_delegated_meeting_create_uses_one_hour_before_an_end_only_request(tmp_path, monkeypatch) -> None:
    from uuid import UUID

    from ax_workspace.entrypoints.mcp import McpReportsFacade
    from ax_workspace.platform.persistence import ConversationTurnRecord, make_session_factory

    client = _client(tmp_path)
    application = client.app.state.workflow_application
    settings = application._settings
    conversation = client.post(
        "/api/conversations", headers={"X-Demo-Persona": "mina"}, json={"title": "종료 시각 회의 제안"}
    ).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={"X-Demo-Persona": "mina", "Idempotency-Key": "end-only-meeting-create"},
        json={"body": "오늘 12시까지 팀장님 포함해서 회의를 만들어줘", "context": []},
    )
    assert accepted.status_code == 202, accepted.text
    with make_session_factory(settings.database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"])).execution_id

    monkeypatch.setenv("AX_MCP_CAUSATION_ID", str(execution_id))
    facade = McpReportsFacade(settings, "mina")
    proposal = facade.create_meeting(
        organization_id=None,
        title="팀장님과 주간 회의",
        starts_at=None,
        ends_at="2026-09-11T12:00:00+09:00",
        visibility="private",
        attendee_ids=[],
    )

    assert proposal["action_type"] == "meeting.create" and proposal["state"] == "pending"
    assert client.get("/api/meetings", headers={"X-Demo-Persona": "mina"}).json() == []
    item = next(row for row in facade.pending_action_items() if row["action_item_id"] == proposal["action_id"])
    assert item["edit_contract"]["values"]["starts_at"] == "2026-09-11T02:00:00+00:00"
    assert item["edit_contract"]["values"]["ends_at"] == "2026-09-11T03:00:00+00:00"
    assert item["edit_contract"]["values"]["organization_id"] == "product"
    assert item["edit_contract"]["values"]["attendee_ids"] == []


def test_direct_meeting_create_uses_one_hour_after_a_start_only_request(tmp_path) -> None:
    from ax_workspace.entrypoints.mcp import McpReportsFacade

    client = _client(tmp_path)
    settings = client.app.state.workflow_application._settings
    meeting = McpReportsFacade(settings, "mina").create_meeting(
        organization_id="scax",
        title="한 시간 기본 회의",
        starts_at="2026-09-11T02:00:00Z",
        ends_at=None,
        visibility="private",
        attendee_ids=[],
    )

    detail = client.get(f"/api/meetings/{meeting['meeting_id']}", headers={"X-Demo-Persona": "mina"}).json()
    assert detail["starts_at"] == "2026-09-11T02:00:00+00:00"
    assert detail["ends_at"] == "2026-09-11T03:00:00+00:00"


def test_meeting_create_does_not_invent_both_missing_time_boundaries(tmp_path) -> None:
    import pytest

    from ax_workspace.entrypoints.mcp import McpReportsFacade

    client = _client(tmp_path)
    settings = client.app.state.workflow_application._settings
    with pytest.raises(ValueError, match="meeting start or end time is required"):
        McpReportsFacade(settings, "mina").create_meeting(
            organization_id="scax",
            title="시간이 없는 회의",
            starts_at=None,
            ends_at=None,
            visibility="private",
            attendee_ids=[],
        )


def test_direct_mcp_meeting_note_does_not_claim_conversation_provenance(tmp_path) -> None:
    from ax_workspace.entrypoints.mcp import McpReportsFacade

    client = _client(tmp_path)
    settings = client.app.state.workflow_application._settings
    meeting = McpReportsFacade(settings, "mina").create_meeting(
        organization_id="scax",
        title="직접 만든 회의",
        starts_at="2026-09-16T01:00:00Z",
        ends_at="2026-09-16T02:00:00Z",
        visibility="private",
        attendee_ids=[],
        initial_note_body="대화 Turn 없이 만든 회의록",
    )
    detail = client.get(f"/api/meetings/{meeting['meeting_id']}", headers={"X-Demo-Persona": "mina"}).json()
    assert detail["note"]["source_status"] is None
    assert detail["note"]["versions"][0]["source_evidence"] == []
