"""시간이 겹치는가 — **한 자리에서만 답하는 규칙** (SPEC-004 §2.9 · DEC-003 증보 8 K22).

이 파일은 **순수**하다. 저장소도 HTTP 도 모르고, 시각 둘과 날짜 하나만 받아 답한다.
답하는 것은 셋이다.

1. **반열림 `[시작, 끝)`.** 경계가 닿는 것은 **겹침이 아니다** — 10:00–11:00 과 11:00–12:00 은
   둘 다 선다. **새로 정한 규칙이 아니다**: `platform/meetings.py` 의 `meetings_visible_to`
   docstring 이 이미 「창은 반열림 `[시작, 끝)` 이다」로 같은 말을 적어 뒀다.
2. **타입이 둘이라 축을 하나로 모은다.** 회의는 `timestamptz`, 배정은 `Date` + `Time` 이다.
   **사무실 시간대(Asia/Seoul)의 벽시계**를 그 시각이 뜻하는 **순간**으로 옮겨 비교한다 —
   서버의 TZ 설정에 기대지 않는다.
3. **자정을 넘는 회의를 날짜별로 쪼갠다.** 배정은 자정을 넘을 수 없지만(`Date` + `Time` + `Time`)
   회의는 넘는다. 쪼개지 않으면 23:00~01:00 회의가 **다음 날에는 없는 것**이 되어 00:30 배정이
   통과한다.

**왜 이 규칙이 여기 모여 있는가.** K22 가 「읽는 문은 하나다」로 못 박았다 — 배정 쪽과 회의 쪽이
각자 판정하면 **두 규칙이 된다.** 문은 `platform/time_blocks.py` 하나이고, 그 문이 쓰는 규칙이 여기다.

**여기에 없는 것.** 「누구의 시간인가」와 「무엇을 조회하는가」는 그 문의 몫이다. 그리고 **제목·내용은
이 파일을 지나지 않는다** — 남의 시간은 busy/free 만 읽는다 (K22).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


#: 사무실의 시간대. `platform/work_tasks.py` 의 `BUSINESS_TIMEZONE` 과 같은 값이고 같은 이유다 —
#: 「그 날」은 UTC 달력의 날이 아니라 사람이 출근하는 날이다.
OFFICE_TIMEZONE = ZoneInfo("Asia/Seoul")

#: 블록의 출처 둘. **표는 둘이어도 문은 하나다** (K22).
TASK_SCHEDULE_BLOCK = "task_schedule"
MEETING_BLOCK = "meeting"

#: 구간 — 반열림 `[시작, 끝)` 의 순간 둘. 회의 조회의 `overlapping` 인자와 같은 모양이다.
TimeWindow = tuple[datetime, datetime]


@dataclass(frozen=True, slots=True)
class TimeSegment:
    """사무실 달력의 **하루에 들어맞게 자른** 구간. 자정을 넘는 회의가 이 모양으로 쪼개진다."""

    on_date: date
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True, slots=True)
class TimeBlock:
    """누군가의 시간을 **차지하고 있는 한 칸** — busy 라는 사실과 그 출처까지다.

    **제목도 내용도 없다** (K22). 거절 문구가 말할 수 있는 것은 **이름까지**이고, 그 이름은
    `member_id` 로 조회하는 것이지 이 블록이 실어 오는 것이 아니다.
    """

    member_id: str
    on_date: date
    starts_at: datetime
    ends_at: datetime
    source: str
    source_id: str

    @property
    def window(self) -> TimeWindow:
        return (self.starts_at, self.ends_at)


def aware(value: datetime) -> datetime:
    """저장된 값을 순간으로 읽는다. **tz 를 잃은 값은 UTC 다** — SQLite 가 그렇게 돌려준다.

    `platform/work_tasks.py` 의 `business_date` 가 같은 결을 쓴다. 로컬 벽시계로 읽지 않는다:
    그러면 같은 행이 기기의 TZ 설정에 따라 다른 시간에 서게 된다.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def office_instant(on_date: date, at: time) -> datetime:
    """사무실 벽시계 한 점이 뜻하는 **순간**. `24:00` 은 `time` 으로 표현되지 않으므로 여기 오지 않는다."""
    return datetime.combine(on_date, at, tzinfo=OFFICE_TIMEZONE)


