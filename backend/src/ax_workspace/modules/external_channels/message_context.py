"""메시지함 메시지 → AX 맥락 — **입구와 떨어진 서버 함수 하나** (SPEC-008 §4.8 ② · DEC-009 D-31 · D-32 · D-37).

대화의 참고 자료 `inbox_message` 는 메시지를 **가리키기만** 한다. 이 모듈이 우리 DB 에서 그 메시지 둘레를 조합해
「우리 모양 JSON」 을 만든다 — 지금은 대화 턴(AX 업무 생성 · 요약)이 부르고, 다음 판의 「메일 도착 이벤트 → AX 초안」
(자동 추천)이 같은 함수를 부른다. 그래서 HTTP·대화 어느 쪽도 모른다 — 회원 id 와 메시지 id 만 받는다.

| 고른 것 | `range` | `lines` |
|---|---|---|
| 슬랙·카톡 방의 최상위 메시지 | `around_100` | 그 메시지 기준 위 100줄 + 그 메시지 + 아래 100줄(최상위 흐름 · 시각 순 · 모자라면 있는 만큼) |
| 슬랙 스레드 안 답글 | `thread` | 그 스레드 전체(부모 포함) |
| 메일 | `mail` | 그 메일 한 통 |

한 줄 = `{at, sender, text, attachments(이름만), thread_reply_count}` · 고른 메시지에만 `target: true`. 원문 JSON
(blocks·리액션·내부 id·URL 미리보기)은 싣지 않는다. 멘션·채널 id 는 이름으로 푼다(화면 렌더 규칙 D-28 과 같다).
메일 글자는 소독한 안전본에서 글자만(인용 접기 제외).

`label` 은 **실제로 실은 범위**를 사람이 읽는 한 줄이다 — 말풍선 아래에 그대로 선다(WP4 계약 고정 1).
「위아래 100줄」 이라는 숫자를 쓰지 않는다 — 방 첫머리면 30건일 수도, 몇 주치일 수도 있다(H-7).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import html as html_lib
import re
from typing import Any, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from typing_extensions import TypedDict

from ax_workspace.modules.external_channels.inbox_html import (
    SAFE_HTML_PREFIX,
    parse_gmail_message,
    safe_html_text,
    sanitize_mail_html,
    text_to_safe_html,
)

#: 기준 메시지 위·아래로 싣는 최상위 메시지 수(D-32).
AROUND_LINES = 100
#: 스레드 하나에서 싣는 상한 — 슬랙 스레드는 대개 짧지만, 끝없는 스레드가 프롬프트를 먹지 않게.
THREAD_LIMIT = 1000
#: 라벨·`at` 의 시간대 — 사옥 지역 시각.
LOCAL_ZONE = ZoneInfo("Asia/Seoul")

RANGE_AROUND = "around_100"
RANGE_THREAD = "thread"
RANGE_MAIL = "mail"

SOURCE_LABELS = {"slack": "슬랙", "kakao": "카톡", "mail": "메일"}


class MessageContextLine(TypedDict, total=False):
    at: str
    sender: str | None
    text: str
    attachments: list[str]
    thread_reply_count: int
    target: bool


class MessageContextDocument(TypedDict):
    source: str
    room: str | None
    range: str
    lines: list[MessageContextLine]


@dataclass(frozen=True, slots=True)
class MessageReference:
    """참고 자료로 고를 수 있는 메시지 하나 — 소유가 확인된 것. 접수 때 해석(`summary`)과 출처 표시(`label`)가 읽는다."""

    message_id: str
    source_kind: str
    room_id: str | None
    #: 출처 링크 글자 — 「슬랙 #채널명」 · 「카톡 {방 이름}」 · 「메일 {제목}」 (S8 §4.8 ③).
    origin_label: str


@dataclass(frozen=True, slots=True)
class ComposedMessageContext:
    reference: MessageReference
    document: MessageContextDocument
    #: 말풍선 아래 한 줄 — 실제로 실은 범위(WP4 계약 고정 1).
    label: str


class MessageContextRepository(Protocol):
    def owned_message(self, member_id: str, message_id: UUID) -> tuple[Any, Any | None, Any] | None:
        """`(메시지, 방|None, 연동)` — 그 회원 소유 연동의 메시지이고 연동·방이 소프트 딜리트되지 않았을 때만."""

    def room_neighbors(
        self, room_id: UUID, *, at: datetime, message_id: UUID, before: int, after: int
    ) -> tuple[list[Any], list[Any]]:
        """기준 메시지 **앞**(오래된 쪽, 가까운 것부터) · **뒤**(새 쪽, 가까운 것부터)의 최상위 메시지."""

    def thread_messages(self, room_id: UUID, thread_key: str, *, limit: int) -> list[Any]:
        """그 스레드 전체(부모 포함) — 시각 순."""

    def attachments_of(self, message_ids: Sequence[UUID]) -> dict[UUID, list[Any]]: ...


class MessageNotFound(LookupError):
    """남의 메시지 · 지운 방·연동의 메시지 · 없는 메시지 — 존재를 가린다(404)."""


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _local(value: datetime) -> datetime:
    return _aware(value).astimezone(LOCAL_ZONE)


def _short(value: datetime) -> str:
    return _local(value).strftime("%m/%d %H:%M")


def is_thread_reply(message: Any) -> bool:
    """슬랙 스레드 **안 답글**인가 — 부모(자기 자신이 스레드 키)는 아니다."""
    return bool(message.thread_key) and message.thread_key != message.external_key


def room_display(room: Any | None, source_kind: str) -> str | None:
    """방 이름 — 슬랙 채널은 `#이름`, DM·그룹 DM·카톡 방은 이름 그대로. 메일은 방이 없다."""
    if room is None:
        return None
    name = (room.name or room.external_id or "").strip()
    if source_kind == "slack" and room.room_type in {"channel", "private"}:
        return f"#{name}"
    return name


def mail_subject(message: Any) -> str:
    return (message.subject or "").strip() or "(제목 없음)"


def message_reference(message: Any, room: Any | None) -> MessageReference:
    """출처 링크 글자까지 — 대화 참고 자료 해석과 업무 출처(`origin.message`)가 같은 값을 쓴다."""
    kind = str(message.source_kind)
    if kind == "mail":
        label = f"메일 {mail_subject(message)}"
    else:
        label = f"{SOURCE_LABELS.get(kind, kind)} {room_display(room, kind) or ''}".strip()
    return MessageReference(str(message.id), kind, str(room.id) if room is not None else None, label)


# ── 슬랙 글자 — 화면 렌더(`inboxModel.ts` `parseInline`)와 같은 규칙으로 이름을 푼다 ─────────────────────────

_SLACK_TOKEN = re.compile(r"<([@#!])?([^>|]+)(?:\|([^>]+))?>")


def _slack_name(user_id: str, users: dict[str, Any], message: dict[str, Any] | None = None) -> str:
    tag = users.get(user_id) or {}
    if isinstance(tag, dict) and tag.get("name"):
        return str(tag["name"])
    if message is not None and message.get("user") == user_id:
        profile = message.get("user_profile") or {}
        for key in ("display_name", "real_name", "name"):
            if profile.get(key):
                return str(profile[key])
    return user_id


def render_slack_text(text: str, users: dict[str, Any]) -> str:
    """`<@U|이름>`·`<#C|채널>`·`<!here>`·`<url|글>` → 사람이 읽는 글자. 서식 기호(`*`·`_`)는 그대로 둔다."""

    def replace(match: re.Match[str]) -> str:
        sigil, target, label = match.group(1), match.group(2), match.group(3)
        if sigil == "@":
            return "@" + (label.lstrip("@") if label else _slack_name(target, users))
        if sigil == "#":
            return f"#{label or target}"
        if sigil == "!":
            if label:
                return "@" + label.lstrip("@")
            return "@그룹" if target.startswith("subteam^") else f"@{target}"
        if label and label != target:
            return f"{label} ({target})"
        return target

    return html_lib.unescape(_SLACK_TOKEN.sub(replace, text or ""))


def _slack_sender(message: Any, users: dict[str, Any]) -> str | None:
    raw = message.raw or {}
    if raw.get("user"):
        return _slack_name(str(raw["user"]), users, raw)
    return raw.get("username") or (raw.get("bot_profile") or {}).get("name") or message.author


def _room_line(message: Any, users: dict[str, Any], attachments: list[Any], *, target: bool) -> MessageContextLine:
    raw = message.raw or {}
    if message.source_kind == "slack":
        sender = _slack_sender(message, users)
        text = render_slack_text(str(raw.get("text") or ""), users)
    else:
        sender = message.author
        text = str(raw.get("text") or message.preview or "")
    if raw.get("ax_deleted"):
        text = "(삭제된 메시지)"
    elif raw.get("edited"):
        text = f"{text} (수정됨)"
    line: MessageContextLine = {
        "at": _local(message.sent_at).isoformat(),
        "sender": sender,
        "text": text,
        "attachments": [str(item.name) for item in attachments if getattr(item, "name", None)],
        "thread_reply_count": int(raw.get("reply_count") or 0) if not is_thread_reply(message) else 0,
    }
    if target:
        line["target"] = True
    return line


def _mail_text(message: Any) -> str:
    """소독한 안전본에서 글자만 — 안전본이 아직 없으면(처음 여는 메일) 같은 규칙으로 만들어 쓴다(저장하지 않는다)."""
    safe = message.safe_html if message.safe_html and message.safe_html.startswith(SAFE_HTML_PREFIX) else None
    if safe is None:
        parts = parse_gmail_message(message.raw or {})
        safe = sanitize_mail_html(parts.html) if parts.html else text_to_safe_html(parts.text or message.preview or "")
    return safe_html_text(safe)


class MessageContextComposer:
    """회원 하나의 메시지를 맥락 JSON 으로 — 입구(단추·자동 추천)는 이 클래스만 부른다."""

    def __init__(self, repository: MessageContextRepository) -> None:
        self._repository = repository

    def reference(self, member_id: str, message_id: UUID) -> MessageReference:
        """소유 확인 + 출처 글자. 남의 것·지운 방·연동이면 `MessageNotFound`."""
        message, room, _ = self._owned(member_id, message_id)
        return message_reference(message, room)

    def compose(self, member_id: str, message_id: UUID) -> ComposedMessageContext:
        message, room, _ = self._owned(member_id, message_id)
        reference = message_reference(message, room)
        kind = reference.source_kind
        if kind == "mail" or room is None:
            attachments = self._repository.attachments_of([message.id]).get(message.id, [])
            line: MessageContextLine = {
                "at": _local(message.sent_at).isoformat(),
                "sender": message.author,
                "text": f"{mail_subject(message)}\n\n{_mail_text(message)}".strip(),
                "attachments": [str(item.name) for item in attachments if getattr(item, "name", None)],
                "thread_reply_count": 0,
                "target": True,
            }
            document: MessageContextDocument = {"source": "mail", "room": None, "range": RANGE_MAIL, "lines": [line]}
            return ComposedMessageContext(reference, document, f"메일 · {mail_subject(message)}")
        users = dict((room.room_meta or {}).get("users") or {})
        if kind == "slack" and is_thread_reply(message):
            rows = self._repository.thread_messages(room.id, str(message.thread_key), limit=THREAD_LIMIT)
            if all(row.id != message.id for row in rows):
                rows = sorted([*rows, message], key=lambda row: (_aware(row.sent_at), str(row.id)))
            range_ = RANGE_THREAD
        else:
            older, newer = self._repository.room_neighbors(
                room.id, at=_aware(message.sent_at), message_id=message.id, before=AROUND_LINES, after=AROUND_LINES
            )
            rows = [*reversed(older), message, *newer]
            range_ = RANGE_AROUND
        attachments = self._repository.attachments_of([row.id for row in rows])
        lines = [
            _room_line(row, users, attachments.get(row.id, []), target=row.id == message.id) for row in rows
        ]
        document = {"source": kind, "room": room_display(room, kind), "range": range_, "lines": lines}
        source = SOURCE_LABELS.get(kind, kind)
        if range_ == RANGE_THREAD:
            label = f"{source} · 스레드 · {len(lines)}건"
        else:
            span = f"{_short(rows[0].sent_at)}~{_short(rows[-1].sent_at)}"
            label = f"{source} · {room_display(room, kind)} · {span} · {len(lines)}건"
        return ComposedMessageContext(reference, document, label)

    def _owned(self, member_id: str, message_id: UUID) -> tuple[Any, Any | None, Any]:
        found = self._repository.owned_message(member_id, message_id)
        if found is None:
            raise MessageNotFound("message was not found")
        return found
