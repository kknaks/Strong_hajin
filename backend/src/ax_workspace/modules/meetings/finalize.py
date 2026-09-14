"""종료 합성의 **말** — 최종 출력 스키마 · 파서 · 검증 · 프롬프트 · 중복 판정.

SCAX-SPEC-004 §8. 회의 중 배치(`batch.py`)와 같은 뼈대이고 최종에서만 차는 셋을 더한다:
`title_candidate` · `concluded` · `todos`. 두 벌이 되지 않도록 **강등과 근거 검사는 `batch` 것을 그대로 쓴다**.

받은 것을 믿지 않는다 — 스키마를 통과해도 코드가 다시 본다:
**사람이 만든 안건이 하나도 빠지거나 합쳐지지 않았는가**(§8-6)가 그 중 가장 무거운 하나다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
import re
from typing import Any

import jsonschema

from ax_workspace.modules.meetings.batch import BatchLine, SchemaViolation, demote_line


SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "ai_final_output.json"
FINAL_OUTPUT_SCHEMA: dict[str, Any] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
_validator = jsonschema.Draft202012Validator(FINAL_OUTPUT_SCHEMA)

#: 합성 시도 상한. 넘으면 「실패」이고 사람이 [다시 시도]로 다시 건다 (WP Open Issue — 잠정값).
FINAL_ATTEMPTS = 3
#: 후보 설명의 마지막 줄이 지는 모양 (SPEC §8.2 `description`).
SOURCE_LINE = "회의 {title} · 안건 {order} 에서"
TITLELESS = "제목 없는 회의"


class FinalizeFailed(Exception):
    """한 시도의 실패 — 그 시도만 실패다. 상한까지 다시 걸고, 그래도 안 되면 「실패」다.

    지금 이 예외를 던지는 자리는 없다. 사람 안건 전수 보존 검사가 유일한 던지는 자리였는데,
    **종료 합성이 회의록을 처음부터 새로 쓰는 일이 되면서 그 검사가 사라졌다**(사용자 결정 2026-09-11).
    벌이 갈려 그 물음이 이제 **성립은 하지만** 강제할지 말지는 미결이다 (SPEC §13.2 `OQ-318`).
    이름은 남겨 둔다 — 「이 시도만 실패」라는 재시도 계약을 부르는 자리가 서비스에 있고, 앞으로 생길
    검증도 같은 뜻으로 이것을 던지면 된다.
    """


@dataclass(slots=True)
class FinalLine(BatchLine):
    """최종 벌의 줄 하나 — 배치 줄에 **계보 하나**를 더한다 (SPEC §4.2-10 · D54).

    `from_lines` 는 **선택이다**: 최종 벌은 새로 쓰는 것이라 원본 한 줄에 대응하지 않는 줄이 정상적으로
    생긴다. 서버는 존재만 검증하고 「정말 그 줄에서 나왔는가」는 검증하지 않는다 — 그것은 AI 의
    자기보고이고 확인할 방법이 없다. 검증할 수 있는 근거는 `evidence` 다.
    """

    from_lines: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FinalTodo:
    title: str
    description: str
    due_candidate: date | None
    checklist_candidate: list[str]
    line_ids: list[str]


@dataclass(slots=True)
class FinalAgenda:
    """최종 벌의 안건 하나 — **이어 쓰는 id 가 아니라 계보를 든다** (SPEC v0.5 §8-5 · §4.1-3 · D54).

    최종 벌은 자기 안건을 갖고 합성이 전량 새로 쓴다. `merged_from` 이 「이 최종 안건이 어느 원본
    안건들에서 나왔나」를 말하고, 그것이 「내가 적은 안건이 어디로 갔나」에 답하는 유일한 길이다.
    출처(`source`)는 사람 벌만 갖는다 (§4.1-2).
    """

    title: str
    merged_from: list[str]
    concluded: bool
    lines: list[FinalLine] = field(default_factory=list)
    todos: list[FinalTodo] = field(default_factory=list)


@dataclass(slots=True)
class FinalNotes:
    title_candidate: str | None
    agendas: list[FinalAgenda]


@dataclass(frozen=True, slots=True)
class FinalizationContext:
    covered_ms: tuple[int, int]
    meeting_title: str | None
    existing_task_titles: frozenset[str]
    next_meeting_starts_on: date | None


@dataclass(frozen=True, slots=True)
class FinalizationOutcome:
    """The normalized notes payload the application should commit atomically."""

    notes: FinalNotes


def parse_final_output(body: str) -> FinalNotes:
    """스키마 1단 — JSON · 구조 · 타입 · enum · 길이. 부분 통과가 없다."""
    try:
        data = json.loads(body)
    except json.JSONDecodeError as error:
        raise SchemaViolation(f"출력이 JSON 이 아닙니다: {error.msg}") from error
    problem = jsonschema.exceptions.best_match(_validator.iter_errors(data))
    if problem is not None:
        raise SchemaViolation(f"스키마 위반: {problem.message}")

    agendas: list[FinalAgenda] = []
    for agenda in data["agendas"]:
        agendas.append(
            FinalAgenda(
                title=agenda["title"],
                # 계보는 **존재만** 검증한다 — 그 검증은 원장을 아는 적재가 한다 (§8-6). 여기는 모양만 본다.
                merged_from=list(agenda["merged_from"]),
                concluded=bool(agenda["concluded"]),
                lines=[
                    FinalLine(
                        text=line["text"],
                        evidence=[
                            {"from_ms": int(span["from_ms"]), "to_ms": int(span["to_ms"])}
                            for span in line["evidence"]
                        ],
                        task_id=None,
                        from_lines=list(line["from_lines"]),
                    )
                    for line in agenda["lines"]
                ],
                todos=[
                    FinalTodo(
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
    return FinalNotes(title_candidate=data["title_candidate"], agendas=agendas)


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise SchemaViolation(f"due_candidate 가 날짜가 아닙니다: {value}") from error


def _bind_evidence(notes: FinalNotes, covered_ms: tuple[int, int]) -> None:
    """근거는 실재하는 확정 발화 구간만 — 밖을 가리키는 구간은 떨어진다 (§8-5 근거 결박).

    **그래서 근거가 하나도 남지 않은 최종 줄은 거절한다** (사용자 결정 「최종 회의록만 회의록이다」
    §바뀌는 것 3). 회의 중 배치의 줄은 근거를 잃어도 본문이 살지만(§7.1 검증 — 그것은 임시 재료다)
    **최종 회의록은 다르다**: 근거가 없으면 사람이 「이 문장은 어디서 왔나」를 되짚을 수 없고, 그것은
    재전사문이 사실의 SoT 라는 결정 자체를 비운다.

    줄을 조용히 버리지 않고 **그 시도를 실패시킨다** — 버리면 사람이 적었고 AI 가 옮긴 내용이 아무 말
    없이 사라진다. 실패는 세 번까지 다시 걸리고(§8-8) 그 재시도가 근거를 붙일 기회다.
    """
    for agenda in notes.agendas:
        agenda.lines = [
            demote_line(line, allowed_task_ids=set(), covered_ms=covered_ms) for line in agenda.lines
        ]
        for line in agenda.lines:
            if not line.evidence:
                raise SchemaViolation(
                    "최종 회의록의 줄은 근거 구간을 하나는 들어야 합니다"
                    f" — 「{line.text[:30]}」 이(가) 딛는 구간이 없습니다"
                )


def _stamp_source_lines(notes: FinalNotes, *, meeting_title: str | None) -> None:
    """후보 설명의 마지막 줄에 출처를 붙인다 — 받는 사람이 회의에 없었어도 이 글만 읽고 시작할 수 있게 (§8.2).

    AI 가 이미 붙였으면 그대로 둔다: 두 번 붙이지 않는다.
    """
    title = meeting_title or TITLELESS
    for order, agenda in enumerate(notes.agendas, start=1):
        stamp = SOURCE_LINE.format(title=title, order=order)
        for todo in agenda.todos:
            if stamp in todo.description:
                continue
            todo.description = f"{todo.description.rstrip()}\n{stamp}"


def resolve_due(todo: FinalTodo, *, next_meeting_starts_on: date | None) -> date | None:
    """기한 세 갈래 (§8.2 `due_candidate`) — ① 말의 날짜 ② 이어진 다음 회의 전날 ③ 없음.

    ①은 AI 가 채워 온다. 여기서 더하는 것은 ②뿐이고, 근거 없이 지어내지 않는 것이 ③이다.
    """
    if todo.due_candidate is not None:
        return todo.due_candidate
    if next_meeting_starts_on is not None:
        return next_meeting_starts_on - timedelta(days=1)
    return None


_NORMALIZE = re.compile(r"[\s\W_]+", re.UNICODE)


def normalize_title(value: str) -> str:
    return _NORMALIZE.sub("", (value or "").lower())


def is_already_work(todo_title: str, existing_titles: set[str]) -> bool:
    """**이미 있는 업무면 후보를 내지 않는다** (SPEC §8.1-1).

    「같은 일」의 기준은 미결이라(§13 `OQ-316`) 잠정으로 **제목 정규화 일치 또는 한쪽이 다른 쪽을 품음**을 쓴다.
    판단이 서지 않으면 **후보로 낸다** — 놓친 일을 사람이 지우는 편이, 뽑히지 않은 일을 알아채는 것보다 쉽다.
    """
    candidate = normalize_title(todo_title)
    if not candidate:
        return False
    for existing in existing_titles:
        if not existing:
            continue
        if candidate == existing or candidate in existing or existing in candidate:
            return True
    return False


def _prepare_final_notes_in_place(
    notes: FinalNotes,
    *,
    covered_ms: tuple[int, int],
    meeting_title: str | None,
    existing_task_titles: set[str],
    next_meeting_starts_on: date | None,
) -> FinalNotes:
    """Apply the Meeting-owned rules to parsed provider output before persistence.

    The service supplies facts from repositories; this function alone decides which evidence and
    follow-ups survive, how due dates are inferred, and whether an AI title may be retained.
    """
    _bind_evidence(notes, covered_ms)
    _stamp_source_lines(notes, meeting_title=meeting_title or notes.title_candidate)
    if meeting_title:
        notes.title_candidate = None

    seen_titles = set(existing_task_titles)
    for agenda in notes.agendas:
        kept: list[FinalTodo] = []
        for todo in agenda.todos:
            if is_already_work(todo.title, seen_titles):
                continue
            todo.due_candidate = resolve_due(
                todo,
                next_meeting_starts_on=next_meeting_starts_on,
            )
            kept.append(todo)
            seen_titles.add(normalize_title(todo.title))
        agenda.todos = kept
    return notes


def finalize_notes(notes: FinalNotes, context: FinalizationContext) -> FinalizationOutcome:
    prepared = _prepare_final_notes_in_place(
        deepcopy(notes),
        covered_ms=context.covered_ms,
        meeting_title=context.meeting_title,
        existing_task_titles=set(context.existing_task_titles),
        next_meeting_starts_on=context.next_meeting_starts_on,
    )
    return FinalizationOutcome(notes=prepared)


# --- 프롬프트 -----------------------------------------------------------------


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


_FINAL_INSTRUCTIONS = """회의가 끝났다. **재료를 보고 최종 회의록 한 벌을 처음부터 새로 써라.**

