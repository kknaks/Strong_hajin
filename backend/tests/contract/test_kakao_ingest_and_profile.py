"""카톡 수신(기기 토큰) · 프로필 이미지 · 비밀번호 변경 계약 (SPEC-008 §4.6·§4.7 · WORK-011 BE-3)."""
from __future__ import annotations

import hashlib

from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from ax_workspace.bootstrap.seed import DEMO_PASSWORD, demo_email
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.external_channels import kakao_ingest
from ax_workspace.modules.external_channels.domain import kakao_attachment_aid
from ax_workspace.platform.external_tokens import generate_key
from ax_workspace.platform.persistence import ExternalAttachmentRecord, ExternalMessageRecord, make_session_factory

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    settings = Settings(
        RuntimeProfile.TEST,
        database_url,
        materials_dir=str(tmp_path / "materials"),
        external_token_encryption_key=generate_key(),
        external_channel_storage_dir=str(tmp_path / "external"),
    )
    app = create_app(settings)
    return TestClient(app), make_session_factory(database_url), tmp_path / "external"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _collector(client: TestClient, headers=MINA, rooms=("18200", "18300")):
    token = client.post("/api/device-tokens", headers=headers, json={"device_name": "Mac"}).json()["token"]
    handshake = client.get("/api/integrations/kakao/handshake", headers=_bearer(token)).json()
    integration = handshake["integration_id"]
    chosen = {"room_ids": list(rooms), "rooms": [{"room_id": room, "type": "group", "name": f"방 {room}"} for room in rooms]}
    assert client.post(f"/api/integrations/{integration}/rooms", headers=headers, json=chosen).status_code == 202
    handshake = client.get("/api/integrations/kakao/handshake", headers=_bearer(token)).json()
    by_chat = {row["external_id"]: row["room_id"] for row in handshake["selected_rooms"]}
    return token, integration, by_chat


def _batch(room_id: str, *logs: int, attachments=None, backfill_done=False):
    return {
        "room_id": room_id,
        "backfill_done": backfill_done,
        "messages": [
            {"logId": log, "author": "지호", "at": 1_790_000_000 + log, "type": 1, "text": f"카톡 {log}",
             "attachments": (attachments or {}).get(log, [])}
            for log in logs
        ],
    }


def test_collector_uploads_a_batch_dedupes_by_chat_and_log_and_it_shows_in_the_inbox(tmp_path) -> None:
    client, sessions, _ = _stack(tmp_path)
    token, integration, rooms = _collector(client)
    room = rooms["18200"]
    first = client.post("/api/integrations/kakao/messages", headers=_bearer(token), json=_batch(room, 101, 102, 102))
    assert first.status_code == 202, first.text
    assert first.json() == {"accepted": 2, "attachment_upload": []}
    again = client.post("/api/integrations/kakao/messages", headers=_bearer(token), json=_batch(room, 102, 103, backfill_done=True))
    assert again.json()["accepted"] == 1  # (연동, chatId, logId) 중복은 버린다
    with sessions() as session:
        keys = session.scalars(select(ExternalMessageRecord.external_key).where(ExternalMessageRecord.container_key == "18200")).all()
        assert sorted(keys) == ["101", "102", "103"]
    handshake = client.get("/api/integrations/kakao/handshake", headers=_bearer(token)).json()
    assert {row["external_id"]: row["last_logId"] for row in handshake["selected_rooms"]}["18200"] == 103
    listed = {row["room_id"]: row for row in client.get(f"/api/integrations/{integration}/rooms", headers=MINA).json()}
    assert listed[room]["synced_count"] == 3

    [card] = client.get("/api/inbox/messages", headers=MINA, params={"source": "kakao"}).json()["items"]
    assert card["room_id"] == room and card["unread_count"] == 3 and card["preview"][-1]["text"] == "카톡 103"
    page = client.get(f"/api/inbox/rooms/{room}/messages", headers=MINA).json()
    assert [row["key"] for row in page["messages"]] == ["101", "102", "103"]
    assert client.get(f"/api/inbox/rooms/{room}/messages", headers=JIHO).status_code == 404


