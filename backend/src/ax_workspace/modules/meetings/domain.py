"""Meeting-owned errors, the six statuses, and the invariants that hold for every transport."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from ax_workspace.modules.errors import ResourceNotFound


class MeetingError(Exception):
    """The Meeting command is structurally invalid."""


class MeetingAccessDenied(MeetingError):
    """The caller may not mutate this Meeting."""


class MeetingNotFound(MeetingError, ResourceNotFound):
    """The Meeting does not exist or is intentionally concealed from this principal."""


class MeetingVersionConflict(MeetingError):
    """A mutable Meeting identity changed before the command arrived."""


class MeetingStateConflict(MeetingError):
    """The command is well formed but the meeting is not in a status that allows it."""


class MeetingRangeIncomplete(MeetingError):
    """회의 목록에 `from`·`to` 중 하나만 왔다 (SPEC-004 `MEETING_RANGE_INCOMPLETE`, 422).

    **두 파라미터는 같이 온다.** 한쪽만으로는 「어느 기간」이 성립하지 않는다 — 없으면 기존 동작
    (`upcoming`/`past` + 커서) 그대로다. 뒤집힌 기간(`from > to`)도 같은 거절이다.
    """


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

#: **벌마다** 안건 20개까지다 — 합쳐서 20이 아니다 (SPEC-004 §4.0-3 · `X-100` 확장).
MAX_AGENDAS_PER_TRACK = 20
AGENDA_TITLE_MAX_LENGTH = 100

#: 회의록 **세 벌** (SPEC-004 §4.0 · D51). 안건과 줄이 **같은 이름을 쓴다** — 한 벌을 두 이름으로
#: 부르지 않는다 (§4.0-2).
#:
#: * `memo`  — 사람 벌. 회의를 만든 사람이 예약 모달과 메모 칸에서 쓴다 (§6)
#: * `ai`    — AI 벌. 회의 중 배치가 매 회차 **전량 교체**한다 (§7)
#: * `final` — 최종 벌. 종료 합성이 두 벌을 재료로 **새로 짓는다** (§8)
TRACK_MEMO = "memo"
TRACK_AI = "ai"
TRACK_FINAL = "final"
AGENDA_TRACKS: frozenset[str] = frozenset({TRACK_MEMO, TRACK_AI, TRACK_FINAL})
#: 줄의 벌은 안건의 벌과 **같은 집합**이다 (§4.2-9 · §4.0-2 「한 벌 한 이름」). 0.4.x 는 줄만 벌을
#: 갖고 있었고 그 이름이 `memo` 였다 — **안건의 벌 이름을 그 표기에 맞췄다**(사용자 결정 2026-09-14):
#: 줄이 이미 쓰던 이름을 그대로 두면 저장된 글자가 뜻을 바꾸지 않고 고칠 자리도 0 이다.
LINE_TRACKS: frozenset[str] = AGENDA_TRACKS
#: 원본 두 벌 — 최종 벌이 딛는 재료이고 계보(`merged_from`·`from_lines`)가 가리킬 수 있는 유일한 자리다.
ORIGIN_TRACKS: frozenset[str] = frozenset({TRACK_MEMO, TRACK_AI})

#: 안건이 어디서 왔는가 — 기획이 정한 넷이다 (사용자 결정 D38 · D51 이 좁혔다).
#:
#: * `manual`  — 사람이 직접 적었다
#: * `set`     — 세트: 같은 회의명으로 앞뒤 이어진 회의에서 **자동으로** 넘어왔다
#: * `carried` — 지난 회의에서 넘어왔다: **사람이** 지난 회의를 골라 불러왔다
#: * `derived` — 다른 회의에서 파생됐다
#:
#: `set` 과 `derived` 는 **값만 열어 둔 것**이고 아직 그것을 만드는 경로가 없다 (D38). 값을 먼저 여는
#: 이유는 저장된 글자가 나중에 뜻을 바꾸지 않게 하려는 것이다 — 스키마는 그대로다(문자열 열).
#:
#: **출처는 사람 벌 안의 출처다** (SPEC §4.1-2 · D51). 0.4.x 의 「AI 정리」(`ai`)는 **은퇴했다** —
#: 그 값이 있던 이유는 AI 가 사람과 같은 목록에 안건을 세웠기 때문이고, 벌이 갈렸으므로 「AI 가 세웠다」는
#: 출처가 아니라 **벌 자체**가 말한다. 어디서 왔는지를 말하는 것은 최종 벌에서 계보(§4.1-3)다.
AGENDA_SOURCES: frozenset[str] = frozenset({"manual", "set", "carried", "derived"})


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
    """**벌 하나**가 이고 있는 안건 수를 본다 — 세 벌을 합쳐서 세지 않는다 (SPEC §4.0-3)."""
    if existing_count >= MAX_AGENDAS_PER_TRACK:
        raise MeetingStateConflict(f"a meeting track holds at most {MAX_AGENDAS_PER_TRACK} agendas")


def ensure_agenda_track(value: object) -> str:
    """안건이 선 벌. 아는 셋 말고는 저장되지 않는다 (SPEC §4.0-2)."""
    track = str(value)
    if track not in AGENDA_TRACKS:
        raise MeetingError(f"agenda track must be one of {sorted(AGENDA_TRACKS)}")
    return track


def ensure_agenda_source(value: object | None, *, track: str) -> str | None:
    """출처는 **사람 벌만 갖는다** (SPEC §4.1-2). 다른 벌에 출처가 오면 거절한다 — 버리지 않는다:
    출처를 실은 AI·최종 안건은 벌 경계를 잘못 읽은 호출이고 조용히 지우면 그 오독이 남는다.
    """
    if track != TRACK_MEMO:
        if value is None:
            return None
        raise MeetingError(f"only the {TRACK_MEMO} track carries an agenda source")
    source = str(value)
    if source not in AGENDA_SOURCES:
        raise MeetingError(f"agenda source must be one of {sorted(AGENDA_SOURCES)}")
    return source


def ensure_line_track(value: object, *, agenda_track: str) -> str:
    """**줄의 벌과 그 줄이 매달린 안건의 벌은 언제나 같다** (SPEC §4.2-9 · §4.0-1).

    다른 벌의 안건 id 를 실은 줄은 거절한다 — 벌이 갈렸다는 것이 이 한 줄로 지켜진다.
    """
    track = str(value)
    if track not in LINE_TRACKS:
        raise MeetingError(f"line track must be one of {sorted(LINE_TRACKS)}")
    if track != agenda_track:
        raise MeetingError(f"a {track} line does not hang on a {agenda_track} agenda")
    return track


def surviving_lineage(claimed: object, *, known_ids: set[str]) -> list[str]:
    """계보는 **존재만 검증한다** — 없는 id 는 그 id 만 버리고 안건·줄 자체는 산다 (SPEC §8-6 · §4.2-10).

    맞는지는 검증하지 않는다: AI 의 자기보고라 서버가 확인할 방법이 없다. 검증 가능한 근거는 `evidence` 다.
    """
    if not isinstance(claimed, (list, tuple)):
        return []
    kept: list[str] = []
    for value in claimed:
        identifier = str(value or "").strip()
        if identifier and identifier in known_ids and identifier not in kept:
            kept.append(identifier)
    return kept


def validate_meeting_schedule(starts_at: datetime, ends_at: datetime) -> None:
    """Meeting times are an aware, increasing interval before persistence is involved."""
    if not isinstance(starts_at, datetime) or not isinstance(ends_at, datetime):
        raise MeetingError("meeting start and end are required")
    if starts_at.tzinfo is None or ends_at.tzinfo is None:
        raise MeetingError("meeting times must include a timezone")
    if starts_at >= ends_at:
        raise MeetingError("meeting start must be before end")


def normalize_optional_text(value: object, *, label: str, limit: int) -> str | None:
    """Treat an empty command field as absent and enforce its domain length."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > limit:
        raise MeetingError(f"{label} must be at most {limit} characters")
    return text


def normalize_external_attendees(values: list[object]) -> tuple[str, ...]:
    """External attendees are distinct display names, never member identities."""
    names: list[str] = []
    for value in values:
        name = str(value or "").strip()
        if not name:
            continue
        if len(name) > 100:
            raise MeetingError("external attendee name must be at most 100 characters")
        if name not in names:
            names.append(name)
    return tuple(names)


def normalize_agenda_order(value: object) -> int:
    try:
        order = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise MeetingError("agenda order must be a whole number") from error
    if order < 1:
        raise MeetingError("agenda order starts at 1")
    return order


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
