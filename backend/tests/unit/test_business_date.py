from datetime import UTC, datetime, timezone, timedelta

from ax_workspace.platform.work_tasks import business_date


def test_business_date_uses_seoul_calendar_day_not_utc() -> None:
    # 18:00 UTC is already 03:00 the next day in Seoul (UTC+9).
    assert business_date(datetime(2026, 9, 3, 18, 0, tzinfo=UTC)) == "2026-09-04"
    assert business_date(datetime(2026, 9, 3, 12, 0, tzinfo=UTC)) == "2026-09-03"


def test_business_date_treats_naive_timestamps_as_utc() -> None:
    assert business_date(datetime(2026, 9, 3, 18, 0)) == "2026-09-04"


def test_business_date_normalizes_other_offsets() -> None:
    kst = timezone(timedelta(hours=9))
    assert business_date(datetime(2026, 9, 4, 3, 0, tzinfo=kst)) == "2026-09-04"
