"""Composition root for the local workflow application."""
from __future__ import annotations

from pathlib import Path

from typing import Any
import sys
from uuid import UUID

from ax_workspace.bootstrap.settings import Settings
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
from ax_workspace.platform.conversation_jobs import ConversationJobQueue
from ax_workspace.platform.durable_jobs import MemoryDurableJobQueue, build_job_queue
from ax_workspace.platform.conversations import (
    SqlAlchemyConversationContextResolver,
    SqlAlchemyConversationRepository,
)
from ax_workspace.platform.actions import SqlAlchemyActionExecutor, SqlAlchemyActionRepository
from ax_workspace.platform.reports import SqlAlchemyDailyReportDraftWorkflow, SqlAlchemyDailyReportRepository
from ax_workspace.modules.work.materials import TaskMaterialApplication
from ax_workspace.modules.work.application import TaskAccessDenied, TaskApplication, TaskState
from ax_workspace.modules.work.assignments import TaskAssignmentApplication
from ax_workspace.modules.work.requests import WorkRequestApplication
from ax_workspace.platform.persistence import make_session_factory
from ax_workspace.platform.materials import LocalDirectoryMaterialStorage
from ax_workspace.modules.work.material_extraction import LexicalMaterialRetriever
from ax_workspace.platform.material_extraction import (
    MaterialJobQueue,
    SqlAlchemyMaterialEvidenceRepository,
    SqlAlchemyMaterialExtractionRepository,
)
from ax_workspace.platform.work_tasks import (
    SqlAlchemyTaskAssignmentRepository,
    SqlAlchemyAttachmentRepository,
    SqlAlchemyCommentRepository,
    SqlAlchemyTaskRepository,
    SqlAlchemyWorkRecordSource,
    SqlAlchemyWorkRequestRepository,
)




