"""Login sessions are the browser credential; the persona header stays a development seam."""
from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database


def _client(tmp_path) -> TestClient:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    return TestClient(create_app(Settings(RuntimeProfile.TEST, database_url)))


def test_login_session_cookie_authenticates_without_the_persona_header(tmp_path) -> None:
    client = _client(tmp_path)
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/my-work").status_code == 401

    login = client.post("/api/auth/login", json={"provider": "developer", "account": "mina"})
    assert login.status_code == 200
    assert login.json()["member_id"] == "mina"
    assert "scax_session" in login.cookies

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["member_id"] == "mina"
    assert client.get("/api/my-work").status_code == 200

    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/my-work").status_code == 401


def test_login_rejects_unknown_accounts_and_lists_local_accounts(tmp_path) -> None:
    client = _client(tmp_path)
    assert client.post("/api/auth/login", json={"account": "intruder"}).status_code == 401
    providers = client.get("/api/auth/providers").json()
    assert providers["developer"] is True and providers["oidc"] is False
    assert {account["id"] for account in providers["accounts"]} >= {"mina", "jiho"}


def test_persona_header_cannot_override_an_active_session(tmp_path) -> None:
    client = _client(tmp_path)
    client.post("/api/auth/login", json={"account": "mina"})
    profile = client.get("/api/organization/me", headers={"X-Demo-Persona": "jiho"}).json()
    assert profile["member_id"] == "mina"
