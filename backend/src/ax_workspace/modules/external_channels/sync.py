"""연동 수집 — **BE-2 소유** (WORK-011 Phase BE-2 · SPEC-008 §5 동기화).

연동 전용 워커(`bootstrap/external_worker.py`, 레플리카 1)가 이 서비스를 돈다. 하는 일:

- **슬랙 실시간**: Socket Mode 이벤트 1건 → 그 `(team, channel)` 을 고른 **모든** 연동에 한 벌씩 저장(사람별 팬아웃
  W-8 · 키 `(연동, channel, ts)`). 수정(`message_changed`)은 원문을 갈고, 삭제(`message_deleted`)는 원문에
  `ax_deleted` 표지를 단다 — 지우지 않는다(D-49 대체 · 계속 보관).
- **슬랙 최초 백필**: 고른 방마다 `conversations.history` 를 API 가 허용하는 끝까지(D-23) · 답글 달린 글은
  `conversations.replies` 로 스레드까지. 진행은 방의 `backfill_cursor`·`backfill_count`, 끝나면 `live`.
- **Gmail**: 연결 직후 `historyId` 를 먼저 잡고(그 뒤 도착분은 history 가 받는다) 받은편지함 전체를 백필한다.
  `users.watch` 를 매일 갱신하고, Pub/Sub pull 알림 또는 주기 폴링으로 `history.list` 증분을 받는다.
- **재시작 메우기**(D-22): 워커가 뜰 때 슬랙 방은 `last_message_key` 이후, Gmail 은 `sync_cursor` 이후를 훑는다.
- **끊김**: 상류가 토큰을 거절하면(만료·권한 회수) 연동을 `disconnected` 로 — 수집이 멈추고 배너가 선다(D-50).

새 메시지는 저장소가 **같은 트랜잭션에서** `ax_user_events` 로 알린다(`events.py` 계약 · BE-3 WS 가 듣는다).
이 파일은 `fastapi`·`sqlalchemy` 를 모른다 — 저장소·상류 클라이언트는 포트로 받는다.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
from typing import Any, Literal, Protocol

from ax_workspace.modules.external_channels.domain import IntegrationKind
from ax_workspace.modules.external_channels.sync_messages import (
    SLACK_EDIT_SUBTYPES,
    NormalizedMessage,
    normalize_gmail_message,
    normalize_slack_message,
)

logger = logging.getLogger(__name__)

#: watch 는 7일 만료 — **매일** 갱신한다(D-22). 만료까지 6일보다 적게 남았으면 다시 건다.
GMAIL_WATCH_RENEW_BEFORE = timedelta(days=6)
#: Pub/Sub 알림이 없어도 이 간격으로 history 를 훑는다 — 알림을 놓쳐도 늦게라도 받는다.
GMAIL_POLL_INTERVAL = timedelta(seconds=60)
#: 한 번에 가져오는 쪽수. 한 연동이 워커를 오래 붙잡지 않게 한 걸음씩 돌아가며 판다.
SLACK_PAGE_LIMIT = 200
GMAIL_PAGE_LIMIT = 100
#: 액세스 토큰은 만료 이만큼 전에 미리 갱신한다.
TOKEN_REFRESH_MARGIN = timedelta(seconds=90)


# ── 상류 오류 ──────────────────────────────────────────────────────────────────────────


class UpstreamAuthRevoked(Exception):
    """토큰이 더는 통하지 않는다 — 만료·권한 회수·계정 비활성. 연동을 끊김으로 바꾼다."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class UpstreamRateLimited(Exception):
    def __init__(self, retry_after: float) -> None:
        super().__init__(f"rate limited for {retry_after}s")
        self.retry_after = retry_after


class UpstreamUnavailable(Exception):
    """상류가 잠깐 안 된다(5xx·네트워크) 또는 그 방만 못 읽는다. 다음 차례에 다시."""


class HistoryExpired(Exception):
    """Gmail `startHistoryId` 가 너무 오래됐다(404) — 날짜로 다시 훑는다."""


