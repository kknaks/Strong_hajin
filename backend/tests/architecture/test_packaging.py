from __future__ import annotations

import subprocess
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _run(*command: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


def test_wheel_installs_canonical_package_and_entrypoints_outside_the_repository(tmp_path: Path) -> None:
    wheel_dir = tmp_path / "wheel"
    build = _run("uv", "build", "--wheel", "--out-dir", str(wheel_dir), cwd=BACKEND_ROOT)
    assert build.returncode == 0, build.stderr
    wheel = next(wheel_dir.glob("ax_workspace-*.whl"))

    venv = tmp_path / "clean-venv"
    create_venv = _run(sys.executable, "-m", "venv", str(venv), cwd=tmp_path)
    assert create_venv.returncode == 0, create_venv.stderr
    python = venv / "bin" / "python"
    install = _run(str(python), "-m", "pip", "install", str(wheel), cwd=tmp_path)
    assert install.returncode == 0, install.stderr

    imported = _run(
        str(python),
        "-c",
        "import ax_workspace; import ax_workspace.entrypoints.http; import ax_workspace.entrypoints.mcp",
        cwd=tmp_path,
    )
    assert imported.returncode == 0, imported.stderr

    # 코드가 아닌 것도 함께 실려야 한다 — 내보내기 조판 템플릿과 AI 출력 스키마는 런타임에 파일로 읽힌다.
    # import 만으로는 이 결함이 드러나지 않는다: 부팅은 멀쩡하고 사람이 [내보내기] 를 누를 때 터진다.
    carried = _run(
        str(python),
        "-c",
        "from ax_workspace.modules.meetings.export import TEMPLATE_PATH;"
        "from ax_workspace.modules.meetings.batch import SCHEMA_PATH;"
        "assert TEMPLATE_PATH.is_file(), TEMPLATE_PATH;"
        "assert SCHEMA_PATH.is_file(), SCHEMA_PATH",
        cwd=tmp_path,
    )
    assert carried.returncode == 0, carried.stderr