회의록은 세 벌이다 — **사람 벌** · **네 벌(AI)** · 이제 네가 지을 **최종 벌**. 재료는 원본 두 벌 전체
(안건과 줄)와 확정 발화다. 셋을 나란히 두고 **안건 목록부터 새로 잡는다**: 두 벌의 안건은 재료이고
**묶어도 되고 나눠도 되고 새로 세워도 된다.** 결과는 「누가 썼나」가 남지 않는 한 벌이다.

**원본 두 벌은 네가 건드리는 것이 아니다.** 그 둘은 최종본을 사람이 대조할 근거로 그대로 남는다 —
네가 하는 일은 그것을 지우거나 고치는 것이 아니라 **그 위에 최종 벌 하나를 새로 짓는 것**이다.
어디서 무엇이 왔는지는 **계보**로 남긴다.

## 쓰는 규칙

- **이어 쓰는 안건이 없다.** 최종 회의록은 자기 안건 목록을 갖고 너는 그것을 전부 새로 낸다 —
  원본 두 벌의 안건은 재료이고, 이 출력이 그것을 고치거나 지우지 않는다. 원본은 그대로 남는다.
- 안건마다 **`merged_from` 에 그 안건이 나온 원본 안건 id 를 적는다.** 둘을 묶었으면 둘 다 적고,
  하나를 둘로 나눴으면 두 최종 안건이 같은 id 를 든다. 전사에만 있던 이야기면 **빈 배열**이다.
  이것이 사람에게 「내가 적은 안건이 어디로 갔나」를 말하는 유일한 값이다 — 성실히 적어라.
