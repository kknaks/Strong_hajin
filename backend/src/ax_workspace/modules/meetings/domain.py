"""Meeting-owned errors, the six statuses, and the invariants that hold for every transport."""
from __future__ import annotations

from enum import StrEnum


class MeetingError(Exception):
    """The Meeting command is structurally invalid."""


class MeetingAccessDenied(MeetingError):
    """The caller may not mutate this Meeting."""


class MeetingNotFound(MeetingError):
    """The Meeting does not exist or is intentionally concealed from this principal."""


class MeetingVersionConflict(MeetingError):
    """A mutable Meeting identity changed before the command arrived."""


class MeetingStateConflict(MeetingError):
    """The command is well formed but the meeting is not in a status that allows it."""


class MeetingStaleWrite(MeetingError):
    """다른 탭이 먼저 저장했다 — 덮어쓰지 않고 지금 있는 것을 함께 돌려준다 (SPEC-004 §8-9).

    판정은 **안건 단위**다: 회의 전체가 아니라 그 안건이 그 사이에 저장됐는가를 본다.
    """

    def __init__(self, message: str, current: object) -> None:
        super().__init__(message)
        self.current = current


class MeetingStatus(StrEnum):
    """SPEC-004 §5.1. 화면 라벨(예정·진행 중·정리 중·완료·실패·취소됨)은 화면이 소유한다."""

    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    SUMMARIZING = "summarizing"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: 허용 전이. 「완료」에서 「정리 중」으로 돌아가는 길은 없다 — 재생성을 두지 않는다 (SPEC §5.1 · §10-19).
#: `cancelled → scheduled` 는 자동 취소가 풀리는 한 갈래뿐이다. 취소된 회의를 다시 시작하는 것은
#: 「풀림 → 시작」 두 걸음이고, 이 표는 그 두 걸음을 각각 허용한다.
MEETING_TRANSITIONS: frozenset[tuple[MeetingStatus, MeetingStatus]] = frozenset(
    {
        (MeetingStatus.SCHEDULED, MeetingStatus.IN_PROGRESS),
        (MeetingStatus.SCHEDULED, MeetingStatus.CANCELLED),
        (MeetingStatus.IN_PROGRESS, MeetingStatus.SUMMARIZING),
        (MeetingStatus.SUMMARIZING, MeetingStatus.DONE),
        (MeetingStatus.SUMMARIZING, MeetingStatus.FAILED),
        (MeetingStatus.FAILED, MeetingStatus.SUMMARIZING),
        (MeetingStatus.CANCELLED, MeetingStatus.SCHEDULED),
    }
)

#: 회의 정보(제목·일시·장소·참석자)를 고칠 수 있는 상태 (SPEC §3.1-7 · §10-18).
INFO_EDITABLE_STATUSES: frozenset[MeetingStatus] = frozenset({MeetingStatus.SCHEDULED, MeetingStatus.DONE})

MAX_AGENDAS_PER_MEETING = 20
AGENDA_TITLE_MAX_LENGTH = 100
#: 안건이 어디서 왔는가 — 기획이 정한 넷과 우리가 쓰는 「AI 정리」 하나 (사용자 결정 D38, 2026-09-11).
#:
#: * `manual`  — 사람이 직접 적었다
#: * `set`     — 세트: 같은 회의명으로 앞뒤 이어진 회의에서 **자동으로** 넘어왔다
#: * `carried` — 지난 회의에서 넘어왔다: **사람이** 지난 회의를 골라 불러왔다
#: * `derived` — 다른 회의에서 파생됐다
#: * `ai`      — 회의 중 배치나 합성이 세웠다
#:
#: `set` 과 `derived` 는 **값만 열어 둔 것**이고 아직 그것을 만드는 경로가 없다 (D38). 값을 먼저 여는
#: 이유는 저장된 글자가 나중에 뜻을 바꾸지 않게 하려는 것이다 — 스키마는 그대로다(문자열 열).
#: 합성의 「사람 안건 보존」은 계속 `source != "ai"` 로 가른다: 넷 모두 사람 쪽이다.
AGENDA_SOURCES: frozenset[str] = frozenset({"manual", "set", "carried", "derived", "ai"})
#: AI 가 세운 안건의 출처. 이 하나만 「사람이 세우지 않은 것」이다.
AI_AGENDA_SOURCE = "ai"
LINE_TRACKS: frozenset[str] = frozenset({"memo", "ai", "final"})


def parse_status(value: object) -> MeetingStatus:
    try:
        return MeetingStatus(str(value))
    except ValueError as error:
        raise MeetingError(f"unknown meeting status: {value!r}") from error


def ensure_transition(current: MeetingStatus | str, target: MeetingStatus | str) -> MeetingStatus:
    """Raise unless the meeting may walk from `current` to `target`; return the target it may walk to."""
    source = parse_status(current)
    destination = parse_status(target)
    if source == destination:
        raise MeetingStateConflict(f"meeting is already {destination.value}")
    if (source, destination) not in MEETING_TRANSITIONS:
        raise MeetingStateConflict(f"meeting cannot move from {source.value} to {destination.value}")
    return destination


def ensure_info_editable(current: MeetingStatus | str) -> None:
    """회의 정보 편집은 「예정」과 「완료」 둘에서만 열린다."""
    if parse_status(current) not in INFO_EDITABLE_STATUSES:
        raise MeetingStateConflict("meeting information may be edited only while scheduled or done")


def normalize_agenda_title(value: object) -> str:
    title = str(value or "").strip()
    if not title:
        raise MeetingError("agenda title is required")
    if len(title) > AGENDA_TITLE_MAX_LENGTH:
        raise MeetingError(f"agenda title must be at most {AGENDA_TITLE_MAX_LENGTH} characters")
    return title


def ensure_agenda_capacity(existing_count: int) -> None:
    if existing_count >= MAX_AGENDAS_PER_MEETING:
        raise MeetingStateConflict(f"a meeting holds at most {MAX_AGENDAS_PER_MEETING} agendas")


def ensure_agenda_source(value: object) -> str:
    source = str(value)
    if source not in AGENDA_SOURCES:
        raise MeetingError(f"agenda source must be one of {sorted(AGENDA_SOURCES)}")
    return source


def is_auto_cancellable(
    status: MeetingStatus | str, ends_at: object, now: object, *, has_record: bool, created_at: object = None
) -> bool:
    """미리 잡은 회의가 종료 시각까지 아무 기록도 없이 지나면 묻지 않고 취소된다 (SPEC §3.1-8 · `X-185`).

    **지난 날짜로 세운 회의는 이 규칙에 걸리지 않는다** — 「미리 잡은」이 조건이므로 세운 시각이 이미 종료 시각을
    지난 회의는 애초에 기다린 적이 없다. 그런 회의는 「예정」으로 남고 목록의 「지난」 구획에 선다 (`X-149`).
    """
    if parse_status(status) is not MeetingStatus.SCHEDULED or has_record:
        return False
    if ends_at is None or not now > ends_at:  # type: ignore[operator]
        return False
    return created_at is None or created_at < ends_at  # type: ignore[operator]


def is_auto_cancel_released(status: MeetingStatus | str, *, has_record: bool) -> bool:
    """기록이 생기면 취소가 풀린다 — 「취소됨」으로 남는 것은 비어 있는 동안뿐이다."""
    return parse_status(status) is MeetingStatus.CANCELLED and has_record


def pending_assignment_result(assignment_id: object, assignee_id: str, state: str) -> dict[str, str]:
    """Compatibility value retained while TaskAssignment consumes this helper."""
    return {"assignment_id": str(assignment_id), "assignee_id": assignee_id, "state": state}
