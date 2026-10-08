"""알림 목록 · 읽음 · 설정 · 사건 채널 항목 (SPEC-011 §4.4 · §4.5 · §4.1-3).

알림 **생성**은 `modules/notification_events.py`(생성기 하나)다. 여기는 이미 선 알림을 받는 사람에게 보이는 쪽이다.

**읽기 인가(§4.5-2-4)** — 줄은 남고 `target` 만 인가로 가른다. 제목 · 행위자 · 값은 **만들 때 저장한 값**이다
(회의에서 빠진 사람 · 취소된 회의 · 철회된 요청처럼 알림을 받는 이유가 곧 자원을 못 보게 된 일이 있다). 받는 사람
확인(남의 알림은 404)은 그대로다. 대상을 지금 열 수 있는지는 조립이 넘기는 `target_open` 하나가 답한다.
"""
from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import UTC, timedelta
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import TypedDict

from ax_workspace.modules.errors import ResourceNotFound
from ax_workspace.modules.notification_events import LEGACY_KINDS, THEMES, default_settings
from ax_workspace.modules.organization_access.domain import Principal


class NotificationNotFound(RuntimeError, ResourceNotFound):
    pass


class NotificationQueryInvalid(ValueError):
    """모르는 테마 · 범위 밖 `limit` · 서버가 주지 않은 `cursor` — 422(SPEC-011 Validation)."""


class NotificationSettingsInvalid(ValueError):
    """설정 모양이 다르다 — 테마 셋 · 항목 16 이 정확히 있어야 하고 값은 bool(422)."""


class NotificationSettingsConflict(RuntimeError):
    """`version` 이 다르다 — 다른 창이 먼저 저장했다(409 `SETTINGS_VERSION_CONFLICT`)."""

    code = "SETTINGS_VERSION_CONFLICT"


class NotificationReadCommand(BaseModel):
    model_config = ConfigDict(extra='forbid')
    notification_id: UUID = Field(title='읽음 처리할 알림')


class NotificationItem(TypedDict):
    """알림 한 줄(SPEC-011 §4.5-2) — 목록 · 읽음 · SSE `notification.upserted` · MCP 가 같은 모양을 쓴다."""

    notification_id: str
    seq: int | None
    kind: str
    theme: str
    item: str
    relation: str
    failure: bool
    actor: dict[str, Any] | None
    subject: dict[str, Any] | None
    data: dict[str, Any]
    target: dict[str, Any] | None
    created_at: str
    updated_at: str
    read_at: str | None


class NotificationPage(TypedDict):
    items: list[NotificationItem]
    next_cursor: str | None


class NotificationUnread(TypedDict):
    all: int
    work: int
    message: int
    meeting: int


class NotificationSummary(TypedDict):
    unread: NotificationUnread


class NotificationReadAllResult(TypedDict):
    read: int


class NotificationReplay(TypedDict):
    """이어 받기 한 번의 결과(SPEC-011 §4.1-3). `outcome` 이 `replayed` 일 때만 `items` 를 보낸다."""

    outcome: Literal["replayed", "no_base", "overflow"]
    items: list[NotificationItem]


#: 이어 받기 상한 — 넘으면 보내지 않고 `resync(replay_overflow)` 만(SPEC-011 §4.1-3 ③).
REPLAY_LIMIT = 200
#: 겹침 창 — 순번은 `nextval` 때 정해지고 보이는 순서는 커밋 순서라, 기준 줄보다 이만큼 앞선 줄까지 다시 준다(§4.1-3-1).
REPLAY_OVERLAP = timedelta(seconds=60)
#: 목록 한 쪽 — 기본 30 · 최대 100(§4.5-1).
PAGE_DEFAULT, PAGE_MAX = 30, 100


class NotificationRepository(Protocol):
    def page_for(self, recipient_member_id: str, *, theme: str | None, before_seq: int | None, limit: int) -> list[Any]: ...
    def unread_counts(self, recipient_member_id: str) -> dict[str, int]: ...
    def for_recipient(self, notification_id: UUID, recipient_member_id: str, *, lock: bool = False) -> Any | None: ...
    def mark_read(self, record: Any) -> None: ...
    def mark_all_read(self, recipient_member_id: str) -> int: ...
    def replay_after(self, recipient_member_id: str, last_seq: int, *, window: timedelta, limit: int) -> list[Any] | None: ...
    def settings_row(self, member_id: str, *, lock: bool = False) -> Any | None: ...
    def save_settings(self, member_id: str, settings: dict[str, Any], version: int) -> Any: ...


