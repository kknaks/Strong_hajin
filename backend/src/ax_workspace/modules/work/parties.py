"""업무 한 건에 사람이 서는 **세 자리** — 요청자 · 담당 · 참조자(cc).

이 파일은 **순수**하다. 저장소도 원장도 모르고, 「이 사람이 어느 자리에 있나」와 「그 자리가 무엇을
여나」만 답한다. 자리를 나눠 두는 이유는 하나다: 예전에는 셋을 `_is_participant` 하나로 묶어서
**읽을 수 있다**와 **답할 수 있다**가 같은 판정에서 나왔다. cc 를 업무(Task) 쪽에도 열면서 그대로
두면, 참조로 받은 사람이 남의 일을 수정·수락할 수 있는 길이 조용히 함께 열린다.

세 자리가 여는 것은 서로 다르다.

| 자리 | 읽기 | 논의 | 판단(수락·거절·협의) | 내용 수정·거두기 | 수행(상태·체크리스트) |
|---|---|---|---|---|---|
| 요청자 | ○ | ○ | ✕ | ○ | ✕ |
| 담당 | ○ | ○ | ○ | ✕ | ○ |
| 참조자 | ○ | ○ | ✕ | ✕ | ✕ |

**참조자는 읽기와 논의뿐이다.** 그래서 cc 를 더하는 것은 누구의 권한도 넓히지 않는다 — 보이는 범위만 넓힌다.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal


#: 업무 한 건의 세 자리. 순서가 곧 **우선순위**다 — 한 사람이 여러 자리에 있으면 더 넓은 자리로 읽는다.
WorkParty = Literal["requester", "assignee", "cc"]

PARTY_ORDER: tuple[WorkParty, ...] = ("requester", "assignee", "cc")

#: 자리마다 열리는 것. 없는 것을 여기 적지 않는다 — 이 표가 곧 계약이다.
PARTY_POWERS: dict[WorkParty, frozenset[str]] = {
    "requester": frozenset({"read", "discuss", "revise"}),
    "assignee": frozenset({"read", "discuss", "decide", "perform"}),
    "cc": frozenset({"read", "discuss"}),
}


def _clean(values: Iterable[object] | None) -> set[str]:
    return {text for value in (values or ()) if (text := str(value or "").strip())}


def party_of(
    member_id: object,
    *,
    requester_ids: Iterable[object] | None = None,
    assignee_ids: Iterable[object] | None = None,
    cc_member_ids: Iterable[object] | None = None,
) -> WorkParty | None:
    """이 사람이 선 자리. 어느 자리도 아니면 `None` 이고, 그때는 **존재를 숨긴다**.

    `requester_ids` 가 여럿인 것은 회의 승격 때문이다 — 요청자 자리에 시스템이 앉고 누른 사람이 그
    자리를 대신 선다 (D40). 자리 판정을 부르는 쪽이 그 두 값을 함께 넘긴다.
    """
    member = str(member_id or "").strip()
    if not member:
        return None
    seats: dict[WorkParty, set[str]] = {
        "requester": _clean(requester_ids),
        "assignee": _clean(assignee_ids),
        "cc": _clean(cc_member_ids),
    }
    for party in PARTY_ORDER:
        if member in seats[party]:
            return party
    return None


def may(party: WorkParty | None, power: str) -> bool:
    """그 자리가 이것을 여는가. 자리가 없으면 아무것도 열리지 않는다."""
    if party is None:
        return False
    return power in PARTY_POWERS[party]


def may_read(party: WorkParty | None) -> bool:
    return may(party, "read")


def may_discuss(party: WorkParty | None) -> bool:
    """논의에 참여할 수 있는가 — **읽기와 같은 문이다** (SPEC-003 §4 논의). 판단은 열지 않는다."""
    return may(party, "discuss")


def may_decide(party: WorkParty | None) -> bool:
    """수락·거절·협의 — **받는 사람뿐이다.**"""
    return may(party, "decide")


def may_revise(party: WorkParty | None) -> bool:
    """내용 수정·재상신·거두기 — **보낸 사람뿐이다.**"""
    return may(party, "revise")
