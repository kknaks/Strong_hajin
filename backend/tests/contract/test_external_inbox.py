"""메시지함·첨부·이미지 프록시·답장·사용자 WS 계약 (SPEC-008 §4.4 · WORK-011 BE-3).

수집(BE-2)은 이 시험의 몫이 아니다 — 연동·방·원문은 BE-1 표에 직접 심고, 상류(Gmail·슬랙·원격 이미지)는 대역으로 바꾼다.
"""
from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from email import message_from_bytes
from email.policy import default as default_policy
import hashlib
import json
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from ax_workspace.modules.external_channels import inbox as inbox_module
from ax_workspace.modules.external_channels.inbox import RefreshedToken, UpstreamFailed, UpstreamUnauthorized
from ax_workspace.platform.external_tokens import FernetTokenCipher, generate_key
from ax_workspace.platform.persistence import (
    ExternalAttachmentRecord,
    ExternalIntegrationRecord,
    ExternalMessageRecord,
    ExternalRoomRecord,
    ExternalSentReplyRecord,
    make_session_factory,
)

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


HOSTILE_HTML = (
    '<html><head><script>steal()</script><style>p{color:red}</style></head>'
    '<body onload="steal()"><p onclick="x()">안녕하세요</p>'
    '<img src="https://tracker.example/pixel.gif"><img src="cid:logo@mail">'
    '<form action="https://evil"><input name="pw"></form><iframe src="https://evil"></iframe>'
    '<object data="x"></object><a href="javascript:alert(1)">나쁜 링크</a><a href="https://ok.example">좋은 링크</a>'
    '<div class="gmail_quote"><blockquote>예전 메일</blockquote></div></body></html>'
)


def gmail_raw(message_id: str, *, subject: str = "견적 문의", html: str = HOSTILE_HTML) -> dict:
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "snippet": "안녕하세요",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "From", "value": "Partner <partner@example.com>"},
                {"name": "To", "value": "mina@company.example, Lee <lee@example.com>"},
                {"name": "Cc", "value": "cc@example.com"},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Wed, 1 Oct 2026 09:00:00 +0900"},
                {"name": "Message-ID", "value": f"<{message_id}@mail.example>"},
            ],
            "parts": [
                {"partId": "0", "mimeType": "text/html", "filename": "", "body": {"data": _b64(html)}},
                {"partId": "1", "mimeType": "image/png", "filename": "",
                 "headers": [{"name": "Content-ID", "value": "<logo@mail>"}], "body": {"attachmentId": "ATT-logo-old", "size": 10}},
                {"partId": "2", "mimeType": "application/pdf", "filename": "견적서.pdf", "body": {"attachmentId": "ATT-pdf-old", "size": 5}},
            ],
        },
    }


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    reset_database(database_url)
    key = generate_key()
    settings = Settings(
        RuntimeProfile.TEST,
        database_url,
        materials_dir=str(tmp_path / "materials"),
        external_token_encryption_key=key,
        external_channel_storage_dir=str(tmp_path / "external"),
        google_oauth_client_id="cid",
        google_oauth_client_secret="secret",
    )
    app = create_app(settings)
    workflow = app.state.workflow_application
    fakes = {"mail": FakeGmail(), "slack": FakeSlack(), "images": FakeImages()}
    workflow._inbox_mail_api = fakes["mail"]
    workflow._inbox_slack_api = fakes["slack"]
    workflow._inbox_image_fetcher = fakes["images"]
    return TestClient(app), app, fakes, FernetTokenCipher(key), make_session_factory(database_url)


class FakeGmail:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, str | None]] = []
        self.fail_send: Exception | None = None
        self.tokens_seen: list[str] = []
        self.reject_token: str | None = None

    def refresh(self, refresh_token: str) -> RefreshedToken:
        assert refresh_token == "refresh-plain"
        return RefreshedToken("access-refreshed", datetime.now(UTC) + timedelta(hours=1))

    def get_message(self, access_token: str, message_id: str) -> dict:
        self.tokens_seen.append(access_token)
        if access_token == self.reject_token:
            raise UpstreamUnauthorized("http_401")
        fresh = gmail_raw(message_id)
        fresh["payload"]["parts"][2]["body"]["attachmentId"] = "ATT-pdf-fresh"
        return fresh

    def attachment(self, access_token: str, message_id: str, attachment_id: str) -> bytes:
        assert attachment_id == "ATT-pdf-fresh"  # 받을 때마다 바뀌는 id — partId 로 다시 찾았다
        return b"%PDF-1.7 bytes"

    def send(self, access_token: str, raw_message: bytes, thread_id: str | None) -> dict:
        if self.fail_send:
            raise self.fail_send
        self.sent.append((raw_message, thread_id))
        return {"id": "sent-1", "threadId": thread_id}