def encode_cursor(seq: int) -> str:
    return base64.urlsafe_b64encode(f"s{seq}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> int | None:
    """서버가 준 커서만 — 모양이 틀리면 None(부르는 쪽이 422)."""
    try:
        if not cursor or not all(char.isalnum() or char in "-_" for char in cursor):
            return None
        text = base64.urlsafe_b64decode((cursor + "=" * (-len(cursor) % 4)).encode()).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    if not text.startswith("s") or not text[1:].isdigit():
        return None
    return int(text[1:])


def _iso(value: Any) -> str | None:
    """시간대 없는 값(sqlite 는 시간대를 저장하지 않는다)은 UTC 로 — 같은 순간이 두 글자가 되지 않게."""
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


def notification_item(row: Any, *, target_open: bool) -> NotificationItem:
    """저장한 값으로 줄을 만든다. 옛 종류(테마 칸이 빈 행 — 마이그레이션 전)는 같은 뜻의 새 종류로 보인다(D-30)."""
    kind, theme, item, relation, data = row.kind, row.theme, row.item, row.relation, dict(row.data or {})
    if theme is None and row.kind in LEGACY_KINDS:
        kind, item, relation, legacy = LEGACY_KINDS[row.kind]
        theme, data = "work", {**legacy, **data}
    actor = row.actor
    if actor is None and theme is not None and row.kind in LEGACY_KINDS:
        actor = {"member_id": row.actor_member_id, "display_name": row.actor_member_id}
    return {
        "notification_id": str(row.id),
        "seq": int(row.seq) if row.seq is not None else None,
        "kind": kind,
        "theme": theme or "work",
        "item": item or "",
        "relation": relation or "",
        "failure": bool(row.failure),
        "actor": actor,
        "subject": {"type": row.resource_type, "id": row.resource_id, "title": row.resource_title} if row.resource_id else None,
        "data": data,
        "target": (row.target or _legacy_target(row)) if target_open else None,
        "created_at": _iso(row.created_at) or "",
        "updated_at": _iso(row.updated_at or row.created_at) or "",
        "read_at": _iso(row.read_at),
    }


def _legacy_target(row: Any) -> dict[str, Any] | None:
    if row.resource_type == "work_request":
        return {"surface": "work", "work_request_id": row.resource_id}
    if row.resource_type == "meeting":
        return {"surface": "meetings", "meeting_id": row.resource_id}
    return None


def validate_settings(body: Any) -> dict[str, Any]:
    """`{enabled, themes: {work: {on, items}, message: …, meeting: …}}` — 테마 셋 · 항목 16 **정확히** · 값 bool(§4.4-2)."""
    if not isinstance(body, dict) or set(body) != {"enabled", "themes"}:
        raise NotificationSettingsInvalid("enabled 와 themes 가 정확히 있어야 합니다")
    if not isinstance(body["enabled"], bool):
        raise NotificationSettingsInvalid("enabled 는 bool 입니다")
    themes = body["themes"]
    if not isinstance(themes, dict) or set(themes) != set(THEMES):
        raise NotificationSettingsInvalid("테마는 work · message · meeting 셋입니다")
    clean: dict[str, Any] = {"enabled": body["enabled"], "themes": {}}
    for theme, items in THEMES.items():
        group = themes[theme]
        if not isinstance(group, dict) or set(group) != {"on", "items"} or not isinstance(group["on"], bool):
            raise NotificationSettingsInvalid(f"{theme} 는 on 과 items 가 정확히 있어야 합니다")
        values = group["items"]
        if not isinstance(values, dict) or set(values) != set(items) or not all(isinstance(v, bool) for v in values.values()):
            raise NotificationSettingsInvalid(f"{theme} 의 항목이 다릅니다")
        clean["themes"][theme] = {"on": group["on"], "items": {item: values[item] for item in items}}
    return clean


class NotificationApplication:
    def __init__(self, repository: NotificationRepository, target_open: Callable[[Principal, dict[str, Any]], bool]) -> None:
        self._repository, self._target_open = repository, target_open

    # ── 목록 · 요약 ──
    def list(
        self, principal: Principal, *, theme: str | None = None, cursor: str | None = None, limit: int | None = None
    ) -> NotificationPage:
        if theme is not None and theme not in THEMES:
            raise NotificationQueryInvalid("theme 은 work · message · meeting 가운데 하나입니다")
        size = PAGE_DEFAULT if limit is None else int(limit)
        if not 1 <= size <= PAGE_MAX:
            raise NotificationQueryInvalid(f"limit 는 1 ~ {PAGE_MAX} 입니다")
        before = None
        if cursor is not None:
            before = decode_cursor(cursor)
            if before is None:
                raise NotificationQueryInvalid("cursor 가 올바르지 않습니다")
        rows = self._repository.page_for(str(principal.id), theme=theme, before_seq=before, limit=size)
        page, more = rows[:size], len(rows) > size
        return {
            "items": [self._item(principal, row) for row in page],
            "next_cursor": encode_cursor(int(page[-1].seq)) if more and page else None,
        }

    def summary(self, principal: Principal) -> NotificationSummary:
        counts = self._repository.unread_counts(str(principal.id))
        return {"unread": {key: int(counts.get(key, 0)) for key in ("all", "work", "message", "meeting")}}  # type: ignore[typeddict-item]

    # ── 읽음 ──
    def mark_read(self, principal: Principal, notification_id: UUID) -> NotificationItem:
        row = self._repository.for_recipient(notification_id, str(principal.id), lock=True)
        if row is None:
            raise NotificationNotFound('notification was not found')
        self._repository.mark_read(row)
        return self._item(principal, row)

    def read_all(self, principal: Principal) -> NotificationReadAllResult:
        return {"read": self._repository.mark_all_read(str(principal.id))}

    # ── 설정 ──
    def settings(self, principal: Principal) -> dict[str, Any]:
        row = self._repository.settings_row(str(principal.id))
        if row is None:
            return {**default_settings(), "version": 0}
        return {**validate_settings_or_default(row.settings), "version": int(row.version)}

    def save_settings(self, principal: Principal, body: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(body, dict) or "version" not in body:
            raise NotificationSettingsInvalid("version 이 필요합니다")
        version = body.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 0:
            raise NotificationSettingsInvalid("version 은 0 이상의 정수입니다")
        clean = validate_settings({key: value for key, value in body.items() if key != "version"})
        row = self._repository.settings_row(str(principal.id), lock=True)
        current = int(row.version) if row is not None else 0
        if version != current:
            raise NotificationSettingsConflict("다른 창이 먼저 저장했습니다 — 다시 읽어 주세요")
        saved = self._repository.save_settings(str(principal.id), clean, current + 1)
        return {**clean, "version": int(saved.version)}

    # ── 사건 채널(SSE) ──
    def event_item(self, principal: Principal, notification_id: UUID) -> NotificationItem | None:
        """사건 채널이 보낼 항목 하나 — 받는 사람의 것일 때만(읽기 인가를 이 한 곳에서 · §4.1-4)."""
        row = self._repository.for_recipient(notification_id, str(principal.id))
        if row is None or row.seq is None:
            return None
        return self._item(principal, row)

    def replay(self, principal: Principal, last_seq: int) -> NotificationReplay:
        """`last_seq` 뒤에 놓쳤을 수 있는 알림 — 겹침 창 포함, 순번 순(SPEC-011 §4.1-3)."""
        rows = self._repository.replay_after(str(principal.id), last_seq, window=REPLAY_OVERLAP, limit=REPLAY_LIMIT)
        if rows is None:
            return {"outcome": "no_base", "items": []}
        if len(rows) > REPLAY_LIMIT:
            return {"outcome": "overflow", "items": []}
        return {"outcome": "replayed", "items": [self._item(principal, row) for row in rows if row.seq is not None]}

    def _item(self, principal: Principal, row: Any) -> NotificationItem:
        target = row.target or _legacy_target(row)
        is_open = target is not None and self._target_open(principal, target)
        return notification_item(row, target_open=is_open)


def validate_settings_or_default(stored: Any) -> dict[str, Any]:
    """저장한 값이 지금 모양과 다르면(항목이 늘었던 옛 판 등) 기본값 위에 아는 칸만 얹는다."""
    try:
        return validate_settings(stored)
    except NotificationSettingsInvalid:
        merged = default_settings()
        if isinstance(stored, dict):
            if isinstance(stored.get("enabled"), bool):
                merged["enabled"] = stored["enabled"]
            for theme, group in (stored.get("themes") or {}).items():
                if theme in merged["themes"] and isinstance(group, dict):
                    if isinstance(group.get("on"), bool):
                        merged["themes"][theme]["on"] = group["on"]
                    for item, value in (group.get("items") or {}).items():
                        if item in merged["themes"][theme]["items"] and isinstance(value, bool):
                            merged["themes"][theme]["items"][item] = value
        return merged
