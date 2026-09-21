"""겹침을 판정하는 **규칙** — 반열림과 자정 분할 (SPEC-004 §2.9 · DEC-003 증보 8 K22).

**무엇을 증명하는가.** 두 표를 조회하는 문(`platform/time_blocks.py`)이 **쓰는 규칙**이다:
경계가 닿는 것은 겹침이 아니고, `timestamptz` 로 사는 회의는 **사무실 시간대의 날짜별로 쪼개져**
`Date` + `Time` 로 사는 배정과 같은 축에서 비교된다.

**왜 순수한 자리에 있는가.** 반열림 판정과 자정 분할이 **배정 쪽과 회의 쪽에 각각 있으면 두 규칙이
된다**(K22 「읽는 문은 하나다」). 문은 하나이고, 그 문이 쓰는 규칙은 데이터베이스 없이 증명된다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from ax_workspace.modules.time_blocks import (
    OFFICE_TIMEZONE,
    office_day_window,
    office_instant,
    office_span,
    overlaps,
    split_across_office_dates,
)


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


# ---- 반열림 `[시작, 끝)` — 새로 정한 규칙이 아니다 (`meetings_visible_to` docstring) ----


def test_a_block_that_ends_where_the_next_one_starts_does_not_overlap_it() -> None:
    """10:00–11:00 과 11:00–12:00 은 **둘 다 선다.** 경계가 닿는 것은 겹침이 아니다."""
    earlier = (_utc(2027, 3, 3, 1), _utc(2027, 3, 3, 2))
    later = (_utc(2027, 3, 3, 2), _utc(2027, 3, 3, 3))

    assert not overlaps(earlier, later)
    assert not overlaps(later, earlier)


def test_one_minute_of_shared_time_is_an_overlap_in_both_directions() -> None:
    assert overlaps((_utc(2027, 3, 3, 1), _utc(2027, 3, 3, 2)), (_utc(2027, 3, 3, 1, 59), _utc(2027, 3, 3, 3)))
    assert overlaps((_utc(2027, 3, 3, 1, 59), _utc(2027, 3, 3, 3)), (_utc(2027, 3, 3, 1), _utc(2027, 3, 3, 2)))


def test_a_block_wholly_inside_another_overlaps_it() -> None:
    assert overlaps((_utc(2027, 3, 3, 1), _utc(2027, 3, 3, 5)), (_utc(2027, 3, 3, 2), _utc(2027, 3, 3, 3)))
    assert overlaps((_utc(2027, 3, 3, 2), _utc(2027, 3, 3, 3)), (_utc(2027, 3, 3, 1), _utc(2027, 3, 3, 5)))


def test_an_empty_interval_overlaps_nothing() -> None:
    """길이가 0 인 구간은 반열림에서 **아무것도 담지 않는다** — 최소 길이가 없어도 그렇다."""
    assert not overlaps((_utc(2027, 3, 3, 2), _utc(2027, 3, 3, 2)), (_utc(2027, 3, 3, 1), _utc(2027, 3, 3, 3)))


# ---- 타입이 다르다: 배정은 `Date` + `Time`, 회의는 `timestamptz` ----


def test_a_wall_clock_time_becomes_the_instant_the_office_means_by_it() -> None:
    """사무실은 Asia/Seoul 이다 — 10:00 은 `01:00Z` 다. 서버의 TZ 설정에 기대지 않는다."""
    assert office_instant(date(2027, 3, 3), time(10, 0)) == _utc(2027, 3, 3, 1)
    assert office_instant(date(2027, 3, 3), time(10, 0)).tzinfo is OFFICE_TIMEZONE


def test_one_assignment_becomes_a_half_open_instant_interval() -> None:
    assert office_span(date(2027, 3, 3), time(10, 0), time(11, 30)) == (
        office_instant(date(2027, 3, 3), time(10, 0)),
        office_instant(date(2027, 3, 3), time(11, 30)),
    )


def test_one_office_day_runs_from_its_own_midnight_to_the_next() -> None:
    assert office_day_window(date(2027, 3, 3), date(2027, 3, 3)) == (
        office_instant(date(2027, 3, 3), time(0, 0)),
        office_instant(date(2027, 3, 4), time(0, 0)),
    )
    # 여러 날이면 마지막 날의 **다음** 자정까지다 — 마지막 날이 반 토막 나지 않는다.
    assert office_day_window(date(2027, 3, 1), date(2027, 3, 5))[1] == office_instant(date(2027, 3, 6), time(0, 0))


# ---- 자정을 넘는 회의를 날짜별로 쪼갠다 — 배정은 자정을 못 넘지만 회의는 넘는다 ----


def test_a_meeting_that_crosses_midnight_splits_into_one_segment_per_office_date() -> None:
    """23:00 에 시작해 다음 날 01:00 에 끝나는 회의는 **두 날에 선다.**

    배정은 `Date` + `Time` 이라 자정을 넘을 수 없다 — 쪼개지 않으면 다음 날 00:30 배정이
    「그 날의 블록이 없다」로 통과한다.
    """
    segments = split_across_office_dates(
        office_instant(date(2027, 3, 3), time(23, 0)), office_instant(date(2027, 3, 4), time(1, 0))
    )

    assert [segment.on_date for segment in segments] == [date(2027, 3, 3), date(2027, 3, 4)]
    assert segments[0].starts_at == office_instant(date(2027, 3, 3), time(23, 0))
    assert segments[0].ends_at == office_instant(date(2027, 3, 4), time(0, 0))
    assert segments[1].starts_at == office_instant(date(2027, 3, 4), time(0, 0))
    assert segments[1].ends_at == office_instant(date(2027, 3, 4), time(1, 0))


def test_a_meeting_that_ends_exactly_at_midnight_stays_on_one_date() -> None:
    """반열림이라 **끝나는 순간의 날은 포함되지 않는다** — 빈 조각을 만들지 않는다."""
    segments = split_across_office_dates(
        office_instant(date(2027, 3, 3), time(22, 0)), office_instant(date(2027, 3, 4), time(0, 0))
    )

    assert [segment.on_date for segment in segments] == [date(2027, 3, 3)]


def test_a_meeting_spanning_three_dates_yields_three_segments() -> None:
    segments = split_across_office_dates(
        office_instant(date(2027, 3, 3), time(23, 0)), office_instant(date(2027, 3, 5), time(1, 0))
    )

    assert [segment.on_date for segment in segments] == [date(2027, 3, 3), date(2027, 3, 4), date(2027, 3, 5)]
    assert segments[1].starts_at == office_instant(date(2027, 3, 4), time(0, 0))
    assert segments[1].ends_at == office_instant(date(2027, 3, 5), time(0, 0))


def test_splitting_clips_to_the_window_so_a_long_meeting_never_becomes_a_long_list() -> None:
    """창 밖은 잘려 나간다 — 10년짜리 행이 3650 조각으로 불어나지 않는다."""
    window = office_day_window(date(2027, 3, 4), date(2027, 3, 4))
    segments = split_across_office_dates(
        office_instant(date(2027, 3, 1), time(9, 0)),
        office_instant(date(2027, 3, 8), time(9, 0)),
        within=window,
    )

    assert [segment.on_date for segment in segments] == [date(2027, 3, 4)]
    assert (segments[0].starts_at, segments[0].ends_at) == window


def test_a_meeting_outside_the_window_yields_nothing() -> None:
    assert (
        split_across_office_dates(
            office_instant(date(2027, 3, 1), time(9, 0)),
            office_instant(date(2027, 3, 1), time(10, 0)),
            within=office_day_window(date(2027, 3, 4), date(2027, 3, 4)),
        )
        == []
    )


def test_a_naive_stored_timestamp_is_read_as_utc_not_as_local_wall_clock() -> None:
    """SQLite 는 tz 를 잃는다. 저장된 값은 UTC 로 읽는다 — 저장소의 `business_date` 와 같은 결이다."""
    segments = split_across_office_dates(datetime(2027, 3, 3, 14, 0), datetime(2027, 3, 3, 15, 0))

    assert [segment.on_date for segment in segments] == [date(2027, 3, 3)]
    assert segments[0].starts_at == _utc(2027, 3, 3, 14)
