"""HTTP authentication adapters.

The login session is the credential boundary: the browser holds an opaque session cookie and the server resolves it to
an active organization principal. Proving who someone is and deciding what they may do are separate — the provider
that signed them in never carries a privilege, and a Google OIDC provider will plug into the same login route and
session store without touching authorization.

`DeveloperAuthAdapter` is a development/test seam only: it lets a test say which member it is acting as, and the
membership ledger still decides whether that member exists and is active.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request, status

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.modules.organization_access.domain import Principal

SESSION_COOKIE = "scax_session"
SESSION_MAX_AGE = 12 * 60 * 60


class DeveloperAuthAdapter:
    """A development-only seam. It names a member; it never carries a capability."""

    provider_name = "developer"

    def __init__(self, settings: Settings) -> None:
        if not settings.developer_auth_enabled:
            raise RuntimeError("DeveloperAuthAdapter is forbidden outside development and test")

    def member_id(self, header_value: str | None) -> str:
        member_id = (header_value or "").strip()
        if not member_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="로그인이 필요합니다.")
        return member_id


def session_id_from(request: Request) -> UUID | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


def _active_principal(request: Request, member_id: str) -> Principal:
    try:
        return request.app.state.workflow_application.authenticated_principal(member_id)
    except LookupError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Active organization membership is required.") from error


def session_principal(request: Request) -> Principal | None:
    """Resolve the login-session cookie only; returns None when no valid session exists."""
    session_id = session_id_from(request)
    if session_id is None:
        return None
    member_id = request.app.state.auth_sessions.member_for(session_id)
    if member_id is None:
        return None
    return _active_principal(request, member_id)


def current_principal(request: Request) -> Principal:
    """Login session first; the developer persona header stays a development/test seam only."""
    principal = session_principal(request)
    if principal is not None:
        return principal
    adapter: DeveloperAuthAdapter | None = getattr(request.app.state, "developer_auth", None)
    header_member = request.headers.get("X-Demo-Persona")
    if adapter is not None and header_member:
        # The header says which member a test is acting as; the ledger still decides whether that member may act.
        return _active_principal(request, adapter.member_id(header_member))
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="로그인이 필요합니다.")


def connection_principal(connection: Any) -> Principal | None:
    """WebSocket 핸드셰이크의 사람. 브라우저 WS 는 헤더를 못 붙이므로 **쿠키가 인증한다**.

    첫 프레임은 역할만 정하고 토큰을 싣지 않는다 (SCAX-SPEC-004 §5.3 · WP-002 Interface Contract).
    개발 페르소나 헤더는 REST 와 같은 개발·시험 이음새로만 남는다. 실패는 예외가 아니라 `None` 이다 —
    WS 는 HTTP 상태코드가 아니라 close code 로 답한다.
    """
    try:
        principal = session_principal(connection)
        if principal is not None:
            return principal
        adapter: DeveloperAuthAdapter | None = getattr(connection.app.state, "developer_auth", None)
        header_member = connection.headers.get("X-Demo-Persona")
        if adapter is not None and header_member:
            return _active_principal(connection, adapter.member_id(header_member))
    except HTTPException:
        return None
    return None


# Backwards-compatible name used by existing routes.
developer_principal = current_principal


def cookie_secure(settings: Settings) -> bool:
    return settings.profile == RuntimeProfile.PRODUCTION