def office_span(on_date: date, starts_at: time, ends_at: time) -> TimeWindow:
    """배정 한 칸(`Date` + `Time` + `Time`)이 차지하는 구간. **자정을 넘지 않는다.**"""
    return (office_instant(on_date, starts_at), office_instant(on_date, ends_at))


def office_day_window(span_from: date, span_to: date) -> TimeWindow:
    """사무실 달력의 날 구간이 차지하는 구간 — **마지막 날의 다음 자정까지**다.

    `span_to` 의 자정에서 끊으면 마지막 날이 통째로 빠진다.
    """
    return (office_instant(span_from, time.min), office_instant(span_to + timedelta(days=1), time.min))


def office_dates_in(window: TimeWindow) -> tuple[date, date]:
    """그 구간이 닿는 사무실 달력의 날 — `Date` 열만 가진 표를 좁힐 때 쓴다.

    **끝은 반열림이라 한 순간을 뺀 뒤 읽는다** — 자정에 끝나는 창이 다음 날을 끌어오지 않는다.
    빈 구간(`시작 == 끝`)은 시작 날 하루로 읽는다: 조회를 좁히는 용도이고 판정은 `overlaps` 가 한다.
    """
    window_from, window_to = aware(window[0]), aware(window[1])
    first = window_from.astimezone(OFFICE_TIMEZONE).date()
    last_instant = window_to if window_to <= window_from else window_to - timedelta(microseconds=1)
    return (first, max(first, last_instant.astimezone(OFFICE_TIMEZONE).date()))


def overlaps(one: TimeWindow, other: TimeWindow) -> bool:
    """**반열림 `[시작, 끝)`.** 경계가 닿는 것은 겹침이 아니다.

    **길이 0 인 구간은 아무것도 담지 않으므로 아무것과도 겹치지 않는다.** 배정은 `starts_at < ends_at`
    을 강제하지만(`work/schedule.py` · DB CHECK) **이미 저장된 회의 행은 그 보장을 받지 않는다** —
    검증은 새 쓰기에만 걸렸고 기존 데이터는 그대로 두므로(K22), 그런 행 하나가 남을 막지 않게 한다.
    """
    one_from, one_to = aware(one[0]), aware(one[1])
    other_from, other_to = aware(other[0]), aware(other[1])
    if one_from >= one_to or other_from >= other_to:
        return False
    return one_from < other_to and other_from < one_to


def split_across_office_dates(
    starts_at: datetime, ends_at: datetime, *, within: TimeWindow | None = None
) -> list[TimeSegment]:
    """구간 하나를 **사무실 달력의 날마다 한 조각**으로 쪼갠다 (K22 「자정 넘는 회의는 날짜별로」).

    `within` 을 주면 **먼저 창으로 잘라낸다** — 그래야 오래 걸친 행 하나가 조각 수천 개가 되지 않는다.
    반열림이라 **자정에 정확히 끝나는 구간은 다음 날에 조각을 만들지 않는다.**
    """
    block_from, block_to = aware(starts_at), aware(ends_at)
    if within is not None:
        window_from, window_to = aware(within[0]), aware(within[1])
        block_from, block_to = max(block_from, window_from), min(block_to, window_to)
    if block_from >= block_to:
        return []
    segments: list[TimeSegment] = []
    cursor = block_from.astimezone(OFFICE_TIMEZONE)
    local_end = block_to.astimezone(OFFICE_TIMEZONE)
    while cursor < local_end:
        on_date = cursor.date()
        next_midnight = office_instant(on_date + timedelta(days=1), time.min)
        segment_end = min(next_midnight, local_end)
        segments.append(TimeSegment(on_date=on_date, starts_at=cursor, ends_at=segment_end))
        cursor = segment_end
    return segments
