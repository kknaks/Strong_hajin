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


def test_work_request_application_is_composed_only_by_the_bootstrap() -> None:
    """Direct, MCP and approved effects must not drift into separately assembled WorkRequest services."""
    owners = []
    for module in PACKAGE_ROOT.rglob("*.py"):
        if "WorkRequestApplication(" in module.read_text(encoding="utf-8"):
            owners.append(module.relative_to(PACKAGE_ROOT).as_posix())

    assert owners == ["bootstrap/application.py"]


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


def test_the_browser_never_learns_the_stt_provider() -> None:
    """브라우저는 STT provider 를 모른다 — 오디오는 반드시 우리 서버의 회의 스트림을 지난다.

    SCAX-SPEC-004 §5.2-2 · §10-10. provider 주소·모델·키 이름이 프론트 소스에 있으면 직결 경로가 살아 있다는 뜻이다.
    서버 쪽에서도 `platform/soniox.py` 하나만 그 이름을 안다 — `entrypoints`·`modules` 는 상대를 모른다.
    """
    repository_root = Path(__file__).resolve().parents[3]
    forbidden = ("soniox", "stt-rt", "transcribe-websocket", "SONIOX_API_KEY")

    for source in (repository_root / "frontend" / "src").rglob("*.ts*"):
        text = source.read_text(encoding="utf-8").lower()
        assert not any(token.lower() in text for token in forbidden), source

    # 어댑터 하나와, 그 어댑터를 고르는 조립층 하나. 그 밖에서는 provider 의 이름이 나오지 않는다.
    allowed = {PACKAGE_ROOT / "platform" / "soniox.py", PACKAGE_ROOT / "bootstrap" / "application.py"}
    provider_named = {
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if any(token.lower() in path.read_text(encoding="utf-8").lower() for token in ("soniox", "stt-rt"))
    }
    assert provider_named == allowed, sorted(provider_named)
