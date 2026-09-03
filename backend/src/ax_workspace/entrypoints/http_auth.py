"""HTTP authentication adapters.

The login session is the production credential boundary: the browser holds an opaque
session cookie and the server resolves it to an active organization principal.  The
DeveloperAuthAdapter is the only credential *provider* wired today; it accepts a seeded
persona in development/test.  A Google OIDC provider will plug into the same login route
and session store without touching authorization.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, Request, status

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.modules.organization_access.domain import Principal, seeded_principal

SESSION_COOKIE = "scax_session"
SESSION_MAX_AGE = 12 * 60 * 60


class DeveloperAuthAdapter:
    """A development-only credential provider which never accepts caller-supplied privileges."""

    provider_name = "developer"

    def __init__(self, settings: Settings) -> None:
        if not settings.developer_auth_enabled:
            raise RuntimeError("DeveloperAuthAdapter is forbidden outside development and test")

    def authenticate(self, persona: str | None) -> Principal:
        try:
            return seeded_principal(persona or "")
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Select one of the seeded demo personas.",
            ) from error


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
    header_persona = request.headers.get("X-Demo-Persona")
    if adapter is not None and header_persona:
        return _active_principal(request, str(adapter.authenticate(header_persona).id))
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="로그인이 필요합니다.")


# Backwards-compatible name used by existing routes.
developer_principal = current_principal


def cookie_secure(settings: Settings) -> bool:
    return settings.profile == RuntimeProfile.PRODUCTION
