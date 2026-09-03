"""Explicit local-demo schema bootstrap; never invoked by application startup."""
from __future__ import annotations

from ax_workspace.bootstrap.seed import seed_catalog, seed_technical_workflow_spike
from ax_workspace.platform.conversation_queue import ensure_conversation_queue
from ax_workspace.platform.persistence import Base, make_session_factory
from sqlalchemy import text


def reset_database(database_url: str, *, technical_spike: bool = False) -> None:
    session_factory = make_session_factory(database_url)
    engine = session_factory.kw["bind"]
    if engine.dialect.name == "postgresql":
        # reset_demo validates the URL before this boundary. Recreate the isolated
        # disposable schema so obsolete development FKs cannot survive metadata changes.
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
    else:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            ensure_conversation_queue(connection)
    with session_factory() as session:
        seed_catalog(session)
        if technical_spike:
            seed_technical_workflow_spike(session)
