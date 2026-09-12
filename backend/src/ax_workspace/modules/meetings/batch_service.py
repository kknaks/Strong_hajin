"""회의 중 AI 배치 — 언제 낼지, 무엇을 실을지, 받은 것을 어떻게 믿을지.

SCAX-SPEC-004 §7.1. 못박는 것 —

1. **트리거 판정은 `evaluate(meeting_id, cause)` 하나다.** 셋 중 먼저 오는 것이 배치를 낸다 —
   ① 미처리 확정 발화가 분량에 닿음 ② 안건 전환 즉시(미처리가 너무 적으면 생략) ③ 미처리가 생긴 뒤 경과.
2. **회의당 배치 하나.** 도는 중에 온 트리거는 아무것도 하지 않는다 — 끝난 뒤 커진 구간으로 낸다.
3. 검증은 두 단이다 — 스키마 위반이면 **배치 전체 폐기**(직전 성공분 유지), 못 믿을 참조는 **그 줄만 강등**.
4. **커서는 성공분만 전진한다.** 실패·폐기 구간은 다음 배치에 합쳐진다.
5. **provider 호출 중에는 트랜잭션을 열어 두지 않는다** — 읽기 → 커밋 → 제출 → 새 세션에서 쓰기.
6. 적재 커밋 **직후** push 한다. 모아 두지 않는다.
7. 실패는 조용하다 — 화면에 내지 않고 회의를 막지 않는다.

이 모듈은 전송도 ORM 도 모른다. 원장은 `BatchGateway` 포트가, provider 는 `BatchAgent` 포트가 맡는다.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
from typing import Any, Protocol

from ax_workspace.modules.meetings.batch import (
    BATCH_CHARS,
    BATCH_MAX_WAIT_SECONDS,
    BATCH_SWITCH_MIN_CHARS,
    CAUSE_AGENDA_SWITCH,
    CAUSE_TIMER,
    CAUSE_TRANSCRIPT,
    STATUS_DISCARDED,
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    BatchAgenda,
    SchemaViolation,
    build_batch_prompt,
    build_warm_start_prompt,
    demote_line,
    parse_output,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BatchInput:
    """한 회차가 읽는 것 — 세션 참조와 증분뿐이다. 앞 구간은 세션이 기억한다."""

    seq: int
    session_ref: str
    persona_id: str
    blocks: list[dict[str, Any]]
    memos: list[dict[str, Any]]
    from_seq: int
    to_seq: int
    covered_ms: tuple[int, int]
    allowed_task_ids: set[str]


class BatchAgent(Protocol):
    """provider 경계. 테스트가 여기서 대역을 끼운다 — 서비스를 목으로 바꾸지 않는다."""

    def open_session(self, *, persona_id: str, prompt: str, tools: tuple[str, ...]) -> str | None:
        """새 세션을 열고 그 참조를 돌려준다. 응답 본문은 버린다."""

    def run_batch(self, *, persona_id: str, session_ref: str, prompt: str, tools: tuple[str, ...]) -> str:
        """세션을 이어 쓰고(resume) 스키마로 강제한 출력 본문을 돌려준다."""


class BatchGateway(Protocol):
    """배치가 원장에 묻는 것 전부. 세션 경계와 트랜잭션은 조립층이 소유한다."""

    def warm_start_context(self, meeting_id: str) -> dict[str, Any] | None:
        """웜스타트 첫 turn 이 실을 맥락. 회의가 없거나 열 수 없으면 `None`."""

    def record_session(self, meeting_id: str, *, session_ref: str, persona_id: str) -> None: ...

    def load_batch_input(self, meeting_id: str) -> BatchInput | None:
        """제출할 것이 있으면 입력, 없으면(세션 없음·미처리 없음·진행 중 아님) `None`."""

    def pending_chars(self, meeting_id: str) -> int: ...

    def record_run(
        self, meeting_id: str, *, seq: int, status: str, cause: str, from_seq: int, to_seq: int, reason: str | None
    ) -> None: ...

    def replace_ai_track(self, meeting_id: str, agendas: list[BatchAgenda]) -> list[dict[str, Any]]:
        """검증 통과분으로 AI 트랙 전량 교체. 돌려주는 것은 push 가 실을 안건 트리다."""

    def push(self, meeting_id: str, *, seq: int, agendas: list[dict[str, Any]]) -> None: ...


class MeetingBatchService:
    """회의당 하나의 배치를 지키는 자리. 타이머와 락은 프로세스 안에 산다 — 표가 아니다."""

    def __init__(
        self,
        *,
        gateway: BatchGateway,
        agent: BatchAgent,
        tools: tuple[str, ...],
        chars: int = BATCH_CHARS,
        switch_min_chars: int = BATCH_SWITCH_MIN_CHARS,
        max_wait_seconds: int = BATCH_MAX_WAIT_SECONDS,
    ) -> None:
        self._gateway = gateway
        self._agent = agent
        self._tools = tools
        self._chars = chars
        self._switch_min_chars = switch_min_chars
        self._max_wait_seconds = max_wait_seconds
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._threads: set[threading.Thread] = set()
        #: 도는 중에 온 트리거의 사유. 타이머를 기다리지 않고 끝난 뒤 그 자리에서 다시 본다.
        self._deferred: dict[str, str] = {}

    # --- 상태 -----------------------------------------------------------------
    #
    # 배치는 처음부터 끝까지 블로킹이다 — DB 도 provider 도 동기다. 그래서 백그라운드도 스레드 하나이고
    # 이벤트 루프에 기대지 않는다: 요청 스레드에서 불려도, WS 루프에서 불려도 같게 돈다.

    def is_running(self, meeting_id: str) -> bool:
        lock = self._locks.get(meeting_id)
        return lock is not None and lock.locked()

    def is_timer_armed(self, meeting_id: str) -> bool:
        with self._guard:
            return meeting_id in self._timers

    def _lock_for(self, meeting_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(meeting_id, threading.Lock())

    def drain(self, timeout: float = 30.0) -> None:
        """시험 격리용 — 떠 있는 백그라운드 작업을 거둔다. 제품 경로는 부르지 않는다."""
        while True:
            with self._guard:
                threads = [thread for thread in self._threads if thread.is_alive()]
            if not threads:
                return
            for thread in threads:
                thread.join(timeout)
            with self._guard:
                self._threads = {thread for thread in self._threads if thread.is_alive()}

    def reset(self) -> None:
        with self._guard:
            for handle in self._timers.values():
                handle.cancel()
            self._timers.clear()
            self._locks.clear()
            self._threads.clear()
            self._deferred.clear()

    # --- 웜스타트 ---------------------------------------------------------------

    def launch_warm_start(self, meeting_id: str) -> None:
        """`/start` 의 커밋 뒤 훅 — 스레드만 띄우고 즉시 돌아온다.

        기다리면 「회의 시작」이 provider 를 기다리게 된다. **실패 처리를 만들지 않는다**:
        실패의 결과는 세션 참조가 없는 것 하나이고, 그러면 배치가 제출되지 않을 뿐 회의는 정상이다 (SPEC §7.1).
        """
        self._spawn(self.warm_start, (meeting_id,), "회의 웜스타트")

    def warm_start(self, meeting_id: str) -> None:
        context = self._gateway.warm_start_context(meeting_id)
        if context is None:
            return
        persona_id = str(context.pop("persona_id"))
        session_ref = self._agent.open_session(
            persona_id=persona_id,
            prompt=build_warm_start_prompt(context, self._tools),
            tools=self._tools,
        )
        if not session_ref:
            logger.info("회의 %s 의 AI 세션을 열지 못했습니다 — 배치는 제출되지 않습니다", meeting_id)
            return
        self._gateway.record_session(meeting_id, session_ref=session_ref, persona_id=persona_id)

    # --- 트리거 -----------------------------------------------------------------

    def schedule(self, meeting_id: str, cause: str) -> None:
        """요청·스트림 경로에서 부르는 진입점 — **커밋 뒤** 백그라운드로 평가한다."""
        self._spawn(self.evaluate, (meeting_id, cause), "회의 배치")

    def evaluate(self, meeting_id: str, cause: str) -> bool:
        """트리거 셋 판정. 배치를 냈으면 True.

        **실행 중이면 새 배치를 내지 않는다.** 다만 그 트리거를 잊지는 않는다 — 사유를 기억해 두고
        도는 배치가 끝나는 자리에서 커진 구간으로 다시 본다. 90초 타이머를 기다리게 하지 않는다.
        """
        if self.is_running(meeting_id):
            with self._guard:
                self._deferred[meeting_id] = cause
            return False
        if cause == CAUSE_TIMER:
            with self._guard:
                self._timers.pop(meeting_id, None)

        pending = self._gateway.pending_chars(meeting_id)
        if cause == CAUSE_TRANSCRIPT:
            fire = pending >= self._chars
        elif cause == CAUSE_AGENDA_SWITCH:
            fire = pending >= self._switch_min_chars
        elif cause == CAUSE_TIMER:
            fire = pending > 0
        else:
            raise ValueError(f"알 수 없는 트리거: {cause}")

        if not fire:
            if pending > 0:
                self._arm_timer(meeting_id)
            return False
        self.run(meeting_id, cause)
        return True

    def _arm_timer(self, meeting_id: str) -> None:
        """미처리가 생긴 뒤 상한에 시간 트리거. 이미 걸려 있으면 그대로 둔다."""
        if self._max_wait_seconds <= 0:
            return
        with self._guard:
            if meeting_id in self._timers:
                return
            handle = threading.Timer(self._max_wait_seconds, self.schedule, (meeting_id, CAUSE_TIMER))
            handle.daemon = True
            self._timers[meeting_id] = handle
        handle.start()

    def _disarm_timer(self, meeting_id: str) -> None:
        with self._guard:
            handle = self._timers.pop(meeting_id, None)
        if handle is not None:
            handle.cancel()

    # --- 실행 -------------------------------------------------------------------

    def run(self, meeting_id: str, cause: str = CAUSE_TRANSCRIPT) -> None:
        """회의당 락 아래에서 한 회차. **락이 잡혀 있으면 기다리지 않고 돌아간다** — 동시 실행 0."""
        lock = self._lock_for(meeting_id)
        if not lock.acquire(blocking=False):
            return
        try:
            self._run_once(meeting_id, cause)
        finally:
            lock.release()
        self._disarm_timer(meeting_id)
        with self._guard:
            deferred = self._deferred.pop(meeting_id, None)
        if deferred is not None:
            # 도는 중에 온 트리거를 그 자리에서 갚는다 — 커진 구간으로 한 번 더 본다.
            self.schedule(meeting_id, deferred)
            return
        if self._gateway.pending_chars(meeting_id) > 0:
            self._arm_timer(meeting_id)

    def _run_once(self, meeting_id: str, cause: str) -> None:
        # ① 읽기 — 트랜잭션을 닫고 나온다.
        batch = self._gateway.load_batch_input(meeting_id)
        if batch is None:
            return

        # ② 제출 — 트랜잭션 밖. 설계한 실패는 여기서 `failed` 로 접힌다.
        try:
            body = self._agent.run_batch(
                persona_id=batch.persona_id,
                session_ref=batch.session_ref,
                prompt=build_batch_prompt(batch.blocks, batch.memos),
                tools=self._tools,
            )
        except Exception as error:  # noqa: BLE001 — provider 실패는 조용히 다음 배치로 합친다 (SPEC §7.1 실패)
            self._record(meeting_id, batch, STATUS_FAILED, cause, str(error))
            return

        # ③ 검증 1단 — 스키마 위반이면 배치 전체 폐기. 직전 성공분이 그대로 남는다.
        try:
            agendas = parse_output(body)
        except SchemaViolation as error:
            self._record(meeting_id, batch, STATUS_DISCARDED, cause, str(error))
            return

        # ④ 검증 2단 — 못 믿을 참조만 줄 단위로 뗀다. 본문은 산다.
        for agenda in agendas:
            agenda.lines = [
                demote_line(line, allowed_task_ids=batch.allowed_task_ids, covered_ms=batch.covered_ms)
                for line in agenda.lines
            ]

        # ⑤ 적재 — 전량 교체 한 트랜잭션. 커밋 직후 push.
        tree = self._gateway.replace_ai_track(meeting_id, agendas)
        self._gateway.record_run(
            meeting_id,
            seq=batch.seq,
            status=STATUS_SUCCEEDED,
            cause=cause,
            from_seq=batch.from_seq,
            to_seq=batch.to_seq,
            reason=None,
        )
        self._gateway.push(meeting_id, seq=batch.seq, agendas=tree)

    def _record(self, meeting_id: str, batch: BatchInput, status: str, cause: str, reason: str) -> None:
        """실패·폐기 기록 — **커서는 전진하지 않는다**. 사용자에게 표시할 것이 없다."""
        logger.info("회의 %s 배치 %s: %s", meeting_id, status, reason)
        self._gateway.record_run(
            meeting_id,
            seq=batch.seq,
            status=status,
            cause=cause,
            from_seq=batch.from_seq,
            to_seq=batch.to_seq,
            reason=reason,
        )

    # --- 백그라운드 --------------------------------------------------------------

    def _spawn(self, target: Any, arguments: tuple[Any, ...], what: str) -> None:
        thread = threading.Thread(target=self._guarded, args=(target, arguments, what), daemon=True)
        with self._guard:
            self._threads = {alive for alive in self._threads if alive.is_alive()}
            self._threads.add(thread)
        thread.start()

    @staticmethod
    def _guarded(target: Any, arguments: tuple[Any, ...], what: str) -> None:
        """배치는 요청 경계 밖이라 500 이 없다 — 설계 밖 예외를 여기서 스택째 드러낸다. 삼키지 않는다."""
        try:
            target(*arguments)
        except Exception:  # noqa: BLE001 — 백그라운드 스레드의 마지막 자리다. 로그가 유일한 출구다
            logger.exception("%s 가 예외로 끝났습니다", what)
