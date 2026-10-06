"""외부 채널 연동의 기반 계약 — 연결(state)·소유 404·소프트 딜리트·고른 방·기기 토큰 범위 (SPEC-008 §4.2·§4.3·§4.6 · WORK-011 BE-1)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import io
import json
from urllib import error as urlerror
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.external_channels.application import OAuthGrant
from ax_workspace.modules.external_channels.domain import OAuthExchangeFailed, secret_digest
from ax_workspace.platform.external_tokens import FernetTokenCipher, generate_key
from slack_directory_support import FakeSlackDirectory
from ax_workspace.modules.external_channels.application import SlackRoomInfo
from ax_workspace.platform.persistence import (
    ExternalDeviceTokenRecord,
    ExternalIntegrationRecord,
    ExternalOAuthStateRecord,
    make_session_factory,
)

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
WEB = "http://127.0.0.1:5176"
API = "http://127.0.0.1:8001"


class FakeProvider:
    """상류 대역 — code 하나에 계정 하나. 실제 동의 화면은 사람만 누를 수 있다."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.grants: dict[str, OAuthGrant] = {}

    def authorize_url(self, *, state: str, redirect_uri: str) -> str:
        return f"https://consent.example/{self.kind}?state={state}&redirect_uri={redirect_uri}"

    def exchange(self, *, code: str, redirect_uri: str) -> OAuthGrant:
        assert redirect_uri == f"{API}/api/integrations/{self.kind}/callback"
        if code not in self.grants:
            raise OAuthExchangeFailed("upstream refused (400 invalid_grant)")
        return self.grants[code]


def _grant(account: str, token: str = "access-plain", refresh: str | None = "refresh-plain") -> OAuthGrant:
    return OAuthGrant(account, account, {"email": account}, token, refresh, None, "scope-a scope-b")


def _stack(tmp_path, *, providers: bool = True, **overrides):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    key = generate_key()
    settings = Settings(
        RuntimeProfile.TEST,
        database_url,
        materials_dir=str(tmp_path / "materials"),
        web_origin=WEB,
        api_origin=API,
        external_token_encryption_key=key,
        **overrides,
    )
    app = create_app(settings)
    fakes = {"mail": FakeProvider("mail"), "slack": FakeProvider("slack")}
    if providers:
        app.state.workflow_application._external_oauth = fakes
    # 슬랙 방 접근 확인(F-1)은 회원 토큰으로 슬랙에 묻는다 — 시험에서는 대역이 「누구에게나 보이는 방」으로 답한다.
    app.state.workflow_application._slack_directory = FakeSlackDirectory(
        rooms={"access-plain": [SlackRoomInfo("C111", "private", "운영", member_count=4), SlackRoomInfo("D222", "dm", "D222")]},
        default={"C1": "channel", "C999": "channel"},
    )
    return TestClient(app, follow_redirects=False), app, fakes, FernetTokenCipher(key), make_session_factory(database_url)


