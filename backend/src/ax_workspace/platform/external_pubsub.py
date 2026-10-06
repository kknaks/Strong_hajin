"""Google Cloud Pub/Sub **pull** — Gmail watch 알림을 받는다 (SPEC-008 §5 · OQ-803 pull · 공개 수신 주소 없음).

서비스 계정 키 파일(`GOOGLE_PUBSUB_SA_KEY_FILE`)로 JWT 를 서명해 액세스 토큰을 받는다 — 구글 SDK 없이
`cryptography` 의 RS256 서명 하나로 된다. 키 파일 내용은 메모리에만, 로그에는 이메일·프로젝트도 싣지 않는다.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any
from urllib import error as urlerror, parse as urlparse, request as urlrequest

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from ax_workspace.modules.external_channels.sync import UpstreamAuthRevoked, UpstreamUnavailable

PUBSUB_API = "https://pubsub.googleapis.com/v1/"
PUBSUB_SCOPE = "https://www.googleapis.com/auth/pubsub"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def qualified(name: str, project_id: str, kind: str) -> str:
    """`ax-gmail-pull` 같은 짧은 이름을 `projects/<p>/subscriptions/ax-gmail-pull` 로. 이미 전체 이름이면 그대로."""
    return name if name.startswith("projects/") else f"projects/{project_id}/{kind}/{name}"


@dataclass(frozen=True, slots=True)
class GmailNotification:
    ack_id: str
    email: str | None
    history_id: str | None


class PubSubPuller:
    def __init__(self, key_file: str, subscription: str, *, timeout: float = 60.0) -> None:
        info = json.loads(Path(key_file).read_text(encoding="utf-8"))
        self._email = info["client_email"]
        self._token_uri = info.get("token_uri") or "https://oauth2.googleapis.com/token"
        self._key = serialization.load_pem_private_key(info["private_key"].encode(), password=None)
        self.project_id = str(info.get("project_id") or "")
        self.subscription = qualified(subscription, self.project_id, "subscriptions")
        self._timeout = timeout
        self._token: str | None = None
        self._token_until = 0.0

    def _access_token(self) -> str:
        now = time.time()
        if self._token and now < self._token_until - 60:
            return self._token
        header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        claims = _b64(json.dumps({
            "iss": self._email, "scope": PUBSUB_SCOPE, "aud": self._token_uri, "iat": int(now), "exp": int(now) + 3600,
        }).encode())
        signature = self._key.sign(f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())  # type: ignore[call-arg]
        body = urlparse.urlencode({
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": f"{header}.{claims}.{_b64(signature)}",
        }).encode()
        answer = self._post(self._token_uri, body, form=True, auth=False)
        self._token = str(answer["access_token"])
        self._token_until = now + int(answer.get("expires_in") or 3600)
        return self._token

    def _post(self, url: str, body: bytes, *, form: bool = False, auth: bool = True, timeout: float | None = None) -> dict[str, Any]:
        headers = {"Content-Type": "application/x-www-form-urlencoded" if form else "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {self._access_token()}"
        request = urlrequest.Request(url, data=body, method="POST", headers=headers)
        try:
            with urlrequest.urlopen(request, timeout=timeout or self._timeout) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8") or "{}")
        except urlerror.HTTPError as error:
            if error.code in (401, 403):
                raise UpstreamAuthRevoked(f"pubsub http {error.code}") from None
            raise UpstreamUnavailable(f"pubsub http {error.code}") from None
        except (urlerror.URLError, TimeoutError, OSError, ValueError) as error:
            raise UpstreamUnavailable(f"pubsub unreachable ({type(error).__name__})") from None

    def pull(self, max_messages: int = 50) -> list[GmailNotification]:
        """알림을 기다려 받는다(긴 대기). 없으면 빈 목록."""
        answer = self._post(PUBSUB_API + self.subscription + ":pull", json.dumps({"maxMessages": max_messages}).encode())
        notes = []
        for row in answer.get("receivedMessages") or []:
            data: dict[str, Any] = {}
            try:
                data = json.loads(base64.b64decode((row.get("message") or {}).get("data") or b"").decode("utf-8") or "{}")
            except (ValueError, UnicodeDecodeError):
                pass
            notes.append(GmailNotification(
                ack_id=str(row["ackId"]), email=data.get("emailAddress"),
                history_id=str(data["historyId"]) if data.get("historyId") else None,
            ))
        return notes

    def acknowledge(self, ack_ids: list[str]) -> None:
        if ack_ids:
            self._post(PUBSUB_API + self.subscription + ":acknowledge", json.dumps({"ackIds": ack_ids}).encode())
