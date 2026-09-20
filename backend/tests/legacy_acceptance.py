"""W1 **이전**에 만들어진 **과거 모양**의 행을 테스트에서 세우는 자리.

두 가지 옛 모양을 재현한다.

- **업무 없이 답을 기다리던 수평 요청.** W1 이전에는 수락이 업무를 세웠다. W1 이 그 단계를 없애
  즉시 배정으로 갔고, v2 가 응답 단계만 되돌렸다 — 그래서 v2 에서도 **발송이 업무를 세운다.**
  남은 차이는 「업무가 아직 없다」 하나이고, 그 모양에서 수락이 무엇을 해야 하는지를
  `make_request_look_pending()` 이 지킨다. **수락 회차는 제품이 연다** — 여기서 만들지 않는다.
- **답을 기다리던 관리자 배정.** 신규 배정은 즉시 `active` 이므로(BASE-002 O-28) 제품에는 그 모양을
  만드는 길이 없다. `make_assignment_look_pending()` 이 제품의 회차 여는 코드를 불러 세운다.

제품 코드에 「테스트를 위한 복원 경로」를 두지 않는다. 여기 있는 것은 **예전 배포가 남긴 행**이고,
신규 생성이 다시 이 모양을 만든다는 뜻이 아니다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete as sql_delete, select

from ax_workspace.platform.persistence import (
    Base,
    DecisionItemRecord,
    ReviewAssignmentRecord,
    SubjectVersionRecord,
    SubmissionRecord,
    TaskAssignmentRecord,
    TaskCreationAttemptRecord,
    TaskRecord,
    WorkRequestRecord,
    make_session_factory,
)


def _erase_task(session, task_id: UUID) -> None:
    """그 시절에는 업무가 아직 없었다. 이 업무를 가리키는 줄을 전부 걷고 업무를 지운다.

    어느 표가 업무를 가리키는지는 모델 metadata 가 안다 — 표 이름을 손으로 세면 표가 늘 때마다 낡는다.
    """
    for table in reversed(Base.metadata.sorted_tables):
        for key in table.foreign_keys:
            if key.column.table.name != "tasks" or key.column.name != "id":
                continue
            if table.name == "tasks":
                session.execute(sql_delete(table).where(table.c[key.parent.name] == task_id, table.c.id != task_id))
                continue
            session.execute(sql_delete(table).where(table.c[key.parent.name] == task_id))
    session.execute(sql_delete(Base.metadata.tables["tasks"]).where(Base.metadata.tables["tasks"].c.id == task_id))
    session.flush()


def make_request_look_pending(database_url: str, request_id: str | UUID) -> None:
    """방금 선 요청을 **업무 없이 답을 기다리던 옛 행**으로 되돌린다 (W1 이전의 모양).

    **회차는 더 이상 여기서 만들지 않는다.** v2 는 발송이 수락 회차를 열므로(요청 하나가 받는 사람에게
    던지는 질문 하나) 제품이 이미 세운 것을 쓴다 — 여기서 또 만들면 같은 요청에 판단 항목이 둘이 되고,
    한쪽을 답해도 다른 쪽이 남는다.

    남는 일은 하나다: **그 요청이 세운 업무를 지운다.** 그것이 이 헬퍼가 재현하려는 유일한 차이이고
    (그때는 수락이 업무를 세웠다), 그 모양에서 수락이 무엇을 해야 하는지가 이 픽스처가 지키는 계약이다.
    """
    session_factory = make_session_factory(database_url)
    with session_factory() as session:
        request = session.get(WorkRequestRecord, UUID(str(request_id)))
        assert request is not None

        for task in list(session.scalars(select(TaskRecord).where(TaskRecord.source_work_request_id == request.id))):
            _erase_task(session, task.id)

        request.state = "pending"
        # 제품이 연 회차가 그대로 있어야 한다 — 없으면 이 픽스처가 재현하려는 모양이 아니라
        # 아무도 답할 수 없는 행이 된다.
        item = session.scalar(
            select(DecisionItemRecord).where(
                DecisionItemRecord.kind == "work_request.acceptance",
                DecisionItemRecord.subject_id == request.subject_id,
            )
        )
        assert item is not None, "발송이 수락 회차를 열어야 한다"
        session.commit()


def pending_request(client, database_url: str, headers: dict[str, str], **body: Any) -> dict[str, Any]:
    """과거 모양의 요청 하나 — 보내고 나서 **그 요청이 세운 업무를 걷는다.**

    회차는 발송이 이미 열었다. 돌려주는 투영에서 `task_id`·`assignment_state` 를 비우는 것은
    그 업무를 지웠기 때문이다.
    """
    created = client.post("/api/work-requests", headers=headers, json=body)
    assert created.status_code == 201, created.text
    request = created.json()
    make_request_look_pending(database_url, request["request_id"])
    return {**request, "state": "pending", "task_id": None, "assignment_state": None}


def make_assignment_look_pending(database_url: str, assignment_id: str | UUID) -> None:
    """방금 선 관리자 배정을 **예전 배포가 남긴 수락 대기 배정**으로 되돌린다.

    수락 회차를 여는 코드(`_open_assignment_acceptance`)는 담당자 변경이 계속 쓰므로 그대로 있다 —
    여기서는 그것을 불러 과거 모양을 세운다. 신규 생성 경로가 이 회차를 만들지 않는 것이 W1 의 계약이다.
    """
    from ax_workspace.platform.work_tasks import SqlAlchemyTaskAssignmentRepository

    with make_session_factory(database_url)() as session:
        assignment = session.get(TaskAssignmentRecord, UUID(str(assignment_id)))
        assert assignment is not None
        assignment.status = "pending"
        assignment.accepted_at = None
        task = session.get(TaskRecord, assignment.task_id)
        SqlAlchemyTaskAssignmentRepository(session)._open_assignment_acceptance(
            task, assignment, datetime.now(UTC)
        )
        session.commit()


def pending_assignment(client, database_url: str, headers: dict[str, str], **body: Any) -> dict[str, Any]:
    """과거 모양의 관리자 배정 하나 — 만들고 나서 그 시절의 수락 대기 회차를 세운다."""
    created = client.post("/api/tasks/assign", headers=headers, json=body)
    assert created.status_code == 201, created.text
    assignment = created.json()
    make_assignment_look_pending(database_url, assignment["assignment_id"])
    return {**assignment, "status": "pending"}
