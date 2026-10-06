"""Postgres `LISTEN/NOTIFY` 의 보내는 쪽 — 채널·페이로드 계약은 `modules/external_channels/events.py` 가 정본이다.

NOTIFY 는 트랜잭션에 묶인다: 같은 세션이 커밋할 때 전달되고, 롤백하면 사라진다. 그래서 저장과 같은 세션에서
낸다. PostgreSQL 이 아닌 DB(sqlite 시험)에서는 아무것도 하지 않는다 — 듣는 쪽이 없다.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


def publish(session: Session, channel: str, payload: str) -> None:
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return
    session.execute(text("SELECT pg_notify(:channel, :payload)"), {"channel": channel, "payload": payload})
