"""Composition root for the local workflow application."""
from __future__ import annotations

from typing import Any, Callable, TypeVar
from uuid import UUID

from ax_workspace.bootstrap.settings import Settings
from ax_workspace.modules.ax_execution.application import AccessDenied, WorkflowRunStarter
from ax_workspace.modules.organization_access.domain import Principal
from ax_workspace.modules.work.application import TaskApplication, TaskState
from ax_workspace.platform.persistence import make_session_factory
from ax_workspace.platform.work_tasks import SqlAlchemyTaskRepository
from ax_workspace.platform.workflow_runtime import SqlAlchemyUnitOfWork, workflow_service


T = TypeVar("T")


class WorkflowApplication:
    """Transaction boundary shared by HTTP, MCP, and local rehearsal adapters."""

    def __init__(self, settings: Settings) -> None:
        self._session_factory = make_session_factory(settings.database_url)

    def _use(self, operation: Callable[[WorkflowRunStarter], T]) -> T:
        with SqlAlchemyUnitOfWork(self._session_factory) as uow:
            assert uow.workflows is not None
            return operation(workflow_service(uow.workflows))

    def execute(self, operation: Callable[[WorkflowRunStarter], T]) -> T:
        """Run a local composition-owned operation in one application transaction."""
        return self._use(operation)

    def start(self, workflow_id: str, principal: Principal, input_data: dict[str, Any]) -> dict[str, Any]:
        return self._use(lambda service: service.start(workflow_id, principal, input_data))

    def run(self, run_id: UUID, principal: Principal) -> dict[str, Any]:
        def operation(service: WorkflowRunStarter) -> dict[str, Any]:
            result = service.summary(run_id)
            if not service.can_view(run_id, principal):
                raise AccessDenied("Principal cannot view this workflow run")
            return result
        return self._use(operation)

    def inbox(self, principal: Principal) -> list[dict[str, Any]]:
        return self._use(lambda service: service.inbox(principal))

    def meeting_assignment_candidates(self, principal: Principal) -> list[dict[str, str]]:
        return self._use(lambda service: service.meeting_assignment_candidates(principal))

    def decide(self, run_id: UUID, node_id: str, principal: Principal, decision: str, rationale: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        return self._use(lambda service: service.decide(run_id, node_id, principal, decision, rationale, payload))

    def my_work(self, principal: Principal) -> list[dict[str, Any]]:
        workflow_work = self._use(lambda service: service.my_work(principal))
        with self._session_factory() as session:
            direct_work = TaskApplication(SqlAlchemyTaskRepository(session)).list_for(principal)
        return [*direct_work, *workflow_work]

    def create_self_task(self, principal: Principal, title: str) -> dict[str, Any]:
        with self._session_factory() as session:
            result = TaskApplication(SqlAlchemyTaskRepository(session)).create_self(principal, title)
            session.commit()
            return result

    def transition_task(self, task_id: UUID, principal: Principal, target: TaskState, reason: str | None = None) -> dict[str, Any]:
        with self._session_factory() as session:
            result = TaskApplication(SqlAlchemyTaskRepository(session)).transition(task_id, principal, target, reason)
            session.commit()
            return result


def create_workflow_application(settings: Settings) -> WorkflowApplication:
    return WorkflowApplication(settings)
