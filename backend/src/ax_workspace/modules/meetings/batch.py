"""회의 중 AI 배치의 **말** — 출력 스키마 · 파서 · 강등 · 트리거 수치 · 프롬프트.

SCAX-SPEC-004 §7. 여기는 provider 도 ORM 도 모른다: 무엇을 받아 무엇을 믿을지만 정한다.

- **스키마는 파일 하나다** — provider 에 그대로 걸고 서버가 같은 파일로 다시 검증한다. 강제와 검증은 다른 층이고,
  받은 것을 믿지 않는 것이 계약이다.
- **부분 파싱이 없다** — 어느 항목 하나라도 어긋나면 배치 전체가 폐기다. 직전 성공분이 그대로 남는다 (§7.1 검증).
- 강등은 **줄 단위**다 — 없는 업무를 가리키거나 근거가 구간 밖이면 그 줄의 참조만 떼고 본문은 살린다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import json
from pathlib import Path
from typing import Any

import jsonschema


SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "ai_batch_output.json"
OUTPUT_SCHEMA: dict[str, Any] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
_validator = jsonschema.Draft202012Validator(OUTPUT_SCHEMA)


# --- 트리거 (SPEC §13 `OQ-307` 잠정값 — 실측으로 갈아 끼운다) ------------------------
#: 미처리 확정 발화가 이만큼 쌓이면 낸다.
BATCH_CHARS = 600
#: 안건이 바뀌면 즉시 낸다 — 다만 미처리가 이보다 적으면 생략한다(빈 배치를 내지 않는다).
BATCH_SWITCH_MIN_CHARS = 80
#: 미처리 구간이 생긴 뒤 이 시간이 지나면 분량과 무관하게 낸다.
BATCH_MAX_WAIT_SECONDS = 90

CAUSE_TRANSCRIPT = "transcript"
CAUSE_AGENDA_SWITCH = "agenda_switch"
CAUSE_TIMER = "timer"

STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_DISCARDED = "discarded"

#: 배치가 여는 도구의 바닥 넷 (SPEC §7.2-2). 레지스트리가 이 위에 더 얹을 수 있다 — 바닥이지 천장이 아니다.
DEFAULT_TOOL_REGISTRY: tuple[str, ...] = ("task_list", "project_list", "meeting_get", "member_list")


@dataclass(frozen=True, slots=True)
class BatchTriggerContext:
    cause: str
    pending_chars: int
    chars: int = BATCH_CHARS
    switch_min_chars: int = BATCH_SWITCH_MIN_CHARS


@dataclass(frozen=True, slots=True)
class BatchTriggerDecision:
    fire: bool
    arm_timer: bool


def decide_batch_trigger(context: BatchTriggerContext) -> BatchTriggerDecision:
    """Decide whether pending speech fires a batch or arms its timer, without I/O."""
    if context.cause == CAUSE_TRANSCRIPT:
        fire = context.pending_chars >= context.chars
    elif context.cause == CAUSE_AGENDA_SWITCH:
        fire = context.pending_chars >= context.switch_min_chars
    elif context.cause == CAUSE_TIMER:
        fire = context.pending_chars > 0
    else:
        raise ValueError(f"알 수 없는 트리거: {context.cause}")
    return BatchTriggerDecision(fire=fire, arm_timer=context.pending_chars > 0 and not fire)


class SchemaViolation(Exception):
    """검증 1단 실패 — 배치 전체 폐기. 직전 성공분은 그대로 남는다."""


@dataclass(slots=True)
class BatchLine:
    text: str
    evidence: list[dict[str, int]]
    task_id: str | None


@dataclass(slots=True)
class BatchTodo:
    """회의 중에 나온 후속 업무 후보 하나 (D46). **읽기 전용**이라 담당자도 승격도 없다."""

    title: str
    description: str
    due_candidate: date | None
    checklist_candidate: list[str] = field(default_factory=list)
    line_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BatchAgenda:
    agenda_id: str | None
    title: str
    source: str
    lines: list[BatchLine] = field(default_factory=list)
    todos: list[BatchTodo] = field(default_factory=list)


def parse_output(body: str) -> list[BatchAgenda]:
    """검증 1단 — JSON · 구조 · 타입 · enum · 길이. 어긋나면 `SchemaViolation` 하나이고 부분 통과가 없다."""
    try:
        data = json.loads(body)
    except json.JSONDecodeError as error:
        raise SchemaViolation(f"출력이 JSON 이 아닙니다: {error.msg}") from error
    problem = jsonschema.exceptions.best_match(_validator.iter_errors(data))
    if problem is not None:
        raise SchemaViolation(f"스키마 위반: {problem.message}")

    agendas: list[BatchAgenda] = []
    for agenda in data["agendas"]:
        lines = [
            BatchLine(
                text=line["text"],
                evidence=[{"from_ms": int(span["from_ms"]), "to_ms": int(span["to_ms"])} for span in line["evidence"]],
                task_id=line["task_id"],
            )
            for line in agenda["lines"]
        ]
        agendas.append(
            BatchAgenda(
                agenda_id=agenda["agenda_id"],
                title=agenda["title"],
                source=agenda["source"],
                lines=lines,
                todos=[
                    BatchTodo(
                        title=todo["title"],
                        description=todo["description"],
                        due_candidate=_parse_date(todo["due_candidate"]),
                        checklist_candidate=list(todo["checklist_candidate"]),
                        line_ids=list(todo["line_ids"]),
                    )
                    for todo in agenda["todos"]
                ],
            )
        )
    return agendas


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise SchemaViolation(f"due_candidate 가 날짜가 아닙니다: {value}") from error


def demote_line(line: BatchLine, *, allowed_task_ids: set[str], covered_ms: tuple[int, int]) -> BatchLine:
    """검증 2단 — **그 줄만** 강등한다. 본문은 살고 못 믿을 참조만 떨어진다 (SPEC §7.1 검증).

    ① 없는 업무를 가리키면 `task_id` 를 뗀다 — 회의를 만든 사람이 보는 범위가 그 집합이다 (`OQ-315`).
    ② 근거가 이번에 읽은 발화 구간 밖이면 그 근거를 뗀다 — 존재하지 않는 구간을 인용할 수 없다 (§10-8).
    """
    if line.task_id is not None and line.task_id not in allowed_task_ids:
        line.task_id = None
    # 구간은 **회의 전체**다 (D5) — 이번 배치의 미처리 구간으로 좁히면 앞 구간을 근거로 단 줄이
    # 매 회차 근거를 떼여, 화면에 최신 구간의 시간 칩만 남는다.
    start, end = covered_ms
    line.evidence = [
        span for span in line.evidence if start <= span["from_ms"] < span["to_ms"] <= end
    ]
    return line


# --- 프롬프트 -----------------------------------------------------------------


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def build_warm_start_prompt(context: dict[str, Any], tools: tuple[str, ...]) -> str:
    """첫 turn — 맥락을 싣는다: 회의 정보 · 안건 · 참석자 · 이어진 이전 회의 (SPEC §7.1 첫 배치).

    응답은 버린다. 이 turn 이 남기는 것은 세션 참조 하나다.
    참석자는 **이름을 싣지 않는다** — 화자는 익명이고 프롬프트에 실명을 흘리지 않는다 (WP Pre-deploy Check).
    """
    listed = "\n".join(f"  {name}" for name in tools)
    return f"""너는 회의에 들어와 있는 또 하나의 참가자다. 사람과 네가 각자 회의록을 쓰고, 회의가 끝나면 합친다.
