from __future__ import annotations

import subprocess
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]


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
