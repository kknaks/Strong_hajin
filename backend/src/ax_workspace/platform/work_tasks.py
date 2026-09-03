from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ax_workspace.modules.work.application import TaskNotFound, TaskState
from ax_workspace.platform.persistence import (
    TaskActivityRecord,
    TaskRecord,
    WorkRequestAuditEventRecord,
    WorkRequestRecord,
    WorkRequestTaskAssignmentRecord,
)


class SqlAlchemyTaskRepository:
    def __init__(self, session: Session) -> None: self.session = session

    def create_self_task(self, owner_id: str, title: str) -> TaskRecord:
        now = datetime.now(UTC)
        task = TaskRecord(owner_id=owner_id, title=title, state=TaskState.OPEN, block_reason=None, version=1, created_at=now, updated_at=now)
        self.session.add(task)
        self.session.flush()
        self.session.add(TaskActivityRecord(task_id=task.id, task_version=task.version, state=task.state, occurred_at=now))
        return task

    def task(self, task_id: UUID, owner_id: str, *, lock: bool = False) -> TaskRecord:
        statement = select(TaskRecord).where(TaskRecord.id == task_id, TaskRecord.owner_id == owner_id)
        task = self.session.scalar(statement.with_for_update() if lock else statement)
        if task is None: raise TaskNotFound("task was not found")
        return task

    def tasks_for(self, owner_id: str) -> list[TaskRecord]:
        return list(self.session.scalars(select(TaskRecord).where(TaskRecord.owner_id == owner_id).where(TaskRecord.state.not_in([TaskState.DONE, TaskState.CANCELLED])).order_by(TaskRecord.created_at)))

    def touch(self, task: TaskRecord) -> None:
        task.updated_at = datetime.now(UTC)
        self.session.add(TaskActivityRecord(task_id=task.id, task_version=task.version, state=task.state, occurred_at=task.updated_at))


class SqlAlchemyWorkRecordSource:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list(self, principal, report_date: str) -> list[dict[str, object]]:
        activities = self._session.scalars(
            select(TaskActivityRecord)
            .join(TaskRecord, TaskRecord.id == TaskActivityRecord.task_id)
            .where(TaskRecord.owner_id == str(principal.id))
            .order_by(TaskActivityRecord.occurred_at)
        )
        return [
            {
                "task_id": str(item.task_id),
                "task_version": item.task_version,
                "state": item.state,
                "occurred_at": item.occurred_at.isoformat(),
            }
            for item in activities
            if item.occurred_at.date().isoformat() == report_date
        ]


class SqlAlchemyWorkRequestRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_request(self, requester_id: str, assignee_id: str, title: str) -> WorkRequestRecord:
        now = datetime.now(UTC)
        request = WorkRequestRecord(
            requester_id=requester_id,
            assignee_id=assignee_id,
            title=title,
            state="pending",
            version=1,
            conditions=None,
            created_at=now,
            updated_at=now,
        )
        self._session.add(request)
        self._session.flush()
        return request

    def request(self, request_id: UUID, *, lock: bool = False) -> WorkRequestRecord | None:
        statement = select(WorkRequestRecord).where(WorkRequestRecord.id == request_id)
        return self._session.scalar(statement.with_for_update() if lock else statement)

    def create_accepted_task(self, request: WorkRequestRecord) -> TaskRecord:
        now = datetime.now(UTC)
        task = TaskRecord(
            owner_id=request.assignee_id,
            title=request.title,
            state=TaskState.OPEN,
            block_reason=None,
            version=1,
            created_at=now,
            updated_at=now,
        )
        self._session.add(task)
        self._session.flush()
        self._session.add(
            TaskActivityRecord(task_id=task.id, task_version=task.version, state=task.state, occurred_at=now)
        )
        self._session.add(
            WorkRequestTaskAssignmentRecord(
                request_id=request.id,
                task_id=task.id,
                assignee_id=request.assignee_id,
                state="active",
                accepted_at=now,
            )
        )
        return task

    def append_audit(self, request_id: UUID, actor_id: str, event_type: str, payload: dict) -> None:
        self._session.add(
            WorkRequestAuditEventRecord(
                request_id=request_id,
                actor_id=actor_id,
                event_type=event_type,
                payload=payload,
                occurred_at=datetime.now(UTC),
            )
        )

    def inbox_for(self, assignee_id: str) -> list[WorkRequestRecord]:
        return list(
            self._session.scalars(
                select(WorkRequestRecord)
                .where(
                    WorkRequestRecord.assignee_id == assignee_id,
                    WorkRequestRecord.state.in_(("pending", "negotiating")),
                )
                .order_by(WorkRequestRecord.created_at)
            )
        )
