"""AI 맥락 목록의 조회 — **그 순간 DB** 에서 조직 전체를 읽는다 (SPEC-010 §4.5 · `modules/ax_execution/context_catalog.py`).

조회 넷(프로젝트 · 열린 업무 · 활성 구성원 · 그 구성원의 주 소속과 주 보직)으로 끝난다 — 구성원마다 다시 묻지 않는다.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ax_workspace.modules.ax_execution.context_catalog import (
    AiContextCatalog,
    ContextMember,
    ContextProject,
    ContextTask,
)
from ax_workspace.platform.persistence import (
    AppointmentRecord,
    MemberRecord,
    MembershipRecord,
    OrganizationUnitRecord,
    PositionDefinitionRecord,
    ProjectRecord,
    TaskRecord,
)

#: 맥락에 싣지 않는 업무 상태 — 완료 · 취소(D-14 기본값). 나머지는 전부 「열린」 업무다.
CLOSED_TASK_STATES = ("done", "cancelled")


class SqlAlchemyAiContextCatalogSource:
    def __init__(self, session: Session, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._session = session
        self._clock = clock

    def load(self) -> AiContextCatalog:
        now = self._clock()
        projects = [
            ContextProject(id=str(row.id), name=row.name, status=row.state)
            for row in self._session.scalars(select(ProjectRecord).order_by(ProjectRecord.name, ProjectRecord.id))
        ]
        tasks = [
            ContextTask(
                id=str(row.id),
                title=row.title,
                project_id=str(row.project_id) if row.project_id else None,
                state=row.state,
                due_date=row.due_date,
            )
            for row in self._session.scalars(
                select(TaskRecord)
                .where(TaskRecord.state.not_in(CLOSED_TASK_STATES))
                .order_by(TaskRecord.created_at, TaskRecord.id)
            )
        ]
        units = {row.id: row.name for row in self._session.scalars(select(OrganizationUnitRecord))}
        current = or_(MembershipRecord.valid_until.is_(None), MembershipRecord.valid_until > now)
        primary_unit: dict[str, str] = {}
        for row in self._session.scalars(
            select(MembershipRecord).where(current).order_by(MembershipRecord.is_primary.desc(), MembershipRecord.valid_from)
        ):
            primary_unit.setdefault(row.member_id, units.get(row.organization_id, row.organization_id))
        positions: dict[str, str] = {}
        appointment_current = or_(AppointmentRecord.valid_until.is_(None), AppointmentRecord.valid_until > now)
        for member_id, kind, name in self._session.execute(
            select(AppointmentRecord.member_id, AppointmentRecord.appointment_kind, PositionDefinitionRecord.name)
            .join(PositionDefinitionRecord, PositionDefinitionRecord.id == AppointmentRecord.position_definition_id)
            .where(appointment_current)
            .order_by(AppointmentRecord.valid_from)
        ):
            # 주 보직(`primary`)이 먼저 — 겸직은 주 보직이 없을 때만 그 사람의 직책으로 쓴다.
            if kind == "primary" or member_id not in positions:
                positions[member_id] = name
        members = [
            ContextMember(
                id=row.id,
                name=row.display_name,
                unit=primary_unit.get(row.id),
                position=positions.get(row.id),
            )
            for row in self._session.scalars(
                select(MemberRecord).where(MemberRecord.record_status == "active").order_by(MemberRecord.display_name, MemberRecord.id)
            )
        ]
        return AiContextCatalog(at=now, projects=projects, tasks=tasks, members=members)