# ── 포트 ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class IntegrationState:
    id: str
    member_id: str
    kind: str
    status: str
    account_key: str
    access_token_encrypted: str | None
    refresh_token_encrypted: str | None
    token_expires_at: datetime | None
    sync_cursor: str | None
    backfill_cursor: str | None
    backfill_done_at: datetime | None
    watch_expires_at: datetime | None
    last_synced_at: datetime | None


@dataclass(frozen=True, slots=True)
class RoomState:
    id: str
    integration_id: str
    member_id: str
    external_id: str
    room_type: str
    name: str
    last_message_key: str | None
    backfill_cursor: str | None
    backfill_count: int
    backfill_done_at: datetime | None
    access_token_encrypted: str | None


SaveMode = Literal["backfill", "live"]


class SyncStore(Protocol):
    def active_integrations(self, kind: str) -> list[IntegrationState]: ...
    def integration(self, integration_id: str) -> IntegrationState | None: ...
    def mail_integrations_for_address(self, address: str) -> list[IntegrationState]: ...
    def slack_rooms(self) -> list[RoomState]: ...
    def slack_fanout_targets(self, team_id: str, channel: str) -> list[RoomState]: ...
    def save_messages(
        self, integration_id: str, source_kind: str, room_id: str | None, messages: list[NormalizedMessage], *, mode: SaveMode
    ) -> int: ...
    def replace_raw(self, integration_id: str, container_key: str, external_key: str, raw: dict[str, Any]) -> bool: ...
    def update_integration(self, integration_id: str, **fields: Any) -> None: ...
    def update_room(self, room_id: str, **fields: Any) -> None: ...
    def mark_disconnected(self, integration_id: str, reason: str) -> None: ...


class SlackApi(Protocol):
    def history(
        self, token: str, channel: str, *, cursor: str | None = None, oldest: str | None = None, limit: int = SLACK_PAGE_LIMIT
    ) -> tuple[list[dict[str, Any]], str | None]: ...
    def replies(self, token: str, channel: str, ts: str, *, cursor: str | None = None) -> tuple[list[dict[str, Any]], str | None]: ...
    def conversation_info(self, token: str, channel: str) -> dict[str, Any]: ...
    def conversation_members(self, token: str, channel: str) -> list[str]: ...
    def user_name(self, token: str, user_id: str) -> str | None: ...


class GmailApi(Protocol):
    def refresh(self, refresh_token: str) -> tuple[str, datetime | None]: ...
    def profile(self, token: str) -> dict[str, Any]: ...
    def watch(self, token: str, topic: str) -> tuple[str, datetime]: ...
    def list_inbox(self, token: str, *, page_token: str | None = None, query: str | None = None) -> tuple[list[str], str | None]: ...
    def get_message(self, token: str, message_id: str) -> dict[str, Any] | None: ...
    def history(self, token: str, start_history_id: str, *, page_token: str | None = None) -> tuple[list[str], str | None, str]: ...


