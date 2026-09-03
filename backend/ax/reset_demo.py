from __future__ import annotations

from ax.database import Base, make_session_factory
from ax.seed import seed_catalog
from ax.settings import Settings


def reset_database(database_url: str) -> None:
    """The only schema-mutating operation. Application startup never calls this."""
    session_factory = make_session_factory(database_url)
    engine = session_factory.kw["bind"]
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_factory() as session:
        seed_catalog(session)


def main() -> None:
    settings = Settings.from_environment()
    reset_database(settings.database_url)
    print("Demo schema reset and 9 workflow definitions seeded.")


if __name__ == "__main__":
    main()
