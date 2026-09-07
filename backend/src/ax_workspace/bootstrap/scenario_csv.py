"""예제 업무 계획을 저장소 밖의 CSV로 읽는다.

조직 dataset과 같은 자리, 같은 모양이다 — 사람이 편집할 수 있는 외부 key로 서로를 가리키는 표들이고, 저장소는
그 표를 읽는 계약만 갖는다. 계획에는 실제 구성원의 key와 고객사 이름이 들어가므로 Git 밖에 둔다.

읽기만 CSV이고 넣는 길은 그대로다. 여기서 만드는 모든 것은 제품의 정식 command를 그 사람으로서 지나간다 —
표를 데이터베이스에 그대로 붓지 않는다.

    scenario_people.csv        alias,member_key
    scenario_work.csv          key,owner,title,description,starts_in,days,state,project,parent
    scenario_requests.csv      key,requester,assignee,title,description,due_in,accept
    scenario_request_cc.csv    request_key,member
    scenario_assignments.csv   key,assigner,assignee,title,description,starts_in,days,accept
    scenario_meetings.csv      key,owner,unit,title,starts_in,minutes,visibility,note
    scenario_attendees.csv     meeting_key,member
    scenario_checklists.csv    owner_kind,owner_key,text
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from ax_workspace.bootstrap.scenario import Ask, Gathering, Handout, ScenarioPlan, Work

#: 표 이름 → 반드시 있어야 하는 열. 나머지 열은 비어 있어도 되고, 모르는 열은 거절한다.
TABLES: dict[str, tuple[str, ...]] = {
    "scenario_people": ("alias", "member_key"),
    "scenario_work": ("key", "owner", "title", "description", "starts_in", "days", "state", "project", "parent"),
    "scenario_requests": ("key", "requester", "assignee", "title", "description", "due_in", "accept"),
    "scenario_request_cc": ("request_key", "member"),
    "scenario_assignments": ("key", "assigner", "assignee", "title", "description", "starts_in", "days", "accept"),
    "scenario_meetings": ("key", "owner", "unit", "title", "starts_in", "minutes", "visibility", "note"),
    "scenario_attendees": ("meeting_key", "member"),
    "scenario_checklists": ("owner_kind", "owner_key", "text"),
}
CHECKLIST_KINDS = frozenset({"work", "request", "assignment"})


class ScenarioPlanError(ValueError):
    """계획이 읽히지 않는다. 어느 표 몇 번째 줄의 무엇인지까지 말한다."""


def initialize(target: Path) -> None:
    """빈 표들을 만든다. 다른 사람이 자기 조직의 예제를 같은 도구로 쓸 수 있게."""
    for table, header in TABLES.items():
        path = target / f"{table}.csv"
        if path.exists():
            continue
        with path.open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerow(header)


def load_plan(target: Path) -> ScenarioPlan:
    rows = {table: _read(target, table, header) for table, header in TABLES.items()}
    people = {row["alias"]: row["member_key"] for row in rows["scenario_people"] if row["alias"]}

    def who(name: str, where: str) -> str:
        """`scenario_people`에 적힌 이름이면 그 key로, 아니면 적힌 그대로 쓴다."""
        if not name:
            raise ScenarioPlanError(f"{where}에 사람이 비어 있습니다")
        return people.get(name, name)

    checklists: dict[tuple[str, str], list[str]] = {}
    for index, row in enumerate(rows["scenario_checklists"]):
        kind = row["owner_kind"]
        if kind not in CHECKLIST_KINDS:
            raise ScenarioPlanError(f"scenario_checklists[{index}] · 모르는 owner_kind: {kind}")
        checklists.setdefault((kind, row["owner_key"]), []).append(row["text"])

    cc: dict[str, list[str]] = {}
    for index, row in enumerate(rows["scenario_request_cc"]):
        cc.setdefault(row["request_key"], []).append(who(row["member"], f"scenario_request_cc[{index}]"))

    attendees: dict[str, list[str]] = {}
    for index, row in enumerate(rows["scenario_attendees"]):
        attendees.setdefault(row["meeting_key"], []).append(who(row["member"], f"scenario_attendees[{index}]"))

    seen: set[str] = set()
    work: list[Work] = []
    for index, row in enumerate(rows["scenario_work"]):
        where = f"scenario_work[{index}]"
        key = _key(row["key"], where)
        if key in seen:
            raise ScenarioPlanError(f"{where} · key가 겹칩니다: {key}")
        if row["parent"] and row["parent"] not in seen:
            # 상위가 뒤에 있으면 만들 때 가리킬 것이 없다. 표의 순서가 곧 만드는 순서다.
            raise ScenarioPlanError(f"{where} · 상위 업무({row['parent']})가 이 줄보다 뒤에 있습니다")
        seen.add(key)
        work.append(
            Work(
                key=key,
                owner=who(row["owner"], where),
                title=_required(row["title"], "title", where),
                description=row["description"],
                starts_in=_optional_int(row["starts_in"], "starts_in", where),
                days=_optional_int(row["days"], "days", where),
                state=row["state"] or "open",
                project=row["project"],
                parent=row["parent"],
                checklist=tuple(checklists.get(("work", key), ())),
            )
        )

    asks = [
        Ask(
            requester=who(row["requester"], f"scenario_requests[{index}]"),
            assignee=who(row["assignee"], f"scenario_requests[{index}]"),
            title=_required(row["title"], "title", f"scenario_requests[{index}]"),
            description=row["description"],
            due_in=_int(row["due_in"], "due_in", f"scenario_requests[{index}]"),
            checklist=tuple(checklists.get(("request", row["key"]), ())),
            accept=_bool(row["accept"], "accept", f"scenario_requests[{index}]"),
            cc=tuple(cc.get(row["key"], ())),
        )
        for index, row in enumerate(rows["scenario_requests"])
    ]

    handouts = [
        Handout(
            assigner=who(row["assigner"], f"scenario_assignments[{index}]"),
            assignee=who(row["assignee"], f"scenario_assignments[{index}]"),
            title=_required(row["title"], "title", f"scenario_assignments[{index}]"),
            description=row["description"],
            starts_in=_int(row["starts_in"], "starts_in", f"scenario_assignments[{index}]"),
            days=_int(row["days"], "days", f"scenario_assignments[{index}]"),
            checklist=tuple(checklists.get(("assignment", row["key"]), ())),
            accept=_bool(row["accept"], "accept", f"scenario_assignments[{index}]"),
        )
        for index, row in enumerate(rows["scenario_assignments"])
    ]

    gatherings = [
        Gathering(
            owner=who(row["owner"], f"scenario_meetings[{index}]"),
            unit=_required(row["unit"], "unit", f"scenario_meetings[{index}]"),
            title=_required(row["title"], "title", f"scenario_meetings[{index}]"),
            starts_in=_int(row["starts_in"], "starts_in", f"scenario_meetings[{index}]"),
            minutes=_int(row["minutes"], "minutes", f"scenario_meetings[{index}]"),
            attendees=tuple(attendees.get(row["key"], ())),
            # 회의록의 줄바꿈은 CSV 한 칸 안에서 `\n` 두 글자로 적는다.
            note=row["note"].replace("\\n", "\n"),
            visibility=row["visibility"] or "public",
        )
        for index, row in enumerate(rows["scenario_meetings"])
    ]

    return ScenarioPlan(work=tuple(work), asks=tuple(asks), handouts=tuple(handouts), gatherings=tuple(gatherings))


def _read(target: Path, table: str, header: tuple[str, ...]) -> list[dict[str, str]]:
    path = target / f"{table}.csv"
    if not path.exists():
        # 없는 표는 빈 표다. 쓰지 않는 관계까지 파일로 만들라고 요구하지 않는다.
        return []
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as error:
        raise ScenarioPlanError(f"{table}.csv를 읽을 수 없습니다: {error}") from error
    for index, row in enumerate(rows):
        unknown = {name for name in row if name not in header}
        if unknown:
            raise ScenarioPlanError(f"{table}[{index}] · 모르는 열: {', '.join(sorted(unknown))}")
    return [{name: (row.get(name) or "").strip() for name in header} for row in rows]


def _key(value: str, where: str) -> str:
    return _required(value, "key", where)


def _required(value: str, field: str, where: str) -> str:
    if not value:
        raise ScenarioPlanError(f"{where}에 `{field}`가 없습니다")
    return value


def _int(value: str, field: str, where: str) -> int:
    parsed = _optional_int(value, field, where)
    if parsed is None:
        raise ScenarioPlanError(f"{where}에 `{field}`가 없습니다")
    return parsed


def _optional_int(value: str, field: str, where: str) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        raise ScenarioPlanError(f"{where}의 `{field}`는 정수여야 합니다: {value!r}") from None


def _bool(value: str, field: str, where: str) -> bool:
    lowered = value.lower()
    if lowered in {"", "false", "no", "0"}:
        return False
    if lowered in {"true", "yes", "1"}:
        return True
    raise ScenarioPlanError(f"{where}의 `{field}`는 true 또는 false여야 합니다: {value!r}")


def as_dict(plan: ScenarioPlan) -> dict[str, Any]:
    """무엇을 읽었는지 세어 보는 곳. 셀 값은 내보내지 않는다."""
    return {
        "work": len(plan.work),
        "requests": len(plan.asks),
        "assignments": len(plan.handouts),
        "meetings": len(plan.gatherings),
    }