class FakeSlack:
    def __init__(self) -> None:
        self.posts: list[tuple] = []
        self.uploads: list[tuple] = []
        self.fail: Exception | None = None

    def file_info(self, access_token: str, file_id: str) -> dict:
        return {"url_private_download": f"https://files.slack.com/{file_id}"}

    def download(self, access_token: str, url: str):
        assert access_token == "xoxp-plain"
        return b"slack-bytes", "image/png"

    def post_message(self, access_token: str, channel: str, text: str, thread_ts: str | None) -> dict:
        if self.fail:
            raise self.fail
        self.posts.append((access_token, channel, text, thread_ts))
        return {"ok": True, "ts": "1700000099.000100"}

    def upload_files(self, access_token, channel, thread_ts, text, files) -> dict:
        self.uploads.append((channel, thread_ts, text, [(item.name, len(item.data)) for item in files]))
        return {"ok": True, "files": [{"id": "F1"}]}


class FakeImages:
    def __init__(self) -> None:
        self.fetched: list[str] = []

    def fetch(self, url: str):
        self.fetched.append(url)
        return b"GIF89a", "image/gif"


def _seed(sessions, cipher, *, member="mina", mail_status="connected", token_expires=None):
    """메일 계정 하나(메일 둘) · 슬랙 방 하나(본문 셋 + 스레드 답글 하나 + 파일) · 카톡 방 하나."""
    with sessions() as session:
        now = T0
        mail = ExternalIntegrationRecord(
            member_id=member, kind="mail", status=mail_status, account_key=f"{member}@company.example",
            display_name=f"{member}@company.example", account_meta={}, access_token_encrypted=cipher.encrypt("access-plain"),
            refresh_token_encrypted=cipher.encrypt("refresh-plain"), token_expires_at=token_expires, created_at=now, updated_at=now,
        )
        slack = ExternalIntegrationRecord(
            member_id=member, kind="slack", status="connected", account_key="T1", display_name="회사", account_meta={"user_id": "U-ME"},
            access_token_encrypted=cipher.encrypt("xoxp-plain"), created_at=now, updated_at=now,
        )
        kakao = ExternalIntegrationRecord(
            member_id=member, kind="kakao", status="connected", account_key="kakao", display_name="", account_meta={},
            created_at=now, updated_at=now,
        )
        session.add_all([mail, slack, kakao])
        session.flush()
        channel = ExternalRoomRecord(integration_id=slack.id, external_id="C1", room_type="channel", name="general", status="live",
                                     member_count=12, created_at=now, updated_at=now)
        chat = ExternalRoomRecord(integration_id=kakao.id, external_id="18200", room_type="group", name="팀방", status="live",
                                  created_at=now, updated_at=now)
        session.add_all([channel, chat])
        session.flush()
        mails = []
        for index, gid in enumerate(["g-old", "g-new"]):
            row = ExternalMessageRecord(
                integration_id=mail.id, source_kind="mail", container_key="", external_key=gid, thread_key=f"thread-{gid}",
                sent_at=now + timedelta(minutes=index), subject=f"메일 {index}", author="Partner <partner@example.com>",
                preview="안녕하세요", raw=gmail_raw(gid, subject=f"메일 {index}"), created_at=now,
            )
            session.add(row)
            session.flush()
            session.add(ExternalAttachmentRecord(
                message_id=row.id, aid=_sha(f"{mail.id}:{gid}:2"), seq=0, kind="file", state="reference", name="견적서.pdf",
                size=5, mime="application/pdf", external_ref=json.dumps({"part_id": "2", "attachment_id": "ATT-pdf-old"}), created_at=now,
            ))
            mails.append(row)
        slack_rows = []
        for index, (ts, user, thread) in enumerate([
            ("1700000001.000100", "U-OTHER", None),
            ("1700000002.000100", "U-ME", None),
            ("1700000003.000100", "U-OTHER", "1700000003.000100"),
            ("1700000004.000100", "U-OTHER", "1700000003.000100"),  # 스레드 답글 — 본문 줄이 아니다
        ]):
            raw = {"ts": ts, "user": user, "text": f"슬랙 {index}", **({"thread_ts": thread} if thread else {})}
            if index == 0:
                raw["files"] = [{"id": "F1", "name": "사진.png", "url_private_download": "https://files.slack.com/F1"}]
            row = ExternalMessageRecord(
                integration_id=slack.id, room_id=channel.id, source_kind="slack", container_key="C1", external_key=ts, thread_key=thread,
                sent_at=now + timedelta(minutes=10 + index), author=user, preview=f"슬랙 {index}", raw=raw, created_at=now,
            )
            session.add(row)
            session.flush()
            slack_rows.append(row)
        session.add(ExternalAttachmentRecord(
            message_id=slack_rows[0].id, aid="slack-aid-1", seq=0, kind="image", state="reference", name="사진.png", size=3,
            mime="image/png", external_ref="F1", created_at=now,
        ))
        session.commit()
        return {
            "mail": str(mail.id), "slack": str(slack.id), "kakao": str(kakao.id), "channel": str(channel.id), "chat": str(chat.id),
            "mails": [str(row.id) for row in mails], "slack_rows": [str(row.id) for row in slack_rows],
            "pdf_aid": _sha(f"{mail.id}:g-new:2"), "logo_aid": _sha(f"{mail.id}:g-new:1"),
        }


