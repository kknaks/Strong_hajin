"""회의록 내보내기 — 마지막 저장분을 **주제 스레드형 A4 페이지**로 (SCAX-SPEC-004 §8-10 · D39).

판이 없으므로 마지막 저장분이 그 회의록이다. 담는 것은 **회의 정보 · 안건별 줄 · 다음 할 일** 셋이고,
저장 위치·provider 참조·계정 같은 내부 값은 담지 않는다 (WP Pre-deploy Check).

형식은 HTML 하나다 (§2.2) — PDF·DOCX 는 데모 범위 밖이다.

못박는 것 —

1. **표로 늘어놓지 않는다.** 회의는 시간 순으로 흐른 이야기이므로 스레드 축 위에 안건을 세우고
   그 아래 줄을 단다 (§8-10). 마크업과 CSS 는 디자이너 정본을 그대로 옮겼고 이 모듈은 값만 채운다.
2. **외부 자원을 부르지 않는다.** `<link>`·`<script>`·`@import`·`url()` 이 하나도 없다 — 내려받은
   파일이 네트워크 없이도 같은 모양으로 열려야 한다.
3. **쪽은 서버가 센다.** `counter(page)` 는 `@page` 마진 박스에서만 도는데 Chrome 이 그것을
   지원하지 않는다. 그래서 내용을 재어 `.page` 를 나누고 번호를 박는다 (템플릿 REPORT §5-1).
4. 사람이 읽을 글자만 나간다 — id·저장 위치·계정은 한 자도 싣지 않는다.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from html import escape
from math import ceil
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo


TITLELESS = "제목 없는 회의"

TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "export_thread.html"

#: 회의가 선 자리의 시간대. 벽시계 시각은 여기서 읽는다 — UTC 로 찍으면 아홉 시간이 밀린다.
OFFICE_TIMEZONE = ZoneInfo("Asia/Seoul")

_WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")

#: 안건 머리에 세우는 시각 칩의 개수. 넘치는 만큼은 「+N」 하나로 접는다 (정본 §2).
MAX_AGENDA_STAMPS = 3

# ── 조판 치수 (템플릿 REPORT §3 「페이지 나누기 계약」의 실측값) ────────────────────
#: 한 장이 담는 본문 높이 — 261mm ≈ 986px.
PAGE_BODY_HEIGHT = 986
#: 첫 장 머리(회의명·일시·참석자와 밑줄)가 먹는 높이.
FIRST_HEAD_HEIGHT = 74
#: 이어지는 장 머리 — 회의명 한 줄로 줄인다.
CONT_HEAD_HEIGHT = 28
#: 요약 상자의 테두리·여백, 그리고 접힌 줄 하나마다 더해지는 높이.
SUMMARY_BOX_HEIGHT = 30
SUMMARY_LINE_HEIGHT = 18
#: 안건 하나의 머리(제목·칩)와, 안건 사이 간격.
AGENDA_HEAD_HEIGHT = 36
AGENDA_GAP_HEIGHT = 16
#: 본문 줄 하나. 긴 줄은 접히므로 접힌 수만큼 곱한다 (REPORT §5-4).
LINE_HEIGHT = 21
#: 「다음 할 일」 상자의 테두리·라벨과 할 일 하나.
TODOS_BOX_HEIGHT = 46
TODO_HEIGHT = 21
#: 본문 폭 178mm 에서 12px 한글이 한 줄에 들어가는 대략의 글자 수 (REPORT §5-4).
CHARS_PER_LINE = 60

_FRAGMENT = re.compile(r"<!--#fragment (?P<name>[a-z_]+)-->\n(?P<body>.*?)<!--/#fragment-->", re.S)


def _fragments() -> dict[str, str]:
    """템플릿 파일을 조각으로 가른다. 마크업과 CSS 는 그 파일이 소유한다 — 여기서 짓지 않는다."""
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    return {match["name"]: match["body"] for match in _FRAGMENT.finditer(text)}


def _fill(fragment: str, **values: Any) -> str:
    """`{{이름}}` 은 이스케이프해서, `{{{이름}}}` 은 이미 만들어진 마크업으로 갈아 끼운다.

    세 겹을 **먼저** 바꾼다 — 두 겹을 먼저 바꾸면 세 겹의 안쪽이 먼저 걸려 남은 중괄호가 글자로 샌다.
    """
    filled = fragment
    for key, value in values.items():
        filled = filled.replace("{{{" + key + "}}}", str(value))
    for key, value in values.items():
        filled = filled.replace("{{" + key + "}}", escape(str(value)))
    return filled


# ── 값 고르기 ────────────────────────────────────────────────────────────────


def _title(meeting: dict[str, Any]) -> str:
    """회의명. 사람이 지은 제목이 먼저이고, 없으면 합성이 낸 후보, 그것도 없으면 「제목 없는 회의」."""
    return (meeting.get("title") or meeting.get("title_candidate") or "").strip() or TITLELESS


def _local(value: Any) -> datetime | None:
    """저장된 ISO 글자를 사옥 지역 시각으로. 읽히지 않으면 없는 것으로 친다 — 내보내다 넘어지지 않는다."""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(OFFICE_TIMEZONE)


def _date(meeting: dict[str, Any]) -> str:
    starts = _local(meeting.get("starts_at"))
    if starts is None:
        return ""
    return f"{starts:%Y. %m. %d.}({_WEEKDAYS[starts.weekday()]})"


def _clock(value: Any) -> str:
    moment = _local(value)
    return "" if moment is None else f"{moment:%H:%M}"


def _people(meeting: dict[str, Any]) -> str:
    """사내와 사외를 한 줄로. 사외는 이름뿐이므로 그대로 이어 적는다."""
    names = [str(person.get("display_name") or person.get("member_id") or "") for person in meeting.get("attendees") or []]
    names += [str(name) for name in meeting.get("external_attendees") or []]
    return ", ".join(name for name in names if name)


def _stamp_of(line: dict[str, Any], started_at: datetime | None) -> str:
    """줄 오른쪽의 벽시계 시각 = 회의를 연 시각 + 그 줄의 시작 지점.

    합성 줄은 근거 구간의 **첫 발화**를 기준으로 삼고(REPORT §5-3 권고), 메모 줄은 서버가 매긴
    `at_ms` 를 쓴다. 둘 다 없으면 **빈칸**이다 — 없는 시각을 지어내지 않는다.
    """
    if started_at is None:
        return ""
    evidence = line.get("evidence") or []
    offset = evidence[0].get("from_ms") if evidence else line.get("at_ms")
    if offset is None:
        return ""
    try:
        return f"{started_at + timedelta(milliseconds=int(offset)):%H:%M}"
    except (TypeError, ValueError, OverflowError):
        return ""


def _wrapped(text: str, *, per_line: int = CHARS_PER_LINE) -> int:
    """접히고 나면 몇 줄인가. 재는 것은 대략이고, 넘치는 쪽보다 남는 쪽으로 틀리게 둔다."""
    return max(1, ceil(len(text or "") / per_line))


# ── 조각 만들기 ──────────────────────────────────────────────────────────────


def _agenda_block(agenda: dict[str, Any], started_at: datetime | None, parts: dict[str, str], *, head: bool) -> str:
    """안건 하나. `head` 가 거짓이면 앞 장에서 이어지는 뒷부분이라 칩을 다시 세우지 않는다."""
    lines = agenda.get("lines") or []
    stamps = [stamp for stamp in (_stamp_of(line, started_at) for line in lines) if stamp]
    chips: list[str] = []
    if head:
        chips.append(
            '<span class="chip done">결론 남</span>'
            if agenda.get("concluded")
            else '<span class="chip open">결론 안 남</span>'
        )
        for stamp in stamps[:MAX_AGENDA_STAMPS]:
            chips.append(f'<span class="stamp">{escape(stamp)}</span>')
        if len(stamps) > MAX_AGENDA_STAMPS:
            chips.append(f'<span class="stamp more">+{len(stamps) - MAX_AGENDA_STAMPS}</span>')

    rendered_lines = ""
    if lines:
        items = "".join(
            _fill(parts["line"], text=line.get("text") or "", at=_stamp_of(line, started_at)) for line in lines
        )
        rendered_lines = _fill(parts["lines"], items=items)

    rendered_todos = ""
    todos = agenda.get("todos") or [] if head else []
    if todos:
        items = "".join(_todo_block(todo, started_at, agenda, parts) for todo in todos)
        rendered_todos = _fill(parts["todos"], items=items)

    return _fill(
        parts["agenda"],
        title=agenda.get("title") or "",
        chips="".join(f"\n        {chip}" for chip in chips),
        lines=rendered_lines,
        todos=rendered_todos,
    )


def _todo_block(todo: dict[str, Any], started_at: datetime | None, agenda: dict[str, Any], parts: dict[str, str]) -> str:
    """다음 할 일 한 줄. 오른쪽 자리는 셋 중 하나다 — 이미 요청된 것 · 근거 시각 · 「근거 없음」."""
    if todo.get("linked"):
        mark = '<span class="chip requested">요청됨</span>'
    else:
        stamp = _todo_stamp(todo, agenda, started_at)
        mark = (
            f'<span class="stamp">{escape(stamp)}</span>'
            if stamp
            else '<span class="stamp none">근거 없음</span>'
        )
    return _fill(
        parts["todo"],
        title=todo.get("title") or "",
        due=_due(todo.get("due_candidate")),
        mark=mark,
    )


def _todo_stamp(todo: dict[str, Any], agenda: dict[str, Any], started_at: datetime | None) -> str:
    """후보가 딛은 줄의 시각. 근거로 가리킨 줄을 찾아 그 줄의 시각을 그대로 쓴다."""
    wanted = {str(line_id) for line_id in (todo.get("reference") or {}).get("line_ids") or []}
    if not wanted:
        return ""
    for line in agenda.get("lines") or []:
        if str(line.get("line_id")) in wanted:
            return _stamp_of(line, started_at)
    return ""


def _due(value: Any) -> str:
    """기한은 월-일만 낸다 — 회의록을 읽는 자리에서 연도는 이미 머리에 있다."""
    if not value:
        return ""
    text = str(value)
    return text[5:] if len(text) >= 10 and text[4] == "-" else text


# ── 장 나누기 ────────────────────────────────────────────────────────────────


def _agenda_height(agenda: dict[str, Any], *, head: bool) -> int:
    # 이어지는 조각도 제목을 다시 세우므로 머리 높이는 같다 — 다른 것은 다음 할 일을 반복하지 않는 것뿐이다.
    height = AGENDA_HEAD_HEIGHT + AGENDA_GAP_HEIGHT
    for line in agenda.get("lines") or []:
        height += LINE_HEIGHT * _wrapped(str(line.get("text") or ""))
    todos = agenda.get("todos") or [] if head else []
    if todos:
        height += TODOS_BOX_HEIGHT + TODO_HEIGHT * len(todos)
    return height


def _split_agenda(agenda: dict[str, Any], room: int, *, head: bool) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """한 장에 안 들어가는 안건을 **줄 단위로** 가른다 (D39 「안건/줄 단위로 끊는다」).

    앞부분은 지금 장에 남기고 뒷부분은 다음 장으로 넘긴다. 넘어간 쪽은 제목만 다시 세우고 칩과
    다음 할 일을 반복하지 않는다 — 같은 안건이 두 번 결론 난 것처럼 읽히면 안 된다.
    한 줄도 못 넣을 자리면 통째로 넘긴다.
    """
    lines = list(agenda.get("lines") or [])
    used = AGENDA_HEAD_HEIGHT + AGENDA_GAP_HEIGHT
    taken = 0
    for line in lines:
        cost = LINE_HEIGHT * _wrapped(str(line.get("text") or ""))
        if used + cost > room:
            break
        used += cost
        taken += 1
    if taken == 0:
        return None, agenda
    if taken == len(lines):
        return agenda, None
    return (
        {**agenda, "lines": lines[:taken], "todos": []},
        {**agenda, "lines": lines[taken:], "_continued": True},
    )


def _paginate(document: dict[str, Any], *, summary_lines: int) -> list[list[tuple[dict[str, Any], bool]]]:
    """안건을 장에 나눠 담는다. 한 장은 `(안건, 머리를 세우는가)` 의 목록이다.

    안건은 `break-inside: avoid` 라 되도록 통째로 한 장에 둔다 — 그래도 한 장을 넘는 안건은
    줄 단위로 갈라 다음 장으로 잇는다.
    """
    first = PAGE_BODY_HEIGHT - FIRST_HEAD_HEIGHT
    if summary_lines:
        first -= SUMMARY_BOX_HEIGHT + SUMMARY_LINE_HEIGHT * summary_lines
    rest = PAGE_BODY_HEIGHT - CONT_HEAD_HEIGHT

    pages: list[list[tuple[dict[str, Any], bool]]] = [[]]
    room = first
    pending = [(agenda, True) for agenda in document.get("agendas") or []]
    while pending:
        agenda, head = pending.pop(0)
        height = _agenda_height(agenda, head=head)
        if height <= room:
            pages[-1].append((agenda, head))
            room -= height
            continue
        kept, carried = _split_agenda(agenda, room, head=head)
        if kept is not None:
            pages[-1].append((kept, head))
            head = False
        if carried is None:
            room = rest
            continue
        # 새 장을 연다. 앞부분을 남겼으면 넘어가는 쪽은 이어지는 조각이므로 머리를 다시 세우지 않는다.
        pages.append([])
        room = rest
        pending.insert(0, (carried, head))
        if kept is None and _agenda_height(carried, head=head) > rest:
            # 빈 장에도 안 들어가는 안건이다 — 가르고 또 갈라도 끝이 없으므로 그 장에 그대로 둔다.
            # 템플릿이 `min-height` 로만 잠가 두어 넘친 만큼은 잘리지 않고 밀려 보인다.
            pages[-1].append(pending.pop(0))
            room = 0
    return [page for page in pages if page] or [[]]


# ── 내보내기 ─────────────────────────────────────────────────────────────────


def render_meeting_html(document: dict[str, Any], *, exported_at: datetime | None = None) -> str:
    """회의록 한 벌. 주제 스레드형 A4 페이지이고, 장 나눔과 쪽 번호는 여기서 센다."""
    parts = _fragments()
    meeting = document["meeting"]
    title = _title(meeting)
    started_at = _local(meeting.get("started_at")) or _local(meeting.get("starts_at"))
    # 요약 자리에는 회의가 들고 있는 산문이 온다 — 그것이 없으면 **블록째 뺀다** (정본 §2).
    summary = (meeting.get("purpose") or "").strip()
    stamped = (exported_at or datetime.now(UTC)).astimezone(OFFICE_TIMEZONE)

    layout = _paginate(document, summary_lines=_wrapped(summary) if summary else 0)
    rendered: list[str] = []
    for number, page in enumerate(layout, start=1):
        if number == 1:
            head = _fill(
                parts["first_head"],
                title=title,
                date=_date(meeting),
                starts=_clock(meeting.get("starts_at")),
                ends=_clock(meeting.get("ends_at")),
                attendees=_people(meeting),
                summary=_fill(parts["summary"], summary=summary) if summary else "",
            )
        else:
            head = _fill(parts["cont_head"], title=title, date=_date(meeting))
        items = "".join(
            _agenda_block(agenda, started_at, parts, head=head_shown) for agenda, head_shown in page
        )
        rendered.append(
            _fill(parts["page"], head=head, items=items, page_no=number, exported_at=f"{stamped:%Y-%m-%d %H:%M}")
        )
    return _fill(parts["document"], title=title, pages="".join(rendered)).strip() + "\n"


def export_filename(meeting: dict[str, Any]) -> str:
    """파일 이름은 제목이다. 내부 식별자를 파일 이름으로 흘리지 않는다."""
    safe = "".join(character for character in _title(meeting) if character.isalnum() or character in " -_")
    return safe.strip() or TITLELESS
