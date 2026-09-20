"""업무 한 건의 세 자리가 **각자 다른 것**을 연다 (`modules/work/parties.py`).

예전에는 요청자·담당·참조자가 `_is_participant` 하나로 묶여 있었다. 읽기만 물을 때는 맞는 답이었지만,
같은 판정이 「답할 수 있는가」와 「고칠 수 있는가」의 근거로도 쓰이면 참조로 이름만 적힌 사람에게
판단과 수정이 함께 열린다. 여기서 닫는 것은 그 하나다 — **자리마다 여는 것이 다르다.**
"""

import pytest

from ax_workspace.modules.work.parties import (
    PARTY_POWERS,
    may_decide,
    may_discuss,
    may_read,
    may_revise,
    party_of,
)


def test_each_seat_is_named_by_who_stands_in_it_and_nobody_else_gets_one() -> None:
    seats = dict(requester_ids=["mina"], assignee_ids=["jiho"], cc_member_ids=["yuna"])
    assert party_of("mina", **seats) == "requester"
    assert party_of("jiho", **seats) == "assignee"
    assert party_of("yuna", **seats) == "cc"
    # 어느 자리도 아니면 **자리가 없다** — 그때 아무것도 열리지 않는다.
    assert party_of("somebody-else", **seats) is None
    assert party_of("", **seats) is None
    assert party_of(None, **seats) is None


def test_a_promoted_request_keeps_the_person_who_pressed_it_in_the_requester_seat() -> None:
    """회의 승격이면 요청자 자리에 시스템이 앉고 누른 사람이 그 자리를 대신 선다 (D40)."""
    party = party_of(
        "mina",
        requester_ids=["system:meeting", "mina"],
        assignee_ids=["jiho"],
        cc_member_ids=[],
    )
    assert party == "requester" and may_revise(party) is True


def test_standing_in_two_seats_reads_as_the_wider_one() -> None:
    """한 사람이 요청자이면서 참조자로도 적혀 있으면 요청자다 — 더 좁은 자리로 떨어지지 않는다."""
    assert party_of("mina", requester_ids=["mina"], assignee_ids=[], cc_member_ids=["mina"]) == "requester"
    assert party_of("jiho", requester_ids=[], assignee_ids=["jiho"], cc_member_ids=["jiho"]) == "assignee"


@pytest.mark.parametrize(
    "party, read, discuss, decide, revise",
    [
        ("requester", True, True, False, True),
        ("assignee", True, True, True, False),
        # **참조자는 읽기와 논의뿐이다.** cc 를 더하는 것이 누구의 권한도 넓히지 않는 이유다.
        ("cc", True, True, False, False),
        (None, False, False, False, False),
    ],
)
def test_a_seat_opens_exactly_what_the_table_says_and_nothing_more(party, read, discuss, decide, revise) -> None:
    assert (may_read(party), may_discuss(party), may_decide(party), may_revise(party)) == (
        read, discuss, decide, revise,
    )


def test_the_power_table_never_grows_a_capability_by_accident() -> None:
    """표 자체를 잠근다 — 새 권한을 조용히 얹으면 여기서 먼저 걸린다."""
    assert PARTY_POWERS == {
        "requester": frozenset({"read", "discuss", "revise"}),
        "assignee": frozenset({"read", "discuss", "decide", "perform"}),
        "cc": frozenset({"read", "discuss"}),
    }
