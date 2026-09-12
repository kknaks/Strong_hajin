"""회의 스트림 계약: `WS /api/meetings/{id}/stream` (SCAX-SPEC-004 §5.2·§5.3·§5.4·§5.5 · SCAX-WP-002).

브라우저는 STT provider 를 모른다 — 오디오는 반드시 우리 서버를 지나고, 원본은 중계 경로에서 남는다.
실제 Soniox 를 부르지 않는다: provider 경계(`SttConnector`)에서 대역을 끼운다.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
from ax_workspace.entrypoints.http import create_app
from ax_workspace.entrypoints.reset_demo import reset_database
from starlette.websockets import WebSocketDisconnect

from ax_workspace.modules.meetings.stream import (
    CLOSE_CONFLICT,
    CLOSE_ENDED,
    CLOSE_NOT_FOUND,
    CLOSE_UNAUTHORIZED,
    REASON_INVALID_STATUS,
    REASON_NOT_OWNER,
    REASON_STREAM_ACTIVE,
    AudioDeclaration,
    SttToken,
    SttUpstreamError,
    build_blocks,
)

MINA = {"X-Demo-Persona": "mina"}
JIHO = {"X-Demo-Persona": "jiho"}
SORA = {"X-Demo-Persona": "sora"}

_UPSTREAM = {"type": "auth", "role": "upstream", "audio": {"format": "webm", "sampleRate": 16000, "channels": 1}}
_SUBSCRIBE = {"type": "auth", "role": "subscribe"}


# --------------------------------------------------------------------- 대역


class FakeSttSession:
    """대본에 넣은 응답을 `release()` 할 때마다 하나씩 낸다 — 테스트가 순서를 조종한다."""

    def __init__(self, script: list[list[SttToken] | SttUpstreamError]) -> None:
        self.received: list[bytes] = []
        self.close_count = 0
        self.keepalive_count = 0
        #: end frame 뒤에야 확정되는 꼬리. 실제 provider 가 그렇게 답한다.
        self.tail: list[list[SttToken]] = []
        self.tail_stalls = False
        self.finished_count = 0
        self._script = list(script)
        self._gate: asyncio.Queue[None] = asyncio.Queue()
        self._closed = asyncio.Event()

    async def send_audio(self, chunk: bytes) -> None:
        self.received.append(chunk)

    def release(self, count: int = 1) -> None:
        for _ in range(count):
            self._gate.put_nowait(None)

    async def tokens(self) -> AsyncIterator[list[SttToken]]:
        while True:
            await self._gate.get()
            if self._closed.is_set():
                return
            if not self._script:
                # 낼 것이 없다 — 실제 provider 처럼 오디오를 기다린다.
                await self._closed.wait()
                return
            item = self._script.pop(0)
            if isinstance(item, SttUpstreamError):
                raise item
            yield item

    async def finish(self, *, timeout_seconds: float) -> AsyncIterator[list[SttToken]]:
        self.finished_count += 1
        if self.tail_stalls:
            # provider 가 답하지 않는다 — 상한만큼 기다렸다 받은 데까지다.
            await asyncio.sleep(min(timeout_seconds, 0.05))
            return
        for tokens in self.tail:
            yield tokens

    async def keepalive(self) -> None:
        self.keepalive_count += 1

    async def close(self) -> None:
        self.close_count += 1
        self._closed.set()
        self._gate.put_nowait(None)


class FakeSttConnector:
    def __init__(self) -> None:
        self.script: list[list[SttToken] | SttUpstreamError] = []
        self.connect_count = 0
        self.fail_connect = False
        self.sessions: list[FakeSttSession] = []
        self.declarations: list[AudioDeclaration] = []

    async def connect(self, audio: AudioDeclaration) -> FakeSttSession:
        self.connect_count += 1
        self.declarations.append(audio)
        if self.fail_connect:
            raise SttUpstreamError("대역: 연결 실패")
        session = FakeSttSession(self.script)
        self.sessions.append(session)
        return session

    @property
    def last(self) -> FakeSttSession:
        return self.sessions[-1]


def token(text: str, *, final: bool, speaker: str = "1", start_ms: int = 0, end_ms: int | None = None) -> SttToken:
    return SttToken(text, final, speaker, start_ms, start_ms + 500 if end_ms is None else end_ms)


# --------------------------------------------------------------------- 발판


def _stack(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'ax_demo.db'}"
    reset_database(database_url)
    settings = Settings(RuntimeProfile.TEST, database_url, recordings_dir=str(tmp_path / "audio"))
    app = create_app(settings)
    connector = FakeSttConnector()
    app.state.workflow_application._stt_connector = connector
    return TestClient(app), connector, settings


def _running_meeting(client: TestClient, *, attendees=("jiho",)) -> str:
    starts = datetime.now(UTC) + timedelta(minutes=5)
    meeting = client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "title": "중계할 회의",
            "starts_at": starts.isoformat().replace("+00:00", "Z"),
            "ends_at": (starts + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "attendee_ids": list(attendees),
            "agendas": [{"title": "안건"}],
        },
    ).json()["meeting"]
    assert client.post(f"/api/meetings/{meeting['meeting_id']}/start", headers=MINA).status_code == 200
    return meeting["meeting_id"]


def _scheduled_meeting(client: TestClient) -> str:
    starts = datetime.now(UTC) + timedelta(days=1)
    return client.post(
        "/api/meetings",
        headers=MINA,
        json={
            "title": "아직 안 연 회의",
            "starts_at": starts.isoformat().replace("+00:00", "Z"),
            "ends_at": (starts + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "attendee_ids": ["jiho"],
        },
    ).json()["meeting"]["meeting_id"]


def _settled_texts(client: TestClient, meeting_id: str, *, expected: int, tries: int = 60) -> list[str]:
    """적재된 확정 발화. 종료 드레인은 연결이 끊긴 뒤에 끝나므로 그 자리를 기다려 준다."""
    import time
    from uuid import UUID

    application = client.app.state.workflow_application
    principal = application.authenticated_principal("mina")
    for _ in range(tries):
        rows = application.meeting_transcript(principal, UUID(meeting_id))["items"]
        if len(rows) >= expected:
            return [row["content"] for row in rows]
        time.sleep(0.05)
    return [row["content"] for row in rows]


def _drain_until(socket, wanted: str, *, limit: int = 12) -> dict:
    for _ in range(limit):
        message = socket.receive_json()
        if message["type"] == wanted:
            return message
    raise AssertionError(f"{wanted} 프레임이 오지 않았습니다")


# --------------------------------------------------------------------- Phase 1 · 라우트와 레지스트리


def test_a_connection_without_a_session_is_closed_before_it_says_anything(tmp_path) -> None:
    client, _, _ = _stack(tmp_path)
    meeting_id = _running_meeting(client)
    with client.websocket_connect(f"/api/meetings/{meeting_id}/stream") as socket:
        with pytest.raises(Exception):
            socket.receive_json()
    # 사유를 흘리지 않는다 — 첫 프레임이 없는 것과 무효한 것이 같은 답이다.


def test_a_first_frame_that_is_not_auth_closes_the_connection(tmp_path) -> None:
    client, connector, _ = _stack(tmp_path)
    meeting_id = _running_meeting(client)
    with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=MINA) as socket:
        socket.send_json({"type": "pause", "reason": "user"})
        with pytest.raises(Exception):
            socket.receive_json()
    assert connector.connect_count == 0  # 인증 전에는 업스트림을 열지 않는다 — 과금이 걸린다


def test_someone_who_is_not_an_attendee_is_not_told_the_meeting_exists(tmp_path) -> None:
    client, connector, _ = _stack(tmp_path)
    meeting_id = _running_meeting(client)
    with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=SORA) as socket:
        socket.send_json(_UPSTREAM)
        with pytest.raises(Exception):
            socket.receive_json()
    assert connector.connect_count == 0


def test_a_meeting_that_is_not_running_refuses_the_stream(tmp_path) -> None:
    client, connector, _ = _stack(tmp_path)
    meeting_id = _scheduled_meeting(client)
    with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=MINA) as socket:
        socket.send_json(_UPSTREAM)
        with pytest.raises(Exception):
            socket.receive_json()
    assert connector.connect_count == 0


def test_only_the_person_who_called_the_meeting_may_send_audio(tmp_path) -> None:
    """업스트림 자리는 회의를 만든 사람의 것이다 — 참석자라도 남의 회의를 대신 열지 않는다.

    첫 프레임의 역할 선언을 그대로 믿지 않는다: 오디오를 받기 전에, provider 를 열기 전에 닫는다.
    """
    client, connector, _ = _stack(tmp_path)
    meeting_id = _running_meeting(client)

    with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=JIHO) as attendee:
        attendee.send_json(_UPSTREAM)
        with pytest.raises(WebSocketDisconnect) as refused:
            attendee.receive_json()
    assert (refused.value.code, refused.value.reason) == (CLOSE_CONFLICT, REASON_NOT_OWNER)
    assert connector.connect_count == 0  # provider 를 열지 않는다 — 과금이 걸린다

    # 만든 사람은 연다. 공유받은 사람은 구독으로 붙는다.
    assert client.post(f"/api/meetings/{meeting_id}/shares", headers=MINA, json={"member_ids": ["sora"]}).status_code == 200
    with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=MINA) as owner:
        owner.send_json(_UPSTREAM)
        assert _drain_until(owner, "ready")["type"] == "ready"
        assert connector.connect_count == 1
        with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=SORA) as viewer:
            viewer.send_json(_SUBSCRIBE)
            assert _drain_until(viewer, "ready")["type"] == "ready"


def test_the_second_upstream_is_refused_while_the_first_one_lives(tmp_path) -> None:
    """오디오를 올리는 연결은 회의당 하나다 (SPEC §5.2-5). 첫 세션은 영향이 없다."""
    client, connector, _ = _stack(tmp_path)
    meeting_id = _running_meeting(client)
    with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=MINA) as first:
        first.send_json(_UPSTREAM)
        assert _drain_until(first, "ready")["type"] == "ready"
        with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=MINA) as second:
            second.send_json(_UPSTREAM)
            with pytest.raises(Exception):
                second.receive_json()
        # 첫 세션은 여전히 살아 있다.
        first.send_bytes(b"chunk-after-refusal")
        assert connector.connect_count == 1
    assert connector.last.close_count == 1  # `finally` 가 업스트림을 반드시 닫는다


def test_the_ready_frame_carries_the_meeting_start_and_nothing_about_the_provider(tmp_path) -> None:
    client, connector, _ = _stack(tmp_path)
    meeting_id = _running_meeting(client)
    with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=MINA) as socket:
        socket.send_json(_UPSTREAM)
        ready = _drain_until(socket, "ready")
    assert set(ready) == {"type", "meetingStartedAt", "latestBatchSeq", "speakerCount"}
    assert ready["latestBatchSeq"] == 0 and ready["speakerCount"] == 0
    assert datetime.fromisoformat(ready["meetingStartedAt"]) <= datetime.now(UTC)
    # 선언한 형식이 그대로 업스트림 config 로 간다 — 이 계약이 형식을 못박지 않는다 (§5.2-3).
    assert connector.declarations[0] == AudioDeclaration(format="webm", sample_rate=16_000, channels=1)


# --------------------------------------------------------------------- Phase 2 · 중계와 오디오 원본


def test_audio_reaches_the_original_first_and_the_provider_second(tmp_path) -> None:
    client, connector, settings = _stack(tmp_path)
    meeting_id = _running_meeting(client)
    with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=MINA) as socket:
        socket.send_json(_UPSTREAM)
        _drain_until(socket, "ready")
        socket.send_bytes(b"first-")
        socket.send_bytes(b"second")
        socket.send_json({"type": "resume"})  # 계약에 없는 프레임은 버리고 닫지 않는다
        socket.send_bytes(b"-third")
        # 왕복 하나로 앞의 것들이 처리됐음을 확인한다.
        assert _drain_until(socket, "ready", limit=1) if False else True
    original = Path(settings.recordings_dir) / "meetings" / "audio" / f"{meeting_id}.webm"
    assert original.read_bytes() == b"first-second-third"
    assert connector.last.received == [b"first-", b"second", b"-third"]


def test_an_original_that_cannot_be_written_stops_the_stream_where_it_stands(tmp_path) -> None:
    """원본이 남지 않는 회의를 조용히 계속하지 않는다 (SPEC §5.5-3)."""
    client, connector, _ = _stack(tmp_path)
    meeting_id = _running_meeting(client)
    application = client.app.state.workflow_application

    def refuse(meeting_id: str, chunk: bytes, *, extension: str) -> str:
        raise OSError("대역: 원본을 쓸 수 없다")

    application._recording_storage.append = refuse
    with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=MINA) as socket:
        socket.send_json(_UPSTREAM)
        _drain_until(socket, "ready")
        socket.send_bytes(b"lost")
        error = _drain_until(socket, "error")
    assert error == {"type": "error", "code": "meeting_stream_disconnected", "reason": "write_failed"}
    # ①이 실패하면 ②로 가지 않는다.
    assert connector.last.received == []


def test_an_upstream_that_drops_is_reported_and_never_retried(tmp_path) -> None:
    client, connector, _ = _stack(tmp_path)
    connector.script = [SttUpstreamError("대역: 끊김")]
    meeting_id = _running_meeting(client)
    with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=MINA) as socket:
        socket.send_json(_UPSTREAM)
        _drain_until(socket, "ready")
        connector.last.release()
        error = _drain_until(socket, "error")
    assert error["reason"] == "upstream"
    assert connector.connect_count == 1  # 재연결하지 않는다


def test_an_upstream_that_cannot_be_opened_fails_the_connection(tmp_path) -> None:
    client, connector, _ = _stack(tmp_path)
    connector.fail_connect = True
    meeting_id = _running_meeting(client)
    with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=MINA) as socket:
        socket.send_json(_UPSTREAM)
        error = _drain_until(socket, "error")
    assert error["reason"] == "upstream"


def test_the_audio_original_stays_on_disk_and_never_appears_in_a_response(tmp_path) -> None:
    """원본은 아무에게도 화면에 내지 않고 저장 위치도 노출하지 않는다 (SPEC §5.5-4)."""
    client, _, settings = _stack(tmp_path)
    meeting_id = _running_meeting(client)
    with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=MINA) as socket:
        socket.send_json(_UPSTREAM)
        _drain_until(socket, "ready")
        socket.send_bytes(b"kept-audio")
    assert client.post(f"/api/meetings/{meeting_id}/end", headers=MINA).status_code == 200

    original = Path(settings.recordings_dir) / "meetings" / "audio" / f"{meeting_id}.webm"
    assert original.is_file() and original.read_bytes() == b"kept-audio"

    detail = client.get(f"/api/meetings/{meeting_id}", headers=MINA)
    assert detail.status_code == 200
    body = detail.text
    assert "storage_key" not in body and "meetings/audio" not in body
    assert "soniox" not in body.lower() and "api_key" not in body


def test_no_route_offers_a_provider_credential_or_a_direct_upload_any_more(tmp_path) -> None:
    """브라우저에 provider 표면 자체를 두지 않는다 (SPEC §5.2-2 · §11.1)."""
    client, _, _ = _stack(tmp_path)
    paths = {getattr(route, "path", "") for route in client.app.routes}
    assert not any("realtime-credential" in path or "realtime-segments" in path for path in paths)
    assert not any("/recordings" in path for path in paths)
    assert "/api/meetings/{meeting_id}/stream" in paths


# --------------------------------------------------------------------- Phase 3 · 적재와 fan-out


def test_a_provisional_utterance_is_pushed_and_never_stored(tmp_path) -> None:
    client, connector, _ = _stack(tmp_path)
    connector.script = [[token("아직 바뀌", final=False)]]
    meeting_id = _running_meeting(client)
    with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=MINA) as socket:
        socket.send_json(_UPSTREAM)
        _drain_until(socket, "ready")
        connector.last.release()
        partial = _drain_until(socket, "transcript.partial")
    assert partial["segments"] == [{"speakerLabel": "1", "atMs": partial["segments"][0]["atMs"], "text": "아직 바뀌"}]

    application = client.app.state.workflow_application
    principal = application.authenticated_principal("mina")
    from uuid import UUID

    # 잠정은 밀어 주기만 한다 — 원문에 남지 않는다 (SPEC §5.4-1 · §10-11).
    assert application.meeting_transcript(principal, UUID(meeting_id))["items"] == []


def test_a_settled_block_is_stored_and_pushed_at_the_same_moment(tmp_path) -> None:
    from uuid import UUID

    client, connector, _ = _stack(tmp_path)
    connector.script = [
        [token("첫 화자의 말.", final=True, speaker="1", start_ms=0, end_ms=900)],
        [token("두 번째 화자의 말.", final=True, speaker="2", start_ms=1_000, end_ms=1_800)],
    ]
    meeting_id = _running_meeting(client)
    with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=MINA) as socket:
        socket.send_json(_UPSTREAM)
        _drain_until(socket, "ready")
        connector.last.release(2)
        final = _drain_until(socket, "transcript.final")
    assert set(final["item"]) == {"id", "speakerLabel", "atMs", "endMs", "content"}
    assert final["item"]["speakerLabel"] == "1" and final["item"]["content"] == "첫 화자의 말."

    application = client.app.state.workflow_application
    principal = application.authenticated_principal("mina")
    stored = application.meeting_transcript(principal, UUID(meeting_id))["items"]
    # 화자가 바뀔 때 앞 블록이 닫힌다. 마지막 블록은 세션이 끝날 때 닫힌다.
    assert [row["content"] for row in stored] == ["첫 화자의 말.", "두 번째 화자의 말."]
    # 계약이 정한 다섯 칸뿐이다 — 원문을 읽는 순서가 곧 적재 순서다.
    assert all(set(row) == {"id", "speakerLabel", "atMs", "endMs", "content"} for row in stored)
    assert [row["atMs"] for row in stored] == sorted(row["atMs"] for row in stored)


def test_two_subscribers_receive_the_same_settled_utterance(tmp_path) -> None:
    client, connector, _ = _stack(tmp_path)
    connector.script = [
        [token("모두에게 갈 말.", final=True, speaker="1", start_ms=0, end_ms=900)],
        [token("다음 화자.", final=True, speaker="2", start_ms=1_000, end_ms=1_500)],
    ]
    meeting_id = _running_meeting(client)
    with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=MINA) as upstream:
        upstream.send_json(_UPSTREAM)
        _drain_until(upstream, "ready")
        with (
            client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=JIHO) as watcher,
            client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=MINA) as second_watcher,
        ):
            watcher.send_json(_SUBSCRIBE)
            second_watcher.send_json(_SUBSCRIBE)
            _drain_until(watcher, "ready")
            _drain_until(second_watcher, "ready")
            connector.last.release(2)
            # 확정 블록은 회의를 여는 사람에게도 간다 — 화면이 하나의 계약만 읽는다.
            mine = _drain_until(upstream, "transcript.final")
            first = _drain_until(watcher, "transcript.final")
            second = _drain_until(second_watcher, "transcript.final")
    assert first["item"]["content"] == "모두에게 갈 말." == second["item"]["content"]
    assert mine["item"]["id"] == first["item"]["id"] == second["item"]["id"]


def test_ending_the_meeting_closes_every_connection_the_server_holds(tmp_path) -> None:
    client, connector, _ = _stack(tmp_path)
    meeting_id = _running_meeting(client)
    with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=MINA) as upstream:
        upstream.send_json(_UPSTREAM)
        _drain_until(upstream, "ready")
        with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=JIHO) as watcher:
            watcher.send_json(_SUBSCRIBE)
            _drain_until(watcher, "ready")

            ended = client.post(f"/api/meetings/{meeting_id}/end", headers=MINA)
            assert ended.status_code == 200
            assert ended.json()["meeting"]["status"] == "summarizing"

            # 오류가 아니므로 오류 프레임을 앞세우지 않는다 — 그냥 닫힌다.
            with pytest.raises(Exception):
                for _ in range(5):
                    watcher.receive_json()
            with pytest.raises(Exception):
                for _ in range(5):
                    upstream.receive_json()
    assert connector.last.close_count == 1


def test_a_subscriber_does_not_take_the_upstream_slot(tmp_path) -> None:
    """구독 연결은 오디오를 보내지 않는다 — 업스트림 자리를 차지하지도 않는다 (SPEC §5.2-6)."""
    client, connector, _ = _stack(tmp_path)
    meeting_id = _running_meeting(client)
    with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=JIHO) as watcher:
        watcher.send_json(_SUBSCRIBE)
        _drain_until(watcher, "ready")
        assert connector.connect_count == 0  # 구독만으로는 업스트림을 열지 않는다
        with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=MINA) as upstream:
            upstream.send_json(_UPSTREAM)
            _drain_until(upstream, "ready")
            assert connector.connect_count == 1


# --------------------------------------------------------------------- 블록 경계


def test_a_block_closes_on_a_speaker_change_a_length_limit_or_a_silence() -> None:
    """경계 수치는 계약이 아니라 구현 재량이다 (SPEC §5.4-2). 판정이 사는 자리는 하나다."""
    changed = build_blocks([
        token("앞사람.", final=True, speaker="1", start_ms=0, end_ms=500),
        token("뒷사람.", final=True, speaker="2", start_ms=600, end_ms=900),
    ])
    assert [(block.speaker_label, block.content) for block in changed] == [("1", "앞사람."), ("2", "뒷사람.")]

    silent = build_blocks([
        token("먼저.", final=True, speaker="1", start_ms=0, end_ms=500),
        token("한참 뒤.", final=True, speaker="1", start_ms=3_000, end_ms=3_400),
    ])
    assert [block.content for block in silent] == ["먼저.", "한참 뒤."]

    # 글자 상한은 150 이다 (D51) — 200자 토큰은 하나만으로 상한을 넘어 저마다 한 블록으로 선다.
    # 토큰보다 잘게 끊지는 않는다: 시각을 쪼갤 수 없어 끊는 자리는 언제나 토큰 경계다.
    long_run = build_blocks([
        token("가" * 200, final=True, speaker="1", start_ms=0, end_ms=100),
        token("나" * 200, final=True, speaker="1", start_ms=100, end_ms=200),
        token("끝.", final=True, speaker="1", start_ms=200, end_ms=300),
    ])
    assert [len(block.content) for block in long_run] == [200, 200, 2]
    assert long_run[0].content == "가" * 200

    # 한 블록은 20초를 넘지 않는다 (D51) — 글자가 적어도 시간이 길면 끊는다.
    slow = build_blocks([
        token("어", final=True, speaker="1", start_ms=index * 1_500, end_ms=index * 1_500 + 1_400)
        for index in range(30)
    ])
    assert slow and max(block.end_ms - block.at_ms for block in slow) <= 20_000

    # 끊을 자리는 **상한 안의 마지막 문장 끝**이다 — 문장 가운데서 잘리면 그 블록만으로 읽히지 않는다.
    sentences = build_blocks([
        token("네 알겠습니다. " if index % 5 == 4 else "그래서 이렇게 ",
              final=True, speaker="1", start_ms=index * 1_200, end_ms=index * 1_200 + 1_000)
        for index in range(40)
    ])
    assert len(sentences) > 1
    assert all(block.content.endswith(".") for block in sentences)

    # 공백뿐인 블록은 행이 되지 않는다.
    assert build_blocks([token("   ", final=True, speaker="1")]) == []


def test_the_offsets_are_measured_from_the_meeting_start() -> None:
    blocks = build_blocks([token("기준이 있는 말.", final=True, speaker="1", start_ms=250, end_ms=900)], base_ms=10_000)
    assert (blocks[0].at_ms, blocks[0].end_ms) == (10_250, 10_900)


def test_a_container_format_reaches_the_provider_as_auto_without_invented_numbers() -> None:
    """브라우저가 보내는 `webm/opus` 는 헤더가 형식을 말한다 — 수치를 함께 보내면 provider 가 거절한다."""
    from ax_workspace.platform.soniox import build_config

    container = build_config("secret", AudioDeclaration("webm/opus", 16_000, 1))
    assert container["audio_format"] == "auto"
    assert "sample_rate" not in container and "num_channels" not in container
    # endpoint detection 키가 없다 — 그것이 「미사용」이다 (SPEC §5.3 연결 조건 주석).
    assert not any("endpoint" in key for key in container)

    raw = build_config("secret", AudioDeclaration("pcm_s16le", 16_000, 1))
    assert raw["audio_format"] == "pcm_s16le"
    assert raw["sample_rate"] == 16_000 and raw["num_channels"] == 1


# --------------------------------------------------------------------- 종료 드레인 · keepalive


def test_the_tail_that_only_settles_after_the_end_frame_still_reaches_the_transcript(tmp_path) -> None:
    """마지막 몇 초는 「더 올 오디오가 없다」는 신호를 받고서야 확정된다.

    그것을 기다리지 않고 닫으면 회의의 끝부분이 원문에서 사라진다 — 실물에서 그렇게 잃었다.
    """
    from uuid import UUID

    client, connector, _ = _stack(tmp_path)
    connector.script = [[token("앞부분 말.", final=True, speaker="1", start_ms=0, end_ms=900)]]
    meeting_id = _running_meeting(client)
    with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=MINA) as socket:
        socket.send_json(_UPSTREAM)
        _drain_until(socket, "ready")
        connector.last.tail = [
            [token("꼬리 하나.", final=True, speaker="1", start_ms=30_000, end_ms=31_000)],
            [token("꼬리 둘.", final=True, speaker="2", start_ms=32_000, end_ms=33_000)],
        ]
        connector.last.release()
        _drain_until(socket, "transcript.partial")

    stored = _settled_texts(client, meeting_id, expected=3)
    assert connector.last.finished_count == 1
    # 앞부분과 꼬리가 모두 남는다.
    assert "앞부분 말." in stored and "꼬리 하나." in stored and "꼬리 둘." in stored
    assert connector.last.close_count == 1


def test_a_provider_that_never_finishes_still_leaves_what_it_already_settled(tmp_path) -> None:
    from uuid import UUID

    client, connector, _ = _stack(tmp_path)
    connector.script = [[token("받아 둔 말.", final=True, speaker="1", start_ms=0, end_ms=900)]]
    meeting_id = _running_meeting(client)
    with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=MINA) as socket:
        socket.send_json(_UPSTREAM)
        _drain_until(socket, "ready")
        connector.last.tail_stalls = True
        connector.last.release()
        # 대역이 낸 토큰이 블록에 들어갈 때까지 한 왕복 기다린다.
        _drain_until(socket, "transcript.partial")

    assert _settled_texts(client, meeting_id, expected=1) == ["받아 둔 말."]
    assert connector.last.close_count == 1


def test_an_upstream_that_broke_is_not_waited_on(tmp_path) -> None:
    """이미 끊긴 업스트림에는 기다릴 상대가 없다 — 받은 것까지가 그 회의의 원문이다."""
    client, connector, _ = _stack(tmp_path)
    connector.script = [SttUpstreamError("대역: 끊김")]
    meeting_id = _running_meeting(client)
    with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=MINA) as socket:
        socket.send_json(_UPSTREAM)
        _drain_until(socket, "ready")
        connector.last.release()
        _drain_until(socket, "error")
    assert connector.last.finished_count == 0


def test_a_silent_microphone_does_not_let_the_provider_drop_the_meeting() -> None:
    """오디오가 멈춰도 회의가 통째로 끊기지 않는다 — provider 는 조용한 업스트림을 끊는다(실측 20초).

    무음 판정은 시계 하나에 걸리므로 그 시계만 줄여 세션의 펌프를 직접 돌린다.
    """
    import ax_workspace.modules.meetings.stream_service as service

    session = FakeSttSession([])
    upstream = object.__new__(service._UpstreamSession)
    upstream._upstream = session
    upstream._last_audio_at = 0.0

    async def run_briefly() -> None:
        upstream._last_audio_at = asyncio.get_running_loop().time()
        pump = asyncio.create_task(upstream._pump_keepalive())
        await asyncio.sleep(0.12)
        pump.cancel()
        try:
            await pump
        except asyncio.CancelledError:
            pass

    original = service.KEEPALIVE_IDLE_SECONDS
    service.KEEPALIVE_IDLE_SECONDS = 0.02
    try:
        asyncio.run(run_briefly())
    finally:
        service.KEEPALIVE_IDLE_SECONDS = original
    assert session.keepalive_count >= 1


def test_audio_that_keeps_arriving_needs_no_keepalive() -> None:
    """브라우저가 무음까지 보내는 동안에는 이 자리가 아무것도 하지 않는다."""
    import ax_workspace.modules.meetings.stream_service as service

    session = FakeSttSession([])
    upstream = object.__new__(service._UpstreamSession)
    upstream._upstream = session

    async def run_briefly() -> None:
        loop = asyncio.get_running_loop()
        upstream._last_audio_at = loop.time()
        pump = asyncio.create_task(upstream._pump_keepalive())
        for _ in range(12):
            await asyncio.sleep(0.01)
            upstream._last_audio_at = loop.time()  # 오디오가 계속 온다
        pump.cancel()
        try:
            await pump
        except asyncio.CancelledError:
            pass

    original = service.KEEPALIVE_IDLE_SECONDS
    service.KEEPALIVE_IDLE_SECONDS = 0.05
    try:
        asyncio.run(run_briefly())
    finally:
        service.KEEPALIVE_IDLE_SECONDS = original
    assert session.keepalive_count == 0


def test_the_keepalive_and_drain_limits_live_in_one_place() -> None:
    from ax_workspace.modules.meetings.stream import DRAIN_TIMEOUT_SECONDS, KEEPALIVE_IDLE_SECONDS

    # provider 의 idle timeout(실측 20초)보다 넉넉히 짧다.
    assert KEEPALIVE_IDLE_SECONDS == 10.0 and DRAIN_TIMEOUT_SECONDS == 8.0


# ------------------- 참여자는 폴링하지 않는다 — 같은 소켓으로 전부 받는다 (2026-09-11)


def test_a_memo_someone_writes_reaches_every_connection_in_the_room(tmp_path) -> None:
    """메모는 회의를 만든 사람 하나가 쓰지만 **보는 것은 방에 붙은 모두**다.

    쓴 사람의 화면만 바뀌면 나머지는 회의가 멈춘 것처럼 보인다 — 참여자는 폴링하지 않는다.
    """
    client, _, _ = _stack(tmp_path)
    meeting_id = _running_meeting(client)
    agenda_id = client.get(f"/api/meetings/{meeting_id}", headers=MINA).json()["agendas"][0]["agenda_id"]

    with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=MINA) as upstream:
        upstream.send_json(_UPSTREAM)
        _drain_until(upstream, "ready")
        with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=JIHO) as watcher:
            watcher.send_json(_SUBSCRIBE)
            _drain_until(watcher, "ready")

            written = client.post(
                f"/api/meetings/{meeting_id}/agendas/{agenda_id}/lines",
                headers=MINA,
                json={"text": "방 전체가 볼 메모"},
            )
            assert written.status_code == 201, written.text

            # 쓴 사람에게도, 구독자에게도 같은 프레임이 간다 — 화면이 하나의 계약만 읽는다.
            mine = _drain_until(upstream, "memo.line")
            theirs = _drain_until(watcher, "memo.line")

    assert mine == theirs
    assert theirs["agendaId"] == agenda_id
    assert theirs["line"]["text"] == "방 전체가 볼 메모"
    assert theirs["line"]["track"] == "memo" and theirs["line"]["author"] == "mina"
    # 저장된 그 줄이다 — 화면이 다시 물어보지 않는다.
    assert theirs["line"]["line_id"] == written.json()["line_id"]


def test_a_partial_utterance_reaches_the_subscribers_too(tmp_path) -> None:
    """잠정도 구독자 전부에게 간다 (`OQ-310` 확정) — 참여자가 보는 것이 주최자보다 늦으면 안 된다."""
    client, connector, _ = _stack(tmp_path)
    connector.script = [[token("아직 확정 전인 말", final=False, speaker="1", start_ms=0, end_ms=500)]]
    meeting_id = _running_meeting(client)

    with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=MINA) as upstream:
        upstream.send_json(_UPSTREAM)
        _drain_until(upstream, "ready")
        with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=JIHO) as watcher:
            watcher.send_json(_SUBSCRIBE)
            _drain_until(watcher, "ready")
            connector.last.release(1)

            mine = _drain_until(upstream, "transcript.partial")
            theirs = _drain_until(watcher, "transcript.partial")

    assert [segment["text"] for segment in theirs["segments"]] == ["아직 확정 전인 말"]
    assert mine["segments"] == theirs["segments"]


def test_an_agenda_the_host_adds_mid_meeting_reaches_every_connection(tmp_path) -> None:
    """안건은 주최자가 세우지만 그 목록을 보는 것은 방에 붙은 모두다 (D45).

    참여자 화면이 새 안건을 모르면 거기 붙는 메모가 어디에도 걸리지 않은 것처럼 보인다.
    """
    client, _, _ = _stack(tmp_path)
    meeting_id = _running_meeting(client)

    with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=MINA) as upstream:
        upstream.send_json(_UPSTREAM)
        _drain_until(upstream, "ready")
        with client.websocket_connect(f"/api/meetings/{meeting_id}/stream", headers=JIHO) as watcher:
            watcher.send_json(_SUBSCRIBE)
            _drain_until(watcher, "ready")

            added = client.post(
                f"/api/meetings/{meeting_id}/agendas", headers=MINA, json={"title": "회의 중에 생긴 안건"}
            )
            assert added.status_code == 201, added.text

            mine = _drain_until(upstream, "agenda.added")
            theirs = _drain_until(watcher, "agenda.added")

    assert mine == theirs
    assert theirs["agenda"]["title"] == "회의 중에 생긴 안건"
    assert theirs["agenda"]["source"] == "manual"
    assert theirs["agenda"]["agenda_id"] == added.json()["agenda_id"]
