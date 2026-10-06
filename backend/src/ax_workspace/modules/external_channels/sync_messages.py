"""수집한 원문을 저장 한 줄로 — 슬랙·Gmail 의 정규화 (BE-2 · SPEC-008 §4.1 · D-28).

원문은 **그대로** `raw` 에 둔다. 여기서 뽑는 것은 목록 카드가 원문을 다시 풀지 않게 하는 요약(제목·보낸 사람·두
줄)과 중복 방지 키·첨부 메타뿐이다. 첨부 바이트는 받지 않는다 — 메일·슬랙 첨부는 받을 때 중계한다(D-29).

첨부 `aid`(API 의 첨부 id, BE-3 중계 경로가 받는다):
- 슬랙 = SHA-256("{integration_id}:{channel}:{ts}:{file_id}") hex · `external_ref` = 슬랙 file id
- Gmail = SHA-256("{integration_id}:{message_id}:{part_id}") hex · `external_ref` = JSON
  `{"part_id": …, "attachment_id": …}` — Gmail 의 attachmentId 는 받을 때마다 글자가 바뀔 수 있어 partId 가 안정 키다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import html
import json
from typing import Any


@dataclass(frozen=True, slots=True)
class NormalizedAttachment:
    aid: str
    seq: int
    kind: str
    name: str
    size: int | None
    mime: str | None
    external_ref: str | None
    state: str = "reference"


@dataclass(frozen=True, slots=True)
class NormalizedMessage:
    container_key: str
    external_key: str
    thread_key: str | None
    sent_at: datetime
    subject: str | None
    author: str | None
    preview: str | None
    raw: dict[str, Any]
    attachments: tuple[NormalizedAttachment, ...] = field(default_factory=tuple)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def attachment_kind(mime: str | None) -> str:
    major = (mime or "").split("/", 1)[0].lower()
    return {"image": "image", "video": "video", "audio": "audio"}.get(major, "file")


# ── 슬랙 ───────────────────────────────────────────────────────────────────────────────

#: 원문 메시지가 아니라 다른 메시지에 대한 사건 — 따로 다룬다(수정·삭제) 또는 버린다(옛 「답글 달림」 알림).
SLACK_EDIT_SUBTYPES = frozenset({"message_changed", "message_deleted", "message_replied"})


def slack_sent_at(ts: str) -> datetime:
    return datetime.fromtimestamp(float(ts), UTC)


def normalize_slack_message(integration_id: str, channel: str, message: dict[str, Any]) -> NormalizedMessage:
    ts = str(message["ts"])
    attachments = []
    for seq, item in enumerate(message.get("files") or []):
        if not isinstance(item, dict) or not item.get("id"):
            continue
        mime = item.get("mimetype")
        attachments.append(
            NormalizedAttachment(
                aid=_sha(f"{integration_id}:{channel}:{ts}:{item['id']}"),
                seq=seq,
                kind=attachment_kind(mime),
                name=str(item.get("name") or item.get("title") or item["id"])[:500],
                size=item.get("size") if isinstance(item.get("size"), int) else None,
                mime=str(mime)[:200] if mime else None,
                external_ref=str(item["id"]),
            )
        )
    text = message.get("text") or ""
    return NormalizedMessage(
        container_key=channel,
        external_key=ts,
        thread_key=str(message["thread_ts"]) if message.get("thread_ts") else None,
        sent_at=slack_sent_at(ts),
        subject=None,
        author=str(message.get("user") or message.get("bot_id") or message.get("username") or "")[:500] or None,
        preview=text[:2000] or None,
        raw=message,
        attachments=tuple(attachments),
    )


# ── Gmail ──────────────────────────────────────────────────────────────────────────────


def gmail_headers(message: dict[str, Any]) -> dict[str, str]:
    headers = ((message.get("payload") or {}).get("headers")) or []
    return {str(row.get("name", "")).lower(): str(row.get("value", "")) for row in headers if isinstance(row, dict)}


def _gmail_parts(part: dict[str, Any]):
    yield part
    for child in part.get("parts") or []:
        if isinstance(child, dict):
            yield from _gmail_parts(child)


def normalize_gmail_message(integration_id: str, message: dict[str, Any]) -> NormalizedMessage:
    message_id = str(message["id"])
    headers = gmail_headers(message)
    attachments = []
    for part in _gmail_parts(message.get("payload") or {}):
        body = part.get("body") or {}
        if not part.get("filename") or not body.get("attachmentId"):
            continue
        part_id = str(part.get("partId") or len(attachments))
        mime = part.get("mimeType")
        attachments.append(
            NormalizedAttachment(
                aid=_sha(f"{integration_id}:{message_id}:{part_id}"),
                seq=len(attachments),
                kind=attachment_kind(mime),
                name=str(part["filename"])[:500],
                size=body.get("size") if isinstance(body.get("size"), int) else None,
                mime=str(mime)[:200] if mime else None,
                external_ref=json.dumps({"part_id": part_id, "attachment_id": body["attachmentId"]}),
            )
        )
    internal = message.get("internalDate")
    sent_at = (
        datetime.fromtimestamp(int(internal) / 1000, UTC) if str(internal or "").isdigit() else datetime.now(UTC)
    )
    snippet = html.unescape(str(message.get("snippet") or ""))
    return NormalizedMessage(
        container_key="",
        external_key=message_id,
        thread_key=str(message["threadId"]) if message.get("threadId") else None,
        sent_at=sent_at,
        subject=headers.get("subject", "")[:1000] or None,
        author=headers.get("from", "")[:500] or None,
        preview=snippet[:2000] or None,
        raw=message,
        attachments=tuple(attachments),
    )
