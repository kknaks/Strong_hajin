from fastapi.testclient import TestClient
import pytest
from pathlib import Path

from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.http_auth import DeveloperAuthAdapter
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings


PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "ax_workspace"


def test_application_startup_never_mutates_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("schema mutation belongs exclusively to ax_workspace.entrypoints.reset_demo")

    monkeypatch.setattr("ax_workspace.platform.persistence.Base.metadata.create_all", fail_if_called)
    app = create_app(Settings(RuntimeProfile.TEST, "postgresql+psycopg://unused"))
    assert TestClient(app).get("/health").json()["status"] == "ok"


def test_production_has_no_developer_login_surface() -> None:
    app = create_app(Settings(RuntimeProfile.PRODUCTION, "postgresql+psycopg://unused"))
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"email": "a@b.c", "password": "x"}).status_code == 404
    assert client.get("/api/catalog", headers={"X-Demo-Persona": "yuna"}).status_code == 404
    with pytest.raises(RuntimeError, match="forbidden"):
        DeveloperAuthAdapter(Settings(RuntimeProfile.PRODUCTION, "postgresql+psycopg://unused"))


def test_production_exposes_no_persona_surface_at_all(tmp_path) -> None:
    """Not two examples: every route the production app registers, and none of them is a way in without an identity.

    The real login is Google OIDC and is not built yet. Until it is, production must have no door at all rather than
    a development one left ajar.
    """
    app = create_app(Settings(RuntimeProfile.PRODUCTION, "postgresql+psycopg://unused"))
    paths = {getattr(route, "path", "") for route in app.routes}
    assert not any("developer" in path or "persona" in path for path in paths), sorted(paths)
    # And nothing carrying work data is registered either, so a missing session cannot fall through to data.
    assert not any(path.startswith(("/api/tasks", "/api/work-requests", "/api/meetings", "/api/graph")) for path in paths)

    client = TestClient(app)
    for path in ("/api/developer/personas", "/api/my-work", "/api/graph/search?q=x"):
        assert client.get(path, headers={"X-Demo-Persona": "yuna"}).status_code == 404


def test_the_development_seam_names_a_member_and_never_invents_one(tmp_path) -> None:
    """The header says who a test is acting as. Whether that person exists and still works here is the ledger's answer."""
    from ax_workspace.entrypoints.reset_demo import reset_database

    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    client = TestClient(create_app(Settings(RuntimeProfile.TEST, database_url)))
    assert client.get("/api/my-work", headers={"X-Demo-Persona": "invented-admin"}).status_code == 403
    assert client.get("/api/my-work", headers={"X-Demo-Persona": ""}).status_code == 401
    assert client.get("/api/my-work", headers={"X-Demo-Persona": "mina"}).status_code == 200


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


def test_reports_feature_does_not_embed_provider_policy_or_spawn_processes() -> None:
    reports_sources = [
        PACKAGE_ROOT / "modules" / "reports" / "application.py",
        PACKAGE_ROOT / "platform" / "reports.py",
    ]
    forbidden = ("gpt-5.6-terra", 'service_tier="fast"', "subprocess")
    for source_path in reports_sources:
        source = source_path.read_text(encoding="utf-8")
        assert not any(token in source for token in forbidden), source_path


def test_frontend_is_the_only_canonical_ui_source_root() -> None:
    repository_root = Path(__file__).resolve().parents[3]

    assert (repository_root / "frontend" / "package.json").is_file()
    assert not (repository_root / "web").exists()
