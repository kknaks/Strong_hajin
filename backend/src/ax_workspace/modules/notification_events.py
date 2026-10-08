"""알림 생성기 — 사건 × 관계 → 알림 한 줄 (SPEC-011 §4.2 · §4.3 · §4.4 · §5 「생성기 하나」 · WORK-013 WP2-BE).

사건 자리(업무 · 요청 · 담당 · 회의 · 메시지 저장 · 연동)는 **「무슨 일이 일어났고 관련자가 누구인가」 만** 넘긴다 —
`NotificationEvent` 하나. 규칙은 전부 여기 한 곳이다:

1. **원칙 ① 행위자 거름** — 사건의 행위자가 받는 사람이면 만들지 않는다(메시지는 `from_me` 줄을 사건 자리가 넘기지 않는다).
2. **원칙 ② 항목 · 설정 거름** — 받는 사람의 설정에서 전체 · 그 테마 · 그 항목 중 하나라도 꺼져 있으면 만들지 않는다.
   설정이 없으면 기본값(시안 `on` — 업무 `comment` 만 끔). 캐시를 두지 않는다 — 사건마다 읽는다(§4.4-2).
3. **원칙 ③ 관계 없음** — 관계가 없는 사람은 사건 자리가 넘기지 않는다. 받는 사람이 **비활성 회원**이면 관계가 없는 것과
   같다(OQ-1109 제안). **예외 M06**(회의에서 빠진 참석자)은 빠지는 순간의 관계로 사건 자리가 넘긴다.
4. **관계가 둘 이상이면 한 줄**(§4.3-3) — 꼬리표 우선 업무 = 담당 > 요청자 > 배정자 > 참조, 회의 = 소유자 > 참석자 > 공유받음
   (OQ-1104 제안). 받는 사람마다 첫 꼬리표 하나.
5. **같은 트랜잭션** — 저장소가 사건을 쓴 세션에서 행을 쓰고 NOTIFY 한다(§4.3-8). 멱등은 `(받는 사람, 원천)`(§4.3-7).
6. **슬랙 채널 합침**(§4.3-4) — `coalesce_key` 가 있는 사건은 그 받는 사람의 **안 읽은** 같은 열쇠 줄을 고친다(저장소 몫).

이 모듈은 다른 업무 모듈을 import 하지 않는다 — 업무 · 회의 · 메시지 모듈이 이것을 import 해 사건을 넘긴다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

# ── 종류 · 테마 · 항목 (§4.2-1 · §4.4-1) ──────────────────────────────────────────────────

THEMES: dict[str, tuple[str, ...]] = {
    "work": ("request", "assign", "answer", "report", "rework", "change", "comment", "unblock"),
    "message": ("mail", "slack", "kakao"),
    "meeting": ("invite", "change", "minutes", "minutes-fail", "share"),
}
#: 기본 꺼짐 항목 — 시안 `NOTIFY_GROUPS` 의 `on: false` 는 업무 댓글 하나.
DEFAULT_OFF: frozenset[tuple[str, str]] = frozenset({("work", "comment")})

#: `kind` → (테마, 항목). `message.integration_lost` 는 항목이 그 채널이라 사건이 정한다(`item=`).
KINDS: dict[str, tuple[str, str | None]] = {
    "work.request_received": ("work", "request"),
    "work.request_answered": ("work", "answer"),
    "work.assignment_answered": ("work", "answer"),
    "work.assigned": ("work", "assign"),
    "work.changed": ("work", "change"),
    "work.proposal_answered": ("work", "answer"),
    "work.completion_reported": ("work", "report"),
    "work.rework_requested": ("work", "rework"),
    "work.predecessor_released": ("work", "unblock"),
    "work.commented": ("work", "comment"),
    "message.mail": ("message", "mail"),
    "message.slack": ("message", "slack"),
    "message.kakao": ("message", "kakao"),
    "message.integration_lost": ("message", None),
    "meeting.invited": ("meeting", "invite"),
    "meeting.changed": ("meeting", "change"),
    "meeting.minutes_ready": ("meeting", "minutes"),
    "meeting.minutes_failed": ("meeting", "minutes-fail"),
    "meeting.shared": ("meeting", "share"),
}
#: 붉은 표식(§4.2-1) — 연동 끊김 · 회의록 실패.
FAILURE_KINDS = frozenset({"message.integration_lost", "meeting.minutes_failed"})
#: 메시지함 읽음이 함께 읽는 종류(§4.5-3) — 연동 끊김 줄은 메시지가 아니라 빠진다.
MESSAGE_KINDS = frozenset({"message.mail", "message.slack", "message.kakao"})

#: 옛 종류 두 개(D-30 흡수) — 마이그레이션 전 행을 읽을 때도 같은 뜻으로 보인다.
LEGACY_KINDS: dict[str, tuple[str, str, str, dict[str, Any]]] = {
    "work_request.received": ("work.request_received", "request", "assignee", {"resubmitted": False}),
    "work_request.accepted": ("work.request_answered", "answer", "requester", {"answer": "accepted"}),
}

#: 꼬리표 우선(§4.3-3) — 앞일수록 이긴다. 업무와 회의의 꼬리표는 겹치지 않는다.
RELATION_PRIORITY: tuple[str, ...] = (
    "assignee", "requester", "assigner", "cc",
    "owner", "attendee", "shared",
    "to", "mail-cc", "mail-other", "dm", "mention", "channel", "kakao-direct", "kakao-group", "integration",
)


def theme_item(kind: str, item: str | None = None) -> tuple[str, str]:
    theme, fixed = KINDS[kind]
    chosen = item or fixed
    if chosen is None or chosen not in THEMES[theme]:
        raise ValueError(f"{kind} 의 항목이 없습니다: {item!r}")
    return theme, chosen


# ── 설정 (§4.4) ──────────────────────────────────────────────────────────────────────────


def default_settings() -> dict[str, Any]:
    return {
        "enabled": True,
        "themes": {
            theme: {"on": True, "items": {item: (theme, item) not in DEFAULT_OFF for item in items}}
            for theme, items in THEMES.items()
        },
    }


def settings_allow(settings: dict[str, Any] | None, theme: str, item: str) -> bool:
    """전체 · 테마 · 항목이 모두 켜져 있는가(§4.3-2). 저장한 값에 없는 칸은 기본값으로 본다."""
    defaults = default_settings()
    current = settings or defaults
    if not current.get("enabled", True):
        return False
    group = (current.get("themes") or {}).get(theme) or defaults["themes"][theme]
    if not group.get("on", True):
        return False
    items = group.get("items") or {}
    return bool(items.get(item, defaults["themes"][theme]["items"][item]))


# ── 사건 ─────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Recipient:
    member_id: str
    relation: str


@dataclass(frozen=True, slots=True)
class NotificationEvent:
    """사건 하나 — 사건 자리가 아는 것만. `row` 는 SPEC-011 §4.2 의 행 id(시험 · 기록용)."""

    row: str
    kind: str
    #: 멱등 원천(§4.3-7) — 그 사건의 원장 · 감사 행. 받는 사람마다 한 번.
    source_kind: str
    source_id: str
    recipients: tuple[Recipient, ...]
    #: 대상 — `{type, id, title}`. title 은 만들 때의 값으로 저장한다(§4.5-2-4).
    subject: dict[str, Any]
    #: 회원 행위자(원칙 ① · 표시 이름은 저장소가 붙인다). 시스템 · 외부 발신자면 None.
    actor_member_id: str | None = None
    #: 외부 발신자 이름(메일 · 슬랙 · 카톡).
    actor_name: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    #: 누르면 갈 곳(§4.5-2-3). 읽을 때 인가로 `null` 이 될 수 있다.
    target: dict[str, Any] | None = None
    #: 테마 안 항목을 사건이 정하는 종류(연동 끊김 = 그 채널).
    item: str | None = None
    #: 슬랙 채널 합침 열쇠 — `slack-channel:{room_id}`.
    coalesce_key: str | None = None


@dataclass(frozen=True, slots=True)
class NotificationDraft:
    """받는 사람 한 명의 줄 — 저장소가 쓴다."""

    recipient_member_id: str
    relation: str
    theme: str
    item: str
    failure: bool
    event: NotificationEvent


class NotificationStore(Protocol):
    def is_active_member(self, member_id: str) -> bool: ...
    def settings_for(self, member_id: str) -> dict[str, Any] | None: ...
    def write(self, draft: NotificationDraft) -> Any | None: ...


class Notifier(Protocol):
    def notify(self, event: NotificationEvent) -> list[Any]: ...


def recipients(*pairs: tuple[str | None, str]) -> tuple[Recipient, ...]:
    """(회원 id, 꼬리표) 들 — 비었거나 시스템 자리(`system:…`)는 뺀다."""
    return tuple(
        Recipient(str(member), relation) for member, relation in pairs if member and not str(member).startswith("system:")
    )


def _rank(relation: str) -> int:
    return RELATION_PRIORITY.index(relation) if relation in RELATION_PRIORITY else len(RELATION_PRIORITY)


class NotificationGenerator:
    """사건 → 줄. 규칙은 모듈 머리의 여섯."""

    def __init__(self, store: NotificationStore) -> None:
        self._store = store

    def notify(self, event: NotificationEvent) -> list[Any]:
        theme, item = theme_item(event.kind, event.item)
        chosen: dict[str, str] = {}
        for recipient in event.recipients:
            current = chosen.get(recipient.member_id)
            if current is None or _rank(recipient.relation) < _rank(current):
                chosen[recipient.member_id] = recipient.relation
        written: list[Any] = []
        for member_id, relation in chosen.items():
            if event.actor_member_id is not None and member_id == str(event.actor_member_id):
                continue  # ① 행위자
            if not self._store.is_active_member(member_id):
                continue  # ③ 비활성 = 관계 없음
            if not settings_allow(self._store.settings_for(member_id), theme, item):
                continue  # ② 설정
            row = self._store.write(
                NotificationDraft(member_id, relation, theme, item, event.kind in FAILURE_KINDS, event)
            )
            if row is not None:
                written.append(row)
        return written


class NoNotifier:
    """조립이 생성기를 끼우지 않은 자리(단위 시험 등) — 아무것도 하지 않는다."""

    def notify(self, event: NotificationEvent) -> list[Any]:
        return []


NO_NOTIFIER = NoNotifier()


# ── 메시지 판정 재료 (§4.3-6) — 저장할 때 raw 에서 한 번 ──────────────────────────────────


def _bare(value: str | None) -> str:
    from email.utils import parseaddr

    return parseaddr(value or "")[1].strip().lower()


def _addresses(value: str | None) -> list[str]:
    from email.utils import getaddresses

    return [address.strip().lower() for _, address in getaddresses([value or ""]) if address]


def _gmail_headers(raw: dict[str, Any]) -> dict[str, str]:
    rows = ((raw.get("payload") or {}).get("headers")) or []
    return {str(row.get("name", "")).lower(): str(row.get("value", "")) for row in rows if isinstance(row, dict)}


def mail_from_me(raw: dict[str, Any], account_key: str | None) -> bool | None:
    """메일 = From 주소가 그 연동 계정 주소인가(대소문자 무시 · addr-spec · D-41). 모르면 None."""
    sender = _bare(_gmail_headers(raw).get("from"))
    if not sender or not account_key:
        return None
    return sender == account_key.strip().lower()


def mail_relation(raw: dict[str, Any], account_key: str | None) -> str:
    """X01 · X02 · X03 — To 에 나 · CC 에 나 · 어디에도 없음(숨은 참조 · 메일링)."""
    headers = _gmail_headers(raw)
    me = (account_key or "").strip().lower()
    if me and me in _addresses(headers.get("to")):
        return "to"
    if me and me in _addresses(headers.get("cc")):
        return "mail-cc"
    return "mail-other"


def mail_sender_name(raw: dict[str, Any]) -> str | None:
    from email.utils import parseaddr

    name, address = parseaddr(_gmail_headers(raw).get("from") or "")
    value = (name or address).strip()
    return value[:300] if value else None


def slack_from_me(raw: dict[str, Any], my_user_id: str | None) -> bool | None:
    """슬랙 = `raw.user` 가 그 연동의 `account_meta.user_id` 인가(이름으로 덮인 `author` 가 아니다 · D-34)."""
    user = raw.get("user")
    if not user or not my_user_id:
        return None
    return str(user) == str(my_user_id)


def slack_mentions_me(raw: dict[str, Any], my_user_id: str | None) -> bool:
    """`<@{내 id}>` 가 본문에 있는가. `@here` · `@channel` · 사용자 그룹은 멘션이 아니다(OQ-1105 제안)."""
    if not my_user_id:
        return False
    text = str(raw.get("text") or "")
    return f"<@{my_user_id}>" in text or f"<@{my_user_id}|" in text


def excerpt(text: str | None, limit: int = 120) -> str | None:
    if not text:
        return None
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


# ── 메시지 사건 (§4.2-3 · X01 ~ X11) — 저장 자리가 부른다 ─────────────────────────────────


def message_notification(
    *,
    kind: str,
    member_id: str,
    integration_id: str,
    account_key: str | None,
    account_label: str | None,
    my_user_id: str | None,
    room: dict[str, Any] | None,
    message_id: str,
    raw: dict[str, Any],
    author: str | None,
    preview: str | None,
    subject: str | None,
    sent_at: str | None,
    attachment_count: int,
    from_me: bool | None,
) -> NotificationEvent | None:
    """실시간 저장 한 줄 → 사건(없으면 None). **내가 보낸 줄(`from_me = true`)은 없다**(X07 · 원칙 ①).

    메일 X01 ~ X03(To · CC · 어디에도 없음) · 슬랙 X04(DM · 그룹 DM) · X05(멘션 — 합치지 않음) · X06(채널 — 방별 합침) ·
    카톡 X08(1:1) · X09(단체방). 백필 · 메우기는 부르는 쪽이 넘기지 않는다(OQ-1101).
    """
    if from_me is True:
        return None
    owner = (member_id,)
    if kind == "mail":
        relation = mail_relation(raw, account_key)
        title = subject or ""
        return NotificationEvent(
            row={"to": "X01", "mail-cc": "X02"}.get(relation, "X03"), kind="message.mail",
            source_kind="external_message", source_id=message_id, recipients=tuple(Recipient(m, relation) for m in owner),
            subject={"type": "mail", "id": message_id, "title": title}, actor_name=mail_sender_name(raw) or author,
            data={"subject": title, "attachment_count": attachment_count, "account": account_label, "sent_at": sent_at},
            target={"surface": "inbox", "source": "mail", "message_id": message_id},
        )
    if room is None:
        return None
    room_id, room_type, room_name = str(room["id"]), str(room.get("room_type") or ""), str(room.get("name") or "")
    common = {"room_name": room_name, "excerpt": excerpt(preview), "sent_at": sent_at, "last_sent_at": sent_at}
    if kind == "slack":
        thread_ts = raw.get("thread_ts")
        target = {"surface": "inbox", "source": "slack", "room_id": room_id, "message_id": message_id}
        if thread_ts and str(thread_ts) != str(raw.get("ts") or ""):
            target["thread_ts"] = str(thread_ts)
        if room_type in {"dm", "group_dm"}:
            row, relation, coalesce = "X04", "dm", None
        elif slack_mentions_me(raw, my_user_id):
            row, relation, coalesce = "X05", "mention", None
        else:
            row, relation, coalesce = "X06", "channel", f"slack-channel:{room_id}"
        return NotificationEvent(
            row=row, kind="message.slack", source_kind="external_message", source_id=message_id,
            recipients=tuple(Recipient(m, relation) for m in owner),
            subject={"type": "room", "id": room_id, "title": room_name}, actor_name=author,
            data={**common, "count": 1, "senders": [author] if author else [], "sender_count": 1 if author else 0}, target=target, coalesce_key=coalesce,
        )
    if kind == "kakao":
        relation = "kakao-direct" if room_type == "direct" else "kakao-group"
        return NotificationEvent(
            row="X08" if relation == "kakao-direct" else "X09", kind="message.kakao", source_kind="external_message",
            source_id=message_id, recipients=tuple(Recipient(m, relation) for m in owner),
            subject={"type": "room", "id": room_id, "title": room_name}, actor_name=author, data=common,
            target={"surface": "inbox", "source": "kakao", "room_id": room_id, "message_id": message_id},
        )
    return None


def integration_lost_notification(
    *,
    kind: str,
    member_id: str,
    integration_id: str,
    account_label: str | None,
    occurred_at: str,
    room: dict[str, Any] | None = None,
) -> NotificationEvent:
    """X10 연동 끊김 · X11 방 접근 잃음 — 꼬리표 `integration` · 항목 = 그 채널 · 붉은 표식(§4.2-3 · D-20)."""
    if room is None:
        return NotificationEvent(
            row="X10", kind="message.integration_lost", source_kind="integration_lost", source_id=f"{integration_id}@{occurred_at}",
            recipients=(Recipient(member_id, "integration"),), item=kind,
            subject={"type": "integration", "id": integration_id, "title": account_label or ""},
            data={"channel": kind, "reason": "disconnected", "account": account_label},
            target={"surface": "settings", "tab": kind},
        )
    return NotificationEvent(
        row="X11", kind="message.integration_lost", source_kind="room_access_lost", source_id=f"{room['id']}@{occurred_at}",
        recipients=(Recipient(member_id, "integration"),), item=kind,
        subject={"type": "room", "id": str(room["id"]), "title": str(room.get("name") or "")},
        data={"channel": kind, "reason": "room_access_lost", "account": account_label, "room_name": room.get("name")},
        target={"surface": "settings", "tab": kind},
    )
