"""업무의 **기간을 읽는 법 네 경우**와 닫기 사유 둘 (SPEC-004 §4 Validation · 증보 K7·K11·K14).

여기서 증명하는 것은 하나다 — **뒤집힌 기간을 빈 구간으로 보지 않는다.** 그 기간은 가정이 아니라
실재한다: 시작 전이와 조건 변경 제안 동의가 `validate_schedule` 을 지나지 않고 날짜를 넣기 때문이다.
"""

from __future__ import annotations

from datetime import date, time

from ax_workspace.modules.work.schedule import (
    RELEASE_OUT_OF_RANGE,
    RELEASE_TASK_DATES_CLEARED,
    is_valid_time_range,
    is_within_span,
    release_reason_for,
    task_span,
)


def test_both_dates_in_order_read_as_that_interval() -> None:
    span = task_span(date(2026, 9, 2), date(2026, 9, 4))
    assert (span.span_from, span.span_to) == (date(2026, 9, 2), date(2026, 9, 4))
    assert span.covers(date(2026, 9, 3))
    assert not span.covers(date(2026, 9, 5))


def test_only_one_date_reads_as_that_single_day() -> None:
    """한쪽만 있으면 **그 날 하루**다 (증보 K7) — 프론트가 이미 쓰는 규칙과 같은 한 규칙이다."""
    only_due = task_span(None, date(2026, 9, 4))
    assert (only_due.span_from, only_due.span_to) == (date(2026, 9, 4), date(2026, 9, 4))
    only_start = task_span(date(2026, 9, 2), None)
    assert (only_start.span_from, only_start.span_to) == (date(2026, 9, 2), date(2026, 9, 2))
    assert not only_start.covers(date(2026, 9, 3))


def test_no_dates_at_all_means_there_is_no_interval() -> None:
    """기간이 없는 것은 「제한이 없다」가 아니다 — **어떤 날에도 배정할 수 없다** (원문 R2)."""
    assert task_span(None, None) is None
    assert is_within_span(None, date(2026, 9, 3)) is False


def test_a_reversed_interval_is_normalised_not_emptied() -> None:
    """`start > due` 는 `[min, max]` 로 읽는다 (증보 K11). **빈 구간으로 보지 않는다.**"""
    span = task_span(date(2026, 9, 6), date(2026, 9, 4))
    assert (span.span_from, span.span_to) == (date(2026, 9, 4), date(2026, 9, 6))
    assert span.covers(date(2026, 9, 5))


def test_the_release_reason_is_one_of_two_and_the_cleared_one_needs_no_dates() -> None:
    """사유는 둘뿐이다 — 한쪽만 지운 것은 **여전히 기간이 있으므로** `out_of_range` 다 (증보 K7)."""
    assert release_reason_for(None) == RELEASE_TASK_DATES_CLEARED
    assert release_reason_for(task_span(date(2026, 9, 4), None)) == RELEASE_OUT_OF_RANGE
    assert release_reason_for(task_span(date(2026, 9, 2), date(2026, 9, 4))) == RELEASE_OUT_OF_RANGE


def test_the_end_has_to_come_after_the_start_and_midnight_cannot_be_crossed() -> None:
    assert is_valid_time_range(time(10, 0), time(11, 0)) is True
    assert is_valid_time_range(time(10, 0), time(10, 0)) is False
    assert is_valid_time_range(time(11, 0), time(10, 0)) is False