사람이 모든 것을 다 칠 수 없고 너도 모든 것을 정확히 요약할 수 없다 — 그래서 둘 다 쓴다.

## 회의는 이렇게 흐른다

회의가 도는 동안 사람의 말이 실시간으로 받아쓰기 되어 확정 발화로 너에게 온다. 한 번에 다 오지 않는다 —
일정량이 쌓이면 그 구간만 온다. 사람은 그와 별개로 자기 메모를 안건에 매단다.

**너는 네 트랙에만 쓴다.** 사람의 메모를 고치거나 지우지 않는다 — 사람 트랙은 읽기 전용이다.

## 무엇을 만드나

안건이 뼈대이고 줄이 그 아래 산다. 줄 하나가 짧은 문장 하나다 — 문단을 쓰지 마라.

- 이미 있는 안건에 맞으면 **그 안건의 `agenda_id` 를 그대로 적고** 아래에 줄을 붙인다.
- 정말 어디에도 안 붙을 때만 새 안건을 세운다 — 그때 `agenda_id` 는 null 이고 `source` 는 "ai" 다.
- 화제가 조금 옮겨갔다고 새 안건을 만들지 마라. 같은 주제 안에서 이야기가 흐르는 것은 한 안건이다.

## 어떻게 요약하나

- 발화를 그대로 옮기지 마라. 한 문장으로 압축한다. 말버릇은 버린다.
- **말한 사람 이름을 쓰지 마라.** 화자는 익명이고 너는 이름을 모른다.
- 인사 · 잡담 · 같은 말 반복은 버린다.
- 숫자 · 날짜 · 고유명사는 그대로 옮긴다.
- **말하지 않은 것을 채우지 마라.** 흐름상 그럴 것 같아도 발화에 없으면 쓰지 않는다.
- 줄마다 근거 구간(`evidence`)을 단다 — 그 줄이 어느 발화에서 나왔는지의 [from_ms, to_ms] 다.
- 이미 있는 업무를 가리킬 때만 `task_id` 를 적는다. **없는 업무를 지어내지 마라** — 도구로 조회해 확인해라.

