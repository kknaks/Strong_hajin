"""분석 규칙이 바뀐 뒤 자료 검색 색인을 다시 만든다.

    DATABASE_URL=... uv run python -m ax_workspace.entrypoints.reindex_search

원문은 건드리지 않고 찾기 위한 형태만 다시 만든다. 같은 규칙으로 이미 만들어진 것은 지나가므로 여러 번 돌려도
한 번 돌린 것과 같고, 중간에 멈춰도 남은 것부터 이어서 한다.
"""
from __future__ import annotations

import json
import sys


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="reindex_search", description="Rebuild the material search index.")
    parser.add_argument("--batch", type=int, default=500, help="한 번에 다시 만들 구간 수")
    arguments = parser.parse_args(argv)

    from ax_workspace.bootstrap.application import create_workflow_application
    from ax_workspace.bootstrap.settings import Settings

    settings = Settings.from_environment()
    if not settings.developer_auth_enabled:
        print("reindex_search는 개발·테스트 프로파일에서만 씁니다", file=sys.stderr)
        return 2
    application = create_workflow_application(settings)
    total = 0
    while True:
        written = application.reindex_material_search(limit=arguments.batch)
        total += written
        if written == 0:
            break
    print(json.dumps({"reindexed_chunks": total}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
