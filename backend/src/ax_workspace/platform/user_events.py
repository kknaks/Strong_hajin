"""Postgres `LISTEN/NOTIFY` 의 보내는 쪽 — 채널·페이로드 계약은 `modules/external_channels/events.py` 가 정본이다.

NOTIFY 는 트랜잭션에 묶인다: 같은 세션이 커밋할 때 전달되고, 롤백하면 사라진다. 그래서 저장과 같은 세션에서
낸다. PostgreSQL 이 아닌 DB(sqlite 시험)에서는 `publish` 가 아무것도 하지 않는다 — 듣는 쪽이 없다.

`publish_user_event` 는 사용자 사건 채널(`ax_user_events`)에 사건 하나를 내는 **어느 프로세스든 같은 길**이다
(SPEC-011 §4.1-4 · D-13) — API 든 워커(meeting_worker 등)든 DB 세션이 있고 그 사건을 쓴 트랜잭션 안이면 된다.
PostgreSQL 이 아니면 NOTIFY 대신 **그 세션이 커밋된 뒤** 세션 `info` 의 `LOCAL_DISPATCH_KEY` 함수(같은 프로세스의
`UserEventHub.dispatch` — 조립이 세션 공장에 끼운다)로 흘린다. 롤백하면 버린다 — NOTIFY 와 같은 뜻이다.
"""
from __future__ import annotations

from sqlalchemy import event, text
from sqlalchemy.orm import Session

from ax_workspace.modules.external_channels.events import USER_EVENTS_CHANNEL, UserEvent

#: 세션 `info` 에서 같은 프로세스 배달 함수(`Callable[[str], None]`)를 찾는 열쇠 — PostgreSQL 이 아닐 때만 쓴다.
LOCAL_DISPATCH_KEY = "ax_user_event_dispatch"
_PENDING_KEY = "ax_user_events_pending"


def publish(session: Session, channel: str, payload: str) -> None:
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return
    session.execute(text("SELECT pg_notify(:channel, :payload)"), {"channel": channel, "payload": payload})


def publish_user_event(session: Session, user_event: UserEvent) -> None:
    """사건 하나를 **이 트랜잭션에** 싣는다 — 커밋 때 나가고 롤백이면 사라진다."""
    payload = user_event.to_payload()
    if session.get_bind().dialect.name == "postgresql":
        publish(session, USER_EVENTS_CHANNEL, payload)
        return
    if session.info.get(LOCAL_DISPATCH_KEY) is None:
        return
    session.info.setdefault(_PENDING_KEY, []).append(payload)


@event.listens_for(Session, "after_commit")
def _deliver_local_events(session: Session) -> None:
    pending = session.info.pop(_PENDING_KEY, None)
    dispatch = session.info.get(LOCAL_DISPATCH_KEY)
    if not pending or dispatch is None:
        return
    for payload in pending:
        dispatch(payload)


@event.listens_for(Session, "after_rollback")
def _drop_local_events(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)
