"""예제 업무 계획을 저장소 밖의 파일에서 읽는다.

조직 dataset과 같은 이유다 — 저장소는 계약과 loader를 갖고, 사람 key와 고객사 이름이 적힌 계획은 갖지 않는다.
계획 파일은 실제 구성원의 key를 가리키므로 dataset과 같은 자리(Git 밖)에 둔다.

파일 모양은 `ScenarioPlan`의 필드 이름을 그대로 쓴다. 맨 위의 `people`은 사람 key에 이름을 붙이는 곳으로,
계획 안에서는 그 이름만 쓰면 된다.

    people:
      hr_lead: m-abc
    own_work:
      - owner: hr_lead
        title: 9월 채용 공고 3건 게시
        description: 직무기술서 확인 후 게시한다.
        starts_in: 1
        days: 4
        checklist: [공고 초안, 게시]
"""
from __future__ import annotations

from dataclasses import MISSING, fields
from pathlib import Path
from typing import Any

import yaml

from ax_workspace.bootstrap.scenario import Ask, CompositeWork, Gathering, Handout, OwnWork, ProjectWork, ScenarioPlan

#: 계획 파일의 절 이름과 그 절이 만드는 것.
SECTIONS: dict[str, type] = {
    "own_work": OwnWork,
    "project_work": ProjectWork,
    "composite_work": CompositeWork,
    "asks": Ask,
    "handouts": Handout,
    "gatherings": Gathering,
}

#: 사람을 가리키는 필드. 여기에 적힌 이름은 `people`을 거쳐 실제 member key가 된다.
_PERSON_FIELDS = frozenset({"owner", "requester", "assignee", "assigner", "attendees"})


class ScenarioPlanError(ValueError):
    """계획 파일이 읽히지 않는다. 무엇이 어디서 잘못됐는지까지 말한다."""


def load_plan(path: Path) -> ScenarioPlan:
    """계획 파일 하나를 읽는다. 잘못된 곳은 절 이름과 몇 번째인지까지 붙여 알린다."""
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ScenarioPlanError(f"계획 파일을 읽을 수 없습니다: {path} · {error}") from error
    if not isinstance(document, dict):
        raise ScenarioPlanError(f"계획 파일은 최상위가 mapping이어야 합니다: {path}")

    people = document.get("people") or {}
    if not isinstance(people, dict):
        raise ScenarioPlanError("`people`은 이름 → member key mapping이어야 합니다")
    unknown = set(document) - set(SECTIONS) - {"people"}
    if unknown:
        raise ScenarioPlanError(f"모르는 절: {', '.join(sorted(unknown))}")

    built: dict[str, tuple[Any, ...]] = {}
    for section, shape in SECTIONS.items():
        rows = document.get(section) or []
        if not isinstance(rows, list):
            raise ScenarioPlanError(f"`{section}`은 목록이어야 합니다")
        built[section] = tuple(_row(shape, row, people, f"{section}[{index}]") for index, row in enumerate(rows))
    return ScenarioPlan(**built)


def _row(shape: type, row: Any, people: dict[str, Any], where: str) -> Any:
    if not isinstance(row, dict):
        raise ScenarioPlanError(f"{where}는 mapping이어야 합니다")
    known = {field.name for field in fields(shape)}
    unknown = set(row) - known
    if unknown:
        raise ScenarioPlanError(f"{where}에 모르는 항목: {', '.join(sorted(unknown))}")
    values: dict[str, Any] = {}
    for field in fields(shape):
        if field.name not in row:
            if field.default is MISSING and field.default_factory is MISSING:  # type: ignore[misc]
                raise ScenarioPlanError(f"{where}에 `{field.name}`이 없습니다")
            continue
        value = row[field.name]
        if field.name in _PERSON_FIELDS:
            value = [_person(item, people, where) for item in value] if isinstance(value, list) else _person(value, people, where)
        values[field.name] = _frozen(value)
    return shape(**values)


def _person(name: Any, people: dict[str, Any], where: str) -> str:
    """`people`에 적힌 이름이면 그 key로, 아니면 적힌 그대로 쓴다."""
    if not isinstance(name, str):
        raise ScenarioPlanError(f"{where}의 사람은 문자열이어야 합니다: {name!r}")
    return str(people.get(name, name))


def _frozen(value: Any) -> Any:
    """YAML의 목록은 계획의 tuple이 된다 — 계획은 읽고 나면 바뀌지 않는다."""
    return tuple(_frozen(item) for item in value) if isinstance(value, list) else value
