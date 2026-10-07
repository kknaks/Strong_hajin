"""사용자 사건 채널의 **듣는 쪽** — Postgres `LISTEN ax_user_events` → 그 회원의 WS 들 (SPEC-008 §4.4 · N-6 · BE-3).

보내는 쪽은 `platform/user_events.publish`(NOTIFY, 트랜잭션 커밋 때 전달)이고 계약은 `modules/external_channels/events.py`
다. back 한 프로세스에 듣는 연결은 **하나**다 — 첫 구독자가 생길 때 데몬 스레드 하나가 `LISTEN` 하고, 받은 페이로드를
`UserEvent.from_payload` 로 풀어 **받는 사람(`member_id`)의 큐에만** 넣는다. 깨진·모르는 판 페이로드는 버린다.

**`integration.changed` 는 같은 회원·같은 연동에 1초에 한 번**으로 묶는다(SPEC-008 §4.4 v0.6.0 · OQ-818). 창의 첫 사건은
곧바로 보내고, 창 안에 더 오면 마지막 하나를 창이 닫힐 때 보낸다 — 백필이 쪽마다 사건을 내도 화면은 초에 한 번 다시 읽고,
마지막 숫자는 놓치지 않는다. 다른 사건(새 메시지·답장 결과)은 묶지 않는다.

연결이 끊기면 물러섰다가 다시 붙는다(그 사이 사건은 잃는다 — 화면은 WS 재연결 때 API 로 다시 읽는다). PostgreSQL 이 아닌
DB(시험)에서는 듣는 스레드를 띄우지 않고, 같은 프로세스의 쓰기가 커밋 뒤 `dispatch` 로 직접 흘린다.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
import logging
import threading
import time

from collections.abc import Callable
from dataclasses import replace

from ax_workspace.modules.external_channels.events import USER_EVENTS_CHANNEL, UserEvent, UserEventType

logger = logging.getLogger(__name__)
QUEUE_LIMIT = 200
#: 같은 회원·같은 연동의 `integration.changed` 를 묶는 창(초) — OQ-818 코디 기본값 · 실측 뒤 조정.
COALESCE_SECONDS = 1.0


def _start_timer(delay: float, callback: Callable[[], None]) -> None:
    timer = threading.Timer(delay, callback)
    timer.daemon = True
    timer.start()


def _libpq_url(database_url: str) -> str:
    for prefix in ("postgresql+psycopg://", "postgresql+psycopg2://"):
        if database_url.startswith(prefix):
            return "postgresql://" + database_url[len(prefix):]
    return database_url


class UserEventHub:
    def __init__(
        self,
        database_url: str,
        *,
        coalesce_seconds: float | None = None,
        schedule: Callable[[float, Callable[[], None]], None] = _start_timer,
    ) -> None:
        self._database_url = database_url
        # 안 주면 모듈 값을 **만들 때** 읽는다 — 계약 시험 앱이 그 값을 0 으로 갈아 끼워 진짜 1초 타이머를 돌리지 않는다.
        self._coalesce_seconds = COALESCE_SECONDS if coalesce_seconds is None else coalesce_seconds
        self._schedule = schedule
        #: (회원, 연동) → 창 안에 밀린 마지막 사건(없으면 None). 키가 있으면 창이 열려 있다.
        self._windows: dict[tuple[str, str], UserEvent | None] = {}
        self._listen = database_url.startswith("postgresql")
        self._lock = threading.Lock()
        self._subscribers: dict[str, set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = defaultdict(set)
        self._thread: threading.Thread | None = None
        self._stopped = threading.Event()

    def subscribe(self, member_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_LIMIT)
        entry = (asyncio.get_running_loop(), queue)
        with self._lock:
            self._subscribers[member_id].add(entry)
            if self._listen and (self._thread is None or not self._thread.is_alive()):
                self._thread = threading.Thread(target=self._run, name="ax-user-events-listener", daemon=True)
                self._thread.start()
        return queue

    def unsubscribe(self, member_id: str, queue: asyncio.Queue) -> None:
        with self._lock:
            entries = self._subscribers.get(member_id)
            if not entries:
                return
            entries.difference_update({entry for entry in entries if entry[1] is queue})
            if not entries:
                self._subscribers.pop(member_id, None)

    def dispatch(self, payload: str) -> None:
        event = UserEvent.from_payload(payload)
        if event is None:
            return
        if event.type == UserEventType.INTEGRATION_CHANGED and event.integration_id and self._coalesce_seconds > 0:
            if not self._open_window(event):
                return
        self._deliver(event)

    def _open_window(self, event: UserEvent) -> bool:
        """창이 닫혀 있으면 열고 True(곧바로 보낸다). 열려 있으면 마지막 사건으로 갈아 두고 False."""
        key = (event.member_id, event.integration_id or "")
        with self._lock:
            if key in self._windows:
                pending = self._windows[key]
                if pending is not None and pending.room_id != event.room_id:
                    event = replace(event, room_id=None)  # 여러 방이 바뀌었다 — 연동 전체를 다시 읽으라는 뜻
                self._windows[key] = event
                return False
            self._windows[key] = None
        self._schedule(self._coalesce_seconds, lambda: self._close_window(key))
        return True

    def _close_window(self, key: tuple[str, str]) -> None:
        with self._lock:
            pending = self._windows.pop(key, None)
            if pending is not None:
                self._windows[key] = None  # 밀린 것을 보내며 창을 하나 더 연다 — 계속 몰려도 초에 한 번
        if pending is None:
            return
        self._schedule(self._coalesce_seconds, lambda: self._close_window(key))
        self._deliver(pending)

    def _deliver(self, event: UserEvent) -> None:
        with self._lock:
            targets = list(self._subscribers.get(event.member_id, ()))
        for loop, queue in targets:
            try:
                loop.call_soon_threadsafe(_offer, queue, event)
            except RuntimeError:  # 그 루프는 이미 닫혔다
                continue

    def close(self) -> None:
        self._stopped.set()

    def _run(self) -> None:
        import psycopg

        backoff = 1.0
        while not self._stopped.is_set():
            try:
                with psycopg.connect(_libpq_url(self._database_url), autocommit=True) as connection:
                    connection.execute(f"LISTEN {USER_EVENTS_CHANNEL}")
                    backoff = 1.0
                    while not self._stopped.is_set():
                        for notice in connection.notifies(timeout=5.0):
                            self.dispatch(notice.payload)
            except Exception as error:  # noqa: BLE001 — 듣기가 죽어도 back 은 산다. 다시 붙는다.
                logger.warning("user event listener reconnecting after %s", type(error).__name__)
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)


def _offer(queue: asyncio.Queue, event: UserEvent) -> None:
    if queue.full():
        try:
            queue.get_nowait()  # 느린 화면은 가장 오래된 것을 잃는다 — 사건은 «다시 읽으라»는 신호일 뿐이다
        except asyncio.QueueEmpty:
            pass
    queue.put_nowait(event)
