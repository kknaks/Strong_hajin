"""Gmail 상류 어댑터 — 연결한 사람의 OAuth 토큰으로 받은편지함을 읽는다 (WORK-011 BE-2 · D-09·D-10·D-22).

액세스 토큰은 짧다. 만료되면 refresh token 으로 새로 받고(`refresh`), refresh 가 `invalid_grant` 면 권한이
거둬진 것이다 → 연동 끊김. 토큰·code 는 로그·예외에 싣지 않는다.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import logging
from typing import Any
from urllib import error as urlerror, parse as urlparse, request as urlrequest

from ax_workspace.modules.external_channels.sync import (
    HistoryExpired,
    UpstreamCallDenied,
    UpstreamAuthRevoked,
    UpstreamRateLimited,
    UpstreamUnavailable,
)

logger = logging.getLogger(__name__)

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me/"
#: 속도·할당량 사유 — Gmail 은 이것들을 403 으로도 낸다.
RATE_LIMIT_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded", "dailyLimitExceeded", "concurrentLimitExceeded"})
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
DEFAULT_TIMEOUT = 30.0


def _request(request: urlrequest.Request, what: str, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    try:
        with urlrequest.urlopen(request, timeout=timeout) as response:  # noqa: S310 — 고정된 https 상류
            return json.loads(response.read().decode("utf-8") or "{}")
    except urlerror.HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8"))
        except (ValueError, OSError):
            detail = {}
        body = detail.get("error") if isinstance(detail, dict) else None
        if isinstance(body, dict):
            code = str(body.get("status") or body.get("message") or "")
            reasons = {str(row.get("reason")) for row in body.get("errors") or [] if isinstance(row, dict)}
            message = str(body.get("message") or "")[:200]
        else:
            code, reasons, message = str(body or ""), set(), str(detail.get("error_description") or "")[:200] if isinstance(detail, dict) else ""
        # 무엇이 왜 거절됐는지 남긴다 — 상류의 상태·사유·문장만(토큰·메일 내용은 요청·응답 어디에도 여기 오지 않는다).
        logger.warning("%s refused: http %s status=%s reasons=%s message=%s", what, error.code, code, sorted(reasons), message)
        if error.code == 401 or code in {"invalid_grant", "unauthorized_client"}:
            raise UpstreamAuthRevoked(str(code or "unauthorized")[:40]) from None
        if error.code == 404:
            raise LookupError(what) from None
        if error.code == 429 or reasons & RATE_LIMIT_REASONS or "EXHAUSTED" in code:
            # Gmail 은 속도 제한도 403·`PERMISSION_DENIED` 로 낸다 — 사유(`userRateLimitExceeded` …)로 가른다.
            raise UpstreamRateLimited(float(error.headers.get("Retry-After") or 60)) from None
        if error.code == 403:
            # 그 호출만의 거절이다. **토큰 폐기로 보지 않는다**(BE 수정 판 4) — 폐기는 401·invalid_grant 일 때만.
            raise UpstreamCallDenied(f"{what}: {code or 'forbidden'} {sorted(reasons)}"[:120]) from None
        raise UpstreamUnavailable(f"{what} http {error.code}") from None
    except (urlerror.URLError, TimeoutError, OSError, ValueError) as error:
        raise UpstreamUnavailable(f"{what} unreachable ({type(error).__name__})") from None


def _expires(seconds: Any) -> datetime | None:
    return datetime.now(UTC) + timedelta(seconds=int(seconds)) if isinstance(seconds, int) else None


class GmailApi:
    """`modules/external_channels/sync.GmailApi` 의 실물."""

    def __init__(self, client_id: str, client_secret: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret

    def _get(self, token: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = urlparse.urlencode([(k, v) for k, v in (params or {}).items() if v is not None], doseq=True)
        request = urlrequest.Request(GMAIL_API + path + (f"?{query}" if query else ""), headers={"Authorization": f"Bearer {token}"})
        return _request(request, f"gmail {path.split('/')[0]}")

    def refresh(self, refresh_token: str) -> tuple[str, datetime | None]:
        body = urlparse.urlencode({
            "client_id": self._client_id, "client_secret": self._client_secret,
            "refresh_token": refresh_token, "grant_type": "refresh_token",
        }).encode()
        request = urlrequest.Request(GOOGLE_TOKEN_URL, data=body, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            answer = _request(request, "google token refresh")
        except LookupError:
            raise UpstreamUnavailable("google token refresh 404") from None
        if not answer.get("access_token"):
            raise UpstreamAuthRevoked("refresh_failed")
        return str(answer["access_token"]), _expires(answer.get("expires_in"))

    def profile(self, token: str) -> dict[str, Any]:
        return self._get(token, "profile")

    def watch(self, token: str, topic: str) -> tuple[str, datetime]:
        request = urlrequest.Request(
            GMAIL_API + "watch", method="POST",
            data=json.dumps({"topicName": topic, "labelIds": ["INBOX"], "labelFilterBehavior": "include"}).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        answer = _request(request, "gmail watch")
        return str(answer.get("historyId") or ""), datetime.fromtimestamp(int(answer["expiration"]) / 1000, UTC)

    def list_inbox(self, token: str, *, page_token: str | None = None, query: str | None = None) -> tuple[list[str], str | None]:
        answer = self._get(token, "messages", {"labelIds": "INBOX", "maxResults": 100, "pageToken": page_token, "q": query})
        return [str(row["id"]) for row in answer.get("messages") or []], answer.get("nextPageToken") or None

    def get_message(self, token: str, message_id: str) -> dict[str, Any] | None:
        try:
            return self._get(token, f"messages/{message_id}", {"format": "full"})
        except LookupError:
            return None

    def history(self, token: str, start_history_id: str, *, page_token: str | None = None) -> tuple[list[str], str | None, str]:
        try:
            answer = self._get(token, "history", {
                "startHistoryId": start_history_id, "historyTypes": "messageAdded", "labelId": "INBOX",
                "maxResults": 500, "pageToken": page_token,
            })
        except LookupError:
            raise HistoryExpired(start_history_id) from None
        ids = [
            str(added["message"]["id"])
            for row in answer.get("history") or []
            for added in row.get("messagesAdded") or []
            if isinstance(added.get("message"), dict) and added["message"].get("id")
        ]
        return ids, answer.get("nextPageToken") or None, str(answer.get("historyId") or start_history_id)
