"""THE CONNECT(TDL) 예약 시스템 어댑터 — 이 파일 하나만 그쪽 주소와 계정을 안다 (SCAX-WP-007).

`mediness-app/back/app/clients/the_connect.py`(kernel HttpTdlGateway 포팅)를 이 리포의 결로 옮긴 것이다.
의도적인 차이 —

1. **세션 쿠키는 메모리에만 산다.** 저 쪽은 DB(`tdl_sessions`)를 SoT 로 두지만 여기서는 어댑터 하나가
   프로세스 수명만큼 들고 있으면 된다 — 예약은 사람 한 번의 [만들기]에 한 번 도는 일이다.
2. **방을 대신 고르지 않는다.** 사람이 고른 방이 차 있으면 그 자리에서 다른 방을 잡지 않고,
   **그 시간에 가능한 방 목록을 실어** `RoomUnavailable` 로 돌려준다. 대체할지 거절할지는 위층의
   판단이다(D36 자동 대체) — 어댑터는 사실만 나른다.
3. 계정·비밀번호·쿠키·그쪽 응답 본문은 **로그에도 예외 문구에도 싣지 않는다.**
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import logging
import re
import threading
from typing import Any
from urllib import error as urlerror, request as urlrequest
import http.cookiejar

from ax_workspace.modules.meetings.rooms import (
    MeetingRoom,
    ReservationBooked,
    ReservationRequest,
    RoomAuthFailed,
    RoomGatewayUnavailable,
    RoomOutcomeUnknown,
    RoomUnavailable,
)

logger = logging.getLogger(__name__)

#: 회의실 목록은 자주 바뀌지 않는다 — 매 요청마다 묻지 않는다.
ROOMS_CACHE_SECONDS = 300

#: 공용 회의실의 위쪽 경계. 그 위 번호는 스튜디오와 대표실이라 예약 대상이 아니다.
#: 2026-09-11 실측: id 1~7 이 「회의실 1~7」, 11 스튜디오, 12~15 대표실.
PUBLIC_ROOM_MAX_ID = 7
_STUDIO = re.compile(r"스튜디오|studio", re.IGNORECASE)


def _to_minutes(value: str) -> int:
    hour, _, minute = value.partition(":")
    return int(hour) * 60 + int(minute)


def _overlaps(start_a: str, end_a: str, start_b: str, end_b: str) -> bool:
    return _to_minutes(start_a) < _to_minutes(end_b) and _to_minutes(start_b) < _to_minutes(end_a)


class TheConnectGateway:
    """실연동 gateway. 요청 단위로 가볍고, 로그인 쿠키만 인스턴스가 들고 있는다."""

    def __init__(
        self,
        *,
        base_url: str,
        email: str,
        password: str,
        company_id: int,
        notify: bool,
        timeout_seconds: float,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._email = email
        self._password = password
        self._company_id = company_id
        self._notify = notify
        self._timeout = timeout_seconds
        self._lock = threading.Lock()
        self._jar = http.cookiejar.CookieJar()
        self._opener = urlrequest.build_opener(urlrequest.HTTPCookieProcessor(self._jar))
        self._authenticated = False
        self._rooms: list[MeetingRoom] = []
        self._rooms_read_at: datetime | None = None

    # ---- 공개 계약 (MeetingRoomGateway) ----

    def rooms(self) -> list[MeetingRoom]:
        with self._lock:
            now = datetime.now(UTC)
            fresh = self._rooms_read_at is not None and (now - self._rooms_read_at).total_seconds() < ROOMS_CACHE_SECONDS
            if fresh and self._rooms:
                return list(self._rooms)
            rows = self._json("GET", "/api/rooms")
            self._rooms = [
                MeetingRoom(room_id=int(row["id"]), name=str(row["name"]), capacity=int(row.get("capacity") or 0))
                for row in rows
                if int(row["id"]) <= PUBLIC_ROOM_MAX_ID and not _STUDIO.search(str(row.get("name") or ""))
            ]
            self._rooms_read_at = now
            return list(self._rooms)

    def available(self, date: str, start: str, end: str) -> list[MeetingRoom]:
        """그 시간에 비어 있는 공용 회의실. **한 번의 예약 조회로 전부 가른다** — 방마다 묻지 않는다."""
        busy = {
            int(row.get("room_id") or 0)
            for row in self._json("GET", f"/api/reservations?date={date}")
            if _overlaps(start, end, str(row["start_time"]), str(row["end_time"]))
        }
        return [room for room in self.rooms() if room.room_id not in busy]

    def members(self) -> list[dict[str, str]]:
        return [
            {"name": str(row["name"]), "email": str(row["email"])}
            for row in self._json("GET", "/api/members")
            if row.get("name") and row.get("email")
        ]

    def create(self, request: ReservationRequest) -> ReservationBooked:
        """자리를 잡기 전에 **그 방이 그 시간에 비어 있는지 먼저 본다** — 겹쳐 잡히는 것을 막는다.

        찼으면 그 시간에 **가능한 방 목록을 실어** 거절한다 — 부르는 쪽이 곧바로 대체를 고를 수 있게 (D36).
        """
        room = self._room(request.room_id)
        free = self.available(request.date, request.start, request.end)
        if all(one.room_id != request.room_id for one in free):
            raise RoomUnavailable("그 시간에 그 회의실은 이미 예약돼 있습니다", available=tuple(free))
        created = self._json("POST", "/api/reservations", self._body(request, room), expect=(200, 201))
        external_id = str(created.get("id") or "")
        if not external_id:
            raise RoomGatewayUnavailable("예약 시스템이 예약 번호를 주지 않았습니다")
        return ReservationBooked(external_id=external_id, room_id=room.room_id, room_name=room.name)

    def update(self, external_id: str, *, date: str, start: str, end: str, room_id: int | None = None) -> None:
        """시간·회의실만 옮긴다. **참석자는 절대 싣지 않는다.**

        참고 구현의 실측(2026-08-07): PUT 에 `participants` 를 어떤 모양으로 실어도 200 을 주면서
        참석자를 지우거나 이메일을 비워 버린다. 실패 신호가 없어 더 위험하다 — 그래서 막는다.
        """
        body: dict[str, Any] = {"date": date, "start_time": start, "end_time": end}
        if room_id is not None:
            body["room_id"] = room_id
        self._json("PUT", f"/api/reservations/{external_id}", body)

    def cancel(self, external_id: str) -> None:
        self._json("DELETE", f"/api/reservations/{external_id}")

    # ---- 내부 ----

    def _body(self, request: ReservationRequest, room: MeetingRoom) -> dict[str, Any]:
        """`POST /api/reservations` body — 참고 구현의 `build_create_reservation_body` 와 같은 모양이다.

        `participants` 는 **콤마로 이은 이메일**이고 `attendees` 는 표시 문자열이다. 사외 참석자가
        `attendees` 로 가는 이유는 §1 조사(외부인 등록 엔드포인트 없음)에 있다.
        """
        booker = self._email.strip().lower()
        participants = [email for email in request.participant_emails if email.strip().lower() != booker]
        return {
            "room_id": room.room_id,
            "date": request.date,
            "start_time": request.start,
            "end_time": request.end,
            "company_id": self._company_id,
            "title": request.title,
            "password": "login",
            "attendees": ", ".join(request.display_names),
            "booker_email": self._email,
            "participants": ",".join(participants),
            "notify": self._notify,
        }

    def _room(self, room_id: int) -> MeetingRoom:
        for room in self.rooms():
            if room.room_id == room_id:
                return room
        raise RoomUnavailable("고른 회의실이 예약 목록에 없습니다")

    def _json(self, method: str, path: str, body: dict[str, Any] | None = None, *, expect: tuple[int, ...] = (200,)) -> Any:
        self._authenticate()
        status, payload = self._call(method, path, body)
        if status == 401 or status == 403:
            # 쿠키가 죽었을 수 있다 — 한 번만 다시 로그인하고 같은 요청을 되돌린다.
            self._authenticated = False
            self._authenticate()
            status, payload = self._call(method, path, body)
        if status not in expect:
            # **응답 본문을 싣지 않는다** — 그쪽이 무엇을 돌려주든 계정·참석자가 섞여 있을 수 있다.
            raise RoomGatewayUnavailable(f"예약 시스템이 요청을 받지 않았습니다 (HTTP {status})")
        return payload

    def _authenticate(self) -> None:
        if self._authenticated:
            return
        if not self._email or not self._password:
            raise RoomAuthFailed("예약 시스템 계정이 설정되어 있지 않습니다")
        status, _ = self._call("POST", "/api/auth/login", {"email": self._email, "password": self._password, "remember": True})
        if status != 200:
            raise RoomAuthFailed(f"예약 시스템 로그인이 거절됐습니다 (HTTP {status})")
        self._authenticated = True

    def _call(self, method: str, path: str, body: dict[str, Any] | None) -> tuple[int, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urlrequest.Request(
            f"{self._base}{path}", data=data, method=method, headers={"Content-Type": "application/json"}
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                return response.status, _decoded(response.read())
        except urlerror.HTTPError as error:
            # 본문은 읽어 버리고 상태만 들고 간다 — 어디에도 남기지 않는다.
            error.read()
            return int(error.code), None
        except Exception as error:  # noqa: BLE001 — 네트워크·시간 초과는 예약의 실패이지 회의의 실패가 아니다
            logger.warning("예약 시스템에 닿지 못했습니다: %s", type(error).__name__)
            if method in {"POST", "PUT", "DELETE"} and path.startswith("/api/reservations"):
                raise RoomOutcomeUnknown("예약 시스템 처리 결과를 확인해야 합니다") from error
            raise RoomGatewayUnavailable("예약 시스템에 닿지 못했습니다") from error


def _decoded(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def rooms_cache_is_stale(read_at: datetime | None, now: datetime) -> bool:
    """캐시 판정을 한 자리에 둔다 — 시험이 시계를 들고 이 규칙만 확인할 수 있게."""
    return read_at is None or (now - read_at) >= timedelta(seconds=ROOMS_CACHE_SECONDS)
