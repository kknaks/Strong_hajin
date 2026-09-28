"""상위 이동의 **V-8 파급 게이트** — 무엇을 세고 무엇을 이름으로 내나 (SPEC-007 §4 6번 · D-18).

**왜 단위 테스트인가.** 이 게이트의 두 갈래 중 하나는 **HTTP 표면에서 도달할 수 없다.**
게이트가 서는 조건은 「담당자가 이 업무의 담당자와 같은 직속 하위」이고, 업무 편집은 **그 업무를 든
사람**만 부를 수 있다(`repository.task(task_id, owner_id)` 의 소유 투영). 그래서 실제 요청에서 막는
하위는 **언제나 부르는 사람 자신이 든 것**이고 반드시 읽을 수 있다.

그런데 SPEC-007 §4 는 「**읽을 수 없는 하위까지 전부 센다** — 안 그러면 권한으로 게이트를 우회한다」를
계약으로 못 박았다. 그 절반은 **방어**이고, 방어가 실제로 서 있는지는 이 층에서만 셀 수 있다.
여기서 `may_read_task` 를 거짓으로 돌려 **이름이 사라지고 건수만 남는 것**과 **그래도 막는 것**을 센다.

계약의 나머지(어느 이동이 걸리나 · 직속만 보나 · 아무것도 안 움직이나)는
`tests/contract/test_task_parent_change.py` 가 실제 표면에서 센다.
"""
from types import SimpleNamespace

import pytest

from ax_workspace.modules.work.application import TaskApplication
from ax_workspace.modules.work.errors import TaskChildrenDirectNesting


def _node(identifier: str, title: str) -> SimpleNamespace:
    return SimpleNamespace(id=identifier, title=title)


class _Repository:
    """`children_of` 하나만 답하는 저장소 — 게이트가 **직속 하위**만 묻는다는 사실이 여기 드러난다."""

    def __init__(self, children: list[SimpleNamespace]) -> None:
        self._children = children
        self.asked: list[str] = []

    def children_of(self, task_id):  # noqa: ANN001 - 스텁
        self.asked.append(str(task_id))
        return list(self._children)


def _application(children: list[SimpleNamespace], *, holders: dict[str, str | None], readable: set[str]):
    """게이트만 살아 있는 최소 조립 — 담당자 판정과 읽기 판정을 갈아 끼운다."""
    application = TaskApplication.__new__(TaskApplication)
    application.repository = _Repository(children)
    application._holder_of = lambda task: holders.get(str(task.id))  # type: ignore[method-assign]
    application.may_read_task = lambda principal, task_id: str(task_id) in readable  # type: ignore[method-assign]
    return application


MOVER = _node("mover", "옮길 업무")
PARENT = _node("parent", "새 상위")
PRINCIPAL = SimpleNamespace(id="mina", capabilities=frozenset())


def test_the_gate_names_the_children_it_can_read() -> None:
    """읽을 수 있는 하위는 **이름으로** 나온다 — 무엇을 고쳐야 하는지가 그 이름이다."""
    child = _node("c1", "민아가 든 하위")
    application = _application(
        [child], holders={"mover": "mina", "parent": "mina", "c1": "mina"}, readable={"c1"}
    )
    with pytest.raises(TaskChildrenDirectNesting) as raised:
        application._require_children_may_follow(PRINCIPAL, MOVER, PARENT)
    assert "민아가 든 하위" in str(raised.value)
    assert raised.value.blocking == ({"task_id": "c1", "title": "민아가 든 하위"},)


def test_a_child_the_caller_cannot_read_still_blocks_but_is_only_a_count() -> None:
    """**읽을 수 없는 하위도 센다.** 안 그러면 권한으로 게이트를 우회한다 (SPEC-007 §4 6번).

    그리고 **이름도 제목도 내지 않는다** — 거절 사유로 남의 업무 제목을 알려 주면 그 자체가 곁수로다.
    선행 게이트와 미완 하위 거절이 이미 그 모양이다.
    """
    hidden = _node("c2", "볼 수 없는 하위")
    application = _application(
        [hidden], holders={"mover": "mina", "parent": "mina", "c2": "mina"}, readable=set()
    )
    with pytest.raises(TaskChildrenDirectNesting) as raised:
        application._require_children_may_follow(PRINCIPAL, MOVER, PARENT)
    message = str(raised.value)
    assert "1건이 중심 업무 밖에 놓입니다" in message
    assert "볼 수 없는 하위" not in message
    # `blocking` 에도 그 제목이 없다 — 밖으로 나가는 자리 전부에서 가린다.
    assert raised.value.blocking == ()


