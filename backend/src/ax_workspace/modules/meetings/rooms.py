"""회의실 예약 — 회의를 세울 때 사옥 예약 시스템에 자리를 함께 잡는 자리 (SCAX-WP-007).

못박는 것 —

1. **자리를 못 잡으면 회의도 서지 않는다** (사용자 결정 D36, 2026-09-11 — 앞선 「실패해도 회의는
   만든다」를 뒤집은 것이다). 회의실을 고른 사람은 그 방을 쓰려고 회의를 잡는 것이므로, 방 없는
   회의가 조용히 남는 쪽이 더 나쁘다. 그래서 **예약을 먼저 하고 성공한 뒤에 회의를 세운다.**
   다만 고른 방이 찼을 때 곧장 거절하지는 않는다 — **조건에 맞는 다른 방으로 자동 대체**하고,
   대체됐다는 사실을 응답에 실어 화면이 알린다. 대체할 방도 없을 때만 거절이다.
2. **사내와 사외는 다른 자리로 간다.** 사내 참석자는 예약 시스템의 회사 계정(이메일)으로 실리고,
   사외 참석자는 계정이 없으므로 **표시 문자열**에 이름으로 실린다 — 그 시스템에 외부인을 등록하는
   자리가 없다는 것을 조사로 확인했다(§1).
3. **계정과 외부 식별자는 밖으로 나가지 않는다.** 화면에 내는 것은 상태·회의실 이름·사유 셋뿐이다.
4. 이 모듈은 **무엇을 보낼지**만 안다. 어떻게 보내는지(HTTP·쿠키·재시도)는 어댑터가 소유한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo

#: 사옥이 선 자리의 시간대. 예약 시스템은 날짜와 시:분을 지역 시각으로 읽는다 — UTC 로 보내면 아홉 시간이 밀린다.
OFFICE_TIMEZONE = ZoneInfo("Asia/Seoul")

#: 예약에 붙일 제목이 없을 때. 예약 시스템 목록에서 빈칸으로 서지 않게 한다.
UNTITLED_RESERVATION = "제목 없는 회의"

#: 예약 상태. D36 부터 **새로 쓰이는 것은 둘뿐이다** — 자리를 못 잡으면 회의가 아예 서지 않으므로
#: `failed` 인 예약이 새로 생기지 않는다. 그 이름은 D36 이전에 저장된 행을 되읽기 위해 남겨 둔다.
STATUS_BOOKED = "booked"
STATUS_CANCELLED = "cancelled"
STATUS_FAILED = "failed"
STATUS_NEEDS_VERIFICATION = "needs_verification"


class RoomReservationError(RuntimeError):
    """예약이 되지 않은 **예상 가능한** 실패. 사유는 사람이 읽을 한 줄이다.

    거절은 화면에서 막다른 길이면 안 된다 — 그래서 실패가 **그 시간에 가능한 방 목록**을 이고 간다.
    사람이 사유만 읽고 창을 닫는 대신 곧바로 다른 방을 고를 수 있다 (D36-2).
    닿지도 못한 실패(인증·연결)는 목록을 알 길이 없으므로 비어 있다.
    """

    reason = "room_reservation_failed"

    def __init__(self, message: str = "", *, available: tuple["MeetingRoom", ...] = ()) -> None:
        super().__init__(message or self.reason)
        self.available = available


class RoomBookingRefused(RoomReservationError):
    """**회의를 만들지 않았다** — 자리를 못 잡았기 때문이다 (D36-2).

    바깥 표면(HTTP)이 읽는 하나의 거절이다: 무엇 때문인지(`reason`)와, 그 시간에 **대신 고를 수 있는
    방 목록**을 함께 이고 있어 사람이 창을 닫지 않고 이어서 고를 수 있다.
    """

    def __init__(self, cause: RoomReservationError, available_rooms: list[dict[str, object]]) -> None:
        super().__init__(str(cause))
        self.reason = cause.reason
        self.available_rooms = available_rooms


class RoomUnavailable(RoomReservationError):
    """그 시간에 그 회의실이 이미 찼다. 사람이 다른 방이나 다른 시간을 고르면 된다."""

    reason = "room_unavailable"


class RoomAuthFailed(RoomReservationError):
    """예약 시스템에 로그인하지 못했다 — 계정 미설정이거나 거절이다. **값은 사유에 담지 않는다.**"""

    reason = "reservation_auth_failed"


class RoomGatewayUnavailable(RoomReservationError):
    """예약 시스템에 닿지 못했다 — 네트워크·시간 초과·그쪽 오류."""

    reason = "reservation_unavailable"


class RoomOutcomeUnknown(RoomReservationError):
    """The transport ended after a mutation may have reached the reservation provider."""

    reason = "reservation_needs_verification"

    def __init__(
        self,
        message: str = "",
        *,
        room_id: int | None = None,
        room_name: str | None = None,
        replaced: bool = False,
        requested_room_name: str | None = None,
    ) -> None:
        super().__init__(message)
        self.room_id = room_id
        self.room_name = room_name
        self.replaced = replaced
        self.requested_room_name = requested_room_name


class RoomCreationIdempotencyRequired(RoomReservationError):
    """A room-create mutation needs a stable caller key before it can leave the service."""

    reason = "reservation_idempotency_required"


class RoomCreationIdempotencyConflict(RoomReservationError):
    """One caller key cannot name two different meeting creation payloads."""

    reason = "reservation_idempotency_conflict"


@dataclass(frozen=True, slots=True)
class MeetingRoom:
    """고를 수 있는 회의실 하나. 화면이 읽는 것은 이 셋뿐이다."""

    room_id: int
    name: str
    capacity: int

    def view(self) -> dict[str, object]:
        return {"room_id": self.room_id, "name": self.name, "capacity": self.capacity}


@dataclass(frozen=True, slots=True)
class ReservationRequest:
    """예약 한 건에 보낼 값. **여기서 이미 사내와 사외가 갈려 있다.**"""

    room_id: int
    date: str
    start: str
    end: str
    title: str
    #: 사내 참석자 — 예약 시스템 계정 이메일. 계정을 못 찾은 사람은 여기 오지 못한다.
    participant_emails: tuple[str, ...] = ()
    #: 표시 문자열에 실을 이름들 — 만든 사람, 그리고 **계정이 없는 사외 참석자**.
    display_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReservationBooked:
    """자리가 잡혔다. `external_id` 는 나중에 바꾸고 거두기 위한 것이고 **화면에 내지 않는다**."""

    external_id: str
    room_id: int
    room_name: str


@dataclass(frozen=True, slots=True)
class RoomReservation:
    """회의가 이고 있는 예약의 현재 상태. 저장되는 모양이자 화면에 내는 모양의 원본이다."""

    status: str
    room_id: int | None = None
    room_name: str | None = None
    external_id: str | None = None
    reason: str | None = None
    #: 고른 방이 차서 **다른 방으로 잡혔는가**. 참이면 `requested_room_name` 이 원래 고른 방이다 (D36-1).
    replaced: bool = False
    requested_room_name: str | None = None

    def stored(self) -> dict[str, object | None]:
        """원장에 남기는 모양 — 외부 식별자가 여기 있다. 밖으로는 `view()` 만 나간다."""
        return {
            "status": self.status,
            "room_id": self.room_id,
            "room_name": self.room_name,
            "external_id": self.external_id,
            "reason": self.reason,
            "replaced": self.replaced,
            "requested_room_name": self.requested_room_name,
        }

    def view(self) -> dict[str, object | None]:
        """화면이 읽는 모양. **외부 식별자도 계정도 나가지 않는다** — 상태와 방 이름과 사유, 그리고
        고른 방이 아닌 다른 방으로 잡혔다면 그 사실과 원래 고른 방 이름이다 (D36-1)."""
        return {
            "status": self.status,
            "room_name": self.room_name,
            "reason": self.reason,
            "replaced": self.replaced,
            "requested_room_name": self.requested_room_name,
        }

    @classmethod
    def restored(cls, stored: object) -> "RoomReservation | None":
        """저장된 값을 되읽는다. 모양이 아니면 없는 것으로 친다 — 회의를 읽다 넘어지지 않는다."""
        if not isinstance(stored, dict) or not stored.get("status"):
            return None
        room_id = stored.get("room_id")
        return cls(
            status=str(stored["status"]),
            room_id=int(room_id) if isinstance(room_id, int | str) and str(room_id).isdigit() else None,
            room_name=_text(stored.get("room_name")),
            external_id=_text(stored.get("external_id")),
            reason=_text(stored.get("reason")),
            replaced=bool(stored.get("replaced")),
            requested_room_name=_text(stored.get("requested_room_name")),
        )


def _text(value: object) -> str | None:
    return str(value) if isinstance(value, str) and value.strip() else None


class MeetingRoomGateway(Protocol):
    """예약 시스템 경계. 시험은 여기에 대역을 끼운다 — 실제 호출은 조립층 뒤에만 있다."""

    def rooms(self) -> list[MeetingRoom]:
        """고를 수 있는 회의실 목록."""

    def available(self, date: str, start: str, end: str) -> list[MeetingRoom]:
        """그 시간에 **비어 있는** 공용 회의실만. 정원 판정은 하지 않는다 — 그것은 도메인의 몫이다."""

    def members(self) -> list[dict[str, str]]:
        """예약 시스템이 아는 사람들 — `{"name", "email"}`. 사내 참석자를 계정에 잇는 근거다."""

    def create(self, request: ReservationRequest) -> ReservationBooked:
        """자리를 잡는다. 잡히지 않으면 `RoomReservationError`."""

    def update(self, external_id: str, *, date: str, start: str, end: str, room_id: int | None = None) -> None:
        """시간·회의실을 옮긴다. **참석자는 싣지 않는다** — 참고 구현의 실측(PUT 이 참석자를 망가뜨림)이다."""

    def cancel(self, external_id: str) -> None:
        """자리를 거둔다."""


@dataclass(frozen=True, slots=True)
class Attendee:
    """예약에 실을 사람 하나 — 우리 쪽 이름과, 있으면 이메일."""

    name: str
    email: str | None = None


@dataclass(slots=True)
class ParticipantMapping:
    """사내 참석자를 예약 시스템 계정에 이은 결과. **못 이은 사람도 남긴다** — 조용히 빠지지 않게."""

    emails: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)


def map_participants(attendees: list[Attendee], members: list[dict[str, str]]) -> ParticipantMapping:
    """사내 참석자 → 회사 계정. **이메일 일치가 먼저, 없으면 이름 일치다.**

    우리 명부의 이메일은 데모 도메인이라 실제 회사 계정과 어긋나는 일이 흔하다 — 그때 사람을 통째로
    버리지 않고 이름으로 한 번 더 찾는다. 그래도 없으면 `unmatched` 로 남아 표시 문자열로 간다.
    """
    by_email = {str(row.get("email", "")).strip().lower(): row for row in members if row.get("email")}
    by_name = {str(row.get("name", "")).strip(): row for row in members if row.get("name")}
    mapping = ParticipantMapping()
    for attendee in attendees:
        email = (attendee.email or "").strip().lower()
        found = by_email.get(email) if email else None
        if found is None:
            found = by_name.get(attendee.name.strip())
        if found is None:
            mapping.unmatched.append(attendee.name)
            continue
        address = str(found["email"]).strip().lower()
        if address not in mapping.emails:
            mapping.emails.append(address)
    return mapping


def local_slot(starts_at: datetime, ends_at: datetime) -> tuple[str, str, str]:
    """회의 시각을 예약 시스템이 읽는 `(날짜, 시작, 끝)` 로 옮긴다 — **사옥 지역 시각**이다.

    UTC 로 보내면 아홉 시간이 밀린 자리를 잡는다. 환산은 한 곳에만 둔다.
    """
    local_start = starts_at.astimezone(OFFICE_TIMEZONE)
    local_end = ends_at.astimezone(OFFICE_TIMEZONE)
    return local_start.date().isoformat(), local_start.strftime("%H:%M"), local_end.strftime("%H:%M")


def build_reservation(
    *,
    room_id: int,
    starts_at: datetime,
    ends_at: datetime,
    title: str | None,
    booker_name: str,
    participant_emails: tuple[str, ...],
    outside_names: tuple[str, ...],
) -> ReservationRequest:
    """회의 하나를 예약 한 건으로 옮긴다.

    **사외 참석자는 표시 문자열로 간다.** 예약 시스템에 외부인을 등록하는 자리가 없어서다(§1 조사) —
    계정 없는 사람을 `participants` 에 이름으로 밀어 넣으면 그쪽이 이메일 빈 참석자를 만들어
    조회에서 사라진다. 이름이 보이는 자리에 두는 편이 사실에 가깝다.
    """
    date, start, end = local_slot(starts_at, ends_at)
    names = [name.strip() for name in (booker_name, *outside_names) if name and name.strip()]
    return ReservationRequest(
        room_id=room_id,
        date=date,
        start=start,
        end=end,
        title=(title or "").strip() or UNTITLED_RESERVATION,
        participant_emails=tuple(participant_emails),
        display_names=tuple(dict.fromkeys(names)),
    )


def headcount(*, inside: int, outside: int) -> int:
    """예약이 앉혀야 할 사람 수 — 사내 참석자 + 사외 참석자다.

    `inside` 는 **만든 사람을 이미 포함한다**(회의를 만든 사람은 언제나 참석자다). 여기서 한 번 더
    더하면 방이 한 자리씩 커진다.
    """
    return max(1, inside + outside)


def choose_replacement(available: list[MeetingRoom], *, people: int, exclude_room_id: int | None = None) -> MeetingRoom | None:
    """대체할 방 하나 — **정원이 인원을 감당하는 것 중 가장 작은 방**이다 (D36-1).

    큰 방부터 주면 여섯 명이 12인실을 차지해 뒤에 오는 회의가 갈 곳을 잃는다. 정원을 채우는
    가장 작은 방이 남는 자리를 가장 적게 버린다. 정원이 모자라면 후보가 아니다 — 앉을 수 없는
    방을 대신 잡아 주는 것은 도움이 아니다.
    """
    fitting = [
        room
        for room in available
        if room.room_id != exclude_room_id and room.capacity >= people
    ]
    if not fitting:
        return None
    return min(fitting, key=lambda room: (room.capacity, room.room_id))


def failure_reason(error: Exception) -> str:
    """실패를 화면이 읽는 **한 낱말**로 줄인다 — 그쪽 응답 본문도 계정도 새지 않는다."""
    if isinstance(error, RoomReservationError):
        return error.reason
    return RoomReservationError.reason
