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
#: provider 에 거는 스키마 — 용어 보정 표(`term_corrections`)까지 **필수**로 건다(모델이 정정 pass 를 빼먹지 않게).
FINAL_OUTPUT_SCHEMA: dict[str, Any] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _without_term_corrections(schema: dict[str, Any]) -> dict[str, Any]:
    """서버 검증용 — 보정 표 칸만 뺀 같은 스키마. 보정 표는 **항목마다** 따로 본다(틀린 항목만 버림 · SPEC-010 §4.7-6)."""
    trimmed = deepcopy(schema)
    trimmed["properties"].pop("term_corrections", None)
    trimmed["required"] = [name for name in trimmed["required"] if name != "term_corrections"]
    return trimmed


_validator = jsonschema.Draft202012Validator(_without_term_corrections(FINAL_OUTPUT_SCHEMA))
#: 용어 보정 표의 등급 둘 (SPEC-010 §4.7-3 · D-17) — `auto` 는 본문에 바로잡은 말로 · `presumed` 는 표에만.
TERM_GRADES = ("auto", "presumed")
#: 들린 말 · 바로잡은 말의 길이 (OQ-1013 코디 기본값).
TERM_MAX_CHARS = 100

#: 합성 시도 상한. 넘으면 「실패」이고 사람이 [다시 시도]로 다시 건다 (WP Open Issue — 잠정값).
FINAL_ATTEMPTS = 3
#: **한 회차(한 배달)에서 provider 를 부르는 최대 횟수** — lease 바닥이 이 수로 셈한다(WORK-012 WP2 수정 1 F-1).
#: 시도 `FINAL_ATTEMPTS` 번 + 세션 유실 콜드 폴백 1번(첫 resume 이 「세션 없음」 이면 같은 시도 안에서 한 번 더 부르고, 그 뒤
#: 시도는 세션 없이 돈다) + timeout 뒤 새 세션 1번(시도 상한과 무관하게 보장되고, 그 뒤 실패는 무엇이든 끝이다).
#: `MeetingFinalizeService` 의 루프가 이 수를 넘지 않는다 — 시험이 최악 경로를 실제로 돌려 센다.
FINAL_MAX_PROVIDER_CALLS = FINAL_ATTEMPTS + 2
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


