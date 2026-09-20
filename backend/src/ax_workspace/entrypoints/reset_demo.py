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
    parser = argparse.ArgumentParser(prog="reset_demo", description="Bring the local demo schema to the current model.")
    # 실제 조직을 dataset으로 들여올 때는 예시 회사가 옆에 서 있지 않아야 한다.
    parser.add_argument("--catalog-only", action="store_true", help="제품 catalog만 두고 예시 회사는 만들지 않는다")
    parser.add_argument(
        "--sync",
        action="store_true",
        help="지우지 않고 맞춘다 — 모델에 있고 데이터베이스에 없는 표와 열만 더하고, 잃을 수 있는 것은 사람이 정하도록 남긴다",
    )
    arguments = parser.parse_args(argv)

    settings = Settings.from_environment()
    if not settings.developer_auth_enabled:
        raise RuntimeError("reset_demo is available only in development and test profiles")

    # 스키마를 다루는 명령은 하나다. 지울지 말지는 플래그가 가른다 — 이름이 둘이면 파괴적인 쪽을 잘못 부르기 쉽다.
    if arguments.sync:
        from ax_workspace.bootstrap.schema_sync import apply

        made = apply(settings.database_url)
        for statement in made["statements"]:
            print(f"applied: {statement}")
        for note in made.get("manual", []):
            print(f"사람이 정할 것: {note}")
        if not made["statements"]:
            print("데이터베이스가 이미 모델과 같습니다.")
        return

    reset_database(settings.database_url, demo_organization=not arguments.catalog_only)
    if arguments.catalog_only:
        print("Catalog reset: unit types, capabilities, recommended roles and daily-report-generation@1. 예시 회사는 없다.")
        return
    # Keep reset_database/seed_catalog's empty-work contract for test and dataset
    # callers. Only this explicit disposable-demo command installs screen fixtures.
    from ax_workspace.bootstrap.application import create_workflow_application
    from ax_workspace.bootstrap.demo_work import seed_demo_work

    seed_demo_work(create_workflow_application(settings))
    print("Demo schema reset, My Work fixtures seeded (mina/jiho), and daily-report-generation@1 installed.")


if __name__ == "__main__":
    main()
