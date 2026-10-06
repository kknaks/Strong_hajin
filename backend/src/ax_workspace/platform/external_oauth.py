"""Gmail·슬랙 OAuth 어댑터 — 동의 URL 만들기와 code → 토큰 교환 (SPEC-008 §4.2).

연결은 로그인이 아니다: 여기서 얻는 토큰은 외부 채널을 읽고 보내는 권한일 뿐이고, 누가 연결했는지는 서버가
발급한 `state` 가 정한다(`http_auth.py` 머리 주석의 경계). 토큰·code·client secret 은 예외 메시지와 로그에 싣지
않는다 — 상류 오류는 그 `error` 코드만 남긴다.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import logging
from typing import Any
from urllib import error as urlerror, parse as urlparse, request as urlrequest

from ax_workspace.modules.external_channels.application import OAuthGrant
from ax_workspace.modules.external_channels.domain import OAuthExchangeFailed

logger = logging.getLogger(__name__)

#: Gmail — 받은편지함 읽기와 답장 보내기를 **한 번에**(D-09).
GMAIL_SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
)
GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"

#: 슬랙 사용자 토큰 권한 — 실측 10개(BASE-006 입력 4-2) + 답장 첨부 `files:write`(D-32) = 11.
#: 사용자 토큰이라 **사람이 이미 볼 수 있는 방만** 읽는다(봇은 남의 DM 을 못 읽는다 — D-11).
SLACK_USER_SCOPES = (
    "channels:history",
    "channels:read",
    "groups:history",
    "groups:read",
    "im:history",
    "im:read",
    "mpim:history",
    "mpim:read",
    "users:read",
    "chat:write",
    "files:write",
)
SLACK_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
SLACK_TOKEN_URL = "https://slack.com/api/oauth.v2.access"

DEFAULT_TIMEOUT_SECONDS = 20.0


def _post_form(url: str, fields: dict[str, str], timeout: float) -> dict[str, Any]:
    body = urlparse.urlencode(fields).encode("utf-8")
    request = urlrequest.Request(url, data=body, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"})
    return _json(request, timeout)


def _get_bearer(url: str, token: str, timeout: float) -> dict[str, Any]:
    request = urlrequest.Request(url, method="GET", headers={"Authorization": f"Bearer {token}"})
    return _json(request, timeout)


def _json(request: urlrequest.Request, timeout: float) -> dict[str, Any]:
    host = urlparse.urlsplit(request.full_url).netloc
    try:
        with urlrequest.urlopen(request, timeout=timeout) as response:  # noqa: S310 — 고정된 https 상류만
            return json.loads(response.read().decode("utf-8"))
    except urlerror.HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8"))
        except (ValueError, OSError):
            detail = {}
        code = detail.get("error") if isinstance(detail, dict) else None
        reason = f"upstream refused ({error.code}{' ' + code if isinstance(code, str) else ''})"
    except (urlerror.URLError, TimeoutError, ValueError, OSError) as error:
        reason = f"upstream unreachable ({type(error).__name__})"
    # 상류 이름과 오류 코드만 남긴다 — code·토큰·client secret 은 요청 본문에만 있고 여기 오지 않는다.
    logger.warning("external oauth call to %s failed: %s", host, reason)
    raise OAuthExchangeFailed(reason)


class GoogleGmailOAuth:
    def __init__(self, client_id: str, client_secret: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = timeout

    def authorize_url(self, *, state: str, redirect_uri: str) -> str:
        query = {
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(GMAIL_SCOPES),
            # 수집은 사람이 없는 동안 돈다 — refresh token 이 있어야 한다. 다시 동의해도 새로 받게 `consent`.
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
        return f"{GOOGLE_AUTHORIZE_URL}?{urlparse.urlencode(query)}"

    def exchange(self, *, code: str, redirect_uri: str) -> OAuthGrant:
        token = _post_form(
            GOOGLE_TOKEN_URL,
            {
                "code": code,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            self._timeout,
        )
        access_token = token.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise OAuthExchangeFailed("google returned no access token")
        granted = set(str(token.get("scope") or "").split())
        missing = [scope for scope in GMAIL_SCOPES if scope not in granted]
        if missing:
            # 동의 화면에서 일부 권한을 뺐다 — 반쪽 연동은 만들지 않는다.
            raise OAuthExchangeFailed("google consent did not grant every gmail scope")
        profile = _get_bearer(GMAIL_PROFILE_URL, access_token, self._timeout)
        email = str(profile.get("emailAddress") or "").strip().lower()
        if not email:
            raise OAuthExchangeFailed("gmail profile had no address")
        expires_in = token.get("expires_in")
        return OAuthGrant(
            account_key=email,
            display_name=email,
            account_meta={"email": email},
            access_token=access_token,
            refresh_token=token.get("refresh_token") if isinstance(token.get("refresh_token"), str) else None,
            expires_at=datetime.now(UTC) + timedelta(seconds=int(expires_in)) if isinstance(expires_in, int) else None,
            scopes=" ".join(sorted(granted)),
        )


class SlackUserOAuth:
    def __init__(self, client_id: str, client_secret: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = timeout

    def authorize_url(self, *, state: str, redirect_uri: str) -> str:
        query = {
            "client_id": self._client_id,
            # 봇 권한(`scope`)이 아니라 **사용자 토큰**(`user_scope`) — 내 이름으로 읽고 보낸다(D-11 · D-32).
            "user_scope": ",".join(SLACK_USER_SCOPES),
            "redirect_uri": redirect_uri,
            "state": state,
        }
        return f"{SLACK_AUTHORIZE_URL}?{urlparse.urlencode(query)}"

    def exchange(self, *, code: str, redirect_uri: str) -> OAuthGrant:
        answer = _post_form(
            SLACK_TOKEN_URL,
            {"code": code, "client_id": self._client_id, "client_secret": self._client_secret, "redirect_uri": redirect_uri},
            self._timeout,
        )
        if not answer.get("ok"):
            code_name = answer.get("error")
            reason = f"slack refused ({code_name if isinstance(code_name, str) else 'unknown'})"
            logger.warning("external oauth call to slack.com failed: %s", reason)
            raise OAuthExchangeFailed(reason)
        user = answer.get("authed_user") or {}
        team = answer.get("team") or {}
        access_token = user.get("access_token")
        team_id = team.get("id")
        if not isinstance(access_token, str) or not access_token or not isinstance(team_id, str) or not team_id:
            raise OAuthExchangeFailed("slack returned no user token")
        expires_in = user.get("expires_in")
        return OAuthGrant(
            account_key=team_id,
            display_name=str(team.get("name") or team_id),
            account_meta={"team_id": team_id, "team_name": team.get("name"), "user_id": user.get("id")},
            access_token=access_token,
            refresh_token=user.get("refresh_token") if isinstance(user.get("refresh_token"), str) else None,
            expires_at=datetime.now(UTC) + timedelta(seconds=int(expires_in)) if isinstance(expires_in, int) else None,
            scopes=str(user.get("scope") or ""),
        )
