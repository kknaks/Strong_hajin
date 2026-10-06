"""슬랙 상류 어댑터 — 사용자 토큰 Web API 와 앱 토큰 Socket Mode (WORK-011 BE-2 · SPEC-008 §5 동기화).

- Web API 는 **연결한 사람의 사용자 토큰**으로 부른다 — 그 사람이 볼 수 있는 방만 읽는다(D-11).
- Socket Mode 는 **앱 토큰 1개로 이 워커 한 곳에서만** 연다(OQ-802 · 단일 소유). 여러 프로세스가 같은 앱 토큰으로
  붙으면 슬랙이 이벤트를 나눠 보내 한쪽만 받는다 — 레플리카 1 이 계약이다.

토큰은 헤더에만 싣고 로그·예외에는 상류 오류 코드만 남긴다.
"""
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import logging
import re
import threading
import time
from typing import Any
from urllib import error as urlerror, parse as urlparse, request as urlrequest

from ax_workspace.modules.external_channels.application import SlackRoomInfo
from ax_workspace.modules.external_channels.domain import (
    IntegrationDisconnected,
    RoomAccessDenied,
    UpstreamUnavailableError,
)
from ax_workspace.modules.external_channels.sync import (
    UpstreamAuthRevoked,
    UpstreamRateLimited,
    UpstreamRoomDenied,
    UpstreamUnavailable,
)

logger = logging.getLogger(__name__)

SLACK_API = "https://slack.com/api/"
#: 토큰이 더는 통하지 않는다는 뜻의 슬랙 오류 — 연동을 끊김으로(D-50).
AUTH_ERRORS = frozenset({
    "invalid_auth", "not_authed", "token_revoked", "token_expired", "account_inactive",
    "team_access_not_granted", "org_login_required", "ekm_access_denied",
})
#: 이 토큰으로 그 방을 볼 수 없다는 뜻 — 연동 전체가 아니라 그 방만 멈춘다(F-1).
ROOM_DENIED_ERRORS = frozenset({"channel_not_found", "not_in_channel", "access_denied", "is_archived", "user_not_found"})
DEFAULT_TIMEOUT = 30.0


