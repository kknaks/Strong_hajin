from __future__ import annotations

import argparse
from pathlib import Path
import re

from sqlalchemy.engine import make_url

from ax_workspace.bootstrap.reset import reset_database as _reset_database
from ax_workspace.bootstrap.settings import Settings


_LOCAL_POSTGRES_HOSTS = {"localhost", "127.0.0.1", "::1"}
_SAFE_POSTGRES_DATABASE = re.compile(r"^ax_(?:demo|test)(?:_[a-z0-9_]+)?$")
_SAFE_SQLITE_FILE = re.compile(r"^(?:ax_)?(?:demo|test)(?:[-_a-z0-9]*)?\.db$")


def require_safe_demo_database(database_url: str) -> None:
    """Fail before connecting unless this is an explicitly named local demo database."""
    url = make_url(database_url)
    if url.drivername.startswith("postgresql"):
        if url.host in _LOCAL_POSTGRES_HOSTS and url.database and _SAFE_POSTGRES_DATABASE.fullmatch(url.database):
            return
    elif url.drivername == "sqlite":
        if url.database == ":memory:" or (url.database and _SAFE_SQLITE_FILE.fullmatch(Path(url.database).name)):
            return
    raise ValueError("reset_demo requires a safe local demo database URL")


def reset_database(database_url: str, *, demo_organization: bool = True) -> None:
    """The only schema-mutating operation. Application startup never calls this."""
    require_safe_demo_database(database_url)
    _reset_database(database_url, demo_organization=demo_organization)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="reset_demo", description="Recreate the local demo schema.")
    # 실제 조직을 dataset으로 들여올 때는 예시 회사가 옆에 서 있지 않아야 한다.
    parser.add_argument("--catalog-only", action="store_true", help="제품 catalog만 두고 예시 회사는 만들지 않는다")
    arguments = parser.parse_args(argv)

    settings = Settings.from_environment()
    if not settings.developer_auth_enabled:
        raise RuntimeError("reset_demo is available only in development and test profiles")
    reset_database(settings.database_url, demo_organization=not arguments.catalog_only)
    if arguments.catalog_only:
        print("Catalog reset: unit types, capabilities, recommended roles and daily-report-generation@1. 예시 회사는 없다.")
        return
    print("Demo schema reset and daily-report-generation@1 installed.")


if __name__ == "__main__":
    main()