- 같은 말이 여러 재료에 있으면 **한 줄로 접는다.** 접힌 줄의 `from_lines` 에 원래 줄 id 를 다 적는다.
  딛는 원본 줄이 없는 줄이면 빈 배열이다 — 없는 id 를 지어내지 마라.
- 줄 하나가 짧은 문장 하나다. 문단을 쓰지 마라.
- **줄마다 근거 구간(`evidence`)을 반드시 단다.** 근거 없는 줄은 회의록에 서지 못한다 — 사람이
  「이 문장은 어디서 왔나」를 되짚을 수 없기 때문이다. **하나도 없으면 그 답 전체가 거절된다.**
  - 재료로 받은 AI 벌 줄에는 이미 `evidence` 가 붙어 있다 — **그것을 그대로 이어 적어라.**
    네가 회의 중에 그 줄을 쓸 때 딛었던 구간이고, 다시 쓴다고 근거가 바뀌는 것이 아니다.
  - 사람 벌 줄에는 `at_ms`(적은 시각)가 있다 — 그 무렵의 발화 구간이 그 줄의 근거다.
  - **없는 구간을 지어내지 마라.** 지어낸 구간은 서버가 떨어뜨리고, 그러면 근거가 없어 거절된다.
- `concluded` 는 그 안건에서 결론이 났는가다. 확실하지 않으면 false 다.

