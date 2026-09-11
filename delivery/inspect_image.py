"""Inspect the final SCAX delivery image and every exported final-image layer."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


AUDIT_MARKER = "SCAX_COMMUNITY_NUITKA_EXPOSURE_CANARY_20260911"
SOURCE_SUFFIXES = {".py", ".pyc", ".pyo", ".c", ".h"}
FORBIDDEN_NAMES = {".git", ".venv", "__pycache__", "auth.json", ".env"}
PRIVATE_KEY_SUFFIXES = {".key", ".pem"}
MODULE_PATH = re.compile(r"ax_workspace/.+\.py")
INTERNAL_SYMBOL = re.compile(r"McpReportsFacade|create_codex_cli_provider|SCAX_RUNTIME_EXECUTABLE")


def _run(*command: str, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=capture, check=True)


def _python_abi(version: str) -> str:
    parts = version.split(".")
    if len(parts) < 2 or not all(part.isdigit() for part in parts):
        raise ValueError(f"invalid Python version label: {version!r}")
    return ".".join(parts[:2])


def _forbidden_delivery_path(name: str) -> bool:
    while name.startswith("./"):
        name = name[2:]
    path = PurePosixPath(name.lstrip("/"))
    parts = path.parts
    if any(part in FORBIDDEN_NAMES for part in parts):
        return True
    protected_root = bool(parts) and parts[0] in {"opt", "var"}
    if protected_root and path.suffix.lower() in SOURCE_SUFFIXES | PRIVATE_KEY_SUFFIXES:
        return True
    if protected_root and any(part == "tests" or part.startswith("test_") for part in parts):
        return True
    return "build/backend" in str(path) or "protected.build" in str(path)


def _scan_exported_layers(image: str, archive: Path) -> list[str]:
    _run("docker", "save", "--output", str(archive), image)
    hits: set[str] = set()
    with tarfile.open(archive, "r") as outer:
        for member in outer:
            if not member.isfile():
                continue
            layer = outer.extractfile(member)
            if layer is None:
                continue
            try:
                with tarfile.open(fileobj=layer, mode="r|*") as contents:
                    for item in contents:
                        if _forbidden_delivery_path(item.name):
                            hits.add(item.name)
            except tarfile.ReadError:
                continue
    return sorted(hits)


def _scan_runtime_tree(root: Path) -> tuple[list[str], dict[str, object]]:
    forbidden: list[str] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if (
            any(part in FORBIDDEN_NAMES for part in relative.parts)
            or relative.suffix.lower() in SOURCE_SUFFIXES | PRIVATE_KEY_SUFFIXES
            or any(part == "tests" or part.startswith("test_") for part in relative.parts)
        ):
            forbidden.append(str(relative))
    marker_visible = False
    module_path_count = 0
    symbol_count = 0
    samples: list[str] = []
    python_shared_libraries: set[str] = set()
    for binary in root.rglob("*"):
        if not binary.is_file():
            continue
        if binary.name.startswith("libpython") and ".so" in binary.name:
            python_shared_libraries.add(binary.name)
        try:
            with binary.open("rb") as handle:
                magic = handle.read(4)
            if magic != b"\x7fELF":
                continue
        except OSError:
            continue
        process = subprocess.Popen(
            ["strings", "-a", str(binary)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            value = line.rstrip()
            if AUDIT_MARKER in value:
                marker_visible = True
            if MODULE_PATH.search(value):
                module_path_count += 1
                if len(samples) < 8:
                    samples.append(value)
            if INTERNAL_SYMBOL.search(value):
                symbol_count += 1
                if len(samples) < 8:
                    samples.append(value)
        if process.wait() != 0:
            raise RuntimeError(f"strings failed for {binary}")
    return forbidden, {
        "audit_marker_visible": marker_visible,
        "module_path_string_count": module_path_count,
        "internal_symbol_string_count": symbol_count,
        "exposure_samples": samples,
        "python_shared_libraries": sorted(python_shared_libraries),
    }


def inspect_image(image: str, expected_constants: str) -> dict[str, object]:
    metadata = json.loads(_run("docker", "image", "inspect", image).stdout)[0]
    if metadata["Architecture"] != "amd64":
        raise RuntimeError(f"expected amd64 image, got {metadata['Architecture']}")
    if metadata["Config"]["User"] != "scax":
        raise RuntimeError(f"expected non-root scax user, got {metadata['Config']['User']!r}")
    config_text = json.dumps(metadata["Config"], ensure_ascii=False)
    config_leaks = [value for value in ("/Users/", "/build/backend", "auth.json") if value in config_text]
    labels = metadata["Config"].get("Labels") or {}
    python_version = labels.get("org.scax.python.version")
    if not python_version:
        raise RuntimeError("image is missing org.scax.python.version label")

    with tempfile.TemporaryDirectory(prefix="scax-image-inspect-") as temporary:
        scratch = Path(temporary)
        container_id = _run("docker", "create", "--platform", "linux/amd64", image).stdout.strip()
        try:
            _run("docker", "cp", f"{container_id}:/opt", str(scratch / "opt"))
        finally:
            _run("docker", "container", "rm", "--force", container_id)
        tree_forbidden, exposure = _scan_runtime_tree(scratch / "opt")
        layer_forbidden = _scan_exported_layers(image, scratch / "image.tar")

    expected_library_prefix = f"libpython{_python_abi(python_version)}.so"
    if not any(name.startswith(expected_library_prefix) for name in exposure["python_shared_libraries"]):
        raise RuntimeError(
            f"compiled runtime ABI mismatch: label={python_version}, libraries={exposure['python_shared_libraries']}"
        )

    marker_visible = bool(exposure["audit_marker_visible"])
    expected_visible = expected_constants == "visible"
    if marker_visible != expected_visible:
        raise RuntimeError(
            f"audit marker visibility mismatch: expected {expected_constants}, observed {'visible' if marker_visible else 'hidden'}"
        )
    if tree_forbidden or layer_forbidden or config_leaks:
        raise RuntimeError(
            json.dumps(
                {
                    "runtime_forbidden": tree_forbidden,
                    "layer_forbidden": layer_forbidden,
                    "config_leaks": config_leaks,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return {
        "image": image,
        "image_id": metadata["Id"],
        "architecture": metadata["Architecture"],
        "size_bytes": metadata["Size"],
        "user": metadata["Config"]["User"],
        "python_version": python_version,
        "nuitka_version": labels.get("org.scax.nuitka.version"),
        "codex_version": labels.get("org.scax.codex.version"),
        "runtime_forbidden": tree_forbidden,
        "layer_forbidden": layer_forbidden,
        "config_leaks": config_leaks,
        "constant_protection_expectation": expected_constants,
        **exposure,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="scax-protected:test")
    parser.add_argument("--expect-constants", choices=("visible", "hidden"), required=True)
    args = parser.parse_args()
    print(json.dumps(inspect_image(args.image, args.expect_constants), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
