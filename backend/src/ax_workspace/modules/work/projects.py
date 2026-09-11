"""프로젝트 — 부서를 가로질러 묶이는 일과, 그 일을 함께 하는 사람들.

조직 단위가 사람이 어디에 속하는지를 말한다면, 프로젝트는 사람들이 무엇을 함께 하는지를 말한다. 둘은 나란히 선다:
프로젝트에 붙는다고 조직 안에서 갖던 것이 줄지 않고, 프로젝트 밖의 일이 열리지도 않는다.

소유 조직 단위를 두지 않는다. 마케팅 한 건에 국내사업부 AE와 비주얼디자인팀 디자이너가 함께 붙는 것이 정상이고,
그것이 이 모듈이 있는 이유다 — 어느 한 부서의 것이라고 적으면 그 부서가 프로젝트를 여는 열쇠가 된다.

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
        starts_on: date | None,
        ends_on: date | None,
        external_key: str | None,
        created_by: str,
    ) -> Any: ...
    def project(self, project_id: UUID) -> Any | None: ...
    def by_external_key(self, external_key: str) -> Any | None: ...
    def all_projects(self) -> list[Any]: ...
    def assignments_for(self, project_id: UUID) -> list[Any]: ...
    def assignment_history(self, project_id: UUID) -> list[Any]: ...
    def assignment(self, project_id: UUID, member_id: str, *, lock: bool = False) -> Any | None: ...
    def assignment_round(
        self,
        project_id: UUID,
        member_id: str,
        assignment_id: UUID,
        *,
        lock: bool = False,
    ) -> Any | None: ...
    def add_assignment(self, *, project_id: UUID, member_id: str, kind: str, valid_from: datetime | None, valid_until: datetime | None, assigned_by: str) -> Any: ...
    def end_assignment(self, assignment: Any, *, ended_by: str, reason: str | None) -> None: ...
    def member_projects(self, member_id: str) -> list[Any]: ...
    def tasks_in(self, project_id: UUID) -> list[Any]: ...
    def member_names(self, member_ids: list[str]) -> dict[str, str]: ...


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
        description: str | None = None,
        starts_on: date | None = None,
        ends_on: date | None = None,
        external_key: str | None = None,
    ) -> dict[str, Any]:
        """프로젝트 하나. 만들고, 사람을 붙인다 — 그 둘이 프로젝트의 전부다.

        소유 조직을 두지 않는다. 프로젝트는 부서를 가로질러 묶이려고 있는 것이라 어느 한 부서의 것이라고
        말하는 순간 그 부서가 열쇠가 되고, 붙어야 보인다는 규칙에 뒷문이 생긴다.

        만든 사람은 담당자로 함께 기록된다. 그러지 않으면 만든 사람조차 자기 프로젝트를 찾지 못해 아무도
        붙일 수 없고, 프로젝트는 만들어지자마자 고아가 된다.
        """
        if PROJECT_MANAGE not in principal.capabilities:
            raise ProjectAccessDenied("프로젝트를 만들 수 있는 자격이 없습니다")
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
            starts_on=starts_on,
            ends_on=ends_on,
            external_key=external_key,
            created_by=str(principal.id),
        )
        self._repository.add_assignment(
            project_id=project.id,
            member_id=str(principal.id),
            kind="lead",
            valid_from=None,
            valid_until=None,
            assigned_by=str(principal.id),
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
        existing = self._repository.assignment(project.id, member_id, lock=True)
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

    def release(
        self,
        principal: Principal,
        project_id: UUID,
        member_id: str,
        *,
        assignment_id: UUID | None = None,
        reason: str | None = None,
    ) -> None:
        """사람을 뗀다. 그와 함께 그 프로젝트로 얻었던 권한도 끝난다."""
        project = self._manageable(principal, project_id)
        assignment = (
            self._repository.assignment_round(project.id, member_id, assignment_id, lock=True)
            if assignment_id is not None
            else self._repository.assignment(project.id, member_id, lock=True)
        )
        if assignment is None:
            raise ProjectNotFound("이 프로젝트에 배정된 구성원이 아닙니다")
        # 회차를 지정한 종료 요청은 그 회차의 receipt다. 종료 뒤 재전송되어도 새 회차를 대신 끝내지 않는다.
        if assignment.ended_at is not None:
            return
        self._repository.end_assignment(
            assignment,
            ended_by=str(principal.id),
            reason=(reason or "").strip() or None,
        )

    # ---- queries ----

    def list(self, principal: Principal) -> list[dict[str, Any]]:
        """이 사람이 읽을 수 있는 프로젝트만. 읽을 수 없는 프로젝트는 개수로도 드러나지 않는다."""
        reach = self._readable(principal)
        return [
            {**self._view(project)}
            for project in self._repository.all_projects()
            if str(project.id) in reach
        ]

    def get(self, principal: Principal, project_id: UUID) -> dict[str, Any]:
        project = self._readable_project(principal, project_id)
        assignments = self._repository.assignments_for(project.id)
        names = self._repository.member_names([row.member_id for row in assignments])
        tasks = self._repository.tasks_in(project.id)
        return {
            **self._view(project),
            # 무엇을 할 수 있는지는 서버가 말한다. 화면이 권한을 추측해 버튼을 그리면 눌러야 아는 거절이 된다.
            "may_manage": principal.allows(PROJECT_MANAGE, project=str(project.id)),
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

    def participation_history(self, principal: Principal, project_id: UUID) -> list[dict[str, Any]]:
        project = self._readable_project(principal, project_id)
        rows = self._repository.assignment_history(project.id)
        names = self._repository.member_names(
            sorted(
                {
                    member_id
                    for row in rows
                    for member_id in (row.member_id, row.assigned_by_member_id, row.ended_by_member_id)
                    if member_id
                }
            )
        )
        return [
            {
                **self._assignment_history_view(row),
                "display_name": names.get(row.member_id, row.member_id),
                "assigned_by_display_name": names.get(row.assigned_by_member_id) if row.assigned_by_member_id else None,
                "ended_by_display_name": names.get(row.ended_by_member_id) if row.ended_by_member_id else None,
            }
            for row in rows
        ]

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
        """읽을 수 있는 프로젝트 — 붙어 있는 것뿐이다.

        축이 하나면 뒷문이 없다. 부서로도, 만든 사람이라는 사실로도 열리지 않고, 배정이라는 한 가지 사실로만
        열린다. 조직 전체를 읽는 자격은 그 자격이 따로 말한다.
        """
        if PROJECT_READ not in principal.capabilities:
            return frozenset()
        return frozenset(principal.projects_for(PROJECT_READ))

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
        if not principal.allows(PROJECT_MANAGE, project=str(project.id)):
            raise ProjectAccessDenied("이 프로젝트를 관리할 수 있는 자격이 없습니다")
        return project

    @staticmethod
    def _view(project: Any) -> dict[str, Any]:
        return {
            "project_id": str(project.id),
            "name": project.name,
            "description": project.description,
            "state": project.state,
            "starts_on": project.starts_on.isoformat() if project.starts_on else None,
            "ends_on": project.ends_on.isoformat() if project.ends_on else None,
            "external_key": project.external_key,
            "version": project.version,
        }

    @staticmethod
    def _assignment_view(assignment: Any) -> dict[str, Any]:
        def utc(value: datetime | None) -> str | None:
            # SQLite returns stored UTC timestamps without tzinfo; never reinterpret
            # those wall-clock values in the host's local timezone.
            if value is None:
                return None
            return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC).isoformat()

        return {
            "assignment_id": str(assignment.id),
            "member_id": assignment.member_id,
            "assignment_kind": assignment.assignment_kind,
            # 유효기간은 비어 있을 수 있다. 비어 있으면 지금부터 계속이라는 뜻이다.
            "valid_from": utc(assignment.valid_from),
            "valid_until": utc(assignment.valid_until),
        }

    @classmethod
    def _assignment_history_view(cls, assignment: Any) -> dict[str, Any]:
        def utc(value: datetime | None) -> str | None:
            if value is None:
                return None
            return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC).isoformat()

        return {
            **cls._assignment_view(assignment),
            "assigned_by_member_id": assignment.assigned_by_member_id,
            "created_at": utc(assignment.created_at),
            "ended_at": utc(assignment.ended_at),
            "ended_by_member_id": assignment.ended_by_member_id,
            "end_reason": assignment.end_reason,
        }