def test_mixed_children_name_the_readable_and_count_the_rest() -> None:
    """섞여 있으면 **읽을 수 있는 것만 이름**이고 나머지는 꼬리에 건수로 붙는다."""
    shown = _node("c3", "보이는 하위")
    hidden = _node("c4", "안 보이는 하위")
    application = _application(
        [shown, hidden],
        holders={"mover": "mina", "parent": "mina", "c3": "mina", "c4": "mina"},
        readable={"c3"},
    )
    with pytest.raises(TaskChildrenDirectNesting) as raised:
        application._require_children_may_follow(PRINCIPAL, MOVER, PARENT)
    message = str(raised.value)
    assert "보이는 하위" in message and "볼 수 없는 하위 1건" in message
    assert "안 보이는 하위" not in message


def test_clearing_the_parent_never_reaches_the_gate() -> None:
    """상위를 **비우면** 이 업무는 반드시 중심 업무다 — 저장소를 묻지도 않는다."""
    child = _node("c5", "민아가 든 하위")
    application = _application([child], holders={"mover": "mina", "c5": "mina"}, readable={"c5"})
    application._require_children_may_follow(PRINCIPAL, MOVER, None)
    assert application.repository.asked == []


def test_a_parent_held_by_someone_else_never_reaches_the_gate() -> None:
    """새 상위의 담당자가 **다르면** 이 업무는 이동 후에도 중심 업무다 (SPEC-003 §5 판정 그대로).

    그래서 하위를 세지 않는다 — 어긋남이 생길 수 없는 이동에 질의를 쓰지 않는다.
    """
    child = _node("c6", "민아가 든 하위")
    application = _application(
        [child], holders={"mover": "mina", "parent": "jiho", "c6": "mina"}, readable={"c6"}
    )
    application._require_children_may_follow(PRINCIPAL, MOVER, PARENT)
    assert application.repository.asked == []


def test_an_unheld_task_never_reaches_the_gate() -> None:
    """아무도 들지 않은 업무에는 「같은 사람의 직접 작업」이 성립하지 않는다.

    새 상위는 검증 3(`WORK_PARENT_UNASSIGNED`)을 지났으므로 담당이 있다 — 두 값이 다르다.
    """
    child = _node("c7", "하위")
    application = _application([child], holders={"mover": None, "parent": "mina", "c7": None}, readable={"c7"})
    application._require_children_may_follow(PRINCIPAL, MOVER, PARENT)
    assert application.repository.asked == []


def test_children_held_by_others_do_not_block() -> None:
    """담당자가 **다른** 직속 하위는 걸리지 않는다 — V-8 은 「같은 사람의 직접 작업」이다."""
    child = _node("c8", "지호가 든 하위")
    application = _application(
        [child], holders={"mover": "mina", "parent": "mina", "c8": "jiho"}, readable={"c8"}
    )
    application._require_children_may_follow(PRINCIPAL, MOVER, PARENT)
    # 하위는 **물었고**(직속만) 그 중 걸리는 것이 없었다.
    assert application.repository.asked == ["mover"]


def test_the_gate_asks_for_direct_children_only() -> None:
    """**자손 전체를 돌지 않는다** — `children_of` 한 번이고 `descendants_of` 를 부르지 않는다.

    손자의 V-8 판정은 자기 부모의 중심 업무 여부를 보고 **그 값은 이 이동으로 바뀌지 않는다** —
    이동이 이 업무의 담당자를 바꾸지 않기 때문이다. 프로젝트 파급(자손 전체)과 범위가 다른 이유가 그것이다.
    """
    child = _node("c9", "민아가 든 하위")
    application = _application(
        [child], holders={"mover": "mina", "parent": "mina", "c9": "mina"}, readable={"c9"}
    )
    with pytest.raises(TaskChildrenDirectNesting):
        application._require_children_may_follow(PRINCIPAL, MOVER, PARENT)
    assert application.repository.asked == ["mover"]
    assert not hasattr(application.repository, "descendants_of")
