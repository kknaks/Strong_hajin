"""Opt-in smoke against the real Soniox API.

Everything else in the suite injects a fake opener, which fixes how we build a request and read a response but says
nothing about whether Soniox actually answers that way. This runs the two real calls the product depends on — issuing a
restricted temporary key, and one async transcription with diarization — through the same adapter the application uses.

It needs a real credential and refuses to pretend otherwise: without `SONIOX_API_KEY` it exits non-zero rather than
reporting success. Only provider request ids, durations and counts are printed; never the key, and never transcript text.

    make soniox-smoke
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from ax_workspace.modules.meetings.transcription import TranscriptionFailure
from ax_workspace.platform.soniox import SonioxTranscriptionAdapter

SPOKEN = "안녕하세요. 오늘 회의 요약 테스트입니다."


def _speech_wav() -> tuple[bytes, str]:
    """Real speech, not silence: a synthesized utterance so the provider has something to transcribe."""
    with tempfile.TemporaryDirectory() as directory:
        aiff = Path(directory) / "sample.aiff"
        wav = Path(directory) / "sample.wav"
        subprocess.run(["say", "-v", "Yuna", "-o", str(aiff), SPOKEN], check=True, capture_output=True)
        subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)],
            check=True,
            capture_output=True,
        )
        return wav.read_bytes(), wav.name


def main() -> int:
    if not os.getenv("SONIOX_API_KEY"):
        print("SONIOX_API_KEY is not set; the smoke needs a real credential and will not fake one.", file=sys.stderr)
        return 2

    adapter = SonioxTranscriptionAdapter()
    reference = f"soniox-smoke:{int(time.time())}"
    receipt: dict[str, object] = {"client_reference_id": reference}

    started = time.monotonic()
    try:
        credential = adapter.issue(client_reference_id=reference, max_session_duration_seconds=900)
    except TranscriptionFailure as error:
        print(json.dumps({**receipt, "step": "issue", "error": str(error), "retryable": error.retryable}, ensure_ascii=False))
        return 1
    receipt["issue_ms"] = round((time.monotonic() - started) * 1000)
    # The key itself never leaves this process; only its shape is evidence.
    receipt["temporary_key_length"] = len(credential.temporary_key)
    receipt["expires_at"] = credential.expires_at.isoformat()
    receipt["model"] = credential.model
    receipt["diarization"] = credential.enable_speaker_diarization
    if credential.temporary_key == os.environ["SONIOX_API_KEY"]:
        print(json.dumps({**receipt, "error": "provider returned the long-lived key"}), file=sys.stderr)
        return 1

    audio, name = _speech_wav()
    receipt["audio_bytes"] = len(audio)
    started = time.monotonic()
    try:
        result = adapter.transcribe(data=audio, original_name=name, content_type="audio/wav", client_reference_id=reference)
    except TranscriptionFailure as error:
        print(json.dumps({**receipt, "step": "transcribe", "error": str(error), "retryable": error.retryable}, ensure_ascii=False))
        return 1
    receipt["transcribe_ms"] = round((time.monotonic() - started) * 1000)
    receipt["provider_reference"] = result.provider_reference
    receipt["provider_request_id"] = result.provider_request_id
    receipt["segment_count"] = len(result.segments)
    receipt["speaker_labels"] = sorted({segment.speaker_label for segment in result.segments if segment.speaker_label})
    receipt["transcribed_ms"] = max((segment.end_ms for segment in result.segments), default=0)
    # The provider-side file and transcription are deleted after collection; a warning is the only sign of failure.
    receipt["cleanup_warnings"] = list(result.cleanup_warnings)
    receipt["provider_artifacts_deleted"] = not result.cleanup_warnings

    print(json.dumps(receipt, ensure_ascii=False))
    return 0 if result.segments else 1


if __name__ == "__main__":
    raise SystemExit(main())
