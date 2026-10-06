"""외부 채널 표의 운영 SQL 이 모델과 어긋나지 않는다 — 운영은 `schema_sync` 가 아니라 이 파일로 선다 (WORK-011 BE-1)."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from ax_workspace.platform.persistence import Base

BACKEND_ROOT = Path(__file__).resolve().parents[2]
MANUAL_SQL = BACKEND_ROOT / "migrations" / "manual" / "2026-10-06-external-channels.sql"
TABLES = (
    "external_integrations",
    "external_rooms",
    "external_messages",
    "external_attachments",
    "external_read_states",
    "external_sent_replies",
    "external_device_tokens",
    "external_oauth_states",
    "profile_images",
)


def _normalized(sql: str) -> str:
    return " ".join(sql.split())


def test_manual_sql_is_the_compiled_model_for_every_external_channel_table() -> None:
    applied = _normalized(MANUAL_SQL.read_text(encoding="utf-8"))
    dialect = postgresql.dialect()
    for name in TABLES:
        table = Base.metadata.tables[name]
        assert _normalized(str(CreateTable(table, if_not_exists=True).compile(dialect=dialect))) in applied, name
        for index in table.indexes:
            assert _normalized(str(CreateIndex(index, if_not_exists=True).compile(dialect=dialect))) in applied, index.name
    model = {name for name in Base.metadata.tables if name.startswith("external_")}
    assert model <= set(TABLES), f"새 external_* 표가 운영 SQL 에 없다: {sorted(model - set(TABLES))}"


def test_later_phases_have_their_own_files_in_the_external_channels_module() -> None:
    """BE-2(수집)·BE-3(메시지함·카톡 수신)이 같은 파일을 열지 않고 나란히 가도록 BE-1 이 자리를 갈라 둔다."""
    module = BACKEND_ROOT / "src" / "ax_workspace" / "modules" / "external_channels"
    for name in ("domain.py", "events.py", "application.py", "sync.py", "inbox.py", "kakao_ingest.py"):
        assert (module / name).is_file(), name
