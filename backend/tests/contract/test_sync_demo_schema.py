"""로컬 스키마 맞추기는 더하기만 한다.

The development helper closes the gap between a running demo database and the current model without a destructive
reset. It may add what is missing; anything that could lose data — a column the model no longer has, or a NOT NULL
column on a table that already has rows — is reported for a person to decide, never performed.
"""
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.bootstrap.schema_sync import apply, plan


def _database(tmp_path) -> str:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    return database_url


def test_a_current_database_needs_nothing(tmp_path) -> None:
    made = plan(_database(tmp_path))
    assert made["statements"] == [] and made["manual"] == []


def test_a_missing_table_and_column_are_added_without_touching_data(tmp_path) -> None:
    database_url = _database(tmp_path)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO tasks (id, created_by_actor_id, title, state, version, origin_kind, visibility, created_at, updated_at) "
            "VALUES ('11111111-1111-4111-8111-111111111111', 'mina', '남아 있을 업무', 'open', 1, 'direct', 'scope_default', "
            "'2026-09-06T00:00:00', '2026-09-06T00:00:00')"
        ))
        connection.execute(text("DROP TABLE task_references"))
        # A column an older database simply never had: SQLite cannot drop one under a foreign key, so use a
        # column with no constraint attached to stand for "the model moved on and this database has not".
        connection.execute(text("ALTER TABLE tasks DROP COLUMN block_reason"))

    made = apply(database_url)
    assert any("task_references" in statement for statement in made["statements"])
    assert any("block_reason" in statement for statement in made["statements"])

    inspector = inspect(create_engine(database_url))
    assert "task_references" in inspector.get_table_names()
    assert "block_reason" in {column["name"] for column in inspector.get_columns("tasks")}
    with create_engine(database_url).begin() as connection:
        # The row that was already there is still there.
        assert connection.execute(text("SELECT title FROM tasks")).scalar() == "남아 있을 업무"

    # Running it again is a no-op, so it is safe to run whenever.
    assert apply(database_url)["statements"] == []


def test_anything_that_could_lose_data_is_left_to_a_person(tmp_path) -> None:
    database_url = _database(tmp_path)
    with create_engine(database_url).begin() as connection:
        connection.execute(text("ALTER TABLE tasks ADD COLUMN 사람이_만든_컬럼 TEXT"))

    made = plan(database_url)
    assert any("사람이_만든_컬럼" in note for note in made["manual"])
    # It is reported, not dropped.
    assert not any("DROP" in statement.upper() for statement in made["statements"])
    apply(database_url)
    assert "사람이_만든_컬럼" in {column["name"] for column in inspect(create_engine(database_url)).get_columns("tasks")}


def test_additive_folder_table_requires_exactly_one_owner(tmp_path):
    database_url = _database(tmp_path)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE material_folders"))
        members_before = connection.execute(text("SELECT id FROM members ORDER BY id")).all()
    statements = apply(database_url)["statements"]
    assert any("ck_material_folder_owner" in statement for statement in statements)
    with engine.begin() as connection:
        assert connection.execute(text("SELECT id FROM members ORDER BY id")).all() == members_before
    with pytest.raises(IntegrityError, match="ck_material_folder_owner"):
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO material_folders (id, kind, title, owner_member_id, organization_id, created_by, created_at) "
                "VALUES ('11111111111141118111111111111111', 'personal', 'invalid owner', 'mina', 'product', 'mina', CURRENT_TIMESTAMP)"
            ))
    assert apply(database_url)["statements"] == []


def test_content_receipt_is_the_only_material_evidence_schema(tmp_path):
    database_url = _database(tmp_path)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE conversation_content_evidence"))
        members_before = connection.execute(text("SELECT id FROM members ORDER BY id")).all()
    made = apply(database_url)
    assert made["manual"] == []
    assert any("conversation_content_evidence" in statement for statement in made["statements"])
    assert "conversation_material_evidence" not in inspect(engine).get_table_names()
    with engine.begin() as connection:
        assert connection.execute(text("SELECT id FROM members ORDER BY id")).all() == members_before
    assert apply(database_url)["statements"] == []
