"""Errors owned by the Work Request domain."""

from ax_workspace.modules.errors import ResourceNotFound


class WorkRequestError(Exception):
    pass


class WorkRequestIdempotencyConflict(WorkRequestError):
    """An idempotency key was reused with different content."""


class WorkRequestAccessDenied(WorkRequestError):
    pass


class WorkRequestNotFound(WorkRequestError, ResourceNotFound):
    pass