## 쓸 수 있는 도구

{listed}

  필요할 때 조회해라. 미리 다 주지 않는다.
  **도구는 전부 조회다.** 무엇을 바꾸는 도구는 없고, 회의록에 남길 것은 출력으로만 낸다 — 저장하는 것은 서버다.

## 이 회의

{_dumps(context)}

## 앞으로 답하는 모양

회의가 도는 동안 너에게 오는 요청은 **정해진 JSON 스키마 하나로만** 답한다. 스키마는 매 요청에 함께 온다 —
설명도 인사도 코드블록 표시도 붙이지 말고 JSON 그것만 낸다.

이번 요청에는 아무것도 만들지 말고 「준비됨」이라고만 답하라."""


_OUTPUT_CONTRACT = """
## 답하는 모양

**아래 스키마를 따르는 JSON 하나로만 답하라.** 설명도 인사도 코드블록 표시도 붙이지 마라 — JSON 그것뿐이다.

{schema}
"""


_BATCH_INSTRUCTIONS = """회의 중 배치다. 아래는 아직 반영하지 않은 확정 발화와 그 사이 들어온 메모다.

**이번 구간을 반영해 네 트랙 전체를 다시 정리해라.** 앞 배치에서 낸 안건과 줄도 포함해 처음부터 다시 낸다 —
이 출력이 「AI 요약」 탭 전체가 된다. 앞 배치가 잘못 가른 안건을 합치거나 잘못 붙인 줄을 옮기는 것도 여기서 한다.

안건마다 **그 안건에서 나온 후속 업무 후보(`todos`)도 함께 낸다 — 담당자는 뽑지 마라.** 아직 후보일 뿐이라
`checklist_candidate` 는 비어도 되고, `due_candidate` 는 말에 날짜가 있을 때만 채운다. 낼 것이 없으면 빈 배열이다.

앞 구간은 다시 싣지 않는다 — 세션이 기억한다.
`evidence` 는 근거가 된 확정 발화의 [from_ms, to_ms] 구간이다(최대 3개). **회의 전체 발화 중 실제 구간**을
적어라 — 앞 구간에서 나온 줄이면 그때의 구간을 그대로 다시 적는다. 이번 구간으로 옮겨 적지 마라.
"""


def build_batch_prompt(blocks: list[dict[str, Any]], memos: list[dict[str, Any]]) -> str:
    """증분만 싣는다 (SPEC §7.1 이후 배치) — 직전 성공 배치 이후의 확정 블록과 새 메모."""
    contract = _OUTPUT_CONTRACT.format(schema=_dumps(OUTPUT_SCHEMA))
    return (
        f"{_BATCH_INSTRUCTIONS}{contract}\n확정 발화:\n{_dumps(blocks)}\n\n"
        f"사람이 남긴 메모(읽기만 한다):\n{_dumps(memos)}"
    )
