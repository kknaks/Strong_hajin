"""Keep unit tests isolated from public transports and database implementations."""

from __future__ import annotations

import ast
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]
TEST_ROOT = BACKEND_ROOT / "tests"
MODULE_ROOT = BACKEND_ROOT / "src" / "ax_workspace" / "modules"
PURE_DOMAIN_MODULES = (
    "actions/confirmation.py",
    "actions/policy.py",
    "actions/replay.py",
    "meetings/batch.py",
    "meetings/finalize.py",
    "meetings/material_policy.py",
    "meetings/policy.py",
    # 겹침 판정의 규칙 — 반열림과 자정 분할 (SPEC-004 §2.9). 두 표를 조회하는 문은 platform 이지만
    # **그 문이 쓰는 규칙**은 데이터베이스 없이 서야 한다: 규칙이 두 곳에 있으면 두 규칙이 된다.
    "time_blocks.py",
    "work/checklist.py",
    "work/lifecycle.py",
    "work/material_folder_policy.py",
    "work/material_search_policy.py",
    "work/material_values.py",
    "work/request_lifecycle.py",
    "work/schedule.py",
    # 상태 투영과 기한 경과일 — **판정 한 자리** (SPEC-005 §4 · 어긋남 ①). 업무 목록·상세·캘린더와
    # 프로젝트 상세가 같은 함수를 지나야 같은 값을 낸다. 규칙이 두 곳에 있으면 두 규칙이 된다.
    "work/task_projection.py",
)


def _imported_modules(tree: ast.AST, *, package: tuple[str, ...] = ()) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level and package:
            parent = package[: max(0, len(package) - node.level + 1)]
            imported_parts = (*parent, *(node.module.split(".") if node.module else ()))
            imported_from = ".".join(imported_parts)
        else:
            imported_from = node.module or ""
        if imported_from:
            modules.add(imported_from)
        modules.update(
            ".".join(filter(None, (imported_from, alias.name)))
            for alias in node.names
            if alias.name != "*"
        )
    modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    return modules


def _matches_import_boundary(module: str, boundary: str) -> bool:
    return module == boundary or module.startswith(f"{boundary}.")


def test_unit_tests_do_not_cross_transport_or_database_boundaries() -> None:
    forbidden_imports = (
        "fastapi.testclient",
        "starlette.testclient",
        "sqlalchemy.orm",
        "ax_workspace.entrypoints.http",
        "ax_workspace.entrypoints.mcp",
        "ax_workspace.entrypoints.reset_demo",
        "ax_workspace.platform.persistence",
    )
    forbidden_calls = {
        "TestClient",
        "create_app",
        "create_engine",
        "make_session_factory",
        "reset_database",
    }
    forbidden_literals = ("postgresql://", "postgresql+psycopg://")
    violations: list[str] = []

    for path in sorted((TEST_ROOT / "unit").glob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for module in sorted(_imported_modules(tree)):
            if any(_matches_import_boundary(module, boundary) for boundary in forbidden_imports):
                violations.append(f"{path.name}: imports {module}")
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in forbidden_calls
            ):
                violations.append(f"{path.name}:{node.lineno}: calls {node.func.id}")
        for literal in forbidden_literals:
            if literal in source:
                violations.append(f"{path.name}: contains database URL {literal}")

    assert violations == [], violations


def test_domain_and_policy_modules_do_not_depend_on_adapters_or_frameworks() -> None:
    """A module advertised as domain policy must stay usable without transport or persistence setup."""
    forbidden_imports = (
        "ax_workspace.entrypoints",
        "ax_workspace.platform",
        "fastapi",
        "psycopg",
        "sqlalchemy",
        "starlette",
    )
    discovered = {
        path
        for path in MODULE_ROOT.rglob("*.py")
        if path.name in {"domain.py", "policy.py"} or path.name.endswith("_policy.py")
    }
    declared = {MODULE_ROOT / relative for relative in PURE_DOMAIN_MODULES}
    candidates = sorted(discovered | declared)
    violations: list[str] = []

    for path in candidates:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module_path = path.relative_to(BACKEND_ROOT / "src").with_suffix("")
        package = module_path.parts[:-1]
        for module in sorted(_imported_modules(tree, package=package)):
            if any(_matches_import_boundary(module, boundary) for boundary in forbidden_imports) or (
                module.endswith(".application") or ".application." in module
            ):
                relative = path.relative_to(BACKEND_ROOT)
                violations.append(f"{relative}: imports {module}")

    assert candidates, "no production domain or policy modules were found"
    assert all(path.is_file() for path in declared), "a declared pure domain module is missing"
    assert violations == [], violations


def test_functional_domain_interfaces_use_business_specific_results() -> None:
    """Generic Facts/Plan names hide whether a seam is state, decision, projection, or execution."""
    violations: list[str] = []

    for relative in PURE_DOMAIN_MODULES:
        path = MODULE_ROOT / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name.endswith(("Facts", "Plan")):
                violations.append(f"{relative}:{node.lineno}: generic class {node.name}")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("plan_"):
                violations.append(f"{relative}:{node.lineno}: generic function {node.name}")

    assert violations == [], violations
