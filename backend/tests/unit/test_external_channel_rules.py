"""외부 채널의 파생 규칙 — 앱과 서버가 같은 식을 쓰는 자리 (SPEC-008 §4.6 · 4차 검수 W4-3)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json

import pytest

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.modules.external_channels.domain import (
    COLLECTOR_OFFLINE_AFTER,
    effective_room_status,
    kakao_attachment_aid,
    next_selected_rooms_version,
    settings_return_url,
)
from ax_workspace.modules.external_channels.events import (
    SYNC_WAKE_CHANNEL,
    USER_EVENTS_CHANNEL,
    UserEvent,
    UserEventType,
    sync_wake_payload,
)
from ax_workspace.platform.external_tokens import FernetTokenCipher, TokenDecryptionFailed, development_key, generate_key

NOW = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)
RUNNING = {"app": {"device_name": "Mac", "version": "0.1.0"}, "kakao": {"state": "running"}, "account": {"name": "하진"}}


def test_kakao_aid_is_the_documented_sha256_with_the_integration_in_scope() -> None:
    expected = hashlib.sha256(b"9f0c:18200:4401:2").hexdigest()
    assert kakao_attachment_aid("9f0c", "18200", 4401, 2) == expected
    # 동료 둘이 같은 단체방의 같은 사진을 올려도 연동이 다르면 aid 가 갈린다(D-25 · W3-2).
    assert kakao_attachment_aid("other", "18200", 4401, 2) != expected


def test_kakao_rooms_pause_when_the_collector_is_silent_off_or_switched_account() -> None:
    def status(**overrides):
        return effective_room_status(
            "live", kind="kakao", collector_status=overrides.get("collector", RUNNING),
            collector_reported_at=overrides.get("reported_at", NOW - timedelta(seconds=30)), now=NOW,
        )

    assert status() == "live"
    assert status(reported_at=None) == "paused"
    assert status(reported_at=NOW - COLLECTOR_OFFLINE_AFTER - timedelta(seconds=1)) == "paused"
    assert status(collector={**RUNNING, "kakao": {"state": "off"}}) == "paused"
    assert status(collector={**RUNNING, "kakao": {"state": "unreadable", "reason": "permission"}}) == "paused"
    assert status(collector={**RUNNING, "account_changed": True}) == "paused"
    # 슬랙 방은 수집기와 무관하다 — 저장값 그대로.
    assert effective_room_status("backfilling", kind="slack", collector_status=None, collector_reported_at=None, now=NOW) == "backfilling"


def test_selected_rooms_version_moves_only_when_the_selection_changes() -> None:
    assert next_selected_rooms_version(4, changed=False) == 4
    assert next_selected_rooms_version(4, changed=True) == 5


def test_callback_destination_is_a_query_on_the_web_origin() -> None:
    assert settings_return_url("http://127.0.0.1:5176", "mail", "ok") == "http://127.0.0.1:5176/?surface=settings&tab=mail&connect=ok"


def test_user_event_payload_round_trips_and_carries_no_body() -> None:
    event = UserEvent(
        UserEventType.MESSAGE_ARRIVED, "mina", integration_id="i-1", room_id="r-1", message_id="m-1", source_kind="slack",
        data={"count": 3},
    )
    payload = event.to_payload()
    assert json.loads(payload) == {
        "v": 1, "type": "inbox.message_arrived", "member_id": "mina", "integration_id": "i-1",
        "room_id": "r-1", "message_id": "m-1", "source_kind": "slack", "data": {"count": 3},
    }
    assert len(payload.encode("utf-8")) < 8000
    assert UserEvent.from_payload(payload) == event
    assert UserEvent.from_payload('{"v":2,"type":"x","member_id":"mina"}') is None
    assert UserEvent.from_payload("not json") is None
    assert USER_EVENTS_CHANNEL == "ax_user_events" and SYNC_WAKE_CHANNEL == "ax_external_sync"
    assert json.loads(sync_wake_payload("i-1", "rooms")) == {"v": 1, "integration_id": "i-1", "reason": "rooms"}


def test_token_cipher_round_trips_and_refuses_another_key() -> None:
    cipher = FernetTokenCipher(generate_key())
    sealed = cipher.encrypt("xoxp-not-a-real-token")
    assert "xoxp" not in sealed
    assert cipher.decrypt(sealed) == "xoxp-not-a-real-token"
    with pytest.raises(TokenDecryptionFailed):
        FernetTokenCipher(generate_key()).decrypt(sealed)
    with pytest.raises(ValueError):
        FernetTokenCipher("too-short")


def test_development_key_is_made_once_and_reused(tmp_path) -> None:
    path = tmp_path / ".scax" / "external-token.key"
    first = development_key(path)
    assert development_key(path) == first
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    FernetTokenCipher(first)


def test_external_settings_load_from_the_environment_and_never_print_secrets(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid.apps.example")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "google-secret-value")
    monkeypatch.setenv("SLACK_CLIENT_ID", "slack-cid")
    monkeypatch.setenv("SLACK_CLIENT_SECRET", "slack-secret-value")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-secret-value")
    monkeypatch.setenv("AX_EXTERNAL_TOKEN_ENCRYPTION_KEY", "fernet-secret-value")
    monkeypatch.setenv("AX_EXTERNAL_CHANNEL_STORAGE_DIR", "/mnt/ext")
    monkeypatch.setenv("AX_WEB_ORIGIN", "http://127.0.0.1:5176")
    monkeypatch.delenv("AX_API_ORIGIN", raising=False)
    settings = Settings.from_environment()
    assert settings.gmail_oauth_configured and settings.slack_oauth_configured
    assert settings.external_channel_storage_dir == "/mnt/ext"
    # 운영은 웹과 API 가 같은 origin 하나다 — 따로 말하지 않으면 웹 origin 을 쓴다.
    assert settings.api_origin == "http://127.0.0.1:5176"
    printed = repr(settings)
    for secret in ("google-secret-value", "slack-secret-value", "xapp-secret-value", "fernet-secret-value"):
        assert secret not in printed
    monkeypatch.setenv("AX_API_ORIGIN", "http://127.0.0.1:8001/")
    assert Settings.from_environment().api_origin == "http://127.0.0.1:8001"
    assert not Settings(RuntimeProfile.TEST, "sqlite://").gmail_oauth_configured
