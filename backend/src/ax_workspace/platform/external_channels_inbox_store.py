"""메시지함 조회·읽음·보낸 답장 · 카톡 수신 쓰기 · 프로필 이미지 저장소 — **BE-3 소유** (코디 조정 1).

BE-1 의 `platform/external_channels.py` 는 얼렸다 — 여기서 고치지 않고 이 파일에 새로 둔다. 표는 BE-1 이 만든
`External*Record`·`ProfileImageRecord` 그대로다(스키마 변경 없음).

조회는 언제나 **회원 범위**다: 연동의 `member_id` 가 맞고 연동·방이 소프트 딜리트되지 않았을 때만 답한다(남의 것은
`None` → 404). 방 «본문 줄»(top-level)은 스레드 답글을 뺀 것이다 — `thread_key` 가 없거나 자기 자신(슬랙 스레드 부모).
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ax_workspace.modules.external_channels.message_context import message_reference
from ax_workspace.platform import user_events
from ax_workspace.platform.persistence import (
    AuthSessionRecord,
    ExternalAttachmentRecord,
    ExternalIntegrationRecord,
    ExternalMessageRecord,
    ExternalReadStateRecord,
    ExternalRoomRecord,
    ExternalSentReplyRecord,
    MemberCredentialRecord,
    ProfileImageRecord,
    TaskRecord,
)

M = ExternalMessageRecord


def _top_level():
    return or_(M.thread_key.is_(None), M.thread_key == M.external_key)


def _before(cursor: tuple[datetime, str] | None):
    if cursor is None:
        return None
    at, identity = cursor
    try:
        marker = UUID(identity)
    except ValueError:
        return None
    return or_(M.sent_at < at, and_(M.sent_at == at, M.id < marker))


class SqlAlchemyInboxStore:
    def __init__(self, session: Session) -> None:
        self._session = session
        #: PostgreSQL 이 아닐 때(시험) 커밋 뒤 같은 프로세스 안에서 대신 흘려 보낼 사건.
        self.local_events: list[str] = []

    @property
    def session(self) -> Session:
        """같은 트랜잭션에 작업 큐(`durable_jobs`)를 함께 쓰는 조립층이 쓴다."""
        return self._session

    # ── 연동·방 ──
    def active_integrations(self, member_id: str) -> list[ExternalIntegrationRecord]:
        return list(
            self._session.scalars(
                select(ExternalIntegrationRecord)
                .where(ExternalIntegrationRecord.member_id == member_id, ExternalIntegrationRecord.removed_at.is_(None))
                .order_by(ExternalIntegrationRecord.created_at)
            )
        )

    def integration_by_id(self, integration_id: UUID) -> ExternalIntegrationRecord | None:
        return self._session.get(ExternalIntegrationRecord, integration_id)

    def room_by_id(self, room_id: UUID) -> ExternalRoomRecord | None:
        return self._session.get(ExternalRoomRecord, room_id)

    def message_by_id(self, message_id: UUID) -> ExternalMessageRecord | None:
        return self._session.get(ExternalMessageRecord, message_id)

    def mail_for(self, member_id: str, message_id: UUID) -> tuple[Any, Any] | None:
        row = self._session.execute(
            select(M, ExternalIntegrationRecord)
            .join(ExternalIntegrationRecord, ExternalIntegrationRecord.id == M.integration_id)
            .where(
                M.id == message_id,
                M.room_id.is_(None),
                M.source_kind == "mail",
                ExternalIntegrationRecord.member_id == member_id,
                ExternalIntegrationRecord.removed_at.is_(None),
            )
        ).first()
        return (row[0], row[1]) if row else None

    def room_for(self, member_id: str, room_id: UUID) -> tuple[Any, Any] | None:
        row = self._session.execute(
            select(ExternalRoomRecord, ExternalIntegrationRecord)
            .join(ExternalIntegrationRecord, ExternalIntegrationRecord.id == ExternalRoomRecord.integration_id)
            .where(
                ExternalRoomRecord.id == room_id,
                ExternalRoomRecord.removed_at.is_(None),
                ExternalIntegrationRecord.member_id == member_id,
                ExternalIntegrationRecord.removed_at.is_(None),
            )
        ).first()
        return (row[0], row[1]) if row else None

    # ── 목록 ──
    def mail_page(
        self, integration_ids: Sequence[UUID], member_id: str, *, before: tuple[datetime, str] | None, unread_only: bool, limit: int
    ) -> list[ExternalMessageRecord]:
        statement = select(M).where(M.integration_id.in_(list(integration_ids)), M.room_id.is_(None))
        condition = _before(before)
        if condition is not None:
            statement = statement.where(condition)
        if unread_only:
            # 내가 보낸 메일(`from_me = true`)은 안 읽음이 아니다(SPEC-008 v0.7.0 §4.4 · 검수 F-2 ③).
            statement = statement.where(~self._mail_read_exists(member_id), _not_mine())
        return list(self._session.scalars(statement.order_by(M.sent_at.desc(), M.id.desc()).limit(limit)))

    @staticmethod
    def _mail_read_exists(member_id: str):
        return (
            select(ExternalReadStateRecord.id)
            .where(ExternalReadStateRecord.member_id == member_id, ExternalReadStateRecord.message_id == M.id)
            .exists()
        )

    def mail_unread_count(self, integration_ids: Sequence[UUID], member_id: str) -> int:
        return int(
            self._session.scalar(
                select(func.count(M.id)).where(
                    M.integration_id.in_(list(integration_ids)), M.room_id.is_(None), ~self._mail_read_exists(member_id),
                    _not_mine(),
                )
            )
            or 0
        )

    def read_message_ids(self, member_id: str, message_ids: Sequence[UUID]) -> set[UUID]:
        if not message_ids:
            return set()
        return set(
            self._session.scalars(
                select(ExternalReadStateRecord.message_id).where(
                    ExternalReadStateRecord.member_id == member_id,
                    ExternalReadStateRecord.message_id.in_(list(message_ids)),
                )
            )
        )

    def attachment_counts(self, message_ids: Sequence[UUID]) -> dict[UUID, int]:
        if not message_ids:
            return {}
        rows = self._session.execute(
            select(ExternalAttachmentRecord.message_id, func.count(ExternalAttachmentRecord.id))
            .where(ExternalAttachmentRecord.message_id.in_(list(message_ids)))
            .group_by(ExternalAttachmentRecord.message_id)
        )
        return {message_id: int(count) for message_id, count in rows}

    def has_unread(self, member_id: str) -> bool:
        """메시지함에 안 읽은 것이 **하나라도** 있나 — 사이드바 점(SPEC-011 §2.2 · 검수 W-3). 레일 숫자와 같은 규칙을
        `EXISTS` 하나로 묻는다: 내 활성 연동의 메일 가운데 읽음 표지가 없는 것 · 내 방의 본문 줄 가운데 방 읽음 지점 뒤의 것 —
        둘 다 내가 보낸 줄(`from_me = true`)은 빼고(`_not_mine`)."""
        I, R, S = ExternalIntegrationRecord, ExternalRoomRecord, ExternalReadStateRecord
        mine = select(I.id).where(I.member_id == member_id, I.removed_at.is_(None))
        mail = (
            select(M.id)
            .join(I, I.id == M.integration_id)
            .where(I.member_id == member_id, I.removed_at.is_(None), I.kind == "mail", M.room_id.is_(None))
            .where(~self._mail_read_exists(member_id), _not_mine())
        )
        read_point = (
            select(S.read_up_to_at)
            .where(S.member_id == member_id, S.room_id == M.room_id)
            .limit(1)
            .scalar_subquery()
        )
        room = (
            select(M.id)
            .join(R, R.id == M.room_id)
            .where(R.integration_id.in_(mine), R.removed_at.is_(None), _top_level(), _not_mine())
            .where(or_(read_point.is_(None), M.sent_at > read_point))
        )
        return bool(self._session.scalar(select(or_(mail.exists(), room.exists()))))

    def room_cards(self, member_id: str, integrations: Sequence[Any]) -> list[dict[str, Any]]:
        """고른 방마다 마지막 시각과 «나의» 미읽음 수. 내 연동의 내 계정이 쓴 줄(슬랙 `user_id`)은 미읽음에 넣지 않는다."""
        if not integrations:
            return []
        by_id = {row.id: row for row in integrations}
        rooms = list(
            self._session.scalars(
                select(ExternalRoomRecord).where(
                    ExternalRoomRecord.integration_id.in_(list(by_id)), ExternalRoomRecord.removed_at.is_(None)
                )
            )
        )
        if not rooms:
            return []
        room_ids = [room.id for room in rooms]
        last = dict(
            self._session.execute(
                select(M.room_id, func.max(M.sent_at)).where(M.room_id.in_(room_ids), _top_level()).group_by(M.room_id)
            ).all()
        )
        reads = {
            row.room_id: row
            for row in self._session.scalars(
                select(ExternalReadStateRecord).where(
                    ExternalReadStateRecord.member_id == member_id, ExternalReadStateRecord.room_id.in_(room_ids)
                )
            )
        }
        cards = []
        for room in rooms:
            integration = by_id[room.integration_id]
            statement = select(func.count(M.id)).where(M.room_id == room.id, _top_level())
            state = reads.get(room.id)
            if state is not None and state.read_up_to_at is not None:
                statement = statement.where(M.sent_at > state.read_up_to_at)
            # 「내 줄 빼기」 = 저장 때 판정한 `from_me`(SPEC-008 v0.7.0 §4.4 · D-34) — 예전엔 이름으로 덮인 `author` 를
            # 슬랙 id 와 견줘 한 줄도 못 뺐다. `null`(모름)은 센다.
            statement = statement.where(_not_mine())
            unread = int(self._session.scalar(statement) or 0) if room.id in last else 0
            cards.append({"room": room, "integration": integration, "last_at": last.get(room.id), "unread_count": unread})
        return cards

    def room_preview(self, room_id: UUID, limit: int) -> list[ExternalMessageRecord]:
        return list(
            self._session.scalars(
                select(M).where(M.room_id == room_id, _top_level()).order_by(M.sent_at.desc(), M.id.desc()).limit(limit)
            )
        )

    # ── 방 메시지 ──
    def room_messages(
        self, room_id: UUID, *, before: tuple[datetime, str] | None, thread_key: str | None, limit: int
    ) -> list[ExternalMessageRecord]:
        statement = select(M).where(M.room_id == room_id)
        if thread_key:
            statement = statement.where(or_(M.thread_key == thread_key, M.external_key == thread_key))
        else:
            statement = statement.where(_top_level())
        condition = _before(before)
        if condition is not None:
            statement = statement.where(condition)
        return list(self._session.scalars(statement.order_by(M.sent_at.desc(), M.id.desc()).limit(limit)))

    # ── AX 맥락 조합(SPEC-008 §4.8 ② · `modules/external_channels/message_context.py`) — **내부 조회 · 공개 API 아님** ──
    def owned_message(self, member_id: str, message_id: UUID) -> tuple[Any, Any | None, Any] | None:
        """그 회원 소유 연동의 메시지(메일·방) — 연동·방이 소프트 딜리트됐으면 `None`(404 로 가린다)."""
        row = self._session.execute(
            select(M, ExternalRoomRecord, ExternalIntegrationRecord)
            .join(ExternalIntegrationRecord, ExternalIntegrationRecord.id == M.integration_id)
            .outerjoin(ExternalRoomRecord, ExternalRoomRecord.id == M.room_id)
            .where(
                M.id == message_id,
                ExternalIntegrationRecord.member_id == member_id,
                ExternalIntegrationRecord.removed_at.is_(None),
                or_(M.room_id.is_(None), ExternalRoomRecord.removed_at.is_(None)),
            )
        ).first()
        return (row[0], row[1], row[2]) if row else None

    def message_origins(self, member_id: str, message_ids: set[UUID]) -> dict[UUID, dict[str, Any]]:
        """업무 상세 `origin.message` (SPEC-008 §4.8 ③ · `MessageOriginPort`). 주인이고 연동·방이 살아 있으면 이름 붙은 글자,
        아니면 이름 없는 글자 — 남의 채널 이름·메일 제목을 업무를 든 다른 사람에게 흘리지 않는다."""
        if not message_ids:
            return {}
        rows = self._session.execute(
            select(M, ExternalRoomRecord, ExternalIntegrationRecord)
            .join(ExternalIntegrationRecord, ExternalIntegrationRecord.id == M.integration_id)
            .outerjoin(ExternalRoomRecord, ExternalRoomRecord.id == M.room_id)
            .where(M.id.in_(list(message_ids)))
        ).all()
        found: dict[UUID, dict[str, Any]] = {}
        for message, room, integration in rows:
            alive = integration.removed_at is None and (room is None or room.removed_at is None)
            if alive and integration.member_id == member_id:
                label = message_reference(message, room).origin_label
            else:
                label = "원래 메일" if message.source_kind == "mail" else "원래 메시지"
            found[message.id] = {
                "message_id": str(message.id),
                "source_kind": message.source_kind,
                "room_id": str(message.room_id) if message.room_id is not None else None,
                "label": label,
            }
        for message_id in message_ids - set(found):
            found[message_id] = {"message_id": str(message_id), "source_kind": None, "room_id": None, "label": "원래 메시지"}
        return found

    def made_task_counts(self, message_ids: Sequence[UUID]) -> dict[UUID, int]:
        """「업무 만듦」 수(SPEC-008 §4.8 ④) — 그 메시지를 원래 메시지로 남긴 업무 수. 요청은 보내는 때 업무가 함께 서므로
        업무만 센다(요청과 그 업무를 두 번 세지 않는다)."""
        if not message_ids:
            return {}
        rows = self._session.execute(
            select(TaskRecord.source_inbox_message_id, func.count(TaskRecord.id))
            .where(TaskRecord.source_inbox_message_id.in_(list(message_ids)))
            .group_by(TaskRecord.source_inbox_message_id)
        ).all()
        return {message_id: int(count) for message_id, count in rows}

    def room_neighbors(
        self, room_id: UUID, *, at: datetime, message_id: UUID, before: int, after: int
    ) -> tuple[list[ExternalMessageRecord], list[ExternalMessageRecord]]:
        """기준 메시지 앞·뒤의 **최상위** 메시지(가까운 것부터). 순서는 방 메시지 조회와 같은 `(sent_at, id)` 다 —
        인덱스 `ix_external_messages_room_sent_at` 를 탄다. 기준 메시지가 스레드 답글이어도 최상위 흐름에서 잰다."""
        base = select(M).where(M.room_id == room_id, _top_level(), M.id != message_id)
        older = self._session.scalars(
            base.where(or_(M.sent_at < at, and_(M.sent_at == at, M.id < message_id)))
            .order_by(M.sent_at.desc(), M.id.desc())
            .limit(before)
        )
        newer = self._session.scalars(
            base.where(or_(M.sent_at > at, and_(M.sent_at == at, M.id > message_id)))
            .order_by(M.sent_at.asc(), M.id.asc())
            .limit(after)
        )
        return list(older), list(newer)

    def thread_messages(self, room_id: UUID, thread_key: str, *, limit: int) -> list[ExternalMessageRecord]:
        return list(
            self._session.scalars(
                select(M)
                .where(M.room_id == room_id, or_(M.thread_key == thread_key, M.external_key == thread_key))
                .order_by(M.sent_at.asc(), M.id.asc())
                .limit(limit)
            )
        )

    def message_in_room(self, room_id: UUID, key: str) -> ExternalMessageRecord | None:
        return self._session.scalar(select(M).where(M.room_id == room_id, M.external_key == key))

    def attachments_of(self, message_ids: Sequence[UUID]) -> dict[UUID, list[ExternalAttachmentRecord]]:
        if not message_ids:
            return {}
        grouped: dict[UUID, list[ExternalAttachmentRecord]] = {}
        for row in self._session.scalars(
            select(ExternalAttachmentRecord)
            .where(ExternalAttachmentRecord.message_id.in_(list(message_ids)))
            .order_by(ExternalAttachmentRecord.seq)
        ):
            grouped.setdefault(row.message_id, []).append(row)
        return grouped

    def attachment_in_room(self, room_id: UUID, aid: str) -> tuple[Any, Any] | None:
        row = self._session.execute(
            select(ExternalAttachmentRecord, M)
            .join(M, M.id == ExternalAttachmentRecord.message_id)
            .where(M.room_id == room_id, ExternalAttachmentRecord.aid == aid)
        ).first()
        return (row[0], row[1]) if row else None

    # ── 읽음 ──
    def room_read_state(self, member_id: str, room_id: UUID) -> ExternalReadStateRecord | None:
        return self._session.scalar(
            select(ExternalReadStateRecord).where(
                ExternalReadStateRecord.member_id == member_id, ExternalReadStateRecord.room_id == room_id
            )
        )

    def mark_message_read(self, member_id: str, message_id: UUID, at: datetime) -> None:
        exists = self._session.scalar(
            select(ExternalReadStateRecord.id).where(
                ExternalReadStateRecord.member_id == member_id, ExternalReadStateRecord.message_id == message_id
            )
        )
        if exists is None:
            # 화면이 같은 메일 읽음을 겹쳐 보내면(카드 누름 + 본문 열림) 둘 다 「없음」을 보고 넣는다 — 진 쪽은 유니크 위반을
            # savepoint 안에서 받아 「이미 읽음」으로 끝낸다(BE 수정 판 5 · 예전엔 500).
            try:
                with self._session.begin_nested():
                    self._session.add(ExternalReadStateRecord(member_id=member_id, message_id=message_id, read_at=at))
                    self._session.flush()
            except IntegrityError:
                pass

    def mark_room_read(self, member_id: str, room_id: UUID, key: str, up_to: datetime, at: datetime) -> None:
        state = self.room_read_state(member_id, room_id)
        if state is None:
            try:
                with self._session.begin_nested():
                    self._session.add(
                        ExternalReadStateRecord(member_id=member_id, room_id=room_id, read_up_to_key=key, read_up_to_at=up_to, read_at=at)
                    )
                    self._session.flush()
                return
            except IntegrityError:
                state = self.room_read_state(member_id, room_id)  # 겹친 요청이 먼저 넣었다 — 그 행을 앞으로만 민다
                if state is None:
                    return
        current = state.read_up_to_at
        if current is not None and current.tzinfo is None and up_to.tzinfo is not None:
            current = current.replace(tzinfo=up_to.tzinfo)
        if current is None or up_to > current:  # 뒤로 가지 않는다
            state.read_up_to_key = key
            state.read_up_to_at = up_to
        state.read_at = at

    def read_all(self, member_id: str, integrations: Sequence[Any], at: datetime) -> None:
        mail_ids = [row.id for row in integrations if row.kind == "mail"]
        if mail_ids:
            unread = list(
                self._session.scalars(
                    select(M.id).where(
                        M.integration_id.in_(mail_ids), M.room_id.is_(None), ~self._mail_read_exists(member_id)
                    )
                )
            )
            try:
                with self._session.begin_nested():
                    self._session.add_all(
                        ExternalReadStateRecord(member_id=member_id, message_id=message_id, read_at=at) for message_id in unread
                    )
                    self._session.flush()
            except IntegrityError:
                # 그 사이 낱장 읽음이 겹쳤다 — 한 통씩 다시(이미 있는 것은 건너뛴다).
                for message_id in unread:
                    self.mark_message_read(member_id, message_id, at)
        room_integrations = [row.id for row in integrations if row.kind != "mail"]
        if room_integrations:
            rooms = self._session.scalars(
                select(ExternalRoomRecord.id).where(
                    ExternalRoomRecord.integration_id.in_(room_integrations), ExternalRoomRecord.removed_at.is_(None)
                )
            ).all()
            for room_id in rooms:
                latest = self._session.scalar(
                    select(M).where(M.room_id == room_id, _top_level()).order_by(M.sent_at.desc(), M.id.desc()).limit(1)
                )
                if latest is not None:
                    self.mark_room_read(member_id, room_id, latest.external_key, latest.sent_at, at)
        self._session.flush()

    # ── 보낸 답장 ──
    def reply_by_key(self, member_id: str, idempotency_key: str) -> ExternalSentReplyRecord | None:
        return self._session.scalar(
            select(ExternalSentReplyRecord).where(
                ExternalSentReplyRecord.member_id == member_id, ExternalSentReplyRecord.idempotency_key == idempotency_key
            )
        )

    def reply_by_id(self, reply_id: UUID) -> ExternalSentReplyRecord | None:
        return self._session.get(ExternalSentReplyRecord, reply_id)

    def add_reply(self, **fields: Any) -> ExternalSentReplyRecord:
        record = ExternalSentReplyRecord(**fields)
        self._session.add(record)
        self._session.flush()
        return record

    def replies_for_message(self, member_id: str, message_id: UUID) -> list[ExternalSentReplyRecord]:
        return list(
            self._session.scalars(
                select(ExternalSentReplyRecord)
                .where(ExternalSentReplyRecord.member_id == member_id, ExternalSentReplyRecord.message_id == message_id)
                .order_by(ExternalSentReplyRecord.created_at)
            )
        )

    # ── 카톡 수신 ──
    def kakao_integration(self, member_id: str, account_key: str, *, lock: bool = False) -> ExternalIntegrationRecord | None:
        statement = select(ExternalIntegrationRecord).where(
            ExternalIntegrationRecord.member_id == member_id,
            ExternalIntegrationRecord.kind == "kakao",
            ExternalIntegrationRecord.account_key == account_key,
            ExternalIntegrationRecord.removed_at.is_(None),
        )
        return self._session.scalar(statement.with_for_update() if lock else statement)

    def selected_room(self, integration_id: UUID, room_id: UUID, *, lock: bool = False) -> ExternalRoomRecord | None:
        statement = select(ExternalRoomRecord).where(
            ExternalRoomRecord.id == room_id,
            ExternalRoomRecord.integration_id == integration_id,
            ExternalRoomRecord.removed_at.is_(None),
        )
        return self._session.scalar(statement.with_for_update() if lock else statement)

    def existing_message_keys(self, integration_id: UUID, container_key: str, keys: list[str]) -> set[str]:
        if not keys:
            return set()
        return set(
            self._session.scalars(
                select(M.external_key).where(
                    M.integration_id == integration_id, M.container_key == container_key, M.external_key.in_(keys)
                )
            )
        )

    def add_message(self, **fields: Any) -> ExternalMessageRecord:
        record = ExternalMessageRecord(**fields)
        self._session.add(record)
        self._session.flush()
        return record

    def add_attachment(self, **fields: Any) -> ExternalAttachmentRecord:
        record = ExternalAttachmentRecord(**fields)
        self._session.add(record)
        self._session.flush()
        return record

    def pending_attachments(self, integration_id: UUID, container_key: str, keys: list[str]) -> list[tuple[Any, Any]]:
        if not keys:
            return []
        rows = self._session.execute(
            select(ExternalAttachmentRecord, M)
            .join(M, M.id == ExternalAttachmentRecord.message_id)
            .where(
                M.integration_id == integration_id,
                M.container_key == container_key,
                M.external_key.in_(keys),
                ExternalAttachmentRecord.state == "pending",
            )
            .order_by(M.sent_at, ExternalAttachmentRecord.seq)
        )
        return [(row[0], row[1]) for row in rows]

    def kakao_attachment(self, integration_id: UUID, aid: str, *, lock: bool = False) -> ExternalAttachmentRecord | None:
        """aid 만으로 찾지 않는다 — 그 회원 카톡 연동 범위 안에서만(조정 1 · 검수 지적)."""
        statement = (
            select(ExternalAttachmentRecord)
            .join(M, M.id == ExternalAttachmentRecord.message_id)
            .where(M.integration_id == integration_id, ExternalAttachmentRecord.aid == aid)
        )
        return self._session.scalar(statement.with_for_update() if lock else statement)

    # ── 사건 ──
    def notify(self, channel: str, payload: str) -> None:
        if self._session.get_bind().dialect.name == "postgresql":
            user_events.publish(self._session, channel, payload)
        else:
            self.local_events.append(payload)


class SqlAlchemyProfileSettingsStore:
    """프로필 이미지 경로·비밀번호 해시·로그인 세션 일괄 해제. 셋이 한 트랜잭션이라 비밀번호만 바뀌고 세션이 남는 일이 없다."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def profile_image(self, member_id: str) -> ProfileImageRecord | None:
        return self._session.get(ProfileImageRecord, member_id)

    def save_profile_image(self, member_id: str, *, storage_key: str, content_type: str, size: int, at: datetime) -> ProfileImageRecord:
        record = self._session.get(ProfileImageRecord, member_id)
        if record is None:
            record = ProfileImageRecord(member_id=member_id, storage_key=storage_key, content_type=content_type, size=size, updated_at=at)
            self._session.add(record)
        else:
            record.storage_key = storage_key
            record.content_type = content_type
            record.size = size
            record.updated_at = at
        self._session.flush()
        return record

    def delete_profile_image(self, member_id: str) -> None:
        self._session.execute(delete(ProfileImageRecord).where(ProfileImageRecord.member_id == member_id))

    def password_hash(self, member_id: str) -> str | None:
        record = self._session.get(MemberCredentialRecord, member_id)
        return record.password_hash if record is not None else None

    def set_password_hash(self, member_id: str, password_hash: str) -> None:
        record = self._session.get(MemberCredentialRecord, member_id)
        if record is None:
            raise LookupError("credential was not found")
        record.password_hash = password_hash

    def revoke_other_sessions(self, member_id: str, keep_session_id: UUID | None, at: datetime) -> int:
        """회원 단위 일괄 해제(SPEC-008 §4.7 — 새로 필요). 지금 이 요청의 세션만 남긴다."""
        statement = update(AuthSessionRecord).where(
            AuthSessionRecord.member_id == member_id, AuthSessionRecord.revoked_at.is_(None)
        )
        if keep_session_id is not None:
            statement = statement.where(AuthSessionRecord.id != keep_session_id)
        result = self._session.execute(statement.values(revoked_at=at).execution_options(synchronize_session="fetch"))
        return int(result.rowcount or 0)


def _not_mine():
    """안 읽음에 세는 줄 — 내가 보낸 줄(`from_me = true`)이 아닌 것. `null`(모름)은 센다(SPEC-011 §4.3-6)."""
    return or_(M.from_me.is_(None), M.from_me.is_(False))
