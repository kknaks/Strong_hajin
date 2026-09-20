"""참조자·결재자 후보를 **누가 읽을 수 있는가** (WORK-003 gap D).

`POST /api/tasks` 의 본인 갈래는 `cc_member_ids` 와 `approver_id` 를 이미 받는다. 그런데 후보 조회만
`work_request.create` 를 요구해서, 업무만 만들 수 있는 사람의 생성 창에서는 **고를 명단이 비어 두 칸이
통째로 사라졌다** — 받는 값과 고를 목록이 어긋나 있었다. 경계를 여기서 못 박는다: 한 칸 넓어지고,
담당 후보는 그대로다.
"""
import pytest

from ax_workspace.modules.organization_access.domain import Principal
from ax_workspace.modules.work.request_errors import WorkRequestAccessDenied
from ax_workspace.modules.work.requests import WorkRequestApplication


class _Directory:
    def member_candidates(self, principal):
        return [{"id": "yuna", "display_name": "유나 (대표)"}]

    def work_request_assignee_candidates(self, principal):
        return [{"id": "jiho", "display_name": "지호 (팀장)"}]

    def is_work_request_assignee(self, principal, assignee_id):  # pragma: no cover - 계약 충족용
        return True

    def is_active_member(self, principal, member_id):  # pragma: no cover - 계약 충족용
        return True


def _principal(*capabilities: str) -> Principal:
    return Principal(id="mina", display_name="민아", organization_scope=frozenset({"product"}),
                     capabilities=frozenset(capabilities))


def _application() -> WorkRequestApplication:
    return WorkRequestApplication(object(), _Directory())


def test_the_cc_candidate_boundary_opens_one_notch_and_no_further() -> None:
    """**무엇이든 만들 수 있는 사람**이면 고를 명단을 읽는다 — 둘 중 하나면 된다.

    넓어지는 것은 그 한 칸까지다: 둘 다 없으면 여전히 거절이고, 담당 후보(`assignee_candidates`)는
    요청 생성 권한 그대로다.
    """
    application, expected = _application(), [{"id": "yuna", "display_name": "유나 (대표)"}]
    assert application.cc_candidates(_principal("work_request.create")) == expected
    assert application.cc_candidates(_principal("task.self_manage")) == expected
    with pytest.raises(WorkRequestAccessDenied):
        application.cc_candidates(_principal("work_request.read"))
    with pytest.raises(WorkRequestAccessDenied):
        application.assignee_candidates(_principal("task.self_manage"))
