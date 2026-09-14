"""`ADD COLUMN` 이 모델의 제약을 그대로 싣는가 (`bootstrap/schema_sync.py`).

alembic 이 없으므로 로컬 스키마를 모델에 맞추는 길은 이 파일 하나다. 그래서 **여기서 새는 것은
이미 쌓인 데이터로 곧장 새어 나간다** — 타입만 싣고 `DEFAULT`·`NOT NULL` 을 빼면 기존 행이 전부
NULL 이 되고, 그 컬럼으로 거르는 조회에서 그 행들이 통째로 사라진다.

실물에서 그 일이 날 뻔했다: 회의록을 세 벌로 가르며 `meeting_agendas.track` 을 더했는데 DDL 에
기본값이 실리지 않아 기존 안건 52건이 `track = NULL` 이 될 참이었다 — 세 벌 필터 어디에도 걸리지
않으니 회의 39건이 빈 화면이 된다. **두 판이 지나도록 아무도 몰랐던 이유는 이 자리를 거는 시험이
없었기 때문이다.**
"""
from __future__ import annotations

from sqlalchemy import JSON, Boolean, Integer, MetaData, String, Table, create_engine, text
from sqlalchemy.schema import Column

from ax_workspace.bootstrap.schema_sync import apply, plan
from ax_workspace.platform.persistence import Base


def _url(tmp_path, name: str = "sync.db") -> str:
    return f"sqlite:///{tmp_path / name}"


def _statements_for(made: dict, table: str) -> list[str]:
    return [line for line in made["statements"] if f"ALTER TABLE {table} " in line]


def _manual_for(made: dict, table: str) -> list[str]:
    return [line for line in made["manual"] if line.startswith(f"{table}.")]


def test_a_not_null_column_with_a_server_default_carries_both_into_the_ddl(tmp_path) -> None:
    """**모델이 `NOT NULL` 과 기본값을 말하면 DDL 도 그것을 말한다.**

    기본값이 있는 `NOT NULL` 컬럼을 더하면 데이터베이스가 **기존 행을 그 값으로 채운다** — 그래서
    이 한 줄이 「이미 쌓인 행을 어떻게 하나」를 통째로 없앤다. 타입만 실으면 그 행들이 NULL 로 남는다.
    """
    engine = create_engine(_url(tmp_path))
    metadata = MetaData()
    Table("widgets", metadata, Column("id", String(10), primary_key=True))
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO widgets (id) VALUES ('before')"))
    engine.dispose()

    grown = MetaData()
    Table(
        "widgets",
        grown,
        Column("id", String(10), primary_key=True),
        Column("track", String(10), nullable=False, default="memo", server_default=text("'memo'")),
        Column("lineage", JSON, nullable=False, default=list, server_default=text("'[]'")),
        Column("flag", Boolean, nullable=False, default=False, server_default=text("false")),
    )

    made = _plan_against(grown, _url(tmp_path))
    ddl = _statements_for(made, "widgets")
    assert len(ddl) == 3, ddl
    for line in ddl:
        assert "NOT NULL" in line, line
        assert "DEFAULT" in line, line
    assert _manual_for(made, "widgets") == []

    _apply_against(grown, _url(tmp_path))
    engine = create_engine(_url(tmp_path))
    with engine.begin() as connection:
        row = connection.execute(text("SELECT track, lineage, flag FROM widgets")).one()
    engine.dispose()
    # **기존 행이 기본값으로 찼다** — NULL 이 아니다. 이것이 이 시험의 전부다.
    assert row.track == "memo"
    assert row.lineage == "[]"
    assert row.flag in (0, False)


def test_a_not_null_column_without_a_server_default_is_a_decision_for_a_person(tmp_path) -> None:
    """**파이썬 쪽 `default=` 만으로는 기존 행에 닿지 않는다** — 그것은 ORM 이 INSERT 할 때만 쓰인다.

    그런 컬럼을 조용히 nullable 로 붙이면 모델은 「비어 있을 수 없다」고 하는데 데이터베이스에는 NULL 이
    있는 상태가 된다. 그래서 **DDL 을 내지 않고 사람에게 올린다.**
    """
    engine = create_engine(_url(tmp_path))
    metadata = MetaData()
    Table("gadgets", metadata, Column("id", String(10), primary_key=True))
    metadata.create_all(engine)
    engine.dispose()

    grown = MetaData()
    Table(
        "gadgets",
        grown,
        Column("id", String(10), primary_key=True),
        # 파이썬 기본값만 있다 — DDL 에 실을 값이 없다.
        Column("counted", Integer, nullable=False, default=0),
        # 기본값이 아예 없다.
        Column("named", String(20), nullable=False),
    )

    made = _plan_against(grown, _url(tmp_path))
    assert _statements_for(made, "gadgets") == []
    assert sorted(_manual_for(made, "gadgets")) == [
        "gadgets.counted: NOT NULL 컬럼은 기존 행의 값을 사람이 정해야 합니다",
        "gadgets.named: NOT NULL 컬럼은 기존 행의 값을 사람이 정해야 합니다",
    ]


