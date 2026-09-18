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


# ---- SPEC-003 §4 Case Matrix — v2 가 더하는 거절들 ----


class WorkRequestNotPending(WorkRequestError):
    """이미 응답한 요청에 수락·거절·철회를 불렀다 (SPEC-003 `WORK_REQUEST_NOT_PENDING`, 409).

    **재전송이면 영수증이 먼저다** — 같은 사람의 같은 답은 두 번째 effect 없이 현재 상태를 돌려준다.
    """


class WorkRequestResponderOnly(WorkRequestAccessDenied):
    """수신자가 아닌 사람이 수락·거절·협의를 불렀다 (SPEC-003 `WORK_REQUEST_RESPONDER_ONLY`, 403)."""


class WorkRequestLockedAfterAccept(WorkRequestError):
    """수락 뒤에 `PATCH` 로 조건을 바꾸려 했다 (SPEC-003 `WORK_REQUEST_LOCKED_AFTER_ACCEPT`, 409).

    바꾸는 길은 제안–동의 하나다 — 이미 받아들인 조건을 한쪽이 혼자 고칠 수 없다 (정책 V-20).
    """


class WorkRejectReasonRequired(WorkRequestError):
    """거절 사유가 비었다 (SPEC-003 `WORK_REJECT_REASON_REQUIRED`, 422)."""
