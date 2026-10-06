"""메시지함·읽음·첨부 중계·이미지 프록시·답장 — **BE-3 소유** (WORK-011 Phase BE-3 · SPEC-008 §4.4).

- 목록 `GET /api/inbox/messages` — 메일은 **한 통 = 카드 하나**, 슬랙·카톡은 **방 하나 = 카드 하나**(D-08). 최신순 ·
  `cursor` 는 (시각, id) 를 감싼 불투명 글자.
- 본문 — 메일은 **소독한 안전본**(`inbox_html` · F-3)을 처음 열 때 만들어 `safe_html` 에 채운다. 방은 원문 JSON 을
  페이지로(위로 `cursor`) · `thread_ts` 면 그 스레드.
- 읽음 — **사용자별**(D-25). 메일 단건 · 방은 `up_to_ts` 까지(뒤로 가지 않는다) · 모두 읽음(출처 한정).
- 첨부 — 메일·슬랙은 **그 회원 토큰으로 그때 받아 넘긴다**(저장 안 함 · D-29) · 카톡은 저장본(D-30) · 만료 410.
- 이미지 프록시 — **그 메일 안전본에 실제로 있는 주소만**. 주소·IP·MIME·크기 규칙은 `ImageFetcher` 어댑터가 지킨다.
- 답장 — `202 {local_id}` 뒤 보내고 결과는 사용자 사건(`inbox.reply_result`)으로. 재시도 중복은 `Idempotency-Key`.

소유는 BE-1 의 규칙 그대로다 — 남의 연동·방·메시지는 없는 것처럼 404(`ResourceNotFound`).
"""
from __future__ import annotations

import logging

import base64
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
import json
from typing import Any, Literal, Protocol, TypeVar
from uuid import UUID

from typing_extensions import TypedDict

from ax_workspace.modules.errors import ResourceNotFound
from ax_workspace.modules.external_channels.domain import ExternalChannelError, IntegrationKind, IntegrationStatus
from ax_workspace.modules.external_channels.events import USER_EVENTS_CHANNEL, UserEvent, UserEventType
from ax_workspace.modules.external_channels.inbox_html import (
    CSP_META,
    bare_address,
    gmail_attachment_aid,
    iter_parts,
    parse_gmail_message,
    remote_image_urls,
    sanitize_mail_html,
    split_addresses,
    text_to_safe_html,
)
from ax_workspace.modules.organization_access.domain import Principal

#: 슬랙 보내기 파일 하나 · 카톡 저장 한도(OQ-807).
SLACK_FILE_LIMIT_BYTES = 50 * 1024 * 1024
#: 메일 답장 첨부 **합계**(인코딩 전) — Gmail 은 메일 전체 한도다(W-11 · OQ-806).
MAIL_ATTACHMENTS_LIMIT_BYTES = 25 * 1024 * 1024
LIST_PAGE_SIZE = 30
ROOM_PAGE_SIZE = 50
MAX_REPLY_FILES = 10
SOURCES = ("all", "mail", "slack", "kakao")
#: 토큰 만료가 이만큼 남았으면 미리 갈아 끼운다.
TOKEN_REFRESH_MARGIN = timedelta(seconds=60)


# ── 오류 — HTTP 상태는 `entrypoints/http_inbox.py` 가 정한다 ─────────────────────────────────────


class InboxNotFound(RuntimeError, ResourceNotFound):
    """메시지·방·첨부가 없거나 남의 것이다 — 404 로 가린다(D-24)."""


class IntegrationUnavailable(ExternalChannelError):
    """끊긴 연동(다시 연결 필요) · 카톡 답장(조회 전용) — 409."""

    def __init__(self, message: str, *, code: str = "integration_disconnected") -> None:
        super().__init__(message)
        self.code = code


class AttachmentGone(ExternalChannelError):
    """카톡 첨부를 낼 수 없다 — `expired`(만료) · `too_large`(50MB 초과라 저장 안 함) · `not_stored`(동영상·음성) — 410."""

    def __init__(self, code: str) -> None:
        super().__init__(f"attachment is not available: {code}")
        self.code = code


class UpstreamFailed(ExternalChannelError):
    """상류(Gmail·슬랙·원격 이미지)가 실패했다 — 조회 중계는 502. 사유 코드만 싣고 토큰은 싣지 않는다."""

    def __init__(self, code: str, *, retryable: bool = True) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class UpstreamUnauthorized(UpstreamFailed):
    """상류가 토큰을 거절했다(만료·권한 회수) — 연동을 끊김으로 돌린다(D-50)."""

    def __init__(self, code: str = "unauthorized") -> None:
        super().__init__(code, retryable=False)


class RemoteImageRejected(ExternalChannelError):
    """이미지 프록시 규칙 위반 — 본문에 없는 주소 · http(s) 아님 · 사설/루프백/메타데이터 IP · 이미지 아님 · 너무 큼 — 400."""


class PayloadTooLarge(ExternalChannelError):
    """첨부 한도 초과 — 413."""


class InvalidInboxRequest(ExternalChannelError):
    """요청 모양이 계약에 맞지 않는다 — 422."""


# ── 포트 ─────────────────────────────────────────────────────────────────────────────────


