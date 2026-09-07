"""scenario CLI — 들어와 있는 조직 위에 예제 업무를 만든다.

    DATABASE_URL=... uv run python -m ax_workspace.entrypoints.scenario ~/scax-datasets/thesc

조직은 정본이고 이것은 아니다. 여기서 만드는 모든 것은 제품의 정식 command를 그 사람으로서 지나가므로 actor·
이력·권한이 진짜다. 지울 수 있는 로컬 demo 데이터베이스에만 쓴다.

계획은 조직 dataset과 같은 폴더의 CSV 표들이다. 실제 사람 key와 고객사 이름을 가리키므로 저장소 밖에 두고,
저장소는 그 표를 읽는 계약만 갖는다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scenario", description="Build example work on the organization already loaded.")
    parser.add_argument("plan", type=Path, help="저장소 밖의 dataset 폴더 (계획 CSV가 있는 곳)")
    arguments = parser.parse_args(argv)

    from ax_workspace.bootstrap.application import create_workflow_application
    from ax_workspace.bootstrap.scenario import build
    from ax_workspace.bootstrap.scenario_csv import ScenarioPlanError, load_plan
    from ax_workspace.bootstrap.settings import Settings
    from ax_workspace.entrypoints.reset_demo import require_safe_demo_database

    plan_path = arguments.plan.expanduser().resolve()
    repository = Path(__file__).resolve().parents[4]
    if plan_path.is_relative_to(repository):
        # 저장소는 계약을 갖고, 실제 사람과 고객사가 적힌 계획은 갖지 않는다.
        print(f"계획은 저장소 밖에 두어야 합니다: {plan_path}", file=sys.stderr)
        return 2

    settings = Settings.from_environment()
    if not settings.developer_auth_enabled:
        print("scenario는 개발·테스트 프로파일에서만 씁니다", file=sys.stderr)
        return 2
    try:
        require_safe_demo_database(settings.database_url)
    except ValueError:
        print(f"이 데이터베이스에는 만들지 않습니다: {settings.database_url}", file=sys.stderr)
        return 2

    if not plan_path.is_dir():
        print(f"계획 폴더가 없습니다: {plan_path}", file=sys.stderr)
        return 2

    try:
        plan = load_plan(plan_path)
    except ScenarioPlanError as error:
        print(str(error), file=sys.stderr)
        return 2

    result = build(create_workflow_application(settings), plan)
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