class WorkflowApplication:
    """Transaction boundary shared by HTTP, MCP, and local rehearsal adapters."""

    def __init__(self, settings: Settings, report_provider: AiProvider | None = None) -> None:
        self._settings = settings
        self._session_factory = make_session_factory(settings.database_url)
        self._material_storage = LocalDirectoryMaterialStorage(Path(settings.materials_dir))
        # One in-process job store per application when the memory backend is selected (tests); postgres joins each session.
        self.memory_job_queue: MemoryDurableJobQueue | None = MemoryDurableJobQueue() if settings.job_queue_backend == "memory" else None
        self._report_provider = report_provider or create_codex_cli_provider(settings)

    def my_work(self, principal: Principal) -> list[dict[str, Any]]:
        """ERD work_inbox projection: tasks the principal currently holds an active assignment for."""
        with self._session_factory() as session:
            try:
                return TaskApplication(SqlAlchemyTaskRepository(session)).list_for(principal)
            except TaskAccessDenied:
                return []

    def organization_tree(self, principal: Principal) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return OrganizationApplication(SqlAlchemyOrganizationRepository(session)).organization_tree(principal)

    def organization_unit_members(self, principal: Principal, unit_id: str) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return OrganizationApplication(SqlAlchemyOrganizationRepository(session)).unit_members(principal, unit_id)

    def my_organization_profile(self, principal: Principal) -> dict[str, Any]:
        with self._session_factory() as session:
            return OrganizationApplication(SqlAlchemyOrganizationRepository(session)).my_profile(principal)

    def authenticated_principal(self, persona_id: str) -> Principal:
        with self._session_factory() as session:
            return OrganizationApplication(SqlAlchemyOrganizationRepository(session)).authenticated_principal(persona_id)

    def generate_daily_report_draft(
        self,
        principal: Principal,
        report_date: str,
        causation_key: str | None = None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            try:
                result = self._reports(session).generate_draft(
                    principal,
                    report_date,
                    causation_key,
                )
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

    def daily_report_status(self, principal: Principal, report_date: str) -> dict[str, Any]:
        with self._session_factory() as session:
            return self._reports(session).status_for_date(principal, report_date)

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
        *,
        description: str | None = None,
        start_date: Any = None,
        due_date: Any = None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = TaskApplication(SqlAlchemyTaskRepository(session)).create_self(principal, title, causation_key, description=description, start_date=start_date, due_date=due_date)
            session.commit()
            return result

    def update_task(self, principal: Principal, task_id: UUID, expected_version: int, changes: dict[str, Any]) -> dict[str, Any]:
        with self._session_factory() as session:
            result = TaskApplication(SqlAlchemyTaskRepository(session)).update(task_id, principal, expected_version, changes)
            session.commit()
            return result

    def list_task_materials(self, principal: Principal, task_id: UUID) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return self._materials(session).list(principal, task_id)

    def attach_task_material(
        self, principal: Principal, task_id: UUID, *, kind: str, name: str, content_type: str, data: bytes
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._materials(session).attach(principal, task_id, kind=kind, name=name, content_type=content_type, data=data)
            session.commit()
            return result

    def open_task_material(self, principal: Principal, task_id: UUID, material_id: UUID) -> tuple[dict[str, Any], bytes]:
        with self._session_factory() as session:
            return self._materials(session).open(principal, task_id, material_id)

    def detach_task_material(self, principal: Principal, task_id: UUID, material_id: UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._materials(session).detach(principal, task_id, material_id)
            session.commit()
            return result

    def search_task_materials(self, principal: Principal, task_id: UUID, query: str, *, limit: int = 5, execution_id: UUID | None = None) -> dict[str, Any]:
        """`material.search` for one Task. With a delegated execution id the hits are also recorded as that turn's evidence."""
        with self._session_factory() as session:
            result = self._materials(session).search(principal, task_id, query, limit=limit)
            if execution_id is not None and result["results"]:
                SqlAlchemyMaterialEvidenceRepository(session).record(execution_id, str(principal.id), task_id, result["query"], result["results"])
                session.commit()
            return result

    def job_queue(self, session: Any):
        """The shared durable job transport bound to this session (postgres) or this application (memory)."""
        return build_job_queue(self._settings.job_queue_backend, session, self.memory_job_queue)

    def _material_queue(self, session: Any) -> MaterialJobQueue:
        return MaterialJobQueue(self.job_queue(session))

    def _materials(self, session: Any) -> TaskMaterialApplication:
        extractions = SqlAlchemyMaterialExtractionRepository(session)
        return TaskMaterialApplication(
            SqlAlchemyTaskRepository(session),
            SqlAlchemyAttachmentRepository(session),
            self._material_storage,
            extractions,
            self._material_queue(session),
            LexicalMaterialRetriever(extractions),
        )

    def assign_task(self, principal: Principal, title: str, assignee_id: str, **fields: Any) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._assignments(session).assign(principal, title, assignee_id, **fields)
            session.commit()
            return result

    def task_assignment_candidates(self, principal: Principal) -> list[dict[str, str]]:
        with self._session_factory() as session:
            return self._assignments(session).candidates(principal)

    def task_assignment_inbox(self, principal: Principal) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return self._assignments(session).inbox(principal)

    def sent_task_assignments(self, principal: Principal) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return self._assignments(session).sent(principal)

    def accept_task_assignment(self, principal: Principal, assignment_id: UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._assignments(session).accept(principal, assignment_id)
            session.commit()
            return result

    def decline_task_assignment(self, principal: Principal, assignment_id: UUID, reason: str) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._assignments(session).decline(principal, assignment_id, reason)
            session.commit()
            return result

    @staticmethod
    def _assignments(session: Any) -> TaskAssignmentApplication:
        return TaskAssignmentApplication(
            SqlAlchemyTaskAssignmentRepository(session),
            OrganizationApplication(SqlAlchemyOrganizationRepository(session)),
        )

    def list_tasks(self, principal: Principal, *, include_closed: bool = False) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return TaskApplication(SqlAlchemyTaskRepository(session)).list_for(principal, include_closed=include_closed)

    def get_task(self, principal: Principal, task_id: UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            return TaskApplication(SqlAlchemyTaskRepository(session)).get(principal, task_id)

    def create_work_request(
        self,
        principal: Principal,
        title: str,
        assignee_id: str,
        causation_key: str | None = None,
        *,
        description: str | None = None,
        due_date: Any = None,
        cc_member_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._work_requests(session).create(
                principal, title, assignee_id, causation_key, description=description, due_date=due_date, cc_member_ids=cc_member_ids
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

    def resubmit_work_request(self, principal: Principal, request_id: UUID, expected_version: int, **changes: Any) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._work_requests(session).resubmit(principal, request_id, expected_version, **changes)
            session.commit()
            return result

    def work_request_cc_candidates(self, principal: Principal) -> list[dict[str, str]]:
        with self._session_factory() as session:
            return self._work_requests(session).cc_candidates(principal)

    def attach_to_work_request_comment(self, principal: Principal, request_id: UUID, comment_id: UUID, **file: Any) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._work_requests(session).attach_to_comment(principal, request_id, comment_id, **file)
            session.commit()
            return result

    def add_work_request_evidence(self, principal: Principal, request_id: UUID, **file: Any) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._work_requests(session).add_evidence(principal, request_id, **file)
            session.commit()
            return result

    def open_work_request_attachment(self, principal: Principal, request_id: UUID, attachment_id: UUID) -> tuple[dict[str, Any], bytes]:
        with self._session_factory() as session:
            return self._work_requests(session).open_attachment(principal, request_id, attachment_id)

    def add_work_request_comment(self, principal: Principal, request_id: UUID, body: str) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._work_requests(session).add_comment(principal, request_id, body)
            session.commit()
            return result

    def work_request_timeline(self, principal: Principal, request_id: UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            return self._work_requests(session).timeline(principal, request_id)

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

    def retry_conversation_turn(self, principal: Principal, conversation_id: UUID, turn_id: UUID) -> dict[str, Any]:
        with self._session_factory() as session:
            result = self._conversations(session).retry(principal, conversation_id, turn_id)
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

    def _work_requests(self, session: Any) -> WorkRequestApplication:
        return WorkRequestApplication(
            SqlAlchemyWorkRequestRepository(session),
            OrganizationApplication(SqlAlchemyOrganizationRepository(session)),
            SqlAlchemyCommentRepository(session),
            SqlAlchemyAttachmentRepository(session),
            self._material_storage,
        )

    def _conversations(self, session: Any) -> ConversationApplication:
        return ConversationApplication(
            SqlAlchemyConversationRepository(
                session,
                ConversationJobQueue(self.job_queue(session)),
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


def create_auth_session_store(settings: Settings):
    """Login sessions live beside the operational database; the entrypoint only sees this factory."""
    from ax_workspace.platform.auth_sessions import SqlAlchemyAuthSessionStore
    from ax_workspace.platform.persistence import make_session_factory

    return SqlAlchemyAuthSessionStore(make_session_factory(settings.database_url))


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
