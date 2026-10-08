from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from ax_workspace.modules.external_channels.events import UserEvent, UserEventType
from ax_workspace.modules.notification_events import (
    MESSAGE_KINDS,
    NotificationDraft,
    NotificationGenerator,
    default_settings,
)
from ax_workspace.platform.persistence import (
    NOTIFICATION_SEQ,
    MemberRecord,
    NotificationRecord,
    NotificationSettingsRecord,
)
from ax_workspace.platform.user_events import publish_user_event

#: 합친 줄에 남기는 보낸 사람 이름 수 — 그 밖은 「외 N명」(화면 몫).
COALESCED_SENDERS = 5


def next_notification_seq(session: Session) -> int:
    """사건 순번 하나 — PostgreSQL 은 전역 시퀀스, 그 밖(sqlite 시험)은 표의 최댓값 + 1 (SPEC-011 §4.1-3)."""
    if session.get_bind().dialect.name == "postgresql":
        return int(session.execute(NOTIFICATION_SEQ.next_value()).scalar_one())
    return int(session.scalar(select(func.coalesce(func.max(NotificationRecord.seq), 0))) or 0) + 1


def publish_notification_upserted(session: Session, record: NotificationRecord, *, created: bool) -> None:
    """`notification.upserted` 를 **이 트랜잭션에** 싣는다 — 짧은 페이로드(`notification_id` · `seq` · `created`)만.

    항목 모양은 싣지 않는다 — SSE 를 내는 API 프로세스가 DB 에서 읽어 인가와 함께 만든다(SPEC-011 §4.1-4).
    어느 프로세스든(API · meeting_worker · external_worker …) 알림을 쓴 그 세션으로 부르면 된다.
    """
    publish_user_event(
        session,
        UserEvent(
            type=UserEventType.NOTIFICATION_UPSERTED,
            member_id=str(record.recipient_member_id),
            notification_id=str(record.id),
            seq=int(record.seq) if record.seq is not None else None,
            created=created,
        ),
    )


def publish_notification_read(
    session: Session, member_id: str, *, notification_ids: list[str] | None = None, theme: str | None = None
) -> None:
    """`notification.read` — 건별이면 `{"notification_ids": [..]}`, 모두 읽음이면 `{"all": true, "theme": …}` (SPEC-011 §4.1-2)."""
    data: dict[str, object] = {"notification_ids": list(notification_ids)} if notification_ids is not None else {"all": True, "theme": theme}
    publish_user_event(session, UserEvent(type=UserEventType.NOTIFICATION_READ, member_id=member_id, data=data))


def notification_generator(session: Session) -> NotificationGenerator:
    """그 세션의 생성기 — 사건을 쓴 트랜잭션 안에서 행을 쓰고 NOTIFY 한다(SPEC-011 §4.3-8)."""
    return NotificationGenerator(SqlAlchemyNotificationRepository(session))