## 다음 할 일

- 줄에서 할 일을 뽑는다. **담당자는 뽑지 마라** — 조직 데이터에 역할 설명이 없어 고르면 근거 없는 추측이 된다.
- **뽑기 전에 `task_list` 로 이미 있는 업무를 조회해라.** 이미 있는 일이면 후보를 내지 않는다.
  판단이 서지 않으면 후보로 낸다.
- `description` 은 **언제나** 채운다 — 무엇을 왜 해야 하는지 2~4문장 + 근거 줄 요약.
  받는 사람이 회의에 없었어도 이 글만 읽고 일을 시작할 수 있어야 한다.
- `checklist_candidate` 는 **언제나** 2~5단계로 제안한다. 말에 단계가 없어도 네가 쪼갠다.
- `due_candidate` 는 **말에 날짜가 있을 때만** 채운다. 「이번 주 금요일」·「다음 주 목요일」 같은 상대 표현도
  **아래 기준일로 환산해 YYYY-MM-DD 로** 적어라 — 환산이 서지 않으면 null 로 둔다.
  말에 아무 날짜도 없으면 null 이다. 서버가 이어진 다음 회의를 보고 채운다.
- `line_ids` 에 그 후보가 딛는 줄들을 적는다.

## 제목

회의 제목이 비어 있으면 `title_candidate` 에 한 줄 후보를 낸다. 제목이 이미 있으면 null 이다.

## 답하는 모양

**아래 스키마를 따르는 JSON 하나로만 답하라.** 설명도 인사도 코드블록 표시도 붙이지 마라 — JSON 그것뿐이다.

{schema}
"""


#: 요일 이름 — 상대 날짜를 환산하려면 기준일이 무슨 요일인지가 있어야 한다.
_WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")


def describe_day(value: date | None) -> str | None:
    """`2026-09-10 (목)` — 모델이 「이번 주 금요일」을 셀 수 있는 모양."""
    if value is None:
        return None
    return f"{value.isoformat()} ({_WEEKDAYS[value.weekday()]})"


def build_final_prompt(
    *,
    meeting: dict[str, Any],
    memo_agendas: list[dict[str, Any]],
    ai_agendas: list[dict[str, Any]],
    memo_lines: list[dict[str, Any]],
    ai_lines: list[dict[str, Any]],
    transcript: list[dict[str, Any]] | None = None,
) -> str:
    """합성 입력 — **원본 두 벌 전체**(안건과 줄)다 (SPEC §8-3).

    0.4.x 는 사람 쪽 입력이 줄뿐이었다. 이제 사람이 세운 **안건 목록도 함께 실린다** — 사람이 이야기를
    어떻게 갈랐는지가 그 자체로 재료이고, 계보가 그 id 를 딛는다. 세션이 발화를 기억하므로 원문은
    폴백에서만 싣는다.
    """
    base_day = meeting.get("starts_on")
    next_day = meeting.get("next_meeting_on")
    when = [f"\n**기준일: {base_day}**" if base_day else ""]
    if next_day:
        when.append(f" · 이어진 다음 회의: {next_day}")
    sections = [
        _FINAL_INSTRUCTIONS.format(schema=_dumps(FINAL_OUTPUT_SCHEMA)),
        "".join(when) + " — 상대 날짜 표현은 이 날을 기준으로 환산한다.\n" if base_day else "",
        f"\n회의:\n{_dumps(meeting)}",
        f"\n사람 벌의 안건(재료다 — 묶어도 나눠도 되고, 계보에 그 id 를 적는다):\n{_dumps(memo_agendas)}",
        f"\nAI 벌의 안건(네가 회의 중에 세운 것이다 — 같은 재료다):\n{_dumps(ai_agendas)}",
        f"\n사람 벌의 줄:\n{_dumps(memo_lines)}",
        f"\nAI 벌의 줄(네가 회의 중에 낸 것이다):\n{_dumps(ai_lines)}",
    ]
    if transcript is not None:
        # 콜드 스타트 — 세션이 없어 회의를 기억하지 못한다. 확정 발화 전량을 한 번에 싣는다.
        sections.append(f"\n확정 발화 전량:\n{_dumps(transcript)}")
    return "".join(sections)