def call(method: str, token: str, params: dict[str, Any] | None = None, *, post: bool = False, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    fields = {key: str(value) for key, value in (params or {}).items() if value is not None}
    url = SLACK_API + method
    headers = {"Authorization": f"Bearer {token}"}
    if post:
        request = urlrequest.Request(url, data=urlparse.urlencode(fields).encode(), method="POST",
                                     headers={**headers, "Content-Type": "application/x-www-form-urlencoded"})
    else:
        request = urlrequest.Request(f"{url}?{urlparse.urlencode(fields)}" if fields else url, method="GET", headers=headers)
    try:
        with urlrequest.urlopen(request, timeout=timeout) as response:  # noqa: S310 — 고정된 https 상류
            body = json.loads(response.read().decode("utf-8"))
            scopes = response.headers.get("x-oauth-scopes")
    except urlerror.HTTPError as error:
        if error.code == 429:
            raise UpstreamRateLimited(float(error.headers.get("Retry-After") or 30)) from None
        raise UpstreamUnavailable(f"slack {method} http {error.code}") from None
    except (urlerror.URLError, TimeoutError, OSError, ValueError) as error:
        raise UpstreamUnavailable(f"slack {method} unreachable ({type(error).__name__})") from None
    if not body.get("ok"):
        code = str(body.get("error") or "unknown")
        if code in AUTH_ERRORS:
            raise UpstreamAuthRevoked(code)
        if code in ROOM_DENIED_ERRORS:
            raise UpstreamRoomDenied(code)
        if code == "ratelimited":
            raise UpstreamRateLimited(30)
        raise UpstreamUnavailable(f"slack {method} refused ({code})")
    if scopes is not None:
        body.setdefault("_scopes", scopes)
    return body


USER_CACHE_TTL_SECONDS = 600
ROOM_LIST_CACHE_TTL_SECONDS = 90
#: 속도 제한이면 이만큼까지는 기다렸다 한 번 더 — 그보다 길면 묵은 캐시를 쓰거나 실패로(Retry-After 존중).
RATE_LIMIT_WAIT_LIMIT_SECONDS = 3.0
#: 방 목록 한 쪽을 풀 때 나란히 부르는 상한 — 슬랙 속도 제한을 넘지 않을 만큼만.
LIST_WORKERS = 6
_MPDM_NAME = re.compile(r"^mpdm-(.+)-\d+$")


@dataclass(frozen=True, slots=True)
class SlackUser:
    name: str | None
    handle: str | None
    is_bot: bool
    avatar: str | None


def _user_from(payload: dict[str, Any]) -> SlackUser:
    profile = payload.get("profile") or {}
    return SlackUser(
        name=profile.get("display_name") or profile.get("real_name") or payload.get("real_name") or payload.get("name"),
        handle=payload.get("name"),
        is_bot=bool(payload.get("is_bot") or payload.get("is_app_user") or payload.get("id") == "USLACKBOT"),
        avatar=profile.get("image_72") or profile.get("image_48"),
    )


def _call_respecting_retry(method: str, token: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """속도 제한이면 Retry-After 가 짧을 때만 기다렸다 한 번 더 부른다. 길면 그대로 올린다(부르는 쪽이 판단)."""
    try:
        return call(method, token, params)
    except UpstreamRateLimited as limited:
        if limited.retry_after > RATE_LIMIT_WAIT_LIMIT_SECONDS:
            raise
        time.sleep(limited.retry_after)
        return call(method, token, params)


class SlackUserCache:
    """워크스페이스 사용자 이름표 — **`users.list` 몇 쪽으로 한 번에** 받아 10분 기억한다(BE 수정 판 6).

    방 목록(DM 상대·그룹 DM 참여자)과 연동 워커(방 이름·보낸 사람 이름표)가 **같은 캐시**를 쓴다. 예전에는 사람마다
    `users.info` 를 하나씩 불러 첫 쪽이 20초였다. 키는 토큰의 지문이다(그 토큰이 보는 워크스페이스 · 토큰은 보관 안 함).
    캐시에 없는 id(다른 워크스페이스 사람 · 방금 들어온 사람)만 `users.info` 로 하나씩 메운다.
    """

    def __init__(self, *, ttl: float = USER_CACHE_TTL_SECONDS) -> None:
        self._ttl = ttl
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, dict[str, SlackUser], dict[str, str]]] = {}
        self._missing: dict[str, set[str]] = {}

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]

    def _load(self, token: str) -> tuple[dict[str, SlackUser], dict[str, str]]:
        users: dict[str, SlackUser] = {}
        cursor = None
        while True:
            body = _call_respecting_retry("users.list", token, {"limit": 200, "cursor": cursor})
            for member in body.get("members") or []:
                if member.get("id"):
                    users[str(member["id"])] = _user_from(member)
            cursor = (body.get("response_metadata") or {}).get("next_cursor") or None
            if not cursor:
                break
        handles = {user.handle: user_id for user_id, user in users.items() if user.handle}
        return users, handles

    def directory(self, token: str) -> tuple[dict[str, SlackUser], dict[str, str]]:
        key = self._key(token)
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and now - entry[0] < self._ttl:
                return entry[1], entry[2]
        try:
            users, handles = self._load(token)
        except UpstreamRateLimited:
            if entry is not None:  # 속도 제한이면 묵은 이름표라도 쓴다
                return entry[1], entry[2]
            raise
        with self._lock:
            self._entries[key] = (time.monotonic(), users, handles)
            self._missing.pop(key, None)
        return users, handles

    def user(self, token: str, user_id: str) -> SlackUser | None:
        users, _ = self.directory(token)
        found = users.get(user_id)
        if found is not None:
            return found
        if user_id in self._missing.get(self._key(token), set()):
            return None
        try:
            payload = call("users.info", token, {"user": user_id}).get("user") or {}
        except UpstreamRoomDenied:  # user_not_found — 다음에 다시 묻지 않는다
            with self._lock:
                self._missing.setdefault(self._key(token), set()).add(user_id)
            return None
        found = _user_from(payload)
        with self._lock:
            entry = self._entries.get(self._key(token))
            if entry is not None:
                entry[1][user_id] = found
                if found.handle:
                    entry[2][found.handle] = user_id
        return found

    def by_handle(self, token: str, handle: str) -> SlackUser | None:
        users, handles = self.directory(token)
        user_id = handles.get(handle)
        return users.get(user_id) if user_id else None

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


#: 프로세스 하나에 하나 — API 의 방 목록과 연동 워커가 같은 것을 쓴다.
SLACK_USERS = SlackUserCache()


