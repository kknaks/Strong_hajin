"""용어 보정 표가 **살아 있는 PostgreSQL** 에서 선다 (SPEC-010 §4.7-4 · WORK-012 WP2-BE · 새 표 + `meetings.term_corrected_at`).

sqlite 계약 시험이 응답 모양을 보고, 여기서는 운영과 같은 엔진에서 표·칸·순서·`timestamptz` 가 그대로 도는지와
`schema_sync`(로컬 additive 경로)가 두 자리를 새로 만들 줄 아는지를 본다. 빈 격리 데이터베이스에서만 돈다.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import tempfile
from uuid import UUID

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, text

from ax_workspace.bootstrap import schema_sync
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database

from test_postgres_integration import _postgres_test_url

pytestmark = pytest.mark.integration
MINA = {"X-Demo-Persona": "mina"}


def test_the_term_table_round_trips_in_order_and_null_means_never_corrected() -> None:
    database_url = _postgres_test_url()
    reset_database(database_url)
    app = create_app(Settings(RuntimeProfile.TEST, database_url, materials_dir=tempfile.mkdtemp()))
    client = TestClient(app)
    starts = datetime.now(UTC) + timedelta(days=2)
    created = client.post(
        "/api/meetings",
        headers=MINA,
        json={"title": "보정 표 회의", "starts_at": starts.isoformat(), "ends_at": (starts + timedelta(hours=1)).isoformat()},
    )
    assert created.status_code == 201, created.text
    meeting_id = created.json()["meeting"]["meeting_id"]
    assert client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["term_corrections"] is None

    application = app.state.workflow_application
    rows = [
        {"heard": "캐스티", "corrected": "CASTI", "grade": "auto"},
        {"heard": "차티", "corrected": "팀원 B", "grade": "presumed"},
    ]
    with application._session_factory() as session:
        repository = application._meetings(session)._repository
        repository.replace_term_corrections(repository.meeting(UUID(meeting_id), lock=True), rows, at=datetime.now(UTC))
        session.commit()
    assert client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["term_corrections"] == rows

    # 다시 적재하면 통째로 갈아 끼운다 — 0개도 「돌았다」 다.
    with application._session_factory() as session:
        repository = application._meetings(session)._repository
        repository.replace_term_corrections(repository.meeting(UUID(meeting_id), lock=True), [], at=datetime.now(UTC))
        session.commit()
    assert client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["term_corrections"] == []


def test_schema_sync_adds_the_new_table_and_the_nullable_column_to_an_older_database() -> None:
    """운영 SQL(`migrations/manual/2026-10-07-meeting-term-corrections.sql`)과 같은 두 자리를 로컬 additive 경로가 만든다."""
    database_url = _postgres_test_url()
    reset_database(database_url)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE meeting_term_corrections"))
        connection.execute(text("ALTER TABLE meetings DROP COLUMN term_corrected_at"))
    engine.dispose()
    planned = schema_sync.plan(database_url)
    statements = "\n".join(planned["statements"])
    assert "CREATE TABLE meeting_term_corrections" in statements
    assert "ix_meeting_term_corrections_meeting_order" in statements
    assert "ALTER TABLE meetings ADD COLUMN term_corrected_at" in statements
    assert planned["manual"] == []