# ── 목록 ────────────────────────────────────────────────────────────────────────────────


def test_list_is_cards_newest_first_with_source_filter_and_unread_counts(tmp_path) -> None:
    client, _, _, cipher, sessions = _stack(tmp_path)
    ids = _seed(sessions, cipher)
    page = client.get("/api/inbox/messages", headers=MINA)
    assert page.status_code == 200, page.text
    body = page.json()
    kinds = [item["kind"] for item in body["items"]]
    assert kinds == ["slack", "mail", "mail"]  # 슬랙 방 하나 = 카드 하나 · 빈 카톡 방은 카드가 없다
    room = body["items"][0]
    assert room["room_id"] == ids["channel"] and room["title"] == "general" and room["member_count"] == 12
    # 미읽음 — 스레드 답글·내가 쓴 줄은 세지 않는다
    assert room["unread_count"] == 2
    assert [line["text"] for line in room["preview"]] == ["슬랙 0", "슬랙 1", "슬랙 2"]
    mail = body["items"][1]
    assert mail["message_id"] == ids["mails"][1] and mail["unread"] is True and mail["attach_count"] == 1
    assert mail["subject"] == "메일 1" and mail["account"] == "mina@company.example"
    assert body["unread_counts"] == {"mail": 2, "slack": 1, "kakao": 0, "all": 3}

    only_mail = client.get("/api/inbox/messages", headers=MINA, params={"source": "mail"}).json()["items"]
    assert {item["kind"] for item in only_mail} == {"mail"}
    assert client.get("/api/inbox/messages", headers=MINA, params={"source": "fax"}).status_code == 422
    # 남의 메시지함은 비어 있다(D-24)
    assert client.get("/api/inbox/messages", headers=JIHO).json()["items"] == []


