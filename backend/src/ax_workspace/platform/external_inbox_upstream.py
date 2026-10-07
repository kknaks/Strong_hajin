"""메시지함의 상류 호출 — 첨부 중계·답장 보내기·원격 이미지 프록시 (SPEC-008 §4.4 · BE-3).

BE-2 의 수집 어댑터(Socket Mode·Pub/Sub·백필)와 파일을 가른다 — 여기는 **사람이 메시지함에서 누를 때** 부르는 것만:

- Gmail: 토큰 갱신 · `messages.get`(첨부 partId → 지금의 attachmentId) · `messages.attachments.get` · `messages.send`
- 슬랙: `files.info` · `url_private_download` 받기 · `chat.postMessage` · 파일 올리기(`files.getUploadURLExternal` →
  올리기 → `files.completeUploadExternal` — 옛 `files.upload` 는 닫혔다)
- 원격 이미지: **SSRF 규칙** — http(s) 만 · 이름을 풀어 **모든** 주소가 공인이어야 하고(사설·루프백·링크로컬(=클라우드
  메타데이터 169.254.169.254)·CGNAT·멀티캐스트·예약 거절) · **푼 그 주소로 바로 붙는다**(다시 풀어 바뀌는 DNS rebinding
  차단) · 리다이렉트는 **3번까지 따르되 홉마다 같은 검사를 다시 한다** · **바이트로 판정한** 래스터(PNG·JPEG·GIF·WebP·BMP·ICO)
  또는 SVG 만(SPEC-008 §4.4 v0.6.0 · DEC-009 D-24 — SVG 는 `image/svg+xml` 그대로 내주고, 스크립트가 되지 못하게 하는 것은
  응답 머리(샌드박스 CSP·nosniff·CORP)다) · HTML 은 거절 · 5MB 상한.

토큰·본문은 로그·예외 메시지에 싣지 않는다 — 상류 이름과 오류 코드만.
"""
from __future__ import annotations

import base64
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
import http.client
import ipaddress
import json
import logging
import re
import socket
import ssl
from typing import Any

import certifi
from urllib import error as urlerror, parse as urlparse, request as urlrequest

from ax_workspace.modules.external_channels.inbox import (
    OutgoingFile,
    RefreshedToken,
    RemoteImageRejected,
    UpstreamFailed,
    UpstreamUnauthorized,
)

logger = logging.getLogger(__name__)

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
SLACK_API = "https://slack.com/api"
DEFAULT_TIMEOUT_SECONDS = 30.0
#: 중계 한 번에 받는 최대 바이트 — 슬랙 파일·메일 첨부(받는 쪽 한도는 상류가 정한다). 메모리 보호용 상한.
RELAY_LIMIT_BYTES = 200 * 1024 * 1024
REMOTE_IMAGE_LIMIT_BYTES = 5 * 1024 * 1024
REMOTE_IMAGE_MAX_REDIRECTS = 3


