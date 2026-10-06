"""수집 원문 → 저장 한 줄 — 키·요약·첨부 aid 규칙 (WORK-011 BE-2)."""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json

from ax_workspace.modules.external_channels.sync_messages import (
    attachment_kind,
    normalize_gmail_message,
    normalize_slack_message,
)


def test_slack_message_keeps_the_raw_and_keys_by_channel_and_ts() -> None:
    raw = {"type": "message", "user": "U1", "text": "hi", "ts": "1790000001.000100", "thread_ts": "1790000000.000100",
           "files": [{"id": "F1", "name": "v.mp4", "mimetype": "video/mp4", "size": 5}]}
    message = normalize_slack_message("I1", "C1", raw)
    assert (message.container_key, message.external_key, message.thread_key) == ("C1", "1790000001.000100", "1790000000.000100")
    assert message.raw is raw and message.author == "U1" and message.preview == "hi"
    assert message.sent_at == datetime.fromtimestamp(1790000001.0001, UTC)
    [file] = message.attachments
    assert file.aid == hashlib.sha256(b"I1:C1:1790000001.000100:F1").hexdigest()
    assert (file.kind, file.state, file.external_ref) == ("video", "reference", "F1")


def test_gmail_message_summarises_headers_and_keys_attachments_by_part() -> None:
    raw = {"id": "M1", "threadId": "T1", "internalDate": "1790000000000", "snippet": "a &lt; b",
           "payload": {"headers": [{"name": "SUBJECT", "value": "s"}, {"name": "from", "value": "x@y"}],
                       "parts": [{"partId": "0", "mimeType": "text/html", "body": {"size": 3}},
                                 {"partId": "1", "parts": [{"partId": "1.1", "filename": "p.png", "mimeType": "image/png",
                                                            "body": {"attachmentId": "A", "size": 9}}]}]}}
    message = normalize_gmail_message("I1", raw)
    assert (message.container_key, message.external_key, message.thread_key) == ("", "M1", "T1")
    assert (message.subject, message.author, message.preview) == ("s", "x@y", "a < b")
    [image] = message.attachments
    assert image.aid == hashlib.sha256(b"I1:M1:1.1").hexdigest() and image.kind == "image" and image.size == 9
    assert json.loads(image.external_ref) == {"part_id": "1.1", "attachment_id": "A"}


def test_attachment_kind_follows_the_mime_major_type() -> None:
    assert [attachment_kind(m) for m in ("image/jpeg", "audio/m4a", "application/zip", None)] == ["image", "audio", "file", "file"]
