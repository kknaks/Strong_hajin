"""프로젝트 — 부서를 가로질러 묶이는 일과, 그 일을 함께 하는 사람들.

조직 단위가 사람이 어디에 속하는지를 말한다면, 프로젝트는 사람들이 무엇을 함께 하는지를 말한다. 둘은 나란히 선다:
프로젝트에 붙는다고 조직 안에서 갖던 것이 줄지 않고, 프로젝트 밖의 일이 열리지도 않는다.

소유 조직 단위는 이 프로젝트가 누구 책임인지를 말할 뿐 참여 자격을 제한하지 않는다. 마케팅 한 건에 국내사업부
AE와 비주얼디자인팀 디자이너가 함께 붙는 것이 정상이며, 그것이 이 모듈이 있는 이유다.

이 모듈은 권한을 스스로 만들지 않는다. 배정이 무슨 권한을 부르는지는 조직·권한 모듈의 표준 규칙이 답하며,
여기서는 배정이라는 사실만 기록한다.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Protocol
from uuid import UUID

from ax_workspace.modules.organization_access.domain import (
    PROJECT_MANAGE,
    PROJECT_READ,
    TASK_ASSIGN,
    Principal,
)


class ProjectError(Exception):
    pass


class ProjectNotFound(ProjectError):
    pass


class ProjectAccessDenied(ProjectError):
    pass


class ProjectRepository(Protocol):
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
    ) -> Any: ...
    def project(self, project_id: UUID) -> Any | None: ...
    def by_external_key(self, external_key: str) -> Any | None: ...
    def all_projects(self) -> list[Any]: ...
    def assignments_for(self, project_id: UUID) -> list[Any]: ...
    def assignment(self, project_id: UUID, member_id: str) -> Any | None: ...
    def add_assignment(self, *, project_id: UUID, member_id: str, kind: str, valid_from: datetime | None, valid_until: datetime | None, assigned_by: str) -> Any: ...
    def remove_assignment(self, assignment: Any) -> None: ...
    def member_projects(self, member_id: str) -> list[Any]: ...
    def tasks_in(self, project_id: UUID) -> list[Any]: ...
    def member_names(self, member_ids: list[str]) -> dict[str, str]: ...
    def unit_names(self) -> dict[str, str]: ...


ASSIGNMENT_KINDS = ("lead", "member")


class ProjectApplication:
    """프로젝트를 만들고, 사람을 붙이고, 읽는다. 읽을 수 있는 범위는 언제나 grant가 답한다."""

    def __init__(self, repository: ProjectRepository) -> None:
        self._repository = repository

    # ---- commands ----

    def create(
        self,
        principal: Principal,
        *,
        name: str,
        organization_unit_id: str,
        description: str | None = None,
        starts_on: date | None = None,
        ends_on: date | None = None,
        external_key: str | None = None,
    ) -> dict[str, Any]:
        """프로젝트 하나. 만드는 권한은 그 프로젝트를 소유할 조직 단위에서 나온다."""
        if not principal.allows(PROJECT_MANAGE, unit=organization_unit_id):
            raise ProjectAccessDenied("이 조직에서 프로젝트를 만들 수 있는 자격이 없습니다")
        cleaned = " ".join(str(name or "").split())
        if not cleaned:
            raise ProjectError("프로젝트 이름이 필요합니다")
        if starts_on and ends_on and ends_on < starts_on:
            raise ProjectError("끝나는 날이 시작하는 날보다 앞설 수 없습니다")
        if external_key and self._repository.by_external_key(external_key) is not None:
            raise ProjectError("이미 있는 프로젝트 key입니다")
        project = self._repository.create(
            name=cleaned,
            description=(description or "").strip() or None,
            organization_unit_id=organization_unit_id,
            starts_on=starts_on,
            ends_on=ends_on,
            external_key=external_key,
            created_by=str(principal.id),
        )
        return self._view(project)

    def assign(
        self,
        principal: Principal,
        project_id: UUID,
        member_id: str,
        *,
        kind: str = "member",
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
    ) -> dict[str, Any]:
        """사람을 붙인다. 그 사람이 어느 부서인지는 묻지 않는다 — 그것이 프로젝트가 있는 이유다."""
        project = self._manageable(principal, project_id)
        if kind not in ASSIGNMENT_KINDS:
            raise ProjectError("배정 종류는 담당 또는 참여입니다")
        if valid_from and valid_until and valid_until < valid_from:
            raise ProjectError("끝나는 날이 시작하는 날보다 앞설 수 없습니다")
        existing = self._repository.assignment(project.id, member_id)
        if existing is not None:
            return self._assignment_view(existing)
        assignment = self._repository.add_assignment(
            project_id=project.id,
            member_id=member_id,
            kind=kind,
            valid_from=valid_from,
            valid_until=valid_until,
            assigned_by=str(principal.id),
        )
        return self._assignment_view(assignment)

    def release(self, principal: Principal, project_id: UUID, member_id: str) -> None:
        """사람을 뗀다. 그와 함께 그 프로젝트로 얻었던 권한도 끝난다."""
        project = self._manageable(principal, project_id)
        assignment = self._repository.assignment(project.id, member_id)
        if assignment is None:
            raise ProjectNotFound("이 프로젝트에 배정된 구성원이 아닙니다")
        self._repository.remove_assignment(assignment)

    # ---- queries ----

    def list(self, principal: Principal) -> list[dict[str, Any]]:
        """이 사람이 읽을 수 있는 프로젝트만. 읽을 수 없는 프로젝트는 개수로도 드러나지 않는다."""
        reach = self._readable(principal)
        units = self._repository.unit_names()
        return [
            {**self._view(project), "organization_unit_name": units.get(project.organization_unit_id)}
            for project in self._repository.all_projects()
            if str(project.id) in reach
        ]

    def get(self, principal: Principal, project_id: UUID) -> dict[str, Any]:
        project = self._readable_project(principal, project_id)
        assignments = self._repository.assignments_for(project.id)
        names = self._repository.member_names([row.member_id for row in assignments])
        units = self._repository.unit_names()
        tasks = self._repository.tasks_in(project.id)
        return {
            **self._view(project),
            "organization_unit_name": units.get(project.organization_unit_id),
            # 무엇을 할 수 있는지는 서버가 말한다. 화면이 권한을 추측해 버튼을 그리면 눌러야 아는 거절이 된다.
            "may_manage": principal.allows(PROJECT_MANAGE, unit=project.organization_unit_id, project=str(project.id)),
            "members": [
                {**self._assignment_view(row), "display_name": names.get(row.member_id, row.member_id)}
                for row in assignments
            ],
            "tasks": [
                {
                    "task_id": str(task.id),
                    "title": task.title,
                    "state": task.state,
                    "start_date": task.start_date.isoformat() if task.start_date else None,
                    "due_date": task.due_date.isoformat() if task.due_date else None,
                    "parent_task_id": str(task.parent_task_id) if task.parent_task_id else None,
                }
                for task in tasks
            ],
        }

    def readable_project_ids(self, principal: Principal) -> frozenset[str]:
        """업무 읽기가 물어보는 것: 이 사람의 프로젝트 범위가 닿는 프로젝트들."""
        return self._readable(principal)

    def assignable_members(self, principal: Principal) -> list[dict[str, str]]:
        """이 사람이 배정할 수 있는 프로젝트의 사람들. 어느 부서인지는 묻지 않는다.

        배정 권한이 닿는 프로젝트에 함께 붙어 있는 사람들이며, 자기 자신은 빼고 돌려준다. 조직 축에서 오는
        후보와 합치는 일은 배정 모듈이 한 곳에서 한다 — 여기서는 프로젝트가 아는 것만 말한다.
        """
        reach = principal.projects_for(TASK_ASSIGN) if TASK_ASSIGN in principal.capabilities else frozenset()
        if not reach:
            return []
        found: dict[str, str] = {}
        for project_id in sorted(reach):
            for row in self._repository.assignments_for(UUID(project_id)):
                if row.member_id == str(principal.id) or row.member_id in found:
                    continue
                found[row.member_id] = ""
        names = self._repository.member_names(sorted(found))
        return [{"id": member_id, "display_name": names.get(member_id, member_id)} for member_id in sorted(found)]

    def may_assign_in(self, principal: Principal, project_id: UUID) -> bool:
        return principal.allows(TASK_ASSIGN, project=str(project_id))

    # ---- internals ----

    def _readable(self, principal: Principal) -> frozenset[str]:
        """읽을 수 있는 프로젝트 — 두 축 중 하나라도 닿으면 된다.

        붙어 있는 프로젝트(프로젝트 축)와, 자기 조직이 소유한 프로젝트(조직 축). 후자가 없으면 아무도 자기 팀의
        프로젝트에 일을 매달 수 없고, 누군가 배정해 줄 때까지 그 프로젝트는 존재하지 않는 것이 된다.
        """
        if PROJECT_READ not in principal.capabilities:
            return frozenset()
        joined = principal.projects_for(PROJECT_READ)
        units = principal.scope_for(PROJECT_READ)
        owned = {
            str(project.id)
            for project in self._repository.all_projects()
            if project.organization_unit_id in units
        }
        return frozenset(joined | owned)

    def _readable_project(self, principal: Principal, project_id: UUID) -> Any:
        project = self._repository.project(project_id)
        # 읽을 수 없는 프로젝트는 없는 것과 같이 답한다. 있다는 사실 자체가 알려지지 않는다.
        if project is None or str(project.id) not in self._readable(principal):
            raise ProjectNotFound("프로젝트를 찾을 수 없습니다")
        return project

    def _manageable(self, principal: Principal, project_id: UUID) -> Any:
        project = self._repository.project(project_id)
        if project is None:
            raise ProjectNotFound("프로젝트를 찾을 수 없습니다")
        if not principal.allows(PROJECT_MANAGE, unit=project.organization_unit_id, project=str(project.id)):
            raise ProjectAccessDenied("이 프로젝트를 관리할 수 있는 자격이 없습니다")
        return project

    @staticmethod
    def _view(project: Any) -> dict[str, Any]:
        return {
            "project_id": str(project.id),
            "name": project.name,
            "description": project.description,
            "organization_unit_id": project.organization_unit_id,
            "state": project.state,
            "starts_on": project.starts_on.isoformat() if project.starts_on else None,
            "ends_on": project.ends_on.isoformat() if project.ends_on else None,
            "external_key": project.external_key,
            "version": project.version,
        }

    @staticmethod
    def _assignment_view(assignment: Any) -> dict[str, Any]:
        return {
            "member_id": assignment.member_id,
            "assignment_kind": assignment.assignment_kind,
            # 유효기간은 비어 있을 수 있다. 비어 있으면 지금부터 계속이라는 뜻이다.
            "valid_from": assignment.valid_from.astimezone(UTC).isoformat() if assignment.valid_from else None,
            "valid_until": assignment.valid_until.astimezone(UTC).isoformat() if assignment.valid_until else None,
        }
