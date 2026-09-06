"""세션은 브라우저의 자격이고, persona 헤더는 개발 seam으로만 남는다."""
from fastapi.testclient import TestClient

from ax_workspace.bootstrap.seed import DEMO_PASSWORD, demo_email
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database


def _client(tmp_path) -> TestClient:
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    return TestClient(create_app(Settings(RuntimeProfile.TEST, database_url)))


def _sign_in(client: TestClient, member_id: str):
    return client.post("/api/auth/login", json={"email": demo_email(member_id), "password": DEMO_PASSWORD})


def test_login_session_cookie_authenticates_without_the_persona_header(tmp_path) -> None:
    client = _client(tmp_path)
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/my-work").status_code == 401

    assert _sign_in(client, "mina").status_code == 200
    me = client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["member_id"] == "mina"
    assert client.get("/api/my-work").status_code == 200

    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/my-work").status_code == 401


def test_persona_header_cannot_override_an_active_session(tmp_path) -> None:
    client = _client(tmp_path)
    _sign_in(client, "mina")
    profile = client.get("/api/organization/me", headers={"X-Demo-Persona": "jiho"}).json()
    assert profile["member_id"] == "mina"


def test_a_revoked_session_cannot_be_reused_by_holding_on_to_the_cookie(tmp_path) -> None:
    client = _client(tmp_path)
    _sign_in(client, "mina")
    cookie = client.cookies.get("scax_session")
    client.post("/api/auth/logout")
    client.cookies.set("scax_session", cookie)
    assert client.get("/api/auth/me").status_code == 401
