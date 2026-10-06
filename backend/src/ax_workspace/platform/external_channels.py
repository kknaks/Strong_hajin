"""외부 채널 연동의 저장소 — 연동·고른 방·OAuth state·기기 토큰 (WORK-011 Phase BE-1).

조회는 언제나 **회원 범위**로 묻는다(남의 것은 `None` → 404). 소프트 딜리트된 연동·방은 «없는 것»으로 답하고,
되살림이 필요한 자리(`integration_by_account`·`room_by_external`)만 지운 것까지 본다.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ax_workspace.platform import user_events
from ax_workspace.platform.persistence import (
    ExternalDeviceTokenRecord,
    ExternalIntegrationRecord,
    ExternalOAuthStateRecord,
    ExternalRoomRecord,
)


class SqlAlchemyExternalChannelRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # ── 연동 ──
    def integrations_for(self, member_id: str) -> list[ExternalIntegrationRecord]:
        return list(
            self._session.scalars(
                select(ExternalIntegrationRecord)
                .where(ExternalIntegrationRecord.member_id == member_id, ExternalIntegrationRecord.removed_at.is_(None))
                .order_by(ExternalIntegrationRecord.created_at, ExternalIntegrationRecord.id)
            )
        )

    def integration_for(self, integration_id: UUID, member_id: str, *, lock: bool = False) -> ExternalIntegrationRecord | None:
        statement = select(ExternalIntegrationRecord).where(
            ExternalIntegrationRecord.id == integration_id,
            ExternalIntegrationRecord.member_id == member_id,
            ExternalIntegrationRecord.removed_at.is_(None),
        )
        return self._session.scalar(statement.with_for_update() if lock else statement)

    def integration_by_account(
        self, member_id: str, kind: str, account_key: str, *, lock: bool = False
    ) -> ExternalIntegrationRecord | None:
        statement = select(ExternalIntegrationRecord).where(
            ExternalIntegrationRecord.member_id == member_id,
            ExternalIntegrationRecord.kind == kind,
            ExternalIntegrationRecord.account_key == account_key,
        )
        return self._session.scalar(statement.with_for_update() if lock else statement)

    def active_integrations_of_kind(self, member_id: str, kind: str) -> list[ExternalIntegrationRecord]:
        return list(
            self._session.scalars(
                select(ExternalIntegrationRecord)
                .where(
                    ExternalIntegrationRecord.member_id == member_id,
                    ExternalIntegrationRecord.kind == kind,
                    ExternalIntegrationRecord.removed_at.is_(None),
                )
                .order_by(ExternalIntegrationRecord.created_at)
            )
        )

    def add_integration(self, **fields: Any) -> ExternalIntegrationRecord:
        record = ExternalIntegrationRecord(**fields)
        self._session.add(record)
        self._session.flush()
        return record

    # ── 고른 방 ──
    def rooms_of(self, integration_id: UUID) -> list[ExternalRoomRecord]:
        return list(
            self._session.scalars(
                select(ExternalRoomRecord)
                .where(ExternalRoomRecord.integration_id == integration_id, ExternalRoomRecord.removed_at.is_(None))
                .order_by(ExternalRoomRecord.created_at, ExternalRoomRecord.external_id)
            )
        )

    def room_of(self, integration_id: UUID, room_id: UUID, *, lock: bool = False) -> ExternalRoomRecord | None:
        statement = select(ExternalRoomRecord).where(
            ExternalRoomRecord.id == room_id,
            ExternalRoomRecord.integration_id == integration_id,
            ExternalRoomRecord.removed_at.is_(None),
        )
        return self._session.scalar(statement.with_for_update() if lock else statement)

    def room_by_external(self, integration_id: UUID, external_id: str, *, lock: bool = False) -> ExternalRoomRecord | None:
        statement = select(ExternalRoomRecord).where(
            ExternalRoomRecord.integration_id == integration_id, ExternalRoomRecord.external_id == external_id
        )
        return self._session.scalar(statement.with_for_update() if lock else statement)

    def add_room(self, **fields: Any) -> ExternalRoomRecord:
        record = ExternalRoomRecord(**fields)
        self._session.add(record)
        self._session.flush()
        return record

    # ── OAuth state ──
    def add_oauth_state(self, **fields: Any) -> ExternalOAuthStateRecord:
        record = ExternalOAuthStateRecord(**fields)
        self._session.add(record)
        self._session.flush()
        return record

    def oauth_state(self, state_hash: str, *, lock: bool = False) -> ExternalOAuthStateRecord | None:
        statement = select(ExternalOAuthStateRecord).where(ExternalOAuthStateRecord.state_hash == state_hash)
        return self._session.scalar(statement.with_for_update() if lock else statement)

    # ── 기기 토큰 ──
    def add_device_token(self, **fields: Any) -> ExternalDeviceTokenRecord:
        record = ExternalDeviceTokenRecord(**fields)
        self._session.add(record)
        self._session.flush()
        return record

    def device_tokens_for(self, member_id: str) -> list[ExternalDeviceTokenRecord]:
        return list(
            self._session.scalars(
                select(ExternalDeviceTokenRecord)
                .where(ExternalDeviceTokenRecord.member_id == member_id, ExternalDeviceTokenRecord.revoked_at.is_(None))
                .order_by(ExternalDeviceTokenRecord.created_at.desc())
            )
        )

    def device_token_for(self, token_id: UUID, member_id: str, *, lock: bool = False) -> ExternalDeviceTokenRecord | None:
        statement = select(ExternalDeviceTokenRecord).where(
            ExternalDeviceTokenRecord.id == token_id,
            ExternalDeviceTokenRecord.member_id == member_id,
            ExternalDeviceTokenRecord.revoked_at.is_(None),
        )
        return self._session.scalar(statement.with_for_update() if lock else statement)

    def device_token_by_hash(self, token_hash: str) -> ExternalDeviceTokenRecord | None:
        return self._session.scalar(select(ExternalDeviceTokenRecord).where(ExternalDeviceTokenRecord.token_hash == token_hash))

    def revoke_device_tokens(self, member_id: str, at: datetime) -> int:
        result = self._session.execute(
            update(ExternalDeviceTokenRecord)
            .where(ExternalDeviceTokenRecord.member_id == member_id, ExternalDeviceTokenRecord.revoked_at.is_(None))
            .values(revoked_at=at)
            .execution_options(synchronize_session="fetch")
        )
        return int(result.rowcount or 0)

    # ── 사건 ──
    def notify(self, channel: str, payload: str) -> None:
        user_events.publish(self._session, channel, payload)