def test_upload_to_a_room_the_server_did_not_select_is_403_and_big_batches_413(tmp_path, monkeypatch) -> None:
    client, _, _ = _stack(tmp_path)
    token, integration, rooms = _collector(client)
    other_token, _, other_rooms = _collector(client, headers=JIHO)
    url = "/api/integrations/kakao/messages"
    # 다른 회원의 방 · 없는 방 · 뺀 방 — 모두 «서버의 고른 방»이 아니다
    assert client.post(url, headers=_bearer(token), json=_batch(other_rooms["18200"], 1)).status_code == 403
    assert client.post(url, headers=_bearer(token), json=_batch("not-a-uuid", 1)).status_code == 403
    assert client.delete(f"/api/integrations/{integration}/rooms/{rooms['18300']}", headers=MINA).status_code == 204
    assert client.post(url, headers=_bearer(token), json=_batch(rooms["18300"], 1)).status_code == 403
    monkeypatch.setattr(kakao_ingest, "KAKAO_BATCH_LIMIT", 2)
    assert client.post(url, headers=_bearer(token), json=_batch(rooms["18200"], 1, 2, 3)).status_code == 413
    # 수집기 라우트는 세션·페르소나를 받지 않는다(R3-F1)
    assert client.post(url, headers=MINA, json=_batch(rooms["18200"], 1)).status_code == 401
    assert client.post("/api/integrations/kakao/status", headers=MINA, json={}).status_code == 401