class InboxRepository(Protocol):
    def active_integrations(self, member_id: str) -> list[Any]: ...
    def integration_by_id(self, integration_id: UUID) -> Any | None: ...
    def mail_page(
        self, integration_ids: Sequence[UUID], member_id: str, *, before: tuple[datetime, str] | None, unread_only: bool, limit: int
    ) -> list[Any]: ...
    def mail_unread_count(self, integration_ids: Sequence[UUID], member_id: str) -> int: ...
    def read_message_ids(self, member_id: str, message_ids: Sequence[UUID]) -> set[UUID]: ...
    def attachment_counts(self, message_ids: Sequence[UUID]) -> dict[UUID, int]: ...
    def room_cards(self, member_id: str, integrations: Sequence[Any]) -> list[dict[str, Any]]: ...
    def room_preview(self, room_id: UUID, limit: int) -> list[Any]: ...
    def mail_for(self, member_id: str, message_id: UUID) -> tuple[Any, Any] | None: ...
    def room_for(self, member_id: str, room_id: UUID) -> tuple[Any, Any] | None: ...
    def room_messages(self, room_id: UUID, *, before: tuple[datetime, str] | None, thread_key: str | None, limit: int) -> list[Any]: ...
    def attachments_of(self, message_ids: Sequence[UUID]) -> dict[UUID, list[Any]]: ...
    def attachment_in_room(self, room_id: UUID, aid: str) -> tuple[Any, Any] | None: ...
    def message_in_room(self, room_id: UUID, key: str) -> Any | None: ...
    def room_read_state(self, member_id: str, room_id: UUID) -> Any | None: ...
    def mark_message_read(self, member_id: str, message_id: UUID, at: datetime) -> None: ...
    def mark_room_read(self, member_id: str, room_id: UUID, key: str, up_to: datetime, at: datetime) -> None: ...
    def read_all(self, member_id: str, integrations: Sequence[Any], at: datetime) -> None: ...
    def reply_by_key(self, member_id: str, idempotency_key: str) -> Any | None: ...
    def reply_by_id(self, reply_id: UUID) -> Any | None: ...
    def add_reply(self, **fields: Any) -> Any: ...
    def replies_for_message(self, member_id: str, message_id: UUID) -> list[Any]: ...
    def message_by_id(self, message_id: UUID) -> Any | None: ...
    def room_by_id(self, room_id: UUID) -> Any | None: ...
    def notify(self, channel: str, payload: str) -> None: ...


class TokenCipher(Protocol):
    def encrypt(self, plaintext: str) -> str: ...
    def decrypt(self, ciphertext: str) -> str: ...


@dataclass(frozen=True, slots=True)
class RefreshedToken:
    access_token: str = field(repr=False)
    expires_at: datetime | None


class MailUpstream(Protocol):
    def refresh(self, refresh_token: str) -> RefreshedToken: ...
    def get_message(self, access_token: str, message_id: str) -> dict[str, Any]: ...
    def attachment(self, access_token: str, message_id: str, attachment_id: str) -> bytes: ...
    def send(self, access_token: str, raw_message: bytes, thread_id: str | None) -> dict[str, Any]: ...


class SlackUpstream(Protocol):
    def file_info(self, access_token: str, file_id: str) -> dict[str, Any]: ...
    def download(self, access_token: str, url: str) -> tuple[bytes, str | None]: ...
    def post_message(self, access_token: str, channel: str, text: str, thread_ts: str | None) -> dict[str, Any]: ...
    def upload_files(
        self, access_token: str, channel: str, thread_ts: str | None, text: str, files: Sequence["OutgoingFile"]
    ) -> dict[str, Any]: ...


class ImageFetcher(Protocol):
    def fetch(self, url: str) -> tuple[bytes, str]: ...


class BlobStorage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


# ── 값 ──────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class OutgoingFile:
    name: str
    content_type: str
    data: bytes


@dataclass(frozen=True, slots=True)
class Download:
    name: str
    content_type: str
    data: bytes


@dataclass(frozen=True, slots=True)
class ReplyAccepted:
    """`deliver` 가 거짓이면 같은 `Idempotency-Key` 의 재전송 — 두 번째 effect 없이 첫 `local_id` 만 돌려준다."""

    local_id: str
    deliver: bool


class PreviewLine(TypedDict):
    author: str | None
    text: str | None
    at: str


class InboxListItem(TypedDict, total=False):
    kind: str
    integration_id: str
    at: str
    # 메일 카드
    message_id: str
    account: str
    subject: str | None
    sender: str | None
    unread: bool
    attach_count: int
    snippet: str | None
    # 방 카드
    room_id: str
    room_type: str
    title: str
    member_count: int | None
    unread_count: int
    last_at: str
    preview: list[PreviewLine]


class InboxPage(TypedDict):
    items: list[dict[str, Any]]
    next_cursor: str | None
    unread_counts: dict[str, int]


class AttachmentView(TypedDict):
    aid: str
    name: str
    size: int | None
    mime: str | None
    kind: str
    state: str


class SentReplyView(TypedDict):
    local_id: str
    status: str
    payload: dict[str, Any]
    error: str | None
    created_at: str
    sent_at: str | None