def sniff_raster(data: bytes) -> str | None:
    """바이트 머리로 본 래스터 형식. 래스터만 — SVG·HTML 은 None(SVG 는 `sniff_svg` 가 따로 본다)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    if data.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    return None


#: SVG 판정에서 앞머리로 건너뛰는 것 — XML 선언 · 처리 지시 · 주석 · `<!DOCTYPE svg …>`.
_SVG_PROLOG = re.compile(rb"\A(?:\s+|<\?.*?\?>|<!--.*?-->|<!DOCTYPE\s+svg\b[^>]*>)*", re.DOTALL | re.IGNORECASE)
_SVG_ROOT = re.compile(rb"<svg[\s>/]", re.IGNORECASE)
#: 앞머리를 훑을 최대 바이트 — 판정은 문서 머리로 충분하다.
_SVG_SNIFF_BYTES = 4096


def sniff_svg(data: bytes) -> bool:
    """**바이트가 SVG 문서인가** (SPEC-008 §5 원격 이미지 프록시 v0.6.0) — 선언 `Content-Type` 이 아니라 바이트로 본다.

    BOM · 공백 · XML 선언 · 주석 · `<!DOCTYPE svg …>` 뒤 첫 요소가 `<svg` 면 SVG. HTML(`<html`·`<!DOCTYPE html`)은 False 다.
    """
    head = data[:_SVG_SNIFF_BYTES]
    if head.startswith(b"\xef\xbb\xbf"):
        head = head[3:]
    prolog = _SVG_PROLOG.match(head)
    rest = head[prolog.end():] if prolog else head
    return _SVG_ROOT.match(rest) is not None


SVG_MEDIA_TYPE = "image/svg+xml"
#: 토큰 자체가 죽었다는 슬랙 오류만. `missing_scope`(권한 하나 부족)는 **그 호출만** 실패다 — 연동을 끊지 않는다(BE 수정 판 4).
SLACK_AUTH_ERRORS = frozenset({"invalid_auth", "not_authed", "token_revoked", "token_expired", "account_inactive"})


def _read_limited(response: Any, limit: int) -> bytes:
    data = response.read(limit + 1)
    if len(data) > limit:
        raise UpstreamFailed("upstream_too_large", retryable=False)
    return data


def _call(request: urlrequest.Request, *, timeout: float, limit: int = RELAY_LIMIT_BYTES) -> tuple[bytes, str | None]:
    host = urlparse.urlsplit(request.full_url).netloc
    try:
        with urlrequest.urlopen(request, timeout=timeout) as response:  # noqa: S310 — 고정된 https 상류만
            return _read_limited(response, limit), response.headers.get("Content-Type")
    except urlerror.HTTPError as error:
        status = error.code
        logger.warning("inbox upstream %s refused (%s)", host, status)
        if status == 401:
            raise UpstreamUnauthorized(f"http_{status}") from None
        if status == 403:
            # 403 은 **그 자원만의 거절**이다 — 슬랙 파일 주소는 `files:read` 없으면 403, Gmail 은 속도 제한도 403 이다.
            # 토큰 폐기로 보고 연동을 끊으면 멀쩡한 연동이 조용히 끊긴다(BE 수정 판 4 결함 2).
            raise UpstreamFailed("forbidden", retryable=False) from None
        raise UpstreamFailed(f"http_{status}", retryable=status == 429 or status >= 500) from None
    except (urlerror.URLError, TimeoutError, OSError) as error:
        logger.warning("inbox upstream %s unreachable (%s)", host, type(error).__name__)
        raise UpstreamFailed("unreachable") from None


def _json(request: urlrequest.Request, *, timeout: float) -> dict[str, Any]:
    data, _ = _call(request, timeout=timeout, limit=10 * 1024 * 1024)
    try:
        body = json.loads(data.decode("utf-8"))
    except ValueError:
        raise UpstreamFailed("invalid_json") from None
    return body if isinstance(body, dict) else {}


class GmailInboxApi:
    def __init__(self, client_id: str, client_secret: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = timeout

    def refresh(self, refresh_token: str) -> RefreshedToken:
        body = urlparse.urlencode(
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("utf-8")
        request = urlrequest.Request(
            GOOGLE_TOKEN_URL, data=body, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        try:
            answer = _json(request, timeout=self._timeout)
        except UpstreamFailed as failure:
            # invalid_grant(권한 회수)은 400 으로 온다 — 다시 시도해도 안 된다.
            if failure.code == "http_400":
                raise UpstreamUnauthorized("invalid_grant") from None
            raise
        token = answer.get("access_token")
        if not isinstance(token, str) or not token:
            raise UpstreamUnauthorized("no_access_token")
        expires_in = answer.get("expires_in")
        return RefreshedToken(token, datetime.now(UTC) + timedelta(seconds=int(expires_in)) if isinstance(expires_in, int) else None)

    def _get(self, token: str, path: str) -> dict[str, Any]:
        request = urlrequest.Request(f"{GMAIL_API}{path}", headers={"Authorization": f"Bearer {token}"})
        return _json(request, timeout=self._timeout)

    def get_message(self, access_token: str, message_id: str) -> dict[str, Any]:
        return self._get(access_token, f"/messages/{urlparse.quote(message_id, safe='')}?format=full")

    def attachment(self, access_token: str, message_id: str, attachment_id: str) -> bytes:
        body = self._get(
            access_token,
            f"/messages/{urlparse.quote(message_id, safe='')}/attachments/{urlparse.quote(attachment_id, safe='')}",
        )
        data = body.get("data")
        if not isinstance(data, str):
            raise UpstreamFailed("attachment_without_data", retryable=False)
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))

    def send(self, access_token: str, raw_message: bytes, thread_id: str | None) -> dict[str, Any]:
        payload: dict[str, Any] = {"raw": base64.urlsafe_b64encode(raw_message).decode("ascii")}
        if thread_id:
            payload["threadId"] = thread_id
        request = urlrequest.Request(
            f"{GMAIL_API}/messages/send",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        )
        return _json(request, timeout=max(self._timeout, 120.0))


#: 슬랙 파일이 사는 도메인 — 회원 토큰을 실어 보내도 되는 곳은 이것과 그 하위뿐이다.
SLACK_FILE_DOMAINS = ("slack.com", "slack-edge.com", "slack-files.com")


def slack_file_host(host: str | None) -> bool:
    """`host == d` 또는 `host` 가 `.d` 로 끝날 때만 — 점이 없으면 `evilslack.com` 도 통과한다(BE-2·3 검수 W-2)."""
    host = (host or "").lower().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in SLACK_FILE_DOMAINS)


class SlackInboxApi:
    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout

    def _api(self, token: str, method: str, fields: dict[str, Any], *, as_json: bool = False) -> dict[str, Any]:
        if as_json:
            data = json.dumps(fields).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        else:
            data = urlparse.urlencode({key: value for key, value in fields.items() if value is not None}).encode("utf-8")
            content_type = "application/x-www-form-urlencoded"
        request = urlrequest.Request(
            f"{SLACK_API}/{method}",
            data=data,
            method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
        )
        answer = _json(request, timeout=self._timeout)
        if not answer.get("ok"):
            code = str(answer.get("error") or "unknown")[:80]
            logger.warning("slack %s failed: %s", method, code)
            if code in SLACK_AUTH_ERRORS:
                raise UpstreamUnauthorized(code)
            raise UpstreamFailed(code, retryable=code in {"ratelimited", "internal_error", "fatal_error", "service_unavailable"})
        return answer

    def file_info(self, access_token: str, file_id: str) -> dict[str, Any]:
        return self._api(access_token, "files.info", {"file": file_id}).get("file") or {}

    def download(self, access_token: str, url: str) -> tuple[bytes, str | None]:
        parts = urlparse.urlsplit(url)
        if parts.scheme != "https" or not slack_file_host(parts.hostname):
            # 원문에 적힌 주소라도 슬랙 밖이면 토큰을 실어 보내지 않는다.
            raise UpstreamFailed("not_a_slack_file_url", retryable=False)
        request = urlrequest.Request(url, headers={"Authorization": f"Bearer {access_token}"})
        return _call(request, timeout=max(self._timeout, 120.0))

    def post_message(self, access_token: str, channel: str, text: str, thread_ts: str | None) -> dict[str, Any]:
        return self._api(
            access_token, "chat.postMessage", {"channel": channel, "text": text, "thread_ts": thread_ts}, as_json=True
        )

    def upload_files(
        self, access_token: str, channel: str, thread_ts: str | None, text: str, files: Sequence[OutgoingFile]
    ) -> dict[str, Any]:
        uploaded = []
        for item in files:
            slot = self._api(access_token, "files.getUploadURLExternal", {"filename": item.name, "length": len(item.data)})
            upload_url, file_id = slot.get("upload_url"), slot.get("file_id")
            if not upload_url or not file_id:
                raise UpstreamFailed("no_upload_url", retryable=False)
            request = urlrequest.Request(
                str(upload_url), data=item.data, method="POST", headers={"Content-Type": "application/octet-stream"}
            )
            _call(request, timeout=max(self._timeout, 300.0), limit=1024 * 1024)
            uploaded.append({"id": file_id, "title": item.name})
        fields: dict[str, Any] = {"files": uploaded, "channel_id": channel}
        if thread_ts:
            fields["thread_ts"] = thread_ts
        if text:
            fields["initial_comment"] = text
        return self._api(access_token, "files.completeUploadExternal", fields, as_json=True)


# ── 원격 이미지 — SSRF 규칙 ─────────────────────────────────────────────────────────────────


def address_is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(
        ip.is_global
        and not ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_multicast
        and not ip.is_reserved
        and not ip.is_unspecified
    )


def _image_tls_context() -> ssl.SSLContext:
    """원격 이미지용 TLS — **certifi 묶음 + 시스템 루트** 둘 다 믿는다(BE 수정 판 5).

    아무 메일 발신 서버나 상대한다. 시스템 묶음이 낡으면(로컬 macOS `/etc/ssl/cert.pem` 에 Sectigo R46 루트가 없어
    삼성SDS 뉴스레터 이미지가 전부 `CERTIFICATE_VERIFY_FAILED` 였다) 검증이 실패한다 — 검증을 끄지 않고 루트를 넓힌다.
    """
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_default_certs()
    return context


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """이미 검사한 주소로 붙되 인증서·SNI 는 원래 이름으로 확인한다."""

    def __init__(self, host: str, address: str, port: int, *, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout, context=_image_tls_context())
        self._address = address

    def connect(self) -> None:
        sock = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, address: str, port: int, *, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


class SafeImageFetcher:
    def __init__(self, *, timeout: float = 10.0, limit: int = REMOTE_IMAGE_LIMIT_BYTES, resolver=socket.getaddrinfo) -> None:
        self._timeout = timeout
        self._limit = limit
        self._resolver = resolver

    def fetch(self, url: str) -> tuple[bytes, str]:
        """원격 이미지 하나. 리다이렉트(추적·CDN·camo)는 **홉마다 SSRF 검사를 다시 하며** 몇 번까지 따른다.
        실패는 사유와 함께 한 줄 로그를 남긴다 — 무엇이 왜 안 나오는지 운영에서 알 수 있게(BE 수정 판 5)."""
        current = url
        for _ in range(REMOTE_IMAGE_MAX_REDIRECTS + 1):
            try:
                outcome = self._fetch_once(current)
            except (RemoteImageRejected, UpstreamFailed) as failure:
                logger.warning("remote image refused: host=%s reason=%s", urlparse.urlsplit(current).hostname, getattr(failure, "code", None) or failure)
                raise
            if isinstance(outcome, tuple):
                return outcome
            current = urlparse.urljoin(current, outcome)
        logger.warning("remote image refused: host=%s reason=too_many_redirects", urlparse.urlsplit(current).hostname)
        raise UpstreamFailed("too_many_redirects", retryable=False)

    def _fetch_once(self, url: str) -> tuple[bytes, str] | str:
        parts = urlparse.urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            raise RemoteImageRejected("http(s) 주소만 받습니다")
        try:
            port = parts.port or (443 if scheme == "https" else 80)
        except ValueError:
            raise RemoteImageRejected("포트가 올바르지 않습니다") from None
        if port not in (80, 443, 8080, 8443):
            raise RemoteImageRejected("허용하지 않는 포트입니다")
        try:
            resolved = {row[4][0] for row in self._resolver(parts.hostname, port, type=socket.SOCK_STREAM)}
        except (socket.gaierror, UnicodeError, OSError):
            raise UpstreamFailed("dns_failed") from None
        if not resolved or not all(address_is_public(address) for address in resolved):
            raise RemoteImageRejected("사설·루프백·메타데이터 주소는 받지 않습니다")
        address = sorted(resolved)[0]
        connection_type = _PinnedHTTPSConnection if scheme == "https" else _PinnedHTTPConnection
        connection = connection_type(parts.hostname, address, port, timeout=self._timeout)
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        try:
            connection.request("GET", path, headers={
                "Host": parts.netloc, "User-Agent": "Mozilla/5.0 (compatible; ax-inbox-image-proxy)", "Accept": "image/*,*/*;q=0.5",
            })
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308) and response.getheader("Location"):
                return response.getheader("Location")  # 다음 홉 — 부르는 쪽이 같은 검사를 다시 한다
            if response.status != 200:
                raise UpstreamFailed(f"http_{response.status}", retryable=False)
            declared = response.getheader("Content-Length")
            if declared and declared.isdigit() and int(declared) > self._limit:
                raise RemoteImageRejected("이미지가 너무 큽니다")
            data = response.read(self._limit + 1)
            if len(data) > self._limit:
                raise RemoteImageRejected("이미지가 너무 큽니다")
            # 선언된 type 이 아니라 **바이트**로 가른다 — 없는 type · octet-stream · `image/jpg` 같은 틀린 이름도 실제 래스터면
            # 받고, 래스터도 SVG 도 아니면(HTML 포함) 이름이 image/* 여도 받지 않는다. 내려보내는 type 도 바이트가 정한다.
            # SVG 는 래스터로 바꾸지 않고 `image/svg+xml` 그대로다(SPEC-008 §4.4 v0.6.0 · D-24 — GitHub camo 배지 등).
            sniffed = sniff_raster(data) or (SVG_MEDIA_TYPE if sniff_svg(data) else None)
            if sniffed is None:
                raise RemoteImageRejected("이미지가 아닙니다")
            return data, sniffed
        except ssl.SSLError as error:
            raise UpstreamFailed(f"tls_{getattr(error, 'reason', None) or 'error'}"[:40]) from None
        except (OSError, http.client.HTTPException) as error:
            raise UpstreamFailed(f"unreachable_{type(error).__name__}"[:40]) from None
        finally:
            connection.close()
