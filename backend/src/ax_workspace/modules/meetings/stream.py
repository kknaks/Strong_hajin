"""회의 스트림의 계약 — 프레임·포트·블록 경계. 전송(WS)도 provider 도 ORM 도 모른다.

SCAX-SPEC-004 §5.2·§5.3. 브라우저는 STT provider 를 모르고, 오디오는 반드시 우리 서버를 지난다.
이 파일은 그 통로의 **말**만 정한다 — 누가 실어 나르는지는 `entrypoints`(WS)와 `platform`(STT 어댑터)이 안다.
여기에 provider 의 이름이 없는 것이 계약이다: 도메인은 상대를 모른다.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


# --- close code (SPEC §5.3 거절·종료 사유) --------------------------------------
CLOSE_UNAUTHORIZED = 4401
CLOSE_NOT_FOUND = 4404
CLOSE_CONFLICT = 4409
# 오류 프레임 뒤 서버가 닫는 코드. 「그 밖」의 끊김이고 재연결하지 않는다.
CLOSE_AFTER_ERROR = 1011
# `/end` 가 살아 있는 스트림을 닫는 코드 — 오류가 아니므로 오류 프레임을 앞세우지 않는다.
CLOSE_ENDED = 1000

REASON_UNAUTHORIZED = "unauthorized"
REASON_NOT_FOUND = "not_found"
REASON_INVALID_STATUS = "invalid_meeting_status"
REASON_STREAM_ACTIVE = "meeting_stream_active"
#: 오디오를 올리는 자리는 회의를 만든 사람의 것이다 — 참석자라도 남의 회의를 대신 열지 않는다.
REASON_NOT_OWNER = "not_meeting_owner"
REASON_DISCONNECTED = "meeting_stream_disconnected"
REASON_ENDED = "meeting_ended"

ERROR_CODE_DISCONNECTED = "meeting_stream_disconnected"
DISCONNECT_UPSTREAM = "upstream"
DISCONNECT_WRITE_FAILED = "write_failed"

ROLE_UPSTREAM = "upstream"
ROLE_SUBSCRIBE = "subscribe"

# 첫 프레임을 기다리는 시간. 없거나 무효면 4401 이고 사유를 흘리지 않는다.
AUTH_TIMEOUT_SECONDS = 5.0

# 확정 블록 경계 — 계약이 아니라 구현 재량이고(SPEC §5.4-2), 값은 **이 파일에만** 있다.
#
# 실물 3회차에서 한 화자가 51초를 한 블록으로 말했다. 그 한 덩이에 시간 칩 하나가 걸리니 어느 대목의
# 근거인지 알 수 없고, 근거 구간이 블록에 걸쳐 줄 둘이 함께 켜졌다. 그래서 길이와 **시간**에 함께
# 상한을 두고, 끊을 자리는 문장 끝을 먼저 찾는다 (사용자 결정 D51, 2026-09-11).
BLOCK_MAX_CHARS = 150
#: 한 블록이 담는 최대 길이(ms). 넘기면 끊는다 — 칩 하나가 가리키는 구간을 사람이 짚을 수 있어야 한다.
BLOCK_MAX_MS = 20_000
BLOCK_GAP_MS = 2_000
#: 문장이 끝났다고 보는 자리. 한국어는 종결 어미 뒤 공백도 함께 본다 — 받아 적은 글에는 마침표가 자주 없다.
SENTENCE_END_MARKS = (".", "?", "!", "…")
SENTENCE_END_ENDINGS = ("다", "요", "죠", "까")
# 화자 분리 결과가 아직 없는 확정 토큰 — 직전 블록 화자에 붙이고, 없으면 첫 화자로 본다.
DEFAULT_SPEAKER = "1"

# 백프레셔 상한 — 넘으면 **잠정만** 버린다. 확정·AI 증분·오류는 버리지 않는다.
OUTBOX_MAX = 64

# 정상 종료에서 남은 확정 토큰을 기다리는 상한. 넘으면 받은 데까지 적재한다 — 회의를 붙들어 두지 않는다.
DRAIN_TIMEOUT_SECONDS = 8.0
# 오디오가 이만큼 없으면 keepalive 를 보낸다. provider 의 idle timeout(실측 20초)보다 넉넉히 짧게 잡는다.
KEEPALIVE_IDLE_SECONDS = 10.0


# --- 클라이언트 → 서버 ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AudioDeclaration:
    """`auth.audio` 로 선언한 형식. 서버가 업스트림 config 에 그대로 옮긴다 (SPEC §5.2-3)."""

    format: str
    sample_rate: int
    channels: int


@dataclass(frozen=True, slots=True)
class AudioFrame:
    chunk: bytes


@dataclass(frozen=True, slots=True)
class IgnoredFrame:
    """계약에 없는 텍스트 프레임. 닫지 않고 버린다 — `pause`/`resume` 이 여기로 온다 (SPEC §2.2)."""


@dataclass(frozen=True, slots=True)
class ClientGone:
    """클라이언트가 끊었다. 세션은 여기서 끝난다 — 재연결하지 않는다."""


InboundFrame = AudioFrame | IgnoredFrame | ClientGone


# --- 서버 → 클라이언트 (SPEC §5.3 5종) ------------------------------------------


@dataclass(frozen=True, slots=True)
class ReadyFrame:
    """provider 연결 성립 후. 재연결에서도 온다 — 회의 시작 시각·최대 배치 회차·화자 수."""

    meeting_started_at: datetime
    latest_batch_seq: int
    speaker_count: int


@dataclass(frozen=True, slots=True)
class PartialSegment:
    speaker_label: str
    at_ms: int
    text: str


@dataclass(frozen=True, slots=True)
class TranscriptPartialFrame:
    """**교체 렌더** — 올 때마다 이전 잠정을 통째로 바꾼다. 저장하지 않는다. 밀리면 버린다."""

    segments: list[PartialSegment] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class TranscriptItem:
    """적재된 확정 블록 하나. `at_ms` 는 회의 시작 기준 오프셋이다 — 근거 타임칩이 이 값에 걸린다."""

    id: str
    speaker_label: str
    at_ms: int
    end_ms: int
    content: str


@dataclass(frozen=True, slots=True)
class TranscriptFinalFrame:
    """**추가 렌더** — 적재와 push 가 같은 시점이다."""

    item: TranscriptItem


@dataclass(frozen=True, slots=True)
class AiBatchFrame:
    """그 회차가 낸 **AI 트랙 전체**. 화면은 통째로 교체한다.

    SCAX-WP-003 이 `push_ai_batch` 로 채운다 — 이 WP 는 통로만 연다.
    """

    seq: int
    agendas: list[dict]


@dataclass(frozen=True, slots=True)
class MemoLineFrame:
    """사람이 방금 던진 메모 줄 하나 — **추가 렌더**다 (사용자 결정 2026-09-11).

    참여자는 폴링하지 않고 같은 소켓에 붙어 받는다. 메모는 회의를 만든 사람 하나가 쓰지만, 그 줄을
    보는 것은 방에 붙은 모두다 — 쓴 사람의 화면만 바뀌면 나머지는 회의가 멈춘 것처럼 보인다.
    """

    agenda_id: str
    line: dict


@dataclass(frozen=True, slots=True)
class AgendaAddedFrame:
    """회의 중에 새로 선 안건 하나 — **추가 렌더**다 (사용자 결정 D45, 2026-09-11).

    안건은 주최자가 세우지만 그 목록을 보는 것은 방에 붙은 모두다. 참여자 화면이 새 안건을 모르면
    주최자가 그 안건에 적는 메모가 어디에도 걸리지 않은 것처럼 보인다.
    """

    agenda: dict


@dataclass(frozen=True, slots=True)
class StreamErrorFrame:
    """`error{meeting_stream_disconnected, reason}` — 이 뒤 연결을 닫는다. 재연결하지 않는다."""

    reason: str


OutboundFrame = (
    ReadyFrame
    | TranscriptPartialFrame
    | TranscriptFinalFrame
    | AiBatchFrame
    | MemoLineFrame
    | AgendaAddedFrame
    | StreamErrorFrame
)


# --- 포트 -------------------------------------------------------------------


class StreamClientClosed(Exception):
    """보낼 곳이 없다 — 세션 종료 신호이지 실패가 아니다."""


class StreamClient(Protocol):
    """`entrypoints` 가 WebSocket 으로 구현한다. dto ↔ JSON 변환은 거기서만 일어난다."""

    async def receive(self) -> InboundFrame: ...

    async def send(self, frame: OutboundFrame) -> None: ...

    async def close(self, code: int, reason: str) -> None: ...


@dataclass(frozen=True, slots=True)
class SttToken:
    text: str
    is_final: bool
    # 익명 라벨 번호 문자열. 확인되지 않은 사람에게 이름을 지어내지 않는다 (SPEC §5.4-4).
    speaker: str | None
    start_ms: int
    end_ms: int


class SttUpstreamError(Exception):
    """업스트림 연결 실패·끊김·provider 오류 — 설계한 실패 하나 (`error{reason:"upstream"}`)."""


class SttSession(Protocol):
    async def send_audio(self, chunk: bytes) -> None: ...

    def tokens(self) -> AsyncIterator[list[SttToken]]: ...

    def finish(self, *, timeout_seconds: float) -> AsyncIterator[list[SttToken]]:
        """입력이 끝났음을 알리고 **남은 확정 토큰을 끝까지 받는다**.

        마지막 몇 초는 「더 올 오디오가 없다」는 신호를 받고서야 확정된다 — 그것을 기다리지 않고 닫으면
        그 구간이 원문에서 사라진다. 상한 안에 끝나지 않으면 받은 데까지다.
        """

    async def keepalive(self) -> None:
        """오디오가 한동안 없을 때 연결을 살려 둔다 — provider 의 idle timeout 이 회의를 끊지 않게."""

    async def close(self) -> None: ...


class SttConnector(Protocol):
    async def connect(self, audio: AudioDeclaration) -> SttSession: ...


class AudioOriginalStore(Protocol):
    """오디오 원본은 중계 경로에서 적재한다. 별도 업로드 경로를 두지 않는다 (SPEC §5.5-2).

    `append` 는 저장 위치(key)를 돌려준다. 그 값은 어느 응답에도 나가지 않는다 (§5.5-4).
    """

    def append(self, meeting_id: str, chunk: bytes, *, extension: str) -> str: ...

    def extension_for(self, audio_format: str) -> str: ...


# --- 확정 블록 경계 -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TranscriptBlock:
    speaker_label: str
    at_ms: int
    end_ms: int
    content: str


def ends_sentence(raw: str) -> bool:
    """이 토큰에서 문장이 끝났는가 — 끊을 자리를 고르는 힌트다 (D51).

    마침표·물음표·느낌표가 있으면 확실하다. 받아 적은 말에는 그것이 자주 빠지므로 한국어 종결 어미
    뒤에 **공백이 따라오는 것**도 끝으로 본다: 공백이 없으면 아직 그 어절이 이어지는 중이다.
    「그러다 」처럼 종결이 아닌데 걸리는 경우가 있지만, 그때 손해는 한 문장 일찍 끊기는 것뿐이다.
    """
    stripped = raw.rstrip()
    if not stripped:
        return False
    if stripped.endswith(SENTENCE_END_MARKS):
        return True
    return raw != stripped and stripped.endswith(SENTENCE_END_ENDINGS)


@dataclass
class _Piece:
    """블록을 이루는 토큰 하나. **끊는 자리는 언제나 토큰 경계다** — 시각을 쪼갤 수 없기 때문이다."""

    text: str
    start_ms: int
    end_ms: int
    sentence_end: bool


@dataclass
class _OpenBlock:
    speaker: str
    pieces: list[_Piece] = field(default_factory=list)

    @property
    def start_ms(self) -> int:
        return self.pieces[0].start_ms if self.pieces else 0

    @property
    def end_ms(self) -> int:
        return max(piece.end_ms for piece in self.pieces) if self.pieces else 0

    @property
    def text(self) -> str:
        return "".join(piece.text for piece in self.pieces)

    def close(self) -> TranscriptBlock | None:
        """공백뿐인 블록은 행이 되지 않는다."""
        content = self.text.strip()
        if not content:
            return None
        return TranscriptBlock(self.speaker, self.start_ms, self.end_ms, content)


def _fits(pieces: list[_Piece]) -> bool:
    """상한 안인가 — 글자와 **시간** 둘 다 본다."""
    if not pieces:
        return True
    if len(("".join(piece.text for piece in pieces)).strip()) > BLOCK_MAX_CHARS:
        return False
    return max(piece.end_ms for piece in pieces) - pieces[0].start_ms <= BLOCK_MAX_MS


def _cut_at(pieces: list[_Piece]) -> int:
    """어디서 끊을 것인가 — **상한 안의 마지막 문장 끝**, 없으면 상한이다 (D51).

    토큰 하나가 이미 상한을 넘으면 그 하나로 끊는다: 더 쪼갤 자리가 없다.
    """
    limit = 1
    for index in range(1, len(pieces) + 1):
        if _fits(pieces[:index]):
            limit = index
        else:
            break
    for index in range(limit, 0, -1):
        if pieces[index - 1].sentence_end:
            return index
    return limit


class BlockBuilder:
    """확정 토큰을 받아 블록을 닫아 준다 — **경계 판정이 사는 유일한 자리**.

    화자가 바뀌거나 · 토큰 사이 침묵이 이어지거나 · 길이나 시간 상한을 넘으면 닫는다 (SPEC §5.4-2 · D51).
    상한을 넘어 끊을 때는 **상한 안의 마지막 문장 끝**을 먼저 찾는다 — 문장 가운데서 잘린 블록은
    그 자체로 읽히지 않고, 근거 칩이 반쪽을 가리키게 된다.
    `base_ms` 가 업스트림 시각을 회의 시작 기준(0)으로 옮긴다.
    """

    def __init__(self, *, base_ms: int = 0) -> None:
        self.base_ms = base_ms
        self._block: _OpenBlock | None = None

    @property
    def open_speaker(self) -> str | None:
        """아직 닫히지 않은 블록의 화자 — `ready.speakerCount` 가 이것까지 센다."""
        return None if self._block is None else self._block.speaker

    def add(self, token: SttToken) -> list[TranscriptBlock]:
        """토큰 하나를 넣고 그 결과 닫힌 블록들을 돌려준다.

        여럿이 될 수 있다 — 화자가 바뀌어 앞 블록이 닫히고, 이어 붙인 뒤 상한을 넘겨 또 끊기는 경우다.
        """
        closed: list[TranscriptBlock] = []
        speaker = token.speaker or (self._block.speaker if self._block else DEFAULT_SPEAKER)
        piece = _Piece(
            text=token.text,
            start_ms=self.base_ms + token.start_ms,
            end_ms=self.base_ms + token.end_ms,
            sentence_end=ends_sentence(token.text),
        )

        block = self._block
        if block is not None and (speaker != block.speaker or piece.start_ms - block.end_ms >= BLOCK_GAP_MS):
            closed.extend(self.flush())
            block = None

        if block is None:
            block = _OpenBlock(speaker=speaker)
            self._block = block
        block.pieces.append(piece)

        # 상한을 넘는 동안 앞에서부터 끊어 낸다 — 한 토큰이 여러 블록을 닫을 수 있다.
        while not _fits(block.pieces) and len(block.pieces) > 1:
            cut = _cut_at(block.pieces)
            head, rest = block.pieces[:cut], block.pieces[cut:]
            if not rest:
                break
            finished = _OpenBlock(speaker=block.speaker, pieces=head).close()
            if finished is not None:
                closed.append(finished)
            block.pieces = rest
        return closed

    def flush(self) -> list[TranscriptBlock]:
        """열린 블록을 닫는다 — 스트림이 끝나는 자리에서 부른다."""
        block, self._block = self._block, None
        if block is None:
            return []
        closed = block.close()
        return [] if closed is None else [closed]


def build_blocks(tokens: Iterable[SttToken], *, base_ms: int = 0) -> list[TranscriptBlock]:
    """토큰 전량을 한 번에 묶는다 — 같은 경계를 지난다."""
    builder = BlockBuilder(base_ms=base_ms)
    blocks: list[TranscriptBlock] = []
    for token in tokens:
        blocks.extend(builder.add(token))
    blocks.extend(builder.flush())
    return blocks
