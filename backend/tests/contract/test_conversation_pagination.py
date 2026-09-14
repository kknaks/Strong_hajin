"""History stays bounded: the list shows only the most recent conversations, one conversation's detail shows
only its most recent messages, and a `before_sequence` cursor pages further back. A paginated window pulls in
only what its own messages point at.
"""
import asyncio

from fastapi.testclient import TestClient

from ax_workspace.bootstrap.conversation_worker import ConversationWorker
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.ax_execution.ai import AiConversationResult
from ax_workspace.platform.conversation_jobs import ConversationJobQueue

MINA = {"X-Demo-Persona": "mina"}


class EchoProvider:
    def converse(self, request, *, sink=None, cancel=None):
        return AiConversationResult(None, None, "답변", [], usage=None)


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(tmp_path / "materials"))
    app = create_app(settings, report_provider=EchoProvider())
    client = TestClient(app)
    application = app.state.workflow_application
    worker = ConversationWorker(
        settings, provider=EchoProvider(),
        queue_factory=lambda session: ConversationJobQueue(application.memory_job_queue, worker_id="test"),
    )
    return client, worker


def _send_and_complete(client, worker, conversation_id: str, body: str, key: str) -> None:
    accepted = client.post(
        f"/api/conversations/{conversation_id}/messages",
        headers={**MINA, "Idempotency-Key": key},
        json={"body": body, "context": []},
    )
    assert accepted.status_code == 202, accepted.text
    assert asyncio.run(worker.run_once()) is True


def test_conversation_list_is_bounded_to_the_most_recent(tmp_path) -> None:
    client, worker = _stack(tmp_path)
    conversation_ids = []
    for i in range(12):
        conversation = client.post("/api/conversations", headers=MINA, json={"title": f"conv-{i}"}).json()
        conversation_ids.append(conversation["conversation_id"])
        _send_and_complete(client, worker, conversation["conversation_id"], f"질문 {i}", f"k-{i}")

    listed = client.get("/api/conversations", headers=MINA).json()
    assert len(listed) == 10
    assert [item["conversation_id"] for item in listed] == list(reversed(conversation_ids[-10:]))

    narrower = client.get("/api/conversations", headers=MINA, params={"limit": 3}).json()
    assert [item["conversation_id"] for item in narrower] == list(reversed(conversation_ids[-3:]))


def test_conversation_detail_windows_messages_and_a_cursor_pages_older_ones(tmp_path) -> None:
    client, worker = _stack(tmp_path)
    conversation_id = client.post("/api/conversations", headers=MINA, json={"title": "long"}).json()["conversation_id"]
    for i in range(30):
        _send_and_complete(client, worker, conversation_id, f"질문 {i}", f"k-{i}")
    # 30 exchanges = 60 messages (user + assistant each), 30 turns.

    first_page = client.get(f"/api/conversations/{conversation_id}", headers=MINA).json()
    assert len(first_page["messages"]) == 50
    assert first_page["has_more_messages"] is True
    assert first_page["messages"][0]["body"] == "질문 5"
    assert first_page["messages"][-1]["role"] == "assistant"
    # A paginated window carries only the turns its own messages belong to, not the whole conversation's 30.
    assert len(first_page["turns"]) == 25

    oldest_loaded_sequence = first_page["messages"][0]["sequence"]
    older_page = client.get(
        f"/api/conversations/{conversation_id}", headers=MINA,
        params={"before_sequence": oldest_loaded_sequence},
    ).json()
    assert older_page["has_more_messages"] is False
    assert [m["body"] for m in older_page["messages"] if m["role"] == "user"] == [f"질문 {i}" for i in range(5)]
    assert all(m["sequence"] < oldest_loaded_sequence for m in older_page["messages"])

    # A history preview/search/count must stay accurate for the whole conversation, not just the visible window.
    assert first_page["user_message_count"] == 30
    assert first_page["first_user_message_excerpt"] == "질문 0"
    assert first_page["has_final_answer"] is True
