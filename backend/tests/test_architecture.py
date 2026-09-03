from fastapi.testclient import TestClient
import pytest

from ax.api import create_app
from ax.auth import DeveloperAuthAdapter
from ax.settings import RuntimeProfile, Settings


def test_application_startup_never_mutates_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("schema mutation belongs exclusively to ax.reset_demo")

    monkeypatch.setattr("ax.database.Base.metadata.create_all", fail_if_called)
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

