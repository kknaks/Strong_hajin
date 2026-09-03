"""PostgreSQL-backed login sessions for the HTTP entrypoint."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ax_workspace.platform.persistence import AuthSessionRecord

DEFAULT_SESSION_TTL = timedelta(hours=12)


class SqlAlchemyAuthSessionStore:
    def __init__(self, session_factory: sessionmaker[Session], ttl: timedelta = DEFAULT_SESSION_TTL) -> None:
        self._session_factory = session_factory
        self._ttl = ttl

    def create(self, member_id: str, provider: str) -> UUID:
        now = datetime.now(UTC)
        record = AuthSessionRecord(member_id=member_id, provider=provider, created_at=now, expires_at=now + self._ttl)
        with self._session_factory() as session:
            session.add(record)
            session.commit()
            return record.id

    def member_for(self, session_id: UUID) -> str | None:
        now = datetime.now(UTC)
        with self._session_factory() as session:
            record = session.scalar(select(AuthSessionRecord).where(AuthSessionRecord.id == session_id))
            if record is None or record.revoked_at is not None:
                return None
            expires_at = record.expires_at if record.expires_at.tzinfo else record.expires_at.replace(tzinfo=UTC)
            if expires_at <= now:
                return None
            return record.member_id

    def revoke(self, session_id: UUID) -> None:
        with self._session_factory() as session:
            record = session.scalar(select(AuthSessionRecord).where(AuthSessionRecord.id == session_id))
            if record is not None and record.revoked_at is None:
                record.revoked_at = datetime.now(UTC)
                session.commit()
