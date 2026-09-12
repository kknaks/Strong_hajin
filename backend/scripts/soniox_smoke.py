"""Opt-in smoke against the real Soniox realtime API.

The suite injects a fake connector everywhere else, which fixes how we build a config and read a response but says
nothing about whether Soniox actually answers that way. This runs the one real path the product depends on —
open a realtime session, stream audio through it, send the end frame, and read tokens until `finished` — through the
same adapter the relay uses.

**The end frame matters.** The last few seconds of speech are only settled after the provider is told no more audio is
coming; without it the tail is simply missing. This smoke exists partly to keep that true against the real service.

It needs a real credential and refuses to pretend otherwise: without `SONIOX_API_KEY` it exits non-zero rather than
reporting success. Only counts and durations are printed — never the key, never the transcript text.

    make soniox-smoke
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from ax_workspace.modules.meetings.stream import AudioDeclaration, SttUpstreamError
from ax_workspace.platform.soniox import SonioxRealtimeConnector

SPOKEN = "안녕하세요. 오늘 회의 요약 테스트입니다. 다음 주 목요일까지 초안을 준비하겠습니다."
#: 실시간 세션이 받는 raw PCM — 컨테이너가 아니므로 sample_rate·channels 를 그대로 선언한다.
AUDIO = AudioDeclaration(format="pcm_s16le", sample_rate=16_000, channels=1)
#: 한 번에 밀어 넣지 않고 실제 캡처처럼 잘라 보낸다.
CHUNK_BYTES = 3_200
DRAIN_TIMEOUT_SECONDS = 15.0


def _speech_pcm() -> bytes:
    """Real speech, not silence: a synthesized utterance so the provider has something to transcribe."""
    with tempfile.TemporaryDirectory() as directory:
        aiff = Path(directory) / "sample.aiff"
        raw = Path(directory) / "sample.pcm"
        subprocess.run(["say", "-v", "Yuna", "-o", str(aiff), SPOKEN], check=True, capture_output=True)
        subprocess.run(
            ["afconvert", "-f", "caff", "-d", "LEI16@16000", "-c", "1", str(aiff), str(raw)],
            check=True,
            capture_output=True,
        )
        data = raw.read_bytes()
    # caff 헤더를 넘긴 뒤가 곧 PCM 이다. 헤더 길이는 고정이 아니므로 `data` 청크를 찾는다.
    marker = data.find(b"data")
    return data[marker + 12 :] if marker >= 0 else data


async def _run() -> dict[str, object]:
    connector = SonioxRealtimeConnector()
    pcm = _speech_pcm()
    started = time.monotonic()
    session = await connector.connect(AUDIO)
    receipt: dict[str, object] = {"audio_bytes": len(pcm), "connect_ms": round((time.monotonic() - started) * 1000)}

    settled = 0
    provisional = 0
    last_end_ms = 0

    async def read_while_sending() -> None:
        nonlocal settled, provisional, last_end_ms
        async for tokens in session.tokens():
            for token in tokens:
                if token.is_final:
                    settled += 1
                    last_end_ms = max(last_end_ms, token.end_ms)
                else:
                    provisional += 1

    reader = asyncio.create_task(read_while_sending())
    try:
        for offset in range(0, len(pcm), CHUNK_BYTES):
            await session.send_audio(pcm[offset : offset + CHUNK_BYTES])
            # 100ms 분량을 100ms 마다 — 실제 캡처와 같은 속도로 흘린다.
            await asyncio.sleep(0.1)
        receipt["settled_before_end_frame"] = settled
        reader.cancel()
        try:
            await reader
        except asyncio.CancelledError:
            pass

        # end frame 뒤에야 확정되는 꼬리를 받는다 — 이 스모크가 지키려는 것이 이 구간이다.
        drained = 0
        async for tokens in session.finish(timeout_seconds=DRAIN_TIMEOUT_SECONDS):
            for token in tokens:
                if not token.is_final:
                    continue
                drained += 1
                settled += 1
                last_end_ms = max(last_end_ms, token.end_ms)
        receipt["settled_after_end_frame"] = drained
    finally:
        await session.close()

    receipt["settled_tokens"] = settled
    receipt["provisional_tokens"] = provisional
    receipt["last_end_ms"] = last_end_ms
    receipt["total_ms"] = round((time.monotonic() - started) * 1000)
    return receipt


def main() -> int:
    if not os.getenv("SONIOX_API_KEY"):
        print("SONIOX_API_KEY is not set; the smoke needs a real credential and will not fake one.", file=sys.stderr)
        return 2
    try:
        receipt = asyncio.run(_run())
    except SttUpstreamError as error:
        print(json.dumps({"step": "realtime", "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(receipt, ensure_ascii=False))
    # 꼬리가 오지 않으면 릴레이도 그 구간을 잃는다 — 성공으로 넘기지 않는다.
    return 0 if receipt["settled_tokens"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
