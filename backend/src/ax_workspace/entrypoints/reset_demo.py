from __future__ import annotations

from pathlib import Path
import re

from sqlalchemy.engine import make_url

from ax_workspace.bootstrap.reset import reset_database as _reset_database
from ax_workspace.bootstrap.settings import Settings


_LOCAL_POSTGRES_HOSTS = {"localhost", "127.0.0.1", "::1"}
_SAFE_POSTGRES_DATABASE = re.compile(r"^ax_(?:demo|test)(?:_[a-z0-9_]+)?$")
_SAFE_SQLITE_FILE = re.compile(r"^(?:ax_)?(?:demo|test)(?:[-_a-z0-9]*)?\.db$")


def _require_safe_demo_database(database_url: str) -> None:
    """Fail before connecting unless this is an explicitly named local demo database."""
    url = make_url(database_url)
    if url.drivername.startswith("postgresql"):
        if url.host in _LOCAL_POSTGRES_HOSTS and url.database and _SAFE_POSTGRES_DATABASE.fullmatch(url.database):
            return
    elif url.drivername == "sqlite":
        if url.database == ":memory:" or (url.database and _SAFE_SQLITE_FILE.fullmatch(Path(url.database).name)):
            return
    raise ValueError("reset_demo requires a safe local demo database URL")


def reset_database(database_url: str) -> None:
    """The only schema-mutating operation. Application startup never calls this."""
    _require_safe_demo_database(database_url)
    _reset_database(database_url)


def main() -> None:
    settings = Settings.from_environment()
    if not settings.developer_auth_enabled:
        raise RuntimeError("reset_demo is available only in development and test profiles")
    reset_database(settings.database_url)
    print("Demo schema reset and 9 workflow definitions seeded.")


if __name__ == "__main__":
    main()
