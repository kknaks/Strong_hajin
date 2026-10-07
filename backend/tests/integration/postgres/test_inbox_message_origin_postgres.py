"""메시지함 → AX 의 원래 메시지 칸이 **살아 있는 PostgreSQL** 에서 선다 (SPEC-008 §4.8 ③④ · WORK-012 WP4-BE).

sqlite 계약 시험(`tests/contract/test_inbox_message_context.py`)이 모양을 보고, 여기서는 운영과 같은 엔진에서
① 대화 참고 자료 → 업무 확정 → `tasks`·`work_requests` 의 원래 메시지 칸 · `made_task_count` · `label` 이 그대로 돌고
② 운영 SQL 이 옛 DB 에 칸 셋(트랜잭션) → 인덱스(`CONCURRENTLY`) 순으로 놓으며
③ `schema_sync`(로컬 additive 경로)가 칸 셋을 새로 만들 줄 아는지를 본다. 빈 격리 데이터베이스에서만 돈다.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import tempfile
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, inspect, text

from ax_workspace.bootstrap import schema_sync
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.platform.persistence import (
    ConversationTurnRecord,
    ExternalIntegrationRecord,
    ExternalMessageRecord,
    ExternalRoomRecord,
    TaskRecord,
    WorkRequestRecord,
)

from test_postgres_integration import _postgres_test_url

pytestmark = pytest.mark.integration
MINA = {"X-Demo-Persona": "mina"}
MANUAL = Path(__file__).resolve().parents[3] / "migrations" / "manual"
MIGRATION = MANUAL / "2026-10-07-inbox-message-origin.sql"
CONCURRENT = MANUAL / "2026-10-07-inbox-message-origin.concurrent.sql"
ISOLATED_INDEX = MANUAL / "2026-10-07-inbox-message-origin-index.sql"


def _seed_message(application) -> tuple[str, str]:
    now = datetime(2026, 10, 7, 1, 0, tzinfo=UTC)
    with application._session_factory() as session:
        slack = ExternalIntegrationRecord(
            member_id="mina", kind="slack", status="connected", account_key="T1", display_name="회사",
            account_meta={"user_id": "U-ME"}, created_at=now, updated_at=now,
        )
        session.add(slack)
        session.flush()
        room = ExternalRoomRecord(integration_id=slack.id, external_id="C1", room_type="channel", name="general", status="live",
                                  room_meta={}, created_at=now, updated_at=now)
        session.add(room)
        session.flush()
        ids = []
        for index in range(3):
            ts = f"{1800000000 + index}.000100"
            row = ExternalMessageRecord(
                integration_id=slack.id, room_id=room.id, source_kind="slack", container_key="C1", external_key=ts,
                sent_at=now + timedelta(minutes=index), author="U-OTHER", preview=f"줄 {index}",
                raw={"ts": ts, "user": "U-OTHER", "text": f"줄 {index}"}, created_at=now,
            )
            session.add(row)
            session.flush()
            ids.append(str(row.id))
        session.commit()
        return str(room.id), ids[1]


def _confirmed(client, application, message_id: str, action_type: str, payload: dict) -> None:
    conversation = client.post("/api/conversations", headers=MINA, json={"title": "메시지함"}).json()
    accepted = client.post(
        f"/api/conversations/{conversation['conversation_id']}/messages",
        headers={**MINA, "Idempotency-Key": str(uuid4())},
        json={"body": "이 메시지 읽고 업무를 생성해 줘", "context": [
            {"resource_type": "inbox_message", "resource_id": message_id, "resource_version": 1, "included": True},
        ]},
    )
    assert accepted.status_code == 202, accepted.text
    with application._session_factory() as session:
        execution_id = session.get(ConversationTurnRecord, UUID(accepted.json()["turn_id"])).execution_id
    proposal = application.propose_action(application.authenticated_principal("mina"), execution_id, action_type, "확인", payload)
    item = client.get(f"/api/action-items/{proposal['action_id']}", headers=MINA).json()
    confirmed = client.post(
        f"/api/action-items/{item['action_item_id']}/commands/confirm",
        headers=MINA,
        json={"expected_version": item["expected_version"], "base_submission_version": item["submission_version"]},
    )
    assert confirmed.status_code == 200, confirmed.text


def test_confirmed_work_keeps_its_original_message_and_the_message_counts_it() -> None:
    database_url = _postgres_test_url()
    reset_database(database_url)
    app = create_app(Settings(RuntimeProfile.TEST, database_url, materials_dir=tempfile.mkdtemp()))
    client = TestClient(app)
    application = app.state.workflow_application
    room_id, message_id = _seed_message(application)
    _confirmed(client, application, message_id, "task.create_self", {"title": "슬랙에서 온 일"})
    _confirmed(client, application, message_id, "work_request.create", {"title": "슬랙에서 온 요청", "assignee_id": "jiho"})
    with application._session_factory() as session:
        task = session.query(TaskRecord).filter(TaskRecord.title == "슬랙에서 온 일").one()
        request = session.query(WorkRequestRecord).filter(WorkRequestRecord.title == "슬랙에서 온 요청").one()
        request_task = session.query(TaskRecord).filter(TaskRecord.source_work_request_id == request.id).one()
        assert {task.source_inbox_message_id, request.source_inbox_message_id, request_task.source_inbox_message_id} == {UUID(message_id)}
        task_id = str(task.id)
    origin = client.get(f"/api/tasks/{task_id}", headers=MINA).json()["origin"]
    assert origin["message"] == {"message_id": message_id, "source_kind": "slack", "room_id": room_id, "label": "슬랙 #general"}
    counts = {row["id"]: row["made_task_count"] for row in client.get(f"/api/inbox/rooms/{room_id}/messages", headers=MINA).json()["messages"]}
    assert counts[message_id] == 2 and sorted(counts.values()) == [0, 0, 2]


def _statements(path: Path) -> list[str]:
    sql = "\n".join(line for line in path.read_text().splitlines() if not line.lstrip().startswith("--"))
    return [statement for statement in sql.split(";") if statement.strip()]


def _drop_new_schema(engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_tasks_source_inbox_message_id"))
        connection.execute(text("ALTER TABLE tasks DROP COLUMN source_inbox_message_id"))
        connection.execute(text("ALTER TABLE work_requests DROP COLUMN source_inbox_message_id"))
        connection.execute(text("ALTER TABLE conversation_context_references DROP COLUMN label"))


def test_the_operational_files_lay_columns_in_a_transaction_then_the_index_concurrently() -> None:
    """검수 W-2 — 칸 판은 칸 셋만(트랜잭션 안전 · 인덱스 없음), 인덱스는 `.concurrent.sql` 이 트랜잭션 밖에서 놓는다."""
    database_url = _postgres_test_url()
    reset_database(database_url)
    engine = create_engine(database_url)
    _drop_new_schema(engine)
    columns = _statements(MIGRATION)
    assert columns and all(statement.strip().upper().startswith("ALTER TABLE") for statement in columns)
    with engine.begin() as connection:  # `psql -1` 과 같다 — 통째로 한 트랜잭션
        for statement in columns * 2:  # 재적용 가능(IF NOT EXISTS)
            connection.execute(text(statement))
    inspector = inspect(engine)
    assert "source_inbox_message_id" in {column["name"] for column in inspector.get_columns("tasks")}
    assert "source_inbox_message_id" in {column["name"] for column in inspector.get_columns("work_requests")}
    assert "label" in {column["name"] for column in inspector.get_columns("conversation_context_references")}
    assert "ix_tasks_source_inbox_message_id" not in {index["name"] for index in inspector.get_indexes("tasks")}
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:  # `psql` 기본 autocommit
        for statement in _statements(CONCURRENT):
            assert "CONCURRENTLY" in statement
            connection.execute(text(statement))
    assert "ix_tasks_source_inbox_message_id" in {index["name"] for index in inspect(engine).get_indexes("tasks")}
    engine.dispose()


def test_the_isolated_index_file_matches_and_runs_in_a_transaction() -> None:
    database_url = _postgres_test_url()
    reset_database(database_url)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_tasks_source_inbox_message_id"))
    with engine.begin() as connection:
        for statement in _statements(ISOLATED_INDEX):
            connection.execute(text(statement))
    assert "ix_tasks_source_inbox_message_id" in {index["name"] for index in inspect(engine).get_indexes("tasks")}
    normalize = lambda statement: " ".join(statement.replace("CONCURRENTLY ", "").split())  # noqa: E731
    assert [normalize(s) for s in _statements(ISOLATED_INDEX)] == [normalize(s) for s in _statements(CONCURRENT)]
    engine.dispose()


def test_schema_sync_adds_the_three_nullable_columns() -> None:
    database_url = _postgres_test_url()
    reset_database(database_url)
    engine = create_engine(database_url)
    _drop_new_schema(engine)
    engine.dispose()
    planned = schema_sync.plan(database_url)
    statements = "\n".join(planned["statements"])
    assert "ALTER TABLE tasks ADD COLUMN source_inbox_message_id" in statements
    assert "ALTER TABLE work_requests ADD COLUMN source_inbox_message_id" in statements
    assert "ALTER TABLE conversation_context_references ADD COLUMN label" in statements
    assert planned["manual"] == []
