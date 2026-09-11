"""Single executable dispatcher for the source-free delivery image."""
from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Sequence


EXPOSURE_AUDIT_MARKER = "SCAX_COMMUNITY_NUITKA_EXPOSURE_CANARY_20260911"


def _run_api() -> None:
    import uvicorn

    from ax_workspace.entrypoints.http import app

    uvicorn.run(
        app,
        host=os.getenv("SCAX_API_HOST", "0.0.0.0"),
        port=int(os.getenv("SCAX_API_PORT", "8000")),
    )


def _run_conversation_worker() -> None:
    from ax_workspace.entrypoints.conversation_worker import main

    main()


def _run_material_worker() -> None:
    from ax_workspace.entrypoints.material_worker import main

    main()


def _run_meeting_worker() -> None:
    from ax_workspace.entrypoints.meeting_worker import main

    main()


def _run_mcp() -> None:
    from ax_workspace.entrypoints.mcp import main

    main()


def main(argv: Sequence[str] | None = None) -> int:
    if os.getenv("SCAX_PROTECTION_AUDIT") == "1":
        print(EXPOSURE_AUDIT_MARKER)
        return 0
    parser = argparse.ArgumentParser(description="Run one SCAX backend process")
    parser.add_argument(
        "role",
        choices=("api", "conversation-worker", "material-worker", "meeting-worker", "mcp"),
    )
    role = parser.parse_args(argv).role
    handlers: dict[str, Callable[[], None]] = {
        "api": _run_api,
        "conversation-worker": _run_conversation_worker,
        "material-worker": _run_material_worker,
        "meeting-worker": _run_meeting_worker,
        "mcp": _run_mcp,
    }
    handlers[role]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