class SlackWebApi:
    """`modules/external_channels/sync.SlackApi` 의 실물."""

    def __init__(self, users: SlackUserCache | None = None) -> None:
        self._users = users or SLACK_USERS

    def history(self, token, channel, *, cursor=None, oldest=None, limit=200):
        body = call("conversations.history", token, {"channel": channel, "cursor": cursor, "oldest": oldest, "limit": limit})
        return list(body.get("messages") or []), ((body.get("response_metadata") or {}).get("next_cursor") or None)

    def replies(self, token, channel, ts, *, cursor=None, oldest=None):
        body = call("conversations.replies", token, {"channel": channel, "ts": ts, "cursor": cursor, "oldest": oldest, "limit": 200})
        return list(body.get("messages") or []), ((body.get("response_metadata") or {}).get("next_cursor") or None)

    def conversation_info(self, token, channel):
        return dict(call("conversations.info", token, {"channel": channel, "include_num_members": "true"}).get("channel") or {})

    def conversation_members(self, token, channel):
        members: list[str] = []
        cursor = None
        while True:
            body = call("conversations.members", token, {"channel": channel, "cursor": cursor, "limit": 200})
            members.extend(body.get("members") or [])
            cursor = (body.get("response_metadata") or {}).get("next_cursor") or None
            if not cursor:
                return members

    def user_name(self, token, user_id):
        try:
            user = self._users.user(token, user_id)
        except (UpstreamUnavailable, UpstreamRateLimited):
            return None
        return user.name if user else None

    def user_tag(self, token, user_id):
        """이름표 하나 — 이름·봇 여부·작은 아바타. 워크스페이스 이름표 캐시에서 꺼낸다(BE 수정 판 6)."""
        user = self._users.user(token, user_id)
        return {"name": user.name, "is_bot": user.is_bot, "avatar": user.avatar} if user else None

    @staticmethod
    def identity(token: str) -> dict[str, Any]:
        """`auth.test` — 개발 토큰 주입이 team·user 를 알아낸다. `_scopes` 에 허용 권한이 실린다."""
        return call("auth.test", token, post=True)


def room_type(info: dict[str, Any]) -> str:
    if info.get("is_im"):
        return "dm"
    if info.get("is_mpim"):
        return "group_dm"
    if info.get("is_private") or info.get("is_group"):
        return "private"
    return "channel"


def group_dm_handles(name: str) -> list[str] | None:
    """`mpdm-kim--lee--park-1` → `["kim", "lee", "park"]`. 형식이 다르면 None(참여자 목록을 따로 묻는다)."""
    match = _MPDM_NAME.match(name or "")
    if not match:
        return None
    handles = [handle for handle in match.group(1).split("--") if handle]
    return handles or None


class SlackRoomDirectory:
    """방 고르기 목록·접근 확인(F-1·F-2 · `application.SlackRoomDirectory` 의 실물).

    빨라야 한다(BE 수정 판 6 — 첫 쪽 20초 → 2초 목표): 이름은 워크스페이스 이름표 캐시 한 번(`users.list`), 그룹 DM 은
    `mpdm-…` 이름의 사용자명으로 풀고(참여자 호출 없음), 목록 쪽은 회원 토큰·커서마다 90초 기억한다(「방 추가」 창을
    다시 열면 즉시).
    """

    def __init__(self, users: SlackUserCache | None = None, *, list_ttl: float = ROOM_LIST_CACHE_TTL_SECONDS) -> None:
        self._users = users or SLACK_USERS
        self._list_ttl = list_ttl
        self._lock = threading.Lock()
        self._pages: dict[tuple[str, str | None], tuple[float, list[SlackRoomInfo], str | None]] = {}

    def _info(self, token: str, channel: dict[str, Any]) -> SlackRoomInfo:
        kind = room_type(channel)
        name = str(channel.get("name") or channel["id"])
        is_bot = False
        member_count = channel.get("num_members") if isinstance(channel.get("num_members"), int) else None
        if kind == "dm" and channel.get("user"):
            user = self._users.user(token, str(channel["user"]))
            if user is not None:
                name, is_bot = user.name or name, user.is_bot
            member_count = 2
        elif kind == "group_dm":
            handles = group_dm_handles(name)
            people = [self._users.by_handle(token, handle) for handle in handles] if handles else []
            if not handles or any(person is None for person in people):
                # 형식이 다르거나 모르는 사람이 있다 — 그때만 참여자를 묻는다.
                members = call("conversations.members", token, {"channel": channel["id"], "limit": 100}).get("members") or []
                people = [self._users.user(token, member) for member in members]
            names = [person.name for person in people if person is not None and person.name]
            name = ", ".join(sorted(names)) or name  # `mpdm-…` 대신 참여자 실명(D-12)
            member_count = len(people)
        return SlackRoomInfo(room_id=str(channel["id"]), type=kind, name=name[:300], is_bot=is_bot, member_count=member_count)

    def _translate(self, work):
        try:
            return work()
        except UpstreamAuthRevoked as error:
            raise IntegrationDisconnected(f"슬랙이 토큰을 거절했습니다({error.reason})") from None
        except UpstreamRoomDenied as error:
            raise RoomAccessDenied(error.code) from None
        except (UpstreamUnavailable, UpstreamRateLimited) as error:
            raise UpstreamUnavailableError(str(error)) from None

    def describe(self, token: str, channel: str) -> SlackRoomInfo:
        def work() -> SlackRoomInfo:
            info = call("conversations.info", token, {"channel": channel, "include_num_members": "true"}).get("channel") or {}
            # DM·그룹 DM·비공개는 참여자가 아니면 슬랙이 channel_not_found 로 답한다. 공개 채널은 누구나 info 를 받으므로
            # **참여(is_member)까지** 본다 — 사용자 토큰 이벤트는 참여한 방에서만 온다.
            if room_type(info) == "channel" and not info.get("is_member"):
                raise UpstreamRoomDenied("not_in_channel")
            if info.get("is_archived"):
                raise UpstreamRoomDenied("is_archived")
            return self._info(token, info)

        return self._translate(work)

    def list_page(self, token: str, cursor: str | None) -> tuple[list[SlackRoomInfo], str | None]:
        key = (SlackUserCache._key(token), cursor)
        now = time.monotonic()
        with self._lock:
            cached = self._pages.get(key)
            if cached is not None and now - cached[0] < self._list_ttl:
                return list(cached[1]), cached[2]

        def work() -> tuple[list[SlackRoomInfo], str | None]:
            # 방 목록과 이름표를 **동시에** 받는다. `users.conversations` = 그 회원이 참여한 방만(공개·비공개·DM·그룹 DM).
            with ThreadPoolExecutor(max_workers=LIST_WORKERS) as pool:
                warm = pool.submit(self._users.directory, token)
                body = _call_respecting_retry("users.conversations", token, {
                    "types": "public_channel,private_channel,im,mpim", "exclude_archived": "true", "limit": 100, "cursor": cursor,
                })
                warm.result()
                channels = [channel for channel in body.get("channels") or [] if channel.get("id")]
                # 대부분 캐시에서 풀린다. 형식이 다른 그룹 DM·이름표에 없는 사람만 따로 묻고, 그것도 나란히.
                rooms = list(pool.map(lambda channel: self._info(token, channel), channels))
            return rooms, ((body.get("response_metadata") or {}).get("next_cursor") or None)

        rooms, next_cursor = self._translate(work)
        with self._lock:
            self._pages[key] = (time.monotonic(), rooms, next_cursor)
        return list(rooms), next_cursor


