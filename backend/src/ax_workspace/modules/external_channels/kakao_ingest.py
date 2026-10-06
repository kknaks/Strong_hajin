"""카톡 수신 — **BE-3 소유** (SPEC-008 §4.6 · 수집기 = SPEC-009).

수집기(Mac 앱)는 기기 토큰(`Authorization: Bearer`)으로만 붙고 올린 것은 **그 회원 소유**로만 들어간다.

- `POST …/kakao/messages` — **서버가 가진 고른 방**이 아니면 403(매니페스트 없음 · R-F1 정정) · 묶음 500건 초과 413 ·
  중복 키 `(연동, chatId, logId)` — 서버는 그 방의 `external_id`(= chatId)로 키를 만든다(★1) · 첨부 `aid` =
  `domain.kakao_attachment_aid` · `backfill_done` 이면 그 방 `live`(W-1). 응답의 `attachment_upload` 는 **아직 바이트가
  없는** 첨부 전부다(새로 들어온 것뿐 아니라 재전송 묶음의 것도 — 앱이 업로드 도중 죽었어도 이어 올린다).
- `POST …/kakao/attachments/{aid}` — 그 회원 카톡 연동 범위 안에서 `aid` 를 찾는다(aid 만으로 찾지 않는다 — 조정 1) ·
  50MB 초과 413 · hostPath 저장 · DB 는 경로만.
- `POST …/kakao/status` — 30초 주기 보고 전문을 그대로 남긴다(90초 무보고 = 앱 꺼짐은 읽을 때 판정 · W-3) · 응답은
  `selected_rooms_version`(W3-3). 상태가 «바뀐» 보고만 설정·배너에 사건을 낸다(30초마다 내지 않는다).

연동 레코드는 **첫 handshake 가 만든다**(BE-1). handshake 전에 오는 messages·status 는 409 `handshake_required`.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing_extensions import TypedDict

from ax_workspace.modules.external_channels.domain import (
    KAKAO_ACCOUNT_KEY,
    ExternalChannelError,
    IntegrationKind,
    RoomStatus,
    kakao_attachment_aid,
)
from ax_workspace.modules.external_channels.events import USER_EVENTS_CHANNEL, UserEvent, UserEventType
from ax_workspace.modules.external_channels.inbox import InboxNotFound, PayloadTooLarge, SLACK_FILE_LIMIT_BYTES, aware

#: 묶음 하나의 최대 메시지 수(§4.6).
KAKAO_BATCH_LIMIT = 500
#: 카톡 수집 저장 한도 — 파일 하나당 50MB(OQ-807). 슬랙 보내기와 같은 값.
KAKAO_ATTACHMENT_LIMIT_BYTES = SLACK_FILE_LIMIT_BYTES
#: 바이트를 받는 종류. 동영상·음성·이모티콘은 표시만(D-31).
STORED_KINDS = frozenset({"image", "album", "file"})


class KakaoRoomNotSelected(ExternalChannelError):
    """서버의 고른 방이 아닌 방으로 올렸다 — 403(R-F1 정정)."""


class KakaoHandshakeRequired(ExternalChannelError):
    """카톡 연동이 아직 없다 — 수집기는 handshake 부터 부른다(409)."""


# ── 요청 ─────────────────────────────────────────────────────────────────────────────────


class KakaoAttachmentInput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    kind: Literal["image", "album", "file", "video", "audio", "sticker"]
    seq: int = Field(ge=0, le=10_000)
    name: str = Field(default="", max_length=500)
    size: int | None = Field(default=None, ge=0)
    mime: str | None = Field(default=None, max_length=200)
    expired: bool = False


class KakaoMessageInput(BaseModel):
    model_config = ConfigDict(extra="allow")
    logId: int | str
    seq_in_log: int | None = None
    author: str | None = Field(default=None, max_length=500)
    at: datetime | int | float
    type: str | int | None = None
    text: str | None = Field(default=None, max_length=100_000)
    attachments: list[KakaoAttachmentInput] = Field(default_factory=list, max_length=200)

    @field_validator("logId")
    @classmethod
    def _log_id(cls, value: int | str) -> int | str:
        if isinstance(value, str) and (not value.strip() or len(value) > 64):
            raise ValueError("logId 가 비었거나 너무 깁니다")
        return value

    def sent_at(self) -> datetime:
        if isinstance(self.at, datetime):
            return aware(self.at)
        # 카톡 DB 의 시각은 초(epoch). 밀리초로 오면 그만큼 나눈다.
        seconds = float(self.at) / 1000 if float(self.at) > 10_000_000_000 else float(self.at)
        return datetime.fromtimestamp(seconds, UTC)


class KakaoMessagesCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    room_id: str = Field(min_length=1, max_length=100, title="서버 내부 방 id(handshake 의 room_id)")
    #: 500건 한도는 413 으로 답해야 해서 모델이 아니라 애플리케이션이 센다.
    messages: list[KakaoMessageInput] = Field(default_factory=list)
    backfill_done: bool = False


class KakaoAppStatus(BaseModel):
    model_config = ConfigDict(extra="ignore")
    device_name: str | None = Field(default=None, max_length=200)
    version: str | None = Field(default=None, max_length=50)


class KakaoClientStatus(BaseModel):
    model_config = ConfigDict(extra="ignore")
    state: Literal["running", "off", "unreadable"]
    reason: Literal["permission", "version", "unknown"] | None = None


class KakaoAccountStatus(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str | None = Field(default=None, max_length=200)


class KakaoStatusCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app: KakaoAppStatus
    kakao: KakaoClientStatus
    account: KakaoAccountStatus = Field(default_factory=KakaoAccountStatus)
    account_changed: bool = False


class AttachmentUploadSlot(TypedDict):
    logId: int | str
    seq: int
    aid: str


class KakaoMessagesAccepted(TypedDict):
    accepted: int
    attachment_upload: list[AttachmentUploadSlot]


class KakaoStatusAccepted(TypedDict):
    selected_rooms_version: int


# ── 포트 ─────────────────────────────────────────────────────────────────────────────────


class KakaoIngestRepository(Protocol):
    def kakao_integration(self, member_id: str, account_key: str, *, lock: bool = False) -> Any | None: ...
    def selected_room(self, integration_id: UUID, room_id: UUID, *, lock: bool = False) -> Any | None: ...
    def existing_message_keys(self, integration_id: UUID, container_key: str, keys: list[str]) -> set[str]: ...
    def add_message(self, **fields: Any) -> Any: ...
    def add_attachment(self, **fields: Any) -> Any: ...
    def pending_attachments(self, integration_id: UUID, container_key: str, keys: list[str]) -> list[tuple[Any, Any]]: ...
    def kakao_attachment(self, integration_id: UUID, aid: str, *, lock: bool = False) -> Any | None: ...
    def notify(self, channel: str, payload: str) -> None: ...


class BlobStorage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...


def _numeric(value: str) -> tuple[int, int | str]:
    return (0, int(value)) if value.isdigit() else (1, value)


def _later(current: str | None, candidate: str) -> str:
    if current is None:
        return candidate
    return candidate if _numeric(candidate) > _numeric(current) else current


def kakao_storage_key(integration_id: UUID, aid: str) -> str:
    return f"kakao/{integration_id}/{aid}"


class KakaoIngestApplication:
    def __init__(
        self,
        repository: KakaoIngestRepository,
        *,
        storage: BlobStorage,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._clock = clock

    def ingest_messages(self, member_id: str, command: KakaoMessagesCommand) -> KakaoMessagesAccepted:
        if len(command.messages) > KAKAO_BATCH_LIMIT:
            raise PayloadTooLarge(f"묶음은 {KAKAO_BATCH_LIMIT}건까지입니다")
        integration = self._integration(member_id, lock=True)
        try:
            room_id = UUID(command.room_id)
        except ValueError:
            raise KakaoRoomNotSelected("서버의 고른 방이 아닙니다") from None
        room = self._repository.selected_room(integration.id, room_id, lock=True)
        if room is None:
            raise KakaoRoomNotSelected("서버의 고른 방이 아닙니다")
        chat_id = room.external_id
        now = self._clock()
        incoming: dict[str, KakaoMessageInput] = {}
        for message in command.messages:
            incoming.setdefault(str(message.logId), message)  # 같은 묶음 안의 중복도 버린다
        existing = self._repository.existing_message_keys(integration.id, chat_id, list(incoming))
        accepted = 0
        last_key = room.last_message_key
        for log_id, message in incoming.items():
            last_key = _later(last_key, log_id)
            if log_id in existing:
                continue
            raw = message.model_dump(mode="json")
            row = self._repository.add_message(
                integration_id=integration.id,
                room_id=room.id,
                source_kind=IntegrationKind.KAKAO,
                container_key=chat_id,
                external_key=log_id,
                thread_key=None,
                sent_at=message.sent_at(),
                subject=None,
                author=(message.author or None),
                preview=(message.text or None) if message.text else _attachment_preview(message),
                raw=raw,
                created_at=now,
            )
            for attachment in message.attachments:
                self._repository.add_attachment(
                    message_id=row.id,
                    aid=kakao_attachment_aid(str(integration.id), chat_id, message.logId, attachment.seq),
                    seq=attachment.seq,
                    kind=attachment.kind,
                    state=_initial_state(attachment),
                    name=attachment.name,
                    size=attachment.size,
                    mime=attachment.mime,
                    created_at=now,
                )
            accepted += 1
        uploads: list[AttachmentUploadSlot] = [
            {"logId": incoming[message.external_key].logId, "seq": attachment.seq, "aid": attachment.aid}
            for attachment, message in self._repository.pending_attachments(integration.id, chat_id, list(incoming))
        ]
        room.last_message_key = last_key
        room.synced_count = (room.synced_count or 0) + accepted
        room.updated_at = now
        if accepted:
            room.last_synced_at = now
            integration.synced_count = (integration.synced_count or 0) + accepted
            integration.last_synced_at = now
            integration.updated_at = now
        status_changed = False
        if command.backfill_done and room.backfill_done_at is None:
            room.backfill_done_at = now
            room.status = RoomStatus.LIVE
            status_changed = True
        if accepted:
            self._repository.notify(
                USER_EVENTS_CHANNEL,
                UserEvent(
                    UserEventType.MESSAGE_ARRIVED,
                    member_id,
                    integration_id=str(integration.id),
                    room_id=str(room.id),
                    source_kind=IntegrationKind.KAKAO,
                    data={"count": accepted},
                ).to_payload(),
            )
        if status_changed:
            self._changed(integration)
        return {"accepted": accepted, "attachment_upload": uploads}

    def store_attachment(self, member_id: str, aid: str, data: bytes, *, name: str | None, mime: str | None) -> None:
        if len(data) > KAKAO_ATTACHMENT_LIMIT_BYTES:
            raise PayloadTooLarge("카톡 첨부는 하나당 50MB 까지입니다")
        integration = self._integration(member_id)
        attachment = self._repository.kakao_attachment(integration.id, aid.strip().lower(), lock=True)
        if attachment is None:
            raise InboxNotFound("attachment was not found")
        content_type = (mime or attachment.mime or "application/octet-stream")[:200]
        key = kakao_storage_key(integration.id, attachment.aid)
        self._storage.put(key, data, content_type)
        now = self._clock()
        attachment.storage_key = key
        attachment.state = "stored"
        attachment.size = len(data)
        attachment.mime = content_type
        if name and not attachment.name:
            attachment.name = name[:500]
        attachment.stored_at = now

    def report_status(self, member_id: str, command: KakaoStatusCommand) -> KakaoStatusAccepted:
        integration = self._integration(member_id, lock=True)
        now = self._clock()
        previous = integration.collector_status or {}
        reported = command.model_dump(mode="json")
        was_offline = integration.collector_reported_at is None or (
            now - aware(integration.collector_reported_at)
        ).total_seconds() > 90
        changed = was_offline or any(
            previous.get(part) != reported.get(part) for part in ("app", "kakao", "account", "account_changed")
        )
        integration.collector_status = reported
        integration.collector_reported_at = now
        if command.account.name:
            integration.display_name = command.account.name[:320]
        if changed:
            integration.updated_at = now
            self._changed(integration)
        return {"selected_rooms_version": integration.selected_rooms_version}

    def _integration(self, member_id: str, *, lock: bool = False) -> Any:
        row = self._repository.kakao_integration(member_id, KAKAO_ACCOUNT_KEY, lock=lock)
        if row is None:
            raise KakaoHandshakeRequired("handshake 를 먼저 부르세요")
        return row

    def _changed(self, integration: Any) -> None:
        self._repository.notify(
            USER_EVENTS_CHANNEL,
            UserEvent(
                UserEventType.INTEGRATION_CHANGED, integration.member_id, integration_id=str(integration.id), source_kind=IntegrationKind.KAKAO
            ).to_payload(),
        )


def _initial_state(attachment: KakaoAttachmentInput) -> str:
    if attachment.expired:
        return "expired"
    if attachment.kind not in STORED_KINDS:
        return "not_stored"
    if attachment.size is not None and attachment.size > KAKAO_ATTACHMENT_LIMIT_BYTES:
        return "too_large"
    return "pending"


def _attachment_preview(message: KakaoMessageInput) -> str | None:
    if not message.attachments:
        return None
    labels = {"image": "(사진)", "album": "(사진)", "file": "(파일)", "video": "(동영상)", "audio": "(음성)", "sticker": "(이모티콘)"}
    return labels.get(message.attachments[0].kind, "(첨부)")
