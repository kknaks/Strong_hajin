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
from ax_workspace.platform.persistence import (
    AssistantCharacterPreferenceRecord,
    EmploymentPeriodRecord,
    MemberCredentialRecord,
    make_session_factory,
)


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


def test_session_profile_projects_the_default_assistant_character_before_a_preference_is_saved(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    client.post("/api/auth/login", json={"email": demo_email("mina"), "password": DEMO_PASSWORD})

    assert client.get("/api/auth/me").json()["assistant_character"] == {
        "character_key": "cream-cat",
        "version": 0,
    }


def test_member_can_save_an_allowlisted_assistant_character_and_recover_it_in_a_new_session(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    credentials = {"email": demo_email("mina"), "password": DEMO_PASSWORD}
    client.post("/api/auth/login", json=credentials)

    saved = client.put(
        "/api/profile/preferences/assistant-character",
        json={"character_key": "red-panda", "expected_version": 0},
    )
    assert saved.status_code == 200
    assert saved.json() == {"character_key": "red-panda", "version": 1}

    client.post("/api/auth/logout")
    client.post("/api/auth/login", json=credentials)
    assert client.get("/api/auth/me").json()["assistant_character"] == saved.json()


def test_assistant_character_preference_rejects_unknown_keys_and_stale_versions(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    client.post("/api/auth/login", json={"email": demo_email("mina"), "password": DEMO_PASSWORD})

    unknown = client.put(
        "/api/profile/preferences/assistant-character",
        json={"character_key": "unapproved-mascot", "expected_version": 0},
    )
    assert unknown.status_code == 422

    assert client.put(
        "/api/profile/preferences/assistant-character",
        json={"character_key": "rabbit", "expected_version": 0},
    ).json() == {"character_key": "rabbit", "version": 1}
    stale = client.put(
        "/api/profile/preferences/assistant-character",
        json={"character_key": "bear", "expected_version": 0},
    )
    assert stale.status_code == 409
    assert client.get("/api/auth/me").json()["assistant_character"] == {
        "character_key": "rabbit",
        "version": 1,
    }


def test_assistant_character_preference_is_isolated_per_principal(tmp_path) -> None:
    client, _ = _stack(tmp_path)

    client.post("/api/auth/login", json={"email": demo_email("mina"), "password": DEMO_PASSWORD})
    client.put(
        "/api/profile/preferences/assistant-character",
        json={"character_key": "red-panda", "expected_version": 0},
    )
    client.post("/api/auth/logout")

    client.post("/api/auth/login", json={"email": demo_email("jiho"), "password": DEMO_PASSWORD})
    assert client.get("/api/auth/me").json()["assistant_character"] == {
        "character_key": "cream-cat",
        "version": 0,
    }
    client.put(
        "/api/profile/preferences/assistant-character",
        json={"character_key": "tuxedo-cat", "expected_version": 0},
    )
    client.post("/api/auth/logout")

    client.post("/api/auth/login", json={"email": demo_email("mina"), "password": DEMO_PASSWORD})
    assert client.get("/api/auth/me").json()["assistant_character"] == {
        "character_key": "red-panda",
        "version": 1,
    }


def test_retired_server_value_is_preserved_until_the_member_saves_a_supported_replacement(tmp_path) -> None:
    client, database_url = _stack(tmp_path)
    with make_session_factory(database_url)() as session:
        session.add(AssistantCharacterPreferenceRecord(
            member_id="mina",
            character_key="retired-fox",
            version=4,
        ))
        session.commit()
    client.post("/api/auth/login", json={"email": demo_email("mina"), "password": DEMO_PASSWORD})

    assert client.get("/api/auth/me").json()["assistant_character"] == {
        "character_key": "retired-fox",
        "version": 4,
    }
    saved = client.put(
        "/api/profile/preferences/assistant-character",
        json={"character_key": "chick", "expected_version": 4},
    )
    assert saved.json() == {"character_key": "chick", "version": 5}


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


def test_the_demo_offers_its_own_accounts_as_a_shortcut_and_production_offers_nothing(tmp_path) -> None:
    """로컬에서는 한 번에 로그인하되, 지나가는 길은 여전히 진짜 로그인이다.

    On a developer machine the page is handed the demo's own accounts and the one password `reset-demo` gave them —
    a way to skip typing. It is not a way in: the shortcut posts those credentials to the same login route.
    """
    client, database_url = _stack(tmp_path)
    providers = client.get("/api/auth/providers").json()
    assert providers["local"] is True and providers["oidc"] is False
    assert {account["member_id"] for account in providers["demo_accounts"]} == {"yuna", "jiho", "mina", "hyeon", "sora", "minseok"}
    assert {account["email"] for account in providers["demo_accounts"]} == {demo_email(member) for member in ("yuna", "jiho", "mina", "hyeon", "sora", "minseok")}
    assert providers["demo_password"] == DEMO_PASSWORD
    # 그 자격으로 실제 로그인 route를 지나야 세션이 생긴다.
    shortcut = providers["demo_accounts"][0]
    signed_in = client.post("/api/auth/login", json={"email": shortcut["email"], "password": providers["demo_password"]})
    assert signed_in.status_code == 200 and signed_in.json()["member_id"] == shortcut["member_id"]

    # 데모 도메인 밖의 계정은 목록에 오르지 않는다 — 로컬 DB에 실제 계정을 만들어도 이름이 새지 않는다.
    with make_session_factory(database_url)() as session:
        session.get(MemberCredentialRecord, "minseok").email = "real.person@example.com"
        session.commit()
    listed = client.get("/api/auth/providers").json()["demo_accounts"]
    assert "minseok" not in {account["member_id"] for account in listed}
    assert "real.person@example.com" not in str(listed)


def test_production_is_offered_no_accounts_and_no_route(tmp_path) -> None:
    """PRODUCTION 은 «무엇으로 로그인할 수 있는가»를 답하되, 지름길도 로컬 로그인도 내놓지 않는다.

    **2026-09-22 (WORK-006 Phase 6b)**: 라우트 등록이 프로파일에서 분리되면서 이 조회는 404 가 아니라
    200 이 된다. 바뀐 것은 **등록**이지 **권한**이 아니다 — 답은 여전히 「로컬 로그인 없음」이고,
    데모 계정·데모 비밀번호는 **한 줄도 실리지 않는다.**
    """
    client, _ = _stack(tmp_path, RuntimeProfile.PRODUCTION)
    providers = client.get("/api/auth/providers")
    assert providers.status_code == 200
    assert providers.json() == {"local": False, "oidc": False}
    # 지름길이 새지 않는다 — 계정 목록도 비밀번호도 없다.
    assert "demo_accounts" not in providers.json() and "demo_password" not in providers.json()
    # 로그인 route 자체가 없고, 개발 전용 표면도 없다.
    paths = {route.path for route in client.app.routes}
    assert "/api/auth/login" not in paths
    assert "/api/developer/personas" not in paths


def test_the_member_directory_puts_names_to_ids_for_anyone_signed_in(tmp_path) -> None:
    client, _ = _stack(tmp_path)
    assert client.get("/api/organization/members").status_code == 401
    client.post("/api/auth/login", json={"email": demo_email("mina"), "password": DEMO_PASSWORD})
    members = client.get("/api/organization/members").json()
    assert {member["id"] for member in members} >= {"mina", "jiho"}
    # 명부의 모양은 누구에게나 같고, 로그인만으로는 이름과 id 밖에 열리지 않는다. 인사 정보는 자리만 오고
    # 값은 비어 있다 — 응답의 모양이 사람마다 달라지면 client가 그 모양으로 권한을 추측하게 된다.
    assert all(set(member) == {"id", "display_name", "phone", "birth_date", "has_account"} for member in members)
    assert all(member["phone"] is None and member["birth_date"] is None for member in members)
    # 계정 유무는 권한이 아니라 들어올 문이 있는지다 — 명부와 같은 기준으로 누구에게나 보인다.
    assert all(member["has_account"] is True for member in members)


def test_which_domain_the_shortcut_list_shows_is_the_environment_s_to_say(tmp_path, monkeypatch) -> None:
    """조직마다 메일 도메인이 다르다 — 어느 도메인을 「바로 로그인」으로 볼지는 실행 환경이 정한다.

    The shortcut list only ever shows one domain, so that a real account in a local database is never enumerated
    beside the demo's own. Which domain that is was a constant in the code, which meant a local stack running a real
    organization's data had no way to say so. `AX_DEMO_EMAIL_DOMAIN` is that way, and the seed writes its accounts at
    the same domain the list reads — one value, one place.
    """
    monkeypatch.setenv("AX_PROFILE", RuntimeProfile.TEST)
    monkeypatch.setenv("AX_JOB_QUEUE_BACKEND", "memory")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'demo.db'}")
    monkeypatch.setenv("AX_DEMO_EMAIL_DOMAIN", "example.test")

    settings = Settings.from_environment()
    assert settings.demo_email_domain == "example.test"

    reset_database(settings.database_url)
    client = TestClient(create_app(settings))
    listed = client.get("/api/auth/providers").json()["demo_accounts"]
    assert {account["member_id"] for account in listed} == {"yuna", "jiho", "mina", "hyeon", "sora", "minseok"}
    assert all(account["email"].endswith("@example.test") for account in listed)
    # 목록은 지나가는 길일 뿐이다 — 그 자격이 실제 로그인 route를 지나야 세션이 생긴다.
    shortcut = listed[0]
    assert client.post("/api/auth/login", json={"email": shortcut["email"], "password": DEMO_PASSWORD}).status_code == 200

    # 아무 말이 없으면 제품의 예시 도메인이다.
    monkeypatch.delenv("AX_DEMO_EMAIL_DOMAIN")
    assert Settings.from_environment().demo_email_domain == "scax.example"
