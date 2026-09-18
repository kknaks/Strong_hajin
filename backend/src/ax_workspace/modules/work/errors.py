"""Task errors shared by value policies and the owning application."""


class TaskError(Exception):
    pass


class TaskNotFound(TaskError):
    pass


class InvalidTaskTransition(TaskError):
    pass


class TaskAccessDenied(TaskError):
    pass


class TaskRecipientNotAllowed(TaskError):
    """보낼 수 있는 사람이 아니다 — 권한 밖 담당 지정 (SPEC-001 `WORK_RECIPIENT_NOT_ALLOWED`)."""


class TaskIdempotencyKeyRequired(TaskError):
    """생성 명령에 멱등 키가 없다 (SPEC-001 `WORK_IDEMPOTENCY_KEY_REQUIRED`)."""


class TaskIdempotencyConflict(TaskError):
    """같은 키로 다른 내용이 왔다 (SPEC-001 `WORK_IDEMPOTENCY_CONFLICT`)."""


# ---- SPEC-003 §4 Case Matrix — v2 가 더하는 거절들 ----
#
# 이 저장소의 관례를 그대로 따른다: **오류 코드는 예외 클래스가 갖고**, HTTP 상태는
# `entrypoints/http.py` 가 매핑하며, 본문은 사람이 읽는 문장이다. 아래 docstring 의 대문자 이름이
# SPEC 의 코드이고, 그 이름을 응답 본문에 새 봉투로 싣지 않는다 — 기존 계약을 깨는 변경이 된다.


class TaskParentCycle(TaskError):
    """자기 자신·조상을 부모로 지정했다 (SPEC-003 `WORK_PARENT_CYCLE`, 422)."""


class TaskDirectNesting(TaskError):
    """같은 담당자의 직접 작업 아래에 직접 작업을 두려 했다 (SPEC-003 `WORK_DIRECT_NESTING`, 409)."""


class TaskParentUnassigned(TaskError):
    """수락 전 요청 Task 아래에 하위를 만들려 했다 (SPEC-003 `WORK_PARENT_UNASSIGNED`, 409)."""


class TaskParentClosed(TaskError):
    """끝난 업무에 하위를 붙이려 했다 (SPEC-003 `WORK_PARENT_CLOSED`, 409 · 현행 계승)."""


class TaskChildrenUnfinished(InvalidTaskTransition):
    """취소 제외 미완결 하위가 남았는데 최종 완료·승인을 시도했다 (SPEC-003 `WORK_CHILDREN_UNFINISHED`, 409).

    **막는 하위의 이름을 본문에 낸다** — 무엇이 막는지 모르면 사람이 다음 걸음을 고를 수 없다.
    """

    def __init__(self, message: str, blocking: tuple[dict[str, str], ...] = ()) -> None:
        super().__init__(message)
        self.blocking = blocking


class TaskCancelRequiresAgreement(TaskError):
    """수락된 요청 Task 를 직접 취소하려 했다 (SPEC-003 `WORK_CANCEL_REQUIRES_AGREEMENT`, 409).

    요청자·담당자·관리자 **모두** 같다. 그 자리에서 취소로 가는 길은 합의 취소 하나다 (정책 V-19).
    """


class TaskAssignmentProposalExists(TaskError):
    """대기 중 담당 변경 제안이 이미 있다 (SPEC-003 `WORK_ASSIGNMENT_PROPOSAL_EXISTS`, 409)."""


class TaskAssignmentResponderOnly(TaskAccessDenied):
    """담당 제안의 수신자가 아닌 사람이 응답했다 (SPEC-003 `WORK_ASSIGNMENT_RESPONDER_ONLY`, 403)."""


class TaskProposalResponderOnly(TaskAccessDenied):
    """담당자가 아닌 사람이 취소·조건 변경 제안에 응답했다 (SPEC-003 `WORK_PROPOSAL_RESPONDER_ONLY`, 403)."""


class TaskProposalNotPending(TaskError):
    """이미 처리된 제안에 응답했다 (SPEC-003 `WORK_PROPOSAL_NOT_PENDING`, 409). 재전송이면 영수증이 먼저다."""


class TaskReopenParentDone(TaskError):
    """완료된 상위 아래의 하위를 재개하려 했다 (SPEC-003 `WORK_REOPEN_PARENT_DONE`, 409).

    **상위를 먼저 재개하라고 낸다** — 막혔다는 사실만으로는 다음 걸음이 보이지 않는다.
    """


class TaskReopenForbidden(TaskAccessDenied):
    """재개를 부를 수 있는 사람이 아니다 (SPEC-003 `WORK_REOPEN_FORBIDDEN`, 403).

    본인 업무는 담당자, 요청 업무는 요청자다. **배정 업무는 이 SPEC 이 정하지 않는다**(미정 M-3) —
    현행 동작을 그대로 둔다.
    """
