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


# ---- SPEC-001 §4 Case Matrix — WORK-003 이 더하는 거절들 ----
#
# **넷은 422 이고 둘은 409 다.** 가르는 기준은 기존 계약 그대로다: 입력이 틀린 것은 422,
# 「명령 자체는 말이 되는데 지금 그 업무의 상태가 받지 않는」 것은 409 다.


class TaskPredecessorProjectRequired(TaskError):
    """선행을 지정했는데 프로젝트가 비었다 (SPEC-001 `WORK_PREDECESSOR_PROJECT_REQUIRED`, 422).

    선행은 **같은 프로젝트 안에서만** 선다. 프로젝트가 없으면 「같은 프로젝트」라는 말이 성립하지 않는다.
    """


class TaskPredecessorProjectMismatch(TaskError):
    """다른 프로젝트의 업무를 선행으로 지정했다 (SPEC-001 `WORK_PREDECESSOR_PROJECT_MISMATCH`, 422)."""


class TaskPredecessorSelf(TaskError):
    """자기 자신을 선행으로 지정했다 (SPEC-001 `WORK_PREDECESSOR_SELF`, 422).

    **데이터베이스 CHECK 과 같은 사실**이다 — 어떤 상태에서도 참이 될 수 없으니 409 가 아니라 422 다.
    """


class TaskPredecessorDuplicate(TaskError):
    """같은 선행을 두 번 실어 보냈다 (SPEC-001 `WORK_PREDECESSOR_DUPLICATE`, 422)."""


class TaskPredecessorCycle(TaskError):
    """선행 관계가 서로를 기다리게 된다 (SPEC-001 `WORK_PREDECESSOR_CYCLE`, 422).

    **데이터베이스가 답할 수 없는 하나다** — 활성 변을 따라 걷는 일이라 application 이 답하고,
    검사와 저장이 **한 transaction** 에 있다 (§5 동시성).
    """


class TaskProjectLockedByPredecessors(TaskError):
    """남은 선행이 있는 업무의 프로젝트를 바꾸려 했다 (SPEC-001 `WORK_PROJECT_LOCKED_BY_PREDECESSORS`, 409).

    선행이 **같은 프로젝트 안**이라는 불변을 프로젝트 쪽에서 깨는 길이라 막는다. 출구는 하나다 —
    **선행을 먼저 비운다.** 하위가 상위를 따라 옮겨 가는 경로에도 같은 규칙이 걸린다.
    """


class TaskPredecessorsUnfinished(InvalidTaskTransition):
    """끝나지 않은 선행이 있는데 시작하거나 `시작 전 → 완료` 로 직행했다
    (SPEC-001 `WORK_PREDECESSORS_UNFINISHED`, 409).

    **하나의 코드다** — 시작을 막는 것과 직행을 막는 것에 같은 코드를 쓴다. 사람에게는 같은 사실이다.
    **미완 하위(`TaskChildrenUnfinished`)와 합치지 않는다**: 하위는 **완료**를, 선행은 **시작**을
    막는다. 한 코드로 합치면 무엇을 먼저 해야 하는지가 사라진다.

    **막는 선행의 이름을 본문에 낸다** — 미완 하위 거절이 이미 그 모양이다.
    """

    def __init__(self, message: str, blocking: tuple[dict[str, str], ...] = ()) -> None:
        super().__init__(message)
        self.blocking = blocking


class TaskApproverInvalid(TaskError):
    """승인자(화면 라벨 「결재자」)가 값으로 서지 못한다 (SPEC-001 `WORK_APPROVER_INVALID`, 422).

    재직 중이 아니거나 **담당자 본인**이다 — 자기 일을 자기가 확인하는 자리를 만들지 않는다.
    """


class TaskApproverLocked(TaskError):
    """`승인 대기` 뒤에 승인자를 바꾸려 했다 (SPEC-001 `WORK_APPROVER_LOCKED`, 409).

    이미 그 사람 앞에 판단이 놓였으므로, 바꾸면 누가 무엇에 답했는지가 어긋난다 (§5 권한).
    """


