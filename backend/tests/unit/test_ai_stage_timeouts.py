"""AI 호출의 단계별 timeout (SPEC-010 §4.6 · OQ-1015 · WORK-012 WP2-BE) — env 넷 · 시작 오류 · 단계별 프로필."""
from __future__ import annotations

import pytest

from ax_workspace.bootstrap.application import create_conversation_provider
from ax_workspace.bootstrap.settings import RuntimeProfile, Settings

STAGE_ENV = {
    "AX_AI_TIMEOUT_BATCH_SECONDS": ("ai_timeout_batch_seconds", 240),
    "AX_AI_TIMEOUT_WARMSTART_SECONDS": ("ai_timeout_warmstart_seconds", 240),
    "AX_AI_TIMEOUT_FINAL_SECONDS": ("ai_timeout_final_seconds", 900),
    "AX_AI_TIMEOUT_CONVERSATION_SECONDS": ("ai_timeout_conversation_seconds", 180),
}


def test_the_four_stage_defaults(monkeypatch) -> None:
    for name in STAGE_ENV:
        monkeypatch.delenv(name, raising=False)
    settings = Settings.from_environment()
    for field, default in STAGE_ENV.values():
        assert getattr(settings, field) == default


@pytest.mark.parametrize("name", sorted(STAGE_ENV))
def test_each_stage_reads_its_own_env(monkeypatch, name) -> None:
    monkeypatch.setenv(name, "77")
    field, _ = STAGE_ENV[name]
    assert getattr(Settings.from_environment(), field) == 77


@pytest.mark.parametrize("value", ["abc", "0", "-5", "1.5"])
@pytest.mark.parametrize("name", sorted(STAGE_ENV))
def test_a_non_positive_or_non_integer_value_stops_startup(monkeypatch, name, value) -> None:
    """조용한 기본값 금지 — 숫자가 아니거나 0·음수면 시작 오류(OQ-1015)."""
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        Settings.from_environment()


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_the_factory_builds_a_profile_with_the_stage_value_for_either_provider(tmp_path, provider) -> None:
    """Codex·Claude 같은 값 — 공장이 단계 값으로 프로필을 만든다. 안 주면 대화 값이다."""
    settings = Settings(RuntimeProfile.TEST, f"sqlite:///{tmp_path / 'x.db'}", ai_provider=provider, ai_timeout_conversation_seconds=123)
    assert create_conversation_provider(settings, timeout_seconds=900)._profile.timeout_seconds == 900
    assert create_conversation_provider(settings)._profile.timeout_seconds == 123


def test_the_finalize_lease_floor_moves_with_the_final_stage_limit(tmp_path) -> None:
    """바닥은 단계 상한을 따라 움직인다 — 루프의 실제 최대 호출 수와의 대조는 `test_meeting_finalize_service.py` 가 최악
    경로를 돌려 센다(`test_the_worst_delivery_never_calls_the_provider_more_than_the_lease_budget`)."""
    short = Settings(RuntimeProfile.TEST, f"sqlite:///{tmp_path / 'x.db'}", ai_timeout_final_seconds=100)
    long = Settings(RuntimeProfile.TEST, f"sqlite:///{tmp_path / 'x.db'}", ai_timeout_final_seconds=900)
    assert long.effective_meeting_finalize_lease_seconds > short.effective_meeting_finalize_lease_seconds
    roomy = Settings(RuntimeProfile.TEST, f"sqlite:///{tmp_path / 'x.db'}", meeting_finalize_lease_seconds=99_999)
    assert roomy.effective_meeting_finalize_lease_seconds == 99_999
