from fastapi.testclient import TestClient
import pytest
from pathlib import Path

from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.http_auth import DeveloperAuthAdapter
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.mcp import DELEGATED_ACTION_CAPABILITIES
from ax_workspace.modules.actions.confirmation import ATTACHABLE_ACTION_TYPES, SUPPORTED_ACTION_TYPES
from ax_workspace.modules.actions.policy import CONFIRM_LABELS, RETIRED_ACTION_TYPES
from ax_workspace.modules.ax_execution.command_contracts import COMMAND_CONTRACTS
from ax_workspace.platform.actions import (
    CURRENT_MEETING_ACTION_TYPES,
    LEGACY_MEETING_ACTION_TYPES,
    MEETING_ACTION_TYPES,
    MEETING_CALLBACK_ACTION_TYPES,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "ax_workspace"


def test_current_and_pending_legacy_meeting_actions_have_distinct_registered_types() -> None:
    legacy_contracts = {kind for kind in COMMAND_CONTRACTS if kind.startswith("meeting.")}
    assert CURRENT_MEETING_ACTION_TYPES.isdisjoint(LEGACY_MEETING_ACTION_TYPES)
    assert "meeting.reservation.create" in CURRENT_MEETING_ACTION_TYPES
    assert legacy_contracts <= MEETING_ACTION_TYPES


def test_the_retired_meeting_creation_contract_keeps_no_executable_registration() -> None:
    """`meeting.create` was withdrawn: one public creation contract remains, and nothing may run the old one."""
    assert "meeting.create" in RETIRED_ACTION_TYPES
    # 원장 쓰기 경로도 정책으로 막는다 — registry 부재에만 기대면 retire 가 우연이 된다.
    for registry in (
        COMMAND_CONTRACTS,
        MEETING_ACTION_TYPES,
        MEETING_CALLBACK_ACTION_TYPES,
        SUPPORTED_ACTION_TYPES,
        ATTACHABLE_ACTION_TYPES,
        CONFIRM_LABELS,
        DELEGATED_ACTION_CAPABILITIES,
    ):
        assert "meeting.create" not in registry


def test_only_the_documented_workers_run_jobs_in_a_killable_child() -> None:
    """어떤 worker가 자식 프로세스 경계를 쓰는지는 문서가 말하는 것과 같아야 한다.

    이 집합이 곧 "멈춘 파서·provider를 끊을 수 있는 worker"의 목록이고, `docs/unified-operations.md`가
    그것을 사람에게 설명한다. 한쪽만 바뀌면 독자는 없는 보호를 있다고 읽게 된다.
    """
    workers = {
        path.stem
        for path in (PACKAGE_ROOT / "bootstrap").glob("*_worker.py")
        if "IsolatedWork(" in path.read_text()
    }
    assert workers == {"material_worker", "report_worker"}

    document = (PACKAGE_ROOT.parents[2] / "docs" / "unified-operations.md").read_text()
    assert "material·report worker의 blocking parser/provider 호출은 `spawn` child process group" in document
    assert "meeting worker는 이 경계를 쓰지 않는다" in document


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


def test_product_applications_are_never_reassembled_by_transport_or_persistence() -> None:
    """Approval and presentation must use the same complete services as direct commands."""
    import ast

    misplaced = []
    for directory in ("platform", "entrypoints"):
        for path in (PACKAGE_ROOT / directory).glob("*.py"):
            tree = ast.parse(path.read_text())
            imports = {
                alias.asname or alias.name: alias.name
                for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                for alias in node.names
            }
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = (
                    imports.get(node.func.id, node.func.id) if isinstance(node.func, ast.Name)
                    else node.func.attr if isinstance(node.func, ast.Attribute) else ""
                )
                if name.endswith("Application") or name == "AccessAdministration":
                    misplaced.append(f"{path.relative_to(PACKAGE_ROOT)}:{node.lineno} {name}")
    assert misplaced == [], misplaced


def test_action_composition_rejects_a_missing_service_before_any_effect() -> None:
    from dataclasses import replace
    from ax_workspace.bootstrap.application import create_workflow_application

    application = create_workflow_application(Settings(RuntimeProfile.TEST, "sqlite:///:memory:"))
    with application._session_factory() as session:
        services = application._action_services(session)
        with pytest.raises(TypeError, match="meetings"):
            replace(services, meetings=None)


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