def test_list_pages_with_an_opaque_cursor(tmp_path, monkeypatch) -> None:
    client, _, _, cipher, sessions = _stack(tmp_path)
    _seed(sessions, cipher)
    first = client.get("/api/inbox/messages", headers=MINA, params={"source": "mail"}).json()
    assert first["next_cursor"] is None
    monkeypatch.setattr(inbox_module, "LIST_PAGE_SIZE", 1)
    seen, cursor = [], None
    workflow = client.app.state.workflow_application
    for _ in range(4):
        page = workflow.inbox_messages(workflow.authenticated_principal("mina"), "all", False, cursor)
        seen += [item.get("message_id") or item.get("room_id") for item in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 3 and len(set(seen)) == 3
    assert client.get("/api/inbox/messages", headers=MINA, params={"cursor": "%%%"}).status_code == 422


# ── 메일 본문 — 소독(F-3) ───────────────────────────────────────────────────────────────────


def test_mail_body_is_a_sanitized_copy_and_the_raw_stays(tmp_path) -> None:
    client, _, _, cipher, sessions = _stack(tmp_path)
    ids = _seed(sessions, cipher)
    response = client.get(f"/api/inbox/mail/{ids['mails'][1]}", headers=MINA)
    assert response.status_code == 200, response.text
    view = response.json()
    safe = view["safe_html"]
    for forbidden in ("<script", "steal()", "onclick", "onload", "<form", "<iframe", "<object", "javascript:", "<style", "<input"):
        assert forbidden not in safe, forbidden
    assert safe.startswith('<meta http-equiv="Content-Security-Policy"')
    assert '<img src="https://tracker.example' not in safe and 'data-ax-remote-src="https://tracker.example/pixel.gif"' in safe
    assert f'src="/api/inbox/mail/{ids["mails"][1]}/attachments/{ids["logo_aid"]}"' in safe  # cid: → 첨부 중계
    assert "<details><summary>" in safe and "예전 메일" in safe  # 인용은 스크립트 없이 접힌다
    assert 'href="https://ok.example"' in safe and 'rel="noopener noreferrer"' in safe
    assert view["sender"] == "Partner <partner@example.com>"
    assert view["to"] == ["mina@company.example", "Lee <lee@example.com>"] and view["cc"] == ["cc@example.com"]
    assert view["attachments"] == [{"aid": ids["pdf_aid"], "name": "견적서.pdf", "size": 5, "mime": "application/pdf", "kind": "file", "state": "reference"}]
    with sessions() as session:
        row = session.get(ExternalMessageRecord, UUID(ids["mails"][1]))
        assert row.safe_html == safe  # 처음 열 때 만들어 채운다
        assert row.raw["payload"]["parts"][0]["body"]["data"] == _b64(HOSTILE_HTML)  # 원문은 그대로(D-28)
    assert client.get(f"/api/inbox/mail/{ids['mails'][1]}", headers=JIHO).status_code == 404
    assert client.get(f"/api/inbox/mail/{uuid4()}", headers=MINA).status_code == 404


# ── 방 메시지 · 스레드 ─────────────────────────────────────────────────────────────────────────


def test_room_messages_page_upward_and_threads_open_separately(tmp_path, monkeypatch) -> None:
    client, _, _, cipher, sessions = _stack(tmp_path)
    ids = _seed(sessions, cipher)
    url = f"/api/inbox/rooms/{ids['channel']}/messages"
    whole = client.get(url, headers=MINA).json()
    assert [row["key"] for row in whole["messages"]] == ["1700000001.000100", "1700000002.000100", "1700000003.000100"]
    assert whole["room"]["kind"] == "slack" and whole["next_cursor"] is None
    assert whole["messages"][0]["attachments"][0]["aid"] == "slack-aid-1"
    assert whole["messages"][0]["raw"]["text"] == "슬랙 0"
    monkeypatch.setattr(inbox_module, "ROOM_PAGE_SIZE", 2)
    newest = client.get(url, headers=MINA).json()
    assert [row["key"] for row in newest["messages"]] == ["1700000002.000100", "1700000003.000100"]
    older = client.get(url, headers=MINA, params={"cursor": newest["next_cursor"]}).json()
    assert [row["key"] for row in older["messages"]] == ["1700000001.000100"] and older["next_cursor"] is None
    thread = client.get(url, headers=MINA, params={"thread_ts": "1700000003.000100"}).json()
    assert [row["key"] for row in thread["messages"]] == ["1700000003.000100", "1700000004.000100"]
    assert client.get(url, headers=JIHO).status_code == 404


# ── 읽음 ────────────────────────────────────────────────────────────────────────────────


def test_read_is_per_member_room_up_to_ts_and_read_all(tmp_path) -> None:
    client, _, _, cipher, sessions = _stack(tmp_path)
    ids = _seed(sessions, cipher)
    _seed(sessions, cipher, member="jiho")  # 동료도 같은 방을 따로 가진다(D-25)

    def counts(headers):
        return client.get("/api/inbox/messages", headers=headers).json()["unread_counts"]

    assert client.post(f"/api/inbox/mail/{ids['mails'][0]}/read", headers=MINA).status_code == 204
    assert counts(MINA)["mail"] == 1 and counts(JIHO)["mail"] == 2
    assert client.post(f"/api/inbox/mail/{ids['mails'][0]}/read", headers=JIHO).status_code == 404

    room = f"/api/inbox/rooms/{ids['channel']}/read"
    assert client.post(room, headers=MINA, json={"up_to_ts": "1700000001.000100"}).status_code == 204
    card = client.get("/api/inbox/messages", headers=MINA, params={"source": "slack"}).json()["items"][0]
    assert card["unread_count"] == 1
    assert client.post(room, headers=MINA, json={"up_to_ts": "nope"}).status_code == 422
    assert client.post(room, headers=MINA, json={"up_to_ts": "1700000003.000100"}).status_code == 204
    # 뒤로 가지 않는다
    assert client.post(room, headers=MINA, json={"up_to_ts": "1700000001.000100"}).status_code == 204
    assert counts(MINA)["slack"] == 0 and counts(JIHO)["slack"] == 1

    assert client.post("/api/inbox/read-all", headers=JIHO, params={"source": "mail"}).status_code == 204
    assert counts(JIHO) == {"mail": 0, "slack": 1, "kakao": 0, "all": 1}
    assert client.post("/api/inbox/read-all", headers=JIHO).status_code == 204
    assert counts(JIHO)["all"] == 0
    unread = client.get("/api/inbox/messages", headers=MINA, params={"unread": "true"}).json()["items"]
    assert [item["message_id"] for item in unread] == [ids["mails"][1]]


# ── 첨부 중계 · 이미지 프록시 ─────────────────────────────────────────────────────────────────


def test_mail_attachment_is_relayed_with_the_members_token_and_not_stored(tmp_path) -> None:
    client, _, fakes, cipher, sessions = _stack(tmp_path)
    ids = _seed(sessions, cipher)
    response = client.get(f"/api/inbox/mail/{ids['mails'][1]}/attachments/{ids['pdf_aid']}", headers=MINA)
    assert response.status_code == 200, response.text
    assert response.content == b"%PDF-1.7 bytes"
    assert response.headers["content-disposition"].startswith("inline;")  # PDF 는 허용 목록
    assert response.headers["x-content-type-options"] == "nosniff"
    assert fakes["mail"].tokens_seen == ["access-plain"]
    assert client.get(f"/api/inbox/mail/{ids['mails'][1]}/attachments/{'0' * 64}", headers=MINA).status_code == 404
    assert client.get(f"/api/inbox/mail/{ids['mails'][1]}/attachments/{ids['pdf_aid']}", headers=JIHO).status_code == 404
    with sessions() as session:
        assert session.scalar(select(ExternalAttachmentRecord.storage_key).where(ExternalAttachmentRecord.aid == ids["pdf_aid"])) is None


def test_expired_mail_token_is_refreshed_and_a_rejected_one_disconnects(tmp_path) -> None:
    client, _, fakes, cipher, sessions = _stack(tmp_path)
    ids = _seed(sessions, cipher, token_expires=datetime.now(UTC) - timedelta(minutes=1))
    url = f"/api/inbox/mail/{ids['mails'][1]}/attachments/{ids['pdf_aid']}"
    assert client.get(url, headers=MINA).status_code == 200
    assert fakes["mail"].tokens_seen == ["access-refreshed"]
    with sessions() as session:
        row = session.get(ExternalIntegrationRecord, UUID(ids["mail"]))
        assert cipher.decrypt(row.access_token_encrypted) == "access-refreshed"

    fakes["mail"].reject_token = "access-refreshed"
    fakes["mail"].refresh = lambda token: (_ for _ in ()).throw(UpstreamUnauthorized("invalid_grant"))
    response = client.get(url, headers=MINA)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "integration_disconnected"
    with sessions() as session:
        assert session.get(ExternalIntegrationRecord, UUID(ids["mail"])).status == "disconnected"  # 배너가 선다(D-50)
    assert client.get(url, headers=MINA).status_code == 409


def test_slack_attachment_relay_and_upstream_failure_is_502(tmp_path) -> None:
    client, _, fakes, cipher, sessions = _stack(tmp_path)
    ids = _seed(sessions, cipher)
    url = f"/api/inbox/rooms/{ids['channel']}/attachments/slack-aid-1"
    response = client.get(url, headers=MINA)
    assert response.status_code == 200 and response.content == b"slack-bytes"
    assert response.headers["content-type"] == "image/png" and response.headers["content-disposition"].startswith("inline;")
    fakes["slack"].download = lambda token, link: (_ for _ in ()).throw(UpstreamFailed("http_503"))
    assert client.get(url, headers=MINA).status_code == 502
    assert client.get(url, headers=JIHO).status_code == 404


def test_remote_image_proxy_only_takes_urls_in_that_mail(tmp_path) -> None:
    client, _, fakes, cipher, sessions = _stack(tmp_path)
    ids = _seed(sessions, cipher)
    url = f"/api/inbox/mail/{ids['mails'][1]}/remote-image"
    rejected = client.get(url, headers=MINA, params={"u": "http://169.254.169.254/latest/meta-data/"})
    assert rejected.status_code == 400 and fakes["images"].fetched == []
    ok = client.get(url, headers=MINA, params={"u": "https://tracker.example/pixel.gif"})
    assert ok.status_code == 200 and ok.content == b"GIF89a"
    assert fakes["images"].fetched == ["https://tracker.example/pixel.gif"]
    assert client.get(url, headers=JIHO, params={"u": "https://tracker.example/pixel.gif"}).status_code == 404


# ── 답장 ────────────────────────────────────────────────────────────────────────────────


def test_slack_reply_is_accepted_sent_in_my_name_and_idempotent(tmp_path) -> None:
    client, _, fakes, cipher, sessions = _stack(tmp_path)
    ids = _seed(sessions, cipher)
    url = f"/api/inbox/rooms/{ids['channel']}/reply"
    first = client.post(url, headers={**MINA, "Idempotency-Key": "k-1"}, data={"text": "확인했습니다", "thread_ts": "1700000003.000100"})
    assert first.status_code == 202, first.text
    local_id = first.json()["local_id"]
    again = client.post(url, headers={**MINA, "Idempotency-Key": "k-1"}, data={"text": "확인했습니다", "thread_ts": "1700000003.000100"})
    assert again.status_code == 202 and again.json()["local_id"] == local_id
    assert fakes["slack"].posts == [("xoxp-plain", "C1", "확인했습니다", "1700000003.000100")]  # 한 번만 — 사용자 토큰
    with sessions() as session:
        reply = session.get(ExternalSentReplyRecord, UUID(local_id))
        assert reply.status == "sent" and reply.external_ref == "1700000099.000100" and reply.member_id == "mina"

    files = client.post(url, headers=MINA, data={"text": "파일"}, files=[("files", ("보고.pdf", b"%PDF", "application/pdf"))])
    assert files.status_code == 202
    assert fakes["slack"].uploads == [("C1", None, "파일", [("보고.pdf", 4)])]
    assert client.post(url, headers=MINA, data={"text": " "}).status_code == 422
    assert client.post(url, headers=JIHO, data={"text": "x"}).status_code == 404
    kakao = client.post(f"/api/inbox/rooms/{ids['chat']}/reply", headers=MINA, data={"text": "x"})
    assert kakao.status_code == 409 and kakao.json()["detail"]["code"] == "read_only"


def test_slack_file_over_50mb_is_413_and_upstream_failure_is_reported_then_retried(tmp_path, monkeypatch) -> None:
    client, _, fakes, cipher, sessions = _stack(tmp_path)
    ids = _seed(sessions, cipher)
    url = f"/api/inbox/rooms/{ids['channel']}/reply"
    monkeypatch.setattr(inbox_module, "SLACK_FILE_LIMIT_BYTES", 10)
    big = client.post(url, headers=MINA, data={"text": "x"}, files=[("files", ("big.bin", b"x" * 11, "application/octet-stream"))])
    assert big.status_code == 413

    fakes["slack"].fail = UpstreamFailed("ratelimited")
    failed = client.post(url, headers={**MINA, "Idempotency-Key": "k-2"}, data={"text": "다시"})
    assert failed.status_code == 202
    with sessions() as session:
        reply = session.get(ExternalSentReplyRecord, UUID(failed.json()["local_id"]))
        assert reply.status == "failed" and reply.error == "ratelimited"
    fakes["slack"].fail = None
    retried = client.post(url, headers={**MINA, "Idempotency-Key": "k-2"}, data={"text": "다시"})  # 「다시 보내기」
    assert retried.json()["local_id"] == failed.json()["local_id"]
    with sessions() as session:
        assert session.get(ExternalSentReplyRecord, UUID(failed.json()["local_id"])).status == "sent"


def test_mail_reply_all_threads_with_re_subject_and_records_what_we_sent(tmp_path, monkeypatch) -> None:
    client, _, fakes, cipher, sessions = _stack(tmp_path)
    ids = _seed(sessions, cipher)
    url = f"/api/inbox/mail/{ids['mails'][1]}/reply"
    response = client.post(
        url,
        headers={**MINA, "Idempotency-Key": "m-1"},
        data={"reply_all": "true", "body": "회신드립니다"},
        files=[("files", ("a.txt", b"hello", "text/plain"))],
    )
    assert response.status_code == 202, response.text
    [(raw, thread)] = fakes["mail"].sent
    assert thread == "thread-g-new"
    sent = message_from_bytes(raw, policy=default_policy)
    assert sent["Subject"] == "Re: 메일 1"
    assert sent["To"] == "Partner <partner@example.com>"
    assert "lee@example.com" in sent["Cc"] and "cc@example.com" in sent["Cc"] and "mina@company.example" not in sent["Cc"]
    assert sent["In-Reply-To"] == "<g-new@mail.example>" and sent["From"] == "mina@company.example"
    assert [part.get_filename() for part in sent.iter_attachments()] == ["a.txt"]
    view = client.get(f"/api/inbox/mail/{ids['mails'][1]}", headers=MINA).json()
    [record] = view["sent_replies"]
    assert record["local_id"] == response.json()["local_id"] and record["status"] == "sent"
    assert record["payload"]["subject"] == "Re: 메일 1" and record["payload"]["files"] == [{"name": "a.txt", "size": 5, "mime": "text/plain"}]

    monkeypatch.setattr(inbox_module, "MAIL_ATTACHMENTS_LIMIT_BYTES", 8)
    # 파일 하나하나는 한도 안이지만 합계가 넘는다 — 합계로 잰다(W-11)
    too_big = client.post(url, headers=MINA, data={"body": "x"}, files=[("files", ("a", b"12345", "text/plain")), ("files", ("b", b"12345", "text/plain"))])
    assert too_big.status_code == 413
    assert client.post(url, headers=JIHO, data={"body": "x"}).status_code == 404


def test_reply_on_a_disconnected_integration_is_409(tmp_path) -> None:
    client, _, _, cipher, sessions = _stack(tmp_path)
    ids = _seed(sessions, cipher, mail_status="disconnected")
    response = client.post(f"/api/inbox/mail/{ids['mails'][1]}/reply", headers=MINA, data={"body": "x"})
    assert response.status_code == 409


# ── 사용자 WS ───────────────────────────────────────────────────────────────────────────────


def test_inbox_stream_pushes_only_my_events(tmp_path) -> None:
    client, _, fakes, cipher, sessions = _stack(tmp_path)
    ids = _seed(sessions, cipher)
    _seed(sessions, cipher, member="jiho")
    with client.websocket_connect("/api/inbox/stream", headers=MINA) as mine:
        assert mine.receive_json() == {"type": "ready"}
        with client.websocket_connect("/api/inbox/stream", headers=JIHO) as theirs:
            assert theirs.receive_json() == {"type": "ready"}
            accepted = client.post(f"/api/inbox/rooms/{ids['channel']}/reply", headers=MINA, data={"text": "hi"}).json()
            event = mine.receive_json()
            assert event["type"] == "inbox.reply_result" and event["data"] == {"local_id": accepted["local_id"], "status": "sent"}
            assert event["room_id"] == ids["channel"] and event["source_kind"] == "slack"
            # jiho 의 채널에는 아무것도 가지 않았다 — jiho 자신의 사건만 받는다
            client.app.state.workflow_application.user_event_hub.dispatch(
                json.dumps({"v": 1, "type": "integration.changed", "member_id": "jiho"})
            )
            assert theirs.receive_json() == {"type": "integration.changed"}


def test_inbox_stream_needs_a_session(tmp_path) -> None:
    client, *_ = _stack(tmp_path)
    with client.websocket_connect("/api/inbox/stream") as anonymous:
        message = anonymous.receive()
        assert message["type"] == "websocket.close" and message["code"] == 4401
