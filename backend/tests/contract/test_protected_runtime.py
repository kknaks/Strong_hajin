import subprocess
import sys
from pathlib import Path

import pytest
from ax_workspace.entrypoints import material_worker, protected


BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.serial
def test_protected_build_entrypoint_runs_outside_the_package_import_root(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(BACKEND_ROOT / "src" / "scax_protected.py"), "--help"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "conversation-worker" in result.stdout


def test_protected_launcher_dispatches_the_selected_runtime_role(monkeypatch) -> None:
    started: list[str] = []
    monkeypatch.setattr(material_worker, "main", lambda: started.append("material-worker"))

    exit_code = protected.main(["material-worker"])

    assert exit_code == 0
    assert started == ["material-worker"]


def test_protected_launcher_exposes_only_the_synthetic_audit_canary_on_request(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("SCAX_PROTECTION_AUDIT", "1")
    monkeypatch.setattr(material_worker, "main", lambda: (_ for _ in ()).throw(AssertionError("must not start")))

    exit_code = protected.main(["material-worker"])

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "SCAX_COMMUNITY_NUITKA_EXPOSURE_CANARY_20260911"