class SqlAlchemyNotificationRepository:
    """알림 행 · 설정 — 생성기의 저장소(`NotificationStore`)이고, 목록 · 읽음 · 이어 받기의 조회다."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ── 생성기의 저장소 ──
    def is_active_member(self, member_id: str) -> bool:
        member = self._session.get(MemberRecord, member_id)
        return member is not None and member.employment_state == "active"

    def settings_for(self, member_id: str) -> dict[str, Any] | None:
        row = self._session.get(NotificationSettingsRecord, member_id)
        return dict(row.settings) if row is not None else None

    def write(self, draft: NotificationDraft) -> NotificationRecord | None:
        """한 줄 — 같은 (받는 사람, 원천)이면 두 번째는 없다. 합침 열쇠가 있으면 안 읽은 같은 열쇠 줄을 고친다."""
        event = draft.event
        existing = self._session.scalar(
            select(NotificationRecord).where(
                NotificationRecord.recipient_member_id == draft.recipient_member_id,
                NotificationRecord.source_kind == event.source_kind,
                NotificationRecord.source_id == event.source_id,
            )
        )
        if existing is not None:
            return None
        now = datetime.now(UTC)
        if event.coalesce_key:
            merged = self._session.scalar(
                select(NotificationRecord)
                .where(
                    NotificationRecord.recipient_member_id == draft.recipient_member_id,
                    NotificationRecord.coalesce_key == event.coalesce_key,
                    NotificationRecord.read_at.is_(None),
                )
                .order_by(NotificationRecord.seq.desc())
                .limit(1)
                .with_for_update()
            )
            if merged is not None:
                return self._merge(merged, draft, now)
        record = NotificationRecord(
            recipient_member_id=draft.recipient_member_id,
            source_kind=event.source_kind,
            source_id=event.source_id,
            kind=event.kind,
            theme=draft.theme,
            item=draft.item,
            relation=draft.relation,
            failure=draft.failure,
            resource_type=str(event.subject.get("type") or ""),
            resource_id=str(event.subject.get("id") or ""),
            resource_version=None,
            resource_title=str(event.subject.get("title") or "")[:300],
            actor=self._actor(event.actor_member_id, event.actor_name),
            # 옛 칸(NOT NULL · FK) — 회원 행위자가 없으면 받는 사람(화면은 `actor` 만 읽는다).
            actor_member_id=str(event.actor_member_id or draft.recipient_member_id),
            safe_summary="",
            data=self._named(dict(event.data)),
            target=dict(event.target) if event.target is not None else None,
            coalesce_key=event.coalesce_key,
            created_at=now,
            updated_at=now,
            seq=next_notification_seq(self._session),
        )
        self._session.add(record)
        self._session.flush()
        publish_notification_upserted(self._session, record, created=True)
        return record

    def _merge(self, record: NotificationRecord, draft: NotificationDraft, now: datetime) -> NotificationRecord:
        """슬랙 채널 합침(§4.3-4) — count +1 · 보낸 사람 · 마지막 시각 · **새 순번** · `created: false`."""
        incoming = draft.event.data
        data = dict(record.data or {})
        data["count"] = int(data.get("count") or 1) + int(incoming.get("count") or 1)
        senders = list(data.get("senders") or [])
        count = int(data.get("sender_count") or len(senders))
        for name in incoming.get("senders") or []:
            if name and name not in senders:
                # 자른 목록에 없는 이름이면 새 사람으로 센다 — 잘려 나간 이름이 다시 오면 한 번 더 셀 수 있다(근사 · 검수 W-1).
                senders.append(name)
                count += 1
        data["senders"] = senders[:COALESCED_SENDERS]
        data["sender_count"] = count
        for key in ("excerpt", "last_sent_at", "room_name"):
            if incoming.get(key) is not None:
                data[key] = incoming[key]
        record.data = data
        if draft.event.actor_name:
            record.actor = self._actor(None, draft.event.actor_name)
        record.updated_at = now
        record.seq = next_notification_seq(self._session)
        self._session.flush()
        publish_notification_upserted(self._session, record, created=False)
        return record

    def _named(self, data: dict[str, Any]) -> dict[str, Any]:
        """사람 id 를 넘긴 값에 **만들 때의 이름**을 붙인다(§2.1 「서버는 … 사람 · 대상 이름만 준다」) — W14 의 새 담당."""
        member_id = data.get("new_assignee_id")
        if member_id and "new_assignee_name" not in data:
            member = self._session.get(MemberRecord, str(member_id))
            data["new_assignee_name"] = member.display_name if member is not None else str(member_id)
        return data

    def _actor(self, member_id: str | None, external_name: str | None) -> dict[str, Any] | None:
        if member_id:
            member = self._session.get(MemberRecord, str(member_id))
            return {"member_id": str(member_id), "display_name": member.display_name if member is not None else str(member_id)}
        if external_name:
            return {"external_name": external_name[:300]}
        return None

    # ── 설정 ──
    def settings_row(self, member_id: str, *, lock: bool = False) -> NotificationSettingsRecord | None:
        return self._session.get(NotificationSettingsRecord, member_id, with_for_update=lock)

    def save_settings(self, member_id: str, settings: dict[str, Any], version: int) -> NotificationSettingsRecord:
        row = self.settings_row(member_id, lock=True)
        now = datetime.now(UTC)
        if row is None:
            row = NotificationSettingsRecord(member_id=member_id, settings=settings, version=version, updated_at=now)
            self._session.add(row)
        else:
            row.settings, row.version, row.updated_at = settings, version, now
        self._session.flush()
        return row

    # ── 조회 ──
    def page_for(self, recipient_member_id: str, *, theme: str | None, before_seq: int | None, limit: int) -> list[NotificationRecord]:
        """최신(사건 순번) 먼저 · 합친 줄은 고쳐질 때 맨 위로. 순번이 빈 옛 행은 마이그레이션 뒤에 보인다."""
        statement = select(NotificationRecord).where(
            NotificationRecord.recipient_member_id == recipient_member_id, NotificationRecord.seq.is_not(None)
        )
        if theme is not None:
            statement = statement.where(self._theme_is(theme))
        if before_seq is not None:
            statement = statement.where(NotificationRecord.seq < before_seq)
        return list(self._session.scalars(statement.order_by(NotificationRecord.seq.desc()).limit(limit + 1)))

    @staticmethod
    def _theme_is(theme: str):
        # 옛 종류(테마 칸이 빈 행)는 업무다 — 마이그레이션 전에도 같은 탭에 선다.
        if theme == "work":
            return or_(NotificationRecord.theme == "work", and_(NotificationRecord.theme.is_(None), NotificationRecord.kind.like("work_request.%")))
        return NotificationRecord.theme == theme

    def unread_counts(self, recipient_member_id: str) -> dict[str, int]:
        rows = self._session.execute(
            select(NotificationRecord.theme, NotificationRecord.kind, func.count(NotificationRecord.id))
            .where(NotificationRecord.recipient_member_id == recipient_member_id, NotificationRecord.read_at.is_(None))
            .group_by(NotificationRecord.theme, NotificationRecord.kind)
        ).all()
        counts = {"work": 0, "message": 0, "meeting": 0}
        for theme, kind, count in rows:
            key = theme or ("work" if str(kind).startswith("work_request.") else None)
            if key in counts:
                counts[key] += int(count)
        return {"all": sum(counts.values()), **counts}

    def has_unread(self, recipient_member_id: str) -> bool:
        return self._session.scalar(
            select(NotificationRecord.id)
            .where(NotificationRecord.recipient_member_id == recipient_member_id, NotificationRecord.read_at.is_(None))
            .limit(1)
        ) is not None

    def list_for(self, recipient_member_id: str) -> list[NotificationRecord]:
        return list(
            self._session.scalars(
                select(NotificationRecord)
                .where(NotificationRecord.recipient_member_id == recipient_member_id)
                .order_by(NotificationRecord.seq.desc(), NotificationRecord.created_at.desc(), NotificationRecord.id.desc())
            )
        )

    def for_recipient(self, notification_id: UUID, recipient_member_id: str, *, lock: bool = False) -> NotificationRecord | None:
        statement = select(NotificationRecord).where(
            NotificationRecord.id == notification_id,
            NotificationRecord.recipient_member_id == recipient_member_id,
        )
        if lock:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def replay_after(self, recipient_member_id: str, last_seq: int, *, window: timedelta, limit: int) -> list[NotificationRecord] | None:
        """이어 받기 범위(SPEC-011 §4.1-3-1 겹침 창)를 순번 순으로. **기준 줄이 없으면 `None`**(이어 받기 없이 `resync`).

        **커서가 그 회원의 가장 큰 순번보다 크면 `None`**(SPEC-011 Validation — 이어 받기 없이 `resync`).
        범위 = 순번 > 마지막 **+** 순번 ≤ 마지막이지만 `updated_at` ≥ 기준 − `window`. 기준 = 마지막 순번 줄의 `updated_at`,
        그 줄이 없으면(합쳐지며 새 순번을 받았다) **그 순번 이하 가장 큰 순번 줄**의 `updated_at`. `limit` 을 넘으면 `limit + 1`
        줄까지만 읽는다 — 부르는 쪽이 넘침(`replay_overflow`)으로 가른다.
        """
        mine = NotificationRecord.recipient_member_id == recipient_member_id
        base = self._session.scalar(
            select(NotificationRecord)
            .where(mine, NotificationRecord.seq.is_not(None), NotificationRecord.seq <= last_seq)
            .order_by(NotificationRecord.seq.desc())
            .limit(1)
        )
        if base is None:
            return None
        newest = self._session.scalar(select(func.max(NotificationRecord.seq)).where(mine))
        if newest is not None and last_seq > newest:
            # 커서가 그 회원의 가장 큰 순번보다 크다 — 이 서버가 낸 순번이 아니거나 DB 가 바뀌었다.
            return None
        floor = (base.updated_at or base.created_at) - window
        return list(
            self._session.scalars(
                select(NotificationRecord)
                .where(
                    mine,
                    NotificationRecord.seq.is_not(None),
                    or_(
                        NotificationRecord.seq > last_seq,
                        and_(NotificationRecord.seq <= last_seq, NotificationRecord.updated_at >= floor),
                    ),
                )
                .order_by(NotificationRecord.seq)
                .limit(limit + 1)
            )
        )

    # ── 읽음 ──
    def mark_read(self, record: NotificationRecord) -> None:
        if record.read_at is None:
            record.read_at = datetime.now(UTC)
        # 같은 회원의 다른 창·앱이 맞춘다 — 이미 읽은 것이어도 성공이면 낸다(멱등 · SPEC-011 §4.5-1).
        publish_notification_read(self._session, str(record.recipient_member_id), notification_ids=[str(record.id)])

    def mark_all_read(self, recipient_member_id: str) -> int:
        """그 회원의 안 읽은 알림 **전부**(D-40 · 시안 `readAll`) — 테마를 가리지 않는다."""
        result = self._session.execute(
            update(NotificationRecord)
            .where(NotificationRecord.recipient_member_id == recipient_member_id, NotificationRecord.read_at.is_(None))
            .values(read_at=datetime.now(UTC))
            .execution_options(synchronize_session=False)
        )
        publish_notification_read(self._session, recipient_member_id, theme=None)
        return int(result.rowcount or 0)

    def mark_message_notifications_read(
        self,
        recipient_member_id: str,
        *,
        message_ids: list[str] | None = None,
        room_id: str | None = None,
        up_to: datetime | None = None,
        sources: set[str] | None = None,
    ) -> list[str]:
        """메시지함 읽음 → 그 메시지의 알림 줄 읽음(D-37 · SPEC-011 §4.5-3). 연동 끊김 줄은 빠진다. 읽은 줄 id 를 낸다.

        - `message_ids` — 그 메일(들)의 `message.mail` 줄
        - `room_id` + `up_to` — 그 방의 `message.slack` · `message.kakao` 줄 가운데 메시지 시각 ≤ `up_to`(합친 줄은 마지막 시각)
        - `sources` — 메시지함 「모두 읽음」 — 그 출처(`mail` · `slack` · `kakao`)의 메시지 알림 줄 전부
        """
        statement = select(NotificationRecord).where(
            NotificationRecord.recipient_member_id == recipient_member_id,
            NotificationRecord.read_at.is_(None),
            NotificationRecord.kind.in_(MESSAGE_KINDS),
        )
        if message_ids is not None:
            statement = statement.where(NotificationRecord.kind == "message.mail", NotificationRecord.resource_id.in_(message_ids))
        elif room_id is not None:
            statement = statement.where(NotificationRecord.resource_type == "room", NotificationRecord.resource_id == room_id)
        elif sources is not None:
            statement = statement.where(NotificationRecord.kind.in_({f"message.{source}" for source in sources}))
        rows = list(self._session.scalars(statement.with_for_update()))
        now = datetime.now(UTC)
        read: list[str] = []
        for row in rows:
            if room_id is not None and up_to is not None:
                at = _parse_time((row.data or {}).get("last_sent_at") or (row.data or {}).get("sent_at"))
                if at is not None and at > up_to:
                    continue
            row.read_at = now
            read.append(str(row.id))
        if read:
            publish_notification_read(self._session, recipient_member_id, notification_ids=read)
        return read


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


__all__ = [
    "SqlAlchemyNotificationRepository",
    "default_settings",
    "next_notification_seq",
    "notification_generator",
    "publish_notification_read",
    "publish_notification_upserted",
]
