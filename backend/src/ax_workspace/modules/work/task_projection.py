"""밖으로 나가는 업무 표시값의 **판정 한 자리** — 상태 투영과 기한 경과일.

**이 파일은 순수하다.** 저장소도 HTTP 도 모르고, 값만 받아 값을 낸다.

여기 있는 이유는 하나다 — **같은 판정이 두 벌이 되면 조용히 갈린다.** 업무 목록·업무 상세·캘린더는
`work/application.py` 를 지나지만 **프로젝트 상세는 그 파일을 지나지 않는다**(`work/projects.py` 가
`work/application.py` 를 import 하지 않는다). 그래서 프로젝트 상세만 내부 값 `completion_submitted` 를
그대로 냈고, 같은 업무가 두 표면에서 다른 상태로 읽혔다 (BASE-004 어긋남 ① · SPEC-005 §4).
**투영을 프로젝트 쪽에 한 벌 더 쓰면 그 어긋남을 다른 모양으로 다시 만드는 것**이라 둘 다 여기를 지난다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from ax_workspace.modules.work.lifecycle import TaskState

#: 업무의 「오늘」은 한 시간대에서만 판정된다. 두 곳에서 판정하면 목록의 `+N` 과 요약 스트립의
#: 「지연」이 자정 전후로 다른 수를 낸다 (SPEC-005 §4).
TASK_TIMEZONE = ZoneInfo("Asia/Seoul")

#: 기한 경과가 **없는** 상태들 — 이미 끝난 일을 늦었다고 계속 말하지 않는다 (정책 V-19).
_FINISHED_STATES = frozenset(
    {TaskState.DONE.value, TaskState.CANCELLED.value, TaskState.COMPLETION_SUBMITTED.value}
)


def today_for_tasks() -> date:
    """업무 표시값이 읽는 「오늘」."""
    return datetime.now(UTC).astimezone(TASK_TIMEZONE).date()


def external_state(state: Any) -> str:
    """밖으로 나가는 수행 상태는 **넷뿐이다** — `open` · `in_progress` · `done` · `cancelled`.

    코드에는 `completion_submitted` 가 남아 있다. 그것은 「완료 보고를 냈고 요청자가 아직 답하지
    않았다」는 사실이고, 계약은 그 사실을 **`state=done` + `derived.approval=awaiting_review`** 로
    말한다 (SPEC-003 §4 State · SPEC-001 §4). **enum 자체를 없애지 않는다** — 제출 가드와 승인 가드가
    그 내부 값을 읽고 있고, 정리는 후속이다. 여기서는 **투영만** 한다.

    `blocked` 도 계약에 없지만 이 자리는 그 값을 **발행하지도 없애지도 않는다**(M-6) — 들어오는
    그대로 낸다. 없는 계약을 여기서 지어내지 않는다.
    """
    return TaskState.DONE.value if str(state) == TaskState.COMPLETION_SUBMITTED.value else str(state)


def overdue_days(due_date: Any, state: Any, today: date) -> int | None:
    """기한이 며칠 지났는가. **표시값이다** — 상태·담당·기한을 아무것도 바꾸지 않는다 (정책 V-19).

    끝난 일에는 지연이 없다: 이미 끝난 것을 늦었다고 계속 말하지 않는다. **`completion_submitted` 도
    끝난 쪽**이라 값이 없다 — 투영이 그 값을 `done` 으로 접기 **전에** 판정해야 그 사실이 남는다.
    """
    if due_date is None or str(state) in _FINISHED_STATES:
        return None
    overdue = (today - due_date).days
    return overdue if overdue > 0 else None
