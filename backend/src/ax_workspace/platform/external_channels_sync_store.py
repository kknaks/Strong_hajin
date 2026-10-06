"""연동 워커의 저장소 — BE-1 의 표에 수집분을 쓴다 (WORK-011 Phase BE-2).

한 호출 = 한 트랜잭션. 메시지 저장·카운터·사건 알림(NOTIFY)이 같은 트랜잭션에 있어 「알림은 왔는데 행이 없다」가
없다. 중복 방지 키 `(integration_id, container_key, external_key)` 는 먼저 묻고, 그 사이 같은 행이 들어오는
경합(이벤트 재전달·메우기와 실시간이 겹침)은 savepoint 의 unique 위반으로 버린다.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ax_workspace.modules.external_channels.events import USER_EVENTS_CHANNEL, UserEvent, UserEventType
from ax_workspace.modules.external_channels.sync import IntegrationState, RoomState, SaveMode
from ax_workspace.modules.external_channels.sync_messages import NormalizedMessage
from ax_workspace.platform import user_events
from ax_workspace.platform.persistence import (
    ExternalAttachmentRecord,
    ExternalIntegrationRecord,
    ExternalMessageRecord,
    ExternalRoomRecord,
)

logger = logging.getLogger(__name__)

#: 한 번 저장에서 낱낱이 알리는 새 메시지 상한. 넘으면(메우기 등) 연동 변경 한 건으로 묶는다 — 8000바이트 NOTIFY 를
#: 수백 번 내지 않는다. 화면은 어차피 API 로 다시 읽는다.
ARRIVAL_EVENTS_PER_SAVE = 20

_INTEGRATION_FIELDS = frozenset({
    "status", "sync_cursor", "watch_expires_at", "backfill_cursor", "backfill_done_at", "access_token_encrypted",
    "token_expires_at", "last_error", "account_meta",
})
_ROOM_FIELDS = frozenset({"status", "backfill_cursor", "backfill_done_at", "room_type", "name", "member_count", "room_meta"})


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _integration_state(row: ExternalIntegrationRecord) -> IntegrationState:
    return IntegrationState(
        id=str(row.id), member_id=row.member_id, kind=row.kind, status=row.status, account_key=row.account_key,
        access_token_encrypted=row.access_token_encrypted, refresh_token_encrypted=row.refresh_token_encrypted,
        account_meta=dict(row.account_meta or {}),
        token_expires_at=_aware(row.token_expires_at), sync_cursor=row.sync_cursor, backfill_cursor=row.backfill_cursor,
        backfill_done_at=_aware(row.backfill_done_at), watch_expires_at=_aware(row.watch_expires_at),
        last_synced_at=_aware(row.last_synced_at),
    )


def _room_state(room: ExternalRoomRecord, integration: ExternalIntegrationRecord) -> RoomState:
    return RoomState(
        id=str(room.id), integration_id=str(integration.id), member_id=integration.member_id, external_id=room.external_id,
        room_type=room.room_type, name=room.name, last_message_key=room.last_message_key,
        backfill_cursor=room.backfill_cursor, backfill_count=room.backfill_count or 0,
        backfill_done_at=_aware(room.backfill_done_at), access_token_encrypted=integration.access_token_encrypted,
        verified=bool((room.room_meta or {}).get("verified_at")), access_lost=(room.room_meta or {}).get("access_lost"),
    )


def _active(model=ExternalIntegrationRecord):
    """수집 대상 연동 — 지우지 않았고 끊기지 않은 것."""
    return and_(model.removed_at.is_(None), model.status.in_(("connected", "backfilling")))


def _key_order(key: str | None) -> tuple[int, float | int | str]:
    """슬랙 ts(`1712345678.000100`)·카톡 logId 를 크기로 비교한다 — 글자 비교면 자릿수가 다를 때 틀린다."""
    if key is None:
        return (0, 0)
    try:
        return (1, float(key))
    except ValueError:
        return (2, key)


class SqlAlchemyExternalChannelsSyncStore:
    def __init__(self, session_factory: sessionmaker[Session], clock=lambda: datetime.now(UTC)) -> None:
        self._sessions = session_factory
        self._clock = clock

    # ── 읽기 ──
    def active_integrations(self, kind: str) -> list[IntegrationState]:
        with self._sessions() as session:
            rows = session.scalars(
                select(ExternalIntegrationRecord)
                .where(ExternalIntegrationRecord.kind == kind, _active())
                .order_by(ExternalIntegrationRecord.created_at)
            )
            return [_integration_state(row) for row in rows]

    def integration(self, integration_id: str) -> IntegrationState | None:
        with self._sessions() as session:
            row = session.get(ExternalIntegrationRecord, UUID(integration_id))
            return _integration_state(row) if row is not None else None

    def mail_integrations_for_address(self, address: str) -> list[IntegrationState]:
        with self._sessions() as session:
            rows = session.scalars(
                select(ExternalIntegrationRecord).where(
                    ExternalIntegrationRecord.kind == "mail", ExternalIntegrationRecord.account_key == address, _active()
                )
            )
            return [_integration_state(row) for row in rows]

    def slack_rooms(self) -> list[RoomState]:
        return self._rooms(ExternalIntegrationRecord.kind == "slack")

    def slack_fanout_targets(self, team_id: str, channel: str) -> list[RoomState]:
        """이벤트 1건의 `(team, channel)` 을 고른 연동의 방 — 사람마다 한 벌(W-8 · D-25).

        **그 회원 토큰으로 접근이 확인된 방만**이다(BE-2·3 검수 F-1). `(team, channel)` 만으로 고르면 남의 DM·비공개
        채널 id 를 고른 사람에게 그 대화가 복제된다. 확인은 방 추가(BE-1 `add_rooms`)와 워커의 실행마다 재확인이 찍는다.
        """
        rooms = self._rooms(
            ExternalIntegrationRecord.kind == "slack",
            ExternalIntegrationRecord.account_key == team_id,
            ExternalRoomRecord.external_id == channel,
        )
        return [room for room in rooms if room.verified and not room.access_lost]

    def _rooms(self, *conditions) -> list[RoomState]:
        with self._sessions() as session:
            rows = session.execute(
                select(ExternalRoomRecord, ExternalIntegrationRecord)
                .join(ExternalIntegrationRecord, ExternalRoomRecord.integration_id == ExternalIntegrationRecord.id)
                .where(ExternalRoomRecord.removed_at.is_(None), _active(), *conditions)
                .order_by(ExternalRoomRecord.created_at, ExternalRoomRecord.external_id)
            ).all()
            return [_room_state(room, integration) for room, integration in rows]

    # ── 쓰기 ──
    def save_messages(
        self, integration_id: str, source_kind: str, room_id: str | None, messages: list[NormalizedMessage], *, mode: SaveMode
    ) -> int:
        if not messages:
            return 0
        now = self._clock()
        with self._sessions() as session:
            integration = session.get(ExternalIntegrationRecord, UUID(integration_id), with_for_update=True)
            if integration is None or integration.removed_at is not None:
                return 0
            room = session.get(ExternalRoomRecord, UUID(room_id), with_for_update=True) if room_id else None
            existing = set(
                session.scalars(
                    select(ExternalMessageRecord.external_key).where(
                        ExternalMessageRecord.integration_id == integration.id,
                        ExternalMessageRecord.container_key.in_({message.container_key for message in messages}),
                        ExternalMessageRecord.external_key.in_({message.external_key for message in messages}),
                    )
                )
            )
            inserted: list[ExternalMessageRecord] = []
            newest = room.last_message_key if room is not None else None
            for message in messages:
                if message.external_key in existing:
                    continue
                existing.add(message.external_key)
                record = ExternalMessageRecord(
                    integration_id=integration.id, room_id=room.id if room is not None else None, source_kind=source_kind,
                    container_key=message.container_key, external_key=message.external_key, thread_key=message.thread_key,
                    sent_at=message.sent_at, subject=message.subject, author=message.author, preview=message.preview,
                    raw=message.raw, created_at=now,
                )
                try:
                    with session.begin_nested():
                        session.add(record)
                        session.flush()
                        for attachment in message.attachments:
                            session.add(ExternalAttachmentRecord(
                                message_id=record.id, aid=attachment.aid, seq=attachment.seq, kind=attachment.kind,
                                state=attachment.state, name=attachment.name, size=attachment.size, mime=attachment.mime,
                                external_ref=attachment.external_ref, created_at=now,
                            ))
                        session.flush()
                except IntegrityError:
                    continue  # 같은 키가 그 사이 들어왔다 — 버린다
                inserted.append(record)
                if room is not None and _key_order(message.external_key) > _key_order(newest):
                    newest = message.external_key
            count = len(inserted)
            if count:
                integration.synced_count = (integration.synced_count or 0) + count
                integration.last_synced_at = now
                if room is not None:
                    room.synced_count = (room.synced_count or 0) + count
                    room.last_synced_at = now
                    room.last_message_key = newest
                    if mode == "backfill":
                        room.backfill_count = (room.backfill_count or 0) + count
                elif mode == "backfill":
                    integration.backfill_count = (integration.backfill_count or 0) + count
                self._announce(session, integration, room, inserted, mode)
            session.commit()
            return count

    def _announce(self, session: Session, integration, room, inserted: list[ExternalMessageRecord], mode: SaveMode) -> None:
        member = integration.member_id
        room_id = str(room.id) if room is not None else None
        if mode == "live" and len(inserted) <= ARRIVAL_EVENTS_PER_SAVE:
            for record in sorted(inserted, key=lambda row: row.sent_at):
                event = UserEvent(
                    UserEventType.MESSAGE_ARRIVED, member, integration_id=str(integration.id), room_id=room_id,
                    message_id=str(record.id), source_kind=integration.kind,
                )
                user_events.publish(session, USER_EVENTS_CHANNEL, event.to_payload())
            return
        # 백필 한 쪽·큰 메우기 = 진행 한 건(「채우는 중 · N건」을 다시 읽게).
        event = UserEvent(
            UserEventType.INTEGRATION_CHANGED, member, integration_id=str(integration.id), room_id=room_id,
            source_kind=integration.kind, data={"saved": len(inserted), "mode": mode},
        )
        user_events.publish(session, USER_EVENTS_CHANNEL, event.to_payload())

    def replace_raw(self, integration_id: str, container_key: str, external_key: str, raw: dict[str, Any]) -> bool:
        """수정·삭제 — 원문을 새 판으로 간다. 행은 지우지 않는다. 없던 메시지면 False."""
        with self._sessions() as session:
            record = session.scalar(
                select(ExternalMessageRecord).where(
                    ExternalMessageRecord.integration_id == UUID(integration_id),
                    ExternalMessageRecord.container_key == container_key,
                    ExternalMessageRecord.external_key == external_key,
                ).with_for_update()
            )
            if record is None:
                return False
            record.raw = raw
            if not raw.get("ax_deleted"):
                record.preview = (raw.get("text") or "")[:2000] or None
            integration = session.get(ExternalIntegrationRecord, record.integration_id)
            event = UserEvent(
                UserEventType.MESSAGE_ARRIVED, integration.member_id, integration_id=integration_id,
                room_id=str(record.room_id) if record.room_id else None, message_id=str(record.id),
                source_kind=integration.kind, data={"edited": True},
            )
            user_events.publish(session, USER_EVENTS_CHANNEL, event.to_payload())
            session.commit()
            return True

    def update_integration(self, integration_id: str, **fields: Any) -> None:
        unknown = set(fields) - _INTEGRATION_FIELDS
        if unknown:
            raise ValueError(f"not a sync field: {sorted(unknown)}")
        with self._sessions() as session:
            row = session.get(ExternalIntegrationRecord, UUID(integration_id), with_for_update=True)
            if row is None:
                return
            status_before = row.status
            for key, value in fields.items():
                if key == "account_meta":
                    value = {**(row.account_meta or {}), **value}  # 연결이 남긴 team·user 정보를 지우지 않는다
                setattr(row, key, value)
            row.updated_at = self._clock()
            if row.status != status_before:
                self._changed(session, row)
            session.commit()

    def update_room(self, room_id: str, **fields: Any) -> None:
        unknown = set(fields) - _ROOM_FIELDS
        if unknown:
            raise ValueError(f"not a sync field: {sorted(unknown)}")
        with self._sessions() as session:
            room = session.get(ExternalRoomRecord, UUID(room_id), with_for_update=True)
            if room is None:
                return
            status_before = room.status
            for key, value in fields.items():
                if key == "room_meta":
                    value = {**(room.room_meta or {}), **value}  # 확인 표지(verified_at)를 지우지 않는다
                setattr(room, key, value)
            room.updated_at = self._clock()
            if room.status != status_before:
                self._changed(session, session.get(ExternalIntegrationRecord, room.integration_id), room_id=room_id)
            session.commit()

    def set_room_access(self, room_id: str, *, ok: bool, reason: str | None = None) -> None:
        """접근 확인 결과(F-1). 잃으면 `paused`+사유(팬아웃·수집에서 빠짐), 되찾으면 표지를 새로 찍고 상태를 되돌린다."""
        with self._sessions() as session:
            room = session.get(ExternalRoomRecord, UUID(room_id), with_for_update=True)
            if room is None:
                return
            now = self._clock()
            meta = dict(room.room_meta or {})
            status_before = room.status
            if ok:
                meta.pop("access_lost", None)
                meta["verified_at"] = now.isoformat()
                if room.status == "paused":
                    room.status = "live" if room.backfill_done_at else "backfilling"
            else:
                meta.pop("verified_at", None)
                meta["access_lost"] = (reason or "access_denied")[:60]
                room.status = "paused"
            room.room_meta = meta
            room.updated_at = now
            if room.status != status_before:
                self._changed(session, session.get(ExternalIntegrationRecord, room.integration_id), room_id=room_id)
            session.commit()

    def room_users(self, room_id: str) -> dict[str, dict[str, Any]]:
        """방에 모아 둔 이름표(`room_meta.users`) — 메시지 응답의 `users` 맵이 된다(BE 수정 판 3)."""
        with self._sessions() as session:
            room = session.get(ExternalRoomRecord, UUID(room_id))
            return dict(((room.room_meta or {}).get("users") or {}) if room is not None else {})

    def thread_roots(self, room_id: str, *, limit: int) -> list[str]:
        """최근 스레드의 부모 ts — 메우기 때 그 답글을 다시 훑는다(W-6). 부모는 `thread_key == external_key` 인 행이다."""
        with self._sessions() as session:
            return list(session.scalars(
                select(ExternalMessageRecord.external_key)
                .where(
                    ExternalMessageRecord.room_id == UUID(room_id),
                    ExternalMessageRecord.thread_key == ExternalMessageRecord.external_key,
                )
                .order_by(ExternalMessageRecord.sent_at.desc())
                .limit(limit)
            ))

    def purge_spent_oauth_states(self, *, older_than: timedelta) -> int:
        """만료·소비된 지 오래된 OAuth `state` 를 지운다(BE-1 검수 W-8). 해시뿐인 일회용 표라 회사 데이터의 「계속 보관」
        원칙(D-49 대체)에 들지 않는다 — 지워도 되살릴 것이 없다."""
        from ax_workspace.platform.persistence import ExternalOAuthStateRecord

        cutoff = self._clock() - older_than
        with self._sessions() as session:
            result = session.execute(delete(ExternalOAuthStateRecord).where(ExternalOAuthStateRecord.expires_at < cutoff))
            session.commit()
            return int(result.rowcount or 0)

    def mark_disconnected(self, integration_id: str, reason: str) -> None:
        with self._sessions() as session:
            row = session.get(ExternalIntegrationRecord, UUID(integration_id), with_for_update=True)
            if row is None or row.removed_at is not None or row.status == "disconnected":
                return
            now = self._clock()
            logger.warning("external integration %s (%s) marked disconnected: %s", row.id, row.kind, reason)
            row.status = "disconnected"
            row.disconnected_reason = reason[:40]
            row.disconnected_at = now
            row.updated_at = now
            self._changed(session, row)
            session.commit()

    @staticmethod
    def _changed(session: Session, row: ExternalIntegrationRecord, *, room_id: str | None = None) -> None:
        event = UserEvent(
            UserEventType.INTEGRATION_CHANGED, row.member_id, integration_id=str(row.id), room_id=room_id,
            source_kind=row.kind, data={"status": row.status},
        )
        user_events.publish(session, USER_EVENTS_CHANNEL, event.to_payload())

    # ── 개발 전용 이음새 (4차 검수 ★3) ──
    def connect_slack_for_development(
        self, *, member_id: str, team_id: str, team_name: str, user_id: str | None, access_token_encrypted: str, scopes: str,
        extra_meta: dict[str, Any] | None = None,
    ) -> str:
        """`~/.slack_test_token` 을 「연결된 슬랙 연동」으로. OAuth 콜백(https·운영)을 거치지 않는 **로컬 전용** 길이다 —
        운영 프로파일 거절은 부르는 쪽(`bootstrap/external_worker.connect_dev_slack`)이 먼저 한다."""
        from ax_workspace.platform.persistence import MemberRecord

        now = self._clock()
        with self._sessions() as session:
            if session.get(MemberRecord, member_id) is None:
                raise LookupError(f"member {member_id} does not exist")
            row = session.scalar(
                select(ExternalIntegrationRecord).where(
                    ExternalIntegrationRecord.member_id == member_id,
                    ExternalIntegrationRecord.kind == "slack",
                    ExternalIntegrationRecord.account_key == team_id,
                ).with_for_update()
            )
            meta = {"team_id": team_id, "team_name": team_name, "user_id": user_id, "source": "development_token", **(extra_meta or {})}
            if row is None:
                row = ExternalIntegrationRecord(
                    member_id=member_id, kind="slack", status="connected", account_key=team_id, display_name=team_name,
                    account_meta=meta, access_token_encrypted=access_token_encrypted, scopes=scopes,
                    backfill_done_at=now, created_at=now, updated_at=now,
                )
                session.add(row)
            else:
                row.status, row.removed_at, row.disconnected_reason, row.disconnected_at = "connected", None, None, None
                row.access_token_encrypted, row.scopes, row.account_meta = access_token_encrypted, scopes, meta
                row.display_name, row.updated_at = team_name, now
            session.flush()
            self._changed(session, row)
            session.commit()
            return str(row.id)
