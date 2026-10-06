"""연결·연동·고른 방·기기 토큰 — 외부 채널의 소유 경계 (SPEC-008 §4.2·§4.3·§4.6 일부 · WORK-011 Phase BE-1).

소유는 하나의 규칙이다: **연동·방은 연결한 회원 것이고, 남의 것은 없는 것처럼 404**(D-24 · `ResourceNotFound`).
웹 라우트는 로그인 세션의 `Principal` 로, 수집기 라우트는 기기 토큰이 가리키는 회원으로 같은 규칙을 지난다.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
import secrets
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import TypedDict

from ax_workspace.modules.errors import ResourceNotFound
from ax_workspace.modules.external_channels.domain import (
    KAKAO_ACCOUNT_KEY,
    OAUTH_KINDS,
    OAUTH_STATE_TTL,
    ROOM_TYPES,
    DeviceTokenOwner,
    IntegrationAlreadyConnected,
    IntegrationKind,
    IntegrationNotConfigured,
    IntegrationStatus,
    IntegrationDisconnected,
    InvalidRoomSelection,
    RoomAccessDenied,
    OAuthExchangeFailed,
    OAuthStateRejected,
    RoomStatus,
    collector_is_offline,
    effective_room_status,
    next_selected_rooms_version,
    secret_digest,
    settings_return_url,
)
from ax_workspace.modules.external_channels.events import (
    SYNC_WAKE_CHANNEL,
    USER_EVENTS_CHANNEL,
    UserEvent,
    UserEventType,
    sync_wake_payload,
)
from ax_workspace.modules.organization_access.domain import Principal

#: 기기 토큰의 눈에 띄는 머리 — 비밀 검사기가 잡을 수 있게. 뒤는 32바이트 난수.
DEVICE_TOKEN_PREFIX = "axdt_"
#: 「마지막 사용 시각」을 매 요청마다 쓰지 않는다 — 30초 주기 보고마다 행을 고치지 않게.
DEVICE_TOKEN_TOUCH_INTERVAL_SECONDS = 60


class IntegrationMissing(RuntimeError, ResourceNotFound):
    """연동·방·기기 토큰이 없거나 남의 것이다 — 같은 404 로 가린다(D-24)."""


# ── 포트 ─────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class OAuthGrant:
    """code 를 바꿔 얻은 것. 토큰은 이 객체를 지나 곧바로 암호화된다 — 로그·응답에 싣지 않는다."""

    account_key: str
    display_name: str
    account_meta: dict[str, Any]
    #: 토큰 두 칸은 repr 에서 뺀다 — 예외 추적·디버그 로그가 grant 를 찍어도 평문이 남지 않는다(검수 W-3).
    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    expires_at: datetime | None
    scopes: str


class OAuthProvider(Protocol):
    def authorize_url(self, *, state: str, redirect_uri: str) -> str: ...
    def exchange(self, *, code: str, redirect_uri: str) -> OAuthGrant: ...


@dataclass(frozen=True, slots=True)
class SlackRoomInfo:
    """슬랙 방 하나를 **그 회원 토큰으로** 본 모습. 그룹 DM 은 참여자 실명(D-12) · DM 은 상대 이름 · 봇 표시(D-13)."""

    room_id: str
    type: str
    name: str
    is_bot: bool = False
    member_count: int | None = None


class SlackRoomDirectory(Protocol):
    """슬랙 방 목록·접근 확인 — 회원의 사용자 토큰으로 묻는다(F-1 · F-2).

    `describe` 는 그 회원이 그 방을 **볼 수 있을 때만** 답한다 — 아니면 `RoomAccessDenied`. 공개 채널은 참여(is_member)까지
    본다: 사용자 토큰 이벤트는 참여한 방만 오고, 참여하지 않은 채로 고르면 실시간이 비어 있다. 토큰이 거절되면
    `IntegrationDisconnected`, 상류가 안 되면 `UpstreamUnavailableError`.
    """

    def describe(self, token: str, channel: str) -> SlackRoomInfo: ...
    def list_page(self, token: str, cursor: str | None) -> tuple[list[SlackRoomInfo], str | None]: ...


class TokenCipher(Protocol):
    def encrypt(self, plaintext: str) -> str: ...
    def decrypt(self, ciphertext: str) -> str: ...


class ExternalChannelRepository(Protocol):
    def integrations_for(self, member_id: str) -> list[Any]: ...
    def integration_for(self, integration_id: UUID, member_id: str, *, lock: bool = False) -> Any | None: ...
    def integration_by_account(self, member_id: str, kind: str, account_key: str, *, lock: bool = False) -> Any | None: ...
    def active_integrations_of_kind(self, member_id: str, kind: str) -> list[Any]: ...
    def add_integration(self, **fields: Any) -> Any: ...
    def rooms_of(self, integration_id: UUID) -> list[Any]: ...
    def room_of(self, integration_id: UUID, room_id: UUID, *, lock: bool = False) -> Any | None: ...
    def room_by_external(self, integration_id: UUID, external_id: str, *, lock: bool = False) -> Any | None: ...
    def add_room(self, **fields: Any) -> Any: ...
    def add_oauth_state(self, **fields: Any) -> Any: ...
    def oauth_state(self, state_hash: str, *, lock: bool = False) -> Any | None: ...
    def add_device_token(self, **fields: Any) -> Any: ...
    def device_tokens_for(self, member_id: str) -> list[Any]: ...
    def device_token_for(self, token_id: UUID, member_id: str, *, lock: bool = False) -> Any | None: ...
    def device_token_by_hash(self, token_hash: str) -> Any | None: ...
    def revoke_device_tokens(self, member_id: str, at: datetime) -> int: ...
    def notify(self, channel: str, payload: str) -> None: ...


# ── 명령·결과 ─────────────────────────────────────────────────────────────────────────────────


class RoomDetail(BaseModel):
    """고른 방 한 줄의 겉 정보. 카톡 방 «목록»은 서버가 모르므로(R-F1) 고른 쪽이 함께 보낸다."""

    model_config = ConfigDict(extra="forbid")
    room_id: str = Field(min_length=1, max_length=100, title="외부 방 id — 슬랙 channel · 카톡 chatId")
    type: str | None = Field(default=None, title="방 종류 — 슬랙 channel/private/dm/group_dm · 카톡 direct/group")
    name: str | None = Field(default=None, max_length=300)
    member_count: int | None = Field(default=None, ge=0)
    is_bot: bool | None = None


class AddRoomsCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    room_ids: list[str] = Field(min_length=1, max_length=500, title="고를 외부 방 id")
    rooms: list[RoomDetail] = Field(default_factory=list, max_length=500, title="방 겉 정보(선택 · 카톡은 종류 필수)")


class DeviceTokenIssueCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_name: str = Field(min_length=1, max_length=200, title="기기 이름")


class AvailableRoomView(TypedDict):
    room_id: str
    type: str
    name: str
    is_bot: bool
    member_count: int | None
    already_added: bool


class AvailableRoomsPage(TypedDict):
    rooms: list[AvailableRoomView]
    next_cursor: str | None


class ConnectStart(TypedDict):
    authorize_url: str
    state: str


class CollectorView(TypedDict):
    app_state: Literal["on", "off"]
    device_name: str | None
    app_version: str | None
    kakao_state: str | None
    kakao_reason: str | None
    account_name: str | None
    account_changed: bool
    reported_at: str | None


class IntegrationView(TypedDict):
    id: str
    kind: str
    status: str
    display_name: str
    synced_count: int
    last_synced_at: str | None
    backfill_count: int
    #: 슬랙 워크스페이스 도메인(`<team>.slack.com`) — 워크스페이스 줄·퍼머링크(BE 수정 판 3). 모르면 null.
    domain: str | None
    collector: CollectorView | None


class RoomView(TypedDict):
    room_id: str
    external_id: str
    type: str
    name: str
    member_count: int | None
    synced_count: int
    status: str


class DeviceTokenIssued(TypedDict):
    token: str


class DeviceTokenView(TypedDict):
    id: str
    device_name: str
    created_at: str
    last_used_at: str | None


class KakaoSelectedRoom(TypedDict):
    room_id: str
    external_id: str
    last_logId: int | str | None


class KakaoHandshakeView(TypedDict):
    integration_id: str
    selected_rooms: list[KakaoSelectedRoom]
    reset_at: str | None
    selected_rooms_version: int


@dataclass(frozen=True, slots=True)
class CallbackOutcome:
    """콜백이 끝난 뒤 브라우저를 돌려보낼 곳. `state` 가 틀리면 이것이 아니라 `OAuthStateRejected`(400)다."""

    kind: str
    outcome: Literal["ok", "denied", "error"]
    redirect_url: str
    integration_id: str | None = None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class ExternalChannelApplication:
    """세션 하나 위의 연동 명령. 조립(`bootstrap/application.py`)이 저장소·암호기·OAuth 어댑터를 끼운다."""

    def __init__(
        self,
        repository: ExternalChannelRepository,
        *,
        cipher: TokenCipher | None,
        providers: dict[str, OAuthProvider],
        api_origin: str,
        web_origin: str,
        slack_directory: SlackRoomDirectory | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._slack_directory = slack_directory
        self._cipher = cipher
        self._providers = providers
        self._api_origin = api_origin
        self._web_origin = web_origin
        self._clock = clock

    # ── 연결 (§4.2) ──────────────────────────────────────────────────────────────────────

    def redirect_uri(self, kind: str) -> str:
        return f"{self._api_origin}/api/integrations/{kind}/callback"

    def start_connect(self, principal: Principal, kind: str) -> ConnectStart:
        provider = self._configured(kind)
        if kind == IntegrationKind.SLACK and self._repository.active_integrations_of_kind(str(principal.id), kind):
            # 슬랙은 워크스페이스 하나. 메일은 계정 여럿이라 409 가 없다(N-1).
            raise IntegrationAlreadyConnected("이미 연결된 슬랙 워크스페이스가 있습니다")
        return self._issue_state(principal, kind, provider, integration_id=None)

    def reconnect(self, principal: Principal, integration_id: UUID) -> ConnectStart:
        row = self._owned(principal, integration_id)
        if row.kind not in OAUTH_KINDS:
            # 카톡은 동의 화면이 없다 — 앱이 기기 토큰으로 붙는다(§4.6).
            raise InvalidRoomSelection("카카오톡 연동은 Mac 앱에서 다시 연결합니다")
        provider = self._configured(row.kind)
        return self._issue_state(principal, row.kind, provider, integration_id=row.id)

    def complete_callback(
        self, kind: str, *, state: str | None, code: str | None, error: str | None, browser_member_id: str | None = None
    ) -> CallbackOutcome:
        """`state` 로 회원을 찾는다 — OS 브라우저에는 세션 쿠키가 없다(F-2). `state` 는 한 번만 통한다.

        `browser_member_id` 는 콜백을 연 브라우저의 로그인 세션 회원이다(없으면 None — 데스크톱이 연 OS 브라우저).
        **세션이 있는데 `state` 의 회원과 다르면 거절한다**(검수 W-2): 남이 만든 연결 링크를 받아 내 브라우저에서
        동의하면 내 메일이 그 사람 연동으로 들어가는 「링크 넘기기」를 막는다. 세션이 없으면 막을 근거가 없어
        종전대로 `state` 만 본다 — 그 경로는 데스크톱 앱이 연 링크다.
        """
        now = self._clock()
        record = self._repository.oauth_state(secret_digest(state), lock=True) if state else None
        if (
            record is None
            or record.kind != kind
            or record.consumed_at is not None
            or _aware(record.expires_at) <= now
        ):
            raise OAuthStateRejected("연결 요청이 만료됐거나 올바르지 않습니다")
        # 결과가 무엇이든 그 state 는 여기서 끝난다 — 같은 링크로 두 번 연결되지 않는다.
        record.consumed_at = now
        if browser_member_id is not None and browser_member_id != record.member_id:
            raise OAuthStateRejected("이 연결 링크는 지금 로그인한 계정이 만든 것이 아닙니다")
        if error or not code:
            return CallbackOutcome(kind, "denied", settings_return_url(self._web_origin, kind, "denied"))
        provider = self._configured(kind)
        try:
            grant = provider.exchange(code=code, redirect_uri=self.redirect_uri(kind))
        except OAuthExchangeFailed:
            return CallbackOutcome(kind, "error", settings_return_url(self._web_origin, kind, "error"))
        member_id = record.member_id
        if kind == IntegrationKind.SLACK:
            others = [
                row for row in self._repository.active_integrations_of_kind(member_id, kind)
                if row.account_key != grant.account_key
            ]
            if others:
                return CallbackOutcome(kind, "error", settings_return_url(self._web_origin, kind, "error"))
        row = self._store_grant(member_id, kind, grant, now)
        self._repository.notify(SYNC_WAKE_CHANNEL, sync_wake_payload(str(row.id), "connected"))
        self._repository.notify(
            USER_EVENTS_CHANNEL,
            UserEvent(UserEventType.INTEGRATION_CHANGED, member_id, integration_id=str(row.id), source_kind=kind).to_payload(),
        )
        return CallbackOutcome(kind, "ok", settings_return_url(self._web_origin, kind, "ok"), str(row.id))

    def disconnect(self, principal: Principal, integration_id: UUID) -> None:
        """소프트 딜리트(D-27). 방·메시지는 남아 같은 계정을 다시 연결하면 되살아난다(D-46). 카톡은 기기 토큰도 철회한다.

        저장 토큰은 지운다 — 「더 이상 받지 않는다」를 그대로 지키고, 되살림은 새 동의가 새 토큰을 가져온다.
        """
        row = self._owned(principal, integration_id, lock=True)
        now = self._clock()
        row.status = IntegrationStatus.REMOVED
        row.removed_at = now
        row.updated_at = now
        row.access_token_encrypted = None
        row.refresh_token_encrypted = None
        row.token_expires_at = None
        row.watch_expires_at = None
        if row.kind == IntegrationKind.KAKAO:
            # 카톡에는 OAuth 토큰이 없다 — 앱의 **기기 토큰**이 수집을 붙든다. 그것까지 철회하지 않으면 다음 handshake 가
            # 연동을 되살려 해제가 30초 만에 무효가 된다(검수 W-5 · D-27). 다시 붙이려면 「이 Mac 연결」로 새로 발급한다.
            self._repository.revoke_device_tokens(row.member_id, now)
        self._changed(row)

    # ── 연동 목록·고른 방 (§4.3) ─────────────────────────────────────────────────────────────

    def integrations(self, principal: Principal) -> list[IntegrationView]:
        now = self._clock()
        return [self._integration_view(row, now) for row in self._repository.integrations_for(str(principal.id))]

    def rooms(self, principal: Principal, integration_id: UUID) -> list[RoomView]:
        row = self._owned(principal, integration_id)
        now = self._clock()
        return [self._room_view(row, room, now) for room in self._repository.rooms_of(row.id)]

    def add_rooms(self, principal: Principal, integration_id: UUID, command: AddRoomsCommand) -> None:
        """고른 방 저장 — 슬랙·카톡 모두 서버 정본(R-F1 정정 · D-26). 이미 고른 방은 다시 붙지 않는다."""
        row = self._owned(principal, integration_id, lock=True)
        allowed = ROOM_TYPES.get(IntegrationKind(row.kind))
        if allowed is None:
            raise InvalidRoomSelection("메일 연동에는 고를 방이 없습니다")
        details = {detail.room_id: detail for detail in command.rooms}
        now = self._clock()
        changed = False
        token = self._slack_token(row) if row.kind == IntegrationKind.SLACK else None
        for external_id in dict.fromkeys(item.strip() for item in command.room_ids):
            if not external_id or len(external_id) > 100:
                raise InvalidRoomSelection("방 id 가 비었거나 너무 깁니다")
            room = self._repository.room_by_external(row.id, external_id, lock=True)
            if room is not None and room.removed_at is None:
                continue
            detail = details.get(external_id)
            verified: dict[str, Any] = {}
            if token is not None:
                # 슬랙은 **그 회원 토큰으로 볼 수 있는 방만** 받는다(F-1). 클라이언트가 보낸 종류·이름은 믿지 않고
                # 슬랙이 그 회원에게 답한 것을 쓴다 — 남의 DM·비공개 채널 id 를 넣어도 여기서 422 로 끝난다.
                assert self._slack_directory is not None
                try:
                    info = self._slack_directory.describe(token, external_id)
                except RoomAccessDenied as denied:
                    raise InvalidRoomSelection(f"이 슬랙 방을 볼 수 없습니다: {external_id}") from denied
                detail = RoomDetail(room_id=external_id, type=info.type, name=info.name, member_count=info.member_count, is_bot=info.is_bot)
                verified = {"verified_at": now.isoformat()}
            room_type = (detail.type if detail else None) or _default_room_type(row.kind, external_id)
            if room_type not in allowed:
                raise InvalidRoomSelection(f"{row.kind} 방 종류가 아닙니다: {room_type}")
            if room is None:
                self._repository.add_room(
                    integration_id=row.id,
                    external_id=external_id,
                    room_type=room_type,
                    name=(detail.name if detail and detail.name else external_id),
                    member_count=detail.member_count if detail else None,
                    room_meta={**({"is_bot": True} if detail and detail.is_bot else {}), **verified},
                    status=RoomStatus.BACKFILLING,
                    created_at=now,
                    updated_at=now,
                )
            else:
                # 다시 고른 방은 되살린다 — 쌓인 대화가 그대로 돌아오고 빈 구간만 새로 받는다(D-46).
                room.removed_at = None
                room.room_type = room_type
                if detail and detail.name:
                    room.name = detail.name
                if detail and detail.member_count is not None:
                    room.member_count = detail.member_count
                room.status = RoomStatus.LIVE if room.backfill_done_at else RoomStatus.BACKFILLING
                if verified:
                    room.room_meta = {key: value for key, value in {**(room.room_meta or {}), **verified}.items() if key != "access_lost"}
                room.updated_at = now
            changed = True
        if changed:
            row.selected_rooms_version = next_selected_rooms_version(row.selected_rooms_version, changed=True)
            row.updated_at = now
            self._repository.notify(SYNC_WAKE_CHANNEL, sync_wake_payload(str(row.id), "rooms"))
            self._changed(row)

    def available_slack_rooms(self, principal: Principal, *, q: str | None, cursor: str | None) -> AvailableRoomsPage:
        """방 고르기 창의 목록(SPEC-008 §4.3 · F-2) — 그 회원 토큰으로 실시간 조회. 「추가됨」은 지금 고른 방."""
        rows = self._repository.active_integrations_of_kind(str(principal.id), IntegrationKind.SLACK)
        if not rows:
            raise IntegrationMissing("slack integration was not found")
        row = rows[0]
        token = self._slack_token(row)
        assert self._slack_directory is not None
        rooms, next_cursor = self._slack_directory.list_page(token, cursor)
        added = {room.external_id for room in self._repository.rooms_of(row.id)}
        needle = (q or "").strip().lower()
        return {
            "rooms": [
                {
                    "room_id": room.room_id,
                    "type": room.type,
                    "name": room.name,
                    "is_bot": room.is_bot,
                    "member_count": room.member_count,
                    "already_added": room.room_id in added,
                }
                for room in rooms
                if not needle or needle in room.name.lower()
            ],
            "next_cursor": next_cursor,
        }

    def _slack_token(self, row: Any) -> str:
        if row.status == IntegrationStatus.DISCONNECTED or not row.access_token_encrypted:
            raise IntegrationDisconnected("슬랙 연결이 끊겼습니다 — 다시 연결해 주세요")
        if self._cipher is None or self._slack_directory is None:
            raise IntegrationNotConfigured(IntegrationKind.SLACK)
        return self._cipher.decrypt(row.access_token_encrypted)

    def remove_room(self, principal: Principal, integration_id: UUID, room_id: UUID) -> None:
        """방 빼기 = 소프트 딜리트(§2.3). 웹에서도 된다 — 고른 방은 서버 정본이다(R-F1 정정)."""
        row = self._owned(principal, integration_id, lock=True)
        room = self._repository.room_of(row.id, room_id, lock=True)
        if room is None:
            raise IntegrationMissing("room was not found")
        now = self._clock()
        room.removed_at = now
        room.updated_at = now
        row.selected_rooms_version = next_selected_rooms_version(row.selected_rooms_version, changed=True)
        row.updated_at = now
        self._changed(row)

    # ── 기기 토큰 (§4.2 · R3-F1) ──────────────────────────────────────────────────────────────

    def issue_device_token(self, principal: Principal, command: DeviceTokenIssueCommand) -> DeviceTokenIssued:
        """발급은 세션 있는 웹에서만. **같은 회원의 옛 토큰을 철회한다**(D-45 — 사람당 Mac 한 대)."""
        member_id = str(principal.id)
        now = self._clock()
        self._repository.revoke_device_tokens(member_id, now)
        token = DEVICE_TOKEN_PREFIX + secrets.token_urlsafe(32)
        self._repository.add_device_token(
            member_id=member_id, device_name=command.device_name.strip(), token_hash=secret_digest(token), created_at=now
        )
        return {"token": token}

    def device_tokens(self, principal: Principal) -> list[DeviceTokenView]:
        return [
            {
                "id": str(row.id),
                "device_name": row.device_name,
                "created_at": row.created_at.isoformat(),
                "last_used_at": _iso(row.last_used_at),
            }
            for row in self._repository.device_tokens_for(str(principal.id))
        ]

    def revoke_device_token(self, principal: Principal, token_id: UUID) -> None:
        row = self._repository.device_token_for(token_id, str(principal.id), lock=True)
        if row is None:
            raise IntegrationMissing("device token was not found")
        row.revoked_at = self._clock()

    def revoke_member_device_tokens(self, member_id: str) -> int:
        """회원의 기기 토큰을 모두 무효로 — 비밀번호 변경·회원 비활성 때 부른다(R3-F1 ⑤ · BE-3 비밀번호 API)."""
        return self._repository.revoke_device_tokens(member_id, self._clock())

    def device_token_owner(self, token: str) -> DeviceTokenOwner | None:
        """Bearer 토큰 → 회원. 철회됐거나 모르는 토큰은 `None`. 회원이 활동 중인지는 부르는 쪽이 따로 묻는다."""
        if not token.startswith(DEVICE_TOKEN_PREFIX):
            return None
        row = self._repository.device_token_by_hash(secret_digest(token))
        if row is None or row.revoked_at is not None:
            return None
        now = self._clock()
        last = _aware(row.last_used_at)
        if last is None or (now - last).total_seconds() >= DEVICE_TOKEN_TOUCH_INTERVAL_SECONDS:
            row.last_used_at = now
        return DeviceTokenOwner(token_id=str(row.id), member_id=row.member_id)

    # ── 카톡 — handshake(기기 토큰) · reset-account(웹 세션) ─────────────────────────────────────

    def kakao_handshake(self, member_id: str) -> KakaoHandshakeView:
        """서버의 고른 방(정본)과 각 방 마지막 반영 지점을 내려준다. **첫 handshake 가 카톡 연동을 만든다**(F-4 ④).

        `external_id`(= chatId)를 함께 준다 — 앱은 로컬 카톡 DB 에서 chatId 로 방을 찾는다(4차 검수 ★1).
        """
        now = self._clock()
        row = self._repository.integration_by_account(member_id, IntegrationKind.KAKAO, KAKAO_ACCOUNT_KEY, lock=True)
        if row is None:
            row = self._repository.add_integration(
                member_id=member_id,
                kind=IntegrationKind.KAKAO,
                status=IntegrationStatus.CONNECTED,
                account_key=KAKAO_ACCOUNT_KEY,
                display_name="",
                account_meta={},
                created_at=now,
                updated_at=now,
            )
            self._changed(row)
        elif row.removed_at is not None:
            # 해제했던 카톡을 앱이 다시 붙이면 되살린다(D-46). 해제가 수집을 막으려면 기기 토큰을 철회한다.
            row.removed_at = None
            row.status = IntegrationStatus.CONNECTED
            row.updated_at = now
            self._changed(row)
        return {
            "integration_id": str(row.id),
            "selected_rooms": [
                {"room_id": str(room.id), "external_id": room.external_id, "last_logId": _log_id(room.last_message_key)}
                for room in self._repository.rooms_of(row.id)
            ],
            "reset_at": _iso(row.account_reset_at),
            "selected_rooms_version": row.selected_rooms_version,
        }

    def kakao_reset_account(self, principal: Principal) -> None:
        """계정이 바뀐 뒤 사람이 「다시 연결」— **웹 세션 라우트**(4차 검수 ★2). 옛 계정의 방은 소프트 딜리트로 남긴다
        (D-46) · `reset_at` 을 찍어 앱이 다음 handshake 에서 안다(W-4 · N-9)."""
        rows = self._repository.active_integrations_of_kind(str(principal.id), IntegrationKind.KAKAO)
        if not rows:
            raise IntegrationMissing("kakao integration was not found")
        row = self._repository.integration_for(rows[0].id, str(principal.id), lock=True)
        now = self._clock()
        for room in self._repository.rooms_of(row.id):
            room.removed_at = now
            room.updated_at = now
        row.account_reset_at = now
        if row.collector_status:
            row.collector_status = {**row.collector_status, "account_changed": False}
        row.selected_rooms_version = next_selected_rooms_version(row.selected_rooms_version, changed=True)
        row.updated_at = now
        self._changed(row)

    # ── 안쪽 ─────────────────────────────────────────────────────────────────────────────

    def _configured(self, kind: str) -> OAuthProvider:
        if kind not in OAUTH_KINDS:
            raise IntegrationMissing("integration kind was not found")
        provider = self._providers.get(kind)
        if provider is None or self._cipher is None:
            raise IntegrationNotConfigured(kind)
        return provider

    def _issue_state(self, principal: Principal, kind: str, provider: OAuthProvider, *, integration_id: UUID | None) -> ConnectStart:
        state = secrets.token_urlsafe(32)
        now = self._clock()
        self._repository.add_oauth_state(
            state_hash=secret_digest(state),
            member_id=str(principal.id),
            kind=kind,
            integration_id=integration_id,
            created_at=now,
            expires_at=now + OAUTH_STATE_TTL,
        )
        return {"authorize_url": provider.authorize_url(state=state, redirect_uri=self.redirect_uri(kind)), "state": state}

    def _store_grant(self, member_id: str, kind: str, grant: OAuthGrant, now: datetime) -> Any:
        assert self._cipher is not None
        access = self._cipher.encrypt(grant.access_token)
        refresh = self._cipher.encrypt(grant.refresh_token) if grant.refresh_token else None
        row = self._repository.integration_by_account(member_id, kind, grant.account_key, lock=True)
        if row is None:
            return self._repository.add_integration(
                member_id=member_id,
                kind=kind,
                status=IntegrationStatus.BACKFILLING,
                account_key=grant.account_key,
                display_name=grant.display_name,
                account_meta=grant.account_meta,
                access_token_encrypted=access,
                refresh_token_encrypted=refresh,
                token_expires_at=grant.expires_at,
                scopes=grant.scopes,
                created_at=now,
                updated_at=now,
            )
        # 같은 주소·워크스페이스 = 예전 것을 되살린다(D-46 · N-1). 쌓인 것은 그대로, 백필이 끝났던 계정은
        # 마지막 반영 지점부터 빈 구간만 메운다(BE-2).
        row.status = IntegrationStatus.CONNECTED if row.backfill_done_at else IntegrationStatus.BACKFILLING
        row.removed_at = None
        row.disconnected_reason = None
        row.disconnected_at = None
        row.last_error = None
        row.display_name = grant.display_name or row.display_name
        row.account_meta = {**(row.account_meta or {}), **grant.account_meta}
        row.access_token_encrypted = access
        # Google 은 다시 동의해도 refresh token 을 안 줄 수 있다 — 있던 것을 지우지 않는다.
        if refresh is not None:
            row.refresh_token_encrypted = refresh
        row.token_expires_at = grant.expires_at
        row.scopes = grant.scopes
        row.updated_at = now
        return row

    def _owned(self, principal: Principal, integration_id: UUID, *, lock: bool = False) -> Any:
        row = self._repository.integration_for(integration_id, str(principal.id), lock=lock)
        if row is None:
            raise IntegrationMissing("integration was not found")
        return row

    def _changed(self, row: Any) -> None:
        self._repository.notify(
            USER_EVENTS_CHANNEL,
            UserEvent(UserEventType.INTEGRATION_CHANGED, row.member_id, integration_id=str(row.id), source_kind=row.kind).to_payload(),
        )

    def _integration_view(self, row: Any, now: datetime) -> IntegrationView:
        return {
            "id": str(row.id),
            "kind": row.kind,
            "status": row.status,
            "display_name": row.display_name,
            "synced_count": row.synced_count or 0,
            "last_synced_at": _iso(row.last_synced_at),
            "backfill_count": row.backfill_count or 0,
            "domain": (row.account_meta or {}).get("domain"),
            "collector": self._collector_view(row, now) if row.kind == IntegrationKind.KAKAO else None,
        }

    @staticmethod
    def _collector_view(row: Any, now: datetime) -> CollectorView:
        status = row.collector_status or {}
        app = status.get("app") or {}
        kakao = status.get("kakao") or {}
        account = status.get("account") or {}
        reported_at = _aware(row.collector_reported_at)
        return {
            "app_state": "off" if collector_is_offline(reported_at, now) else "on",
            "device_name": app.get("device_name"),
            "app_version": app.get("version"),
            "kakao_state": kakao.get("state"),
            "kakao_reason": kakao.get("reason"),
            "account_name": account.get("name"),
            "account_changed": bool(status.get("account_changed")),
            "reported_at": _iso(reported_at),
        }

    @staticmethod
    def _room_view(integration: Any, room: Any, now: datetime) -> RoomView:
        return {
            "room_id": str(room.id),
            "external_id": room.external_id,
            "type": room.room_type,
            "name": room.name,
            "member_count": room.member_count,
            "synced_count": room.synced_count or 0,
            "status": effective_room_status(
                room.status,
                kind=integration.kind,
                collector_status=integration.collector_status,
                collector_reported_at=_aware(integration.collector_reported_at),
                now=now,
            ),
        }


def _default_room_type(kind: str, external_id: str) -> str | None:
    """종류를 안 보냈을 때. 슬랙은 id 머리로 어림한다(`D`=DM) — 정확한 종류는 백필이 채운다(BE-2).

    카톡은 어림할 길이 없다 — 앱 웹뷰가 로컬 목록의 종류를 함께 보내야 한다(없으면 422).
    """
    if kind == IntegrationKind.SLACK:
        return "dm" if external_id.startswith("D") else "channel"
    return None


def _log_id(value: str | None) -> int | str | None:
    if value is None:
        return None
    return int(value) if value.isdigit() else value