def test_messages_before_handshake_are_409(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    token = client.post("/api/device-tokens", headers=MINA, json={"device_name": "Mac"}).json()["token"]
    status = {"app": {"device_name": "Mac", "version": "1.0"}, "kakao": {"state": "running"}, "account": {"name": "민아"}}
    response = client.post("/api/integrations/kakao/status", headers=_bearer(token), json=status)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "handshake_required"


def test_attachments_are_slotted_uploaded_to_host_path_and_served_from_the_saved_copy(tmp_path, monkeypatch) -> None:
    client, sessions, storage = _stack(tmp_path)
    token, integration, rooms = _collector(client)
    room = rooms["18200"]
    attachments = {
        201: [{"kind": "image", "seq": 0, "name": "a.jpg", "size": 4, "mime": "image/jpeg"},
              {"kind": "video", "seq": 1, "name": "v.mp4", "size": 9, "mime": "video/mp4"}],
        202: [{"kind": "image", "seq": 0, "name": "old.jpg", "size": 4, "mime": "image/jpeg", "expired": True}],
        203: [{"kind": "file", "seq": 0, "name": "huge.zip", "size": 60 * 1024 * 1024}],
    }
    answer = client.post("/api/integrations/kakao/messages", headers=_bearer(token), json=_batch(room, 201, 202, 203, attachments=attachments)).json()
    aid = kakao_attachment_aid(integration, "18200", 201, 0)
    assert answer["attachment_upload"] == [{"logId": 201, "seq": 0, "aid": aid}]  # 저장할 것만 — 앱과 같은 식
    # 재전송 묶음도 아직 못 올린 첨부를 다시 알려 준다
    resend = client.post("/api/integrations/kakao/messages", headers=_bearer(token), json=_batch(room, 201, attachments=attachments)).json()
    assert resend == {"accepted": 0, "attachment_upload": [{"logId": 201, "seq": 0, "aid": aid}]}

    upload = f"/api/integrations/kakao/attachments/{aid}"
    jiho_token = client.post("/api/device-tokens", headers=JIHO, json={"device_name": "Mac"}).json()["token"]
    client.get("/api/integrations/kakao/handshake", headers=_bearer(jiho_token))
    # aid 만으로 찾지 않는다 — 남의 연동 범위에서는 없는 첨부다
    assert client.post(upload, headers=_bearer(jiho_token), files={"file": ("a.jpg", b"JPEG", "image/jpeg")}).status_code == 404
    assert client.post(upload, headers=_bearer(token), files={"file": ("a.jpg", b"JPEG", "image/jpeg")}).status_code == 201
    assert (storage / "kakao" / integration / aid).read_bytes() == b"JPEG"
    # 받을 자리(pending)가 아닌 첨부는 받지 않는다(BE-2·3 검수 W-5) — 저장본 덮어쓰기 · 동영상(표시만 · D-31) · 만료
    assert client.post(upload, headers=_bearer(token), files={"file": ("a.jpg", b"OTHER", "image/jpeg")}).status_code == 409
    assert (storage / "kakao" / integration / aid).read_bytes() == b"JPEG"
    for log, seq in ((201, 1), (202, 0)):
        closed = client.post(f"/api/integrations/kakao/attachments/{kakao_attachment_aid(integration, '18200', log, seq)}",
                             headers=_bearer(token), files={"file": ("x", b"x", "video/mp4")})
        assert closed.status_code == 409 and closed.json()["detail"]["code"] == "attachment_not_accepted"
    with sessions() as session:
        stored = session.scalar(select(ExternalAttachmentRecord).where(ExternalAttachmentRecord.aid == aid))
        assert stored.state == "stored" and stored.storage_key == f"kakao/{integration}/{aid}"

    served = client.get(f"/api/inbox/rooms/{room}/attachments/{aid}", headers=MINA)
    assert served.status_code == 200 and served.content == b"JPEG" and served.headers["content-type"] == "image/jpeg"
    expired = client.get(f"/api/inbox/rooms/{room}/attachments/{kakao_attachment_aid(integration, '18200', 202, 0)}", headers=MINA)
    assert expired.status_code == 410 and expired.json()["detail"]["code"] == "expired"
    video = client.get(f"/api/inbox/rooms/{room}/attachments/{kakao_attachment_aid(integration, '18200', 201, 1)}", headers=MINA)
    assert video.status_code == 410 and video.json()["detail"]["code"] == "not_stored"
    huge = client.get(f"/api/inbox/rooms/{room}/attachments/{kakao_attachment_aid(integration, '18200', 203, 0)}", headers=MINA)
    assert huge.status_code == 410 and huge.json()["detail"]["code"] == "too_large"

    monkeypatch.setattr(kakao_ingest, "KAKAO_ATTACHMENT_LIMIT_BYTES", 3)
    assert client.post(upload, headers=_bearer(token), files={"file": ("a.jpg", b"JPEG", "image/jpeg")}).status_code == 413


def test_status_report_feeds_the_state_card_and_returns_the_room_version(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    token, integration, rooms = _collector(client)
    status = {
        "app": {"device_name": "하진의 MacBook", "version": "1.2.0"},
        "kakao": {"state": "unreadable", "reason": "permission"},
        "account": {"name": "민아"},
        "account_changed": False,
    }
    response = client.post("/api/integrations/kakao/status", headers=_bearer(token), json=status)
    assert response.status_code == 200 and response.json() == {"selected_rooms_version": 1}
    [kakao] = [row for row in client.get("/api/integrations", headers=MINA).json() if row["kind"] == "kakao"]
    assert kakao["collector"]["app_state"] == "on" and kakao["collector"]["kakao_reason"] == "permission"
    assert kakao["collector"]["device_name"] == "하진의 MacBook" and kakao["display_name"] == "민아"
    assert client.delete(f"/api/integrations/{integration}/rooms/{rooms['18300']}", headers=MINA).status_code == 204
    assert client.post("/api/integrations/kakao/status", headers=_bearer(token), json=status).json() == {"selected_rooms_version": 2}
    bad = {**status, "kakao": {"state": "dancing"}}
    assert client.post("/api/integrations/kakao/status", headers=_bearer(token), json=bad).status_code == 422


def test_collector_events_reach_the_members_inbox_stream(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    token, integration, rooms = _collector(client)
    with client.websocket_connect("/api/inbox/stream", headers=MINA) as stream:
        assert stream.receive_json() == {"type": "ready"}
        client.post("/api/integrations/kakao/messages", headers=_bearer(token), json=_batch(rooms["18200"], 7))
        event = stream.receive_json()
        assert event == {"type": "inbox.message_arrived", "integration_id": integration, "room_id": rooms["18200"],
                         "source_kind": "kakao", "data": {"count": 1}}


def test_a_live_collector_save_also_announces_the_integration_change(tmp_path) -> None:
    """한 건 저장에도 `integration.changed` 가 함께 나간다 — 설정 화면의 적재 건수·마지막 수집이 새로고침 없이 바뀐다
    (SPEC-008 §4.4 v0.6.0 · DEC-009 D-25 · WORK-012 WP1-BE). 같은 연동은 1초에 한 번으로 묶여 늦어도 1초 안에 온다."""
    client, _, _ = _stack(tmp_path)
    token, integration, rooms = _collector(client)
    with client.websocket_connect("/api/inbox/stream", headers=MINA) as stream:
        assert stream.receive_json() == {"type": "ready"}
        client.post("/api/integrations/kakao/messages", headers=_bearer(token), json=_batch(rooms["18200"], 7))
        events = [stream.receive_json(), stream.receive_json()]
        assert [event["type"] for event in events] == ["inbox.message_arrived", "integration.changed"]
        assert events[1]["integration_id"] == integration and events[1]["source_kind"] == "kakao"


# ── 프로필 ────────────────────────────────────────────────────────────────────────────────


def test_profile_image_is_saved_at_once_served_and_deleted(tmp_path) -> None:
    client, _, storage = _stack(tmp_path)
    assert client.get("/api/organization/me", headers=MINA).json()["profile_image_url"] is None
    assert client.get("/api/profile/image", headers=MINA).status_code == 404
    saved = client.put("/api/profile/image", headers=MINA, files={"file": ("me.png", PNG, "image/png")})
    assert saved.status_code == 200, saved.text
    url = saved.json()["profile_image_url"]
    assert url.startswith("/api/profile/image?v=")
    me = client.get("/api/organization/me", headers=MINA).json()
    assert me["profile_image_url"] == url and me["display_name"]  # 머리 값은 명부 그대로(읽기 전용)
    image = client.get("/api/profile/image", headers=MINA)
    assert image.content == PNG and image.headers["content-type"] == "image/png"
    assert client.get("/api/profile/image", headers=JIHO).status_code == 404
    replaced = client.put("/api/profile/image", headers=MINA, files={"file": ("me.jpg", JPG, "image/jpeg")}).json()
    assert client.get("/api/profile/image", headers=MINA).headers["content-type"] == "image/jpeg"
    # 자리 이름은 회원 id 의 SHA-256 앞 32자다 — id 에 어떤 글자가 와도 키가 깨지지 않는다(검수 W-10).
    assert len(list((storage / "profiles" / hashlib.sha256(b"mina").hexdigest()[:32]).iterdir())) == 1  # 바꾸면 옛 저장본은 지운다
    assert replaced["profile_image_url"].startswith("/api/profile/image?v=")
    assert client.delete("/api/profile/image", headers=MINA).status_code == 204
    assert client.get("/api/profile/image", headers=MINA).status_code == 404
    assert client.get("/api/organization/me", headers=MINA).json()["profile_image_url"] is None


def test_profile_image_limits(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    too_big = PNG + b"\x00" * (1024 * 1024)
    assert client.put("/api/profile/image", headers=MINA, files={"file": ("big.png", too_big, "image/png")}).status_code == 413
    # 선언이 아니라 바이트로 판정한다
    gif = client.put("/api/profile/image", headers=MINA, files={"file": ("a.png", b"GIF89a....", "image/png")})
    assert gif.status_code == 415


def _login(client: TestClient) -> TestClient:
    other = TestClient(client.app)
    assert other.post("/api/auth/login", json={"email": demo_email("mina"), "password": DEMO_PASSWORD}).status_code == 200
    return other


def test_password_change_keeps_this_session_and_signs_out_everything_else(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    here, elsewhere = _login(client), _login(client)
    token = here.post("/api/device-tokens", json={"device_name": "Mac"}).json()["token"]
    assert here.get("/api/integrations/kakao/handshake", headers=_bearer(token)).status_code == 200

    wrong = here.post("/api/profile/password", json={"current": "nope-nope", "new": "brand-new-pass"})
    assert wrong.status_code == 401 and wrong.json()["detail"]["code"] == "current_password_incorrect"
    short = here.post("/api/profile/password", json={"current": DEMO_PASSWORD, "new": "short"})
    assert short.status_code == 422 and short.json()["detail"]["code"] == "password_rejected"
    assert elsewhere.get("/api/auth/me").status_code == 200  # 실패는 아무것도 바꾸지 않았다

    assert here.post("/api/profile/password", json={"current": DEMO_PASSWORD, "new": "brand-new-pass"}).status_code == 204
    assert here.get("/api/auth/me").status_code == 200
    assert elsewhere.get("/api/auth/me").status_code == 401
    assert here.get("/api/integrations/kakao/handshake", headers=_bearer(token)).status_code == 401  # 기기 토큰도 무효
    fresh = TestClient(client.app)
    assert fresh.post("/api/auth/login", json={"email": demo_email("mina"), "password": DEMO_PASSWORD}).status_code == 401
    assert fresh.post("/api/auth/login", json={"email": demo_email("mina"), "password": "brand-new-pass"}).status_code == 200
