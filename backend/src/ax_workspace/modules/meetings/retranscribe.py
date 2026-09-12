"""종료 뒤 재전사 — 저장된 음원 전체를 **한 번 더** 전사해 원문을 갈아 끼운다 (사용자 결정 D44, 2026-09-11).

왜 두 번 도는가 —

실시간 전사는 말이 끝나기 전에 답해야 하므로 **화자 분리가 부정확하다.** 회의가 끝나면 서두를 이유가
없고 음원 전체가 손에 있다: 처음부터 끝까지 한 번에 들으면 누가 말했는지, 언제 말했는지가 훨씬 낫다.
그래서 종료 파이프라인은 ① 재전사 → ② 합성 두 걸음이고, ②가 읽는 원문은 ①이 새로 쓴 것이다.

못박는 것 —

1. **폴백이 없다** (D44 정정, 2026-09-11). 재전사가 어떤 이유로든 되지 않으면 회의는 **「실패」**다.
   실시간 원문 위에서 합성하지 않고, 합성 단계로 넘어가지도 않는다.

   한 번 폴백을 두었다가 실물에서 그 값을 치렀다: 결과를 받다 끊겼는데 파이프라인이 조용히 실시간
   원문으로 넘어가 `done` 이 됐고, 사람은 **재전사되지 않은 원문 위의 회의록**을 재전사된 것으로 알고
   읽었다. 조용히 나쁜 결과를 주는 것보다 「안 됐습니다, 다시 시도하세요」가 낫다.
2. **녹음이 없는 것도 실패다.** 재전사가 원문 정본을 쓰는 일인 이상, 쓸 것이 없으면 회의록도 없다.
3. 블록 경계는 실시간과 **같은 규칙**을 지난다 (`stream.build_blocks`) — 원문이 두 모양이 되지 않는다.
4. 이 모듈은 **무엇을 보낼지**만 안다. 어떻게 보내는지(업로드·폴링·HTTP)는 어댑터가 소유한다.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Protocol

from ax_workspace.modules.meetings.stream import SttToken


#: 원문이 어디서 왔는가. **`done` 인 회의는 언제나 이 값이다** — 재전사에 성공해야만 회의록이 서기 때문이다.
#: 실시간 원문으로 회의록을 만드는 갈래는 없다 (D44 정정).
SOURCE_FINAL = "final"


class RetranscribeFailed(Exception):
    """재전사가 되지 않았다 — **회의가 「실패」로 가는 자리**다.

    `reason` 은 사람이 화면에서 읽는 한 줄이다: 무엇이 안 됐고 [다시 시도] 하면 된다는 것까지만 말하고
    내부 값(회의 id·전사 id·HTTP 본문·예외 이름)은 한 자도 담지 않는다 (D43).
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


#: 실패 여섯 갈래의 문구. 사람이 다음에 무엇을 할 수 있는지가 문장 안에 있다.
FAILED_NO_RECORDING = "회의록을 만들지 못했습니다 — 다시 들을 녹음이 없습니다"
FAILED_UPLOAD = "회의록을 만들지 못했습니다 — 녹음을 올리지 못했습니다. [다시 시도] 해 주세요"
FAILED_PROVIDER = "회의록을 만들지 못했습니다 — 녹음을 다시 듣지 못했습니다. [다시 시도] 해 주세요"
FAILED_RESULT = "회의록을 만들지 못했습니다 — 다시 들은 결과를 받지 못했습니다. [다시 시도] 해 주세요"
FAILED_EMPTY = "회의록을 만들지 못했습니다 — 녹음에서 말을 찾지 못했습니다"
FAILED_TIMEOUT = "회의록을 만들지 못했습니다 — 녹음을 다시 듣는 데 너무 오래 걸립니다. [다시 시도] 해 주세요"

#: 상태를 몇 초마다 보는가, 그리고 얼마나 기다리는가. 음원이 길수록 오래 걸린다 — 한 시간 회의를 담을 상한이다.
POLL_SECONDS = 5
TIMEOUT_SECONDS = 1_200

#: 헤더 없는 PCM 의 선언값. 브라우저가 보내는 것은 컨테이너(webm/opus)이고, 이 값들은 raw 를 올릴 때만 쓴다.
PCM_SAMPLE_RATE = 16_000
PCM_CHANNELS = 1
PCM_BITS = 16


@dataclass(frozen=True, slots=True)
class Recording:
    """재전사에 올릴 음원 하나. **저장 위치는 여기 오지 않는다** — 바이트와 이름뿐이다."""

    data: bytes
    filename: str
    #: 회의 시작과 녹음 시작의 차이(ms). 새 원문의 `at_ms` 를 회의 기준으로 되돌리는 값이다.
    base_ms: int = 0

    @property
    def is_raw_pcm(self) -> bool:
        return self.filename.lower().endswith(".pcm")


#: 결과를 받다 끊겼을 때 **같은 전사 건으로 결과만** 다시 받아 보는 횟수와 그 사이 기다림(초).
#: 실물에서 `IncompleteRead` 로 본문이 중간에 끊겼다 — 전사는 이미 끝나 있으므로 다시 전사할 이유가 없다.
RESULT_RETRIES = 3
RESULT_BACKOFF_SECONDS = (2, 4, 8)


class FileTranscriber(Protocol):
    """음원 파일 하나를 통째로 전사하는 경계. 시험은 여기에 대역을 끼운다.

    실패는 `SttUpstreamError`(올리지 못함·거절·결과를 받지 못함)와 `TimeoutError`(상한 초과) 둘이다.
    """

    def transcribe(self, recording: Recording) -> list[SttToken]:
        """확정 토큰 전량. 잠정 개념이 없다 — 전부 `is_final=True` 다."""


def wav_header(payload_size: int, *, sample_rate: int = PCM_SAMPLE_RATE, channels: int = PCM_CHANNELS,
               bits: int = PCM_BITS) -> bytes:
    """헤더 없는 PCM 앞에 붙일 44바이트 RIFF 머리.

    컨테이너(webm·ogg)는 헤더가 스스로 형식을 말하므로 그대로 올린다. raw 는 아무 말도 하지 않아서
    받는 쪽이 샘플레이트를 추측해야 한다 — 추측하게 두는 대신 우리가 아는 값을 적어 보낸다.
    """
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    return b"".join((
        b"RIFF",
        struct.pack("<I", 36 + payload_size),
        b"WAVEfmt ",
        struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits),
        b"data",
        struct.pack("<I", payload_size),
    ))


def uploadable(recording: Recording) -> tuple[bytes, str]:
    """올릴 바이트와 파일 이름. raw PCM 이면 wav 로 감싸고 이름도 그에 맞춘다."""
    if not recording.is_raw_pcm:
        return recording.data, recording.filename
    wrapped = wav_header(len(recording.data)) + recording.data
    return wrapped, recording.filename.rsplit(".", 1)[0] + ".wav"
