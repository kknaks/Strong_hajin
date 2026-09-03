from fastapi.testclient import TestClient
import pytest
from pathlib import Path

from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.http_auth import DeveloperAuthAdapter
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings


PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "ax_workspace"


def test_application_startup_never_mutates_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("schema mutation belongs exclusively to ax_workspace.entrypoints.reset_demo")

    monkeypatch.setattr("ax_workspace.platform.persistence.Base.metadata.create_all", fail_if_called)
    app = create_app(Settings(RuntimeProfile.TEST, "postgresql+psycopg://unused"))
    assert TestClient(app).get("/health").json()["status"] == "ok"


def test_production_has_no_developer_login_surface() -> None:
    app = create_app(Settings(RuntimeProfile.PRODUCTION, "postgresql+psycopg://unused"))
    client = TestClient(app)
    assert client.get("/api/developer/personas").status_code == 404
    assert client.get("/api/catalog", headers={"X-Demo-Persona": "demo-admin"}).status_code == 404
    with pytest.raises(RuntimeError, match="forbidden"):
        DeveloperAuthAdapter(Settings(RuntimeProfile.PRODUCTION, "postgresql+psycopg://unused"))


def test_developer_auth_only_accepts_seeded_personas() -> None:
    client = TestClient(create_app(Settings(RuntimeProfile.TEST, "postgresql+psycopg://unused")))
    assert client.get("/api/catalog", headers={"X-Demo-Persona": "invented-admin"}).status_code == 401


def test_domain_and_application_modules_do_not_import_inbound_or_sqlalchemy_adapters() -> None:
    forbidden = ("fastapi", "mcp", "sqlalchemy")
    bounded_modules = list((PACKAGE_ROOT / "modules").glob("*/domain.py")) + list(
        (PACKAGE_ROOT / "modules").glob("*/application.py")
    )

    assert bounded_modules
    for module in bounded_modules:
        source = module.read_text(encoding="utf-8")
        assert not any(f"import {dependency}" in source or f"from {dependency}" in source for dependency in forbidden), module


def test_each_work_brief_feature_owns_non_placeholder_code() -> None:
    for name in ("organization_access", "work", "reports", "meetings", "ax_execution"):
        implementation = list((PACKAGE_ROOT / "modules" / name).glob("*.py"))
        assert any(path.name not in {"__init__.py"} for path in implementation), name


def test_entrypoints_do_not_import_platform_implementations() -> None:
    for module in (PACKAGE_ROOT / "entrypoints").glob("*.py"):
        source = module.read_text(encoding="utf-8")
        assert "ax_workspace.platform" not in source, module
