"""회의 스트림 세션 — 브라우저 WS ↔ 우리 서버 ↔ STT provider 를 한 세션으로 묶는 자리.

SCAX-SPEC-004 §5.2·§5.3·§5.5. 못박는 것 —

1. **회의당 세션 레지스트리 하나.** 오디오를 올리는 연결은 회의당 하나이고 두 번째 업스트림은 `4409
   meeting_stream_active` 로 거절한다 (§5.2-5). 구독 연결은 여럿이다.
2. **업스트림은 클라이언트 인증이 끝난 뒤에 연다** — 과금과 300분 한도가 걸린다.
3. 오디오 청크는 **① 원본 append → ② 업스트림 전달** 순. ①이 실패하면 ②로 가지 않고 그 자리에서 끊는다 —
   원본이 남지 않는 회의를 조용히 계속하지 않는다 (§5.5-3).
4. 잠정은 밀어 주기만 하고 저장하지 않는다. 확정 블록은 **적재와 push 가 같은 시점**이다 (§5.4-1 · §10-11).
5. 자동 재연결·자동 재시도를 두지 않는다. 설계한 실패는 `SttUpstreamError`(→ `upstream`)와
   `OSError`(→ `write_failed`) 둘뿐이고 그 밖은 전파한다 (§5.2-7).
6. **종료 처리는 `finally`** — 어느 경로로 끊기든 업스트림을 닫고 레지스트리에서 뺀다.

이 모듈은 전송도 ORM 도 모른다. 프레임은 `stream.py` 의 dto 로 주고받고, 저장은 `StreamGateway` 포트가 한다.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from dataclasses import dataclass
from typing import Any, Protocol

from ax_workspace.modules.meetings.stream import (
    AgendaAddedFrame,
    MemoLineFrame,
    CLOSE_AFTER_ERROR,
    CLOSE_CONFLICT,
    CLOSE_ENDED,
    CLOSE_NOT_FOUND,
    DISCONNECT_UPSTREAM,
    DRAIN_TIMEOUT_SECONDS,
    KEEPALIVE_IDLE_SECONDS,
    DISCONNECT_WRITE_FAILED,
    OUTBOX_MAX,
    REASON_ENDED,
    REASON_DISCONNECTED,
    REASON_INVALID_STATUS,
    REASON_NOT_FOUND,
    REASON_NOT_OWNER,
    REASON_STREAM_ACTIVE,
    ROLE_UPSTREAM,
    AiBatchFrame,
    AudioDeclaration,
    AudioFrame,
    AudioOriginalStore,
    BlockBuilder,
    ClientGone,
    OutboundFrame,
    PartialSegment,
    ReadyFrame,
    StreamClient,
    StreamClientClosed,
    StreamErrorFrame,
    SttConnector,
    SttSession,
    SttToken,
    SttUpstreamError,
    TranscriptBlock,
    TranscriptFinalFrame,
    TranscriptItem,
    TranscriptPartialFrame,
)


class StreamGateway(Protocol):
    """스트림이 원장에 묻는 것 전부. 세션 경계와 트랜잭션은 조립층(`bootstrap`)이 소유한다.

    WS 는 요청 경계가 없다 — 단계마다 세션을 열고 끝에 커밋하는 것이 이 포트의 구현 계약이다.
    """

    def admit(self, member_id: str, meeting_id: str) -> Any | None:
        """참석 판정과 상태를 한 번에 묻는다. 열 수 없으면 `None` — 존재를 알리지 않는다."""

    def append_block(self, meeting_id: str, block: TranscriptBlock) -> str:
        """확정 블록 한 행을 적재하고 그 식별자를 돌려준다."""

    def ready_state(self, meeting_id: str) -> tuple[int, int]:
        """`(최대 배치 회차, 확정 발화의 화자 수)`."""

    def note_recording_file(self, meeting_id: str, storage_key: str, *, content_type: str) -> None:
        """오디오 원본의 자리를 한 번 기록한다. 이 값은 어느 응답에도 나가지 않는다."""


class MeetingAdmission:
    """열람과 상태 판정의 결과. `started_at` 이 `at_ms` 의 기준점이다.

    `is_owner` 가 업스트림 자리의 축이다 — 구독은 참석·공유로 열리지만 오디오를 올리는 것은 만든 사람 하나다.
    """

    __slots__ = ("meeting_id", "status", "started_at", "is_owner")

    def __init__(self, meeting_id: str, status: str, started_at: datetime, *, is_owner: bool = False) -> None:
        self.meeting_id = meeting_id
        self.status = status
        self.started_at = started_at
        self.is_owner = is_owner


class MeetingStreamService:
    """회의당 하나의 업스트림과 여럿의 구독을 들고 있는 레지스트리."""

    def __init__(
        self,
        *,
        gateway: StreamGateway,
        connector_factory: Any,
        store: AudioOriginalStore,
        running_status: str = "in_progress",
    ) -> None:
        self._gateway = gateway
        self._connector_factory = connector_factory
        self._store = store
        self._running_status = running_status
        self._rooms: dict[str, _Room] = {}

    # --- 진입점 ---------------------------------------------------------------

    def has_upstream(self, meeting_id: str) -> bool:
        room = self._rooms.get(meeting_id)
        return room is not None and room.upstream is not None

    async def serve(
        self,
        client: StreamClient,
        *,
        member_id: str,
        meeting_id: str,
        role: str,
        audio: AudioDeclaration | None,
    ) -> None:
        """인증(4401)은 라우터가 끝냈다. 여기서 참석(4404) · 상태(4409) · 단일 업스트림(4409) 을 가른다."""
        admission = self._gateway.admit(member_id, meeting_id)
        if admission is None:
            # 없는 회의와 참석 아닌 회의를 같은 답으로 닫는다 — 존재를 알리지 않는다 (§3.2-1).
            await client.close(CLOSE_NOT_FOUND, REASON_NOT_FOUND)
            return
        if admission.status != self._running_status:
            await client.close(CLOSE_CONFLICT, REASON_INVALID_STATUS)
            return

        room = self._rooms.setdefault(meeting_id, _Room(meeting_id))
        if role == ROLE_UPSTREAM:
            if not admission.is_owner:
                # 첫 프레임의 역할 선언을 그대로 믿지 않는다 — 오디오를 받기 전, provider 를 열기 전에 닫는다.
                await client.close(CLOSE_CONFLICT, REASON_NOT_OWNER)
                self._drop_if_empty(room)
                return
            if room.upstream is not None:
                # 첫 세션은 영향이 없다 — 두 번째만 닫는다.
                await client.close(CLOSE_CONFLICT, REASON_STREAM_ACTIVE)
                self._drop_if_empty(room)
                return
            await self._serve_upstream(room, client, admission, audio or _DEFAULT_AUDIO)
        else:
            await self._serve_subscriber(room, client, admission)

    async def close_for_end(self, meeting_id: str) -> bool:
        """[회의 종료] — 살아 있는 업스트림과 구독 연결을 모두 `1000` 으로 닫는다 (SPEC §5.3 정상 종료).

        스트림이 없어도 종료는 진행된다 — 종료는 상태 전이이지 스트림 조작이 아니다.
        """
        room = self._rooms.get(meeting_id)
        if room is None:
            return False
        await room.end()
        return True

    def push_ai_batch_threadsafe(self, meeting_id: str, *, seq: int, agendas: list[dict]) -> bool:
        """배치는 다른 스레드에서 커밋한다 — 프레임은 연결이 사는 루프에서 깨운다 (`_Outbox` 가 그 일을 안다)."""
        room = self._rooms.get(meeting_id)
        if room is None:
            return False
        room.broadcast(AiBatchFrame(seq=seq, agendas=list(agendas)))
        return True

    async def push_ai_batch(self, meeting_id: str, *, seq: int, agendas: list[dict]) -> bool:
        """배치 커밋 직후 **AI 트랙 전체** push. SCAX-WP-003 이 부른다 — 이 WP 에는 호출자가 없다.

        세션이 없으면 건너뛴다 — 다음 `ready.latestBatchSeq` 로 따라잡는다.
        """
        room = self._rooms.get(meeting_id)
        if room is None:
            return False
        room.broadcast(AiBatchFrame(seq=seq, agendas=list(agendas)))
        return True

    def push_memo_line_threadsafe(self, meeting_id: str, *, agenda_id: str, line: dict) -> bool:
        """메모가 쓰인 그 자리에서 방 전체에 민다 — HTTP 는 다른 스레드다 (`_Outbox` 가 그 일을 안다).

        세션이 없으면 건너뛴다: 메모는 이미 저장됐고, 화면은 다음에 열 때 목록으로 본다.
        """
        room = self._rooms.get(meeting_id)
        if room is None:
            return False
        room.broadcast(MemoLineFrame(agenda_id=agenda_id, line=dict(line)))
        return True

    def push_agenda_added_threadsafe(self, meeting_id: str, *, agenda: dict) -> bool:
        """회의 중 선 안건을 방 전체에 민다 — HTTP 는 다른 스레드다 (`_Outbox` 가 그 일을 안다, D45)."""
        room = self._rooms.get(meeting_id)
        if room is None:
            return False
        room.broadcast(AgendaAddedFrame(agenda=dict(agenda)))
        return True

    # --- 업스트림 -------------------------------------------------------------

    async def _serve_upstream(
        self, room: _Room, client: StreamClient, admission: MeetingAdmission, audio: AudioDeclaration
    ) -> None:
        session = _UpstreamSession(
            room=room,
            client=client,
            gateway=self._gateway,
            store=self._store,
            connector=self._connector_factory(),
            admission=admission,
            audio=audio,
        )
        room.upstream = session
        try:
            await session.run()
        finally:
            if room.upstream is session:
                room.upstream = None
            await session.shutdown()
            self._drop_if_empty(room)

    async def _serve_subscriber(
        self, room: _Room, client: StreamClient, admission: MeetingAdmission
    ) -> None:
        subscriber = _Subscriber(client)
        room.subscribers.add(subscriber)
        latest_batch_seq, speaker_count = self._gateway.ready_state(admission.meeting_id)
        subscriber.enqueue(
            ReadyFrame(
                meeting_started_at=admission.started_at,
                latest_batch_seq=latest_batch_seq,
                speaker_count=speaker_count,
            )
        )
        try:
            await subscriber.run()
        finally:
            room.subscribers.discard(subscriber)
            self._drop_if_empty(room)

    def _drop_if_empty(self, room: _Room) -> None:
        if room.upstream is None and not room.subscribers and self._rooms.get(room.meeting_id) is room:
            del self._rooms[room.meeting_id]


_DEFAULT_AUDIO = AudioDeclaration(format="auto", sample_rate=16_000, channels=1)


def _now_ms_since(started_at: datetime) -> int:
    started = started_at if started_at.tzinfo is not None else started_at.replace(tzinfo=UTC)
    return max(0, int((datetime.now(UTC) - started).total_seconds() * 1000))


class _Room:
    """한 회의의 연결들. 업스트림 하나, 구독 여럿."""

    def __init__(self, meeting_id: str) -> None:
        self.meeting_id = meeting_id
        self.upstream: _UpstreamSession | None = None
        self.subscribers: set[_Subscriber] = set()

    def broadcast(self, frame: OutboundFrame) -> None:
        """확정·AI 증분은 업스트림 자신에게도 간다 — 화면이 하나의 계약만 읽는다."""
        if self.upstream is not None:
            self.upstream.enqueue(frame)
        for subscriber in tuple(self.subscribers):
            subscriber.enqueue(frame)

    async def end(self) -> None:
        if self.upstream is not None:
            await self.upstream.end()
        for subscriber in tuple(self.subscribers):
            await subscriber.end()


@dataclass(frozen=True, slots=True)
class _CloseRequest:
    """다른 연결이 이 연결을 닫아 달라고 큐에 넣는 표. 소켓은 자기 루프에서만 만진다."""

    code: int
    reason: str


class _Outbox:
    """보내는 쪽 큐 하나. 밀리면 **잠정만** 버린다 — 확정·AI 증분·오류는 버리지 않는다.

    한 회의의 연결들이 서로에게 프레임을 넣는다. 그 연결들이 같은 event loop 위에 있다는 보장이 없으므로
    (시험 하네스·멀티 루프 서버) 큐를 소유한 루프를 기억해 두고 밖에서 온 것은 그 루프에서 깨운다.
    소켓을 직접 만지는 것은 언제나 `pump` 뿐이다 — 닫기도 프레임으로 온다.
    """

    def __init__(self, client: StreamClient) -> None:
        self._client = client
        self._queue: asyncio.Queue[OutboundFrame | _CloseRequest] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None

    def enqueue(self, frame: OutboundFrame) -> None:
        if isinstance(frame, TranscriptPartialFrame) and self._queue.qsize() >= OUTBOX_MAX:
            return
        self._put(frame)

    def request_close(self, code: int, reason: str) -> None:
        self._put(_CloseRequest(code, reason))

    def _put(self, item: OutboundFrame | _CloseRequest) -> None:
        loop = self._loop
        try:
            current: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if loop is not None and loop is not current and not loop.is_closed():
            loop.call_soon_threadsafe(self._queue.put_nowait, item)
            return
        self._queue.put_nowait(item)

    async def pump(self) -> None:
        self._loop = asyncio.get_running_loop()
        while True:
            item = await self._queue.get()
            if isinstance(item, _CloseRequest):
                try:
                    await self._client.close(item.code, item.reason)
                except StreamClientClosed:
                    pass
                return
            try:
                await self._client.send(item)
            except StreamClientClosed:
                return


class _Subscriber:
    """구독 전용 연결 — 오디오를 보내지 않는다 (SPEC §5.2-6)."""

    def __init__(self, client: StreamClient) -> None:
        self.client = client
        self._outbox = _Outbox(client)

    def enqueue(self, frame: OutboundFrame) -> None:
        self._outbox.enqueue(frame)

    async def end(self) -> None:
        """[회의 종료] — 정상 종료다. 오류 프레임을 앞세우지 않는다."""
        self._outbox.request_close(CLOSE_ENDED, REASON_ENDED)

    async def run(self) -> None:
        await _race(
            (self._drain(), "subscriber-client"),
            (self._outbox.pump(), "subscriber-outbox"),
        )

    async def _drain(self) -> None:
        """구독 연결이 보내는 것은 없다. 끊김만 본다 — 오디오가 와도 버린다."""
        while True:
            frame = await self.client.receive()
            if isinstance(frame, ClientGone):
                return


class _UpstreamSession:
    def __init__(
        self,
        *,
        room: _Room,
        client: StreamClient,
        gateway: StreamGateway,
        store: AudioOriginalStore,
        connector: SttConnector,
        admission: MeetingAdmission,
        audio: AudioDeclaration,
    ) -> None:
        self.room = room
        self.client = client
        self.audio = audio
        self._gateway = gateway
        self._store = store
        self._connector = connector
        self._meeting_id = admission.meeting_id
        self._started_at = admission.started_at
        self._extension = store.extension_for(audio.format)
        self._upstream: SttSession | None = None
        self._base_ms = 0
        self._blocks = BlockBuilder()
        self._speakers: set[str] = set()
        self._recording_noted = False
        self._failed: str | None = None
        self._last_audio_at = 0.0
        self._outbox = _Outbox(client)

    def enqueue(self, frame: OutboundFrame) -> None:
        self._outbox.enqueue(frame)

    # --- 실행 -----------------------------------------------------------------

    async def run(self) -> None:
        try:
            self._upstream = await self._connector.connect(self.audio)
        except SttUpstreamError:
            # 아직 펌프가 돌지 않는다 — 오류와 닫기를 넣고 그 자리에서 한 번 흘려보낸다.
            await self._fail(DISCONNECT_UPSTREAM)
            await self._outbox.pump()
            return
        self._base_ms = _now_ms_since(self._started_at)
        self._blocks.base_ms = self._base_ms
        self._last_audio_at = asyncio.get_running_loop().time()
        self._send_ready()
        await _race(
            (self._pump_client(), "stream-client"),
            (self._pump_upstream(), "stream-upstream"),
            (self._outbox.pump(), "stream-outbox"),
            (self._pump_keepalive(), "stream-keepalive"),
        )

    async def end(self) -> None:
        """[회의 종료] — 정상 종료다. 오류 프레임을 앞세우지 않는다."""
        self._outbox.request_close(CLOSE_ENDED, REASON_ENDED)

    async def shutdown(self) -> None:
        """`finally` 에서 부른다 — **남은 확정 발화를 끝까지 받아** 적재하고 업스트림을 반드시 닫는다.

        마지막 몇 초는 「더 올 오디오가 없다」는 신호를 받고서야 확정된다. 그것을 기다리지 않고 닫으면
        회의의 끝부분이 원문에서 사라진다 — 정상 종료에서만 기다리고, 업스트림이 이미 끊긴 실패 경로에서는
        받은 것까지만 적재한다(기다릴 상대가 없다).
        """
        try:
            try:
                if self._upstream is not None and self._failed is None:
                    await self._drain_upstream()
            finally:
                # 드레인이 어떻게 끝나든 **열린 블록은 적재한다** — 기다리다 잃는 것이 이 자리의 실패다.
                for block in self._blocks.flush():
                    self._persist_block(block, push=False)
        finally:
            if self._upstream is not None:
                await self._upstream.close()
                self._upstream = None

    async def _drain_upstream(self) -> None:
        """상한 안에 오는 확정 토큰을 블록에 넣는다. 잠정은 보낼 곳이 없으므로 버린다."""
        assert self._upstream is not None
        try:
            async for tokens in self._upstream.finish(timeout_seconds=DRAIN_TIMEOUT_SECONDS):
                for token in tokens:
                    if not token.is_final:
                        continue
                    for block in self._blocks.add(token):
                        self._persist_block(block, push=False)
        except SttUpstreamError:
            # 닫는 중에 끊겼다 — 받은 것까지가 이 회의의 원문이다.
            return

    # --- 클라이언트 → 서버 -------------------------------------------------------

    async def _pump_client(self) -> None:
        while True:
            frame = await self.client.receive()
            if isinstance(frame, ClientGone):
                return
            if isinstance(frame, AudioFrame) and not await self._feed(frame.chunk):
                return

    async def _pump_keepalive(self) -> None:
        """오디오가 한동안 없으면 연결을 살려 둔다.

        provider 는 조용한 업스트림을 끊는다(실측 20초). 마이크가 멈췄다고 회의가 통째로 끊기면 안 된다 —
        브라우저가 무음을 보내는 동안에는 이 자리가 아무것도 하지 않는다.
        """
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(KEEPALIVE_IDLE_SECONDS / 2)
            if self._upstream is None:
                return
            if loop.time() - self._last_audio_at < KEEPALIVE_IDLE_SECONDS:
                continue
            try:
                await self._upstream.keepalive()
            except SttUpstreamError:
                await self._fail(DISCONNECT_UPSTREAM)
                return
            self._last_audio_at = loop.time()

    async def _feed(self, chunk: bytes) -> bool:
        """① 원본 append → ② 업스트림 전달. 거짓이면 세션이 끝난다(이미 오류 프레임을 보냈다)."""
        if self._upstream is None:
            # `ready` 전에 온 프레임은 버린다 — 오류가 아니다.
            return True
        self._last_audio_at = asyncio.get_running_loop().time()
        try:
            key = self._store.append(self._meeting_id, chunk, extension=self._extension)
        except OSError:
            await self._fail(DISCONNECT_WRITE_FAILED)
            return False
        if not self._recording_noted:
            self._gateway.note_recording_file(self._meeting_id, key, content_type=self.audio.format)
            self._recording_noted = True
        try:
            await self._upstream.send_audio(chunk)
        except SttUpstreamError:
            await self._fail(DISCONNECT_UPSTREAM)
            return False
        return True

    # --- 업스트림 → 서버 ---------------------------------------------------------

    async def _pump_upstream(self) -> None:
        assert self._upstream is not None
        try:
            async for tokens in self._upstream.tokens():
                self._handle_tokens(tokens)
        except SttUpstreamError:
            await self._fail(DISCONNECT_UPSTREAM)
            return
        # 우리가 닫기 전에 업스트림이 끝났다 — 끊긴 것과 같다.
        await self._fail(DISCONNECT_UPSTREAM)

    def _handle_tokens(self, tokens: list[SttToken]) -> None:
        partials: list[SttToken] = []
        for token in tokens:
            if token.is_final:
                for block in self._blocks.add(token):
                    self._persist_block(block)
            else:
                partials.append(token)
        # 잠정도 **구독자 전부**에게 간다 (`OQ-310` 확정, 사용자 결정 2026-09-11) — 참여자는 폴링하지 않고
        # 같은 소켓으로 실시간을 받는다. 밀리면 버려지므로(`_Outbox` 가 잠정을 먼저 버린다) 느린 구독자
        # 하나가 방을 붙잡지 않는다.
        self.room.broadcast(TranscriptPartialFrame(segments=self._segments(partials)))

    def _segments(self, tokens: list[SttToken]) -> list[PartialSegment]:
        segments: list[PartialSegment] = []
        for token in tokens:
            speaker = token.speaker or (segments[-1].speaker_label if segments else self._blocks.open_speaker or "1")
            if segments and segments[-1].speaker_label == speaker:
                last = segments[-1]
                segments[-1] = PartialSegment(last.speaker_label, last.at_ms, last.text + token.text)
            else:
                segments.append(PartialSegment(speaker, self._base_ms + token.start_ms, token.text))
        return segments

    def _persist_block(self, block: TranscriptBlock, *, push: bool = True) -> None:
        """**적재와 `transcript.final` push 가 같은 시점.** 블록이 닫히는 자리가 배치 트리거의 자리이기도 하다."""
        block_id = self._gateway.append_block(self._meeting_id, block)
        self._speakers.add(block.speaker_label)
        if push:
            self.room.broadcast(
                TranscriptFinalFrame(
                    item=TranscriptItem(
                        id=block_id,
                        speaker_label=block.speaker_label,
                        at_ms=block.at_ms,
                        end_ms=block.end_ms,
                        content=block.content,
                    )
                )
            )

    # --- 서버 → 클라이언트 --------------------------------------------------------

    def _send_ready(self) -> None:
        latest_batch_seq, speaker_count = self._gateway.ready_state(self._meeting_id)
        seen = set(self._speakers)
        if self._blocks.open_speaker is not None:
            seen.add(self._blocks.open_speaker)
        self.enqueue(
            ReadyFrame(
                meeting_started_at=self._started_at,
                latest_batch_seq=latest_batch_seq,
                speaker_count=max(speaker_count, len(seen)),
            )
        )

    async def _fail(self, reason: str) -> None:
        """설계한 실패 둘 — 오류 프레임을 직접 보내고 닫는다. 재연결하지 않는다."""
        if self._failed is not None:
            return
        self._failed = reason
        self._outbox.enqueue(StreamErrorFrame(reason=reason))
        self._outbox.request_close(CLOSE_AFTER_ERROR, REASON_DISCONNECTED)


async def _race(*coroutines: tuple[Any, str]) -> None:
    """먼저 끝나는 하나가 세션을 끝낸다. 설계 밖 예외는 그대로 올라간다."""
    tasks = [asyncio.create_task(coroutine, name=name) for coroutine, name in coroutines]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in pending:
        try:
            await task
        except asyncio.CancelledError:
            pass
    for task in done:
        task.result()
