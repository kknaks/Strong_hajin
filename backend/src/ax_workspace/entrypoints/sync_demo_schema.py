"""Thin entry point: bring a local demo database up to the current model without a destructive reset."""
from __future__ import annotations

from ax_workspace.bootstrap.schema_sync import apply
from ax_workspace.bootstrap.settings import Settings


def main() -> None:
    settings = Settings.from_environment()
    if not settings.developer_auth_enabled:
        raise RuntimeError("sync_demo_schema is available only in development and test profiles")
    made = apply(settings.database_url)
    for statement in made["statements"]:
        print(f"applied: {statement}")
    for note in made["manual"]:
        print(f"needs a person: {note}")
    if not made["statements"] and not made["manual"]:
        print("Demo schema already matches the model.")


if __name__ == "__main__":
    main()