class TokenCipher(Protocol):
    def encrypt(self, plaintext: str) -> str: ...
    def decrypt(self, ciphertext: str) -> str: ...


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class ExternalSync:
    def __init__(
        self,
        store: SyncStore,
        *,
        cipher: TokenCipher,
        slack: SlackApi | None,
        gmail: GmailApi | None,
        gmail_topic: str | None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._cipher = cipher
        self._slack = slack
        self._gmail = gmail
        self._gmail_topic = gmail_topic or None
        self._clock = clock
        #: 연동·방마다 「이때까지는 건드리지 않는다」(429 Retry-After · 일시 장애 물러서기).
        self._not_before: dict[str, datetime] = {}
        self._last_mail_poll: dict[str, datetime] = {}
        #: 재시작 메우기를 아직 안 한 슬랙 방(시작할 때 · Socket Mode 재연결 뒤 다시 채운다).
        self._gap_fill_pending: set[str] | None = None

    # ── 슬랙 실시간 — 팬아웃 ──────────────────────────────────────────────────────────

    def handle_slack_event(self, payload: dict[str, Any]) -> int:
        """Socket Mode `events_api` 봉투의 `payload` 하나. 저장한 행 수를 돌려준다(팬아웃 합)."""
        event = payload.get("event") or {}
        if event.get("type") != "message" or not event.get("channel"):
            return 0
        team_id = str(payload.get("team_id") or event.get("team") or "")
        channel = str(event["channel"])
        targets = self._store.slack_fanout_targets(team_id, channel)
        if not targets:
            return 0
        subtype = event.get("subtype")
        saved = 0
        for room in targets:
            if subtype == "message_changed" and isinstance(event.get("message"), dict):
                message = {**event["message"]}
                if not self._store.replace_raw(room.integration_id, channel, str(message.get("ts")), message):
                    saved += self._store.save_messages(
                        room.integration_id, IntegrationKind.SLACK, room.id,
                        [normalize_slack_message(room.integration_id, channel, message)], mode="live",
                    )
            elif subtype == "message_deleted" and event.get("deleted_ts"):
                previous = event.get("previous_message") if isinstance(event.get("previous_message"), dict) else {}
                self._store.replace_raw(
                    room.integration_id, channel, str(event["deleted_ts"]),
                    {**previous, "ts": str(event["deleted_ts"]), "ax_deleted": True, "ax_deleted_event_ts": event.get("event_ts")},
                )
            elif subtype in SLACK_EDIT_SUBTYPES or not event.get("ts"):
                continue
            else:
                message = {key: value for key, value in event.items() if key not in {"channel_type", "event_ts"}}
                saved += self._store.save_messages(
                    room.integration_id, IntegrationKind.SLACK, room.id,
                    [normalize_slack_message(room.integration_id, channel, message)], mode="live",
                )
        return saved

    # ── 주기 한 바퀴 ─────────────────────────────────────────────────────────────────

    def request_gap_fill(self) -> None:
        """다음 바퀴에 live 방 전부를 마지막 반영 지점 이후로 훑는다(시작·Socket Mode 재연결 뒤)."""
        self._gap_fill_pending = None

    def tick(self, *, due_mail: Iterable[str] = ()) -> bool:
        """한 바퀴. 할 일이 남았으면 True — 워커가 쉬지 않고 다음 바퀴를 돈다."""
        busy = False
        if self._slack is not None:
            busy |= self._slack_round()
        if self._gmail is not None:
            busy |= self._mail_round(set(due_mail))
        return busy

    # ── 슬랙 ────────────────────────────────────────────────────────────────────────

    def _slack_round(self) -> bool:
        busy = False
        for integration in self._store.active_integrations(IntegrationKind.SLACK):
            if integration.status == "backfilling":
                # 슬랙의 백필은 방 단위다 — 연동은 붙자마자 실시간이다.
                self._store.update_integration(integration.id, status="connected", backfill_done_at=self._clock())
        rooms = self._store.slack_rooms()
        if self._gap_fill_pending is None:
            self._gap_fill_pending = {room.id for room in rooms if room.backfill_done_at is not None}
        for room in rooms:
            if not self._ready(room.id) or not self._ready(room.integration_id) or not room.access_token_encrypted:
                continue
            try:
                token = self._cipher.decrypt(room.access_token_encrypted)
                if room.backfill_done_at is None:
                    busy |= self._slack_backfill_page(token, room)
                elif room.id in self._gap_fill_pending:
                    self._slack_gap_fill(token, room)
                    self._gap_fill_pending.discard(room.id)
            except UpstreamAuthRevoked as error:
                logger.warning("slack integration %s lost its token: %s", room.integration_id, error.reason)
                self._store.mark_disconnected(room.integration_id, error.reason)
            except UpstreamRateLimited as error:
                self._back_off(room.integration_id, error.retry_after)
            except UpstreamUnavailable as error:
                logger.warning("slack room %s sync deferred: %s", room.id, error)
                self._back_off(room.id, 30)
        return busy

    def _slack_backfill_page(self, token: str, room: RoomState) -> bool:
        assert self._slack is not None
        if room.backfill_cursor is None and room.backfill_count == 0:
            self._refresh_slack_room(token, room)
        messages, next_cursor = self._slack.history(token, room.external_id, cursor=room.backfill_cursor)
        batch = self._with_threads(token, room, messages)
        self._store.save_messages(room.integration_id, IntegrationKind.SLACK, room.id, batch, mode="backfill")
        if next_cursor:
            self._store.update_room(room.id, backfill_cursor=next_cursor)
            return True
        self._store.update_room(room.id, backfill_cursor=None, backfill_done_at=self._clock(), status="live")
        return False

    def _slack_gap_fill(self, token: str, room: RoomState) -> None:
        assert self._slack is not None
        cursor: str | None = None
        while True:
            messages, cursor = self._slack.history(token, room.external_id, cursor=cursor, oldest=room.last_message_key)
            if messages:
                self._store.save_messages(
                    room.integration_id, IntegrationKind.SLACK, room.id, self._with_threads(token, room, messages), mode="live"
                )
            if not cursor:
                return

    def _with_threads(self, token: str, room: RoomState, messages: list[dict[str, Any]]) -> list[NormalizedMessage]:
        assert self._slack is not None
        batch: list[NormalizedMessage] = []
        for message in messages:
            if not message.get("ts") or message.get("subtype") in SLACK_EDIT_SUBTYPES:
                continue
            batch.append(normalize_slack_message(room.integration_id, room.external_id, message))
            if message.get("reply_count") and message.get("thread_ts") == message.get("ts"):
                cursor: str | None = None
                while True:
                    replies, cursor = self._slack.replies(token, room.external_id, str(message["ts"]), cursor=cursor)
                    batch.extend(
                        normalize_slack_message(room.integration_id, room.external_id, reply)
                        for reply in replies
                        if reply.get("ts") and reply.get("ts") != message.get("ts")
                    )
                    if not cursor:
                        break
        return batch

    def _refresh_slack_room(self, token: str, room: RoomState) -> None:
        """고른 방의 진짜 종류·이름. 그룹 DM 은 `mpdm-…` 대신 **참여자 실명**(D-12), DM 은 상대 이름."""
        assert self._slack is not None
        try:
            info = self._slack.conversation_info(token, room.external_id)
        except UpstreamUnavailable:
            return
        room_type = (
            "dm" if info.get("is_im")
            else "group_dm" if info.get("is_mpim")
            else "private" if info.get("is_private")
            else "channel"
        )
        name = str(info.get("name") or room.name)
        meta: dict[str, Any] = {}
        if room_type == "dm" and info.get("user"):
            name = self._slack.user_name(token, str(info["user"])) or name
            meta["user_id"] = info["user"]
        elif room_type == "group_dm":
            names = [self._slack.user_name(token, member) for member in self._slack.conversation_members(token, room.external_id)]
            name = ", ".join(sorted(filter(None, names))) or name
        fields: dict[str, Any] = {"room_type": room_type, "name": name[:300]}
        if isinstance(info.get("num_members"), int):
            fields["member_count"] = info["num_members"]
        if meta:
            fields["room_meta"] = meta
        self._store.update_room(room.id, **fields)

    # ── Gmail ───────────────────────────────────────────────────────────────────────

    def mail_due(self, address: str) -> list[str]:
        """Pub/Sub 알림의 주소 → 그 주소를 연결한 연동들(사람마다 따로)."""
        return [row.id for row in self._store.mail_integrations_for_address(address.strip().lower())]

    def _mail_round(self, due: set[str]) -> bool:
        busy = False
        for integration in self._store.active_integrations(IntegrationKind.MAIL):
            if not self._ready(integration.id):
                continue
            try:
                token = self._gmail_token(integration)
                busy |= self._mail_step(token, integration, polled=integration.id in due)
            except UpstreamAuthRevoked as error:
                logger.warning("gmail integration %s lost its token: %s", integration.id, error.reason)
                self._store.mark_disconnected(integration.id, error.reason)
            except UpstreamRateLimited as error:
                self._back_off(integration.id, error.retry_after)
            except UpstreamUnavailable as error:
                logger.warning("gmail integration %s sync deferred: %s", integration.id, error)
                self._back_off(integration.id, 30)
        return busy

    def _mail_step(self, token: str, integration: IntegrationState, *, polled: bool) -> bool:
        assert self._gmail is not None
        now = self._clock()
        cursor = integration.sync_cursor
        if cursor is None:
            # 백필보다 **먼저** 지금의 historyId 를 잡는다 — 백필 중에 온 메일은 history 가 받고 중복 키가 버린다.
            cursor = str(self._gmail.profile(token).get("historyId") or "") or None
            self._store.update_integration(integration.id, sync_cursor=cursor)
        watch = _aware(integration.watch_expires_at)
        if self._gmail_topic and (watch is None or watch - now < GMAIL_WATCH_RENEW_BEFORE):
            _, expires_at = self._gmail.watch(token, self._gmail_topic)
            self._store.update_integration(integration.id, watch_expires_at=expires_at)
        busy = False
        if integration.backfill_done_at is None:
            busy = self._mail_backfill_page(token, integration)
        last = self._last_mail_poll.get(integration.id)
        if cursor and (polled or last is None or now - last >= GMAIL_POLL_INTERVAL):
            self._last_mail_poll[integration.id] = now
            self._mail_history(token, integration, cursor)
        return busy

    def _mail_backfill_page(self, token: str, integration: IntegrationState) -> bool:
        assert self._gmail is not None
        ids, next_page = self._gmail.list_inbox(token, page_token=integration.backfill_cursor)
        self._store.save_messages(integration.id, IntegrationKind.MAIL, None, self._fetch_mail(token, integration, ids), mode="backfill")
        if next_page:
            self._store.update_integration(integration.id, backfill_cursor=next_page)
            return True
        self._store.update_integration(integration.id, backfill_cursor=None, backfill_done_at=self._clock(), status="connected")
        return False

    def _mail_history(self, token: str, integration: IntegrationState, cursor: str) -> None:
        assert self._gmail is not None
        page: str | None = None
        latest = cursor
        try:
            while True:
                ids, page, latest = self._gmail.history(token, cursor, page_token=page)
                if ids:
                    self._store.save_messages(integration.id, IntegrationKind.MAIL, None, self._fetch_mail(token, integration, ids), mode="live")
                if not page:
                    break
        except HistoryExpired:
            # 시작점이 너무 오래됐다 — 마지막 수집 하루 전부터 날짜로 다시 훑는다(중복 키가 겹친 것을 버린다).
            since = _aware(integration.last_synced_at) or (self._clock() - timedelta(days=7))
            query = f"after:{int((since - timedelta(days=1)).timestamp())}"
            page = None
            while True:
                ids, page = self._gmail.list_inbox(token, page_token=page, query=query)
                if ids:
                    self._store.save_messages(integration.id, IntegrationKind.MAIL, None, self._fetch_mail(token, integration, ids), mode="live")
                if not page:
                    break
            latest = str(self._gmail.profile(token).get("historyId") or cursor)
        if latest and latest != cursor:
            self._store.update_integration(integration.id, sync_cursor=latest)

    def _fetch_mail(self, token: str, integration: IntegrationState, ids: list[str]) -> list[NormalizedMessage]:
        assert self._gmail is not None
        batch = []
        for message_id in dict.fromkeys(ids):
            message = self._gmail.get_message(token, message_id)
            # 사이에 지워졌거나 받은편지함을 떠난 메일은 건너뛴다 — 받은편지함만 받는다(D-10).
            if message is None or "INBOX" not in (message.get("labelIds") or []):
                continue
            batch.append(normalize_gmail_message(integration.id, message))
        return batch

    def _gmail_token(self, integration: IntegrationState) -> str:
        assert self._gmail is not None
        expires = _aware(integration.token_expires_at)
        if integration.access_token_encrypted and (expires is None or expires - self._clock() > TOKEN_REFRESH_MARGIN):
            return self._cipher.decrypt(integration.access_token_encrypted)
        if not integration.refresh_token_encrypted:
            raise UpstreamAuthRevoked("token_expired")
        access, expires_at = self._gmail.refresh(self._cipher.decrypt(integration.refresh_token_encrypted))
        self._store.update_integration(
            integration.id, access_token_encrypted=self._cipher.encrypt(access), token_expires_at=expires_at
        )
        return access

    # ── 물러서기 ─────────────────────────────────────────────────────────────────────

    def _ready(self, key: str) -> bool:
        until = self._not_before.get(key)
        return until is None or until <= self._clock()

    def _back_off(self, key: str, seconds: float) -> None:
        self._not_before[key] = self._clock() + timedelta(seconds=max(1.0, seconds))
