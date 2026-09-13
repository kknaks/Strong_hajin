"""Explicit, bounded registration of legacy native materials for an authorized member."""
from __future__ import annotations

import argparse
import json

from ax_workspace.bootstrap.application import create_workflow_application
from ax_workspace.bootstrap.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--member-id", required=True, help="Member whose current read scope limits the backfill")
    parser.add_argument("--limit", type=int, default=100, help="Maximum new registrations (1–1000)")
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")
    settings = Settings.from_environment()
    if settings.job_queue_backend != "postgres":
        parser.error("a postgres job queue is required so queued work survives this process")
    application = create_workflow_application(settings)
    principal = application.authenticated_principal(args.member_id)
    count = application.backfill_native_materials(principal, limit=args.limit)
    print(json.dumps({"registered": count}))


if __name__ == "__main__":
    main()