class FinalizeSessionLost(Exception):
    """이어 쓰려던 세션이 **이 자리에 없다** — 다른 프로세스(파드)가 열었거나 재시작으로 사라졌다.

    일반 실패와 다르다: 같은 resume 을 다시 걸어도 같다. 합성은 시도를 쓰지 않고 그 자리에서
    **콜드 스타트**(확정 발화 전량을 실어 새 세션)로 넘어간다 (WORK-008 Phase 4 · B-03).
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


@dataclass(frozen=True, slots=True)
class TermCorrection:
    """용어 보정 표 한 행 (SPEC-010 §4.7-4) — 들린 말 · 바로잡은 말 · 등급."""

    heard: str
    corrected: str
    grade: str


@dataclass(slots=True)
class FinalNotes:
    title_candidate: str | None
    agendas: list[FinalAgenda]
    #: 정정 pass 의 결과 — 원문에 처음 나온 순. 틀린 항목은 파서가 이미 버렸다. 빈 목록 = 바로잡을 것이 없었다.
    #: `None` = 모델이 보정 표 칸을 내지 않았다(정정 pass 를 건너뜀) → 「정정이 돌았다」 를 찍지 않는다.
    term_corrections: list[TermCorrection] | None = None


@dataclass(frozen=True, slots=True)
class FinalizationContext:
    covered_ms: tuple[int, int]
    meeting_title: str | None
    existing_task_titles: frozenset[str]
    next_meeting_starts_on: date | None
    #: 회의일 — 이 회의 시작 시각의 **KST 날짜**. 기한의 하한이다 (SPEC-010 §4.8). 모르면 하한을 걸지 않는다.
    meeting_starts_on: date | None = None


@dataclass(frozen=True, slots=True)
class FinalizationOutcome:
    """The normalized notes payload the application should commit atomically."""

    notes: FinalNotes


def parse_term_corrections(raw: object) -> list[TermCorrection] | None:
    """용어 보정 표 — **항목마다** 본다 (SPEC-010 §4.7-6 · OQ-1013).

    `heard`·`corrected` 는 1~100자 문자열 · `grade` 는 `auto`|`presumed` · 같은 `heard` 는 첫 것만. 어긋난 항목은 **그 항목만**
    버린다 — 보정 표가 틀려도 시도를 실패시키지 않는다(근거와 결이 다르다: 본문은 이미 섰다).
    **칸이 없거나 배열이 아니면 `None`** — 정정 pass 를 건너뛴 응답이다. 빈 표(`[]` = 「돌았는데 바로잡을 것이 없었다」)로
    보지 않는다(WORK-012 WP2 수정 1 W-2 · H-4): 적재가 `term_corrected_at` 을 찍지 않아 응답이 `null` 이 된다.
    """
    if not isinstance(raw, list):
        return None
    rows: list[TermCorrection] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        heard, corrected, grade = item.get("heard"), item.get("corrected"), item.get("grade")
        if not isinstance(heard, str) or not isinstance(corrected, str) or grade not in TERM_GRADES:
            continue
        heard, corrected = heard.strip(), corrected.strip()
        if not (1 <= len(heard) <= TERM_MAX_CHARS and 1 <= len(corrected) <= TERM_MAX_CHARS):
            continue
        if heard in seen:
            continue
        seen.add(heard)
        rows.append(TermCorrection(heard=heard, corrected=corrected, grade=str(grade)))
    return rows


def parse_final_output(body: str) -> FinalNotes:
    """스키마 1단 — JSON · 구조 · 타입 · enum · 길이. 부분 통과가 없다. **용어 보정 표만 항목 단위로 따로 본다.**"""
    try:
        data = json.loads(body)
    except json.JSONDecodeError as error:
        raise SchemaViolation(f"출력이 JSON 이 아닙니다: {error.msg}") from error
    corrections = parse_term_corrections(data.pop("term_corrections", None)) if isinstance(data, dict) else None
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
    return FinalNotes(title_candidate=data["title_candidate"], agendas=agendas, term_corrections=corrections)


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


def order_agendas_by_evidence(notes: FinalNotes) -> None:
    """최종 안건을 **그 안건 줄들의 가장 이른 근거 시작 시각** 순으로 다시 매긴다 (SPEC-010 §4.7-7 · D-15).

    모델이 낸 순서에 기대지 않는다 — 회의 흐름 순서로 선다. 줄(근거)이 없는 안건은 끝에, 같은 시각이면 모델 순서(안정 정렬).
    """

    def earliest(agenda: FinalAgenda) -> float:
        starts = [int(span["from_ms"]) for line in agenda.lines for span in line.evidence]
        return min(starts) if starts else float("inf")

    notes.agendas = sorted(notes.agendas, key=earliest)


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


def floor_due(due: date | None, *, meeting_starts_on: date | None) -> date | None:
    """**회의일보다 이른 기한은 비운다** (SPEC-010 §4.8 · DEC-009 D-19). 같은 날은 허용한다.

    회의 중 잠정 후보(배치)와 최종 결과가 같은 하한을 탄다(OQ-1014). 비운 이유는 남기지 않는다(H-10).
    """
    if due is None or meeting_starts_on is None:
        return due
    return None if due < meeting_starts_on else due


def resolve_due(
    todo: FinalTodo, *, next_meeting_starts_on: date | None, meeting_starts_on: date | None = None
) -> date | None:
    """기한 세 갈래 (SPEC-010 §4.8) — ① 말의 날짜 ② 다음 회의 전날 ③ 없음 — 그리고 **회의일 하한**.

    ①은 AI 가 채워 온다. 여기서 더하는 것은 ②뿐이고, 근거 없이 지어내지 않는 것이 ③이다. 「다음 회의」 는 **이 회의에서
    이월된 회의**만이고(저장소 `next_meeting_after`) 날짜는 모두 KST 다(부르는 쪽이 뽑는다). ①이든 ②든 회의일보다 이르면
    비운다 — 모델이 옛 규칙으로 전날을 셈해 ①로 내는 경로(BE §5.5 (d))도 여기서 막힌다.
    """
    if todo.due_candidate is not None:
        due: date | None = todo.due_candidate
    elif next_meeting_starts_on is not None:
        due = next_meeting_starts_on - timedelta(days=1)
    else:
        due = None
    return floor_due(due, meeting_starts_on=meeting_starts_on)


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
    meeting_starts_on: date | None = None,
) -> FinalNotes:
    """Apply the Meeting-owned rules to parsed provider output before persistence.

    The service supplies facts from repositories; this function alone decides which evidence and
    follow-ups survive, how due dates are inferred, and whether an AI title may be retained.
    """
    _bind_evidence(notes, covered_ms)
    # 순서를 먼저 매긴다 — 후보 설명의 「안건 N 에서」 가 화면의 안건 순서와 같아야 한다.
    order_agendas_by_evidence(notes)
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
                meeting_starts_on=meeting_starts_on,
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
        meeting_starts_on=context.meeting_starts_on,
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

## 두 단계로 한다 — ① 정정 → ② 다시 쓰기

**① 정정 pass.** 먼저 확정 발화 전량을 읽으며 **STT 오인식으로 보이는 표기**를 고른다 — 아래 「조직 맥락 목록」의
프로젝트·업무·구성원 이름에 비춰 「이 말이 사실은 저것」 인 쌍이다. 쌍마다 등급을 단다:

- `auto` — **확실한 기술 용어·고유명사**(제품·프로젝트·시스템명). ② 에서 회의록 본문에 **바로잡은 말**로 쓴다.
- `presumed` — **인명·숫자·금액·일정**. 확신이 서도 본문은 **들린 말 그대로** 두고 표에만 남긴다 — 확인은 사람이 한다.
- **화자 라벨은 어떤 경우에도 바꾸지 않는다.** 원문(확정 발화) 자체를 고쳐 쓰지 않는다 — 고치는 것은 네가 짓는 최종 벌뿐이다.

고른 쌍을 `term_corrections` 에 `{{heard, corrected, grade}}` 로, **원문에 처음 나온 순서**대로 낸다. 같은 들린 말은 한 번만.
목록에 근거가 없는 짐작은 내지 마라 — 바로잡을 것이 없으면 빈 배열이다.

**② 다시 쓰기.** ① 의 결과를 반영해 최종 벌을 **처음부터** 짓는다(아래 규칙).

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
  말에 아무 날짜도 없으면 null 이다 — **다음 회의 전날을 네가 셈해 넣지 마라.** 서버가 이 회의에서 이월된 다음 회의를
  보고 채운다. **기준일(회의일)보다 이른 날짜는 서버가 비운다.**
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
    transcript: list[dict[str, Any]],
    catalog: str | None = None,
) -> str:
    """합성 입력 — **원본 두 벌 전체**(안건과 줄) · **재전사 전체 발화(매번)** · **AI 맥락 목록** (SPEC §8-3 · SPEC-010 §4.7).

    0.4.x 는 사람 쪽 입력이 줄뿐이었다. 이제 사람이 세운 **안건 목록도 함께 실린다** — 사람이 이야기를
    어떻게 갈랐는지가 그 자체로 재료이고, 계보가 그 id 를 딛는다. 0.6.x 는 세션이 발화를 기억한다고 보고 원문을
    콜드 스타트에서만 실었다 — 세션 기억은 실시간 원문이라 재전사본과 다르고, 회의 앞머리가 회의록에서 빠졌다(SH-IMP-006).
    이제 **세션을 이어 쓰든 새로 열든** 재전사 전체 발화를 싣는다(OQ-904). 맥락 목록은 정정 pass 의 근거다.
    """
    base_day = meeting.get("starts_on")
    next_day = meeting.get("next_meeting_on")
    when = [f"\n**기준일: {base_day}**" if base_day else ""]
    if next_day:
        when.append(f" · 이월된 다음 회의: {next_day}")
    sections = [
        _FINAL_INSTRUCTIONS.format(schema=_dumps(FINAL_OUTPUT_SCHEMA)),
        "".join(when) + " — 상대 날짜 표현은 이 날을 기준으로 환산한다.\n" if base_day else "",
        f"\n회의:\n{_dumps(meeting)}",
        f"\n사람 벌의 안건(재료다 — 묶어도 나눠도 되고, 계보에 그 id 를 적는다):\n{_dumps(memo_agendas)}",
        f"\nAI 벌의 안건(네가 회의 중에 세운 것이다 — 같은 재료다):\n{_dumps(ai_agendas)}",
        f"\n사람 벌의 줄:\n{_dumps(memo_lines)}",
        f"\nAI 벌의 줄(네가 회의 중에 낸 것이다):\n{_dumps(ai_lines)}",
    ]
    if catalog:
        sections.append(f"\n{catalog}")
    # 재전사한 확정 발화 전량 — 사실의 SoT 다(§8-3). 매번 싣는다(OQ-904).
    sections.append(f"\n확정 발화 전량(재전사본 — 이것이 사실의 기준이다):\n{_dumps(transcript)}")
    return "".join(sections)