class MailView(TypedDict):
    message_id: str
    integration_id: str
    account: str
    thread_id: str | None
    subject: str | None
    sender: str | None
    to: list[str]
    cc: list[str]
    reply_to: list[str]
    date: str | None
    at: str
    unread: bool
    safe_html: str
    attachments: list[AttachmentView]
    sent_replies: list[SentReplyView]


class RoomMessageView(TypedDict):
    id: str
    key: str
    at: str
    author: str | None
    thread_key: str | None
    raw: dict[str, Any]
    attachments: list[AttachmentView]
    #: 슬랙 「슬랙에서 열기」 — 워크스페이스 주소를 아는 연동만(BE 수정 판 3). 카톡·모르면 null.
    permalink: str | None


class UserTagView(TypedDict):
    """보낸 사람·멘션 id → 이름표(BE 수정 판 3). 연동 워커가 그 회원 토큰으로 풀어 방에 모아 둔 것이다."""

    name: str | None
    is_bot: bool
    avatar: str | None


class RoomHeaderView(TypedDict):
    room_id: str
    integration_id: str
    kind: str
    room_type: str
    name: str
    member_count: int | None
    external_id: str
    read_up_to_key: str | None
    #: 방 자체의 슬랙 주소(머리 「슬랙에서 열기」). 카톡·모르면 null.
    permalink: str | None


class RoomMessagesPage(TypedDict):
    room: RoomHeaderView
    messages: list[RoomMessageView]
    next_cursor: str | None
    #: `{user id: 이름표}` — 이 방에서 본 보낸 사람·멘션(슬랙). 카톡은 비어 있다(작성자 이름이 이미 글자다).
    users: dict[str, UserTagView]


class ReplyAcceptedView(TypedDict):
    local_id: str


# ── 도움 ─────────────────────────────────────────────────────────────────────────────────


def aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def iso(value: datetime | None) -> str | None:
    value = aware(value)
    return value.isoformat() if value else None


def encode_cursor(at: datetime, identity: str) -> str:
    body = json.dumps({"at": aware(at).isoformat(), "id": identity}, separators=(",", ":"))
    return base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    if not cursor:
        return None
    try:
        body = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("utf-8"))
        return aware(datetime.fromisoformat(body["at"])), str(body["id"])
    except (ValueError, KeyError, TypeError):
        raise InvalidInboxRequest("cursor 가 올바르지 않습니다") from None


def _id_key(value: Any) -> str:
    return value.hex if isinstance(value, UUID) else str(value)


def _before(at: datetime, identity: str, cursor: tuple[datetime, str] | None) -> bool:
    if cursor is None:
        return True
    return (at, identity) < cursor


def _attachment_view(row: Any) -> AttachmentView:
    return {"aid": row.aid, "name": row.name, "size": row.size, "mime": row.mime, "kind": row.kind, "state": row.state}


def _reply_view(row: Any) -> SentReplyView:
    payload = dict(row.payload or {})
    if payload.get("files"):
        # 첨부 대기 저장본의 키는 서버 안쪽 값이다 — 화면에 내지 않는다.
        payload["files"] = [{key: value for key, value in item.items() if key != "storage_key"} for item in payload["files"]]
    return {
        "local_id": str(row.id),
        "status": row.status,
        "payload": payload,
        "error": row.error,
        "created_at": iso(row.created_at) or "",
        "sent_at": iso(row.sent_at),
    }


def _kinds_for(source: str) -> set[str]:
    if source not in SOURCES:
        raise InvalidInboxRequest("source 는 all·mail·slack·kakao 중 하나입니다")
    return {IntegrationKind.MAIL, IntegrationKind.SLACK, IntegrationKind.KAKAO} if source == "all" else {source}


def slack_workspace_url(integration: Any) -> str | None:
    """`https://<domain>.slack.com/` — 연동 워커·개발 토큰 이음새가 `auth.test` 로 알아 둔 값(BE 수정 판 3)."""
    url = str((integration.account_meta or {}).get("url") or "")
    return (url if url.endswith("/") else url + "/") if url.startswith("https://") else None


def slack_permalink(workspace: str, channel: str, ts: str, thread_ts: str | None) -> str:
    """슬랙이 쓰는 퍼머링크 모양 `…/archives/<channel>/p<ts 점 없이>` — 답글이면 스레드를 함께 연다."""
    link = f"{workspace}archives/{channel}/p{ts.replace('.', '')}"
    if thread_ts and thread_ts != ts:
        link += f"?thread_ts={thread_ts}&cid={channel}"
    return link


def reply_subject(subject: str | None) -> str:
    """`Re:` 고정 — 이미 `Re:` 면 겹치지 않는다(§2.8). 접힌 머리의 개행은 공백으로 편다(머리 주입 방지)."""
    base = " ".join((subject or "").split())
    return base if base.lower().startswith("re:") else f"Re: {base}".rstrip()


T = TypeVar("T")

logger = logging.getLogger(__name__)


