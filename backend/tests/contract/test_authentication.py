"""로그인은 누구인지만 증명한다. 무엇을 할 수 있는지는 원장이 정한다.

The browser signs in with an ordinary email and password. Nothing about that login carries a privilege: the session
resolves to a member, and the Organization & Access ledger decides the rest. A failed attempt says the same thing
whatever was wrong with it, so trying addresses never reveals who works here.
"""
from fastapi.testclient import TestClient
from sqlalchemy import select

from ax_workspace.bootstrap.seed import DEMO_PASSWORD, demo_email
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.organization_access.credentials import hash_password, verify_password
from ax_workspace.platform.persistence import EmploymentPeriodRecord, MemberCredentialRecord, make_session_factory


def _stack(tmp_path, profile: RuntimeProfile = RuntimeProfile.TEST):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    return TestClient(create_app(Settings(profile, database_url))), database_url


def test_signing_in_with_an_email_and_password_opens_a_session_that_carries_the_work(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    assert client.get("/api/auth/me").status_code == 401

    login = client.post("/api/auth/login", json={"email": demo_email("mina").upper(), "password": DEMO_PASSWORD})
    assert login.status_code == 200
    assert login.json()["member_id"] == "mina"
    assert "scax_session" in login.cookies

    assert client.get("/api/auth/me").json()["member_id"] == "mina"
    assert client.get("/api/my-work").status_code == 200

    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/my-work").status_code == 401


def test_every_refused_sign_in_says_the_same_thing(tmp_path) -> None:
    client, database_url = _stack(tmp_path)
    with make_session_factory(database_url)() as session:
        employment = session.scalar(select(EmploymentPeriodRecord).where(EmploymentPeriodRecord.member_id == "sora"))
        employment.state = "ended"
        session.commit()

    attempts = [
        {"email": demo_email("mina"), "password": "wrong-password"},
        {"email": "nobody@scax.example", "password": DEMO_PASSWORD},
        # A real address whose membership has ended must not be distinguishable from one that never existed.
        {"email": demo_email("sora"), "password": DEMO_PASSWORD},
    ]
    answers = {(response.status_code, response.json()["detail"]) for response in (client.post("/api/auth/login", json=attempt) for attempt in attempts)}
    assert answers == {(401, "이메일 또는 비밀번호가 올바르지 않습니다.")}
    assert client.get("/api/auth/me").status_code == 401


def test_the_password_itself_is_never_stored(tmp_path) -> None:
    _, database_url = _stack(tmp_path)
    with make_session_factory(database_url)() as session:
        stored = session.get(MemberCredentialRecord, "mina").password_hash
    assert DEMO_PASSWORD not in stored
    assert stored.startswith("pbkdf2_sha256$")
    assert verify_password(DEMO_PASSWORD, stored) and not verify_password(DEMO_PASSWORD + " ", stored)
    # Two people who chose the same password do not share a digest.
    assert hash_password(DEMO_PASSWORD) != stored


def test_the_login_route_does_not_exist_in_production(tmp_path) -> None:
    client, _ = _stack(tmp_path, RuntimeProfile.PRODUCTION)
    assert client.post("/api/auth/login", json={"email": demo_email("mina"), "password": DEMO_PASSWORD}).status_code == 404
    paths = {route.path for route in client.app.routes}
    assert "/api/auth/login" not in paths


def test_the_sign_in_page_is_never_told_who_has_an_account(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    providers = client.get("/api/auth/providers").json()
    assert providers == {"local": True, "oidc": False}
    assert "/api/developer/personas" not in {route.path for route in client.app.routes}


def test_the_member_directory_puts_names_to_ids_for_anyone_signed_in(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    assert client.get("/api/organization/members").status_code == 401
    client.post("/api/auth/login", json={"email": demo_email("mina"), "password": DEMO_PASSWORD})
    members = client.get("/api/organization/members").json()
    assert {member["id"] for member in members} >= {"mina", "jiho"}
    assert all(set(member) == {"id", "display_name"} for member in members)
