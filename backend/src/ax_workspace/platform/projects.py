"""프로젝트 원장의 SQLAlchemy 어댑터, 그리고 배정이 부르는 표준 권한.

배정이 무슨 권한을 부르는지는 여기서 정하지 않는다 — 보직이 역할을 부르는 것과 똑같이 STANDARD_GRANT_RULE이
답하고, 그 규칙이 만든 ACCESS_GRANT가 어디까지 닿는지를 말한다. 프로젝트 참여에서 권한을 암묵적으로 추론하는
두 번째 경로를 만들지 않기 위해서다.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ax_workspace.modules.organization_access.catalog import ROLE_TEMPLATES_BY_KEY
from ax_workspace.platform.persistence import (
    AccessGrantRecord,
    MemberRecord,
    OrganizationUnitRecord,
    ProjectAssignmentRecord,
    ProjectRecord,
    RoleRecord,
    StandardGrantRuleRecord,
    TaskRecord,
)

#: 프로젝트에 붙은 사람이 그 프로젝트에서 갖는 역할. 조직 안에서 갖던 것은 그대로 두고 여기에 더해진다.
PROJECT_ROLE_KEY = "project-participant"


class SqlAlchemyProjectRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        name: str,
        description: str | None,
        organization_unit_id: str,
        starts_on: date | None,
        ends_on: date | None,
        external_key: str | None,
        created_by: str,
    ) -> ProjectRecord:
        project = ProjectRecord(
            name=name,
            description=description,
            organization_unit_id=organization_unit_id,
            starts_on=starts_on,
            ends_on=ends_on,
            external_key=external_key,
            created_by_actor_id=created_by,
        )
        self._session.add(project)
        self._session.flush()
        return project

    def project(self, project_id: UUID) -> ProjectRecord | None:
        return self._session.get(ProjectRecord, project_id)

    def by_external_key(self, external_key: str) -> ProjectRecord | None:
        return self._session.scalar(select(ProjectRecord).where(ProjectRecord.external_key == external_key))

    def all_projects(self) -> list[ProjectRecord]:
        return list(self._session.scalars(select(ProjectRecord).order_by(ProjectRecord.created_at)))

    def assignments_for(self, project_id: UUID) -> list[ProjectAssignmentRecord]:
        return list(
            self._session.scalars(
                select(ProjectAssignmentRecord)
                .where(ProjectAssignmentRecord.project_id == project_id)
                .order_by(ProjectAssignmentRecord.assignment_kind, ProjectAssignmentRecord.member_id)
            )
        )

    def assignment(self, project_id: UUID, member_id: str) -> ProjectAssignmentRecord | None:
        return self._session.scalar(
            select(ProjectAssignmentRecord).where(
                ProjectAssignmentRecord.project_id == project_id,
                ProjectAssignmentRecord.member_id == member_id,
            )
        )

    def add_assignment(
        self,
        *,
        project_id: UUID,
        member_id: str,
        kind: str,
        valid_from: datetime | None,
        valid_until: datetime | None,
        assigned_by: str,
    ) -> ProjectAssignmentRecord:
        assignment = ProjectAssignmentRecord(
            project_id=project_id,
            member_id=member_id,
            assignment_kind=kind,
            valid_from=valid_from,
            valid_until=valid_until,
            assigned_by_member_id=assigned_by,
        )
        self._session.add(assignment)
        self._session.flush()
        grant_project_access(self._session, project_id=project_id, member_id=member_id, granted_by=assigned_by)
        return assignment

    def remove_assignment(self, assignment: ProjectAssignmentRecord) -> None:
        revoke_project_access(self._session, project_id=assignment.project_id, member_id=assignment.member_id)
        self._session.delete(assignment)
        self._session.flush()

    def member_projects(self, member_id: str) -> list[ProjectRecord]:
        return list(
            self._session.scalars(
                select(ProjectRecord)
                .join(ProjectAssignmentRecord, ProjectAssignmentRecord.project_id == ProjectRecord.id)
                .where(ProjectAssignmentRecord.member_id == member_id)
                .order_by(ProjectRecord.created_at)
            )
        )

    def tasks_in(self, project_id: UUID) -> list[TaskRecord]:
        return list(
            self._session.scalars(
                select(TaskRecord).where(TaskRecord.project_id == project_id).order_by(TaskRecord.created_at)
            )
        )

    def member_names(self, member_ids: list[str]) -> dict[str, str]:
        if not member_ids:
            return {}
        rows = self._session.execute(
            select(MemberRecord.id, MemberRecord.display_name).where(MemberRecord.id.in_(member_ids))
        ).all()
        return {str(member_id): str(name) for member_id, name in rows}

    def unit_names(self) -> dict[str, str]:
        return {row.id: row.name for row in self._session.scalars(select(OrganizationUnitRecord))}


def _project_rule(session: Session) -> str:
    """ERD STANDARD_GRANT_RULE — 프로젝트에 배정되면 그 프로젝트 범위의 역할이 따라온다."""
    template = ROLE_TEMPLATES_BY_KEY[PROJECT_ROLE_KEY]
    if session.get(RoleRecord, template.role_id) is None:
        # 역할이 아직 설치되지 않았다면 배정만으로 권한을 지어내지 않는다.
        raise LookupError(f"{template.role_id} 역할이 설치되지 않았습니다")
    rule_id = f"standard:project_assignment:project:{template.role_id}"
    if session.get(StandardGrantRuleRecord, rule_id) is None:
        session.add(
            StandardGrantRuleRecord(
                id=rule_id,
                trigger_kind="project_assignment",
                trigger_source_ref="project",
                role_id=template.role_id,
                scope_template="project",
            )
        )
        session.flush()
    return rule_id


def grant_project_access(session: Session, *, project_id: UUID, member_id: str, granted_by: str | None) -> None:
    """배정이 만든 권한. 조직 단위 grant와 나란히 서고 서로를 대신하지 않는다."""
    template = ROLE_TEMPLATES_BY_KEY[PROJECT_ROLE_KEY]
    scope_ref = str(project_id)
    existing = session.scalar(
        select(AccessGrantRecord).where(
            AccessGrantRecord.member_id == member_id,
            AccessGrantRecord.role_id == template.role_id,
            AccessGrantRecord.scope_ref == scope_ref,
            AccessGrantRecord.revoked_at.is_(None),
        )
    )
    if existing is not None:
        return
    session.add(
        AccessGrantRecord(
            member_id=member_id,
            role_id=template.role_id,
            role_capability_version=template.version,
            scope_kind="project",
            # 프로젝트는 조직 단위가 아니다. 조직 열에 프로젝트 id를 넣지 않는다.
            scope_organization_id=None,
            scope_ref=scope_ref,
            include_descendants=False,
            granted_by_member_id=granted_by,
            origin_rule_id=_project_rule(session),
            origin_rule_version=1,
        )
    )
    session.flush()


def revoke_project_access(session: Session, *, project_id: UUID, member_id: str) -> None:
    """프로젝트에서 빠지면 그 프로젝트로 얻었던 권한도 끝난다. 조직 안에서 갖던 것은 건드리지 않는다."""
    template = ROLE_TEMPLATES_BY_KEY[PROJECT_ROLE_KEY]
    now = datetime.now(UTC)
    for grant in session.scalars(
        select(AccessGrantRecord).where(
            AccessGrantRecord.member_id == member_id,
            AccessGrantRecord.role_id == template.role_id,
            AccessGrantRecord.scope_ref == str(project_id),
            AccessGrantRecord.revoked_at.is_(None),
        )
    ):
        grant.revoked_at = now
    session.flush()


def readable_project_ids(session: Session, member_id: str) -> frozenset[str]:
    """이 사람의 프로젝트 범위가 지금 닿는 프로젝트들. 만료·회수된 grant는 세지 않는다."""
    now = datetime.now(UTC)
    rows = session.scalars(
        select(AccessGrantRecord.scope_ref).where(
            AccessGrantRecord.member_id == member_id,
            AccessGrantRecord.scope_kind == "project",
            AccessGrantRecord.revoked_at.is_(None),
            AccessGrantRecord.valid_from <= now,
            or_(AccessGrantRecord.valid_until.is_(None), AccessGrantRecord.valid_until > now),
        )
    )
    return frozenset(str(ref) for ref in rows if ref)


def project_ids_for_tasks(session: Session, task_ids: list[UUID]) -> dict[UUID, str]:
    if not task_ids:
        return {}
    rows = session.execute(
        select(TaskRecord.id, TaskRecord.project_id).where(TaskRecord.id.in_(task_ids), TaskRecord.project_id.is_not(None))
    ).all()
    return {task_id: str(project_id) for task_id, project_id in rows}


__all__ = [
    "PROJECT_ROLE_KEY",
    "SqlAlchemyProjectRepository",
    "grant_project_access",
    "project_ids_for_tasks",
    "readable_project_ids",
    "revoke_project_access",
]
