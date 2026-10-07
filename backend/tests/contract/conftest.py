"""계약 시험 공통 — 앱 조립에 주입하는 시험용 값 (WORK-012 WP1 검수 W-5)."""
from __future__ import annotations

import pytest

from ax_workspace.platform import user_event_hub


@pytest.fixture(autouse=True)
def _user_event_coalescing_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """연동 사건 묶음(`integration.changed` 1초에 한 번)의 타이머를 **0** 으로 — 계약 시험 앱에서 진짜 1초 타이머 스레드가
    돌지 않는다. 묶어 내기 자체는 `tests/unit/test_user_event_hub.py` 가 타이머 대역으로 잰다."""
    monkeypatch.setattr(user_event_hub, "COALESCE_SECONDS", 0)
