"""Development HTTP authentication adapter; the domain only owns allowed principals."""

from fastapi import HTTPException, Request, status

from ax_workspace.bootstrap.settings import Settings
from ax_workspace.modules.organization_access.domain import Principal, seeded_principal


class DeveloperAuthAdapter:
    """A development-only adapter which never accepts caller-supplied privileges."""

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


def developer_principal(request: Request) -> Principal:
    adapter: DeveloperAuthAdapter = request.app.state.developer_auth
    allowed_persona = adapter.authenticate(request.headers.get("X-Demo-Persona"))
    try:
        return request.app.state.workflow_application.authenticated_principal(str(allowed_persona.id))
    except LookupError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Active organization membership is required.") from error