class InboxApplication:
    """세션 하나 위의 메시지함 명령. 조립(`bootstrap/external_inbox.py`)이 저장소·암호기·상류 어댑터를 끼운다."""

    def __init__(
        self,
        repository: InboxRepository,
        *,
        cipher: TokenCipher | None,
        mail: MailUpstream | None,
        slack: SlackUpstream,
        images: ImageFetcher,
        storage: BlobStorage,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._cipher = cipher
        self._mail = mail
        self._slack = slack
        self._images = images
        self._storage = storage
        self._clock = clock

    # ── 목록 ─────────────────────────────────────────────────────────────────────────────

    def list_messages(
        self, principal: Principal, *, source: str = "all", unread: bool = False, cursor: str | None = None, limit: int | None = None
    ) -> InboxPage:
        kinds = _kinds_for(source)
        before = decode_cursor(cursor)
        limit = max(1, min(limit or LIST_PAGE_SIZE, 100))
        member_id = str(principal.id)
        integrations = self._repository.active_integrations(member_id)
        mail_ids = [row.id for row in integrations if row.kind == IntegrationKind.MAIL]
        room_integrations = [row for row in integrations if row.kind != IntegrationKind.MAIL]
        accounts = {row.id: row.display_name for row in integrations}

        all_cards = self._repository.room_cards(member_id, room_integrations)
        counts = {"mail": self._repository.mail_unread_count(mail_ids, member_id) if mail_ids else 0, "slack": 0, "kakao": 0}
        for card in all_cards:
            if card["unread_count"] > 0:
                counts[card["integration"].kind] += 1
        counts["all"] = counts["mail"] + counts["slack"] + counts["kakao"]

        entries: list[tuple[tuple[datetime, str], dict[str, Any]]] = []
        if IntegrationKind.MAIL in kinds and mail_ids:
            mails = self._repository.mail_page(mail_ids, member_id, before=before, unread_only=unread, limit=limit + 1)
            ids = [row.id for row in mails]
            read = self._repository.read_message_ids(member_id, ids)
            attach = self._repository.attachment_counts(ids)
            for row in mails:
                at = aware(row.sent_at)
                entries.append(((at, _id_key(row.id)), {
                    "kind": "mail",
                    "message_id": str(row.id),
                    "integration_id": str(row.integration_id),
                    "account": accounts.get(row.integration_id, ""),
                    "subject": row.subject,
                    "sender": row.author,
                    "at": at.isoformat(),
                    "unread": row.id not in read,
                    "attach_count": attach.get(row.id, 0),
                    "snippet": row.preview,
                }))
        for card in all_cards:
            integration, room = card["integration"], card["room"]
            if integration.kind not in kinds or card["last_at"] is None:
                continue
            at = aware(card["last_at"])
            key = (at, _id_key(room.id))
            if not _before(*key, before) or (unread and card["unread_count"] == 0):
                continue
            entries.append((key, {
                "kind": integration.kind,
                "room_id": str(room.id),
                "integration_id": str(integration.id),
                "room_type": room.room_type,
                "title": room.name,
                "member_count": room.member_count,
                "unread_count": card["unread_count"],
                "last_at": at.isoformat(),
                "at": at.isoformat(),
            }))
        entries.sort(key=lambda entry: entry[0], reverse=True)
        page = entries[:limit]
        for _, item in page:
            if "room_id" in item:
                item["preview"] = [
                    {"author": row.author, "text": row.preview, "at": iso(row.sent_at) or ""}
                    for row in reversed(self._repository.room_preview(UUID(item["room_id"]), 3))
                ]
        next_cursor = encode_cursor(*page[-1][0]) if len(entries) > limit and page else None
        return {"items": [item for _, item in page], "next_cursor": next_cursor, "unread_counts": counts}

    # ── 메일 본문 ─────────────────────────────────────────────────────────────────────────

    def mail(self, principal: Principal, message_id: UUID) -> MailView:
        message, integration = self._owned_mail(principal, message_id)
        raw = message.raw or {}
        parts = parse_gmail_message(raw)
        safe = self._safe_html(message, integration, parts)
        headers = parts.headers
        read = self._repository.read_message_ids(str(principal.id), [message.id])
        attachments = self._repository.attachments_of([message.id]).get(message.id, [])
        return {
            "message_id": str(message.id),
            "integration_id": str(integration.id),
            "account": integration.display_name,
            "thread_id": message.thread_key,
            "subject": headers.get("subject") or message.subject,
            "sender": headers.get("from") or message.author,
            "to": split_addresses(headers.get("to")),
            "cc": split_addresses(headers.get("cc")),
            "reply_to": split_addresses(headers.get("reply-to")),
            "date": headers.get("date"),
            "at": iso(message.sent_at) or "",
            "unread": message.id not in read,
            "safe_html": safe,
            "attachments": [_attachment_view(row) for row in attachments],
            "sent_replies": [_reply_view(row) for row in self._repository.replies_for_message(str(principal.id), message.id)],
        }

    def _safe_html(self, message: Any, integration: Any, parts: Any = None) -> str:
        """안전본이 없으면(또는 옛 판이면) 원문에서 만들어 채운다 — 원문 `raw` 는 건드리지 않는다(D-28)."""
        if message.safe_html and message.safe_html.startswith(CSP_META):
            return message.safe_html
        parts = parts or parse_gmail_message(message.raw or {})
        cid_urls = {
            content_id: f"/api/inbox/mail/{message.id}/attachments/{gmail_attachment_aid(str(integration.id), message.external_key, part_id)}"
            for content_id, part_id in parts.inline_parts.items()
            if part_id
        }
        if parts.html:
            safe = sanitize_mail_html(parts.html, cid_urls=cid_urls)
        else:
            safe = text_to_safe_html(parts.text or message.preview or "")
        message.safe_html = safe
        return safe

    # ── 방 메시지 ─────────────────────────────────────────────────────────────────────────

    def room_messages(
        self, principal: Principal, room_id: UUID, *, cursor: str | None = None, thread_ts: str | None = None, limit: int | None = None
    ) -> RoomMessagesPage:
        room, integration = self._owned_room(principal, room_id)
        before = decode_cursor(cursor)
        limit = max(1, min(limit or ROOM_PAGE_SIZE, 200))
        rows = self._repository.room_messages(room.id, before=before, thread_key=thread_ts, limit=limit + 1)
        page = rows[:limit]
        next_cursor = encode_cursor(aware(page[-1].sent_at), _id_key(page[-1].id)) if len(rows) > limit and page else None
        attachments = self._repository.attachments_of([row.id for row in page])
        state = self._repository.room_read_state(str(principal.id), room.id)
        workspace = slack_workspace_url(integration) if integration.kind == IntegrationKind.SLACK else None
        return {
            "users": dict((room.room_meta or {}).get("users") or {}),
            "room": {
                "room_id": str(room.id),
                "integration_id": str(integration.id),
                "kind": integration.kind,
                "room_type": room.room_type,
                "name": room.name,
                "member_count": room.member_count,
                "external_id": room.external_id,
                "read_up_to_key": state.read_up_to_key if state else None,
                "permalink": f"{workspace}archives/{room.external_id}" if workspace else None,
            },
            # 화면은 위에서 아래로 읽는다 — 페이지 안은 오래된 것부터, `next_cursor` 는 더 과거로.
            "messages": [
                {
                    "id": str(row.id),
                    "key": row.external_key,
                    "at": iso(row.sent_at) or "",
                    "author": row.author,
                    "thread_key": row.thread_key,
                    "raw": row.raw or {},
                    "attachments": [_attachment_view(item) for item in attachments.get(row.id, [])],
                    "permalink": slack_permalink(workspace, room.external_id, row.external_key, row.thread_key) if workspace else None,
                }
                for row in reversed(page)
            ],
            "next_cursor": next_cursor,
        }

    # ── 읽음 ─────────────────────────────────────────────────────────────────────────────

    def mark_mail_read(self, principal: Principal, message_id: UUID) -> None:
        message, _ = self._owned_mail(principal, message_id)
        self._repository.mark_message_read(str(principal.id), message.id, self._clock())

    def mark_room_read(self, principal: Principal, room_id: UUID, up_to_ts: str) -> None:
        room, _ = self._owned_room(principal, room_id)
        key = str(up_to_ts).strip()
        message = self._repository.message_in_room(room.id, key) if key else None
        if message is None:
            raise InvalidInboxRequest("up_to_ts 가 그 방의 메시지가 아닙니다")
        self._repository.mark_room_read(str(principal.id), room.id, key, aware(message.sent_at), self._clock())

    def read_all(self, principal: Principal, source: str = "all") -> None:
        kinds = _kinds_for(source)
        member_id = str(principal.id)
        integrations = [row for row in self._repository.active_integrations(member_id) if row.kind in kinds]
        self._repository.read_all(member_id, integrations, self._clock())

    # ── 첨부 · 이미지 프록시 ─────────────────────────────────────────────────────────────────

    def mail_attachment(self, principal: Principal, message_id: UUID, aid: str) -> Download:
        message, integration = self._owned_mail(principal, message_id)
        part = next(
            (
                item for item in iter_parts((message.raw or {}).get("payload") or {})
                if (item.get("body") or {}).get("attachmentId")
                and gmail_attachment_aid(str(integration.id), message.external_key, str(item.get("partId") or "")) == aid
            ),
            None,
        )
        if part is None:
            raise InboxNotFound("attachment was not found")
        self._require_connected(integration)
        part_id = str(part.get("partId") or "")

        def fetch(token: str) -> bytes:
            assert self._mail is not None
            # Gmail 의 attachmentId 는 받을 때마다 글자가 바뀔 수 있다 — 지금 원문에서 partId 로 다시 찾는다.
            fresh = self._mail.get_message(token, message.external_key)
            current = next(
                (item for item in iter_parts(fresh.get("payload") or {}) if str(item.get("partId") or "") == part_id), part
            )
            attachment_id = (current.get("body") or {}).get("attachmentId") or (part.get("body") or {}).get("attachmentId")
            return self._mail.attachment(token, message.external_key, str(attachment_id))

        data = self._with_token(integration, fetch)
        return Download(str(part.get("filename") or "attachment"), str(part.get("mimeType") or "application/octet-stream"), data)

    def room_attachment(self, principal: Principal, room_id: UUID, aid: str) -> Download:
        room, integration = self._owned_room(principal, room_id)
        found = self._repository.attachment_in_room(room.id, aid)
        if found is None:
            raise InboxNotFound("attachment was not found")
        attachment, message = found
        content_type = attachment.mime or "application/octet-stream"
        if integration.kind == IntegrationKind.KAKAO:
            if attachment.state == "stored" and attachment.storage_key:
                try:
                    return Download(attachment.name, content_type, self._storage.get(attachment.storage_key))
                except FileNotFoundError:
                    raise InboxNotFound("stored attachment is missing") from None
            if attachment.state in {"expired", "too_large", "not_stored"}:
                raise AttachmentGone(attachment.state)
            raise InboxNotFound("attachment has not been uploaded yet")
        self._require_connected(integration)
        file_id = attachment.external_ref or ""
        listed = next(
            (item for item in (message.raw or {}).get("files") or [] if isinstance(item, dict) and item.get("id") == file_id),
            {},
        )

        def fetch(token: str) -> tuple[bytes, str | None]:
            url = listed.get("url_private_download") or listed.get("url_private")
            if not url:
                info = self._slack.file_info(token, file_id)
                url = info.get("url_private_download") or info.get("url_private")
            if not url:
                raise UpstreamFailed("file_has_no_download_url", retryable=False)
            return self._slack.download(token, str(url))

        data, upstream_type = self._with_token(integration, fetch)
        return Download(attachment.name, attachment.mime or upstream_type or "application/octet-stream", data)

    def remote_image(self, principal: Principal, message_id: UUID, url: str) -> Download:
        message, integration = self._owned_mail(principal, message_id)
        safe = self._safe_html(message, integration)
        if url.strip() not in remote_image_urls(safe):
            raise RemoteImageRejected("그 메일 본문에 있는 이미지 주소가 아닙니다")
        data, content_type = self._images.fetch(url.strip())
        return Download("image", content_type, data)

    # ── 답장 ─────────────────────────────────────────────────────────────────────────────

    def accept_slack_reply(
        self,
        principal: Principal,
        room_id: UUID,
        *,
        text: str,
        thread_ts: str | None,
        files: Sequence[OutgoingFile],
        idempotency_key: str | None,
    ) -> ReplyAccepted:
        room, integration = self._owned_room(principal, room_id)
        if integration.kind == IntegrationKind.KAKAO:
            raise IntegrationUnavailable("카카오톡은 조회 전용입니다 — 보내지 않습니다", code="read_only")
        self._require_connected(integration)
        text = (text or "").strip()
        if not text and not files:
            raise InvalidInboxRequest("보낼 글이나 파일이 없습니다")
        if len(text) > 40_000:
            raise InvalidInboxRequest("글이 너무 깁니다")
        if len(files) > MAX_REPLY_FILES:
            raise InvalidInboxRequest(f"파일은 한 번에 {MAX_REPLY_FILES}개까지입니다")
        if any(len(item.data) > SLACK_FILE_LIMIT_BYTES for item in files):
            raise PayloadTooLarge("슬랙 파일은 하나당 50MB 까지입니다")
        payload = {
            "text": text,
            "thread_ts": (thread_ts or "").strip() or None,
            "files": [{"name": item.name, "size": len(item.data), "mime": item.content_type} for item in files],
        }
        return self._accept(principal, integration, idempotency_key, payload, room_id=room.id, message_id=None, kind="slack")

    def accept_mail_reply(
        self,
        principal: Principal,
        message_id: UUID,
        *,
        reply_all: bool,
        body: str,
        to: Sequence[str],
        cc: Sequence[str],
        files: Sequence[OutgoingFile],
        idempotency_key: str | None,
    ) -> ReplyAccepted:
        message, integration = self._owned_mail(principal, message_id)
        self._require_connected(integration)
        if sum(len(item.data) for item in files) > MAIL_ATTACHMENTS_LIMIT_BYTES:
            raise PayloadTooLarge("메일 첨부는 합계 25MB 까지입니다")
        body = body or ""
        if not body.strip() and not files:
            raise InvalidInboxRequest("보낼 내용이 없습니다")
        headers = parse_gmail_message(message.raw or {}).headers
        me = bare_address(integration.display_name or integration.account_key)
        default_to = split_addresses(headers.get("reply-to")) or split_addresses(headers.get("from"))
        recipients = [item.strip() for item in to if item.strip()] or default_to
        copies = [item.strip() for item in cc if item.strip()]
        if reply_all and not cc:
            taken = {bare_address(item) for item in recipients} | {me}
            for item in split_addresses(headers.get("to")) + split_addresses(headers.get("cc")):
                address = bare_address(item)
                if address and address not in taken:
                    copies.append(item)
                    taken.add(address)
        if not recipients:
            raise InvalidInboxRequest("받는 사람이 없습니다")
        if any("\r" in item or "\n" in item for item in [*recipients, *copies]):
            # 개행이 든 주소는 메일 머리를 하나 더 끼워 넣을 수 있다 — 접수에서 거절한다(BE-2·3 검수 W-1).
            raise InvalidInboxRequest("주소에 줄바꿈을 넣을 수 없습니다")
        if any("@" not in bare_address(item) for item in [*recipients, *copies]):
            raise InvalidInboxRequest("주소가 올바르지 않습니다")
        payload = {
            "reply_all": bool(reply_all),
            "to": recipients,
            "cc": copies,
            "subject": reply_subject(headers.get("subject") or message.subject),
            "body": body,
            "files": [{"name": item.name, "size": len(item.data), "mime": item.content_type} for item in files],
        }
        return self._accept(principal, integration, idempotency_key, payload, room_id=None, message_id=message.id, kind="mail")

    def _accept(
        self,
        principal: Principal,
        integration: Any,
        idempotency_key: str | None,
        payload: dict[str, Any],
        *,
        room_id: UUID | None,
        message_id: UUID | None,
        kind: str,
    ) -> ReplyAccepted:
        member_id = str(principal.id)
        now = self._clock()
        key = (idempotency_key or "").strip() or None
        if key is not None and len(key) > 200:
            raise InvalidInboxRequest("Idempotency-Key 가 너무 깁니다")
        if key is not None:
            existing = self._repository.reply_by_key(member_id, key)
            if existing is not None:
                if existing.room_id != room_id or existing.message_id != message_id:
                    raise InvalidInboxRequest("같은 Idempotency-Key 를 다른 답장에 쓸 수 없습니다")
                if existing.status != "failed":
                    return ReplyAccepted(str(existing.id), deliver=False)
                # 「다시 보내기」— 실패한 것만 같은 키로 다시 보낸다.
                existing.status = "sending"
                existing.error = None
                existing.payload = payload
                existing.updated_at = now
                return ReplyAccepted(str(existing.id), deliver=True)
        row = self._repository.add_reply(
            member_id=member_id,
            integration_id=integration.id,
            room_id=room_id,
            message_id=message_id,
            source_kind=kind,
            idempotency_key=key,
            status="sending",
            payload=payload,
            created_at=now,
            updated_at=now,
        )
        return ReplyAccepted(str(row.id), deliver=True)

    def deliver_reply(self, local_id: UUID, files: Sequence[OutgoingFile]) -> str:
        """연동 워커가 내구 잡(`external.reply_deliver`)으로 보낸다 — 202 와 전송 사이에 프로세스가 죽어도 잡이 남아
        다시 보낸다(BE 수정 판 1). 결과(`sent`/`failed`)는 그 회원의 사건 채널로 — 상류 429/5xx 도 실패로 알린다.
        `sending` 이 아니면(이미 보냈거나 실패) 아무것도 하지 않는다 — 잡 재전달이 두 번째 effect 를 내지 않는다."""
        reply = self._repository.reply_by_id(local_id)
        if reply is None or reply.status != "sending":
            return reply.status if reply is not None else "missing"
        integration = self._repository.integration_by_id(reply.integration_id)
        error: UpstreamFailed | IntegrationUnavailable | None = None
        try:
            if integration is None or integration.removed_at is not None:
                raise IntegrationUnavailable("연동이 없습니다")
            if reply.source_kind == IntegrationKind.SLACK:
                result = self._send_slack(reply, integration, files)
            else:
                result = self._send_mail(reply, integration, files)
            reply.status = "sent"
            reply.external_ref = str(result)[:200] if result else None
            reply.sent_at = self._clock()
        except (UpstreamFailed, IntegrationUnavailable) as failure:
            error = failure
            reply.status = "failed"
            reply.error = getattr(failure, "code", "failed")[:500]
        except Exception:  # noqa: BLE001 — 알 수 없는 실패도 「보내는 중」에 멈추지 않고 「실패」로 닫는다(검수 W-1)
            logger.exception("reply %s failed unexpectedly", reply.id)
            reply.status = "failed"
            reply.error = "internal_error"
            self._announce_result(reply, retryable=False)
            return reply.status
        self._announce_result(reply, retryable=bool(getattr(error, "retryable", False)) if error is not None else None)
        return reply.status

    def abandon_reply(self, local_id: UUID, code: str) -> str:
        """보낼 수 없게 된 답장(첨부 저장본 유실 · 재시도 한도 초과)을 「실패」로 닫고 알린다 — 「보내는 중」에 멈추지 않는다."""
        reply = self._repository.reply_by_id(local_id)
        if reply is None or reply.status != "sending":
            return reply.status if reply is not None else "missing"
        reply.status = "failed"
        reply.error = code[:500]
        self._announce_result(reply, retryable=True)
        return reply.status

    def _announce_result(self, reply: Any, *, retryable: bool | None) -> None:
        reply.updated_at = self._clock()
        data: dict[str, Any] = {"local_id": str(reply.id), "status": reply.status}
        if retryable is not None:
            data["error"] = reply.error
            data["retryable"] = retryable
        self._repository.notify(
            USER_EVENTS_CHANNEL,
            UserEvent(
                UserEventType.REPLY_RESULT,
                reply.member_id,
                integration_id=str(reply.integration_id),
                room_id=str(reply.room_id) if reply.room_id else None,
                message_id=str(reply.message_id) if reply.message_id else None,
                source_kind=reply.source_kind,
                data=data,
            ).to_payload(),
        )

    def _send_slack(self, reply: Any, integration: Any, files: Sequence[OutgoingFile]) -> str | None:
        room = self._repository.room_by_id(reply.room_id)
        if room is None:
            raise IntegrationUnavailable("방이 없습니다")
        payload = reply.payload or {}
        text, thread_ts = payload.get("text") or "", payload.get("thread_ts")

        def send(token: str) -> dict[str, Any]:
            if files:
                return self._slack.upload_files(token, room.external_id, thread_ts, text, files)
            return self._slack.post_message(token, room.external_id, text, thread_ts)

        answer = self._with_token(integration, send)
        return answer.get("ts") or (answer.get("message") or {}).get("ts")

    def _send_mail(self, reply: Any, integration: Any, files: Sequence[OutgoingFile]) -> str | None:
        original = self._repository.message_by_id(reply.message_id)
        if original is None or self._mail is None:
            raise IntegrationUnavailable("원문 메일이 없습니다")
        payload = reply.payload or {}
        headers = parse_gmail_message(original.raw or {}).headers
        email = EmailMessage()
        email["From"] = integration.display_name or integration.account_key
        email["To"] = ", ".join(payload.get("to") or [])
        if payload.get("cc"):
            email["Cc"] = ", ".join(payload["cc"])
        email["Subject"] = payload.get("subject") or reply_subject(original.subject)
        email["Date"] = formatdate(localtime=False)
        email["Message-ID"] = make_msgid(domain=(integration.account_key.split("@")[-1] or None))
        parent = headers.get("message-id")
        if parent:
            # 스레드가 이어진다 — 받는 쪽 메일 앱도 같은 대화로 묶는다(§4.4).
            email["In-Reply-To"] = parent
            email["References"] = " ".join(item for item in (headers.get("references"), parent) if item)
        email.set_content(payload.get("body") or "")
        for item in files:
            major, _, minor = (item.content_type or "application/octet-stream").partition("/")
            email.add_attachment(item.data, maintype=major or "application", subtype=minor or "octet-stream", filename=item.name)
        raw = email.as_bytes()

        def send(token: str) -> dict[str, Any]:
            assert self._mail is not None
            return self._mail.send(token, raw, original.thread_key)

        return self._with_token(integration, send).get("id")

    # ── 안쪽 ─────────────────────────────────────────────────────────────────────────────

    def _owned_mail(self, principal: Principal, message_id: UUID) -> tuple[Any, Any]:
        found = self._repository.mail_for(str(principal.id), message_id)
        if found is None:
            raise InboxNotFound("message was not found")
        return found

    def _owned_room(self, principal: Principal, room_id: UUID) -> tuple[Any, Any]:
        found = self._repository.room_for(str(principal.id), room_id)
        if found is None:
            raise InboxNotFound("room was not found")
        return found

    @staticmethod
    def _require_connected(integration: Any) -> None:
        if integration.status == IntegrationStatus.DISCONNECTED or integration.removed_at is not None:
            raise IntegrationUnavailable("연결이 끊겼습니다 — 설정에서 다시 연결하세요")

    def _with_token(self, integration: Any, call: Callable[[str], T]) -> T:
        """회원 토큰으로 부른다. 메일은 만료 전에 갈아 끼우고, 거절되면 한 번 갈아 다시 — 그래도 거절이면 끊김(D-50)."""
        token = self._access_token(integration, force_refresh=False)
        try:
            return call(token)
        except UpstreamUnauthorized:
            if integration.kind == IntegrationKind.MAIL and integration.refresh_token_encrypted:
                token = self._access_token(integration, force_refresh=True)
                try:
                    return call(token)
                except UpstreamUnauthorized:
                    pass
            self._mark_disconnected(integration, "revoked")
            raise IntegrationUnavailable("상류가 토큰을 거절했습니다 — 설정에서 다시 연결하세요") from None

    def _access_token(self, integration: Any, *, force_refresh: bool) -> str:
        if self._cipher is None or not integration.access_token_encrypted:
            raise IntegrationUnavailable("저장된 토큰이 없습니다 — 설정에서 다시 연결하세요")
        try:
            token = self._cipher.decrypt(integration.access_token_encrypted)
        except Exception:  # noqa: BLE001 — 키가 바뀌었거나 값이 깨졌다. 어떤 값인지는 말하지 않는다.
            raise IntegrationUnavailable("저장된 토큰을 열 수 없습니다 — 다시 연결하세요") from None
        if integration.kind != IntegrationKind.MAIL or self._mail is None:
            return token
        expires_at = aware(integration.token_expires_at)
        due = force_refresh or (expires_at is not None and expires_at - TOKEN_REFRESH_MARGIN <= self._clock())
        if not due:
            return token
        if not integration.refresh_token_encrypted:
            self._mark_disconnected(integration, "token_expired")
            raise IntegrationUnavailable("토큰이 만료됐습니다 — 설정에서 다시 연결하세요")
        try:
            refreshed = self._mail.refresh(self._cipher.decrypt(integration.refresh_token_encrypted))
        except UpstreamUnauthorized:
            self._mark_disconnected(integration, "revoked")
            raise IntegrationUnavailable("Google 에서 권한을 거뒀습니다 — 다시 연결하세요") from None
        integration.access_token_encrypted = self._cipher.encrypt(refreshed.access_token)
        integration.token_expires_at = refreshed.expires_at
        integration.updated_at = self._clock()
        return refreshed.access_token

    def _mark_disconnected(self, integration: Any, reason: str) -> None:
        now = self._clock()
        integration.status = IntegrationStatus.DISCONNECTED
        integration.disconnected_reason = reason
        integration.disconnected_at = now
        integration.updated_at = now
        self._repository.notify(
            USER_EVENTS_CHANNEL,
            UserEvent(
                UserEventType.INTEGRATION_CHANGED, integration.member_id, integration_id=str(integration.id), source_kind=integration.kind
            ).to_payload(),
        )


InboxSource = Literal["all", "mail", "slack", "kakao"]
