"""메시지함·답장·카톡 수신·프로필 설정의 조립 — `WorkflowApplication` 이 이 믹스인을 상속한다 (WORK-011 BE-3).

`bootstrap/application.py` 를 크게 열지 않으려고 따로 둔다(BE-2 와 나란히 간다). 저장소·암호기·상류 어댑터를 끼우는
자리이자 **시험이 대역을 끼우는 자리**다(`_inbox_mail_api`·`_inbox_slack_api`·`_inbox_image_fetcher`).

트랜잭션 규칙: 한 요청 = 세션 하나 · 성공이면 커밋. 연동이 끊김으로 바뀐 실패(`IntegrationUnavailable`)도 커밋한다 —
「상류가 토큰을 거절했다」는 사실을 남겨야 배너가 선다(D-50). 그 밖의 실패는 되돌린다. 사건은 PostgreSQL 이면 같은
트랜잭션의 NOTIFY 로, 아니면(시험) 커밋 뒤 이 프로세스의 허브로 직접 흘린다.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID

from ax_workspace.modules.external_channels.inbox import (
    Download,
    ImageFetcher,
    InboxApplication,
    InboxPage,
    IntegrationUnavailable,
    MailUpstream,
    MailView,
    OutgoingFile,
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
from ax_workspace.platform.user_event_hub import UserEventHub

T = TypeVar("T")


class ExternalInboxOperations:
    """`WorkflowApplication` 의 BE-3 operation 들. `self._settings`·`self._session_factory`·`self._external_cipher` 를 쓴다."""

    _settings: Any
    _session_factory: Any
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
        )

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

    def inbox_room_attachment(self, principal: Principal, room_id: UUID, aid: str) -> Download:
        return self._inbox_unit(lambda store: self._inbox(store).room_attachment(principal, room_id, aid))

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
        return self._inbox_unit(
            lambda store: self._inbox(store).accept_slack_reply(
                principal, room_id, text=text, thread_ts=thread_ts, files=files, idempotency_key=idempotency_key
            )
        )

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
        return self._inbox_unit(
            lambda store: self._inbox(store).accept_mail_reply(
                principal, message_id, reply_all=reply_all, body=body, to=to, cc=cc, files=files, idempotency_key=idempotency_key
            )
        )

    def deliver_inbox_reply(self, local_id: str, files: list[OutgoingFile]) -> str:
        """202 뒤(BackgroundTasks) 보낸다. 결과는 사건으로 — 여기서 예외를 밖으로 던지지 않는다."""
        return self._inbox_unit(lambda store: self._inbox(store).deliver_reply(UUID(local_id), files))

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
