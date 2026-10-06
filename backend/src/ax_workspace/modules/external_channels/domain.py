"""외부 채널의 말 — 종류·상태·오류와 앱·서버가 함께 쓰는 파생 규칙 (SPEC-008 §4.1·§4.6)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
import hashlib


class IntegrationKind(StrEnum):
    MAIL = "mail"
    SLACK = "slack"
    KAKAO = "kakao"


#: OAuth 로 붙는 종류. 카톡은 Mac 앱의 기기 토큰으로 붙는다(§4.6).
OAUTH_KINDS = frozenset({IntegrationKind.MAIL, IntegrationKind.SLACK})


class IntegrationStatus(StrEnum):
    CONNECTED = "connected"  # 실시간
    BACKFILLING = "backfilling"  # 과거 채우는 중
    DISCONNECTED = "disconnected"  # 끊김(토큰 만료·권한 회수) — 「다시 연결」
    REMOVED = "removed"  # 소프트 딜리트(D-27) — 화면에서만 사라지고 되살릴 수 있다(D-46)


class RoomStatus(StrEnum):
    BACKFILLING = "backfilling"
    LIVE = "live"
    #: 카톡 수집기가 꺼졌을 때 **읽으며 파생**한다 — 저장하지 않는다(W-1).
    PAUSED = "paused"


#: 종류별로 받는 방 종류(§4.1). 슬랙 = 공개·비공개·DM·그룹 DM · 카톡 = 1:1·단체(오픈채팅 제외, D-18).
ROOM_TYPES: dict[IntegrationKind, frozenset[str]] = {
    IntegrationKind.SLACK: frozenset({"channel", "private", "dm", "group_dm"}),
    IntegrationKind.KAKAO: frozenset({"direct", "group"}),
}

#: 카톡 연동의 `account_key` — 회원당 하나(D-45 사람당 Mac 한 대).
KAKAO_ACCOUNT_KEY = "kakao"

#: OAuth `state` 수명 — 짧다(§4.2). 동의 화면에서 머무는 시간만큼.
OAUTH_STATE_TTL = timedelta(minutes=10)

#: 카톡 수집기 status 주기 30초 · **90초 무보고 = 앱 꺼짐**(W-3).
COLLECTOR_OFFLINE_AFTER = timedelta(seconds=90)


class ExternalChannelError(Exception):
    """외부 채널 모듈의 오류 바탕. HTTP 상태는 `entrypoints/http.py` 가 정한다."""


class IntegrationNotConfigured(ExternalChannelError):
    """그 연동의 OAuth client 가 이 서버에 없다 — 연동이 스스로 없다고 말한다(SPEC-008 §5 비밀값 위치)."""

    def __init__(self, kind: str) -> None:
        super().__init__(f"{kind} 연동이 이 서버에 설정되어 있지 않습니다")
        self.kind = kind


class IntegrationAlreadyConnected(ExternalChannelError):
    """슬랙은 워크스페이스 하나 — 이미 연결돼 있으면 409. 메일은 계정 여럿이라 이 오류가 없다(N-1)."""


class OAuthStateRejected(ExternalChannelError):
    """`state` 가 없거나·만료됐거나·이미 쓰였거나·다른 종류다 — 400(§4.2). 어느 쪽인지 말하지 않는다."""


class OAuthExchangeFailed(ExternalChannelError):
    """상류가 code 를 토큰으로 바꿔 주지 않았다. 사유는 로그에 남기되 토큰·code 는 싣지 않는다."""


class InvalidRoomSelection(ExternalChannelError):
    """고른 방 요청이 그 연동 종류에 맞지 않는다(빈 id · 모르는 방 종류) — 422."""


class IntegrationDisconnected(ExternalChannelError):
    """연동이 끊겨 상류를 부를 수 없다 — 「다시 연결」(409 · SPEC-008 §4.3 available-rooms)."""


class RoomAccessDenied(ExternalChannelError):
    """그 회원의 토큰으로는 그 방을 볼 수 없다 — 남의 DM·비공개 채널 id 를 고른 경우(BE-2·3 검수 F-1)."""


class UpstreamUnavailableError(ExternalChannelError):
    """상류(슬랙)가 지금 답하지 않는다 — 502(Case Matrix 「상류 429·5xx」)."""


class DeviceTokenRejected(ExternalChannelError):
    """기기 토큰이 없거나 철회됐다 — 401."""


@dataclass(frozen=True, slots=True)
class DeviceTokenOwner:
    """기기 토큰이 가리키는 회원. 수집기 라우트만 이것으로 회원을 찾는다(R3-F1)."""

    token_id: str
    member_id: str


def secret_digest(secret: str) -> str:
    """기기 토큰·OAuth `state` 의 보관 형태 — SHA-256 hex. 원문은 서버 어디에도 남지 않는다(R3-F1).

    둘 다 32바이트 난수라 추측할 사전이 없어 소금·반복이 필요 없다(비밀번호 해시와 다른 이유).
    """
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def settings_return_url(web_origin: str, kind: str, outcome: str) -> str:
    """콜백이 돌려보내는 곳 — 라우터 없는 SPA 라 **쿼리**로(N-2 · `App.tsx` 가 쿼리를 읽는다)."""
    return f"{web_origin}/?surface=settings&tab={kind}&connect={outcome}"


def kakao_attachment_aid(integration_id: str, chat_id: str, log_id: str | int, seq: int) -> str:
    """카톡 첨부 id — **앱과 서버가 같은 바이트 식**으로 만든다(SPEC-008 §4.6 · W3-2 · N-8).

    `SHA-256("{integration_id}:{chatId}:{logId}:{seq}")` 의 소문자 hex. 연동이 식에 들어가므로 동료 둘이 같은
    단체방의 같은 사진을 올려도 aid 가 갈린다(D-25).
    """
    return hashlib.sha256(f"{integration_id}:{chat_id}:{log_id}:{seq}".encode("utf-8")).hexdigest()


def collector_is_offline(reported_at: datetime | None, now: datetime) -> bool:
    """마지막 보고가 없거나 90초 넘게 지났으면 앱 꺼짐(W-3)."""
    return reported_at is None or now - reported_at > COLLECTOR_OFFLINE_AFTER


def effective_room_status(
    stored: str,
    *,
    kind: str,
    collector_status: dict | None,
    collector_reported_at: datetime | None,
    now: datetime,
) -> str:
    """방이 화면에 보이는 상태. 카톡은 앱 꺼짐·카톡 꺼짐이면 `paused` 로 파생한다(W-1 — `app:off`/`kakao:off`).

    카톡 «읽기 불가»(`unreadable`)도 수집이 멈춘 것이라 같은 파생을 따른다. 슬랙·메일 방은 저장값 그대로다.
    """
    if kind != IntegrationKind.KAKAO:
        return stored
    if collector_is_offline(collector_reported_at, now):
        return RoomStatus.PAUSED
    kakao_state = ((collector_status or {}).get("kakao") or {}).get("state")
    if kakao_state != "running" or (collector_status or {}).get("account_changed"):
        return RoomStatus.PAUSED
    return stored


def next_selected_rooms_version(current: int, *, changed: bool) -> int:
    """고른 방 버전 생성 규칙 — 그 연동의 고른 방 «집합»이 실제로 바뀐 때만 1 올린다(W3-3).

    바뀐다 = 방이 새로 붙거나(되살림 포함) 빠지거나, reset-account 가 옛 계정 방을 걷을 때. 이미 고른 방을 다시
    고르는 요청은 바꾸지 않는다 — 수집기가 handshake 를 괜히 다시 부르지 않게. 단조 증가라 비교는 `!=` 로 족하다.
    """
    return current + 1 if changed else current
