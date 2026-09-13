import asyncio

import ax_workspace.modules.meetings.stream_service as stream_service
from ax_workspace.modules.meetings.stream import AudioDeclaration, SttToken, build_blocks
from ax_workspace.modules.meetings.stream import DRAIN_TIMEOUT_SECONDS, KEEPALIVE_IDLE_SECONDS
from ax_workspace.platform.soniox import build_config


def _token(
    text: str,
    *,
    speaker: str = "1",
    start_ms: int = 0,
    end_ms: int | None = None,
) -> SttToken:
    return SttToken(text, True, speaker, start_ms, start_ms + 500 if end_ms is None else end_ms)


def test_blocks_close_on_speaker_silence_length_and_sentence_boundaries() -> None:
    changed = build_blocks(
        [
            _token("앞사람.", speaker="1", start_ms=0, end_ms=500),
            _token("뒷사람.", speaker="2", start_ms=600, end_ms=900),
        ]
    )
    assert [(block.speaker_label, block.content) for block in changed] == [
        ("1", "앞사람."),
        ("2", "뒷사람."),
    ]

    silent = build_blocks(
        [
            _token("먼저.", start_ms=0, end_ms=500),
            _token("한참 뒤.", start_ms=3_000, end_ms=3_400),
        ]
    )
    assert [block.content for block in silent] == ["먼저.", "한참 뒤."]

    long_run = build_blocks(
        [
            _token("가" * 200, start_ms=0, end_ms=100),
            _token("나" * 200, start_ms=100, end_ms=200),
            _token("끝.", start_ms=200, end_ms=300),
        ]
    )
    assert [len(block.content) for block in long_run] == [200, 200, 2]

    slow = build_blocks(
        [
            _token("어", start_ms=index * 1_500, end_ms=index * 1_500 + 1_400)
            for index in range(30)
        ]
    )
    assert slow and max(block.end_ms - block.at_ms for block in slow) <= 20_000

    sentences = build_blocks(
        [
            _token(
                "네 알겠습니다. " if index % 5 == 4 else "그래서 이렇게 ",
                start_ms=index * 1_200,
                end_ms=index * 1_200 + 1_000,
            )
            for index in range(40)
        ]
    )
    assert len(sentences) > 1
    assert all(block.content.endswith(".") for block in sentences)
    assert build_blocks([_token("   ")]) == []


def test_block_offsets_are_measured_from_the_meeting_start() -> None:
    blocks = build_blocks(
        [_token("기준이 있는 말.", start_ms=250, end_ms=900)],
        base_ms=10_000,
    )

    assert (blocks[0].at_ms, blocks[0].end_ms) == (10_250, 10_900)


def test_audio_provider_config_distinguishes_container_and_raw_pcm() -> None:
    container = build_config("secret", AudioDeclaration("webm/opus", 16_000, 1))
    assert container["audio_format"] == "auto"
    assert "sample_rate" not in container and "num_channels" not in container
    assert not any("endpoint" in key for key in container)

    raw = build_config("secret", AudioDeclaration("pcm_s16le", 16_000, 1))
    assert raw["audio_format"] == "pcm_s16le"
    assert raw["sample_rate"] == 16_000 and raw["num_channels"] == 1


class _KeepaliveSession:
    def __init__(self) -> None:
        self.keepalive_count = 0

    async def keepalive(self) -> None:
        self.keepalive_count += 1


def test_silent_upstream_is_kept_alive_before_the_provider_timeout(monkeypatch) -> None:
    assert KEEPALIVE_IDLE_SECONDS == 10.0 and DRAIN_TIMEOUT_SECONDS == 8.0
    session = _KeepaliveSession()
    upstream = object.__new__(stream_service._UpstreamSession)
    upstream._upstream = session
    monkeypatch.setattr(stream_service, "KEEPALIVE_IDLE_SECONDS", 0.02)

    async def run_briefly() -> None:
        upstream._last_audio_at = asyncio.get_running_loop().time()
        pump = asyncio.create_task(upstream._pump_keepalive())
        await asyncio.sleep(0.12)
        pump.cancel()
        try:
            await pump
        except asyncio.CancelledError:
            pass

    asyncio.run(run_briefly())
    assert session.keepalive_count >= 1


def test_arriving_audio_suppresses_keepalive(monkeypatch) -> None:
    session = _KeepaliveSession()
    upstream = object.__new__(stream_service._UpstreamSession)
    upstream._upstream = session
    monkeypatch.setattr(stream_service, "KEEPALIVE_IDLE_SECONDS", 0.05)

    async def run_briefly() -> None:
        loop = asyncio.get_running_loop()
        upstream._last_audio_at = loop.time()
        pump = asyncio.create_task(upstream._pump_keepalive())
        for _ in range(12):
            await asyncio.sleep(0.01)
            upstream._last_audio_at = loop.time()
        pump.cancel()
        try:
            await pump
        except asyncio.CancelledError:
            pass

    asyncio.run(run_briefly())
    assert session.keepalive_count == 0
