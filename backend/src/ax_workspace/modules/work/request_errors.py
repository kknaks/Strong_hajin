"""Errors owned by the Work Request domain."""


class WorkRequestError(Exception):
    pass


class WorkRequestIdempotencyConflict(WorkRequestError):
    """An idempotency key was reused with different content."""


class WorkRequestAccessDenied(WorkRequestError):
    pass
