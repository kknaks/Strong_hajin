"""시간 배정의 조회·쓰기 (SPEC-004 §4 · WORK-004 Phase BE-1).

**조회는 기본으로 살아 있는 행만** 낸다 — 참고 연결(`work_tasks.py` `references_for`)이 세운 결
그대로다. 닫힌 배정은 응답에 실리지 않으므로 외부에서는 존재를 알 수 없고, 이력 조회가 필요해지면
그때 `include_released=True` 로 연다.

**하루 한 칸은 여기서 답하지 않는다.** `uq_task_schedules_active` 가 답한다 — 이 파일의 `create` 가
먼저 묻는 것은 사람에게 이유를 말하기 위한 것이고, 동시 두 명령을 가르는 것은 데이터베이스다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ax_workspace.platform.persistence import TaskScheduleRecord


class SqlAlchemyTaskScheduleRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ---- 읽기 ----------------------------------------------------------------

    def active_on(self, task_id: UUID, on_date: date, *, lock: bool = False) -> TaskScheduleRecord | None:
        """그 업무의 그 날에 **살아 있는** 배정. 없으면 `None` — 닫힌 행은 없는 것과 같다."""
        statement = select(TaskScheduleRecord).where(
            TaskScheduleRecord.task_id == task_id,
            TaskScheduleRecord.on_date == on_date,
            TaskScheduleRecord.released_at.is_(None),
        )
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(statement)

    def schedule(self, schedule_id: UUID, *, lock: bool = False) -> TaskScheduleRecord | None:
        """단건. **닫힌 배정은 존재를 숨긴다** — 시각 변경을 부를 수 없는 자리다 (§4 Case Matrix)."""
        statement = select(TaskScheduleRecord).where(
            TaskScheduleRecord.id == schedule_id, TaskScheduleRecord.released_at.is_(None)
        )
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(statement)

    def in_range(self, task_ids: list[UUID], span_from: date, span_to: date) -> dict[UUID, list[TaskScheduleRecord]]:
        """합본 조회가 한 번에 묻는 자리 — 업무 줄마다 같은 질의를 반복하지 않는다."""
        if not task_ids:
            return {}
        rows = self.session.scalars(
            select(TaskScheduleRecord)
            .where(
                TaskScheduleRecord.task_id.in_(task_ids),
                TaskScheduleRecord.released_at.is_(None),
                TaskScheduleRecord.on_date >= span_from,
                TaskScheduleRecord.on_date <= span_to,
            )
            .order_by(TaskScheduleRecord.on_date, TaskScheduleRecord.starts_at, TaskScheduleRecord.id)
        )
        grouped: dict[UUID, list[TaskScheduleRecord]] = {}
        for row in rows:
            grouped.setdefault(row.task_id, []).append(row)
        return grouped

    def active_for(self, task_id: UUID) -> list[TaskScheduleRecord]:
        """그 업무의 살아 있는 배정 전부. 기간이 바뀔 때 무엇을 닫을지 세는 자리가 읽는다."""
        return list(
            self.session.scalars(
                select(TaskScheduleRecord)
                .where(TaskScheduleRecord.task_id == task_id, TaskScheduleRecord.released_at.is_(None))
                .order_by(TaskScheduleRecord.on_date)
            )
        )

    # ---- 쓰기 ----------------------------------------------------------------

    def create(self, task_id: UUID, on_date: date, starts_at: time, ends_at: time) -> TaskScheduleRecord:
        now = datetime.now(UTC)
        record = TaskScheduleRecord(
            task_id=task_id,
            on_date=on_date,
            starts_at=starts_at,
            ends_at=ends_at,
            version=1,
            created_at=now,
            updated_at=now,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def retime(self, schedule: TaskScheduleRecord, starts_at: time, ends_at: time) -> TaskScheduleRecord:
        """시각만 바꾼다 — **날짜는 옮기지 않는다** (§2.3 R6). 회차는 이 행의 것이 오른다 (증보 K8)."""
        schedule.starts_at = starts_at
        schedule.ends_at = ends_at
        schedule.version += 1
        schedule.updated_at = datetime.now(UTC)
        self.session.flush()
        return schedule

    def release(self, schedule: TaskScheduleRecord, reason: str) -> None:
        """닫는다. **되살아나는 전이가 없다** — 행위자를 남기지 않고 사유만 남긴다 (DEC-003 §J)."""
        schedule.released_at = datetime.now(UTC)
        schedule.released_reason = reason
        schedule.updated_at = schedule.released_at
        self.session.flush()
