from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ax_workspace.platform.persistence import NotificationRecord


class SqlAlchemyNotificationRepository:
    """Stores delivery/read state while the owning resource remains the authorization source."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def emit(
        self,
        *,
        recipient_member_id: str,
        source_kind: str,
        source_id: str,
        kind: str,
        resource_type: str,
        resource_id: str,
        resource_version: int | None,
        resource_title: str,
        actor_member_id: str,
        safe_summary: str,
        created_at: datetime | None = None,
    ) -> NotificationRecord:
        existing = self._session.scalar(
            select(NotificationRecord).where(
                NotificationRecord.recipient_member_id == recipient_member_id,
                NotificationRecord.source_kind == source_kind,
                NotificationRecord.source_id == source_id,
            )
        )
        if existing is not None:
            return existing
        record = NotificationRecord(
            recipient_member_id=recipient_member_id,
            source_kind=source_kind,
            source_id=source_id,
            kind=kind,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_version=resource_version,
            resource_title=resource_title,
            actor_member_id=actor_member_id,
            safe_summary=safe_summary[:300],
            created_at=created_at or datetime.now(UTC),
        )
        self._session.add(record)
        self._session.flush()
        return record

    def list_for(self, recipient_member_id: str) -> list[NotificationRecord]:
        return list(
            self._session.scalars(
                select(NotificationRecord)
                .where(NotificationRecord.recipient_member_id == recipient_member_id)
                .order_by(NotificationRecord.created_at.desc(), NotificationRecord.id.desc())
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

    @staticmethod
    def mark_read(record: NotificationRecord) -> None:
        if record.read_at is None:
            record.read_at = datetime.now(UTC)


def notification_view(record: NotificationRecord, *, title: str, version: int | None) -> dict[str, object]:
    return {
        "notification_id": str(record.id),
        "kind": record.kind,
        "summary": record.safe_summary,
        "actor_id": record.actor_member_id,
        "resource": {
            "type": record.resource_type,
            "id": record.resource_id,
            "version": version,
            "title": title,
        },
        "created_at": record.created_at.isoformat(),
        "read_at": record.read_at.isoformat() if record.read_at else None,
    }
