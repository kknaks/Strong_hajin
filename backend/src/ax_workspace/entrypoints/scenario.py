"""scenario CLI — 들어와 있는 조직 위에 예제 업무를 만든다.

    DATABASE_URL=... uv run python -m ax_workspace.entrypoints.scenario thesc

조직은 정본이고 이것은 아니다. 여기서 만드는 모든 것은 제품의 정식 command를 그 사람으로서 지나가므로 actor·
이력·권한이 진짜다. 지울 수 있는 로컬 demo 데이터베이스에만 쓴다.
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scenario", description="Build example work on the organization already loaded.")
    parser.add_argument("plan", choices=["thesc"], help="어느 계획을 쓸지")
    arguments = parser.parse_args(argv)

    from ax_workspace.bootstrap.application import create_workflow_application
    from ax_workspace.bootstrap.scenario import build
    from ax_workspace.bootstrap.scenario_thesc import THESC
    from ax_workspace.bootstrap.settings import Settings
    from ax_workspace.entrypoints.reset_demo import require_safe_demo_database

    settings = Settings.from_environment()
    if not settings.developer_auth_enabled:
        print("scenario는 개발·테스트 프로파일에서만 씁니다", file=sys.stderr)
        return 2
    try:
        require_safe_demo_database(settings.database_url)
    except ValueError:
        print(f"이 데이터베이스에는 만들지 않습니다: {settings.database_url}", file=sys.stderr)
        return 2

    result = build(create_workflow_application(settings), {"thesc": THESC}[arguments.plan])
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
