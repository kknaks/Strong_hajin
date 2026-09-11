import ast
import runpy
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_delivery_scanner_derives_the_python_abi_from_the_patch_version() -> None:
    scanner = runpy.run_path(str(REPOSITORY_ROOT / "delivery" / "inspect_image.py"))

    assert scanner["_python_abi"]("3.13.15") == "3.13"


def test_delivery_layer_scanner_rejects_a_root_git_directory() -> None:
    scanner = runpy.run_path(str(REPOSITORY_ROOT / "delivery" / "inspect_image.py"))

    assert scanner["_forbidden_delivery_path"]("./.git/config") is True
    assert scanner["_forbidden_delivery_path"]("/.git/HEAD") is True


def test_delivery_dockerfile_pins_the_selected_runtime_and_all_base_images() -> None:
    dockerfile = (REPOSITORY_ROOT / "delivery" / "Dockerfile").read_text(encoding="utf-8")

    assert "PYTHON_VERSION=3.13.15" in dockerfile
    assert "python:3.13.15-slim-bookworm@sha256:" in dockerfile
    assert "node:22.18.0-bookworm-slim@sha256:" in dockerfile
    assert "debian:bookworm-slim@sha256:" in dockerfile
    assert 'org.scax.python.version="${PYTHON_VERSION}"' in dockerfile
    assert "LicenseReport.rst.j2:/build/output/third-party-licenses.rst" in dockerfile


def test_delivery_transform_removes_only_source_docstrings(tmp_path: Path) -> None:
    source = tmp_path / "example.py"
    source.write_text(
        '"""module detail"""\nVALUE = "runtime value"\n\ndef answer():\n    """function detail"""\n    return VALUE\n\nclass Marker:\n    """docstring-only body"""\n',
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / "delivery" / "strip_docstrings.py"), str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    tree = ast.parse(source.read_text(encoding="utf-8"))
    compile(tree, str(source), "exec")
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef))
    marker = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    assert ast.get_docstring(tree) is None
    assert ast.get_docstring(function) is None
    assert isinstance(marker.body[0], ast.Pass)
    assert any(isinstance(node, ast.Constant) and node.value == "runtime value" for node in ast.walk(tree))
