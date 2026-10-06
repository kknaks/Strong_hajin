"""메시지함·답장·카톡 수신·프로필 설정의 조립 — `WorkflowApplication` 이 이 믹스인을 상속한다 (WORK-011 BE-3).

`bootstrap/application.py` 를 크게 열지 않으려고 따로 둔다(BE-2 와 나란히 간다). 저장소·암호기·상류 어댑터를 끼우는
자리이자 **시험이 대역을 끼우는 자리**다(`_inbox_mail_api`·`_inbox_slack_api`·`_inbox_image_fetcher`).

트랜잭션 규칙: 한 요청 = 세션 하나 · 성공이면 커밋. 연동이 끊김으로 바뀐 실패(`IntegrationUnavailable`)도 커밋한다 —
「상류가 토큰을 거절했다」는 사실을 남겨야 배너가 선다(D-50). 그 밖의 실패는 되돌린다. 사건은 PostgreSQL 이면 같은
트랜잭션의 NOTIFY 로, 아니면(시험) 커밋 뒤 이 프로세스의 허브로 직접 흘린다.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import logging
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import select

from ax_workspace.modules.external_channels.inbox import (
    Download,
    ImageFetcher,
    InboxApplication,
    InboxPage,
    IntegrationUnavailable,
    MailUpstream,
    MailView,
    OutgoingFile,
    RelayCache,
    ReplyAccepted,
    RoomMessagesPage,
    SlackUpstream,
)
from ax_workspace.modules.external_channels.kakao_ingest import (
    KakaoIngestApplication,
    KakaoMessagesAccepted,
    KakaoMessagesCommand,
    KakaoStatusAccepted,
    KakaoStatusCommand,
)
from ax_workspace.modules.external_channels.events import SYNC_WAKE_CHANNEL, sync_wake_payload
from ax_workspace.modules.jobs.domain import JOB_KIND_EXTERNAL_REPLY_DELIVER, JobEnvelope
from ax_workspace.modules.organization_access.domain import Principal
from ax_workspace.modules.organization_access.profile_settings import (
    PasswordChangeCommand,
    ProfileImageSaved,
    ProfileSettingsApplication,
)
from ax_workspace.platform.external_channels import SqlAlchemyExternalChannelRepository
from ax_workspace.platform.external_channels_inbox_store import SqlAlchemyInboxStore, SqlAlchemyProfileSettingsStore
from ax_workspace.platform.external_inbox_upstream import GmailInboxApi, SafeImageFetcher, SlackInboxApi
from ax_workspace.platform.external_storage import LocalDirectoryExternalStorage
from ax_workspace.platform import user_events
from ax_workspace.platform.persistence import ExternalSentReplyRecord
from ax_workspace.platform.user_event_hub import UserEventHub

T = TypeVar("T")

logger = logging.getLogger(__name__)

#: 답장 전송 잡 — lease 는 큰 첨부 업로드가 끝날 만큼, 재시도는 몇 번까지만(그 뒤는 「실패」로 닫고 「다시 보내기」).
REPLY_JOB_LEASE_SECONDS = 180
REPLY_JOB_MAX_ATTEMPTS = 5
#: 이만큼 「보내는 중」에 머문 답장은 잡이 사라졌다고 보고 다시 넣는다(잡이 살아 있으면 enqueue 가 같은 잡을 돌려준다).
REPLY_STUCK_AFTER = timedelta(minutes=5)


class ExternalInboxOperations:
    """`WorkflowApplication` 의 BE-3 operation 들. `self._settings`·`self._session_factory`·`self._external_cipher` 를 쓴다."""

    _settings: Any
    _session_factory: Any
    job_queue: Callable[[Any], Any]
    _external_cipher: Any

    # ── 조립 ─────────────────────────────────────────────────────────────────────────────

    @property
    def user_event_hub(self) -> UserEventHub:
        hub = self.__dict__.get("_user_event_hub")
        if hub is None:
            hub = UserEventHub(self._settings.database_url)
            self.__dict__["_user_event_hub"] = hub
        return hub

    @property
    def external_storage(self) -> LocalDirectoryExternalStorage:
        storage = self.__dict__.get("_external_storage")
        if storage is None:
            storage = LocalDirectoryExternalStorage(Path(self._settings.external_channel_storage_dir))
            self.__dict__["_external_storage"] = storage
        return storage

    @property
    def inbox_mail_api(self) -> MailUpstream | None:
        if "_inbox_mail_api" not in self.__dict__:
            settings = self._settings
            self.__dict__["_inbox_mail_api"] = (
                GmailInboxApi(settings.google_oauth_client_id, settings.google_oauth_client_secret)
                if settings.gmail_oauth_configured
                else None
            )
        return self.__dict__["_inbox_mail_api"]

    @property
    def inbox_slack_api(self) -> SlackUpstream:
        return self.__dict__.setdefault("_inbox_slack_api", SlackInboxApi())

    @property
    def inbox_image_fetcher(self) -> ImageFetcher:
        return self.__dict__.setdefault("_inbox_image_fetcher", SafeImageFetcher())

    def _inbox(self, store: SqlAlchemyInboxStore) -> InboxApplication:
        return InboxApplication(
            store,
            cipher=self._external_cipher,
            mail=self.inbox_mail_api,
            slack=self.inbox_slack_api,
            images=self.inbox_image_fetcher,
            storage=self.external_storage,
            relay_cache=self.inbox_relay_cache,
        )

    @property
    def inbox_relay_cache(self) -> RelayCache:
        """중계 첨부의 짧은 메모리 캐시 — 프로세스 하나에 하나(BE 수정 판 6)."""
        return self.__dict__.setdefault("_inbox_relay_cache", RelayCache())

    def _inbox_unit(self, work: Callable[[SqlAlchemyInboxStore], T], *, write: bool = True) -> T:
        with self._session_factory() as session:
            store = SqlAlchemyInboxStore(session)
            try:
                result = work(store)
            except IntegrationUnavailable:
                session.commit()
                self._flush_local_events(store)
                raise
            if write:
                session.commit()
                self._flush_local_events(store)
            return result

    def _flush_local_events(self, store: SqlAlchemyInboxStore) -> None:
        events, store.local_events = store.local_events, []
        for payload in events:
            self.user_event_hub.dispatch(payload)

    # ── 메시지함 ──────────────────────────────────────────────────────────────────────────

    def inbox_messages(self, principal: Principal, source: str, unread: bool, cursor: str | None) -> InboxPage:
        return self._inbox_unit(
            lambda store: self._inbox(store).list_messages(principal, source=source, unread=unread, cursor=cursor), write=False
        )

    def inbox_mail(self, principal: Principal, message_id: UUID) -> MailView:
        # 안전본을 처음 만들면 채워 둔다 — 그래서 쓰기다.
        return self._inbox_unit(lambda store: self._inbox(store).mail(principal, message_id))

    def inbox_room_messages(
        self, principal: Principal, room_id: UUID, cursor: str | None, thread_ts: str | None
    ) -> RoomMessagesPage:
        return self._inbox_unit(
            lambda store: self._inbox(store).room_messages(principal, room_id, cursor=cursor, thread_ts=thread_ts), write=False
        )

    def mark_inbox_mail_read(self, principal: Principal, message_id: UUID) -> None:
        self._inbox_unit(lambda store: self._inbox(store).mark_mail_read(principal, message_id))

    def mark_inbox_room_read(self, principal: Principal, room_id: UUID, up_to_ts: str) -> None:
        self._inbox_unit(lambda store: self._inbox(store).mark_room_read(principal, room_id, up_to_ts))

    def mark_inbox_read_all(self, principal: Principal, source: str) -> None:
        self._inbox_unit(lambda store: self._inbox(store).read_all(principal, source))

    def inbox_mail_attachment(self, principal: Principal, message_id: UUID, aid: str) -> Download:
        return self._inbox_unit(lambda store: self._inbox(store).mail_attachment(principal, message_id, aid))

    def inbox_room_attachment(self, principal: Principal, room_id: UUID, aid: str, variant: str | None = None) -> Download:
        return self._inbox_unit(lambda store: self._inbox(store).room_attachment(principal, room_id, aid, variant=variant))

    def inbox_remote_image(self, principal: Principal, message_id: UUID, url: str) -> Download:
        return self._inbox_unit(lambda store: self._inbox(store).remote_image(principal, message_id, url))

    def accept_inbox_slack_reply(
        self,
        principal: Principal,
        room_id: UUID,
        *,
        text: str,
        thread_ts: str | None,
        files: list[OutgoingFile],
        idempotency_key: str | None,
    ) -> ReplyAccepted:
        def work(store: SqlAlchemyInboxStore) -> ReplyAccepted:
            accepted = self._inbox(store).accept_slack_reply(
                principal, room_id, text=text, thread_ts=thread_ts, files=files, idempotency_key=idempotency_key
            )
            if accepted.deliver:
                self._queue_reply(store, accepted.local_id, files)
            return accepted

        return self._inbox_unit(work)

    def accept_inbox_mail_reply(
        self,
        principal: Principal,
        message_id: UUID,
        *,
        reply_all: bool,
        body: str,
        to: list[str],
        cc: list[str],
        files: list[OutgoingFile],
        idempotency_key: str | None,
    ) -> ReplyAccepted:
        def work(store: SqlAlchemyInboxStore) -> ReplyAccepted:
            accepted = self._inbox(store).accept_mail_reply(
                principal, message_id, reply_all=reply_all, body=body, to=to, cc=cc, files=files, idempotency_key=idempotency_key
            )
            if accepted.deliver:
                self._queue_reply(store, accepted.local_id, files)
            return accepted

        return self._inbox_unit(work)

    # ── 답장 전송 — 내구 경로 (BE 수정 판 1) ─────────────────────────────────────────────────
    # 접수(202)는 「보내는 중」 행 + 첨부 대기 저장본 + `external.reply_deliver` 잡을 **한 트랜잭션**에 둔다. 연동 워커가
    # 잡을 집어 보내고 결과를 반영한다. 프로세스가 죽으면 lease 가 끝나 다른 차례가 다시 집는다 — 「보내는 중」에 멈추지
    # 않는다. 전송은 at-least-once 다: 상류가 받은 직후·커밋 전에 죽으면 한 번 더 갈 수 있다(잡 재전달의 대가).

    def _queue_reply(self, store: SqlAlchemyInboxStore, local_id: str, files: list[OutgoingFile]) -> None:
        reply = store.reply_by_id(UUID(local_id))
        assert reply is not None
        metas = list((reply.payload or {}).get("files") or [])
        for index, item in enumerate(files):
            key = f"replies/{local_id}/{index}"
            self.external_storage.put(key, item.data, item.content_type)
            if index < len(metas):
                metas[index] = {**metas[index], "storage_key": key}
        reply.payload = {**(reply.payload or {}), "files": metas}
        anchor = reply.room_id or reply.message_id
        self.job_queue(store.session).enqueue(JobEnvelope(
            kind=JOB_KIND_EXTERNAL_REPLY_DELIVER,
            # 같은 방·같은 메일의 답장은 접수 순서대로 나간다.
            ordering_key=f"reply:{anchor}",
            idempotency_key=f"reply:{local_id}",
            payload={"local_id": local_id},
        ))
        # 연동 워커를 바로 깨운다(PostgreSQL 일 때만 — 놓쳐도 워커가 주기적으로 잡을 집는다).
        user_events.publish(store.session, SYNC_WAKE_CHANNEL, sync_wake_payload(str(reply.integration_id), "reply"))

    def deliver_inbox_reply(self, local_id: str) -> str:
        """잡 하나의 일 — 대기 저장본에서 첨부를 읽어 보낸다. 저장본이 없으면 「실패」로 닫는다(멈추지 않는다)."""

        def work(store: SqlAlchemyInboxStore) -> str:
            inbox = self._inbox(store)
            reply = store.reply_by_id(UUID(local_id))
            if reply is None or reply.status != "sending":
                return reply.status if reply is not None else "missing"
            files: list[OutgoingFile] = []
            for meta in (reply.payload or {}).get("files") or []:
                key = meta.get("storage_key")
                try:
                    data = self.external_storage.get(key) if key else None
                except (FileNotFoundError, ValueError):
                    data = None
                if data is None:
                    return inbox.abandon_reply(UUID(local_id), "attachments_lost")
                files.append(OutgoingFile(str(meta.get("name") or "file"), str(meta.get("mime") or "application/octet-stream"), data))
            return inbox.deliver_reply(UUID(local_id), files)

        return self._inbox_unit(work)

    def run_inbox_reply_jobs(self, *, worker_id: str, limit: int = 5) -> int:
        """연동 워커의 한 걸음 — 답장 잡을 집어 보낸다. 집은 수를 돌려준다(0 이면 쉰다)."""
        with self._session_factory() as session:
            jobs = self.job_queue(session).claim(
                JOB_KIND_EXTERNAL_REPLY_DELIVER, limit=limit, lease_seconds=REPLY_JOB_LEASE_SECONDS, worker_id=worker_id
            )
            session.commit()
        for job in jobs:
            local_id = str(job.payload.get("local_id") or "")
            try:
                self.deliver_inbox_reply(local_id)
                finished = "complete"
            except Exception:  # noqa: BLE001 — 한 잡의 실패가 다른 잡을 막지 않는다
                logger.exception("reply delivery %s failed (attempt %d)", local_id, job.attempt)
                finished = "fail" if job.attempt >= REPLY_JOB_MAX_ATTEMPTS else "release"
                if finished == "fail":
                    try:
                        self._inbox_unit(lambda store: self._inbox(store).abandon_reply(UUID(local_id), "delivery_failed"))
                    except Exception:  # noqa: BLE001
                        logger.exception("reply %s could not be closed as failed", local_id)
            with self._session_factory() as session:
                queue = self.job_queue(session)
                if finished == "complete":
                    queue.complete(job.job_id, job.lease_token)
                elif finished == "fail":
                    queue.fail(job.job_id, job.lease_token, error="reply delivery failed")
                else:
                    queue.release(job.job_id, job.lease_token, delay_seconds=min(300, 5 * 2 ** job.attempt), error="reply delivery error")
                session.commit()
        return len(jobs)

    def requeue_stuck_inbox_replies(self, *, older_than: timedelta = REPLY_STUCK_AFTER) -> int:
        """「보내는 중」에 오래 머문 답장의 잡을 다시 넣는다 — 잡이 살아 있으면 같은 잡이 돌아와 아무 일도 없다."""
        cutoff = datetime.now(UTC) - older_than
        with self._session_factory() as session:
            rows = list(session.scalars(
                select(ExternalSentReplyRecord).where(
                    ExternalSentReplyRecord.status == "sending", ExternalSentReplyRecord.updated_at < cutoff
                )
            ))
            queue = self.job_queue(session)
            for row in rows:
                queue.enqueue(JobEnvelope(
                    kind=JOB_KIND_EXTERNAL_REPLY_DELIVER,
                    ordering_key=f"reply:{row.room_id or row.message_id}",
                    idempotency_key=f"reply:{row.id}",
                    payload={"local_id": str(row.id)},
                ))
            session.commit()
            return len(rows)

    # ── 카톡 수신(기기 토큰) ────────────────────────────────────────────────────────────────

    def _kakao(self, store: SqlAlchemyInboxStore) -> KakaoIngestApplication:
        return KakaoIngestApplication(store, storage=self.external_storage)

    def kakao_ingest_messages(self, principal: Principal, command: KakaoMessagesCommand) -> KakaoMessagesAccepted:
        return self._inbox_unit(lambda store: self._kakao(store).ingest_messages(str(principal.id), command))

    def kakao_store_attachment(self, principal: Principal, aid: str, data: bytes, name: str | None, mime: str | None) -> None:
        self._inbox_unit(lambda store: self._kakao(store).store_attachment(str(principal.id), aid, data, name=name, mime=mime))

    def kakao_report_status(self, principal: Principal, command: KakaoStatusCommand) -> KakaoStatusAccepted:
        return self._inbox_unit(lambda store: self._kakao(store).report_status(str(principal.id), command))

    # ── 프로필 설정 ───────────────────────────────────────────────────────────────────────

    def _profile_settings(self, session: Any) -> ProfileSettingsApplication:
        return ProfileSettingsApplication(
            SqlAlchemyProfileSettingsStore(session),
            device_tokens=SqlAlchemyExternalChannelRepository(session),
            storage=self.external_storage,
        )

    def profile_image_url(self, member_id: str) -> str | None:
        with self._session_factory() as session:
            return self._profile_settings(session).image_url(member_id)

    def save_profile_image(self, principal: Principal, data: bytes) -> ProfileImageSaved:
        with self._session_factory() as session:
            result = self._profile_settings(session).save_image(principal, data)
            session.commit()
            return result

    def profile_image(self, principal: Principal) -> tuple[bytes, str]:
        with self._session_factory() as session:
            return self._profile_settings(session).image(principal)

    def delete_profile_image(self, principal: Principal) -> None:
        with self._session_factory() as session:
            self._profile_settings(session).delete_image(principal)
            session.commit()

    def change_password(self, principal: Principal, command: PasswordChangeCommand, keep_session_id: UUID | None) -> None:
        with self._session_factory() as session:
            self._profile_settings(session).change_password(principal, command, keep_session_id=keep_session_id)
            session.commit()
