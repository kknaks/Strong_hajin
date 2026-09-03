"""Composition root for the local workflow application."""
from __future__ import annotations

from typing import Any, Callable, TypeVar
from uuid import UUID

from ax_workspace.bootstrap.settings import Settings
from ax_workspace.modules.ax_execution.application import AccessDenied, WorkflowRunStarter
from ax_workspace.modules.organization_access.domain import Principal
from ax_workspace.modules.organization_access.application import OrganizationApplication
from ax_workspace.platform.organization_access import SqlAlchemyOrganizationRepository
from ax_workspace.modules.reports.application import DailyReportApplication
from ax_workspace.modules.ax_execution.ai import AiProvider, ProviderFailure
from ax_workspace.platform.codex_cli import CodexCliProviderAdapter
from ax_workspace.platform.reports import SqlAlchemyDailyReportDraftWorkflow, SqlAlchemyDailyReportRepository
from ax_workspace.modules.work.application import TaskApplication, TaskState
from ax_workspace.platform.persistence import make_session_factory
from ax_workspace.platform.work_tasks import SqlAlchemyTaskRepository, SqlAlchemyWorkRecordSource
from ax_workspace.platform.workflow_runtime import SqlAlchemyUnitOfWork, workflow_service


T = TypeVar("T")


class WorkflowApplication:
    """Transaction boundary shared by HTTP, MCP, and local rehearsal adapters."""

    def __init__(self, settings: Settings, report_provider: AiProvider | None = None) -> None:
        self._session_factory = make_session_factory(settings.database_url)
        self._report_provider = report_provider or CodexCliProviderAdapter()

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

    def my_organization_profile(self, principal: Principal) -> dict[str, Any]:
        with self._session_factory() as session:
            return OrganizationApplication(SqlAlchemyOrganizationRepository(session)).my_profile(principal)

    def authenticated_principal(self, persona_id: str) -> Principal:
        with self._session_factory() as session:
            return OrganizationApplication(SqlAlchemyOrganizationRepository(session)).authenticated_principal(persona_id)

    def generate_daily_report_draft(self, principal: Principal, report_date: str) -> dict[str, Any]:
        with self._session_factory() as session:
            try:
                result = self._reports(session).generate_draft(principal, report_date)
                session.commit()
                return result
            except ProviderFailure:
                # A failed live call is operational evidence, not a synthetic success.
                session.commit()
                raise

    def edit_daily_report(
        self,
        principal: Principal,
        report_id: str,
        draft_id: str,
        expected_version: int,
        body: str,
        include_source_refs: list[dict[str, Any]],
        exclude_source_refs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._reports(session).edit(
                principal,
                report_id,
                draft_id,
                expected_version,
                body,
                include_source_refs,
                exclude_source_refs,
            )
            session.commit()
            return result

    def submit_daily_report(
        self,
        principal: Principal,
        report_id: str,
        draft_id: str,
        expected_version: int,
        reason: str | None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._reports(session).submit(
                principal, report_id, draft_id, expected_version, reason
            )
            session.commit()
            return result

    def daily_report_history(self, principal: Principal, report_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            return self._reports(session).history(principal, report_id)

    def _reports(self, session: Any) -> DailyReportApplication:
        return DailyReportApplication(
            SqlAlchemyDailyReportRepository(session),
            SqlAlchemyDailyReportDraftWorkflow(
                session,
                SqlAlchemyWorkRecordSource(session),
                self._report_provider,
            ),
        )

    def create_self_task(self, principal: Principal, title: str) -> dict[str, Any]:
        with self._session_factory() as session:
            result = TaskApplication(SqlAlchemyTaskRepository(session)).create_self(principal, title)
            session.commit()
            return result

    def transition_task(self, task_id: UUID, principal: Principal, target: TaskState, reason: str | None = None, expected_version: int = 0) -> dict[str, Any]:
        with self._session_factory() as session:
            result = TaskApplication(SqlAlchemyTaskRepository(session)).transition(task_id, principal, target, reason, expected_version)
            session.commit()
            return result


def create_workflow_application(
    settings: Settings,
    report_provider: AiProvider | None = None,
) -> WorkflowApplication:
    return WorkflowApplication(settings, report_provider)