# ---- SPEC-004 §4 Case Matrix — 시간 배정이 더하는 거절들 ----
#
# 같은 기준으로 같은 자리에 선다: **입력이 틀린 것은 422**, 「명령 자체는 말이 되는데 지금 그 자원의
# 상태가 받지 않는」 것은 **409**, 관계가 없는 것은 **403**, 읽을 수 없는 것은 **404**다.
# 읽을 수 **있으나** 담당이 아닌 것은 403 이다 — 읽을 수 있다는 건 존재를 이미 아는 것이라
# 404 로 숨기면 「내 화면에 떠 있는 업무인데 없다고 한다」가 된다 (증보 K6).


class TaskScheduleForbidden(TaskAccessDenied):
    """읽을 수는 있으나 그 업무의 **활성 담당자가 아니다** (SPEC-004 `TASK_SCHEDULE_FORBIDDEN`, 403).

    판정 근거는 **그 업무의 활성 담당 관계**다 — `task.self_manage` 봉투가 아니다 (증보 K5).
    그 봉투는 전역 권한 비트 하나라 **읽을 수 있는 남의 업무에도 배정이 생긴다**.
    """


class TaskScheduleOutOfRange(TaskError):
    """업무 기간 밖의 날에 배정하려 했다 (SPEC-004 `TASK_SCHEDULE_OUT_OF_RANGE`, 422).

    **본문에 그 기간을 적는다** — 그 「기간」은 정규화 구간이다 (증보 K11·K14). 뒤집힌 업무면
    `start_date`~`due_date` 가 아니라 `[min, max]` 를 적는다.
    """


class TaskScheduleTaskUnscheduled(TaskError):
    """기간이 없는 업무에 배정하려 했다 (SPEC-004 `TASK_SCHEDULE_TASK_UNSCHEDULED`, 422).

    **한쪽만 있는 것은 여기 들어오지 않는다** — 남은 한쪽을 그 날 하루로 읽는다 (증보 K7).
    """


class TaskScheduleInvalidRange(TaskError):
    """`starts_at >= ends_at` (SPEC-004 `TASK_SCHEDULE_INVALID_RANGE`, 422).

    **데이터베이스 CHECK 과 같은 사실**이다 — 어떤 상태에서도 참이 될 수 없으니 409 가 아니라 422 다.
    """


class TaskScheduleTaskClosed(TaskError):
    """끝난 업무(`DONE`·`CANCELLED`)에 배정하려 했다 (SPEC-004 `TASK_SCHEDULE_TASK_CLOSED`, 409).

    **재전송이면 영수증이 먼저다** (증보 K12).
    """


class TaskScheduleDayTaken(TaskError):
    """그 날에 **살아 있는 배정이 이미 있다** (SPEC-004 `TASK_SCHEDULE_DAY_TAKEN`, 409).

    **생성은 덮어쓰지 않는다** (증보 K10). 그 날의 시각을 바꾸는 것은 `PATCH` 의 일이다.
    **같은 멱등 키의 재전송은 이 거절에 걸리지 않는다** — 영수증이 먼저다 (증보 K12).
    정상 흐름에서는 보이지 않는다: 화면은 그 날의 `schedule_id` 를 들고 있어 곧바로 `PATCH` 를 부른다.
    """


class TaskScheduleVersionConflict(TaskError):
    """그 **배정 자신의** 회차가 어긋났다 (SPEC-004 `VERSION_CONFLICT`, 409 · 증보 K8).

    업무의 회차가 아니다 — 배정만 바뀌는데 업무 회차를 올리면 다른 화면의 낙관적 잠금이 깨진다.
    **업무 회차 경합(`InvalidTaskTransition`)과 다른 예외**인 이유는 상태가 다르기 때문이다:
    기존 업무 표면의 계약(422)을 이 SPEC 이 바꾸지 않는다.
    """


class CalendarRangeInvalid(TaskError):
    """합본 조회에 `from`·`to` 가 없거나 뒤집혔다 (SPEC-004 `CALENDAR_RANGE_REQUIRED`, 422).

    화면이 항상 채워 보내므로 사용자에게 보이지 않는다.
    """