class SlackSocketMode:
    """Socket Mode 연결 하나 — 받은 봉투마다 **먼저 ack** 하고 `on_event(payload)` 를 부른다.

    끊기면(슬랙의 `disconnect` 안내·네트워크) 새 주소를 받아 다시 붙고, 다시 붙을 때마다 `on_reconnect()` 를 불러
    끊긴 사이를 메우게 한다(D-22).
    """

    def __init__(self, app_token: str, *, on_event: Callable[[dict[str, Any]], None], on_reconnect: Callable[[], None]) -> None:
        self._app_token = app_token
        self._on_event = on_event
        self._on_reconnect = on_reconnect

    def run(self, stop: threading.Event) -> None:
        from websockets.sync.client import connect  # 워커에서만 쓴다

        failures = 0
        first = True
        while not stop.is_set():
            try:
                url = call("apps.connections.open", self._app_token, post=True)["url"]
                with connect(url, open_timeout=15, close_timeout=5) as socket:
                    logger.info("slack socket mode connected")
                    failures = 0
                    if not first:
                        self._on_reconnect()
                    first = False
                    while not stop.is_set():
                        try:
                            frame = socket.recv(timeout=1.0)
                        except TimeoutError:
                            continue
                        envelope = json.loads(frame)
                        if envelope.get("envelope_id"):
                            socket.send(json.dumps({"envelope_id": envelope["envelope_id"]}))
                        kind = envelope.get("type")
                        if kind == "disconnect":
                            logger.info("slack socket mode asked to reconnect (%s)", envelope.get("reason"))
                            break
                        if kind == "events_api":
                            try:
                                self._on_event(envelope.get("payload") or {})
                            except Exception:  # noqa: BLE001 — 이벤트 하나가 연결을 끊지 않는다
                                # 이미 ack 했으니 슬랙은 다시 안 보낸다 — 다음 바퀴에 메우기로 받게 한다(검수 W-3).
                                logger.exception("slack event handling failed; gap fill requested")
                                self._on_reconnect()
            except UpstreamAuthRevoked as error:
                logger.error("slack app token refused (%s) — socket mode stopped", error.reason)
                return
            except Exception as error:  # noqa: BLE001 — 끊김은 다시 붙는다
                failures += 1
                logger.warning("slack socket mode dropped (%s); retry %d", type(error).__name__, failures)
            stop.wait(min(60.0, 2.0 * 2 ** min(failures, 5)) if failures else 0.5)
