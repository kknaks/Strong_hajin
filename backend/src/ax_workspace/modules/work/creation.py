"""One decision for every way a Task is created — 본인 · 수평 요청 · 관리자 배정 (WORK-001 Phase 1).

세 경로가 여기 하나를 지난다. 경로마다 결정 함수를 두면 한 곳만 고쳤을 때 나머지가 조용히 어긋난다.
이 파일은 **순수**하다 — 저장소도 권한 원장도 모른다. 무엇을 검사해야 하는지를 말할 뿐이고,
실제 검사는 그 검사를 소유한 application 이 한다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from ax_workspace.modules.organization_access.domain import (
    TASK_ASSIGN,
    TASK_SELF_MANAGE,
    WORK_REQUEST_CREATE,
)
from ax_workspace.modules.work.errors import (
    TaskError,
    TaskIdempotencyKeyRequired,
)


#: 생성 경로 셋. 「누구의 일이 되는가」가 아니라 「무엇을 검사하는가」로 가른다.
CreationRoute = Literal["self", "horizontal", "managed"]

#: 수신자에 대해 무엇을 물어야 하는가. `none` 은 「검사가 없다」가 아니라 **대상이 본인이라 물을 것이 없다**는 뜻이다.
RecipientCheck = Literal["none", "work_request_candidate", "assignment_scope"]

#: 멱등 원장의 명령 종류 — 키의 유효 범위는 (행위자 · 이 값 · 키)다. 전역이 아니다.
CREATE_TASK_COMMAND = "task.create"
ASSIGN_TASK_COMMAND = "task.assign"
CREATE_WORK_REQUEST_COMMAND = "work_request.create"
#: 시간 배정 생성 (SPEC-004 §5). **키의 유효 범위가 같은 원장 위에 서되 명령 종류가 다르다** —
#: 같은 키를 업무 생성과 배정 생성에 써도 둘은 섞이지 않는다.
CREATE_TASK_SCHEDULE_COMMAND = "task_schedule.create"

MAX_IDEMPOTENCY_KEY_LENGTH = 200


@dataclass(frozen=True, slots=True)
class TaskCreationContext:
    """한 번의 생성 의도. `managed` 는 관리자 배정 명령으로 들어왔다는 사실 하나다."""

    actor_id: str
    assignee_id: str | None
    managed: bool = False


@dataclass(frozen=True, slots=True)
class TaskCreationDecision:
    route: CreationRoute
    #: 실제로 담당이 될 사람. 본인 경로면 행위자 자신이다.
    assignee_id: str
    #: 행위자가 지나야 하는 기본 역량. 세 경로 모두 현행 검사를 그대로 쓴다.
    required_capability: str
    recipient_check: RecipientCheck


def decide_task_creation(context: TaskCreationContext) -> TaskCreationDecision:
    """수신자가 없거나 본인이면 본인 업무, 다르면 타인 배정 — 그리고 각자의 검사 종류를 함께 낸다.

    `self` 가 「검사 없음」이 아니다. **수신자 대상 검사만 없고** 인증과 기본 역량은 셋 다 그대로 지난다.
    """
    actor_id = str(context.actor_id)
    wanted = (context.assignee_id or "").strip() or None
    if wanted is None or wanted == actor_id:
        if context.managed:
            raise TaskError("use a self-owned task instead of assigning yourself")
        return TaskCreationDecision(
            route="self",
            assignee_id=actor_id,
            required_capability=TASK_SELF_MANAGE,
            recipient_check="none",
        )
    if context.managed:
        return TaskCreationDecision(
            route="managed",
            assignee_id=wanted,
            required_capability=TASK_ASSIGN,
            recipient_check="assignment_scope",
        )
    return TaskCreationDecision(
        route="horizontal",
        assignee_id=wanted,
        required_capability=WORK_REQUEST_CREATE,
        recipient_check="work_request_candidate",
    )


def require_idempotency_key(value: Any) -> str:
    """논리적 생성 명령마다 키가 **필수**다 — 없으면 재시도와 일부러 만든 두 건을 가를 근거가 없다.

    서버가 대신 만들어 채우는 fallback 을 두지 않는다. 그러면 응답을 잃은 호출자의 재시도가 새 업무가 된다.
    """
    key = str(value or "").strip()
    if not key:
        raise TaskIdempotencyKeyRequired("업무 생성에는 멱등 키가 필요합니다")
    if len(key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise TaskIdempotencyKeyRequired(
            f"멱등 키는 {MAX_IDEMPOTENCY_KEY_LENGTH}자 이하여야 합니다"
        )
    return key


def creation_fingerprint(payload: dict[str, Any]) -> str:
    """같은 키에 다른 내용이 왔는지 가르는 지문. **키의 재료가 아니다** — 내용이 같다고 두 명령을 합치지 않는다."""
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def followup_idempotency_key(meeting_id: Any, summary_id: Any, statement_index: Any) -> str:
    """회의 후보 identity 에서 만든 안정 키 — 재시도해도 같은 값이다.

    이것은 **생성 층**의 키이고, 후보 잠금(`modules/meetings/followups.py`)은 **다른 층**이다.
    키가 달라도 같은 후보는 한 건이어야 하므로 두 층을 다 남긴다.
    """
    return f"meeting-followup:{meeting_id}:{summary_id}:{statement_index}"