def test_a_nullable_column_is_added_as_the_model_wrote_it(tmp_path) -> None:
    """비어 있어도 되는 컬럼은 그대로 붙는다 — 기본값이 있으면 그것도 함께 싣는다."""
    engine = create_engine(_url(tmp_path))
    metadata = MetaData()
    Table("doodads", metadata, Column("id", String(10), primary_key=True))
    metadata.create_all(engine)
    engine.dispose()

    grown = MetaData()
    Table(
        "doodads",
        grown,
        Column("id", String(10), primary_key=True),
        Column("note", String(20)),
        Column("kind", String(20), server_default=text("'plain'")),
    )

    made = _plan_against(grown, _url(tmp_path))
    ddl = _statements_for(made, "doodads")
    assert len(ddl) == 2, ddl
    assert all("NOT NULL" not in line for line in ddl), ddl
    assert any("DEFAULT 'plain'" in line for line in ddl), ddl
    assert _manual_for(made, "doodads") == []


def test_the_meeting_track_axis_lands_on_existing_rows_rather_than_nulling_them(tmp_path) -> None:
    """실물 자리 하나 — **세 벌 축이 이미 쌓인 안건·줄을 비우지 않는다** (SPEC-004 v0.5 §4.0).

    이것이 이 파일이 생긴 이유다. `meeting_agendas.track` 이 NULL 이면 그 안건은 세 벌 필터 어디에도
    걸리지 않아 **화면에서 사라진다.** 진짜 모델로 0.4.x 스키마를 재현해 그 일이 안 나는 것을 본다.
    """
    url = _url(tmp_path, "meetings.db")
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        # 벌 축과 계보가 서기 전 모양으로 되돌린다.
        connection.execute(text("DROP INDEX ix_meeting_agendas_meeting_track_order"))
        connection.execute(text("ALTER TABLE meeting_agendas DROP COLUMN track"))
        connection.execute(text("ALTER TABLE meeting_agendas DROP COLUMN merged_from"))
        connection.execute(text("DROP INDEX ix_meeting_lines_agenda_track_order"))
        connection.execute(text("ALTER TABLE meeting_lines DROP COLUMN from_lines"))
        connection.execute(
            text(
                "INSERT INTO meetings (id, organization_id, owner_id, starts_at, ends_at, status,"
                " external_attendees, version, created_at, updated_at, transcript_source)"
                " VALUES ('m1','o','mina','2026-01-01','2026-01-01','done','[]',1,"
                "'2026-01-01','2026-01-01','realtime')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO meeting_agendas (id, meeting_id, order_index, title, source, concluded,"
                " title_placeholder, created_at, updated_at)"
                " VALUES ('a1','m1',1,'이미 쌓인 안건','manual',0,0,'2026-01-01','2026-01-01')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO meeting_lines (id, meeting_id, agenda_id, track, order_index, text,"
                " evidence, created_at, updated_at)"
                " VALUES ('l1','m1','a1','memo',1,'이미 쌓인 줄','[]','2026-01-01','2026-01-01')"
            )
        )
    engine.dispose()

    made = plan(url)
    assert _manual_for(made, "meeting_agendas") == []
    assert _manual_for(made, "meeting_lines") == []
    apply(url)

    engine = create_engine(url)
    with engine.begin() as connection:
        agenda = connection.execute(text("SELECT track, merged_from FROM meeting_agendas")).one()
        line = connection.execute(text("SELECT track, from_lines FROM meeting_lines")).one()
    engine.dispose()
    # **기존 안건이 사람 벌로 선다** — NULL 이 아니라서 「메모」 탭에 그대로 보인다.
    assert agenda.track == "memo"
    # 계보는 **빈 목록**이다 — 벌이 갈리기 전의 안건·줄이라 가리킬 원본이 없다. 따옴표가 겹치지 않는다.
    assert agenda.merged_from == "[]"
    assert line.track == "memo" and line.from_lines == "[]"


# --------------------------------------------------------------------- 발판
#
# `plan`·`apply` 는 제품 모델(`Base.metadata`)을 본다. 시험용 표로 같은 코드를 지나려면 그 자리에
# 잠깐 다른 metadata 를 끼운다 — 모델을 시험을 위해 고치지 않는다.


def _plan_against(metadata: MetaData, url: str) -> dict:
    import ax_workspace.bootstrap.schema_sync as module

    original = module.Base.metadata
    module.Base.metadata = metadata
    try:
        return plan(url)
    finally:
        module.Base.metadata = original


def _apply_against(metadata: MetaData, url: str) -> dict:
    import ax_workspace.bootstrap.schema_sync as module

    original = module.Base.metadata
    module.Base.metadata = metadata
    try:
        return apply(url)
    finally:
        module.Base.metadata = original
