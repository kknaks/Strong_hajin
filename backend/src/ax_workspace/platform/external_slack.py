"""슬랙 상류 어댑터 — 사용자 토큰 Web API 와 앱 토큰 Socket Mode (WORK-011 BE-2 · SPEC-008 §5 동기화).

- Web API 는 **연결한 사람의 사용자 토큰**으로 부른다 — 그 사람이 볼 수 있는 방만 읽는다(D-11).
- Socket Mode 는 **앱 토큰 1개로 이 워커 한 곳에서만** 연다(OQ-802 · 단일 소유). 여러 프로세스가 같은 앱 토큰으로
  붙으면 슬랙이 이벤트를 나눠 보내 한쪽만 받는다 — 레플리카 1 이 계약이다.

토큰은 헤더에만 싣고 로그·예외에는 상류 오류 코드만 남긴다.
"""
from __future__ import annotations

from collections.abc import Callable
import json
import logging
import threading
from typing import Any
from urllib import error as urlerror, parse as urlparse, request as urlrequest

from ax_workspace.modules.external_channels.sync import UpstreamAuthRevoked, UpstreamRateLimited, UpstreamUnavailable

logger = logging.getLogger(__name__)

SLACK_API = "https://slack.com/api/"
#: 토큰이 더는 통하지 않는다는 뜻의 슬랙 오류 — 연동을 끊김으로(D-50).
AUTH_ERRORS = frozenset({
    "invalid_auth", "not_authed", "token_revoked", "token_expired", "account_inactive",
    "team_access_not_granted", "org_login_required", "ekm_access_denied",
})
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
        if code == "ratelimited":
            raise UpstreamRateLimited(30)
        raise UpstreamUnavailable(f"slack {method} refused ({code})")
    if scopes is not None:
        body.setdefault("_scopes", scopes)
    return body


class SlackWebApi:
    """`modules/external_channels/sync.SlackApi` 의 실물."""

    def __init__(self) -> None:
        self._names: dict[tuple[str, str], str | None] = {}

    def history(self, token, channel, *, cursor=None, oldest=None, limit=200):
        body = call("conversations.history", token, {"channel": channel, "cursor": cursor, "oldest": oldest, "limit": limit})
        return list(body.get("messages") or []), ((body.get("response_metadata") or {}).get("next_cursor") or None)

    def replies(self, token, channel, ts, *, cursor=None):
        body = call("conversations.replies", token, {"channel": channel, "ts": ts, "cursor": cursor, "limit": 200})
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
        key = (token[-8:], user_id)
        if key not in self._names:
            try:
                user = call("users.info", token, {"user": user_id}).get("user") or {}
            except UpstreamUnavailable:
                return None
            profile = user.get("profile") or {}
            self._names[key] = profile.get("display_name") or profile.get("real_name") or user.get("real_name") or user.get("name")
        return self._names[key]

    @staticmethod
    def identity(token: str) -> dict[str, Any]:
        """`auth.test` — 개발 토큰 주입이 team·user 를 알아낸다. `_scopes` 에 허용 권한이 실린다."""
        return call("auth.test", token, post=True)


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
                                logger.exception("slack event handling failed")
            except UpstreamAuthRevoked as error:
                logger.error("slack app token refused (%s) — socket mode stopped", error.reason)
                return
            except Exception as error:  # noqa: BLE001 — 끊김은 다시 붙는다
                failures += 1
                logger.warning("slack socket mode dropped (%s); retry %d", type(error).__name__, failures)
            stop.wait(min(60.0, 2.0 * 2 ** min(failures, 5)) if failures else 0.5)