def _connect(client: TestClient, kind: str, headers=MINA) -> str:
    response = client.post(f"/api/integrations/{kind}/connect", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == {"authorize_url", "state"}
    return body["state"]


def _connected(client, fakes, kind: str, account: str, *, headers=MINA, code: str | None = None) -> str:
    state = _connect(client, kind, headers)
    code = code or f"code-{account}"
    fakes[kind].grants[code] = _grant(account)
    response = client.get(f"/api/integrations/{kind}/callback", params={"code": code, "state": state})
    assert response.status_code == 302, response.text
    assert response.headers["location"] == f"{WEB}/?surface=settings&tab={kind}&connect=ok"
    [row] = [row for row in client.get("/api/integrations", headers=headers).json() if row["display_name"] == account]
    return row["id"]


# ── 연결 시작 — 실제 Google 어댑터가 동의 URL 을 만든다(네트워크 없이) ──────────────────────────────


def test_gmail_connect_returns_a_consent_url_with_a_one_time_state_bound_to_the_member(tmp_path) -> None:
    client, _, _, _, sessions = _stack(
        tmp_path, providers=False, google_oauth_client_id="cid.apps.example", google_oauth_client_secret="never-printed",
    )
    state = _connect(client, "mail")
    response = client.post("/api/integrations/mail/connect", headers=MINA).json()
    url = urlsplit(response["authorize_url"])
    query = parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://accounts.google.com/o/oauth2/v2/auth"
    assert query["state"] == [response["state"]] and response["state"] != state
    assert query["client_id"] == ["cid.apps.example"]
    assert query["redirect_uri"] == [f"{API}/api/integrations/mail/callback"]
    assert query["scope"] == ["https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send"]
    assert query["access_type"] == ["offline"] and query["response_type"] == ["code"]
    assert "never-printed" not in response["authorize_url"]
    with sessions() as session:
        stored = session.scalar(select(ExternalOAuthStateRecord).where(ExternalOAuthStateRecord.state_hash == secret_digest(state)))
        assert stored.member_id == "mina" and stored.kind == "mail" and stored.consumed_at is None
        # 서버는 state 원문을 갖지 않는다 — 해시만.
        assert session.scalar(select(ExternalOAuthStateRecord).where(ExternalOAuthStateRecord.state_hash == state)) is None


def test_slack_connect_asks_for_user_token_scopes(tmp_path) -> None:
    client, *_ = _stack(tmp_path, providers=False, slack_client_id="slack-cid", slack_client_secret="never-printed")
    body = client.post("/api/integrations/slack/connect", headers=MINA).json()
    query = parse_qs(urlsplit(body["authorize_url"]).query)
    assert "files:write" in query["user_scope"][0].split(",") and "scope" not in query
    assert query["redirect_uri"] == [f"{API}/api/integrations/slack/callback"]


def test_an_unconfigured_integration_says_so_instead_of_failing(tmp_path) -> None:
    client, *_ = _stack(tmp_path, providers=False)
    response = client.post("/api/integrations/mail/connect", headers=MINA)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "integration_not_configured"
    assert client.post("/api/integrations/mail/connect").status_code == 401
    assert client.post("/api/integrations/kakao/connect", headers=MINA).status_code == 422


# ── 콜백 — state 로 회원을 찾는다(쿠키 아님) ───────────────────────────────────────────────


def test_callback_rejects_a_missing_forged_reused_expired_or_crossed_state(tmp_path) -> None:
    client, _, fakes, _, sessions = _stack(tmp_path)
    assert client.get("/api/integrations/mail/callback", params={"code": "x"}).status_code == 400
    assert client.get("/api/integrations/mail/callback", params={"code": "x", "state": "forged"}).status_code == 400

    crossed = _connect(client, "mail")
    assert client.get("/api/integrations/slack/callback", params={"code": "x", "state": crossed}).status_code == 400

    reused = _connect(client, "mail")
    fakes["mail"].grants["good"] = _grant("mina@corp.example")
    assert client.get("/api/integrations/mail/callback", params={"code": "good", "state": reused}).status_code == 302
    assert client.get("/api/integrations/mail/callback", params={"code": "good", "state": reused}).status_code == 400

    expired = _connect(client, "mail")
    with sessions() as session:
        record = session.scalar(select(ExternalOAuthStateRecord).where(ExternalOAuthStateRecord.state_hash == secret_digest(expired)))
        record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    assert client.get("/api/integrations/mail/callback", params={"code": "good", "state": expired}).status_code == 400


def test_a_fake_code_fails_the_exchange_and_burns_the_state(tmp_path, monkeypatch) -> None:
    """실물 Google 어댑터에 가짜 code — 상류가 거절하면 설정으로 돌려보내고, 그 state 는 다시 못 쓴다."""
    client, *_ = _stack(tmp_path, providers=False, google_oauth_client_id="cid", google_oauth_client_secret="secret")
    calls: list[str] = []

    def refuse(request, timeout):
        calls.append(request.full_url)
        assert b"fake-code" in request.data and timeout > 0
        raise urlerror.HTTPError(request.full_url, 400, "Bad Request", {}, io.BytesIO(json.dumps({"error": "invalid_grant"}).encode()))

    monkeypatch.setattr("ax_workspace.platform.external_oauth.urlrequest.urlopen", refuse)
    state = _connect(client, "mail")
    response = client.get("/api/integrations/mail/callback", params={"code": "fake-code", "state": state})
    assert response.status_code == 302
    assert response.headers["location"] == f"{WEB}/?surface=settings&tab=mail&connect=error"
    assert calls == ["https://oauth2.googleapis.com/token"]
    assert client.get("/api/integrations/mail/callback", params={"code": "fake-code", "state": state}).status_code == 400
    assert client.get("/api/integrations", headers=MINA).json() == []


def test_consent_denied_returns_to_settings_without_an_integration(tmp_path) -> None:
    client, *_ = _stack(tmp_path)
    state = _connect(client, "mail")
    response = client.get("/api/integrations/mail/callback", params={"error": "access_denied", "state": state})
    assert response.status_code == 302
    assert response.headers["location"] == f"{WEB}/?surface=settings&tab=mail&connect=denied"
    assert client.get("/api/integrations", headers=MINA).json() == []


def test_callback_stores_tokens_encrypted_for_the_state_owner_only(tmp_path) -> None:
    client, _, fakes, cipher, sessions = _stack(tmp_path)
    integration_id = _connected(client, fakes, "mail", "mina@corp.example")
    [row] = client.get("/api/integrations", headers=MINA).json()
    assert row == {
        "id": integration_id, "kind": "mail", "status": "backfilling", "display_name": "mina@corp.example",
        "synced_count": 0, "last_synced_at": None, "backfill_count": 0, "domain": None, "collector": None,
    }
    assert client.get("/api/integrations", headers=JIHO).json() == []
    with sessions() as session:
        record = session.get(ExternalIntegrationRecord, UUID(integration_id))
        assert record.member_id == "mina"
        assert "access-plain" not in record.access_token_encrypted
        assert cipher.decrypt(record.access_token_encrypted) == "access-plain"
        assert cipher.decrypt(record.refresh_token_encrypted) == "refresh-plain"


def test_mail_accounts_are_many_and_the_same_address_revives(tmp_path) -> None:
    client, _, fakes, _, sessions = _stack(tmp_path)
    first = _connected(client, fakes, "mail", "mina@corp.example")
    second = _connected(client, fakes, "mail", "mina@side.example")
    assert first != second  # 메일은 둘째 계정이 409 로 막히지 않는다(N-1)

    assert client.post(f"/api/integrations/{first}/disconnect", headers=MINA).status_code == 204
    assert [row["id"] for row in client.get("/api/integrations", headers=MINA).json()] == [second]
    with sessions() as session:
        record = session.get(ExternalIntegrationRecord, UUID(first))
        assert record.status == "removed" and record.removed_at is not None  # 소프트 딜리트 — 행은 남는다
        assert record.access_token_encrypted is None

    revived = _connected(client, fakes, "mail", "mina@corp.example", code="code-again")
    assert revived == first  # 같은 주소 = 예전 것을 되살린다(D-46)


def test_slack_is_one_workspace_per_member(tmp_path) -> None:
    client, _, fakes, _, _ = _stack(tmp_path)
    _connected(client, fakes, "slack", "T0001")
    assert client.post("/api/integrations/slack/connect", headers=MINA).status_code == 409
    assert client.post("/api/integrations/slack/connect", headers=JIHO).status_code == 200


def test_reconnect_and_disconnect_hide_someone_elses_integration(tmp_path) -> None:
    client, _, fakes, _, _ = _stack(tmp_path)
    integration_id = _connected(client, fakes, "mail", "mina@corp.example")
    for path in ("reconnect", "disconnect"):
        assert client.post(f"/api/integrations/{integration_id}/{path}", headers=JIHO).status_code == 404
    reconnect = client.post(f"/api/integrations/{integration_id}/reconnect", headers=MINA)
    assert reconnect.status_code == 200 and set(reconnect.json()) == {"authorize_url", "state"}
    assert client.post(f"/api/integrations/{UUID(int=7)}/reconnect", headers=MINA).status_code == 404


# ── 고른 방 — 서버 정본 · 소프트 딜리트 · 버전 ───────────────────────────────────────────────


def test_slack_rooms_are_stored_once_hidden_from_others_and_soft_removed(tmp_path) -> None:
    client, _, fakes, _, sessions = _stack(tmp_path)
    integration_id = _connected(client, fakes, "slack", "T0001")
    rooms = f"/api/integrations/{integration_id}/rooms"
    added = client.post(
        rooms, headers=MINA,
        json={"room_ids": ["C111", "D222"], "rooms": [{"room_id": "C111", "type": "private", "name": "운영", "member_count": 4}]},
    )
    assert added.status_code == 202
    listed = client.get(rooms, headers=MINA).json()
    assert [(row["external_id"], row["type"], row["name"], row["status"]) for row in listed] == [
        ("C111", "private", "운영", "backfilling"),
        ("D222", "dm", "D222", "backfilling"),
    ]
    with sessions() as session:
        assert session.get(ExternalIntegrationRecord, UUID(integration_id)).selected_rooms_version == 1
    # 이미 고른 방은 다시 붙지 않고 버전도 그대로다.
    assert client.post(rooms, headers=MINA, json={"room_ids": ["C111"]}).status_code == 202
    assert len(client.get(rooms, headers=MINA).json()) == 2
    with sessions() as session:
        assert session.get(ExternalIntegrationRecord, UUID(integration_id)).selected_rooms_version == 1

    assert client.get(rooms, headers=JIHO).status_code == 404
    assert client.post(rooms, headers=JIHO, json={"room_ids": ["C999"]}).status_code == 404
    room_id = listed[0]["room_id"]
    assert client.delete(f"{rooms}/{room_id}", headers=JIHO).status_code == 404
    assert client.delete(f"{rooms}/{room_id}", headers=MINA).status_code == 204
    assert client.delete(f"{rooms}/{room_id}", headers=MINA).status_code == 404
    assert [row["external_id"] for row in client.get(rooms, headers=MINA).json()] == ["D222"]
    # 다시 고르면 같은 room_id 로 되살아난다.
    client.post(rooms, headers=MINA, json={"room_ids": ["C111"]})
    assert room_id in {row["room_id"] for row in client.get(rooms, headers=MINA).json()}
    with sessions() as session:
        assert session.get(ExternalIntegrationRecord, UUID(integration_id)).selected_rooms_version == 3


def test_mail_has_no_rooms_and_a_wrong_room_kind_is_refused(tmp_path) -> None:
    client, _, fakes, _, _ = _stack(tmp_path)
    mail = _connected(client, fakes, "mail", "mina@corp.example")
    slack = _connected(client, fakes, "slack", "T0001")
    assert client.post(f"/api/integrations/{mail}/rooms", headers=MINA, json={"room_ids": ["x"]}).status_code == 422
    # 슬랙은 클라이언트가 보낸 종류를 믿지 않는다 — 그 회원 토큰으로 슬랙이 답한 종류를 쓴다(F-1).
    lying = {"room_ids": ["C1"], "rooms": [{"room_id": "C1", "type": "group", "name": "가짜"}]}
    assert client.post(f"/api/integrations/{slack}/rooms", headers=MINA, json=lying).status_code == 202
    [room] = client.get(f"/api/integrations/{slack}/rooms", headers=MINA).json()
    assert (room["type"], room["name"]) == ("channel", "C1")
    # 그 회원이 볼 수 없는 방(남의 DM)은 고를 수 없다.
    assert client.post(f"/api/integrations/{slack}/rooms", headers=MINA, json={"room_ids": ["D-someone-else"]}).status_code == 422
    assert client.post(f"/api/integrations/{slack}/rooms", headers=MINA, json={"room_ids": []}).status_code == 422


# ── 기기 토큰 — 해시만 · 범위 = 수집기 라우트 · 새 발급이 옛 것을 철회 ──────────────────────────────


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_device_token_is_shown_once_stored_as_a_hash_and_replaced_by_a_new_issue(tmp_path) -> None:
    client, app, _, _, sessions = _stack(tmp_path)
    issued = client.post("/api/device-tokens", headers=MINA, json={"device_name": "하진의 MacBook"})
    assert issued.status_code == 201 and issued.headers["cache-control"] == "no-store"
    first = issued.json()["token"]
    assert set(issued.json()) == {"token"}
    with sessions() as session:
        [record] = session.scalars(select(ExternalDeviceTokenRecord)).all()
        assert record.token_hash == secret_digest(first) and first not in record.token_hash
    listed = client.get("/api/device-tokens", headers=MINA).json()
    assert [row["device_name"] for row in listed] == ["하진의 MacBook"] and "token" not in listed[0]
    assert client.get("/api/integrations/kakao/handshake", headers=_bearer(first)).status_code == 200
    assert client.get("/api/device-tokens", headers=MINA).json()[0]["last_used_at"] is not None

    second = client.post("/api/device-tokens", headers=MINA, json={"device_name": "새 Mac"}).json()["token"]
    assert client.get("/api/integrations/kakao/handshake", headers=_bearer(first)).status_code == 401  # D-45
    assert client.get("/api/integrations/kakao/handshake", headers=_bearer(second)).status_code == 200

    # 비밀번호 변경·회원 비활성 때 부를 일괄 철회(R3-F1 ⑤ — BE-3 비밀번호 API 가 부른다).
    assert app.state.workflow_application.revoke_member_device_tokens("mina") == 1
    assert client.get("/api/integrations/kakao/handshake", headers=_bearer(second)).status_code == 401


def test_device_token_scope_is_the_collector_routes_only(tmp_path) -> None:
    client, *_ = _stack(tmp_path)
    token = client.post("/api/device-tokens", headers=MINA, json={"device_name": "Mac"}).json()["token"]
    # 웹 라우트는 Bearer 를 보지 않는다 — 토큰 하나가 전체 계정 권한이 되지 않는다.
    assert client.get("/api/integrations", headers=_bearer(token)).status_code == 401
    assert client.get("/api/my-work", headers=_bearer(token)).status_code == 401
    assert client.post("/api/device-tokens", headers=_bearer(token), json={"device_name": "x"}).status_code == 401
    assert client.post("/api/integrations/kakao/reset-account", headers=_bearer(token)).status_code == 401
    # 수집기 라우트는 세션·페르소나를 받지 않는다.
    assert client.get("/api/integrations/kakao/handshake", headers=MINA).status_code == 401
    assert client.get("/api/integrations/kakao/handshake", headers=_bearer("axdt_unknown")).status_code == 401
    assert client.get("/api/integrations/kakao/handshake", headers={"Authorization": f"Basic {token}"}).status_code == 401


def test_device_token_revocation_is_owner_only_and_immediate(tmp_path) -> None:
    client, *_ = _stack(tmp_path)
    token = client.post("/api/device-tokens", headers=MINA, json={"device_name": "Mac"}).json()["token"]
    [row] = client.get("/api/device-tokens", headers=MINA).json()
    assert client.delete(f"/api/device-tokens/{row['id']}", headers=JIHO).status_code == 404
    assert client.delete(f"/api/device-tokens/{row['id']}", headers=MINA).status_code == 204
    assert client.get("/api/integrations/kakao/handshake", headers=_bearer(token)).status_code == 401
    assert client.get("/api/device-tokens", headers=MINA).json() == []
    assert client.delete(f"/api/device-tokens/{row['id']}", headers=MINA).status_code == 404


# ── 카톡 — 첫 handshake 가 연동을 만든다 · chatId 를 내려준다 · reset-account 는 웹 ─────────────────


def test_first_handshake_creates_the_kakao_integration_and_returns_chat_ids(tmp_path) -> None:
    client, _, _, _, sessions = _stack(tmp_path)
    token = client.post("/api/device-tokens", headers=MINA, json={"device_name": "Mac"}).json()["token"]
    first = client.get("/api/integrations/kakao/handshake", headers=_bearer(token)).json()
    assert first["selected_rooms"] == [] and first["reset_at"] is None and first["selected_rooms_version"] == 0
    again = client.get("/api/integrations/kakao/handshake", headers=_bearer(token)).json()
    assert again["integration_id"] == first["integration_id"]  # 회원당 하나
    [kakao] = client.get("/api/integrations", headers=MINA).json()
    assert kakao["kind"] == "kakao" and kakao["status"] == "connected"
    assert kakao["collector"]["app_state"] == "off"  # 아직 status 보고가 없다 — 90초 규칙

    rooms = f"/api/integrations/{first['integration_id']}/rooms"
    # 카톡 방 «목록»은 서버가 모른다 — 고른 쪽이 종류를 함께 보낸다.
    assert client.post(rooms, headers=MINA, json={"room_ids": ["18200"]}).status_code == 422
    chosen = {"room_ids": ["18200", "18300"], "rooms": [
        {"room_id": "18200", "type": "group", "name": "팀방", "member_count": 6},
        {"room_id": "18300", "type": "direct", "name": "지호"},
    ]}
    assert client.post(rooms, headers=MINA, json=chosen).status_code == 202
    handshake = client.get("/api/integrations/kakao/handshake", headers=_bearer(token)).json()
    assert handshake["selected_rooms_version"] == 1
    assert [(row["external_id"], row["last_logId"]) for row in handshake["selected_rooms"]] == [("18200", None), ("18300", None)]
    listed = client.get(rooms, headers=MINA).json()
    assert {row["room_id"] for row in listed} == {row["room_id"] for row in handshake["selected_rooms"]}
    assert {row["status"] for row in listed} == {"paused"}  # 수집기가 말이 없으면 멈춤으로 보인다(W-1)

    # 웹(세션)에서 계정이 바뀐 뒤 「다시 연결」— 옛 방은 소프트 딜리트, reset_at 이 찍힌다.
    assert client.post("/api/integrations/kakao/reset-account", headers=JIHO).status_code == 404
    assert client.post("/api/integrations/kakao/reset-account", headers=MINA).status_code == 204
    after = client.get("/api/integrations/kakao/handshake", headers=_bearer(token)).json()
    assert after["selected_rooms"] == [] and after["reset_at"] is not None
    assert after["selected_rooms_version"] == 2
    assert client.get(rooms, headers=MINA).json() == []
    with sessions() as session:
        record = session.get(ExternalIntegrationRecord, UUID(first["integration_id"]))
        assert record.member_id == "mina" and record.account_key == "kakao"


def test_handshake_belongs_to_the_token_owner_not_to_whoever_calls(tmp_path) -> None:
    client, *_ = _stack(tmp_path)
    mina = client.post("/api/device-tokens", headers=MINA, json={"device_name": "Mac"}).json()["token"]
    jiho = client.post("/api/device-tokens", headers=JIHO, json={"device_name": "Mac"}).json()["token"]
    a = client.get("/api/integrations/kakao/handshake", headers=_bearer(mina)).json()
    b = client.get("/api/integrations/kakao/handshake", headers=_bearer(jiho)).json()
    assert a["integration_id"] != b["integration_id"]
    assert client.get(f"/api/integrations/{a['integration_id']}/rooms", headers=JIHO).status_code == 404


@pytest.mark.parametrize("kind", ["mail", "slack"])
def test_reconnect_is_for_oauth_kinds_and_kakao_is_refused(tmp_path, kind) -> None:
    client, _, fakes, _, _ = _stack(tmp_path)
    _connected(client, fakes, kind, f"acct-{kind}")
    token = client.post("/api/device-tokens", headers=MINA, json={"device_name": "Mac"}).json()["token"]
    kakao = client.get("/api/integrations/kakao/handshake", headers=_bearer(token)).json()["integration_id"]
    assert client.post(f"/api/integrations/{kakao}/reconnect", headers=MINA).status_code == 422


# ── BE-1 검수 WARN (BE 수정 판 1) ─────────────────────────────────────────────────────────────


def test_callback_refuses_a_link_opened_in_someone_elses_logged_in_browser(tmp_path) -> None:
    """W-2 — 지호가 만든 연결 링크를 민아가 자기 로그인 브라우저에서 동의해도 지호의 연동으로 들어가지 않는다."""
    from ax_workspace.bootstrap.seed import DEMO_PASSWORD, demo_email

    client, _, fakes, _, _ = _stack(tmp_path)
    state = _connect(client, "mail", JIHO)
    fakes["mail"].grants["mina-code"] = _grant("mina@corp.example")
    assert client.post("/api/auth/login", json={"email": demo_email("mina"), "password": DEMO_PASSWORD}).status_code == 200
    refused = client.get("/api/integrations/mail/callback", params={"code": "mina-code", "state": state})
    assert refused.status_code == 400
    assert client.get("/api/integrations", headers=JIHO).json() == []
    # 그 링크는 거기서 끝났다 — 쿠키 없는 브라우저(데스크톱이 연 OS 브라우저)로 다시 들이밀어도 안 된다.
    client.cookies.clear()
    assert client.get("/api/integrations/mail/callback", params={"code": "mina-code", "state": state}).status_code == 400
    # 같은 회원의 브라우저라면(웹 흐름) · 쿠키가 없으면(데스크톱 흐름) 종전대로 된다.
    own = _connect(client, "mail", JIHO)
    fakes["mail"].grants["jiho-code"] = _grant("jiho@corp.example")
    assert client.get("/api/integrations/mail/callback", params={"code": "jiho-code", "state": own}).status_code == 302


def test_oauth_grants_never_print_their_tokens() -> None:
    """W-3 — 토큰을 담은 값은 repr 에 토큰이 없다."""
    from ax_workspace.modules.external_channels.inbox import RefreshedToken
    from ax_workspace.modules.external_channels.sync import IntegrationState

    printed = repr(_grant("a@b.example", token="ya29.secret-access", refresh="1//secret-refresh"))
    assert "secret-access" not in printed and "secret-refresh" not in printed and "a@b.example" in printed
    assert "secret" not in repr(RefreshedToken("ya29.secret", None))
    state = IntegrationState("i", "m", "mail", "connected", "a", "enc-secret", "enc-secret-2", {}, None, None, None, None, None, None)
    assert "enc-secret" not in repr(state)


def test_a_bad_encryption_key_or_a_missing_production_key_stops_the_boot(tmp_path) -> None:
    """W-4 — 요청마다 500 이 아니라 부팅 실패. API origin 도 웹 origin 과 같은 검사."""
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    with pytest.raises(ValueError):
        create_app(Settings(RuntimeProfile.TEST, database_url, external_token_encryption_key="not-a-fernet-key"))
    with pytest.raises(RuntimeError, match="AX_EXTERNAL_TOKEN_ENCRYPTION_KEY"):
        create_app(Settings(RuntimeProfile.PRODUCTION, database_url, google_oauth_client_id="c", google_oauth_client_secret="s"))
    create_app(Settings(RuntimeProfile.PRODUCTION, database_url))  # 외부 연동을 안 쓰는 운영은 그대로 뜬다
    with pytest.raises(ValueError, match="AX_API_ORIGIN"):
        Settings(RuntimeProfile.TEST, database_url, api_origin="https://ax.example/api")


def test_disconnecting_kakao_revokes_the_collector_so_handshake_cannot_revive_it(tmp_path) -> None:
    """W-5 — 카톡 연결 해제는 기기 토큰도 철회한다. 방을 뺀 것도 handshake 가 되살리지 않는다."""
    client, *_ = _stack(tmp_path)
    token = client.post("/api/device-tokens", headers=MINA, json={"device_name": "Mac"}).json()["token"]
    kakao = client.get("/api/integrations/kakao/handshake", headers=_bearer(token)).json()["integration_id"]
    rooms = f"/api/integrations/{kakao}/rooms"
    client.post(rooms, headers=MINA, json={"room_ids": ["1", "2"], "rooms": [{"room_id": "1", "type": "group"}, {"room_id": "2", "type": "direct"}]})
    [first, _] = client.get(rooms, headers=MINA).json()
    assert client.delete(f"{rooms}/{first['room_id']}", headers=MINA).status_code == 204
    again = client.get("/api/integrations/kakao/handshake", headers=_bearer(token)).json()
    assert [row["external_id"] for row in again["selected_rooms"]] == ["2"]  # 뺀 방은 돌아오지 않는다

    assert client.post(f"/api/integrations/{kakao}/disconnect", headers=MINA).status_code == 204
    assert client.get("/api/integrations/kakao/handshake", headers=_bearer(token)).status_code == 401
    assert client.get("/api/integrations", headers=MINA).json() == []
    assert client.get("/api/device-tokens", headers=MINA).json() == []


def test_a_device_token_of_an_inactive_member_is_refused(tmp_path) -> None:
    """W-9 ② — 회원이 비활성이 되면 그 기기 토큰도 통하지 않는다(401)."""
    from ax_workspace.platform.persistence import MemberRecord

    client, _, _, _, sessions = _stack(tmp_path)
    token = client.post("/api/device-tokens", headers=MINA, json={"device_name": "Mac"}).json()["token"]
    assert client.get("/api/integrations/kakao/handshake", headers=_bearer(token)).status_code == 200
    with sessions() as session:
        session.get(MemberRecord, "mina").employment_state = "inactive"
        session.commit()
    assert client.get("/api/integrations/kakao/handshake", headers=_bearer(token)).status_code == 401


def test_racing_first_handshakes_reread_the_winner_instead_of_failing(tmp_path, monkeypatch) -> None:
    """W-7 — 동시 첫 handshake 의 진 쪽은 유니크 위반을 잡고 이긴 쪽 연동을 다시 읽는다."""
    from sqlalchemy.exc import IntegrityError

    from ax_workspace.modules.external_channels.application import ExternalChannelApplication

    client, *_ = _stack(tmp_path)
    token = client.post("/api/device-tokens", headers=MINA, json={"device_name": "Mac"}).json()["token"]
    original = ExternalChannelApplication.kakao_handshake
    lost = {"once": False}

    def lose_once(self, member_id):
        if not lost["once"]:
            lost["once"] = True
            original(self, member_id)  # 이긴 쪽이 만든 것처럼 — 그런데 이 트랜잭션은 유니크 위반으로 진다
            raise IntegrityError("insert", {}, Exception("uq_external_integrations_member_account"))
        return original(self, member_id)

    monkeypatch.setattr(ExternalChannelApplication, "kakao_handshake", lose_once)
    answer = client.get("/api/integrations/kakao/handshake", headers=_bearer(token))
    assert answer.status_code == 200 and lost["once"]


def test_spent_oauth_states_are_purged_after_a_day(tmp_path) -> None:
    """W-8 — 소비·만료된 state 는 연동 워커의 주기 일이 지운다."""
    from ax_workspace.platform.external_channels_sync_store import SqlAlchemyExternalChannelsSyncStore

    client, _, _, _, sessions = _stack(tmp_path)
    old, fresh = _connect(client, "mail"), _connect(client, "mail")
    with sessions() as session:
        record = session.scalar(select(ExternalOAuthStateRecord).where(ExternalOAuthStateRecord.state_hash == secret_digest(old)))
        record.expires_at = datetime.now(UTC) - timedelta(days=2)
        session.commit()
    from datetime import timedelta as _td

    assert SqlAlchemyExternalChannelsSyncStore(sessions).purge_spent_oauth_states(older_than=_td(days=1)) == 1
    with sessions() as session:
        assert session.scalar(select(ExternalOAuthStateRecord).where(ExternalOAuthStateRecord.state_hash == secret_digest(fresh)))


def test_a_slack_app_with_token_rotation_fails_the_connection(tmp_path, monkeypatch) -> None:
    """BE-2·3 검수 W-7 — 로테이션이 켜진 슬랙 앱은 12시간 뒤 전부 끊긴다. 연결을 「실패」로 돌린다."""
    client, *_ = _stack(tmp_path, providers=False, slack_client_id="cid", slack_client_secret="secret")

    class Answer(io.BytesIO):
        headers: dict = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    body = {"ok": True, "team": {"id": "T1", "name": "Medi"},
            "authed_user": {"id": "U1", "access_token": "xoxe.xoxp-1", "refresh_token": "xoxe-1", "expires_in": 43200}}
    monkeypatch.setattr("ax_workspace.platform.external_oauth.urlrequest.urlopen", lambda request, timeout: Answer(json.dumps(body).encode()))
    state = _connect(client, "slack")
    response = client.get("/api/integrations/slack/callback", params={"code": "c", "state": state})
    assert response.headers["location"].endswith("connect=error")
    assert client.get("/api/integrations", headers=MINA).json() == []


# ── 운영 bad_client_secret (BE 수정 판 7) ─────────────────────────────────────────────────────


def test_slack_token_exchange_sends_the_standard_form_with_the_authorize_redirect(tmp_path, monkeypatch, caplog) -> None:
    """교환 = `oauth.v2.access` POST form(code·client_id·client_secret·redirect_uri) · redirect_uri 는 동의 URL 과 같은 값.
    슬랙이 비밀값을 거절하면 무엇을 확인할지 로그에 남긴다 — 비밀값 원문은 남기지 않는다."""
    from urllib.parse import parse_qs

    client, *_ = _stack(tmp_path, providers=False, slack_client_id="1234567.890", slack_client_secret="s3cr3t-value-0000")
    sent: list = []

    class Answer(io.BytesIO):
        headers: dict = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def urlopen(request, timeout):
        sent.append((request.full_url, request.get_method(), request.headers, parse_qs(request.data.decode())))
        return Answer(json.dumps({"ok": False, "error": "bad_client_secret"}).encode())

    monkeypatch.setattr("ax_workspace.platform.external_oauth.urlrequest.urlopen", urlopen)
    started = client.post("/api/integrations/slack/connect", headers=MINA).json()
    authorize_redirect = parse_qs(urlsplit(started["authorize_url"]).query)["redirect_uri"][0]
    with caplog.at_level("WARNING"):
        response = client.get("/api/integrations/slack/callback", params={"code": "real-code", "state": started["state"]})
    assert response.headers["location"].endswith("connect=error")
    [(url, method, headers, form)] = sent
    assert (url, method) == ("https://slack.com/api/oauth.v2.access", "POST")
    assert headers["Content-type"] == "application/x-www-form-urlencoded"
    assert form == {"code": ["real-code"], "client_id": ["1234567.890"], "client_secret": ["s3cr3t-value-0000"],
                    "redirect_uri": [authorize_redirect]}
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "bad_client_secret" in logged and "Client Secret" in logged and "client_id=123456" in logged
    assert "s3cr3t-value-0000" not in logged  # 원문은 어디에도 없다 — 지문(sha256 앞 8자)만


def test_oauth_credentials_from_k8s_style_env_are_unquoted_and_trimmed(monkeypatch) -> None:
    """k8s `--from-env-file`·Secret 은 따옴표·끝 줄바꿈을 그대로 싣는다 — 같은 파일이 운영에서 다른 값이 되지 않게."""
    monkeypatch.setenv("SLACK_CLIENT_ID", ' "1234567.890" ')
    monkeypatch.setenv("SLACK_CLIENT_SECRET", "abc123\n")
    monkeypatch.setenv("SLACK_APP_TOKEN", "'xapp-1-x'")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", '"g-secret"\r\n')
    settings = Settings.from_environment()
    assert settings.slack_client_id == "1234567.890" and settings.slack_client_secret == "abc123"
    assert settings.slack_app_token == "xapp-1-x" and settings.google_oauth_client_secret == "g-secret"
