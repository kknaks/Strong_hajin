"""답변이 여러 개를 말하면, 그 하나하나가 열리는 정본이어야 한다.

An answer that lists work is a list of resources, not a paragraph to parse. What a delegated turn actually read is
kept as canonical ids and the versions the tools saw, in the order they were read — and every time the conversation
is read back, each reference is asked of the module that owns it. A person who has since lost access to one of them
sees no title, no placeholder and no count.
"""
from uuid import UUID

from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.mcp import McpReportsFacade
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import ConversationTurnRecord, make_session_factory

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    return TestClient(create_app(settings)), settings, database_url


def _delegated_turn(client, database_url, headers, title: str) -> tuple[dict, str]:
    conversation = client.post("/api/conversations", headers=headers, json={"title": title}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**headers, "Idempotency-Key": title},
        json={"body": "오늘 하는 일 알려줘", "context": []},
    ).json()
    with make_session_factory(database_url)() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted["turn_id"])).execution_id
    return conversation, str(execution_id)


def test_what_a_turn_read_becomes_something_each_answer_item_can_open(tmp_path, monkeypatch) -> None:
    client, settings, database_url = _stack(tmp_path)
    first = client.post("/api/tasks", headers=MINA, json={"title": "먼저 읽은 업무"}).json()
    second = client.post("/api/tasks", headers=MINA, json={"title": "다음에 읽은 업무"}).json()
    meeting = client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "organization_id": "scax", "title": "답변이 가리킬 회의",
            "starts_at": "2026-09-10T01:00:00Z", "ends_at": "2026-09-10T02:00:00Z",
            "visibility": "private", "attendee_ids": [],
        },
    ).json()
    conversation, execution_id = _delegated_turn(client, database_url, MINA, "resource-turn")

    monkeypatch.setenv("AX_MCP_CAUSATION_ID", execution_id)
    facade = McpReportsFacade(settings, "mina")
    facade.get_task(first["task_id"])
    facade.get_task(second["task_id"])
    # Reading the same thing twice in one turn is one reference, not two.
    facade.get_task(first["task_id"])
    facade.get_meeting(meeting["meeting_id"])
    monkeypatch.delenv("AX_MCP_CAUSATION_ID", raising=False)

    detail = client.get(f"/api/conversations/{conversation['conversation_id']}", headers=MINA).json()
    references = detail["answer_resources"]
    assert [(row["resource_type"], row["resource_id"]) for row in references] == [
        ("task", first["task_id"]),
        ("task", second["task_id"]),
        ("meeting", meeting["meeting_id"]),
    ]
    assert [row["title"] for row in references] == ["먼저 읽은 업무", "다음에 읽은 업무", "답변이 가리킬 회의"]
    # The version the tool saw is kept, so the reference says what the answer stood on.
    assert references[0]["resource_version"] == first["version"]
    assert all(row["turn_id"] for row in references)
    assert [row["sequence"] for row in references] == sorted(row["sequence"] for row in references)


def test_a_reference_is_asked_of_its_owner_again_every_time_it_is_read(tmp_path, monkeypatch) -> None:
    client, settings, database_url = _stack(tmp_path)
    meeting = client.post(
        "/api/meetings",
        headers=JIHO,
        json={
            "organization_id": "scax", "title": "공유가 끊길 회의",
            "starts_at": "2026-09-10T01:00:00Z", "ends_at": "2026-09-10T02:00:00Z",
            "visibility": "private", "attendee_ids": [],
        },
    ).json()
    shared = client.post(
        f"/api/meetings/{meeting['meeting_id']}/shares",
        headers=JIHO,
        json={"member_id": "mina", "expected_version": meeting["version"]},
    )
    assert shared.status_code in {200, 201}, shared.text
    conversation, execution_id = _delegated_turn(client, database_url, MINA, "revoked-turn")

    monkeypatch.setenv("AX_MCP_CAUSATION_ID", execution_id)
    McpReportsFacade(settings, "mina").get_meeting(meeting["meeting_id"])
    monkeypatch.delenv("AX_MCP_CAUSATION_ID", raising=False)
    before = client.get(f"/api/conversations/{conversation['conversation_id']}", headers=MINA).json()["answer_resources"]
    assert [row["title"] for row in before] == ["공유가 끊길 회의"]

    # The share is taken back. The stored reference is still a row; what it says is asked again.
    current = client.get(f"/api/meetings/{meeting['meeting_id']}", headers=JIHO).json()
    revoked = client.request(
        "DELETE",
        f"/api/meetings/{meeting['meeting_id']}/shares/mina",
        headers=JIHO,
        json={"expected_version": current["version"]},
    )
    assert revoked.status_code in {200, 204}, revoked.text
    after = client.get(f"/api/conversations/{conversation['conversation_id']}", headers=MINA).json()
    # No title, no placeholder, and nothing left to count.
    assert after["answer_resources"] == []
    assert "공유가 끊길 회의" not in str(after)


def test_a_turn_records_nothing_for_a_conversation_that_is_not_its_own(tmp_path, monkeypatch) -> None:
    client, settings, database_url = _stack(tmp_path)
    task = client.post("/api/tasks", headers=MINA, json={"title": "남의 대화"}).json()
    conversation, execution_id = _delegated_turn(client, database_url, MINA, "not-mine")

    monkeypatch.setenv("AX_MCP_CAUSATION_ID", execution_id)
    # 지호 may read his own work, but this execution belongs to 민아's conversation: nothing is written.
    try:
        McpReportsFacade(settings, "jiho").get_task(task["task_id"])
    except Exception:
        pass
    monkeypatch.delenv("AX_MCP_CAUSATION_ID", raising=False)
    assert client.get(f"/api/conversations/{conversation['conversation_id']}", headers=MINA).json()["answer_resources"] == []
