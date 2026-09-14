"""종료 합성 — 「정리 중」에서 두 트랙을 한 벌로 합쳐 「완료」로 옮기는 자리.

SCAX-SPEC-004 §8. 못박는 것 —

1. **`/end` 는 전이만 하고 즉시 답한다.** 합성은 durable job 이 뒤에서 돈다 — 사람이 종료를 누르고 기다리지 않는다.
2. **같은 세션을 이어 쓴다** (§8-3) — 회의를 처음부터 다시 읽히지 않는다. 세션이 없거나 죽었으면
   **콜드 스타트**로 확정 발화 전량을 한 번에 실어 폴백한다. 그 횟수는 기록으로 남는다.
3. 시도 상한까지 다시 걸고, 그래도 안 되면 **「실패」**다 — 받은 발화와 메모는 그대로 남고 `/finalize` 가 다시 건다.
4. **원본 두 벌은 불가침이다** (D53 · §8-5). 합성은 최종 벌만 쓰고, 재시도도 최종 벌만 갈아 끼운다.
   **한 벌이 비어도 돈다** — 정본 재료는 재전사한 원문이고 두 벌은 그 위에 얹는 재료다 (§8-3 · W-1).
5. 적재는 **한 트랜잭션** — 최종 벌 전량 신규 작성 · 후보 전량 교체(승격된 것은 유지) · 제목 후보 ·
   상태 done. 부분 결과를 남기지 않는다.
6. **provider 호출 중에는 트랜잭션을 열지 않는다** — 읽기 → 커밋 → 제출 → 새 세션에서 쓰기.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Protocol

from ax_workspace.modules.meetings.batch import SchemaViolation
from ax_workspace.modules.meetings.retranscribe import (
    FAILED_EMPTY,
    FAILED_NO_RECORDING,
    FAILED_PROVIDER,
    FAILED_RESULT,
    FAILED_TIMEOUT,
    FAILED_UPLOAD,
    SOURCE_FINAL,
    FileTranscriber,
    Recording,
    RetranscribeFailed,
)
from ax_workspace.modules.meetings.stream import TranscriptBlock, build_blocks
from ax_workspace.modules.meetings.finalize import (
    FinalizationContext,
    FINAL_ATTEMPTS,
    FinalizeFailed,
    FinalNotes,
    build_final_prompt,
    parse_final_output,
    finalize_notes,
)

logger = logging.getLogger(__name__)


#: 화면에 서는 실패 사유. **사람이 읽는 한 줄이고 내부 값이 한 자도 없다** — 회의 id·안건 id·목록·
#: SQL·예외 이름 어느 것도 여기 오지 않는다. 무엇이 왜 깨졌는지는 로그가 안다.
FAILURE_SCHEMA = "회의록을 만들지 못했습니다 — 출력 형식이 맞지 않았습니다"
FAILURE_UNFINISHED = "회의록을 만들지 못했습니다 — 잠시 뒤 다시 시도해 주세요"


def _retranscribe_reason(error: BaseException) -> str:
    """재전사가 어디서 깨졌는지를 **사람 말 한 줄**로 옮긴다 — 어댑터가 붙인 표를 읽는다.

    올리다 깨진 것과 결과를 받다 깨진 것은 사람이 다음에 할 일이 같지만(다시 시도), 무엇이 안 됐는지는
    다르게 말해 주는 편이 낫다. 어느 것도 아니면 그쪽이 거절한 것으로 본다.
    """
    stage = getattr(error, "stage", "")
    if stage == "upload":
        return FAILED_UPLOAD
    if stage == "result":
        return FAILED_RESULT
    return FAILED_PROVIDER


def _reason_of(error: BaseException) -> str:
    """사람이 화면에서 읽는 실패 사유 — **정해 둔 한 줄 중 하나**다.

    `str(error)` 를 그대로 쓰면 드라이버 예외가 SQL 문을, 검증 예외가 안건 id 목록을 화면으로 흘린다.
    실제로 그렇게 「기대 [] · 실제 [uuid]」가 사람 화면에 떴다.
    """
    return FAILURE_SCHEMA if isinstance(error, SchemaViolation) else FAILURE_UNFINISHED


class FinalizeAgent(Protocol):
    """provider 경계. 테스트가 여기서 대역을 끼운다."""

    def run_final(self, *, persona_id: str, session_ref: str | None, prompt: str) -> str:
        """세션이 있으면 이어 쓰고(resume), 없으면 새로 열어 한 번에 돈다. 스키마로 강제한 본문을 돌려준다."""


class FinalizeGateway(Protocol):
    """합성이 원장에 묻는 것. 세션 경계와 트랜잭션은 조립층이 소유한다."""

    def load_input(self, meeting_id: str) -> dict[str, Any] | None:
        """합성 입력. 「정리 중」이 아니거나 회의가 없으면 `None`."""

    def existing_task_titles(self, persona_id: str) -> set[str]:
        """이미 있는 업무의 정규화된 제목 — 후보 중복을 막는 근거다 (§8.1)."""

    def commit_success(self, meeting_id: str, notes: FinalNotes, *, cold_start: bool) -> None:
        """한 트랜잭션 — 최종 줄·후보 전량 교체 · 제목 후보 · 상태 done."""

    def commit_failure(self, meeting_id: str, reason: str) -> None:
        """상태 failed + 사유. 받은 발화와 메모는 그대로 남는다."""

    def recording_for(self, meeting_id: str) -> Recording | None:
        """다시 전사할 음원. 녹음이 없으면 `None` — 건너뛰는 것이지 실패가 아니다 (D44)."""

    def replace_transcript(self, meeting_id: str, blocks: list[TranscriptBlock]) -> int:
        """새 원문으로 전량 교체하고 몇 블록이 섰는지 돌려준다."""

    def set_transcript_source(self, meeting_id: str, source: str) -> None:
        """이 회의록이 어느 원문으로 만들어졌는지 남긴다."""


class MeetingFinalizeService:
    """회의당 한 번 도는 합성. 배치와 **같은 lock** 을 잡는다 — 회의당 provider 호출은 언제나 하나다."""

    def __init__(
        self,
        *,
        gateway: FinalizeGateway,
        agent: FinalizeAgent,
        lock_for: Any,
        attempts: int = FINAL_ATTEMPTS,
        transcriber: FileTranscriber | None = None,
    ) -> None:
        self._gateway = gateway
        self._agent = agent
        self._lock_for = lock_for
        self._attempts = attempts
        self._transcriber = transcriber
        self.cold_starts = 0
        #: 재전사가 실제로 돌아 원문을 갈아 끼운 횟수 — 시험과 운영 로그가 읽는다.
        self.retranscribes = 0

    def run(self, meeting_id: str) -> bool:
        """한 회차. 성공했으면 True. 배치가 도는 중이면 그 자리에서 기다린다 — 합성은 미룰 수 없다."""
        lock: threading.Lock = self._lock_for(meeting_id)
        with lock:
            return self._run_once(meeting_id)

    def _run_once(self, meeting_id: str) -> bool:
        # ① 재전사 — **합성 입력을 읽기 전에** 원문을 갈아 끼운다. 순서가 뒤집히면 ②가 옛 원문을 읽는다.
        # 되지 않으면 **여기서 끝난다**: 실시간 원문 위에서 합성하지 않는다 (D44 정정).
        try:
            self._retranscribe(meeting_id)
        except RetranscribeFailed as error:
            logger.warning("회의 %s 재전사가 되지 않아 합성으로 가지 않습니다: %s", meeting_id, error)
            self._gateway.commit_failure(meeting_id, error.reason)
            return False

        source = self._gateway.load_input(meeting_id)
        if source is None:
            # 「정리 중」이 아니다 — 이미 끝났거나 되돌려졌다. 잡의 재배달일 뿐이므로 조용히 성공으로 둔다.
            return True

        last_reason = ""
        for attempt in range(1, self._attempts + 1):
            try:
                notes, cold_start = self._attempt(source)
                # **적재도 그 시도 안이다.** 밖에 두면 적재가 깨졌을 때 예외가 이 자리를 그냥 지나쳐
                # 「실패」로 옮기지 못하고, 회의가 「정리 중」에 갇힌 채 화면이 영원히 폴링한다.
                self._gateway.commit_success(meeting_id, notes, cold_start=cold_start)
            except (FinalizeFailed, SchemaViolation) as error:
                # 사유는 화면용 한 줄이고, 무엇이 깨졌는지는 **로그에만** 남는다.
                last_reason = _reason_of(error)
                logger.warning("회의 %s 합성 시도 %s/%s 실패: %s", meeting_id, attempt, self._attempts, error)
                continue
            except Exception as error:  # noqa: BLE001 — provider·적재 실패는 그 시도의 실패다. 회의를 잃지 않는다
                last_reason = _reason_of(error)
                logger.exception("회의 %s 합성 시도 %s/%s 실패", meeting_id, attempt, self._attempts)
                continue
            return True

        self._gateway.commit_failure(meeting_id, last_reason or FAILURE_UNFINISHED)
        return False

    def _retranscribe(self, meeting_id: str) -> None:
        """① 저장된 음원 전체를 다시 전사해 원문을 갈아 끼운다 (사용자 결정 D44, 2026-09-11).

        실시간 전사는 말이 끝나기 전에 답해야 해서 화자 분리가 부정확하다. 회의가 끝나면 서두를 이유가
        없고 음원 전체가 손에 있으니, 처음부터 끝까지 한 번에 들어 원문을 다시 쓴다.

        **되지 않으면 `RetranscribeFailed` 다** — 폴백이 없다 (D44 정정). 실시간 원문 위에서 합성하면
        사람은 재전사되지 않은 회의록을 재전사된 것으로 알고 읽는다. 실물에서 그 일이 실제로 있었다.
        여섯 갈래(녹음 없음 · 올리지 못함 · 그쪽 거절 · 결과 못 받음 · 빈 결과 · 상한 초과)가 모두 실패다.
        """
        if self._transcriber is None:
            raise RetranscribeFailed(FAILED_NO_RECORDING)
        recording = self._gateway.recording_for(meeting_id)
        if recording is None:
            logger.warning("회의 %s 녹음 원본이 없습니다 — 다시 들을 것이 없어 회의록을 만들지 않습니다", meeting_id)
            raise RetranscribeFailed(FAILED_NO_RECORDING)
        try:
            tokens = self._transcriber.transcribe(recording)
        except TimeoutError as error:
            logger.warning("회의 %s 재전사가 상한 안에 끝나지 않았습니다", meeting_id)
            raise RetranscribeFailed(FAILED_TIMEOUT) from error
        except Exception as error:  # noqa: BLE001 — 무엇이 깨졌는지는 로그가 알고 화면은 정해진 한 줄을 읽는다
            logger.exception("회의 %s 재전사에 실패했습니다", meeting_id)
            raise RetranscribeFailed(_retranscribe_reason(error)) from error

        blocks = build_blocks(tokens, base_ms=recording.base_ms)
        if not blocks:
            logger.warning("회의 %s 재전사가 빈 결과를 냈습니다", meeting_id)
            raise RetranscribeFailed(FAILED_EMPTY)
        written = self._gateway.replace_transcript(meeting_id, blocks)
        self._gateway.set_transcript_source(meeting_id, SOURCE_FINAL)
        self.retranscribes += 1
        logger.info("회의 %s 재전사 완료 — 블록 %d개로 원문을 갈아 끼웠습니다", meeting_id, written)

    def _attempt(self, source: dict[str, Any]) -> tuple[FinalNotes, bool]:
        """읽기는 끝났다 — 여기부터는 트랜잭션 밖이다. 적재는 부르는 쪽이 한다."""
        session_ref = source.get("session_ref")
        cold_start = not session_ref
        if cold_start:
            # 세션이 없다 — 회의를 기억하는 상대가 없으므로 확정 발화 전량을 한 번에 싣는다.
            self.cold_starts += 1
            logger.info("회의 %s 합성이 콜드 스타트로 돕니다 — 세션이 없습니다", source["meeting_id"])
        prompt = build_final_prompt(
            meeting=source["meeting"],
            memo_agendas=source["memo_agendas"],
            ai_agendas=source["ai_agendas"],
            memo_lines=source["memo_lines"],
            ai_lines=source["ai_lines"],
            transcript=source["transcript"] if cold_start else None,
        )
        body = self._agent.run_final(
            persona_id=source["persona_id"], session_ref=session_ref, prompt=prompt
        )
        notes = parse_final_output(body)

        # 남은 검증 — 스키마(위 `parse_final_output`) · 근거 범위 · 이미 있는 업무 제외 · **계보 존재**다
        # (§8-6). 계보 검증은 「id 가 그 회의의 원본을 가리키는가」까지이고 없는 id 는 그 id 만 버리므로
        # 원장을 아는 적재(`commit_success`)가 한다 — 여기서 할 수 있는 것은 나머지 셋이다.
        # **「사람 벌 안건을 전수 보존했는가」는 검사하지 않는다**: 벌이 갈려 그 물음이 성립은 하지만
        # 강제할지 말지가 미결이다 (§13.2 `OQ-318`).
        outcome = finalize_notes(
            notes,
            FinalizationContext(
                covered_ms=source["covered_ms"],
                meeting_title=source["meeting"].get("title"),
                existing_task_titles=frozenset(
                    self._gateway.existing_task_titles(source["persona_id"])
                ),
                next_meeting_starts_on=source.get("next_meeting_starts_on"),
            ),
        )
        return outcome.notes, cold_start
