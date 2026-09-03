"""Explicit local-demo schema bootstrap; never invoked by application startup."""
from __future__ import annotations

from ax_workspace.bootstrap.seed import seed_catalog
from ax_workspace.platform.persistence import Base, make_session_factory


def reset_database(database_url: str) -> None:
    session_factory = make_session_factory(database_url)
    engine = session_factory.kw["bind"]
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_factory() as session:
        seed_catalog(session)
