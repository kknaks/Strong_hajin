"""Composition root for the local workflow application."""
from __future__ import annotations

from typing import Any, Callable, TypeVar
import sys
from uuid import UUID

from ax_workspace.bootstrap.settings import Settings
from ax_workspace.modules.ax_execution.application import AccessDenied, WorkflowRunStarter
from ax_workspace.modules.ax_execution.conversations import (
    ConversationApplication,
    ConversationContextReferenceInput,
)
from ax_workspace.modules.ax_execution.actions import ActionApplication
from ax_workspace.modules.organization_access.domain import Principal
from ax_workspace.modules.organization_access.application import OrganizationApplication
from ax_workspace.platform.organization_access import SqlAlchemyOrganizationRepository
from ax_workspace.modules.reports.application import DailyReportApplication
from ax_workspace.modules.ax_execution.ai import AiProvider, ProviderFailure
from ax_workspace.platform.codex_cli import CodexCliMcpServer, CodexCliProviderAdapter
from ax_workspace.platform.conversation_queue import (
    NullConversationExecutionQueue,
    PgmqConversationTurnQueue,
)
from ax_workspace.platform.conversations import (
    SqlAlchemyConversationContextResolver,
    SqlAlchemyConversationRepository,
)
from ax_workspace.platform.actions import SqlAlchemyActionExecutor, SqlAlchemyActionRepository
from ax_workspace.platform.reports import SqlAlchemyDailyReportDraftWorkflow, SqlAlchemyDailyReportRepository
from ax_workspace.modules.work.application import TaskAccessDenied, TaskApplication, TaskState
from ax_workspace.modules.work.requests import WorkRequestApplication
from ax_workspace.platform.persistence import make_session_factory
from ax_workspace.platform.work_tasks import (
    SqlAlchemyTaskRepository,
    SqlAlchemyWorkRecordSource,
    SqlAlchemyWorkRequestRepository,
)
from ax_workspace.platform.workflow_runtime import SqlAlchemyUnitOfWork, workflow_service


T = TypeVar("T")


class WorkflowApplication:
    """Transaction boundary shared by HTTP, MCP, and local rehearsal adapters."""

    def __init__(self, settings: Settings, report_provider: AiProvider | None = None) -> None:
        self._settings = settings
        self._session_factory = make_session_factory(settings.database_url)
        self._report_provider = report_provider or create_codex_cli_provider(settings)

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
            try:
                direct_work = TaskApplication(SqlAlchemyTaskRepository(session)).list_for(principal)
            except TaskAccessDenied:
                direct_work = []
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

    def create_self_task(
        self,
        principal: Principal,
        title: str,
        causation_key: str | None = None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = TaskApplication(SqlAlchemyTaskRepository(session)).create_self(
                principal, title, causation_key
            )
            session.commit()
            return result

    def list_tasks(self, principal: Principal) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return TaskApplication(SqlAlchemyTaskRepository(session)).list_for(principal)

    def get_task(self, principal: Principal, task_id: UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            return TaskApplication(SqlAlchemyTaskRepository(session)).get(principal, task_id)

    def create_work_request(
        self,
        principal: Principal,
        title: str,
        assignee_id: str,
        causation_key: str | None = None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._work_requests(session).create(
                principal, title, assignee_id, causation_key
            )
            session.commit()
            return result

    def work_request_assignee_candidates(self, principal: Principal) -> list[dict[str, str]]:
        with self._session_factory() as session:
            return self._work_requests(session).assignee_candidates(principal)

    def list_work_requests(self, principal: Principal) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return self._work_requests(session).list(principal)

    def get_work_request(self, principal: Principal, request_id: UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            return self._work_requests(session).get(principal, request_id)

    def accept_work_request(
        self, principal: Principal, request_id: UUID, expected_version: int
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._work_requests(session).accept(
                principal, request_id, expected_version
            )
            session.commit()
            return result

    def reject_work_request(
        self, principal: Principal, request_id: UUID, expected_version: int, reason: str
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._work_requests(session).reject(
                principal, request_id, expected_version, reason
            )
            session.commit()
            return result

    def negotiate_work_request(
        self, principal: Principal, request_id: UUID, expected_version: int, conditions: dict[str, Any]
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._work_requests(session).negotiate(
                principal, request_id, expected_version, conditions
            )
            session.commit()
            return result

    def work_request_inbox(self, principal: Principal) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return self._work_requests(session).inbox(principal)

    def create_conversation(self, principal: Principal, title: str) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._conversations(session).create(principal, title)
            session.commit()
            return result

    def conversations(self, principal: Principal) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return self._conversations(session).list(principal)

    def conversation(self, principal: Principal, conversation_id: UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            return self._conversations(session).get(principal, conversation_id)

    def accept_conversation_message(
        self,
        principal: Principal,
        body: str,
        conversation_id: UUID,
        context: list[Any],
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            references = [
                ConversationContextReferenceInput(
                    resource_type=item.resource_type,
                    resource_id=str(item.resource_id),
                    resource_version=item.resource_version,
                    included=item.included,
                )
                for item in context
            ]
            result = self._conversations(session).accept_message(
                principal,
                conversation_id,
                body,
                references,
                idempotency_key,
            )
            session.commit()
            return result

    def cancel_conversation_turn(
        self, principal: Principal, conversation_id: UUID, expected_version: int
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._conversations(session).cancel(principal, conversation_id, expected_version)
            session.commit()
            return result

    def propose_action(
        self,
        principal: Principal,
        execution_id: UUID,
        action_type: str,
        title: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._actions(session).propose(
                principal,
                execution_id,
                action_type,
                title,
                payload,
            )
            session.commit()
            return result

    def actions(self, principal: Principal) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return self._actions(session).list(principal)

    def decide_action(
        self,
        principal: Principal,
        action_id: UUID,
        expected_version: int,
        decision: str,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._actions(session).decide(
                principal,
                action_id,
                expected_version,
                decision,
            )
            session.commit()
            return result

    @staticmethod
    def _work_requests(session: Any) -> WorkRequestApplication:
        return WorkRequestApplication(
            SqlAlchemyWorkRequestRepository(session),
            OrganizationApplication(SqlAlchemyOrganizationRepository(session)),
        )

    def _conversations(self, session: Any) -> ConversationApplication:
        if self._settings.conversation_queue_backend == "pgmq":
            queue = PgmqConversationTurnQueue(session)
        elif self._settings.conversation_queue_backend == "null":
            queue = NullConversationExecutionQueue()
        else:
            raise RuntimeError("Unknown AX conversation queue backend")
        return ConversationApplication(
            SqlAlchemyConversationRepository(
                session,
                queue,
                self._settings.conversation_queue_max_fragments,
            ),
            SqlAlchemyConversationContextResolver(session),
        )

    def _actions(self, session: Any) -> ActionApplication:
        return ActionApplication(
            SqlAlchemyActionRepository(session),
            SqlAlchemyActionExecutor(session, self._report_provider),
        )

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


def create_codex_cli_provider(settings: Settings) -> CodexCliProviderAdapter:
    """Compose the isolated CLI adapter with exactly one server-bound SCAX MCP."""
    return CodexCliProviderAdapter(
        scax_mcp_server=CodexCliMcpServer(
            command=sys.executable,
            arguments=("-m", "ax_workspace.entrypoints.mcp"),
            environment={
                "AX_PROFILE": str(settings.profile),
                "DATABASE_URL": settings.database_url,
            },
        )
    )
