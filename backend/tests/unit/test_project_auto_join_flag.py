"""**조건 ① 의 흔적을 읽는 자리** — 폴백이 있으면 자동 해제가 통째로 조용히 사라진다.

떼는 자리는 넷(요청 거절 · 요청 철회 · 배정 거절 · 배정 철회)이고 **넷이 전부** 「이 배정이 그 사람을
새로 붙였나」를 묻는다 (SPEC-005 §4 · D-14). 그 사실이 사는 칸은 `task_assignments.auto_project_join`
하나다.

그 칸을 **기본값 있는 `getattr` 로** 읽으면, 칸이 사라지거나 이름이 바뀐 날 **오류 하나 없이
「안 붙였다」로 읽힌다** — 네 자리가 전부 무동작이 되는데 테스트도 로그도 아무 말을 안 한다.
「초록인데 아무것도 안 지키는」 자리가 되는 것이다. 그래서 읽는 자리를 하나로 모으고 **폴백을 지웠다.**
이 파일은 그 폴백이 되돌아오지 않는 것을 지킨다.
"""
from dataclasses import dataclass

import pytest

from ax_workspace.modules.work.projects import auto_joined_by


@dataclass
class _AssignmentRow:
    """`task_assignments` 한 행이 이 판정에 내놓는 것 — **칸 하나뿐**이다."""

    auto_project_join: bool | None


def test_the_flag_is_read_straight_off_the_assignment_row() -> None:
    """붙인 배정은 참, 안 붙인 배정은 거짓. **칸은 nullable 이라 `None` 도 「안 붙였다」다.**"""
    assert auto_joined_by(_AssignmentRow(auto_project_join=True)) is True
    assert auto_joined_by(_AssignmentRow(auto_project_join=False)) is False
    # 이 칸이 생기기 전에 만들어진 행은 값이 비어 있다 — 그 행이 붙인 것은 아니다.
    assert auto_joined_by(_AssignmentRow(auto_project_join=None)) is False


def test_no_assignment_row_at_all_is_not_the_same_as_a_missing_column() -> None:
    """**배정 행이 아직 없는 것**은 요청 쪽에서 실제로 일어난다 (`request_assignment()` 가 못 찾는 경우).

    그것은 「안 붙였다」가 **맞고**, 칸이 사라진 것과는 다른 사실이다. 그래서 `None` 만 접는다.
    """
    assert auto_joined_by(None) is False


def test_a_row_that_lost_that_column_breaks_loudly_instead_of_reading_false() -> None:
    """**이 테스트가 지키는 것은 폴백이 돌아오지 않는 것이다.**

    칸 이름이 바뀐 행을 넘기면 `AttributeError` 가 나야 한다. 여기서 `False` 가 나오는 날은
    **떼는 자리 넷이 전부 조용히 멈춘 날**이고, 그때 깨지는 테스트가 이것 하나다.
    """

    class _RenamedColumn:
        # 이름만 바뀐 모양 — `getattr(..., False)` 였다면 이것이 「안 붙였다」로 통과했다.
        auto_join_project = True

    with pytest.raises(AttributeError):
        auto_joined_by(_RenamedColumn())
